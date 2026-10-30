#!/usr/bin/env python3
"""deskaid: the brain behind DeskAI.

Listens to the GNOME Shell extension over D-Bus, classifies what you're doing
against the active mode (rules first, Claude for the ambiguous rest), nudges
you when you drift, suggests a mode when you start working without one, and
lets through held notifications that really can't wait.
"""

import argparse
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
import guide  # noqa: E402
import routine  # noqa: E402
import servers  # noqa: E402
import session  # noqa: E402
from brain import BRAIN_NAME, BUS_NAME, OBJECT_PATH, log  # noqa: E402

TICK_SECONDS = 5
ROUTINE_DELAY_S = 6          # after login or unlock, let the desktop settle before asking
ROUTINE_SNOOZE_S = 45 * 60
SNAPSHOT_EVERY = 30          # seconds between snapshots of the desk, for after a logout
SERVER_CHECK_EVERY = 10      # seconds between looks at the servers on the desk
GUIDE_DELAY_S = 15           # the first-run guide waits for the desktop, and for anything else on offer
GUIDE_TRIES = 8
CLASSIFY_DELAY_MS = 1500     # ignore alt-tab flicker
IDLE_AFTER_MS = 120_000      # no input this long counts as idle, not as the window
AWAY_AFTER_MS = 300_000      # don't nudge someone who isn't there
SUGGEST_AFTER = 120
SUGGEST_COOLDOWN = 1800
LLM_BACKOFF = 300
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


class Stats:
    """Seconds per mode and verdict, one file per day."""

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
            try:
                self.data = json.loads((self.dir / f"{day}.json").read_text())
            except (OSError, ValueError):
                self.data = {"modes": {}, "distractions": {}}
        return self.data

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
        brain.write_json_atomic(self.dir / f"{self.day}.json", self.data)
        self.dirty = False


