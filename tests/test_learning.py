"""The built-in lists and the classifier that learns from you. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import brain  # noqa: E402
import commands  # noqa: E402
import learned  # noqa: E402
import senders  # noqa: E402
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

    def test_ordinary_titles_with_a_listed_word_in_them(self):
        for title in ("Episode 4: Integrals - Khan Academy", "Ramalan Penjualan Q3", "Temu Kembali Informasi", "Titik Temu Dua Garis - Matematika",
                      "Resep obat dan dosis - Jurnal Farmasi", "Minecraft modding in Java tutorial", "Anime.js animation timeline",
                      "Tutorial Facebook Ads untuk Skripsi", "Resep dokter untuk flu", "Resep Sukses Menulis", "Ramalan Cuaca Jakarta"):
            for mode in (CODING, WRITING):
                self.assertNotEqual(self.verdict(title, mode), "off_task", title)

    def test_the_distraction_itself_still_counts(self):
        for title in ("Temu | Explore the Latest", "Temu: Shop Like a Billionaire", "Temu - Shop", "temu.com", "Tas Wanita - Temu", "Resep Nasi Goreng Spesial",
                      "Resep Kue Lebaran", "Ramalan Zodiak Hari Ini", "Ramalan Cinta Minggu Ini", "Minecraft Hardcore Survival", "Nonton Anime Terbaru",
                      "Cookpad", "Kumpulan Video Reddit", "Instagram.com", "Resep Kopi Susu", "Resep bubur", "Resep Brownies Kukus Anti Gagal",
                      "Naruto Episode 220", "Ramalan Tarot Mingguan"):
            self.assertEqual(self.verdict(title), "off_task", title)

    def test_an_editor_counts_in_a_coding_mode(self):
        self.assertEqual(self.verdict("main.rs - app", app="code.desktop"), "on_task")
        self.assertIsNone(self.verdict("main.rs - app", WRITING, app="code.desktop"))


BUILDING = {"label": "Building", "description": "Coding: writing and debugging code", "focus": True,
            "apps": ["code.desktop", "org.gnome.Ptyxis.desktop", "google-chrome.desktop"],
            "block": ["Instagram", "Twitter", " / X", " on X:", "Discord", "Shopee", "Kompas"],
            "allow": ["localhost", "GitHub", "laravel.com", "npm", "Vite"]}
ESSAY = {**BUILDING, "label": "Essay", "description": "Long writing", "apps": []}


class YourLists(unittest.TestCase):
    def verdict(self, title, app="google-chrome.desktop", name="Google Chrome", mode=BUILDING):
        ruled = brain.rule_verdict(mode, app, name, title)
        return ruled and ruled["verdict"]

    def test_a_word_inside_a_name_isnt_the_site(self):
        for title in ("discord-bot", "my-discord server", "~/projects/discord/notes", "discord.py documentation", "Docs / XState", "Instagrammable places",
                      "Invite teammates", "My npm-free life", "Vitest guide", "Github-like tools"):
            self.assertIsNone(self.verdict(title, mode=ESSAY), title)

    def test_the_site_itself_is(self):
        for title in ("Discord | #general", "Home / X", 'A friend on X: "hi"', "(3) Instagram", "Kompas.com - Berita", "www.instagram.com", "Twitter"):
            self.assertEqual(self.verdict(title, mode=ESSAY), "off_task", title)
        for title in ("Vite + React", "vite.config.ts - app", "localhost:5173", "http://localhost:5173/login", "laravel.com/docs", "npm run dev"):
            self.assertEqual(self.verdict(title, mode=ESSAY), "on_task", title)

    def test_an_app_the_mode_is_for_counts_whatever_its_title_says(self):
        self.assertEqual(self.verdict("~/projects/discord-bot", "org.gnome.Ptyxis.desktop", "Terminal"), "on_task")
        self.assertEqual(self.verdict("discord - ssh", "org.gnome.Ptyxis.desktop", "Terminal"), "on_task")
        self.assertEqual(self.verdict("discord-bot - Visual Studio Code", "code.desktop", "Visual Studio Code"), "on_task")

    def test_not_a_browser_because_its_listed(self):
        self.assertEqual(self.verdict("Discord | #general"), "off_task")

    def test_every_browser_goes_by_the_lists_even_listed_as_the_modes_app(self):
        kinds = {"org.example.Surf.desktop": "Network;WebBrowser;", "org.example.Notes.desktop": "Office;"}
        apps = ["vivaldi-stable.desktop", "org.chromium.Chromium.desktop", "com.microsoft.Edge.desktop", "opera.desktop", "librewolf.desktop",
                "org.example.Surf.desktop", "org.example.Notes.desktop"]
        mode = {**BUILDING, "apps": apps}
        with mock.patch.object(brain.launcher, "app_info", side_effect=lambda app: mock.Mock(get_categories=lambda: kinds[app]) if app in kinds else None):
            for app in apps[:-1]:
                self.assertEqual(self.verdict("A friend - Instagram", app, "Browser", mode), "off_task", app)
                self.assertIsNone(self.verdict("Some page", app, "Browser", mode), app)
            self.assertEqual(self.verdict("A friend - Instagram", apps[-1], "Notes", mode), "on_task")
            self.assertEqual(brain.allow_activity({"allow": []}, "org.example.Surf.desktop", "Surf", "Some page"), "Some page")
            self.assertEqual(brain.allow_activity({"allow": []}, "org.example.Notes.desktop", "Notes", "Some page"), "Notes")

    def test_a_site_you_blocked_but_about_your_work(self):
        for title in ("Twitter API v2 documentation", "Scraping Shopee with Python"):
            self.assertIsNone(self.verdict(title), title)
        self.assertEqual(sites.window_verdict(BUILDING, "google-chrome.desktop", "Twitter API v2 documentation")[0], "on_task")
        self.assertIsNone(sites.window_verdict(BUILDING, "google-chrome.desktop", "Scraping Shopee with Python"))

    def test_it_has_to_be_a_coding_mode_and_a_page(self):
        self.assertEqual(self.verdict("Twitter API v2 documentation", mode=ESSAY), "off_task")
        self.assertEqual(self.verdict("Python Indonesia - Discord", "discord.desktop", "Discord"), "off_task")

    def test_calling_a_window_work_changes_only_what_caught_it(self):
        mode = {**BUILDING, "block": list(BUILDING["block"]), "allow": list(BUILDING["allow"])}
        self.assertEqual(brain.allow_activity(mode, "google-chrome.desktop", "Google Chrome", "discord-bot"), "discord-bot")
        self.assertIn("Discord", mode["block"])
        self.assertEqual(self.verdict("discord-bot", mode=mode), "on_task")
        self.assertEqual(brain.allow_activity(mode, "google-chrome.desktop", "Google Chrome", "Discord | #general"), "Discord")
        self.assertNotIn("Discord", mode["block"])
        self.assertIn("Discord", mode["allow"])
        self.assertEqual(self.verdict("Discord | #general", mode=mode), "on_task")


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
        self.assertIsNone(sites.notification_verdict("Gmail", "Acara kantor", "Minggu depan ada acara, datang ya"))

    def test_a_deadline_or_an_error_gets_through(self):
        for title, body in (("Deadline laporan", "Kirim hari Jumat"), ("Sentry", "500 errors naik tajam di production"),
                            ("Pak Budi", "Tolong kirim laporan besok jam 9"), ("Rina", "Can you check this today?")):
            self.assertTrue(sites.notification_verdict("Gmail", title, body), title)

    def test_an_ad_stays_an_ad_even_if_it_presses(self):
        self.assertFalse(sites.notification_verdict("Shopee", "Promo", "Segera klaim voucher kamu, tolong cek hari ini"))

    def test_hurrying_you_isnt_enough(self):
        self.assertIsNone(sites.notification_verdict("Toko", "Stok menipis", "Segera checkout sebelum kehabisan"))


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

    def test_one_correction_doesnt_speak_for_a_whole_site(self):
        self.teach("off_task", "Funny Cat Video - YouTube")
        self.assertIsNone(self.brain.predict("building", "chrome", "Calculus 1 - Full College Course - YouTube"))
        self.assertIsNone(self.brain.predict("building", "chrome", "Calculus 1 - YouTube"))
        self.assertIsNone(self.brain.predict("building", "chrome", "Funny Dog Video - YouTube"))

    def test_nor_do_many_about_a_title_it_mostly_hasnt_seen(self):
        self.teach("off_task", "Funny Cat Video - YouTube", "Prank Compilation - YouTube", "Lagu Galau - YouTube", "Vlog Liburan - YouTube", "Unboxing Hp - YouTube")
        self.assertIsNone(self.brain.predict("building", "chrome", "Calculus 1 - Full College Course - YouTube"))
        self.assertIsNone(self.brain.predict("building", "chrome", "Calculus 1 - YouTube"))
        self.teach("on_task", "Laporan Sprint Tim", "Rencana Sprint Berikutnya", "Retro Sprint Oktober")
        self.assertIsNone(self.brain.predict("building", "chrome", "Calculus 1 - Full College Course - YouTube"))

    def test_one_word_it_knows_in_a_long_title_says_little(self):
        self.teach("off_task", "Harga Motor Bekas Jakarta", "Review Motor Matic 2026", "Modifikasi Motor Keren", "Komunitas Motor Bandung")
        self.teach("on_task", "Laporan Sprint Tim", "Rencana Sprint Berikutnya", "Retro Sprint Oktober")
        self.assertIsNone(self.brain.predict("building", "chrome", "Analisis Emisi Motor Diesel Pada Kapal Laut"))
        self.assertIsNone(self.brain.predict("building", "chrome", "Sejarah Panjang Sprint Atletik Olimpiade Modern"))

    def test_the_site_alone_doesnt_make_a_page_a_distraction(self):
        self.teach("off_task", "Funny Cat Video - YouTube", "Funny Dog Video - YouTube", "Funny Bird Video - YouTube")
        self.teach("on_task", "Pull requests - App - GitHub", "Issues - App - GitHub", "Actions - App - GitHub", "Wiki - App - GitHub")
        for title in ("Python Lecture - YouTube", "Linear Algebra - YouTube", "Lecture 3 - YouTube", "Home - YouTube"):
            self.assertIsNone(self.brain.predict("building", "chrome", title), title)
        self.assertEqual(self.brain.predict("building", "chrome", "Funny Fish Video - YouTube")[0], "off_task")
        self.assertEqual(self.brain.predict("building", "chrome", "YouTube")[0], "off_task")

    def test_it_can_for_work(self):
        self.teach("off_task", "Funny Cat Video - YouTube", "Funny Dog Video - YouTube", "Funny Bird Video - YouTube")
        self.teach("on_task", "Pull requests - App - GitHub", "Issues - App - GitHub", "Actions - App - GitHub", "Wiki - App - GitHub")
        self.assertEqual(self.brain.predict("building", "chrome", "Settings - GitHub")[0], "on_task")

    def test_nor_is_a_guess_enough_to_call_learning_a_distraction(self):
        self.teach("off_task", "Funny Cat Video", "Funny Dog Video", "Funny Bird Video")
        self.teach("on_task", "Laporan Sprint Tim", "Rencana Sprint Berikutnya", "Retro Sprint Oktober")
        self.assertEqual(self.brain.predict("building", "chrome", "Funny Fish Video")[0], "off_task")
        self.assertIsNone(self.brain.predict("building", "chrome", "Funny Video Tutorial"))

    def test_with_nothing_to_compare_it_to_it_takes_more_than_one_word(self):
        self.teach("off_task", "Harga Motor Bekas", "Review Motor Bekas", "Modifikasi Motor Bekas")
        self.assertEqual(self.brain.predict("building", "chrome", "Jual Motor Bekas")[0], "off_task")
        self.assertIsNone(self.brain.predict("building", "chrome", "Motor Tesla"))

    def test_it_isnt_certain_of_what_it_learned(self):
        self.teach("off_task", "Harga Motor Bekas", "Review Motor Bekas", "Modifikasi Motor Bekas")
        self.assertLess(self.brain.predict("building", "chrome", "Jual Motor Bekas")[1], 1.0)

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


class Asking(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.known = learned.Learned(Path(folder.name) / "learned.json")
        self.unsure = learned.Unsure(Path(folder.name) / "review.json")

    def test_the_one_you_spent_the_longest_on_comes_first(self):
        self.unsure.note("building", "a", "A", "Short", 400)
        self.unsure.note("building", "a", "A", "Long", 900)
        self.assertEqual(self.unsure.next("building", self.known)["title"], "Long")
        self.assertEqual(self.unsure.next("building", self.known)["title"], "Short")

    def test_a_glance_isnt_worth_asking_about(self):
        self.unsure.note("building", "a", "A", "Glance", 60)
        self.assertIsNone(self.unsure.next("building", self.known))

    def test_time_adds_up(self):
        for _ in range(5):
            self.unsure.note("building", "a", "A", "Back and forth", 100)
        self.assertEqual(self.unsure.next("building", self.known)["seconds"], 500)

    def test_only_the_mode_youre_in(self):
        self.unsure.note("writing", "a", "A", "Other", 900)
        self.assertIsNone(self.unsure.next("building", self.known))

    def test_not_what_you_already_answered(self):
        self.known.add("building", "a", "Known", "on_task")
        self.unsure.note("building", "a", "A", "Known", 900)
        self.assertIsNone(self.unsure.next("building", self.known))

    def test_a_few_a_day_even_across_a_restart(self):
        for index in range(learned.REVIEW_PER_DAY + 2):
            self.unsure.note("building", "a", "A", f"Window {index}", 900)
        for _ in range(learned.REVIEW_PER_DAY):
            self.assertIsNotNone(self.unsure.next("building", self.known))
        self.assertIsNone(self.unsure.next("building", self.known))
        again = learned.Unsure(self.unsure.path)
        again.note("building", "a", "A", "Another", 900)
        self.assertIsNone(again.next("building", self.known))

    def test_windows_themselves_arent_written_down(self):
        self.unsure.note("building", "a", "A", "Private title", 900)
        self.unsure.next("building", self.known)
        self.assertNotIn("Private", self.unsure.path.read_text())


class Senders(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "senders.json"
        self.senders = senders.Senders(self.path)

    def test_your_word_is_kept(self):
        self.assertIsNone(self.senders.word("Slack", "Dimas"))
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.assertEqual(senders.Senders(self.path).word("Slack", "dimas"), senders.ALWAYS)
        self.assertIsNone(self.senders.word("Slack", "Rina"))
        self.assertIsNone(self.senders.word("Teams", "Dimas"))

    def test_forgetting_on_the_command_line_reaches_the_running_daemon(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.senders.remember("Slack", "Rina", senders.HOLD)
        self.assertEqual(senders.Senders(self.path).forget_name("Dimas"), 1)
        self.assertIsNone(self.senders.word("Slack", "Dimas"))
        self.assertEqual(self.senders.word("Slack", "Rina"), senders.HOLD)

    def test_and_the_daemon_doesnt_write_it_back(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        senders.Senders(self.path).forget_name("Dimas")
        self.senders.remember("Slack", "Budi", senders.HOLD)
        self.assertEqual([name for name, _app, _word in senders.Senders(self.path).listing()], ["Budi"])
        self.senders.remember("Slack", "Dimas", senders.HOLD)
        senders.Senders(self.path).forget_name("Dimas")
        self.assertTrue(self.senders.should_ask("Slack", "Dimas"))
        self.assertEqual([name for name, _app, _word in senders.Senders(self.path).listing()], ["Budi"])

    def test_what_the_other_one_remembered_counts_too(self):
        senders.Senders(self.path).remember("Slack", "Dimas", senders.ALWAYS)
        self.assertEqual(self.senders.word("Slack", "Dimas"), senders.ALWAYS)
        self.assertEqual([name for name, _app, _word in self.senders.listing()], ["Dimas"])

    def test_a_file_you_deleted_is_gone(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.path.unlink()
        self.assertIsNone(self.senders.word("Slack", "Dimas"))

    def test_it_reads_the_file_only_when_it_changed(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        with mock.patch.object(Path, "read_text") as reading:
            self.senders.word("Slack", "Dimas")
            self.senders.listing()
        reading.assert_not_called()

    def test_a_file_half_saved_for_a_moment_loses_nothing(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        for broken in ('{"people": {', "[1, 2]", ""):
            self.path.write_text(broken)
            self.assertEqual(self.senders.word("Slack", "Dimas"), senders.ALWAYS, broken)
            self.senders.remember("Slack", "Rina", senders.HOLD)
            self.assertEqual(sorted(name for name, _app, _word in senders.Senders(self.path).listing()), ["Dimas", "Rina"], broken)

    def test_a_message_count_is_still_the_same_person(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.assertEqual(self.senders.word("Slack", "Dimas (3 pesan)"), senders.ALWAYS)

    def test_a_whole_app(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS, whole_app=True)
        self.assertEqual(self.senders.word("Slack", "Rina"), senders.ALWAYS)

    def test_your_word_on_a_person_beats_the_app(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS, whole_app=True)
        self.senders.remember("Slack", "Bot", senders.HOLD)
        self.assertEqual(self.senders.word("Slack", "Bot"), senders.HOLD)

    def test_not_a_whole_browser(self):
        self.assertFalse(senders.can_take_whole_app("Google Chrome", "Dimas"))
        self.assertTrue(senders.can_take_whole_app("Slack", "Dimas"))
        self.assertFalse(senders.can_take_whole_app("Slack", ""))

    def test_asked_once(self):
        now = time.time()
        self.assertTrue(self.senders.should_ask("Slack", "Dimas", now=now))
        self.assertFalse(self.senders.should_ask("Slack", "Dimas", now=now + 100))

    def test_no_answer_asks_again_the_next_day(self):
        now = time.time()
        self.assertTrue(self.senders.should_ask("Slack", "Dimas", now=now))
        self.assertTrue(self.senders.should_ask("Slack", "Dimas", now=now + senders.ASK_AGAIN_S))

    def test_an_answer_ends_it(self):
        self.senders.remember("Slack", "Dimas", senders.HOLD)
        self.assertFalse(self.senders.should_ask("Slack", "Dimas"))

    def test_saying_no_twice_quiets_the_app(self):
        self.senders.remember("Toko", "Promo satu", senders.HOLD)
        self.assertTrue(self.senders.should_ask("Toko", "Promo dua"))
        self.senders.remember("Toko", "Promo dua", senders.HOLD)
        self.assertFalse(self.senders.should_ask("Toko", "Promo tiga"))
        self.assertTrue(self.senders.should_ask("Slack", "Dimas"))

    def test_not_if_you_let_someone_through(self):
        for name, word in (("Bot satu", senders.HOLD), ("Bot dua", senders.HOLD), ("Dimas", senders.ALWAYS)):
            self.senders.remember("Slack", name, word)
        self.assertTrue(self.senders.should_ask("Slack", "Rina"))

    def test_a_few_questions_a_day(self):
        now = time.time()
        asked = [self.senders.should_ask("Gmail", f"Subject {index}", now=now) for index in range(senders.ASKS_PER_DAY + 3)]
        self.assertEqual(sum(asked), senders.ASKS_PER_DAY)

    def test_taking_it_back(self):
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.assertEqual(self.senders.forget_name("dimas"), 1)
        self.assertIsNone(self.senders.word("Slack", "Dimas"))
        self.assertEqual(self.senders.forget_name("dimas"), 0)

    def test_no_message_is_kept(self):
        self.senders.should_ask("Slack", "Dimas")
        self.senders.remember("Slack", "Dimas", senders.ALWAYS)
        self.assertEqual(set(json.loads(self.path.read_text())), {"people", "apps", "asked"})


if __name__ == "__main__":
    unittest.main()
