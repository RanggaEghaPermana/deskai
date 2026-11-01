#!/usr/bin/env python3
"""deskaid: the brain behind DeskAI.

Listens to the GNOME Shell extension over D-Bus, classifies what you're doing
against the active mode (rules first, Claude for the ambiguous rest), nudges
you when you drift, suggests a mode when you start working without one, and
lets through held notifications that really can't wait.
"""

import argparse
import datetime
import json
import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gio, GLib, GLibUnix  # noqa: E402

import brain  # noqa: E402
import commands  # noqa: E402
import compose  # noqa: E402
import ports  # noqa: E402
import docks  # noqa: E402
import guide  # noqa: E402
import launcher  # noqa: E402
import learned  # noqa: E402
import routine  # noqa: E402
import senders  # noqa: E402
import servers  # noqa: E402
import session  # noqa: E402
import sites  # noqa: E402
import system  # noqa: E402
from brain import BRAIN_NAME, BUS_NAME, OBJECT_PATH, log  # noqa: E402

TICK_SECONDS = 5
TICK_LOG_EVERY_S = 3600      # how often a part of the tick that keeps failing gets a line in the journal
ROUTINE_DELAY_S = 6          # after login or unlock, let the desktop settle before asking
ROUTINE_SNOOZE_S = 45 * 60
SNAPSHOT_EVERY = 30          # seconds between snapshots of the desk, for after a logout
SERVER_CHECK_EVERY = 10      # seconds between looks at the servers on the desk
ROOM_CHECK_EVERY = 60        # seconds between looks at memory and disk space
ROUTINE_STOP_WAIT_S = 3      # closing a project lets a setup still going finish the step it's on, if it's quick
DOCKS_SETTLE_S = 3           # turning a dock off restarts the extensions GNOME loaded after it, this dock among them
DOCK_WAIT_S = 60             # after starting, how long the dock gets to appear before the docks it replaced come back
GUIDE_DELAY_S = 15           # the first-run guide waits for the desktop, and for anything else on offer
GUIDE_TRIES = 8
CLASSIFY_DELAY_MS = 1500     # ignore alt-tab flicker
IDLE_AFTER_MS = 120_000      # no input this long counts as idle, not as the window
AWAY_AFTER_MS = 300_000      # don't nudge someone who isn't there
SUGGEST_AFTER = 120
SUGGEST_COOLDOWN = 1800
LLM_BACKOFF = 300
SENDER_ASK_GAP_S = 90        # two new senders at once get one question
REVIEW_EVERY_S = 3 * 3600    # without a pause to ask in, look this often
SAVE_EVERY = 60
CACHE_LIMIT = 3000


class Activity:
    def __init__(self, seq, app_id, app_name, title):
        self.seq = seq
        self.app_id = app_id
        self.app_name = app_name
        self.title = title
        self.since = time.monotonic()
        self.verdict = None
        self.reason = ""
        self.source = ""
        self.site = ""
        self.belongs_to = ""
        self.suggested = False
        self.carded = False

    @property
    def key(self):
        return (self.app_id, self.title)

    @property
    def label(self):
        return f"{self.app_name}: {self.title}"[:80] if self.title else self.app_name


class VerdictCache:
    def __init__(self, path):
        self.path = path
        self.dirty = False
        try:
            self.data = json.loads(path.read_text())
        except (OSError, ValueError):
            self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value):
        self.data[key] = {**value, "t": time.time()}
        self.dirty = True

    def save(self):
        if not self.dirty:
            return
        if len(self.data) > CACHE_LIMIT:
            newest = sorted(self.data.items(), key=lambda kv: kv[1].get("t", 0))[-CACHE_LIMIT:]
            self.data = dict(newest)
        brain.write_json_atomic(self.path, self.data)
        self.dirty = False

STATS_KEEP_DAYS = 90


class Stats:
    """Seconds per mode and verdict, one file per day, for STATS_KEEP_DAYS: it names what distracted you."""

    def __init__(self, directory):
        self.dir = directory
        self.day = None
        self.data = None
        self.dirty = False

    def _today(self):
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.save()
            self.day = day
            self.prune()
            try:
                self.data = json.loads((self.dir / f"{day}.json").read_text())
            except (OSError, ValueError):
                self.data = {"modes": {}, "distractions": {}}
        return self.data

    def prune(self):
        oldest = datetime.date.today() - datetime.timedelta(days=STATS_KEEP_DAYS)
        for path in self.dir.glob("*.json"):
            try:
                if datetime.date.fromisoformat(path.stem) < oldest:
                    path.unlink()
            except (ValueError, OSError):
                continue  # not a day's file, or not ours to remove

    def add(self, mode, kind, seconds, label):
        data = self._today()
        bucket = data["modes"].setdefault(mode, {})
        bucket[kind] = round(bucket.get(kind, 0) + seconds, 1)
        if kind == "off_task":
            data["distractions"][label] = round(data["distractions"].get(label, 0) + seconds, 1)
        self.dirty = True

    def save(self):
        if not self.dirty or not self.day:
            return
        top = sorted(self.data["distractions"].items(), key=lambda kv: -kv[1])[:50]
        self.data["distractions"] = dict(top)
        try:
            brain.write_json_atomic(self.dir / f"{self.day}.json", self.data)
        except OSError as e:  # a full disk: it stays in memory, and the next save tries again
            log.warning("can't save today's stats: %s", e)
            return
        self.dirty = False


