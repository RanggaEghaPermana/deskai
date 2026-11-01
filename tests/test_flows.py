"""The brain's flows from event to card, without a dock. Run with: python3 -m unittest discover tests"""

import datetime
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import brain  # noqa: E402
import deskaid  # noqa: E402
import learned  # noqa: E402
import presets  # noqa: E402
import senders  # noqa: E402
import servers  # noqa: E402

DEFAULTS = ROOT / "extension" / "deskai@ranggaeghapermana.github.io" / "modes.default.json"


class Fake(deskaid.Brain):
    """The real logic, with what it would send to the dock written down instead."""

    def __init__(self, folder, backend="ollama", language="id", **config):
        self.config = brain.Config(folder / "modes.json")
        (folder / "modes.json").write_text(json.dumps({**json.loads(DEFAULTS.read_text()), "language": language, **config}))
        self.config.refresh()
        self.llm = SimpleNamespace(backend=backend, available=True, ask=mock.Mock())
        self.pool = mock.Mock()
        self.senders = senders.Senders(folder / "senders.json")
        self.learned = learned.Learned(folder / "learned.json")
        self.unsure = learned.Unsure(folder / "review.json")
        self.watch = servers.Watch()
        self.cards, self.calls = [], []
        self.mode = "building"
        self.activity = None
        self.llm_paused_until = 0.0
        self.sender_ask = self.sender_undo = self.review_item = self.closing_hand = None
        self.sender_asked_at = -deskaid.SENDER_ASK_GAP_S
        self.restore_snap = self.routine_plan = None
        self.working_since = None
        self.break_shown = 0.0
        self.room_warned = set()
        self.memory_low_since = None
        self.last_wall = time.time()
        self.last_review_try = time.monotonic()
        self.closing_terminals = []
        self.card_act = self.allow_undo = self.mark_undo = None
        self.drift_since = self.last_nudge = None
        self.servers_sent = None
        self.dock_seen = False
        self.dock_up = False
        self.docks_changed_timer = 0
        self.config_told = None
        self.routine_running = None
        self.presets = presets.Store(folder / "presets.json")
        self.preset_run = self.preset_asking = self.preset_meant = None

    def card(self, spec):
        self.cards.append(spec)

    def call(self, method, signature=None, args=None, reply=None):
        self.calls.append((method, args))

    def _refresh_config(self):
        self.config.refresh()

    def surfaced(self):
        return [args[0] for method, args in self.calls if method == "SurfaceNotification"]


