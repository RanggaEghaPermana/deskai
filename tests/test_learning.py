"""The built-in lists and the classifier that learns from you. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import brain  # noqa: E402
import commands  # noqa: E402
import learned  # noqa: E402
import servers  # noqa: E402
import sites  # noqa: E402

CODING = {"label": "Building", "description": "Coding: writing and debugging code"}
WRITING = {"label": "Writing", "description": "Long writing: a thesis, reports"}


class Windows(unittest.TestCase):
    def verdict(self, title, mode=CODING, app="google-chrome.desktop"):
        found = sites.window_verdict(mode, app, title)
        return found[0] if found else None

    def test_docs_count_in_a_coding_mode(self):
        for title in ("Docker Compose file reference | Docker Docs", "Prisma ORM Documentation", "Tailwind CSS v4 upgrade guide",
                      "How to fix N+1 query in Eloquent - Medium"):
            self.assertEqual(self.verdict(title), "on_task", title)

    def test_docs_mean_nothing_in_another_mode(self):
        self.assertIsNone(self.verdict("Docker Docs", WRITING))

    def test_plain_distractions_in_any_focus_mode(self):
        for title in ("Lazada Indonesia: Diskon", "One Piece Episode 1100 Sub Indo", "Genshin Impact Wiki", "Resep Nasi Goreng - Cookpad",
                      "Zodiak Hari Ini", "Timnas Indonesia Menang 2-0"):
            self.assertEqual(self.verdict(title), "off_task", title)
            self.assertEqual(self.verdict(title, WRITING), "off_task", title)

    def test_it_stays_out_when_a_title_points_both_ways(self):
        self.assertIsNone(self.verdict("Scraping Shopee with Python"))

    def test_it_stays_out_of_what_it_doesnt_know(self):
        self.assertIsNone(self.verdict("Laporan Bulanan Oktober"))

    def test_no_false_alarms_inside_words(self):
        self.assertIsNone(self.verdict("Animation timing in film editing"))
        self.assertIsNone(self.verdict("Git", WRITING))

    def test_an_editor_counts_in_a_coding_mode(self):
        self.assertEqual(self.verdict("main.rs - app", app="code.desktop"), "on_task")
        self.assertIsNone(self.verdict("main.rs - app", WRITING, app="code.desktop"))


class Notifications(unittest.TestCase):
    def test_security_gets_through(self):
        for body in ("Kode OTP kamu 123456", "Ada percobaan login dari perangkat baru", "Your verification code is 44"):
            self.assertIs(sites.notification_verdict("Bank", "", body), True, body)

    def test_ads_and_feeds_wait(self):
        self.assertIs(sites.notification_verdict("Tokopedia", "Flash sale!", "Diskon 90%"), False)
        self.assertIs(sites.notification_verdict("Instagram", "budi", "menyukai fotomu"), False)
        self.assertIs(sites.notification_verdict("Duolingo", "", "Streak kamu"), False)

    def test_security_beats_the_app(self):
        self.assertIs(sites.notification_verdict("Instagram", "", "Your security code is 123"), True)

    def test_the_rest_goes_to_the_model(self):
        self.assertIsNone(sites.notification_verdict("Gmail", "Deadline", "Tolong kirim laporan besok"))


class Learning(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "learned.json"
        self.brain = learned.Learned(self.path)

    def teach(self, verdict, *titles, source="you", mode="building"):
        for title in titles:
            self.brain.add(mode, "chrome", title, verdict, source=source)

    def test_a_correction_applies_to_that_window(self):
        self.teach("off_task", "Berita Motor Terbaru")
        self.assertEqual(self.brain.correction("building", "  berita motor  TERBARU "), "off_task")
        self.assertIsNone(self.brain.correction("writing", "Berita Motor Terbaru"))

    def test_the_latest_word_wins_and_claude_never_overrules_you(self):
        self.teach("off_task", "Forum Kampus")
        self.teach("on_task", "Forum Kampus")
        self.assertEqual(self.brain.correction("building", "Forum Kampus"), "on_task")
        self.brain.add("building", "chrome", "Forum Kampus", "off_task", source="claude")
        self.assertEqual(self.brain.correction("building", "Forum Kampus"), "on_task")
        self.assertEqual(len(self.brain.examples), 1)

    def test_it_generalizes_from_what_it_was_told(self):
        self.teach("off_task", "Harga Motor Bekas Jakarta", "Review Motor Matic 2026", "Modifikasi Motor Keren", "Komunitas Motor Bandung")
        self.teach("on_task", "Laporan Sprint Tim", "Rencana Sprint Berikutnya", "Retro Sprint Oktober")
        self.assertEqual(self.brain.predict("building", "chrome", "Motor Baru Murah")[0], "off_task")
        self.assertEqual(self.brain.predict("building", "chrome", "Catatan Sprint")[0], "on_task")

    def test_it_says_nothing_about_what_it_hasnt_seen(self):
        self.teach("off_task", "Harga Motor Bekas", "Review Motor Matic", "Modifikasi Motor")
        self.assertIsNone(self.brain.predict("building", "chrome", "Kubernetes Pod Lifecycle"))
        self.assertIsNone(self.brain.predict("writing", "chrome", "Harga Motor"))

    def test_it_needs_enough_evidence(self):
        self.teach("off_task", "Harga Motor", source="claude")
        self.assertIsNone(self.brain.predict("building", "chrome", "Motor Baru"))

    def test_your_word_counts_for_more_than_claudes(self):
        self.teach("on_task", "Motor Listrik Riset", "Motor Listrik Data", "Motor Listrik Paper", source="claude")
        self.assertEqual(self.brain.predict("building", "chrome", "Motor Listrik Baru")[0], "on_task")
        self.teach("off_task", "Motor Listrik Harga")  # one word from you and Claude's three don't carry it anymore
        guess = self.brain.predict("building", "chrome", "Motor Listrik Baru")
        self.assertNotEqual(guess and guess[0], "on_task")

    def test_forgetting(self):
        self.teach("off_task", "Harga Motor")
        self.brain.forget("building", "harga motor")
        self.assertIsNone(self.brain.correction("building", "Harga Motor"))

    def test_it_survives_a_restart(self):
        self.teach("off_task", "Harga Motor")
        self.assertEqual(learned.Learned(self.path).correction("building", "Harga Motor"), "off_task")

    def test_it_stays_small_on_disk(self):
        self.teach("off_task", *[f"judul {i}" for i in range(50)])
        self.assertLess(self.path.stat().st_size, 10_000)

    def test_a_broken_file_starts_over(self):
        self.path.write_text("{ nope")
        self.assertEqual(learned.Learned(self.path).examples, [])

    def test_seeding_takes_claudes_verdicts_not_the_local_models(self):
        config = brain.Config(self.path.with_name("modes.json"))
        config.data = {"modes": {"building": {"label": "Building"}}}
        fp = config.fingerprint("building")
        cache = {f"{fp}|chrome|Judul Lama": {"verdict": "off_task"}, f"{fp}|claude-cli|chrome|Judul Baru": {"verdict": "on_task"},
                 f"{fp}|ollama|chrome|Judul Lokal": {"verdict": "off_task"}, "other|chrome|Mode Hilang": {"verdict": "off_task"}}
        self.assertEqual(self.brain.seed_from_verdicts(cache, config), 2)
        self.assertEqual({e["title"] for e in self.brain.examples}, {"Judul Lama", "Judul Baru"})
        self.assertTrue(json.loads(self.path.read_text())["seeded"])


class Telling(unittest.TestCase):
    def action(self, text):
        result = commands.quick(text, [("writing", "Writing")], servers.Watch())
        return result["actions"][0] if result else None

    def test_saying_its_work(self):
        for text in ("ini kerjaan", "kerjaan", "this is work", "ini kerja.", "bukan distraksi"):
            self.assertEqual(self.action(text), {"type": "label", "verdict": "on_task"}, text)

    def test_saying_it_isnt(self):
        for text in ("ini distraksi", "bukan kerjaan", "this is a distraction", "not work", "gak relevan"):
            self.assertEqual(self.action(text), {"type": "label", "verdict": "off_task"}, text)

    def test_other_sentences_arent_mistaken_for_it(self):
        for text in ("kerjaan gua hari ini apa", "work on the report", "distraksi apa aja hari ini"):
            self.assertIsNone(self.action(text), text)


if __name__ == "__main__":
    unittest.main()