class Brain:
    def __init__(self, loop):
        self.loop = loop
        self.config = brain.Config()
        self.config.refresh()
        self.llm = brain.LLM(self.config.llm)
        self.llm_warned = False
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="claude")
        self.cache = VerdictCache(brain.CACHE_DIR / "verdicts.json")
        self.learned = learned.Learned()
        if not self.learned.seeded:
            log.info("learned %d verdicts from before", self.learned.seed_from_verdicts(self.cache.data, self.config))
        self.stats = Stats(brain.STATE_DIR / "stats")
        self.senders = senders.Senders()
        self.unsure = learned.Unsure()
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

        self.mode = None
        self.activity = None
        self.seq = 0
        self.inflight = set()
        self.drift_since = None
        self.last_nudge = None
        self.llm_paused_until = 0.0
        self.fits_shown = set()
        self.suggested_at = {}
        self.away = False
        self.last_snapshot = 0.0
        self.restore_snap = None  # the previous session's desk, on offer or being restored
        self.restore_offered = False
        self.routine_plan = None  # the workday routine on offer, or being set up
        self.routine_marks = {}
        self.watch = servers.Watch()
        self.servers_checking = False
        self.servers_sent = None
        self.last_server_check = 0.0
        self.last_room_check = 0.0
        self.memory_low_since = None  # the first look that found memory running out
        self.room_warned = set()      # what's been warned about and hasn't recovered yet: disks by path, "memory"
        self.clearing = None          # the cache ids on offer to clear, or being cleared
        self.services_offered = set()  # services an opened app needed, offered once a login
        self.pending_edit = None  # a change to modes.json waiting for your OK
        self.closing_terminals = []  # a closed project's terminals, waiting for your OK
        self.guide_step = None
        self.dock_seen = False
        self.routine_running = None  # the setup going on right now
        self.guide_tries = 0
        self.whatsapp_checked = False
        self.card_act = None      # what the last distraction card was about, for "Allow in …"
        self.allow_undo = None    # (card id, mode key, the mode as it was, the window's title)
        self.mark_undo = None     # (card id, mode key, the window's title)
        self.sender_ask = None    # the notification a question about its sender is on offer for
        self.sender_undo = None   # (card id, app, title, whether it's the whole app)
        self.sender_asked_at = -SENDER_ASK_GAP_S
        self.review_item = None   # the window being asked about
        self.last_review_try = time.monotonic()
        self.closing_hand = None  # a closed project's servers you started yourself, waiting for your OK
        self.working_since = None
        self.break_shown = 0.0
        self.last_wall = time.time()
        self.last_tick = time.monotonic()
        self.last_save = time.monotonic()
        self.tick_logged = {}     # the part of the tick that failed → when that last went in the journal

    # ---- wiring ---------------------------------------------------------

    def start(self):
        GLib.timeout_add_seconds(DOCK_WAIT_S, self._dock_never_came)
        Gio.bus_own_name_on_connection(
            self.bus, BRAIN_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, None, self._on_name_lost)
        self.bus.signal_subscribe(
            BUS_NAME, BUS_NAME, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, self._on_signal)
        Gio.bus_watch_name_on_connection(
            self.bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE, self._on_dock_appeared, self._on_dock_vanished)
        GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        log.info("deskaid up · AI: %s", self.llm.describe())

    def shutdown(self):
        self._account()
        self.stats.save()
        self.cache.save()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _on_name_lost(self, _conn, _name):
        log.error("another deskaid already owns %s, exiting", BRAIN_NAME)
        self.loop.quit()

    def _on_dock_appeared(self, _conn, _name, _owner):
        log.info("dock appeared")
        self.call("GetState", reply=self._on_state)
        # The dock comes up at login and again after every unlock: "opening the laptop".
        GLib.timeout_add_seconds(ROUTINE_DELAY_S, self._desk_ready)
        self.servers_sent = None  # this dock hasn't heard yet
        self.guide_tries = 0
        GLib.timeout_add_seconds(GUIDE_DELAY_S, self._maybe_guide)
        self.dock_seen = True
        self._replace_other_docks()

    def _replace_other_docks(self):
        try:
            names = docks.turn_off_others()
        except (OSError, ValueError, GLib.Error) as e:
            log.warning("can't turn off the other docks: %s", e)
            return
        if names:
            log.info("turned off: %s", names)
            GLib.timeout_add_seconds(DOCKS_SETTLE_S, self._say_docks_replaced, names)

    def _say_docks_replaced(self, names):
        self._command_card(self._say("docks_off", names=", ".join(names)), timeout=20)
        return False

    def _dock_never_came(self):
        """A dock that can't load, on a newer GNOME say, gives back what it replaced: you're never left without one."""
        if not self.dock_seen:
            log.warning("no dock %d seconds after starting: turning the other docks back on", DOCK_WAIT_S)
            docks.restore()
        return False

    def _on_dock_vanished(self, _conn, _name):
        if self.mode is not None:
            log.info("dock vanished")
        self._account()
        self.activity = None

    def _on_state(self, result):
        state = json.loads(result[0])
        self.mode = state["mode"]
        focus = state["focus"]
        self.on_focus(focus["appId"], focus["appName"], focus["wmClass"], focus["title"])

    def _on_signal(self, _conn, _sender, _path, _iface, name, params):
        args = params.unpack()
        if name == "FocusChanged":
            self.on_focus(*args)
        elif name == "ModeChanged":
            self.on_mode(*args)
        elif name == "NotificationHeld":
            self.on_notification(*args)
        elif name == "CardAction":
            self.on_card_action(*args)
        elif name == "Command":
            self.on_command(*args)

    def call(self, method, signature=None, args=None, reply=None):
        params = GLib.Variant(signature, args) if signature else None

        def done(conn, result):
            try:
                value = conn.call_finish(result)
            except GLib.Error as e:
                log.warning("D-Bus %s failed: %s", method, e.message)
                return
            if reply:
                reply(value.unpack())

        self.bus.call(BUS_NAME, OBJECT_PATH, BUS_NAME, method, params, None,
                      Gio.DBusCallFlags.NONE, 5000, None, done)

    def card(self, spec):
        self.call("ShowCard", "(s)", (json.dumps(spec, ensure_ascii=False),))

    # ---- cards ----------------------------------------------------------

    def _say(self, key, **params):
        """Card wording in the language modes.json asks for."""
        return brain.card_text(self.config.language, key, **params)

    def _escape_mode(self):
        """Where "Go to …" leads when nothing better is known: the first mode that isn't a focus mode."""
        return next((key for key, mode in self.config.modes() if not mode.get("focus")), None)

    def _go_action(self, target):
        mode = self.config.mode(target)
        wait = self.config.exit_wait(self.mode, target)
        label = (self._say("go_wait", mode=mode["label"], s=wait) if wait
                 else self._say("go", mode=mode["label"]))
        return {"id": f"go:{target}", "label": label, "primary": True, "icon": mode.get("icon")}

    def _distraction_card(self, act, kind):
        home = act.belongs_to if act.belongs_to != self.mode else ""
        target = home or self._escape_mode()
        subtitle = (self._say("belongs_to", mode=self.config.mode(home)["label"]) if home
                    else self._say("not_in_this_mode"))
        actions = [{"id": "stay", "label": self._say("stay")},
                   {"id": "allow", "label": self._say("allow", mode=self.config.mode(self.mode)["label"])}]
        if target and target != self.mode:
            actions.append(self._go_action(target))
        self.card_act = act
        self.card({
            "id": f"act-{act.seq}",
            "kind": kind,
            "icon": {"app_id": act.app_id},
            "title": act.site or act.app_name,
            "subtitle": subtitle,
            "text": self._say("distraction" if kind == "distraction" else "nudge"),
            "actions": actions,
            "anchor_mode": target or self.mode,
            "timeout": 14,
        })

    def _fits_card(self, act):
        mode = self.config.mode(self.mode)
        self.card({
            "id": f"fit-{act.seq}",
            "kind": "fits",
            "icon": {"app_id": act.app_id},
            "title": act.site or act.app_name,
            "subtitle": self._say("not_in_any_mode"),
            "verdict": {"label": self._say("fits", mode=mode["label"]), "reason": act.reason},
            "anchor_mode": self.mode,
            "timeout": 5,
        })

    def _held_card(self, notification_id, app, title, body, result):
        home = result.get("mode")
        if not self.config.mode(home) or home == self.mode:
            # Nothing says it belongs to another mode, so there's nowhere to send you: just let the banner through.
            self.call("SurfaceNotification", "(u)", (notification_id,))
            return
        self.card({
            "id": f"notif-{notification_id}",
            "kind": "held",
            "icon": {"notification_id": notification_id},
            "title": title or app,
            "subtitle": (f"{app} · " if title else "") + self._say("held_for", mode=self.config.mode(home)["label"]),
            "text": body[:160],
            "chip": {"label": result.get("label") or self._say("urgent"), "detail": result.get("detail", "")},
            "actions": [{"id": "later", "label": self._say("later")}, self._go_action(home)],
            "anchor_mode": home,
            "notification_id": notification_id,
            "timeout": 20,
        })

    # ---- events ---------------------------------------------------------

    def on_focus(self, app_id, app_name, wm_class, title):
        if self.mode is None:
            return
        title = brain.normalize_title(title)
        app_id = app_id or wm_class
        app_name = app_name or wm_class
        if self.activity and self.activity.key == (app_id, title):
            return
        self._account()
        self.seq += 1
        self.activity = Activity(self.seq, app_id, app_name, title)
        if not self.whatsapp_checked and app_id in session.BROWSER_APPS and "whatsapp" in title.lower():
            self._maybe_ask_whatsapp()
        self._maybe_offer_service(app_id, app_name)
        seq = self.seq
        GLib.timeout_add(CLASSIFY_DELAY_MS, lambda: self._classify(seq))

    def on_mode(self, mode, previous):
        self._account()
        log.info("mode %s → %s", previous or "-", mode)
        self.mode = mode
        self.drift_since = None
        self.last_nudge = None
        if self.activity:
            self.activity.verdict = None
            self.activity.carded = False
            self.activity.since = time.monotonic()
            self._classify(self.activity.seq)

    def on_notification(self, notification_id, app, title, body):
        self._refresh_config()
        mode = self.config.mode(self.mode)
        if not mode or mode["notifications"].get("policy") != "filter":
            return
        said = self.senders.word(app, title)
        if said:
            log.info("notif %s | %s → %s [you]", app, title[:60], said)
            if said == senders.ALWAYS:
                self.call("SurfaceNotification", "(u)", (notification_id,))
            return
        plain = sites.notification_verdict(app, title, body)
        if plain is not None:
            log.info("notif %s | %s → %s [site]", app, title[:60], "surface" if plain else "hold")
            if plain:
                self._held_card(notification_id, app, title, body, {})
            return
        # A small local model lets nearly everything through (11 of 14 real ones Claude held), so it
        # doesn't get to judge these. They're safe in the tray, and the first time someone new lands
        # there, you get asked.
        if self.llm.backend == "ollama" or not self.config.llm["triage_notifications"] or not self._llm_ready():
            self._maybe_ask_sender(notification_id, app, title, body)
            return
        prompt = brain.notification_prompt(self.mode, mode, self.config.modes(), app, title, body)
        future = self.pool.submit(self.llm.ask, brain.notify_system(self.config.language), prompt, brain.NOTIFY_SCHEMA)
        mode_at = self.mode
        future.add_done_callback(
            lambda f: GLib.idle_add(self._on_triaged, f, notification_id, app, title, body, mode_at))

    def _maybe_ask_sender(self, notification_id, app, title, body):
        now = time.monotonic()
        if now - self.sender_asked_at < SENDER_ASK_GAP_S or not self.senders.should_ask(app, title):
            return
        self.sender_asked_at = now
        card_id = f"sender-{notification_id}"
        self.sender_ask = {"id": card_id, "notification_id": notification_id, "app": app, "title": title}
        actions = [{"id": "sender:hold", "label": self._say("sender_hold")}]
        if senders.can_take_whole_app(app, title):
            actions.append({"id": "sender:app", "label": self._say("sender_app", app=app)})
        actions.append({"id": "sender:always", "label": self._say("sender_always"), "primary": True})
        name = senders.name_of(app, title)
        self.card({
            "id": card_id,
            "kind": "held",
            "icon": {"notification_id": notification_id},
            "title": name,
            "subtitle": (f"{app} · " if app and app != name else "") + self._say("sender_held"),
            "text": body[:160],
            "actions": actions,
            "anchor_mode": self.mode,
            "timeout": 20,
        })

    def _answer_sender(self, action):
        ask, self.sender_ask = self.sender_ask, None
        app, title = ask["app"], ask["title"]
        if action == "sender:hold":
            self.senders.remember(app, title, senders.HOLD)
            return
        whole_app = action == "sender:app"
        self.senders.remember(app, title, senders.ALWAYS, whole_app=whole_app)
        self.call("SurfaceNotification", "(u)", (ask["notification_id"],))
        card_id = f"sender-saved-{ask['notification_id']}"
        self.sender_undo = (card_id, app, title, whole_app)
        self.card({
            "id": card_id,
            "kind": "fits",
            "icon": {"name": "object-select-symbolic"},
            "title": app if whole_app else senders.name_of(app, title),
            "subtitle": self._say("sender_saved"),
            "text": self._say("sender_saved_text"),
            "actions": [{"id": "sender:undo", "label": self._say("undo")}],
            "anchor_mode": self.mode,
            "timeout": 8,
        })

    def on_card_action(self, card_id, action):
        log.info("card %s → %s", card_id, action)
        if action == "stay":
            # You chose to stay: start the drift clock over instead of nagging again soon.
            self.drift_since = None
            self.last_nudge = time.monotonic()
        elif action == "restore:run" and self.restore_snap:
            self._run_restore(self.restore_snap)
        elif action == "restore:later" and self.restore_snap:
            session.mark_restored(self.restore_snap)  # `deskai session restore` still brings it back
            self.restore_snap = None
            self._maybe_offer_routine()
        elif action == "routine:run" and self.routine_plan:
            self._run_routine(self.routine_plan)
        elif action == "routine:later":
            routine.write_state(snoozed_until=time.time() + ROUTINE_SNOOZE_S)
            self.routine_plan = None
        elif action.startswith("server:"):
            _, op, unit = action.split(":", 2)
            self._server_action(op, unit)
        elif action.startswith("service:start:"):
            self._docker_or_service("dev_service", {"op": "start", "unit": action.split(":", 2)[2]})
        elif action == "system:clear-offer":
            self._offer_clearing()
        elif action == "system:clear" and self.clearing:
            self._clear_caches()
        elif action == "project:close-terminals":
            terminals, self.closing_terminals = self.closing_terminals, []
            closed = [session.close_terminal(*terminal) for terminal in terminals]
            log.info("closed %d of %d project terminals", sum(closed), len(closed))
            self._command_card(self._say("project_terminals_closed"))
        elif action == "project:keep-terminals":
            self.closing_terminals = []
        elif action in ("project:stop-hand", "project:keep-hand") and self.closing_hand:
            self._answer_hand_started(action == "project:stop-hand")
        elif action in ("sender:always", "sender:app", "sender:hold") and self.sender_ask and card_id == self.sender_ask["id"]:
            self._answer_sender(action)
        elif action == "sender:undo" and self.sender_undo and card_id == self.sender_undo[0]:
            _, app, title, whole_app = self.sender_undo
            self.sender_undo = None
            self.senders.forget(app, title, whole_app=whole_app)
        elif action in ("review:work", "review:distraction", "review:skip") and self.review_item and card_id == self.review_item["id"]:
            self._answer_review(action)
        elif action == "break:off":
            self._set_breaks(False)
            self._command_card(self._say("breaks_off"))
        elif action == "system:room-off":
            self._set_room_warnings(False)
            self._command_card(self._say("room_off"))
        elif action == "command:apply" and self.pending_edit:
            self._apply_edit()
        elif action == "command:cancel":
            self.pending_edit = None
        elif action == "guide:next":
            self._guide_next()
        elif action == "guide:skip":
            guide.finished()
            self.guide_step = None
        elif action == "guide:whatsapp":
            self._open_whatsapp_setting()
        elif action == "allow" and self.card_act and card_id == f"act-{self.card_act.seq}":
            self._allow(self.card_act)
        elif action == "undo-mark" and self.mark_undo and card_id == self.mark_undo[0]:
            _, key, title = self.mark_undo
            self.mark_undo = None
            self.learned.forget(key, title)
            self._reclassify()
        elif action.startswith("go:") and self.card_act and card_id == f"act-{self.card_act.seq}":
            # You agreed it doesn't belong in this mode.
            self.learned.add(self.mode, self.card_act.app_id, self.card_act.title, "off_task")
        elif action == "undo-allow" and self.allow_undo and card_id == self.allow_undo[0]:
            _, key, before, title = self.allow_undo
            self.allow_undo = None
            self.learned.forget(key, title)

            def restore(mode):
                mode.clear()
                mode.update(before)
            if self._edit_mode(key, restore):
                self._reclassify()

    # ---- after a logout: the desk as you left it ----------------------------

    def _desk_ready(self):
        """After login or unlock: offer the previous session's desk, else the workday routine."""
        future = self.pool.submit(lambda: session.to_restore(session.current_session()))
        future.add_done_callback(lambda f: GLib.idle_add(self._offer_restore, f))
        return False

    def _offer_restore(self, future):
        try:
            snap = future.result()
        except Exception as e:  # reading /proc and the state file; never fatal
            log.warning("restore: can't check: %s", e)
            snap = None
        if not snap or self.restore_offered:
            self._maybe_offer_routine()
            return False
        self.restore_offered = True
        self.restore_snap = snap
        self._restore_card(snap, self._say("restore_title"), 90, offer=True)
        return False

    def _restore_card(self, snap, title, timeout, offer=False):
        marks = {"running": "…", "done": "✓", "skipped": "✓", "failed": "✗"}
        lines = [f"{marks[state]} {label}" for label, state in self.routine_marks.items()]
        self.card({
            "id": "restore",
            "kind": "suggest",
            "icon": {"name": "media-playback-start-symbolic"},
            "title": title,
            "subtitle": self._say("restore_from", time=time.strftime("%H.%M", time.localtime(snap["time"]))),
            "text": " · ".join(session.summary(snap)) if offer else "\n".join(lines),
            "actions": [{"id": "restore:later", "label": self._say("restore_no")},
                        {"id": "restore:run", "label": self._say("restore_go"), "primary": True}] if offer else [],
            "anchor_mode": snap.get("mode") if self.config.mode(snap.get("mode")) else self.mode,
            "timeout": timeout,
        })

    def _run_restore(self, snap):
        self.routine_marks = {}

        def progress(label, state):
            GLib.idle_add(self._restore_progress, snap, label, state)

        def set_mode(mode):
            GLib.idle_add(lambda: self.call("SetMode", "(s)", (mode,)) and False)

        def work():
            try:
                session.restore(snap, progress, set_mode, labels={"windows": self._say("restore_windows")})
            except Exception as e:  # never take the daemon down with it
                log.exception("restore failed: %s", e)
            GLib.idle_add(self._restore_finished, snap)

        log.info("restore: bringing back the desk from %s", time.ctime(snap["time"]))
        threading.Thread(target=work, name="restore", daemon=True).start()

    def _restore_progress(self, snap, label, state):
        self.routine_marks[label] = state
        log.info("restore: %s %s", label, state)
        self._restore_card(snap, self._say("restore_running"), 120)
        return False

    def _restore_finished(self, snap):
        failed = sum(1 for state in self.routine_marks.values() if state == "failed")
        self._restore_card(snap, self._say("routine_partly", n=failed) if failed else self._say("restore_done"), 12)
        self.restore_snap = None
        return False

    def _snapshot_desk(self):
        """What's open right now, for bringing it back after a logout."""
        mode = self.mode

        def take(windows):
            try:
                session.record(session.snapshot(self.bus, windows, mode))
            except Exception as e:
                log.warning("snapshot failed: %s", e)

        def got(conn, result):
            try:
                windows = json.loads(conn.call_finish(result).unpack()[0])
            except (GLib.Error, ValueError):
                windows = None  # an older dock without GetWindows
            threading.Thread(target=take, args=(windows,), name="snapshot", daemon=True).start()

        self.bus.call(BUS_NAME, OBJECT_PATH, BUS_NAME, "GetWindows", None, None,
                      Gio.DBusCallFlags.NONE, 3000, None, got)

    # ---- the workday routine ---------------------------------------------

    def _maybe_offer_routine(self):
        if not routine.due():
            return False
        future = self.pool.submit(routine.plan_for_today)
        future.add_done_callback(lambda f: GLib.idle_add(self._offer_routine, f))
        return False

    def _offer_routine(self, future):
        try:
            plan = future.result()
        except Exception as e:  # anything from reading transcripts or the browser history
            log.warning("routine: can't plan: %s", e)
            return False
        if not plan:
            return False
        if routine.already_set_up(plan):
            routine.write_state(done=time.strftime("%Y-%m-%d"))
            return False
        self.routine_plan = plan
        self.routine_marks = {}
        self.card({
            "id": "routine",
            "kind": "suggest",
            "icon": {"name": "media-playback-start-symbolic"},
            "title": self._say("routine_title", day=self._routine_day(plan)),
            "subtitle": os.path.basename(plan.get("project", "")),
            "text": " · ".join(routine.label(step) for step in plan["steps"] if step["type"] != "mode"),
            "actions": [{"id": "routine:later", "label": self._say("later")},
                        {"id": "routine:run", "label": self._say("routine_go"), "primary": True}],
            "anchor_mode": self._routine_mode(plan) or self.mode,
            "timeout": 60,
        })
        return False

    def _routine_day(self, plan):
        learned = plan.get("learned_from")
        if not learned:
            return self._say("routine_yesterday")
        day = time.strptime(learned, "%Y-%m-%d")
        if time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400)) == learned:
            return self._say("routine_yesterday")
        return self._say("routine_days").split()[day.tm_wday]

    def _routine_mode(self, plan):
        return next((s["mode"] for s in plan["steps"] if s["type"] == "mode" and self.config.mode(s["mode"])), None)

    def _why_it_failed(self, step):
        """What to say about a step of a setup that failed, or "" if there's nothing more than that it did."""
        if step["type"] == "compose":
            return "" if shutil.which("docker") else self._say("compose_no_docker")
        if step["type"] != "server":
            return ""
        said = servers.last_log_line({"kind": "server", "unit": routine.unit_name(step["cwd"], step["command"])})
        tool = servers.missing_tool(said)
        return self._say("routine_no_tool", tool=tool) if tool else said

    def _run_routine(self, plan):
        cancel = threading.Event()
        failed = []

        def progress(step, state):
            if state == "failed":
                failed.append(step)
            if not cancel.is_set():
                GLib.idle_add(self._routine_progress, plan, step, state)

        def set_mode(mode):
            GLib.idle_add(lambda: self.call("SetMode", "(s)", (mode,)) and False)

        def work():
            try:
                routine.run(plan, progress, set_mode, cancel.is_set)
            except Exception as e:  # never take the daemon down with it
                log.exception("routine failed: %s", e)
            if not cancel.is_set():
                GLib.idle_add(self._routine_finished, plan, [self._why_it_failed(step) for step in failed])

        log.info("routine: setting up %s", plan.get("project"))
        thread = threading.Thread(target=work, name="routine", daemon=True)
        self.routine_running = {"project": plan.get("project"), "cancel": cancel, "thread": thread}
        thread.start()

    def _routine_card(self, plan, title, timeout, why=()):
        marks = {"running": "…", "done": "✓", "skipped": "✓", "failed": "✗"}
        lines = [f"{marks.get(self.routine_marks.get(id(step)), '·')} {routine.label(step)}"
                 for step in plan["steps"] if step["type"] != "mode"]
        lines += [line for line in why if line]
        self.card({
            "id": "routine",
            "kind": "suggest",
            "icon": {"name": "media-playback-start-symbolic"},
            "title": title,
            "subtitle": os.path.basename(plan.get("project", "")),
            "text": "\n".join(lines),
            "anchor_mode": self._routine_mode(plan) or self.mode,
            "timeout": timeout,
        })

    def _routine_progress(self, plan, step, state):
        self.routine_marks[id(step)] = state
        log.info("routine: %s %s", routine.label(step), state)
        if step["type"] != "mode":
            self._routine_card(plan, self._say("routine_running"), 120)
        return False

    def _routine_finished(self, plan, why=()):
        failed = sum(1 for state in self.routine_marks.values() if state == "failed")
        title = self._say("routine_partly", n=failed) if failed else self._say("routine_ready")
        self._routine_card(plan, title, 20 if any(why) else 12, why)
        self.routine_plan = None
        self.routine_running = None
        return False

    # ---- servers on the desk ---------------------------------------------

    def _check_servers(self):
        """Looks at the servers off the main thread: the dock hears of any change."""
        if self.servers_checking:
            return
        self.servers_checking = True
        self.last_server_check = time.monotonic()

        def work():
            try:
                down = [(item, servers.last_log_line(item)) for item in self.watch.scan()]
            except Exception as e:  # systemctl and the journal; never fatal
                log.warning("servers: can't check: %s", e)
                down = []
            GLib.idle_add(self._servers_checked, down)

        threading.Thread(target=work, name="servers", daemon=True).start()

    def _servers_checked(self, down):
        self.servers_checking = False
        self._send_servers()
        for item, last_line in down:
            log.info("servers: %s went down", item["label"])
            self._server_problem(item, self._say("server_down", name=item["label"]), last_line)
        return False

    def _send_servers(self):
        text = json.dumps(self.watch.summary())
        if text != self.servers_sent:
            self.servers_sent = text
            self.call("Servers", "(s)", (text,))

    def _server_card(self, item, title, text="", actions=(), accent="#10b981", timeout=8):
        self.card({
            "id": f"server:{item['unit']}",
            "kind": "server",
            "icon": {"name": "network-server-symbolic"},
            "title": title,
            "subtitle": os.path.basename(item["cwd"]),
            "text": text,
            "actions": list(actions),
            "accent": accent,
            "timeout": timeout,
        })

    def _server_problem(self, item, title, text=""):
        self._server_card(item, title, text, accent="#e5484d", timeout=30, actions=[
            {"id": f"server:log:{item['unit']}", "label": self._say("server_log")},
            {"id": f"server:restart:{item['unit']}", "label": self._say("server_restart"), "primary": True}])

    def _server_action(self, op, unit):
        item = self.watch.get(unit)
        if not item:
            return
        if op == "log":
            if item["kind"] == servers.MANUAL:
                self._server_card(item, item["label"], self._say("server_mine", name=item["label"]))
            else:
                servers.open_log(item)
        elif op == "status":
            self._server_status(item)
        elif op == "open" and item.get("port"):
            commands.open_uri(f"http://127.0.0.1:{item['port']}")
        elif op == "forget":
            self.watch.forget(unit)
            self._send_servers()
        elif op in ("restart", "stop"):
            def work():
                ok = self.watch.restart(unit) if op == "restart" else self.watch.stop(unit)
                GLib.idle_add(self._server_done, item, op, ok)
            threading.Thread(target=work, name="server-op", daemon=True).start()
            GLib.timeout_add(100, lambda: self._send_servers() and False)  # "starting", or gone

    def _server_status(self, item):
        """"What's up with this server?", answered from what's known about it."""
        last_line = servers.last_log_line(item)
        if item["state"] == "down":
            self._server_problem(item, self._say("server_down", name=item["label"]), last_line)
        elif item["kind"] == servers.MANUAL:
            self._server_card(item, self._say("server_is_up", name=item["label"]), self._say("server_mine", name=item["label"]), timeout=12)
        else:
            self._server_card(item, self._say("server_is_up", name=item["label"]), last_line, timeout=12, actions=[
                {"id": f"server:log:{item['unit']}", "label": self._say("server_log")}])

    def _server_done(self, item, op, ok):
        self._send_servers()
        if op == "stop":
            self._server_card(item, self._say("server_stopped", name=item["label"]) if ok
                              else self._say("server_failed", name=item["label"]))
        elif ok:
            self._server_card(item, self._say("server_back", name=item["label"]))
        else:
            self._server_problem(item, self._say("server_failed", name=item["label"]), servers.last_log_line(item))
        return False

    # ---- memory and disk space --------------------------------------------

    def _in_background(self, work, then):
        """Runs work() on a thread, and then(its result) back on the main one."""
        def run():
            try:
                result = work()
            except Exception as e:  # du, docker, git: never fatal
                log.warning("background work failed: %s", e)
                result = None
            GLib.idle_add(lambda: then(result) and False)
        threading.Thread(target=run, name="background", daemon=True).start()

    def _check_room(self):
        """Warns once when a disk or the memory runs out, and again only after it has recovered."""
        if not self.config.room_warnings["enabled"]:
            return
        turn_off = {"id": "system:room-off", "label": self._say("room_off_button")}
        low = {disk["path"]: disk for disk in system.low_disks()}
        for path, disk in low.items():
            if path not in self.room_warned:
                self.room_warned.add(path)
                where = path.replace(os.path.expanduser("~"), "~")
                self.card({"id": "room", "kind": "info", "title": self._say("disk_low", path=where, free=system.human(disk["free"])),
                           "text": "", "accent": "#e5484d", "timeout": 30,
                           "actions": [turn_off, {"id": "system:clear-offer", "label": self._say("disk_low_clear"), "primary": True}]})
        self.room_warned -= {path for path in self.room_warned if path != "memory" and path not in low}
        info = system.memory()
        if not system.memory_low(info):
            self.memory_low_since = None
            self.room_warned.discard("memory")
            return
        # A moment of it, like a build, isn't worth a word: it has to last two looks.
        self.memory_low_since = self.memory_low_since or time.monotonic()
        if "memory" not in self.room_warned and time.monotonic() - self.memory_low_since >= ROOM_CHECK_EVERY:
            self.room_warned.add("memory")
            apps = ", ".join(f"{name} {system.human(size)}" for name, size in system.heaviest_apps())
            used = info["total"] - info["available"]
            self.card({"id": "room", "kind": "info", "title": self._say("memory_low", percent=round(used * 100 / info["total"]), apps=apps),
                       "text": "", "accent": "#e5484d", "timeout": 30, "actions": [turn_off]})

    def _offer_clearing(self):
        """Counts the caches safe to clear, then asks before clearing them."""
        self._command_card(self._say("clear_counting"), timeout=60)

        def offer(found):
            found = found or []
            if not found:
                self.clearing = None
                self._command_card(self._say("clear_nothing"))
                return
            self.clearing = [item["id"] for item in found]
            size = system.human(sum(item["size"] for item in found))
            items = ", ".join(f"{item['label']} {system.human(item['size'])}" for item in found)
            self._command_card(self._say("clear_offer", size=size, items=items), timeout=45, actions=[
                {"id": "system:clear", "label": self._say("clear_go", size=size), "primary": True},
                {"id": "later", "label": self._say("not_now")}])
        self._in_background(system.clearable, offer)

    def _clear_caches(self):
        ids, self.clearing = self.clearing, None
        self._command_card(self._say("clear_counting"), timeout=120)
        self._in_background(lambda: system.clear(ids),
                            lambda freed: self._command_card(self._say("clear_done", size=system.human(freed or 0))))

    def _maybe_offer_service(self, app_id, app_name):
        """An app opened that needs a service left off until it's wanted: offers to start it, once a login."""
        unit = commands.APP_SERVICES.get((app_id or "").removesuffix(".desktop"))
        if not unit or unit in self.services_offered or commands.service_active(unit):
            return
        self.services_offered.add(unit)
        self.card({"id": "service-offer", "kind": "info", "timeout": 30,
                   "title": self._say("service_offer", app=app_name or commands.service_label(unit), name=commands.service_label(unit)),
                   "text": "", "actions": [{"id": f"service:start:{unit}", "label": self._say("service_start"), "primary": True},
                                           {"id": "later", "label": self._say("not_now")}]})

    def _start_project(self, name, found, op):
        """compose.start, unless its ports are held by something else: say who, rather than Docker's
        "address already in use" halfway through."""
        taken = ports.busy_for(name, found) if op == "up" else []
        if taken:
            held = ", ".join(f":{port} ({who})" for port, who in taken)
            return False, self._say("ports_taken", ports=held), []
        return self._in_plain_words(compose.start(name, found, op))

    def _in_plain_words(self, result):
        """A tool's (ok, what it said, ...), with what it said put in plain words. As it said it goes in the log."""
        ok, said, *more = result
        if ok:
            return result
        log.warning("command: %s", said)
        return (False, self._say(commands.trouble(said)), *more)

    def _docker_or_service(self, kind, action):
        """Starts, stops or restarts a Compose project or a system service, off the main thread: a first
        start can pull images, and a system service asks for your password."""
        op = action.get("op")
        if kind == "compose":
            if not shutil.which("docker"):
                self._command_card(self._say("compose_no_docker"))
                return
            name = action.get("project", "")
            folder = compose.projects(commands.all_projects().values()).get(name)
            if not folder or op not in ("up", "stop", "restart"):
                self._command_card(self._say("compose_none"))
                return
            found = compose.projects(commands.all_projects().values())
            if op == "stop":
                # Stopping what others need: say so on the card, they lose their database.
                running = compose.states(found)
                hurt = [other for other in compose.dependents(name, found) if running.get(other) == "running"]
                work = lambda: self._in_plain_words((*compose.act(folder, op), hurt))  # noqa: E731
            else:
                work = lambda: self._start_project(name, found, op)  # noqa: E731
        else:
            name = commands.service_label(action.get("unit", ""))
            op = {"start": "up"}.get(op, op)
            if not name or op not in ("up", "stop", "restart"):
                self._command_card(self._say("command_no_server"))
                return
            work = lambda: self._in_plain_words(commands.systemctl(action["unit"], {"up": "start"}.get(op, op)))  # noqa: E731
        self._command_card(self._say(f"compose_working_{op}", name=name), timeout=120)

        def done(result):
            ok, said, *more = result or (False, self._say("trouble_unknown"))
            others = ", ".join(more[0]) if more and more[0] else ""
            text = (self._say(f"compose_done_{op}", name=name) if ok else
                    self._say("compose_failed", name=more[1] if len(more) > 1 else name, why=said))
            if ok and others:
                text += " " + self._say("compose_also_stopped" if op == "stop" else "compose_also_started", names=others)
            self._command_card(text, timeout=20 if others else 15)
            self._check_servers()
        self._in_background(work, done)

    # ---- the command bar --------------------------------------------------

    def _command_card(self, text, actions=(), timeout=10, busy=False):
        spec = {"id": "command", "kind": "command", "title": text if len(text) <= 60 else "DeskAI",
                "text": "" if len(text) <= 60 else text, "actions": list(actions), "timeout": timeout}
        self.card({**spec, "busy": True} if busy else spec)

    def on_command(self, text):
        log.info("command: %s", text[:120])
        quick = commands.quick(text, [(key, mode["label"]) for key, mode in self.config.modes()], self.watch, commands.all_projects())
        if quick:
            self._run_actions(quick)
            return
        if not self.llm.available:
            self._command_card(self._say("command_needs_ai"))
            return
        language, mode = self.config.language, self.mode

        def work():
            try:
                can_restore = bool(session.to_restore(session.current_session(), again=True))
                desk = commands.desk(self.config, mode, self.watch, can_restore)
                result = commands.interpret(self.llm, language, text, desk)
                if self.llm.backend == "ollama":
                    result = commands.only_what_was_asked(result, text, commands.all_projects())
            except (brain.LLMError, ValueError, KeyError, OSError, TypeError) as e:
                log.warning("command failed: %s", e)
                GLib.idle_add(self._command_failed, e)
                return
            GLib.idle_add(self._run_actions, result)

        threading.Thread(target=work, name="command", daemon=True).start()

    def _could_not(self, error):
        return self._say("command_failed", why=self._say(commands.trouble(error)))

    def _command_failed(self, error):
        hint = getattr(error, "hint", None)
        self._command_card(self._say(hint, **error.params) if hint else self._could_not(error))
        return False

    def _run_actions(self, result):
        """Does what was asked, checking everything named against what's really here."""
        say = (result.get("say") or "").strip()
        answered_elsewhere = False  # the steps report on their own card
        if result.get("unsure"):
            say = self._say("command_unsure")
        elif self.llm.backend == "ollama" and result.get("actions"):
            say = ""  # a small model's "done!" isn't worth much: say what really happened
        elif result.get("dropped"):
            say = say or self._say("command_nothing")
        for action in result.get("actions", [])[:5]:
            kind = action.get("type")
            try:
                say, reported = self._run_action(kind, action, say)
            except (GLib.Error, OSError, subprocess.SubprocessError, ValueError) as e:
                log.warning("command: %s failed: %s", kind, e)
                say, reported = self._could_not(e), False
            answered_elsewhere = answered_elsewhere or reported
        if not answered_elsewhere:
            self._command_card(say or self._say("command_nothing"))
        return False

    def _run_action(self, kind, action, say):
        """Does one action. Returns what to say, and whether its steps report on their own card."""
        answered_elsewhere = False
        if kind == "mode" and self.config.mode(action.get("mode")):
            self.call("RequestMode", "(s)", (action["mode"],))
            say = say or self._say("command_mode", mode=self.config.mode(action["mode"])["label"])
        elif kind == "server":
            units = self.watch.match(action.get("target"))
            op = action.get("op")
            if not units or op not in ("restart", "stop", "log", "status"):
                say = self._say("command_no_server")
                units = []
            for unit in units[:1] if op in ("log", "status") else units:
                # The answer's on the server's own card: "Done" on top of it would hide it.
                item = self.watch.get(unit)
                answered_elsewhere = op == "status" or (op == "log" and bool(item) and item["kind"] == servers.MANUAL)
                self._server_action(op, unit)
                say = say or self._say("command_done")
        elif kind == "system" and action.get("what") == "clear":
            self._offer_clearing()
            answered_elsewhere = True
        elif kind == "system":
            say = (commands.memory_report(self.config.language) if action.get("what") == "memory"
                   else commands.storage_report(self.config.language))
        elif kind == "git_report":
            self._command_card(self._say("command_working"))
            self._in_background(lambda: commands.git_report(self.config.language, commands.projects()),
                                lambda text: self._command_card(text, timeout=20))
            answered_elsewhere = True
        elif kind == "docker_images":
            self._command_card(self._say("command_working"), timeout=30)
            self._in_background(lambda: commands.images_report(self.config.language),
                                lambda text: self._command_card(text, timeout=25))
            answered_elsewhere = True
        elif kind == "compose_list":
            self._command_card(self._say("command_working"), timeout=30)
            self._in_background(lambda: commands.compose_report(self.config.language, commands.projects()),
                                lambda text: self._command_card(text, timeout=20))
            answered_elsewhere = True
        elif kind == "ports":
            self._command_card(self._say("command_working"), timeout=30)
            self._in_background(lambda: commands.ports_report(self.config.language, commands.projects(),
                                                              action.get("port"), action.get("about")),
                                lambda text: self._command_card(text, timeout=25))
            answered_elsewhere = True
        elif kind == "image_alone":
            say = self._say("image_alone", image=action.get("image", ""))
        elif kind in ("compose", "dev_service"):
            self._docker_or_service(kind, action)
            answered_elsewhere = True
        elif kind == "servers_list":
            say = commands.running_list(self.watch, action.get("show", "all"), self.config.language) \
                or self._say("command_nothing_running")
        elif kind == "setup_project":
            self._setup_project(action.get("project"))
            answered_elsewhere = True
        elif kind == "close_project":
            self._close_project(action.get("project"))
            answered_elsewhere = True
        elif kind == "restore_desk":
            snap = session.to_restore(session.current_session(), again=True)
            if snap:
                self.restore_snap = snap
                self._run_restore(snap)
                answered_elsewhere = True
            else:
                say = self._say("command_no_desk")
        elif kind == "open_app":
            if commands.open_app(action.get("app")):
                say = say or self._say("command_done")
            else:
                say = self._say("command_no_app")
        elif kind == "open_url":
            if commands.safe_url(action.get("url")):
                commands.open_uri(action["url"])
                say = say or self._say("command_done")
            else:
                say = self._say("command_nothing")
        elif kind == "open_folder":
            path = commands.folder_path(action.get("path"), commands.all_projects())
            if path:
                commands.open_folder(path)
                say = say or self._say("command_done")
            else:
                say = self._say("command_no_folder")
        elif kind == "edit_modes" and action.get("request"):
            self._edit_modes(action["request"])
            answered_elsewhere = True
        elif kind == "mode_list":
            self._edit_list(action)
            answered_elsewhere = True
        elif kind == "focus_today":
            say = commands.focus_answer(self.config)
        elif kind == "breaks":
            on = bool(action.get("on"))
            say = self._say("breaks_on" if on else "breaks_off") if self._set_breaks(on) else self._say("command_nothing")
        elif kind == "room_warnings":
            on = bool(action.get("on"))
            say = self._say("room_on" if on else "room_off") if self._set_room_warnings(on) else self._say("command_nothing")
        elif kind == "label":
            act = self.activity
            mode = self.config.mode(self.mode)
            if not act or not act.title:
                say = self._say("command_no_window")
            elif not mode or not mode.get("focus"):
                say = self._say("command_free_mode")
            else:
                (self._allow if action.get("verdict") == "on_task" else self._mark_distraction)(act)
                answered_elsewhere = True
        return say, answered_elsewhere

    def _setup_project(self, name):
        path = commands.find_project(name, commands.all_projects())
        if not path:
            self._command_card(self._say("command_no_project"))
            return

        def work():
            try:
                routine.write_state(last_project=path)  # what the morning offer goes by without Claude Code
                plan = routine.learn(project=path)
            except Exception as e:  # anything from reading transcripts or the browser history
                log.warning("command: can't plan %s: %s", path, e)
                plan = None
            GLib.idle_add(self._project_planned, plan)

        threading.Thread(target=work, name="plan", daemon=True).start()

    def _close_project(self, name):
        path = commands.find_project(name, commands.all_projects())
        if not path:
            self._command_card(self._say("command_no_project"))
            return
        self._command_card(self._say("project_closing", name=os.path.basename(path)), timeout=120, busy=True)
        # A setup still going mustn't start things or open pages after this.
        setting_up = self.routine_running if self.routine_running and self.routine_running["project"] == path else None
        if setting_up:
            setting_up["cancel"].set()
            self.routine_running = self.routine_plan = None

        def work():
            if setting_up:
                setting_up["thread"].join(timeout=ROUTINE_STOP_WAIT_S)
            try:
                report = servers.close_project(self.watch, path)
                terminals = session.project_terminals(path)
                hand = self.watch.hand_started_in(path)
            except (OSError, subprocess.SubprocessError, ValueError) as e:
                log.warning("command: can't close %s: %s", path, e)
                GLib.idle_add(self._command_failed, e)
                return
            GLib.idle_add(self._project_closed, path, report, terminals, hand, servers.git_state(path))

        threading.Thread(target=work, name="close-project", daemon=True).start()

    def _not_in_git(self, git):
        """The line about (files not committed, commits not pushed), or "" if there's nothing to say."""
        changed, unpushed = git or (0, 0)
        return " · ".join(([self._say("project_uncommitted", count=changed)] if changed else []) +
                          ([self._say("project_unpushed", count=unpushed)] if unpushed else []))

    def _project_closed(self, path, report, terminals, hand, git=None):
        """Servers you started yourself don't get stopped without asking. If nobody answers, they keep running."""
        if not hand:
            return self._project_report(path, report, terminals, git)
        self.closing_hand = {"path": path, "report": report, "terminals": terminals, "items": hand, "git": git}
        lines = [self._say("project_hand_started"), " · ".join(item["label"] for item in hand), self._not_in_git(git)]
        self.card({
            "id": "close-project",
            "kind": "command",
            "title": self._say("project_hand_title", name=os.path.basename(path)),
            "text": "\n".join(line for line in lines if line),
            "actions": [{"id": "project:keep-hand", "label": self._say("project_hand_keep")},
                        {"id": "project:stop-hand", "label": self._say("project_hand_stop"), "primary": True}],
            "timeout": 45,
        })
        return False

    def _answer_hand_started(self, stop):
        closing, self.closing_hand = self.closing_hand, None

        def work():
            report = list(closing["report"])
            for item in closing["items"]:
                try:
                    state = ("stopped" if self.watch.stop(item["unit"]) else "failed") if stop else "left"
                except (OSError, subprocess.SubprocessError) as e:
                    log.warning("command: can't stop %s: %s", item["label"], e)
                    state = "failed"
                report.append((item["label"], state, ""))
            GLib.idle_add(self._project_report, closing["path"], report, closing["terminals"], closing["git"])

        threading.Thread(target=work, name="close-project", daemon=True).start()

    def _project_report(self, path, report, terminals, git=None):
        name = os.path.basename(path)
        log.info("closed %s: %s, %d terminals open", name, report, len(terminals))
        self._send_servers()
        marks = {"stopped": "✓", "failed": "✗"}
        not_in_git = self._not_in_git(git)
        lines = [not_in_git] if not_in_git else []
        lines += [self._say("project_kept", label=label, other=other) if state == "kept"
                  else self._say("project_hand_kept", label=label) if state == "left"
                  else f"{marks[state]} {label}" for label, state, other in report]
        lines.append(self._say("project_tabs"))
        self.closing_terminals = terminals
        actions = []
        if terminals:
            lines.append(self._say("project_terminals"))
            actions = [{"id": "project:keep-terminals", "label": self._say("project_keep_terminals")},
                       {"id": "project:close-terminals", "label": self._say("project_close_terminals"), "primary": True}]
        closed = any(state != "left" for _label, state, _other in report)
        title = self._say("project_closed", name=name) if closed else self._say("project_nothing", name=name)
        self.card({"id": "close-project", "kind": "command", "title": title, "text": "\n".join(lines),
                   "actions": actions, "timeout": 45 if terminals or not_in_git else 12})
        return False

    def _project_planned(self, plan):
        if not plan:
            self._command_card(self._say("command_no_project"))
            return False
        self.routine_plan = plan
        self.routine_marks = {}
        self._routine_card(plan, self._say("routine_running"), 120)
        self._run_routine(plan)
        return False

    def _edit_modes(self, request):
        self._command_card(self._say("command_edit_wait"), timeout=300, busy=True)
        language = self.config.language

        def work():
            try:
                summary, config = commands.edit_modes(self.llm, language, request)
            except (brain.LLMError, ValueError, KeyError, OSError) as e:
                log.warning("command: can't edit modes: %s", e)
                GLib.idle_add(self._command_failed, e)
                return
            GLib.idle_add(self._edit_ready, summary, config)

        threading.Thread(target=work, name="edit", daemon=True).start()

    def _edit_ready(self, summary, config):
        if config is None:
            self._command_card(summary or self._say("command_edit_same"))
            return False
        self.pending_edit = config
        self._command_card(summary, timeout=120, actions=[
            {"id": "command:cancel", "label": self._say("command_cancel")},
            {"id": "command:apply", "label": self._say("command_apply"), "primary": True}])
        return False

    def _apply_edit(self):
        config, self.pending_edit = self.pending_edit, None
        try:
            shutil.copy2(brain.MODES_FILE, brain.MODES_FILE.with_suffix(".json.bak"))
            brain.write_json_atomic(brain.MODES_FILE, config)
        except OSError as e:
            log.warning("command: can't save your modes: %s", e)
            self._command_failed(e)
            return
        self._command_card(self._say("command_applied"))

    # ---- the first-run guide ---------------------------------------------

    def _maybe_guide(self):
        """Once nothing else is on offer. Returns whether to look again in a moment."""
        if self.guide_step is not None or not guide.due():
            return False
        if self.restore_snap or self.routine_plan:
            self.guide_tries += 1
            return self.guide_tries < GUIDE_TRIES
        guide.started()
        self._guide_show(0)
        return False

    def _guide_show(self, index):
        self.guide_step = index
        step = guide.STEPS[index]
        last = index == len(guide.STEPS) - 1
        text = f"guide_{step}_noai" if step == "command" and not self.llm.available else f"guide_{step}"
        actions = [] if last else [{"id": "guide:skip", "label": self._say("guide_skip")}]
        actions.append({"id": "guide:next", "label": self._say("guide_done" if last else "guide_next"), "primary": True})
        self.card({
            "id": "guide",
            "kind": "command" if step == "command" else "suggest",
            "icon": {"name": {"modes": "starred-symbolic", "inbox": "mail-unread-symbolic"}.get(step, "starred-symbolic")},
            "title": self._say(f"guide_{step}_title"),
            "subtitle": self._say("guide_progress", n=index + 1, total=len(guide.STEPS)),
            "text": self._say(text),
            "actions": actions,
            "anchor_mode": self.mode,
            "timeout": 45,
        })

    def _guide_next(self):
        if self.guide_step is None:
            return
        if self.guide_step + 1 < len(guide.STEPS):
            self._guide_show(self.guide_step + 1)
        else:
            guide.finished()
            self.guide_step = None

    def _maybe_ask_whatsapp(self):
        """WhatsApp Web can only reach the inbox if Chrome may show its notifications."""
        self.whatsapp_checked = True
        if guide.ask_whatsapp():
            self.card({
                "id": "whatsapp",
                "kind": "suggest",
                "icon": {"name": "mail-unread-symbolic"},
                "title": self._say("whatsapp_title"),
                "text": self._say("whatsapp_text"),
                "actions": [{"id": "whatsapp:later", "label": self._say("later")},
                            {"id": "guide:whatsapp", "label": self._say("whatsapp_open"), "primary": True}],
                "timeout": 30,
            })

    def _open_whatsapp_setting(self):
        command = session.browser_command({})
        if command:
            subprocess.Popen(launcher.own_scope([*command, guide.WHATSAPP_SETTINGS]), start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ---- break reminders -------------------------------------------------

    def _maybe_break(self, mode, idle_ms):
        """A few minutes away, or a mode that isn't for focus, counts as a break. So does the laptop sleeping."""
        now = time.time()
        slept = now - self.last_wall > TICK_SECONDS * 6
        self.last_wall = now
        if slept or idle_ms > AWAY_AFTER_MS or not mode or not mode.get("focus"):
            self.working_since = None
            return
        self.working_since = self.working_since or now
        settings = self.config.breaks
        if not settings["enabled"] or now - max(self.working_since, self.break_shown) < settings["after_minutes"] * 60:
            return
        self.break_shown = now
        worked = commands.duration(round((now - self.working_since) / 60), self.config.language)
        log.info("break reminder after %s", worked)
        self.card({
            "id": "break",
            "kind": "suggest",
            "icon": {"name": "alarm-symbolic"},
            "title": self._say("break_title", time=worked),
            "text": self._say("break_text"),
            "actions": [{"id": "break:off", "label": self._say("break_off")},
                        {"id": "break:ok", "label": self._say("break_ok"), "primary": True}],
            "anchor_mode": self.mode,
            "timeout": 20,
        })

    def _set_breaks(self, on):
        return self._set_enabled("breaks", on)

    def _set_room_warnings(self, on):
        return self._set_enabled("room_warnings", on)

    def _set_enabled(self, section, on):
        path = self.config.path
        try:
            data = json.loads(path.read_text())
            data[section] = {**data.get(section, {}), "enabled": on}
            brain.write_json_atomic(path, data)
        except (OSError, ValueError) as e:
            log.warning("can't change %s: %s", section, e)
            return False
        self._refresh_config()
        return True

    # ---- asking about the windows it isn't sure of ------------------------

    def _maybe_review(self):
        """Only at a pause, a few times a day, and never over something else that's on offer."""
        self.last_review_try = time.monotonic()
        mode = self.config.mode(self.mode) if self.mode else None
        if not mode or not mode.get("focus") or self.restore_snap or self.routine_plan or routine.due():
            return False
        entry = self.unsure.next(self.mode, self.learned)
        if not entry:
            return False
        self.review_item = {**entry, "id": f"review-{int(time.time())}"}
        self.card({
            "id": self.review_item["id"],
            "kind": "suggest",
            "icon": {"app_id": entry["app_id"]},
            "title": entry["title"][:70],
            "subtitle": self._say("review_subtitle", time=commands.duration(round(entry["seconds"] / 60), self.config.language)),
            "text": self._say("review_text", mode=mode["label"]),
            "actions": [{"id": "review:skip", "label": self._say("review_skip")},
                        {"id": "review:distraction", "label": self._say("review_distraction")},
                        {"id": "review:work", "label": self._say("review_work"), "primary": True}],
            "anchor_mode": self.mode,
            "timeout": 25,
        })
        return False

    def _answer_review(self, action):
        entry, self.review_item = self.review_item, None
        if action == "review:skip":
            return
        verdict = "on_task" if action == "review:work" else "off_task"
        self.learned.add(entry["mode"], entry["app_id"], entry["title"], verdict)
        log.info("%s | %s is %s, by your word", entry["mode"], entry["title"][:60], verdict)
        if self.activity and learned.simple_title(self.activity.title) == learned.simple_title(entry["title"]):
            self._reclassify()
        GLib.timeout_add(600, self._maybe_review)  # while you're answering anyway

    # ---- "this is work" -------------------------------------------------

    def _edit_mode(self, key, edit):
        """Rewrites one mode in modes.json; the dock picks the change up from the file.
        Returns (the mode as it was, whatever edit returned), or None if nothing was written."""
        path = self.config.path
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("can't edit modes.json: %s", e)
            return None
        mode = data.get("modes", {}).get(key)
        if not isinstance(mode, dict):
            return None
        before = json.loads(json.dumps(mode))
        result = edit(mode)
        errors = brain.validate_config(data)
        if errors:
            log.warning("edit to %s rejected: %s", key, "; ".join(errors))
            return None
        brain.write_json_atomic(path, data)
        self._refresh_config()
        return before, result

    def _allow(self, act):
        key = self.mode
        edited = self._edit_mode(key, lambda mode: brain.allow_activity(
            mode, act.app_id, act.app_name, act.title, act.site))
        if not edited:
            return
        before, keyword = edited
        label = self.config.mode(key)["label"]
        log.info("%s | %s allowed as %r", key, act.label, keyword)
        card_id = f"allowed-{act.seq}"
        self.allow_undo = (card_id, key, before, act.title)
        self.learned.add(key, act.app_id, act.title, "on_task")
        self.card({
            "id": card_id,
            "kind": "fits",
            "icon": {"app_id": act.app_id},
            "title": keyword.strip(" /:") or act.app_name,
            "subtitle": self._say("allowed", mode=label),
            "text": self._say("allowed_text", mode=label),
            "actions": [{"id": "undo-allow", "label": self._say("undo")}],
            "anchor_mode": key,
            "timeout": 8,
        })
        self._reclassify()

    def _edit_list(self, action):
        """Allows or blocks something by name in a mode: "blokir reddit di mode writing"."""
        key = action.get("mode") or self.mode
        mode = self.config.mode(key)
        if not mode or not mode.get("focus"):
            self._command_card(self._say("command_free_mode"))
            return
        field, word = action["field"], action["word"]
        other = "block" if field == "allow" else "allow"

        def edit(raw):
            raw[other] = [k for k in raw.get(other, []) if k.lower() != word.lower()]
            raw[field] = [*(k for k in raw.get(field, []) if k.lower() != word.lower()), word]

        edited = self._edit_mode(key, edit)
        if not edited:
            self._command_card(self._say("command_nothing"))
            return
        card_id = f"list-{int(time.time())}"
        self.allow_undo = (card_id, key, edited[0], "")
        self.card({
            "id": card_id,
            "kind": "fits",
            "icon": {"name": "object-select-symbolic"},
            "title": self._say("list_added" if field == "allow" else "list_blocked", word=word, mode=mode["label"]),
            "actions": [{"id": "undo-allow", "label": self._say("undo")}],
            "anchor_mode": key,
            "timeout": 8,
        })
        self._reclassify()

    def _mark_distraction(self, act):
        """"This isn't work": from now on this window counts as a distraction in this mode."""
        key = self.mode
        self.learned.add(key, act.app_id, act.title, "off_task")
        label = self.config.mode(key)["label"]
        log.info("%s | %s marked as a distraction", key, act.label)
        act.verdict = "off_task"
        act.carded = True
        self.drift_since = self.drift_since or time.monotonic()
        self.last_nudge = time.monotonic()
        card_id = f"marked-{act.seq}"
        self.mark_undo = (card_id, key, act.title)
        self.card({
            "id": card_id,
            "kind": "fits",
            "icon": {"app_id": act.app_id},
            "title": act.site or act.app_name,
            "subtitle": self._say("marked", mode=label),
            "text": self._say("marked_text", mode=label),
            "actions": [{"id": "undo-mark", "label": self._say("undo")}],
            "anchor_mode": key,
            "timeout": 8,
        })

    def _reclassify(self):
        act = self.activity
        if act:
            act.verdict = None
            act.carded = False
            self._classify(act.seq)

    # ---- classification -------------------------------------------------

    def _refresh_config(self):
        if self.config.refresh():
            self.llm = brain.LLM(self.config.llm)
            self.llm_warned = False
            log.info("config reloaded · AI: %s", self.llm.describe())

    def _llm_ready(self):
        return self.llm.available and time.monotonic() >= self.llm_paused_until

    def _llm_failed(self, error):
        log.warning("AI call failed, pausing it for %ds: %s", LLM_BACKOFF, error)
        self.llm_paused_until = time.monotonic() + LLM_BACKOFF
        hint = getattr(error, "hint", None)
        if hint and not self.llm_warned:  # once: it's working out without it, just with less
            self.llm_warned = True
            self._command_card(self._say(hint, **error.params), timeout=15)

    def _classify(self, seq):
        act = self.activity
        if not act or act.seq != seq:
            return False
        self._refresh_config()
        mode = self.config.mode(self.mode)
        if not mode:
            return False
        if not mode.get("focus"):
            self._apply(act, brain.verdict("neutral", source="free"))
            return False

        # Your own word first, then what's written down or plain, and only then a guess or a model.
        said = self.learned.correction(self.mode, act.title)
        if said:
            self._apply(act, {**brain.verdict(said, source="you", site=act.app_name), "belongs_to": self._rule_home(act)})
            return False
        ruled = brain.rule_verdict(mode, act.app_id, act.app_name, act.title, self.config.language)
        if ruled:
            self._apply(act, {**ruled, "belongs_to": self._rule_home(act)})
            return False
        plain = sites.window_verdict(mode, act.app_id, act.title)
        if plain:
            self._apply(act, {**brain.verdict(plain[0], source="site", site=plain[1] or act.app_name),
                              "belongs_to": self._rule_home(act)})
            return False

        # Not what a local model said back when it judged windows: that's why it doesn't any more.
        key = f"{self.config.fingerprint(self.mode)}|{self.llm.backend}|{act.app_id}|{act.title}"
        cached = None if self.llm.backend == "ollama" else self.cache.get(key)
        if cached:
            self._apply(act, {**cached, "source": "cache"})
            return False

        guess = self.learned.predict(self.mode, act.app_id, act.title)
        if guess:
            self._apply(act, {**brain.verdict(guess[0], source="learned", site=act.app_name),
                              "belongs_to": self._rule_home(act)})
            return False

        # A small local model calls nearly everything a distraction once titles get ambiguous (it agreed
        # with Claude on 12 of 113 real windows, and flagged 98). It doesn't get to judge windows.
        if self.llm.backend == "ollama" or act.app_id in self.config.llm["exclude_apps"] or not self._llm_ready():
            self._apply(act, brain.verdict("neutral", source="default"))
            return False
        if key in self.inflight:
            return False
        self.inflight.add(key)
        prompt = brain.activity_prompt(self.mode, mode, self.config.modes(), act.app_id, act.app_name, act.title)
        future = self.pool.submit(self.llm.ask, brain.activity_system(self.config.language), prompt, brain.ACTIVITY_SCHEMA)
        future.add_done_callback(lambda f: GLib.idle_add(self._on_classified, f, seq, key))
        return False

    def _rule_home(self, act):
        """The other mode whose rules claim this activity, if exactly one does."""
        homes = [key for key, mode in self.config.modes()
                 if key != self.mode and (brain.rule_verdict(mode, act.app_id, act.app_name, act.title, self.config.language) or {})
                 .get("verdict") == "on_task"]
        return homes[0] if len(homes) == 1 else ""

    def _on_classified(self, future, seq, key):
        self.inflight.discard(key)
        current = self.activity if self.activity and self.activity.seq == seq else None
        try:
            result = future.result()
        except Exception as e:  # LLMError, JSON errors, anything from the SDK
            self._llm_failed(e)
            if current:
                self._apply(current, brain.verdict("neutral", source="error"))
            return False
        if result.get("verdict") not in ("on_task", "off_task", "neutral"):
            result = brain.verdict("neutral", source="llm")
        home = result.get("belongs_to", "")
        result = {
            "verdict": result["verdict"],
            "reason": str(result.get("reason", ""))[:120],
            "site": str(result.get("site", ""))[:60],
            "belongs_to": home if self.config.mode(home) else "",
            "source": "llm",
        }
        self.cache.put(key, result)
        if current:
            if self.llm.backend != "ollama":  # Claude's word is worth learning from; a small model's isn't
                self.learned.add(self.mode, current.app_id, current.title, result["verdict"], source="claude")
            self._apply(current, result, fresh=True)
        return False

    def _on_triaged(self, future, notification_id, app, title, body, mode_at):
        try:
            result = future.result()
        except Exception as e:
            self._llm_failed(e)
            return False
        log.info("notif %s | %s → %s [%s] %s", app, title[:60],
                 "surface" if result.get("surface") else "hold", result.get("mode", ""), result.get("reason", ""))
        if result.get("surface") and mode_at == self.mode:
            self._held_card(notification_id, app, title, body, result)
        return False

    def _apply(self, act, result, fresh=False):
        act.verdict = result["verdict"]
        act.reason = result.get("reason", "")
        act.source = result.get("source", "")
        act.site = result.get("site", "")
        act.belongs_to = result.get("belongs_to", "")
        log.info("%s | %s → %s [%s] %s · %s", self.mode, act.label, act.verdict, act.source,
                 act.site or "-", act.reason)
        now = time.monotonic()
        if act.verdict == "off_task":
            self.drift_since = self.drift_since or now
        elif act.verdict == "on_task":
            self.drift_since = None
            self.last_nudge = None

        mode = self.config.mode(self.mode)
        if not mode or not mode.get("focus") or act.carded:
            return
        if act.verdict == "off_task":
            # Ask right away; the timed nudge in _maybe_nudge follows up if you stay.
            act.carded = True
            self.last_nudge = now
            self._distraction_card(act, "distraction")
        elif act.verdict == "on_task" and fresh:
            # Only for things the model just judged: rules and cache hits are old news.
            seen = (self.mode, act.site or act.label)
            if seen not in self.fits_shown:
                self.fits_shown.add(seen)
                act.carded = True
                self._fits_card(act)

    # ---- the clock ------------------------------------------------------

    def _idle_ms(self):
        try:
            result = self.bus.call_sync(
                "org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                "org.gnome.Mutter.IdleMonitor", "GetIdletime", None, GLib.VariantType("(t)"),
                Gio.DBusCallFlags.NONE, 1000, None)
            return result.unpack()[0]
        except GLib.Error:
            return 0

    def _account(self, idle_ms=None):
        now = time.monotonic()
        elapsed = min(now - self.last_tick, TICK_SECONDS * 2)  # a suspend isn't screen time
        self.last_tick = now
        act = self.activity
        if not act or self.mode is None or elapsed <= 0:
            return
        idle_ms = self._idle_ms() if idle_ms is None else idle_ms
        kind = "idle" if idle_ms > IDLE_AFTER_MS else (act.verdict or "neutral")
        self.stats.add(self.mode, kind, elapsed, act.label)
        if kind == "neutral" and act.source == "default" and act.app_id not in self.config.llm["exclude_apps"]:
            self.unsure.note(self.mode, act.app_id, act.app_name, act.title, elapsed)

    def _safely(self, part, *args):
        """Runs one part of the tick, which loses only its own turn if it fails. GLib drops a timer whose callback
        raises, and everything the tick does would stop with it while the daemon looks fine."""
        try:
            return part(*args)
        except Exception as e:
            name = getattr(part, "__name__", repr(part))
            now = time.monotonic()
            # One that keeps failing would otherwise write a traceback to the journal every few seconds.
            if now - self.tick_logged.get(name, -TICK_LOG_EVERY_S) >= TICK_LOG_EVERY_S:
                self.tick_logged[name] = now
                log.exception("%s failed: %s", name, e)
            return None

    def _tick_away(self, idle_ms):
        # Back at a laptop left open (no unlock to notice): that's the start of the day too.
        if idle_ms > AWAY_AFTER_MS:
            self.away = True
        elif self.away and idle_ms < TICK_SECONDS * 2000:
            self.away = False
            self._maybe_offer_routine()
            self._maybe_review()

    def _tick_coach(self, idle_ms):
        act = self.activity
        mode = self.config.mode(self.mode) if self.mode else None
        self._maybe_break(mode, idle_ms)
        if time.monotonic() - self.last_review_try > REVIEW_EVERY_S and idle_ms < IDLE_AFTER_MS:
            self._maybe_review()
        if act and mode:
            if mode.get("focus"):
                self._maybe_nudge(act, mode, idle_ms)
            else:
                self._maybe_suggest(act)

    def _tick(self):
        self._safely(self._refresh_config)
        idle_ms = self._safely(self._idle_ms) or 0
        self._safely(self._account, idle_ms)
        self._safely(self._tick_away, idle_ms)
        self._safely(self._tick_coach, idle_ms)

        if self.mode is not None and time.monotonic() - self.last_server_check > SERVER_CHECK_EVERY:
            self._safely(self._check_servers)

        if time.monotonic() - self.last_room_check > ROOM_CHECK_EVERY:
            self.last_room_check = time.monotonic()
            self._safely(self._check_room)

        if self.mode is not None and time.monotonic() - self.last_snapshot > SNAPSHOT_EVERY:
            self.last_snapshot = time.monotonic()
            self._safely(self._snapshot_desk)

        if time.monotonic() - self.last_save > SAVE_EVERY:
            self.last_save = time.monotonic()
            self._safely(self.stats.save)
            self._safely(self.cache.save)
        return True

    def _maybe_nudge(self, act, mode, idle_ms):
        now = time.monotonic()
        if act.verdict != "off_task" or not self.drift_since or idle_ms > AWAY_AFTER_MS:
            return
        if now - self.drift_since < mode["nudge_after"]:
            return
        if self.last_nudge and now - self.last_nudge < mode["renudge_after"]:
            return
        self.last_nudge = now
        self._distraction_card(act, "nudge")
        log.info("nudged: %s", act.label)
        if mode["on_distraction"] == "minimize":
            self.call("MinimizeFocused")

    def _maybe_suggest(self, act):
        now = time.monotonic()
        if act.suggested or now - act.since < SUGGEST_AFTER:
            return
        act.suggested = True
        matches = []
        for key, mode in self.config.modes():
            if key == self.mode or not mode.get("focus"):
                continue
            ruled = brain.rule_verdict(mode, act.app_id, act.app_name, act.title, self.config.language)
            if ruled and ruled["verdict"] == "on_task":
                matches.append((key, mode))
        if len(matches) != 1:
            return  # nothing, or ambiguous (an app pinned in several modes)
        key, mode = matches[0]
        if now - self.suggested_at.get(key, -SUGGEST_COOLDOWN) < SUGGEST_COOLDOWN:
            return
        self.suggested_at[key] = now
        self.card({
            "id": f"suggest-{act.seq}",
            "kind": "suggest",
            "icon": {"app_id": act.app_id},
            "title": act.app_name,
            "subtitle": self._say("suggest_subtitle", mode=mode["label"]),
            "text": self._say("suggest_text", mode=mode["label"]),
            "actions": [{"id": "later", "label": self._say("not_now")},
                        {"id": f"go:{key}", "label": self._say("start", mode=mode["label"]), "primary": True,
                         "icon": mode.get("icon")}],
            "anchor_mode": key,
            "timeout": 12,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(message)s")

    loop = GLib.MainLoop()
    deskaid = Brain(loop)
    deskaid.start()

    def stop():
        loop.quit()
        return GLib.SOURCE_REMOVE

    for sig in (signal.SIGINT, signal.SIGTERM):
        # GLib 2.80 (Ubuntu 24.04) has no GLibUnix.signal_add yet; GLib.unix_signal_add is the same call.
        getattr(GLibUnix, "signal_add", GLib.unix_signal_add)(GLib.PRIORITY_DEFAULT, sig, stop)
    try:
        loop.run()
    finally:
        deskaid.shutdown()


if __name__ == "__main__":
    main()
