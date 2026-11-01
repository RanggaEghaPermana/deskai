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
        self.routine_running = None

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
                mock.patch.object(deskaid.servers, "close_project", return_value=self.REPORT), \
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
        with mock.patch.object(deskaid.servers, "close_project", return_value=[]), \
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

    def test_a_dock_that_never_comes_gives_the_others_back(self):
        with mock.patch.object(deskaid.docks, "restore") as restore:
            self.brain._dock_never_came()
        restore.assert_called_once_with()

    def test_a_dock_that_came_keeps_its_place(self):
        self.appeared([])
        with mock.patch.object(deskaid.docks, "restore") as restore:
            self.brain._dock_never_came()
        restore.assert_not_called()


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