class Brain:
    def __init__(self, loop):
        self.loop = loop
        self.config = brain.Config()
        self.config.refresh()
        self.claude = brain.Claude(self.config.llm)
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="claude")
        self.cache = VerdictCache(brain.CACHE_DIR / "verdicts.json")
        self.stats = Stats(brain.STATE_DIR / "stats")
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
        self.pending_edit = None  # a change to modes.json waiting for your OK
        self.guide_step = None
        self.guide_tries = 0
        self.whatsapp_checked = False
        self.card_act = None      # what the last distraction card was about, for "Allow in …"
        self.allow_undo = None    # (card id, mode key, the mode as it was)
        self.last_tick = time.monotonic()
        self.last_save = time.monotonic()

    # ---- wiring ---------------------------------------------------------

    def start(self):
        Gio.bus_own_name_on_connection(
            self.bus, BRAIN_NAME, Gio.BusNameOwnerFlags.DO_NOT_QUEUE, None, self._on_name_lost)
        self.bus.signal_subscribe(
            BUS_NAME, BUS_NAME, None, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, self._on_signal)
        Gio.bus_watch_name_on_connection(
            self.bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE, self._on_dock_appeared, self._on_dock_vanished)
        GLib.timeout_add_seconds(TICK_SECONDS, self._tick)
        log.info("deskaid up · Claude backend: %s · model: %s", self.claude.backend, self.claude.model)

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
        home = result.get("mode") if self.config.mode(result.get("mode")) else self._escape_mode()
        if not home or home == self.mode:
            # It belongs to what you're doing anyway: just let the banner through.
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
        if not self.config.llm["triage_notifications"] or not self._llm_ready():
            return
        prompt = brain.notification_prompt(self.mode, mode, self.config.modes(), app, title, body)
        future = self.pool.submit(self.claude.ask, brain.notify_system(self.config.language), prompt, brain.NOTIFY_SCHEMA)
        mode_at = self.mode
        future.add_done_callback(
            lambda f: GLib.idle_add(self._on_triaged, f, notification_id, app, title, body, mode_at))

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
        elif action == "undo-allow" and self.allow_undo and card_id == self.allow_undo[0]:
            _, key, before = self.allow_undo
            self.allow_undo = None

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

    def _run_routine(self, plan):
        def progress(step, state):
            GLib.idle_add(self._routine_progress, plan, step, state)

        def set_mode(mode):
            GLib.idle_add(lambda: self.call("SetMode", "(s)", (mode,)) and False)

        def work():
            try:
                routine.run(plan, progress, set_mode)
            except Exception as e:  # never take the daemon down with it
                log.exception("routine failed: %s", e)
            GLib.idle_add(self._routine_finished, plan)

        log.info("routine: setting up %s", plan.get("project"))
        threading.Thread(target=work, name="routine", daemon=True).start()

    def _routine_card(self, plan, title, timeout):
        marks = {"running": "…", "done": "✓", "skipped": "✓", "failed": "✗"}
        lines = [f"{marks.get(self.routine_marks.get(id(step)), '·')} {routine.label(step)}"
                 for step in plan["steps"] if step["type"] != "mode"]
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

    def _routine_finished(self, plan):
        failed = sum(1 for state in self.routine_marks.values() if state == "failed")
        title = self._say("routine_partly", n=failed) if failed else self._say("routine_ready")
        self._routine_card(plan, title, 12)
        self.routine_plan = None
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
            servers.open_log(item)
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

    # ---- the command bar --------------------------------------------------

    def _command_card(self, text, actions=(), timeout=10):
        self.card({"id": "command", "kind": "command", "title": text if len(text) <= 60 else "DeskAI",
                   "text": "" if len(text) <= 60 else text, "actions": list(actions), "timeout": timeout})

    def on_command(self, text):
        log.info("command: %s", text[:120])
        quick = commands.quick(text, [(key, mode["label"]) for key, mode in self.config.modes()], self.watch)
        if quick:
            self._run_actions(quick)
            return
        if not self.claude.available:
            self._command_card(self._say("command_needs_ai"))
            return
        language, mode = self.config.language, self.mode

        def work():
            try:
                can_restore = bool(session.to_restore(session.current_session(), again=True))
                desk = commands.desk(self.config, mode, self.watch, can_restore)
                result = commands.interpret(self.claude, language, text, desk)
            except (brain.LLMError, ValueError, KeyError, OSError, TypeError) as e:
                log.warning("command failed: %s", e)
                GLib.idle_add(self._command_failed, str(e))
                return
            GLib.idle_add(self._run_actions, result)

        threading.Thread(target=work, name="command", daemon=True).start()

    def _command_failed(self, error):
        self._command_card(self._say("command_failed", error=error[:160]))
        return False

    def _run_actions(self, result):
        """Does what was asked, checking everything named against what's really here."""
        say = (result.get("say") or "").strip()
        answered_elsewhere = False  # the steps report on their own card
        for action in result.get("actions", [])[:5]:
            kind = action.get("type")
            if kind == "mode" and self.config.mode(action.get("mode")):
                self.call("RequestMode", "(s)", (action["mode"],))
                say = say or self._say("command_mode", mode=self.config.mode(action["mode"])["label"])
            elif kind == "server":
                units = self.watch.match(action.get("target"))
                op = action.get("op")
                if not units or op not in ("restart", "stop", "log"):
                    say = self._say("command_no_server")
                for unit in units[:1] if op == "log" else units:
                    self._server_action(op, unit)
                    say = say or self._say("command_done")
            elif kind == "setup_project":
                self._setup_project(action.get("project"))
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
            elif kind == "open_url" and commands.safe_url(action.get("url")):
                commands.open_uri(action["url"])
                say = say or self._say("command_done")
            elif kind == "open_folder":
                path = commands.folder_path(action.get("path"), commands.projects())
                if path:
                    commands.open_folder(path)
                    say = say or self._say("command_done")
                else:
                    say = self._say("command_no_folder")
            elif kind == "edit_modes" and action.get("request"):
                self._edit_modes(action["request"])
                answered_elsewhere = True
        if not answered_elsewhere:
            self._command_card(say or self._say("command_nothing"))
        return False

    def _setup_project(self, name):
        path = commands.projects().get(name or "")
        if not path:
            self._command_card(self._say("command_no_project"))
            return

        def work():
            try:
                plan = routine.learn(project=path)
            except Exception as e:  # anything from reading transcripts or the browser history
                log.warning("command: can't plan %s: %s", path, e)
                plan = None
            GLib.idle_add(self._project_planned, plan)

        threading.Thread(target=work, name="plan", daemon=True).start()

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
        self._command_card(self._say("command_edit_wait"), timeout=300)
        language = self.config.language

        def work():
            try:
                summary, config = commands.edit_modes(self.claude, language, request)
            except (brain.LLMError, ValueError, KeyError, OSError) as e:
                log.warning("command: can't edit modes: %s", e)
                GLib.idle_add(self._command_failed, str(e))
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
            self._command_failed(str(e))
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
        text = f"guide_{step}_noai" if step == "command" and not self.claude.available else f"guide_{step}"
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
            subprocess.Popen([*command, guide.WHATSAPP_SETTINGS], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

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
        self.allow_undo = (card_id, key, before)
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

    def _reclassify(self):
        act = self.activity
        if act:
            act.verdict = None
            act.carded = False
            self._classify(act.seq)

    # ---- classification -------------------------------------------------

    def _refresh_config(self):
        if self.config.refresh():
            self.claude = brain.Claude(self.config.llm)
            log.info("config reloaded · Claude backend: %s", self.claude.backend)

    def _llm_ready(self):
        return self.claude.available and time.monotonic() >= self.llm_paused_until

    def _llm_failed(self, error):
        log.warning("Claude call failed, pausing it for %ds: %s", LLM_BACKOFF, error)
        self.llm_paused_until = time.monotonic() + LLM_BACKOFF

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

        ruled = brain.rule_verdict(mode, act.app_id, act.app_name, act.title, self.config.language)
        if ruled:
            self._apply(act, {**ruled, "belongs_to": self._rule_home(act)})
            return False

        key = f"{self.config.fingerprint(self.mode)}|{act.app_id}|{act.title}"
        cached = self.cache.get(key)
        if cached:
            self._apply(act, {**cached, "source": "cache"})
            return False

        if act.app_id in self.config.llm["exclude_apps"] or not self._llm_ready():
            self._apply(act, brain.verdict("neutral", source="default"))
            return False
        if key in self.inflight:
            return False
        self.inflight.add(key)
        prompt = brain.activity_prompt(self.mode, mode, self.config.modes(), act.app_id, act.app_name, act.title)
        future = self.pool.submit(self.claude.ask, brain.activity_system(self.config.language), prompt, brain.ACTIVITY_SCHEMA)
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

    def _tick(self):
        idle_ms = self._idle_ms()
        self._account(idle_ms)
        # Back at a laptop left open (no unlock to notice): that's the start of the day too.
        if idle_ms > AWAY_AFTER_MS:
            self.away = True
        elif self.away and idle_ms < TICK_SECONDS * 2000:
            self.away = False
            self._maybe_offer_routine()
        act = self.activity
        mode = self.config.mode(self.mode) if self.mode else None
        if act and mode:
            if mode.get("focus"):
                self._maybe_nudge(act, mode, idle_ms)
            else:
                self._maybe_suggest(act)

        if self.mode is not None and time.monotonic() - self.last_server_check > SERVER_CHECK_EVERY:
            self._check_servers()

        if self.mode is not None and time.monotonic() - self.last_snapshot > SNAPSHOT_EVERY:
            self.last_snapshot = time.monotonic()
            self._snapshot_desk()

        if time.monotonic() - self.last_save > SAVE_EVERY:
            self.last_save = time.monotonic()
            self.stats.save()
            self.cache.save()
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
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, sig, stop)
    try:
        loop.run()
    finally:
        deskaid.shutdown()


if __name__ == "__main__":
    main()