class Base(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.brain = Fake(self.folder)


class NotificationsAndTheModel(Base):
    BODY = "Jadwal demo dimajuin"

    def setUp(self):
        super().setUp()
        self.brain = Fake(self.folder, backend="claude")

    def sent(self, app, title="Dimas"):
        self.brain.pool.submit.reset_mock()
        self.brain.on_notification(1, app, title, self.BODY)
        return self.brain.pool.submit.called

    def test_the_rest_go_to_it(self):
        self.assertTrue(self.sent("Slack"))

    def test_a_password_manager_never_does(self):
        for app in ("KeePassXC", "Bitwarden", "1Password", "keepassxc"):
            self.assertFalse(self.sent(app, "Entri baru"), app)

    def test_nor_its_browser_extension(self):
        self.assertFalse(self.sent("Google Chrome", "Bitwarden"))

    def test_by_the_name_it_has_when_its_installed(self):
        vault = deskaid.launcher.app_info
        (self.folder / "modes.json").write_text(json.dumps({**json.loads((self.folder / "modes.json").read_text()),
                                                            "llm": {"exclude_apps": ["com.example.vlt.desktop"]}}))
        self.brain.config.refresh()
        with mock.patch.object(deskaid.launcher, "app_info", side_effect=lambda app: mock.Mock(get_name=lambda: "My Vault") if "vlt" in app else vault(app)):
            self.assertFalse(self.sent("My Vault"))
        self.assertFalse(self.sent("vlt"))

    def test_your_own_list_adds_to_the_password_managers(self):
        (self.folder / "modes.json").write_text(json.dumps({**json.loads((self.folder / "modes.json").read_text()),
                                                            "llm": {"exclude_apps": ["slack.desktop"]}}))
        self.brain.config.refresh()
        self.assertFalse(self.sent("Slack"))
        self.assertFalse(self.sent("Bitwarden"))
        self.assertTrue(self.sent("Teams"))

    def test_without_any_list_they_stay_out(self):
        config = {key: value for key, value in json.loads((self.folder / "modes.json").read_text()).items() if key != "llm"}
        (self.folder / "modes.json").write_text(json.dumps(config))
        self.brain.config.refresh()
        self.assertFalse(self.sent("Bitwarden"))

    def test_it_still_asks_you_about_them(self):
        self.brain.on_notification(1, "Bitwarden", "Dimas", self.BODY)
        self.assertEqual(self.brain.cards[0]["title"], "Dimas")

    def window(self, app_id, app_name, title="Some window"):
        self.brain.pool.submit.reset_mock()
        self.brain.seq = 1
        self.brain.inflight, self.brain.fits_shown, self.brain.cache = set(), set(), mock.Mock(get=lambda _key: None)
        self.brain.config.data["modes"]["building"].pop("apps", None)
        self.brain.activity = deskaid.Activity(1, app_id, app_name, title)
        self.brain._classify(1)
        return self.brain.pool.submit.called

    def test_a_window_goes_to_it(self):
        self.assertTrue(self.window("org.example.Notes.desktop", "Notes"))

    def test_a_password_managers_window_never_does_however_it_was_installed(self):
        for app_id, name in (("org.keepassxc.KeePassXC.desktop", ""), ("keepassxc_keepassxc.desktop", ""), ("bitwarden_bitwarden.desktop", ""),
                             ("com.bitwarden.desktop.desktop", ""), ("com.1password.1Password.desktop", ""), ("org.example.vlt.desktop", "Bitwarden")):
            self.assertFalse(self.window(app_id, name), app_id)
            self.assertEqual(self.brain.activity.verdict, "neutral", app_id)

    def test_nor_is_it_asked_about_later(self):
        self.brain.activity = deskaid.Activity(1, "bitwarden_bitwarden.desktop", "", "A secret entry")
        self.brain.activity.verdict, self.brain.activity.source = "neutral", "default"
        self.brain.last_tick = time.monotonic() - 1
        self.brain.stats = mock.Mock()
        self.brain._account(idle_ms=0)
        self.assertEqual(self.brain.unsure.seen, {})

    def test_a_short_name_doesnt_catch_every_word(self):
        (self.folder / "modes.json").write_text(json.dumps({**json.loads((self.folder / "modes.json").read_text()),
                                                            "llm": {"exclude_apps": ["my_app.desktop"]}}))
        self.brain.config.refresh()
        self.assertFalse(self.sent("my_app.desktop"))
        self.assertTrue(self.sent("My Documents"))


class Notifications(Base):
    def test_someone_new_gets_asked_about(self):
        self.brain.on_notification(1, "Slack", "Dimas", "Jadwal demo dimajuin")
        card = self.brain.cards[0]
        self.assertEqual((card["title"], card["text"]), ("Dimas", "Jadwal demo dimajuin"))
        self.assertEqual([a["id"] for a in card["actions"]], ["sender:hold", "sender:app", "sender:always"])
        self.assertEqual(self.brain.surfaced(), [])  # asking isn't showing

    def test_always_shows_this_one_and_the_next(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_card_action(self.brain.cards[0]["id"], "sender:always")
        self.assertEqual(self.brain.surfaced(), [1])
        self.brain.on_notification(2, "Slack", "Dimas", "dua")
        self.assertEqual(self.brain.surfaced(), [1, 2])

    def test_hold_stays_quiet_from_then_on(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_card_action(self.brain.cards[0]["id"], "sender:hold")
        self.brain.cards.clear()
        self.brain.sender_asked_at = -deskaid.SENDER_ASK_GAP_S
        self.brain.on_notification(2, "Slack", "Dimas", "dua")
        self.assertEqual((self.brain.cards, self.brain.surfaced()), ([], []))

    def test_the_whole_app(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_card_action(self.brain.cards[0]["id"], "sender:app")
        self.brain.on_notification(2, "Slack", "Rina", "dua")
        self.assertEqual(self.brain.surfaced(), [1, 2])

    def test_undo(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_card_action(self.brain.cards[0]["id"], "sender:always")
        self.brain.on_card_action(self.brain.cards[-1]["id"], "sender:undo")
        self.assertIsNone(self.brain.senders.word("Slack", "Dimas"))

    def test_an_answer_to_an_old_card_is_ignored(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_card_action("sender-99", "sender:always")
        self.assertIsNone(self.brain.senders.word("Slack", "Dimas"))

    def test_two_new_people_at_once_get_one_question(self):
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.brain.on_notification(2, "Slack", "Rina", "dua")
        self.assertEqual(len(self.brain.cards), 1)

    def test_a_browser_doesnt_offer_all_of_itself(self):
        self.brain.on_notification(1, "Google Chrome", "Dimas", "satu")
        self.assertEqual([a["id"] for a in self.brain.cards[0]["actions"]], ["sender:hold", "sender:always"])

    def test_ads_never_ask(self):
        self.brain.on_notification(1, "Tokopedia", "Flash sale!", "Diskon 90%")
        self.assertEqual(self.brain.cards, [])

    def test_urgent_needs_no_question(self):
        self.brain.on_notification(1, "Sentry", "Error spike", "500 errors naik tajam di production")
        self.assertEqual((self.brain.cards, self.brain.surfaced()), ([], [1]))

    def test_urgent_isnt_sent_to_a_mode_nobody_named(self):
        self.brain.on_notification(1, "Claude", "Bikin error di jendela Setup", "Butuh izin kamu")
        self.assertEqual(self.brain.cards, [])  # it used to say "held for Social"
        self.assertEqual(self.brain.surfaced(), [1])

    def triaged(self, **result):
        future = mock.Mock(result=lambda: {"surface": True, "reason": "", "label": "", "detail": "", **result})
        self.brain._on_triaged(future, 1, "Slack", "Dimas", "satu", "building")

    def test_the_model_naming_no_mode_shows_the_banner(self):
        self.triaged(mode="none")
        self.assertEqual((self.brain.cards, self.brain.surfaced()), ([], [1]))

    def test_the_model_naming_this_mode_shows_the_banner(self):
        self.triaged(mode="building")
        self.assertEqual((self.brain.cards, self.brain.surfaced()), ([], [1]))

    def test_the_model_naming_another_mode_offers_to_go_there(self):
        self.triaged(mode="writing")
        self.assertEqual(self.brain.surfaced(), [])
        self.assertEqual([a["id"] for a in self.brain.cards[0]["actions"]], ["later", "go:writing"])

    def test_your_hold_beats_the_word_urgent(self):
        self.brain.senders.remember("Sentry", "Error spike", senders.HOLD)
        self.brain.on_notification(1, "Sentry", "Error spike", "urgent")
        self.assertEqual(self.brain.cards, [])

    def test_a_mode_without_filtering_doesnt_ask(self):
        self.brain.mode = "social"
        self.brain.on_notification(1, "Slack", "Dimas", "satu")
        self.assertEqual(self.brain.cards, [])

    def test_with_claude_it_judges_instead_of_asking(self):
        claude = Fake(self.folder, backend="claude")
        claude.on_notification(1, "Slack", "Dimas", "satu")
        self.assertEqual(claude.cards, [])
        claude.pool.submit.assert_called_once()


class TheTick(Base):
    """GLib drops a timer whose callback raises, and with it nudges, server checks and desk snapshots."""

    PARTS = ("_refresh_config", "_idle_ms", "_account", "_tick_away", "_tick_coach", "_check_servers", "_check_room",
             "_snapshot_desk")

    def setUp(self):
        super().setUp()
        self.brain.stats = mock.Mock()
        self.brain.cache = mock.Mock()
        self.brain.tick_logged = {}
        self.brain.away = False
        # All of it due at once.
        self.brain.last_server_check = self.brain.last_room_check = self.brain.last_snapshot = self.brain.last_save = -1e9
        self.brain.last_tick = time.monotonic()
        self.brain.last_review_try = time.monotonic()
        self.ran = {}
        for name in self.PARTS:
            self.patch_part(name)

    def patch_part(self, name, **kwargs):
        patcher = mock.patch.object(self.brain, name, **kwargs)
        self.ran[name] = patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_part_that_fails_doesnt_stop_the_others_or_the_timer(self):
        for broken in self.PARTS:
            with self.subTest(broken), self.assertLogs("deskai", "ERROR"):
                self.patch_part(broken, side_effect=TypeError("broken"))
                self.brain.last_server_check = self.brain.last_room_check = self.brain.last_snapshot = -1e9
                self.brain.tick_logged.clear()
                self.assertIs(self.brain._tick(), True)
                for name in self.PARTS:
                    self.ran[name].assert_called()
                self.patch_part(broken)

    def test_saving_one_thing_failing_doesnt_stop_saving_the_other(self):
        self.brain.stats.save.side_effect = OSError("No space left on device")
        with self.assertLogs("deskai", "ERROR"):
            self.assertIs(self.brain._tick(), True)
        self.brain.cache.save.assert_called_once()

    def test_a_real_timer_keeps_going(self):
        self.patch_part("_tick_coach", side_effect=TypeError("broken"))
        loop = deskaid.GLib.MainLoop()
        with self.assertLogs("deskai", "ERROR"):
            deskaid.GLib.timeout_add(1, self.brain._tick)
            deskaid.GLib.timeout_add(60, loop.quit)
            loop.run()
        self.assertGreater(self.ran["_tick_coach"].call_count, 2)

    def test_one_that_keeps_failing_doesnt_fill_the_journal(self):
        self.patch_part("_tick_coach", side_effect=TypeError("broken"))
        with self.assertLogs("deskai", "ERROR") as logs:
            for _ in range(20):
                self.brain._tick()
        self.assertEqual(len(logs.records), 1)


class DeskSnapshots(Base):
    """The dock is off while the screen is locked. A snapshot from then would be an empty desk."""

    def took(self, finish):
        self.brain.bus = mock.Mock()
        with mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain._snapshot_desk()
            got = self.brain.bus.call.call_args.args[-1]
            got(mock.Mock(call_finish=finish), None)
        return [call.kwargs["args"][0] for call in thread.call_args_list]

    @staticmethod
    def error(code):
        def finish(_result):
            raise deskaid.GLib.Error.new_literal(deskaid.Gio.dbus_error_quark(), "no", code)
        return finish

    def test_with_no_dock_it_keeps_what_it_has(self):
        self.assertEqual(self.took(self.error(deskaid.Gio.DBusError.SERVICE_UNKNOWN)), [])
        self.assertEqual(self.took(self.error(deskaid.Gio.DBusError.NAME_HAS_NO_OWNER)), [])

    def test_a_dock_that_didnt_answer_doesnt_mean_an_empty_desk(self):
        self.assertEqual(self.took(self.error(deskaid.Gio.DBusError.NO_REPLY)), [])

    def test_an_answer_it_cant_read_doesnt_either(self):
        self.assertEqual(self.took(lambda _r: mock.Mock(unpack=lambda: ("not json",))), [])

    def test_with_a_dock_it_takes_one(self):
        windows = [{"app": "x.desktop"}]
        self.assertEqual(self.took(lambda _r: mock.Mock(unpack=lambda: (json.dumps(windows),))), [windows])

    def test_a_really_empty_desk_counts(self):
        self.assertEqual(self.took(lambda _r: mock.Mock(unpack=lambda: ("[]",))), [[]])

    def test_an_older_dock_still_gets_one(self):
        self.assertEqual(self.took(self.error(deskaid.Gio.DBusError.UNKNOWN_METHOD)), [None])


class AFullDisk(Base):
    def test_a_write_that_fails_leaves_no_half_file(self):
        target = self.folder / "state" / "x.json"
        target.parent.mkdir()
        target.write_text('{"old": true}')
        with mock.patch.object(brain.json, "dump", side_effect=OSError("No space left on device")), self.assertRaises(OSError):
            brain.write_json_atomic(target, {"new": True})
        self.assertEqual([path.name for path in target.parent.iterdir()], ["x.json"])
        self.assertEqual(json.loads(target.read_text()), {"old": True})

    def test_stats_keep_going_and_try_again(self):
        stats = deskaid.Stats(self.folder / "stats")
        stats.add("building", "on_task", 5, "")
        with mock.patch.object(brain, "write_json_atomic", side_effect=OSError("No space left on device")), \
                self.assertLogs("deskai", "WARNING"):
            stats.save()
        self.assertTrue(stats.dirty)
        stats.save()
        self.assertFalse(stats.dirty)


class Breaks(Base):
    def tick(self, seconds_later, idle_ms=0, mode="building"):
        self.brain.last_wall = time.time() - min(seconds_later, 5)
        self.brain._maybe_break(self.brain.config.mode(mode), idle_ms)

    def test_after_two_hours_at_it(self):
        self.tick(5)
        self.brain.working_since = time.time() - 119 * 60
        self.tick(5)
        self.assertEqual(self.brain.cards, [])
        self.brain.working_since = time.time() - 121 * 60
        self.tick(5)
        self.assertEqual(self.brain.cards[0]["title"], "Udah 2 jam 1 menit nonstop")

    def test_it_doesnt_nag(self):
        self.brain.working_since = time.time() - 121 * 60
        self.tick(5)
        self.tick(5)
        self.assertEqual(len(self.brain.cards), 1)

    def test_it_comes_again_after_another_stretch(self):
        self.brain.working_since = time.time() - 121 * 60
        self.tick(5)
        self.brain.break_shown = time.time() - 121 * 60
        self.tick(5)
        self.assertEqual(len(self.brain.cards), 2)

    def test_a_pause_starts_it_over(self):
        self.brain.working_since = time.time() - 121 * 60
        self.tick(5, idle_ms=deskaid.AWAY_AFTER_MS + 1)
        self.assertIsNone(self.brain.working_since)
        self.tick(5)
        self.assertEqual(self.brain.cards, [])

    def test_the_laptop_sleeping_is_a_pause(self):
        self.brain.working_since = time.time() - 121 * 60
        self.brain.last_wall = time.time() - 3600
        self.brain._maybe_break(self.brain.config.mode("building"), 0)
        self.assertEqual(self.brain.cards, [])
        self.assertIsNone(self.brain.working_since)

    def test_a_mode_that_isnt_for_focus_is_a_pause(self):
        self.brain.working_since = time.time() - 121 * 60
        self.tick(5, mode="social")
        self.assertEqual(self.brain.cards, [])

    def test_turning_it_off_and_on(self):
        self.brain.on_card_action("break", "break:off")
        self.assertFalse(json.loads((self.folder / "modes.json").read_text())["breaks"]["enabled"])
        self.brain.working_since = time.time() - 500 * 60
        self.brain.cards.clear()
        self.tick(5)
        self.assertEqual(self.brain.cards, [])
        self.brain._set_breaks(True)
        self.tick(5)
        self.assertEqual(len(self.brain.cards), 1)

    def test_turning_it_off_keeps_the_rest_of_the_config(self):
        self.brain._set_breaks(False)
        self.assertIn("building", json.loads((self.folder / "modes.json").read_text())["modes"])

    def test_how_long_is_yours_to_say(self):
        short = Fake(self.folder, breaks={"after_minutes": 45})
        short.working_since = time.time() - 46 * 60
        short.last_wall = time.time()
        short._maybe_break(short.config.mode("building"), 0)
        self.assertEqual(len(short.cards), 1)


class Review(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(deskaid.routine, "due", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_asking_about_the_unsure_one(self):
        self.brain.unsure.note("building", "google-chrome.desktop", "Chrome", "Kartu Kredit Terbaik", 1200)
        self.brain._maybe_review()
        card = self.brain.cards[0]
        self.assertEqual(card["title"], "Kartu Kredit Terbaik")
        self.assertEqual(card["subtitle"], "20 menit hari ini, gua belum yakin")
        self.assertEqual([a["id"] for a in card["actions"]], ["review:skip", "review:distraction", "review:work"])

    def test_your_answer_is_learned_and_the_next_one_follows(self):
        self.brain.unsure.note("building", "a", "A", "Pertama", 1200)
        self.brain.unsure.note("building", "a", "A", "Kedua", 600)
        self.brain._maybe_review()
        with mock.patch.object(deskaid.GLib, "timeout_add") as later:
            self.brain.on_card_action(self.brain.cards[0]["id"], "review:distraction")
        self.assertEqual(self.brain.learned.correction("building", "Pertama"), "off_task")
        later.call_args[0][1]()
        self.assertEqual(self.brain.cards[-1]["title"], "Kedua")

    def test_skip_learns_nothing_and_stops(self):
        self.brain.unsure.note("building", "a", "A", "Pertama", 1200)
        self.brain._maybe_review()
        with mock.patch.object(deskaid.GLib, "timeout_add") as later:
            self.brain.on_card_action(self.brain.cards[0]["id"], "review:skip")
        self.assertIsNone(self.brain.learned.correction("building", "Pertama"))
        later.assert_not_called()

    def test_it_doesnt_talk_over_an_offer(self):
        self.brain.unsure.note("building", "a", "A", "Pertama", 1200)
        self.brain.restore_snap = {"time": 0}
        self.brain._maybe_review()
        self.assertEqual(self.brain.cards, [])

    def test_only_in_a_focus_mode(self):
        self.brain.mode = "social"
        self.brain.unsure.note("social", "a", "A", "Pertama", 1200)
        self.brain._maybe_review()
        self.assertEqual(self.brain.cards, [])


class Lists(Base):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(Fake, "_reclassify")
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self, mode):
        return json.loads((self.folder / "modes.json").read_text())["modes"][mode]

    def test_blocking(self):
        self.brain._edit_list({"field": "block", "word": "reddit.example", "mode": "writing"})
        self.assertIn("reddit.example", self.saved("writing")["block"])

    def test_it_defaults_to_the_mode_youre_in(self):
        self.brain._edit_list({"field": "allow", "word": "figma.example", "mode": None})
        self.assertIn("figma.example", self.saved("building")["allow"])

    def test_it_moves_between_the_lists_rather_than_being_on_both(self):
        self.brain._edit_list({"field": "block", "word": "figma.example", "mode": "building"})
        self.brain._edit_list({"field": "allow", "word": "Figma.example", "mode": "building"})
        self.assertNotIn("figma.example", [k.lower() for k in self.saved("building")["block"]])
        self.assertIn("Figma.example", self.saved("building")["allow"])

    def test_undo(self):
        before = self.saved("building")
        self.brain._edit_list({"field": "block", "word": "x.example", "mode": "building"})
        self.brain.on_card_action(self.brain.cards[-1]["id"], "undo-allow")
        self.assertEqual(self.saved("building"), before)

    def test_a_mode_that_isnt_for_focus_has_no_lists_to_edit(self):
        self.brain.mode = "social"
        self.brain._edit_list({"field": "block", "word": "x.example", "mode": None})
        self.assertEqual(self.brain.cards[-1]["kind"], "command")


class ClosingWithHandStarted(Base):
    ITEM = {"unit": "manual-8000", "label": "server :8000", "kind": "manual", "cwd": "/w/app", "port": 8000, "pid": 1}

    def closed(self, report=()):
        self.brain._project_closed("/w/app", list(report), [], [self.ITEM])

    def test_it_asks_first_and_stops_nothing(self):
        with mock.patch.object(self.brain.watch, "stop") as stop:
            self.closed()
        stop.assert_not_called()
        self.assertEqual([a["id"] for a in self.brain.cards[0]["actions"]], ["project:keep-hand", "project:stop-hand"])
        self.assertIn("server :8000", self.brain.cards[0]["text"])

    def answer(self, action):
        with mock.patch.object(deskaid.threading, "Thread") as thread, mock.patch.object(deskaid.GLib, "idle_add") as idle, \
                mock.patch.object(self.brain.watch, "stop", return_value=True) as stop:
            self.brain.on_card_action("close-project", action)
            thread.call_args.kwargs["target"]()
        self.brain._project_report(*idle.call_args[0][1:])
        return stop

    def test_stopping_them(self):
        self.closed()
        stop = self.answer("project:stop-hand")
        stop.assert_called_once_with("manual-8000")
        self.assertIn("✓ server :8000", self.brain.cards[-1]["text"])
        self.assertIn("ditutup", self.brain.cards[-1]["title"])

    def test_leaving_them(self):
        self.closed()
        stop = self.answer("project:keep-hand")
        stop.assert_not_called()
        self.assertIn("server :8000 tetep jalan", self.brain.cards[-1]["text"])
        self.assertIn("Nggak ada", self.brain.cards[-1]["title"])

    DATABASE = {"unit": "postgresql@18-main.service", "label": "postgresql@18-main", "kind": "service"}

    def test_closing_hands_the_database_over_to_be_asked_about(self):
        with mock.patch.object(deskaid.commands, "find_project", return_value="/w/app"), \
                mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain._close_project("app")
        with mock.patch.object(deskaid.servers, "close_project", return_value=([], self.DATABASE)), \
                mock.patch.object(deskaid.session, "project_terminals", return_value=[]), \
                mock.patch.object(self.brain.watch, "hand_started_in", return_value=[self.ITEM]), \
                mock.patch.object(deskaid.servers, "git_state", return_value=None), \
                mock.patch.object(deskaid.GLib, "idle_add") as idle:
            thread.call_args.kwargs["target"]()
        self.assertEqual(idle.call_args[0][4], [self.ITEM, self.DATABASE])

    def test_a_database_is_asked_about_too(self):
        self.brain._project_closed("/w/app", [("vite :5173", "stopped", "")], [], [self.DATABASE])
        self.assertIn("postgresql@18-main", self.brain.cards[0]["text"])
        self.assertIn("bukan DeskAI", self.brain.cards[0]["text"])
        self.assertEqual([a["id"] for a in self.brain.cards[0]["actions"]], ["project:keep-hand", "project:stop-hand"])

    def test_stopping_the_database_when_you_say_so(self):
        self.brain._project_closed("/w/app", [], [], [self.DATABASE])
        with mock.patch.object(self.brain.watch, "stop_service", return_value=True) as stopping:
            self.answer("project:stop-hand")
        stopping.assert_called_once_with(self.DATABASE["unit"])
        self.assertIn("✓ postgresql@18-main", self.brain.cards[-1]["text"])

    def test_leaving_the_database(self):
        self.brain._project_closed("/w/app", [], [], [self.DATABASE])
        with mock.patch.object(self.brain.watch, "stop_service") as stopping:
            self.answer("project:keep-hand")
        stopping.assert_not_called()
        self.assertIn("postgresql@18-main tetep jalan", self.brain.cards[-1]["text"])

    def test_without_any_it_says_what_it_did_right_away(self):
        self.brain._project_closed("/w/app", [("vite :5173", "stopped", "")], [], [])
        self.assertIn("✓ vite :5173", self.brain.cards[0]["text"])

    def test_the_question_says_whats_not_in_git(self):
        self.brain._project_closed("/w/app", [], [], [self.ITEM], (2, 0))
        self.assertIn("2 file belum di-commit", self.brain.cards[0]["text"])

    def test_the_answer_says_it_too(self):
        self.brain._project_closed("/w/app", [], [], [self.ITEM], (2, 0))
        self.answer("project:keep-hand")
        self.assertIn("2 file belum di-commit", self.brain.cards[-1]["text"])


class ClosingWithWorkNotInGit(Base):
    REPORT = [("vite :5173", "stopped", "")]

    def closed(self, git, report=REPORT):
        self.brain._project_closed("/w/app", list(report), [], [], git)
        return self.brain.cards[-1]

    def test_both(self):
        card = self.closed((3, 2))
        self.assertIn("3 file belum di-commit · 2 commit belum di-push", card["text"])
        self.assertIn("✓ vite :5173", card["text"])

    def test_it_stays_long_enough_to_read(self):
        self.assertEqual(self.closed((3, 0))["timeout"], 45)
        self.assertEqual(self.closed((0, 0))["timeout"], 12)

    def test_only_files(self):
        card = self.closed((1, 0))
        self.assertIn("1 file belum di-commit", card["text"])
        self.assertNotIn("di-push", card["text"])

    def test_only_commits(self):
        card = self.closed((0, 1))
        self.assertIn("1 commit belum di-push", card["text"])
        self.assertNotIn("di-commit", card["text"])

    def test_nothing_to_say(self):
        for git in ((0, 0), None):
            self.assertNotIn("belum", self.closed(git)["text"])

    def test_even_with_nothing_running(self):
        card = self.closed((1, 0), report=[])
        self.assertIn("Nggak ada", card["title"])
        self.assertIn("1 file belum di-commit", card["text"])

    def test_in_english(self):
        self.brain.config.data["language"] = "en"
        self.assertIn("Files not committed: 3 · Commits not pushed: 2", self.closed((3, 2))["text"])

    def test_closing_a_project_asks_git_about_its_folder(self):
        with mock.patch.object(deskaid.commands, "find_project", return_value="/w/app"), \
                mock.patch.object(deskaid.servers, "close_project", return_value=(self.REPORT, None)), \
                mock.patch.object(deskaid.session, "project_terminals", return_value=[]), \
                mock.patch.object(self.brain.watch, "hand_started_in", return_value=[]), \
                mock.patch.object(deskaid.servers, "git_state", return_value=(4, 0)) as state, \
                mock.patch.object(deskaid.threading, "Thread") as thread, \
                mock.patch.object(deskaid.GLib, "idle_add") as idle:
            self.brain._close_project("app")
            thread.call_args.kwargs["target"]()
        state.assert_called_once_with("/w/app")
        self.brain._project_closed(*idle.call_args[0][1:])
        self.assertIn("4 file belum di-commit", self.brain.cards[-1]["text"])


class ClosingWhileSettingUp(Base):
    PLAN = {"project": "/w/app", "steps": []}

    def start(self, plan=PLAN):
        with mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain._run_routine(plan)
        return thread.call_args.kwargs["target"]

    def close(self, path="/w/app"):
        with mock.patch.object(deskaid.commands, "find_project", return_value=path), \
                mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain._close_project("x")
        return thread.call_args.kwargs["target"]

    def what_the_setup_sees(self, setup):
        seen = {}

        def run(_plan, progress, _set_mode, cancelled):
            seen["cancelled"] = cancelled()
            progress({"type": "server"}, "running")

        with mock.patch.object(deskaid.routine, "run", side_effect=run), mock.patch.object(deskaid.GLib, "idle_add") as idle:
            setup()
        seen["told_the_dock"] = idle.called
        return seen

    def test_closing_the_project_stops_its_setup_and_its_cards(self):
        setup = self.start()
        self.close()
        self.assertEqual(self.what_the_setup_sees(setup), {"cancelled": True, "told_the_dock": False})

    def test_closing_another_project_leaves_it_running(self):
        setup = self.start()
        self.close("/w/other")
        self.assertEqual(self.what_the_setup_sees(setup), {"cancelled": False, "told_the_dock": True})

    def test_closing_waits_a_moment_for_the_step_in_progress(self):
        self.start()
        running = self.brain.routine_running
        closing = self.close()
        with mock.patch.object(deskaid.servers, "close_project", return_value=([], None)), \
                mock.patch.object(deskaid.session, "project_terminals", return_value=[]), \
                mock.patch.object(self.brain.watch, "hand_started_in", return_value=[]), \
                mock.patch.object(deskaid.servers, "git_state", return_value=None), \
                mock.patch.object(deskaid.GLib, "idle_add"):
            closing()
        running["thread"].join.assert_called_once_with(timeout=deskaid.ROUTINE_STOP_WAIT_S)

    def test_a_setup_that_finished_is_forgotten(self):
        self.start()
        self.brain.routine_marks = {}
        self.brain._routine_finished(self.PLAN)
        self.assertIsNone(self.brain.routine_running)


class RoutineStopping(unittest.TestCase):
    def test_no_step_runs_after_it_is_cancelled(self):
        modes = []
        steps = [{"type": "mode", "mode": "a"}, {"type": "mode", "mode": "b"}]
        with mock.patch.object(deskaid.routine, "write_state") as state:
            deskaid.routine.run({"steps": steps}, lambda *_: None, modes.append, lambda: bool(modes))
        self.assertEqual(modes, ["a"])
        state.assert_not_called()  # it didn't set up your day

    def test_the_page_doesnt_open_if_it_was_cancelled_while_waiting(self):
        waited = []

        def wait(_port, _seconds):
            waited.append(True)
            return True

        with mock.patch.object(deskaid.routine, "wait_port", side_effect=wait), \
                mock.patch.object(deskaid.routine.subprocess, "Popen") as popen, \
                mock.patch.object(deskaid.routine, "write_state"):
            deskaid.routine.run({"steps": [{"type": "url", "url": "http://127.0.0.1:8000/"}]}, lambda *_: None, lambda _mode: None,
                                lambda: bool(waited))
        popen.assert_not_called()

    def test_without_cancelling_it_runs_everything(self):
        modes = []
        with mock.patch.object(deskaid.routine, "write_state") as state:
            deskaid.routine.run({"steps": [{"type": "mode", "mode": "a"}, {"type": "mode", "mode": "b"}]},
                                lambda *_: None, modes.append)
        self.assertEqual(modes, ["a", "b"])
        state.assert_called_once()


class ConfigReload(unittest.TestCase):
    def test_a_replaced_file_counts_even_with_the_same_time(self):
        # On Ubuntu 24.04's kernel, two saves in the same few milliseconds get the same time.
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "modes.json"
            config = brain.Config(path)
            defaults = json.loads(DEFAULTS.read_text())
            brain.write_json_atomic(path, {**defaults, "language": "id"})
            self.assertTrue(config.refresh())
            stamp = path.stat().st_mtime_ns
            brain.write_json_atomic(path, {**defaults, "language": "en"})
            os.utime(path, ns=(stamp, stamp))
            self.assertTrue(config.refresh())
            self.assertEqual(config.language, "en")

    def test_nothing_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "modes.json"
            brain.write_json_atomic(path, json.loads(DEFAULTS.read_text()))
            config = brain.Config(path)
            self.assertTrue(config.refresh())
            self.assertFalse(config.refresh())


class ASmallLocalModel(Base):
    def typed(self, text, backend="ollama"):
        self.brain.llm.backend = backend
        with mock.patch.object(deskaid.commands, "quick", return_value=None), mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain.on_command(text)
        return thread.called

    def test_what_the_lists_couldnt_place_is_said_right_away(self):
        self.assertFalse(self.typed("setup blabla"))
        self.assertIn("belum nangkep", self.brain.cards[-1]["text"])

    def test_a_question_still_goes_to_it(self):
        self.assertTrue(self.typed("kenapa servernya mati?"))

    def test_claude_gets_everything_the_lists_dont(self):
        self.assertTrue(self.typed("setup yang kemarin", backend="claude-cli"))

    def test_it_reads_a_shorter_desk_than_claude(self):
        for backend, small in (("ollama", True), ("claude-cli", False)):
            self.brain.llm.backend = backend
            with mock.patch.object(deskaid.commands, "quick", return_value=None), mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                    mock.patch.object(deskaid.threading, "Thread") as thread:
                self.brain.on_command("kenapa servernya mati?")
            with mock.patch.object(deskaid.session, "to_restore", return_value=None), mock.patch.object(deskaid.session, "current_session", return_value="s"), \
                    mock.patch.object(deskaid.commands, "desk", return_value="") as desk, \
                    mock.patch.object(deskaid.commands, "interpret", return_value={"actions": []}), \
                    mock.patch.object(deskaid.commands, "all_projects", return_value={}), mock.patch.object(deskaid.GLib, "idle_add"):
                thread.call_args.kwargs["target"]()
            self.assertEqual(desk.call_args.kwargs["small_model"], small, backend)


class ABrokenConfig(Base):
    def setUp(self):
        super().setUp()
        self.good = (self.folder / "modes.json").read_text()
        self.brain.dock_seen = True
        self.broken('{"modes": {')

    def broken(self, text):
        (self.folder / "modes.json").write_text(text)

    def look(self):
        with mock.patch.object(deskaid.brain, "LLM"):
            deskaid.Brain._refresh_config(self.brain)

    def told(self):
        return [card for card in self.brain.cards if card["id"] == "config-problem"]

    def test_you_hear_about_it_once(self):
        for _ in range(3):
            self.look()
        self.assertEqual(len(self.told()), 1)
        self.assertIn("Ada yang salah di modes.json", self.told()[0]["title"])
        self.assertIn("baris 1", self.told()[0]["text"])
        self.assertIn("versi terakhir", self.told()[0]["text"])

    def test_and_again_when_its_something_else_that_is_wrong(self):
        self.look()
        self.broken(json.dumps({**json.loads(self.good), "dock": None}))
        self.look()
        self.assertEqual(len(self.told()), 2)
        self.assertIn("Cek dock", self.told()[1]["text"])

    def test_and_again_if_it_breaks_after_being_fixed(self):
        self.look()
        self.broken(self.good)
        self.look()
        self.assertEqual(len(self.told()), 1)
        self.broken('{"modes": {')
        self.look()
        self.assertEqual(len(self.told()), 2)

    def test_it_waits_for_a_dock_to_say_it_on(self):
        self.brain.dock_seen = False
        self.look()
        self.assertEqual(self.told(), [])
        self.brain.dock_seen = True
        self.look()
        self.assertEqual(len(self.told()), 1)

    def test_with_nothing_good_to_go_by_it_says_so(self):
        self.brain.config = brain.Config(self.folder / "modes.json")
        self.look()
        self.assertIn("default modes", self.told()[0]["text"])

    def test_it_says_nothing_raw(self):
        self.look()
        self.assertNotIn("Expecting", self.told()[0]["text"])


class WhyASetupFailed(Base):
    SERVER = {"type": "server", "cwd": "/w/app", "command": "npm run dev", "port": 5173}

    def said(self, line):
        with mock.patch.object(deskaid.servers, "last_log_line", return_value=line):
            return self.brain._why_it_failed(self.SERVER)

    def test_a_tool_that_isnt_there(self):
        for line in ("/usr/bin/bash: line 1: npm: command not found", "bash: line 1: npm: command not found", "sh: 1: npm: not found"):
            self.assertEqual(self.said(line), brain.card_text("id", "routine_no_tool", tool="npm"))

    def test_anything_else_in_its_own_words(self):
        self.assertEqual(self.said("Error: port 5173 is already in use"), "Error: port 5173 is already in use")

    def test_docker_that_isnt_there(self):
        with mock.patch.object(deskaid.shutil, "which", return_value=None):
            self.assertEqual(self.brain._why_it_failed({"type": "compose", "cwd": "/w/app"}), brain.card_text("id", "compose_no_docker"))

    def test_it_goes_on_the_card(self):
        self.brain.routine_marks = {id(self.SERVER): "failed"}
        self.brain._routine_finished({"project": "/w/app", "steps": [self.SERVER]}, ["npm nggak ketemu", ""])
        self.assertEqual(self.brain.cards[-1]["text"], "✗ vite :5173\nnpm nggak ketemu")


class NoDocker(Base):
    def test_a_container_by_name_says_so_instead_of_an_errno(self):
        with mock.patch.object(deskaid.shutil, "which", return_value=None), \
                mock.patch.object(deskaid.compose, "act") as act:
            self.brain._docker_or_service("compose", {"type": "compose", "op": "stop", "project": "shop"})
        act.assert_not_called()
        self.assertEqual(self.brain.cards[-1]["title"], brain.card_text("id", "compose_no_docker"))


class PlainWords(Base):
    def setUp(self):
        super().setUp()
        self.brain._in_background = lambda work, then: then(work())
        self.brain._check_servers = mock.Mock()
        self.found = {"shop": "/w/shop"}

    def said(self):
        card = self.brain.cards[-1]
        return card["text"] or card["title"]

    def docker(self, op="up", **patches):
        with mock.patch.object(deskaid.shutil, "which", return_value="/usr/bin/docker"), \
                mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                mock.patch.object(deskaid.compose, "projects", return_value=self.found), \
                mock.patch.object(deskaid.compose, "states", return_value={}), \
                mock.patch.object(deskaid.compose, "dependents", return_value=[]), \
                mock.patch.object(deskaid.ports, "busy_for", return_value=patches.pop("taken", [])), \
                mock.patch.multiple(deskaid.compose, **patches):
            self.brain._docker_or_service("compose", {"type": "compose", "op": op, "project": "shop"})

    def test_what_each_tool_says_comes_out_plain(self):
        for said, key in [
            # As this computer really said them.
            ("Failed to start x.service: Access denied as the requested operation requires interactive authentication. "
             "However, interactive authentication has not been enabled by the calling program.\n"
             "See system logs and 'systemctl status x.service' for details.", "trouble_no_permission"),
            ("[Errno 2] No such file or directory: 'docker'", "trouble_not_there"),
            ("[Errno 13] Permission denied: '/root/x'", "trouble_no_permission"),
            ("Command '['sleep', '2']' timed out after 0.05 seconds", "trouble_too_long"),
            ("Program ‘x’ not found in $PATH", "trouble_not_there"),
            # As Docker and systemd are known to say them.
            ("Failed to start x.service: Unit x.service not found.", "trouble_no_service"),
            ("permission denied while trying to connect to the Docker daemon socket at unix:///var/run/docker.sock", "trouble_no_permission"),
            ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?", "trouble_docker_off"),
            ("failed to connect to the docker API at unix:///var/run/docker.sock", "trouble_docker_off"),
            ("Bind for 0.0.0.0:8080 failed: port is already allocated", "trouble_port_taken"),
            ("listen tcp4 0.0.0.0:80: bind: address already in use", "trouble_port_taken"),
            ("pull access denied for foo, repository does not exist or may require 'docker login'", "trouble_no_image"),
            ("manifest for foo:latest not found: manifest unknown", "trouble_no_image"),
            ("write /var/lib/docker/x: no space left on device", "trouble_no_room"),
            ("yaml: line 3: mapping values are not allowed in this context", "trouble_unknown"),
            ("", "trouble_unknown"),
        ]:
            self.assertEqual(deskaid.commands.trouble(said), key, said)

    def test_the_model_failing_says_so(self):
        self.assertEqual(deskaid.commands.trouble(brain.LLMError("stopped: max_tokens")), "trouble_ai")
        self.assertEqual(deskaid.commands.trouble(json.JSONDecodeError("Expecting value", "", 0)), "trouble_ai")

    def test_dockers_own_words_stay_in_the_log(self):
        with self.assertLogs("deskai", "WARNING") as logged:
            self.docker(start=mock.Mock(return_value=(False, "Bind for 0.0.0.0:8080 failed: port is already allocated", [])))
        self.assertEqual(self.said(), "shop gagal: port yang dibutuhin lagi dipakai")
        self.assertIn("Bind for 0.0.0.0:8080", logged.output[0])

    def test_when_it_stops_too(self):
        self.docker("stop", act=mock.Mock(return_value=(False, "[Errno 13] Permission denied: '/w/shop'")))
        self.assertEqual(self.said(), "shop gagal: nggak ada izin, atau password-nya dibatalin")

    def test_it_says_which_project_failed(self):
        self.docker(start=mock.Mock(return_value=(False, "manifest unknown", [], "infra")))
        self.assertEqual(self.said(), "infra gagal: Docker nggak nemu image-nya")

    def test_a_reason_of_our_own_stays_as_it_is(self):
        self.docker(taken=[(8080, "nginx")], start=mock.Mock())
        self.assertEqual(self.said(), "shop gagal: port :8080 (nginx) lagi dipakai")

    def test_with_nothing_to_go_by_it_points_at_the_log(self):
        self.docker(start=mock.Mock(return_value=(False, "", [])))
        self.assertEqual(self.said(), "shop gagal: alasannya ada di log: journalctl --user -u deskaid")

    def test_nor_when_the_work_itself_broke(self):
        self.brain._in_background = lambda _work, then: then(None)  # what it gets when the work raises
        self.docker(start=mock.Mock())
        self.assertEqual(self.said(), "shop gagal: alasannya ada di log: journalctl --user -u deskaid")

    def test_a_system_service_says_the_real_reason(self):
        denied = "Failed to start mysql.service: Access denied\nSee system logs and 'systemctl status mysql.service' for details."
        with mock.patch.object(deskaid.commands, "service_label", return_value="MySQL"), \
                mock.patch.object(deskaid.commands.subprocess, "run", return_value=mock.Mock(returncode=1, stderr=denied)):
            self.brain._docker_or_service("dev_service", {"type": "dev_service", "op": "start", "unit": "mysql.service"})
        self.assertEqual(self.said(), "MySQL gagal: nggak ada izin, atau password-nya dibatalin")

    def test_the_command_bar_never_shows_an_errno(self):
        with mock.patch.object(self.brain, "_run_action", side_effect=PermissionError(13, "Permission denied", "/root/x")):
            self.brain._run_actions({"say": "", "actions": [{"type": "open_folder"}]})
        self.assertEqual(self.said(), "Nggak bisa: nggak ada izin, atau password-nya dibatalin")

    def test_nor_does_a_failure_from_the_model(self):
        self.brain._command_failed(brain.LLMError("API error 500: internal"))
        self.assertNotIn("500", self.said())
        self.assertIn("AI-nya", self.said())

    def test_a_hint_still_comes_first(self):
        self.brain._command_failed(brain.LLMError("no AI available", hint="llm_none"))
        self.assertEqual(self.said(), brain.card_text("id", "llm_none"))


class SettingUpAProject(Base):
    def test_it_remembers_the_project_for_the_morning(self):
        with mock.patch.object(deskaid.commands, "find_project", return_value="/w/app"), \
                mock.patch.object(deskaid.commands, "all_projects", return_value={}), \
                mock.patch.object(deskaid.threading, "Thread") as thread:
            self.brain._setup_project("app")
        with mock.patch.object(deskaid.routine, "learn", return_value=None), \
                mock.patch.object(deskaid.routine, "write_state") as state, \
                mock.patch.object(deskaid.GLib, "idle_add"):
            thread.call_args.kwargs["target"]()
        state.assert_called_once_with(last_project="/w/app")


class CardsThatMeanStillWorking(Base):
    def run_in_thread(self, start):
        with mock.patch.object(deskaid.commands, "find_project", return_value="/w/app"), \
                mock.patch.object(deskaid.threading, "Thread"):
            start()
        return self.brain.cards[-1]

    def test_closing_a_project(self):
        self.assertTrue(self.run_in_thread(lambda: self.brain._close_project("app"))["busy"])

    def test_editing_modes(self):
        self.assertTrue(self.run_in_thread(lambda: self.brain._edit_modes("x"))["busy"])

    def test_the_answer_isnt_busy(self):
        self.brain._project_report("/w/app", [], [])
        self.assertNotIn("busy", self.brain.cards[-1])
        self.brain._command_card("done")
        self.assertNotIn("busy", self.brain.cards[-1])


class TheOnlyDock(Base):
    def appeared(self, names):
        with mock.patch.object(deskaid.GLib, "timeout_add_seconds") as later, \
                mock.patch.object(deskaid.docks, "turn_off_others", return_value=names):
            self.brain._on_dock_appeared(None, "", "")
        self.assertEqual(self.brain.cards, [])  # GNOME may be restarting this very dock right now
        for call in later.call_args_list:
            if call.args[1] == self.brain._say_docks_replaced:
                self.assertFalse(call.args[1](*call.args[2:]))

    def test_it_says_what_it_replaced_once_gnome_has_settled(self):
        self.appeared(["Ubuntu Dock", "Dash to Panel"])
        self.assertIn("Ubuntu Dock, Dash to Panel", self.brain.cards[-1]["text"])

    def test_with_nothing_to_replace_it_says_nothing(self):
        self.appeared([])
        self.assertEqual(self.brain.cards, [])

    def test_it_doesnt_stop_the_dock_if_it_goes_wrong(self):
        with mock.patch.object(deskaid.GLib, "timeout_add_seconds"), \
                mock.patch.object(deskaid.docks, "turn_off_others", side_effect=OSError("disk")):
            self.brain._on_dock_appeared(None, "", "")
        self.assertTrue(self.brain.dock_seen)

    def changed(self):
        """GNOME's lists change twice, the way turning an extension on does, and the wait runs out."""
        timers = []

        def add(_seconds, fn):
            timers.append(fn)
            return len(timers)
        with mock.patch.object(deskaid.GLib, "timeout_add_seconds", side_effect=add), mock.patch.object(deskaid.GLib, "source_remove") as removed:
            self.brain._on_extensions_changed(None, "disabled-extensions")
            self.brain._on_extensions_changed(None, "enabled-extensions")
        removed.assert_called_once_with(1)  # the first wait is dropped: both changes get one look
        return timers[-1]

    def test_a_dock_that_comes_back_on_goes_off_again(self):
        self.brain.dock_up = True
        finish = self.changed()
        with mock.patch.object(deskaid.docks, "turn_off_others", return_value=["Ubuntu Dock"]) as off, mock.patch.object(deskaid.GLib, "timeout_add_seconds") as said:
            self.assertFalse(finish())
        off.assert_called_once_with()
        said.assert_called_once()  # a card says why it went off

    def test_with_this_dock_gone_theres_nothing_to_be_on_top_of(self):
        self.brain.dock_up = False
        finish = self.changed()
        with mock.patch.object(deskaid.docks, "turn_off_others") as off:
            finish()
        off.assert_not_called()

    def test_the_dock_going_away_stops_the_watching(self):
        self.brain.dock_up = True
        self.brain.stats = mock.Mock()
        with mock.patch.object(self.brain, "_account"):
            self.brain._on_dock_vanished(None, "")
        self.assertFalse(self.brain.dock_up)

    def test_the_dock_coming_back_starts_it_again(self):
        self.appeared([])
        self.assertTrue(self.brain.dock_up)

    def test_watching_needs_the_lists_to_have_been_read(self):
        settings = mock.Mock()
        with mock.patch.object(deskaid.docks, "shell_settings", return_value=settings):
            self.brain._watch_extensions()
        self.assertEqual([call.args[0] for call in settings.connect.call_args_list], ["changed::enabled-extensions", "changed::disabled-extensions"])
        self.assertEqual([call.args[0] for call in settings.get_strv.call_args_list], ["enabled-extensions", "disabled-extensions"])

    def test_a_dock_that_never_comes_gives_the_others_back(self):
        with mock.patch.object(deskaid.docks, "restore") as restore:
            self.brain._dock_never_came()
        restore.assert_called_once_with()

    def test_a_dock_that_came_keeps_its_place(self):
        self.appeared([])
        with mock.patch.object(deskaid.docks, "restore") as restore:
            self.brain._dock_never_came()
        restore.assert_not_called()


class TheLocalAI(Base):
    def say(self, **state):
        self.brain.dock_up = True
        with mock.patch.object(deskaid.localai, "news", return_value=state or None), mock.patch.object(deskaid.localai, "told") as told:
            self.brain._say_how_the_local_ai_went()
        return told

    def test_ready(self):
        told = self.say(stage="ready")
        self.assertIn("udah siap", self.brain.cards[-1]["title"])
        self.assertEqual(self.brain.cards[-1]["actions"], [])
        told.assert_called_once_with()

    def test_failed_says_why_and_offers_another_go(self):
        self.assertEqual(self.brain._say("local_ai_failed", why="x"), "Gagal ngambil AI lokal: x.")
        told = self.say(stage="failed", trouble="trouble_no_room")
        self.assertEqual(self.brain.cards[-1]["actions"][0]["id"], "localai:try-again")
        said = self.brain.cards[-1]["title"] + self.brain.cards[-1]["text"]
        self.assertIn("disk-nya penuh", said)
        told.assert_called_once_with()

    def test_every_way_it_can_fail_has_something_to_say(self):
        import localai
        import re
        for key in set(re.findall(r"trouble_\w+", Path(localai.__file__).read_text())):
            for language in ("en", "id"):
                self.assertIn(key, brain.CARD_STRINGS[language])

    def test_the_button(self):
        with mock.patch.object(deskaid.localai, "try_again") as again:
            self.brain.on_card_action("command", "localai:try-again")
        again.assert_called_once_with()

    def test_nothing_new(self):
        told = self.say()
        self.assertEqual(self.brain.cards, [])
        told.assert_not_called()

    def test_it_waits_for_a_dock_to_say_it_on(self):
        self.brain.dock_up = False
        with mock.patch.object(deskaid.localai, "news", return_value={"stage": "ready"}), mock.patch.object(deskaid.localai, "told") as told:
            self.brain._say_how_the_local_ai_went()
        told.assert_not_called()
        self.assertEqual(self.brain.cards, [])


class ServerStatus(Base):
    def item(self, **values):
        return {"unit": "u", "label": "server :8000", "kind": "server", "cwd": "/w/app", "port": 8000, "state": "up", **values}

    def answer(self, **values):
        with mock.patch.object(deskaid.servers, "last_log_line", return_value="the last line"):
            self.brain._server_status(self.item(**values))
        return self.brain.cards[-1]

    def test_one_thats_down_offers_a_restart_and_says_why(self):
        card = self.answer(state="down")
        self.assertEqual(card["text"], "the last line")
        self.assertIn("server:restart:u", [a["id"] for a in card["actions"]])

    def test_one_thats_up(self):
        self.assertIn("lagi jalan", self.answer()["title"])

    def test_one_of_yours_points_at_your_terminal(self):
        self.assertIn("terminal lu", self.answer(kind="manual")["text"])


class OldVerdicts(Base):
    def setUp(self):
        super().setUp()
        self.brain.seq = 1
        self.brain.activity = deskaid.Activity(1, "google-chrome.desktop", "Google Chrome", "Halaman Biasa")
        self.brain.cache = mock.Mock()
        self.brain.cache.get.return_value = {"verdict": "off_task", "reason": "Browsing distraction", "site": "", "belongs_to": ""}
        self.brain.inflight = set()
        self.brain.fits_shown = set()

    def test_a_local_models_old_word_on_a_window_no_longer_counts(self):
        self.brain._classify(1)
        self.assertEqual(self.brain.activity.verdict, "neutral")
        self.assertEqual(self.brain.cards, [])

    def test_claudes_still_does(self):
        self.brain.llm.backend = "claude"
        self.brain._classify(1)
        self.assertEqual(self.brain.activity.verdict, "off_task")


class AVerbOnItsOwn(Base):
    def add_server(self, unit, label):
        self.brain.watch.items[unit] = {"unit": unit, "kind": "server", "label": label, "command": "x", "cwd": "/w/app", "port": 1,
                                        "state": "up", "misses": 0, "started": 0, "since": 0}

    def ask(self, op="stop"):
        self.brain._run_actions({"say": "", "actions": [{"type": "server_ask", "op": op}]})
        return self.brain.cards[-1]

    def test_with_several_it_offers_all_of_them(self):
        self.add_server("a", "vite :5173")
        self.add_server("b", "server :8000")
        card = self.ask()
        self.assertEqual(card["title"], "Matiin yang mana?")
        self.assertIn("vite :5173, server :8000", card["text"])
        self.assertEqual([(a["id"], a["label"]) for a in card["actions"]], [("later", "Nanti aja"), ("servers:stop", "Matiin semua (2)")])

    def test_with_one_it_offers_that_one(self):
        self.add_server("a", "vite :5173")
        self.assertEqual([(a["id"], a["label"]) for a in self.ask("restart")["actions"]],
                         [("later", "Nanti aja"), ("server:restart:a", "Restart vite :5173")])

    def test_nothing_happens_until_you_answer(self):
        self.add_server("a", "vite :5173")
        self.add_server("b", "server :8000")
        with mock.patch.object(self.brain, "_server_action") as acted:
            self.ask()
            acted.assert_not_called()
            self.brain.on_card_action("servers-ask", "later")
            acted.assert_not_called()
            self.brain.on_card_action("servers-ask", "servers:stop")
        self.assertEqual([call.args for call in acted.call_args_list], [("stop", "a"), ("stop", "b")])

    def test_only_its_own_card_can_answer(self):
        self.add_server("a", "vite :5173")
        with mock.patch.object(self.brain, "_server_action") as acted:
            self.brain.on_card_action("something-else", "servers:stop")
        acted.assert_not_called()

    def test_with_none_running_it_says_so(self):
        self.assertEqual(self.ask()["title"], "Nggak ada yang jalan.")

    def test_told_not_to_it_says_it_left_it_alone(self):
        self.add_server("a", "vite :5173")
        with mock.patch.object(self.brain, "_server_action") as acted:
            self.brain._run_actions(deskaid.commands.quick("jangan matiin server vite", [], self.brain.watch, {}))
        acted.assert_not_called()
        self.assertEqual(self.brain.cards[-1]["title"], "Oke, gua biarin.")


class Commands(Base):
    def test_the_answer_isnt_buried_under_done(self):
        watch = servers.Watch()
        watch.items["u"] = {"unit": "u", "label": "server :8000", "kind": "server", "cwd": "/w/app", "port": 8000, "state": "up", "command": "php"}
        self.brain.watch = watch
        with mock.patch.object(deskaid.servers, "last_log_line", return_value=""):
            self.brain._run_actions({"say": "", "actions": [{"type": "server", "op": "status", "target": "php"}]})
        self.assertEqual([card["kind"] for card in self.brain.cards], ["server"])

    def test_focus_time_is_answered_without_a_model(self):
        with mock.patch.object(deskaid.commands, "focus_answer", return_value="Hari ini fokus 1 jam"):
            self.brain._run_actions({"say": "", "actions": [{"type": "focus_today"}]})
        self.assertEqual(self.brain.cards[-1]["title"], "Hari ini fokus 1 jam")
        self.brain.llm.ask.assert_not_called()

    def test_a_model_saying_it_did_something_when_it_didnt(self):
        self.brain._run_actions({"say": "Saya akan menghentikan server", "actions": [], "dropped": 1, "unsure": True})
        self.assertIn("nggak gua apa-apain", self.brain.cards[-1]["text"])

    def test_turning_reminders_off_by_typing(self):
        self.brain._run_actions({"say": "", "actions": [{"type": "breaks", "on": False}]})
        self.assertFalse(self.brain.config.breaks["enabled"])


class StatsKept(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.stats = deskaid.Stats(self.dir)

    def day(self, back):
        when = datetime.date.today() - datetime.timedelta(days=back)
        path = self.dir / f"{when.isoformat()}.json"
        path.write_text('{"modes": {}, "distractions": {}}')
        return path

    def test_old_days_go_and_recent_ones_stay(self):
        old, edge, recent = self.day(deskaid.STATS_KEEP_DAYS + 1), self.day(deskaid.STATS_KEEP_DAYS), self.day(1)
        self.stats.add("building", "on_task", 5, "x")
        self.assertFalse(old.exists())
        self.assertTrue(edge.exists())
        self.assertTrue(recent.exists())

    def test_only_days_are_touched(self):
        other = self.dir / "notes.json"
        other.write_text("{}")
        self.stats.prune()
        self.assertTrue(other.exists())


class RoomWarnings(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.brain = Fake(self.folder)

    def look(self):
        """One look with a nearly full disk and enough memory."""
        with (mock.patch.object(deskaid.system, "low_disks", return_value=[{"path": "/", "total": 100, "free": 1}]),
              mock.patch.object(deskaid.system, "memory_low", return_value=False)):
            self.brain._check_room()

    def test_a_low_disk_warns_with_a_way_to_turn_it_off(self):
        self.look()
        self.assertEqual([action["id"] for action in self.brain.cards[0]["actions"]], ["system:room-off", "system:clear-offer"])

    def test_low_memory_warns_with_a_way_to_turn_it_off(self):
        self.brain.memory_low_since = time.monotonic() - deskaid.ROOM_CHECK_EVERY
        with (mock.patch.object(deskaid.system, "low_disks", return_value=[]),
              mock.patch.object(deskaid.system, "memory", return_value={"total": 100, "available": 1}),
              mock.patch.object(deskaid.system, "heaviest_apps", return_value=[]),
              mock.patch.object(deskaid.system, "memory_low", return_value=True)):
            self.brain._check_room()
        self.assertEqual([action["id"] for action in self.brain.cards[0]["actions"]], ["system:room-off"])

    def test_turned_off_it_stays_quiet_until_turned_on(self):
        self.brain.on_card_action("room", "system:room-off")
        self.assertFalse(json.loads((self.folder / "modes.json").read_text())["room_warnings"]["enabled"])
        self.brain.cards.clear()
        self.look()
        self.assertEqual(self.brain.cards, [])
        self.brain._run_actions({"say": "", "actions": [{"type": "room_warnings", "on": True}]})
        self.brain.cards.clear()
        self.look()
        self.assertEqual(len(self.brain.cards), 1)


if __name__ == "__main__":
    unittest.main()
