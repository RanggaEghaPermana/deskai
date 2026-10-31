"""The brain's flows from event to card, without a dock. Run with: python3 -m unittest discover tests"""

import datetime
import json
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
        self.last_wall = time.time()
        self.last_review_try = time.monotonic()
        self.closing_terminals = []
        self.card_act = self.allow_undo = self.mark_undo = None
        self.drift_since = self.last_nudge = None
        self.servers_sent = None

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
        self.assertEqual(self.brain.cards[0]["kind"], "held")
        self.assertNotIn("sender:always", [a["id"] for a in self.brain.cards[0]["actions"]])

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


if __name__ == "__main__":
    unittest.main()
