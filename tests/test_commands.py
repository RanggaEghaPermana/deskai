"""The command bar and the first-run guide. Run with: python3 -m unittest discover tests"""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import commands  # noqa: E402
import guide  # noqa: E402
import servers  # noqa: E402

MODES = [("writing", "Writing"), ("building", "Building"), ("kuliah", "Studying")]


def watch_with(*labels):
    watch = servers.Watch()
    for index, (label, port) in enumerate(labels):
        watch.items[f"unit{index}"] = {"unit": f"unit{index}", "kind": "server", "label": label, "command": "x", "cwd": "",
                                       "port": port, "state": "up", "misses": 0, "started": 0, "since": 0}
    return watch



def setUpModule():
    # The tests don't look at this computer's Docker projects or system services.
    for patcher in (mock.patch.object(commands.compose, "ROOTS", []), mock.patch.object(commands.compose, "_docker_ls", return_value=[]),
                    mock.patch.object(commands, "dev_service_unit", return_value=None)):
        patcher.start()


def tearDownModule():
    mock.patch.stopall()


class Quick(unittest.TestCase):
    def action(self, text, watch=None):
        result = commands.quick(text, MODES, watch or watch_with())
        return result["actions"][0] if result else None

    def test_a_mode_by_its_name(self):
        for text in ("writing", "Writing", "pindah ke writing", "switch to Writing", "writing mode", "mode writing.", "ke writing"):
            self.assertEqual(self.action(text), {"type": "mode", "mode": "writing"}, text)

    def test_a_mode_by_its_label_or_id(self):
        self.assertEqual(self.action("studying")["mode"], "kuliah")
        self.assertEqual(self.action("kuliah")["mode"], "kuliah")

    def test_a_server_command(self):
        watch = watch_with(("vite :5173", 5173))
        self.assertEqual(self.action("restart vite", watch), {"type": "server", "op": "restart", "target": "vite"})
        self.assertEqual(self.action("matiin vite", watch)["op"], "stop")
        self.assertEqual(self.action("liat log 5173", watch)["op"], "log")
        self.assertEqual(self.action("restart", watch)["target"], "all")

    def test_anything_else_goes_to_the_model(self):
        watch = watch_with(("vite :5173", 5173))
        for text in ("siapin project toko", "restart laptop gua", "write a poem", "logout", ""):
            self.assertIsNone(self.action(text, watch), text)


class Instant(unittest.TestCase):
    """What used to go to the model, and took seconds or missed."""

    MODES = [("writing", "Writing"), ("building", "Building"), ("studying", "Studying"), ("social", "Social")]
    PROJECTS = {"toko-online": "/w/toko-online", "sistem-gudang": "/w/sistem-gudang"}

    def action(self, text, watch=None):
        result = commands.quick(text, self.MODES, watch or watch_with(), self.PROJECTS)
        return result["actions"][0] if result else None

    def php(self):
        watch = watch_with(("server :8000", 8000))
        watch.items["unit0"]["command"] = "php -S 127.0.0.1:8000"
        return watch

    def test_a_mode_in_everyday_words(self):
        for text, mode in (("gua mau belajar", "studying"), ("belajar", "studying"), ("pindah ke mode nulis", "writing"),
                           ("mau nulis dong", "writing"), ("lanjut ngoding", "building"), ("mode santai", "social"),
                           ("pengen ngoding", "building")):
            self.assertEqual(self.action(text), {"type": "mode", "mode": mode}, text)

    def test_a_word_for_a_mode_you_dont_have_means_nothing(self):
        self.assertIsNone(commands.quick("gua mau belajar", [("writing", "Writing")], watch_with()))

    def test_more_than_a_mode_goes_to_the_model(self):
        self.assertIsNone(self.action("gua mau belajar react dari youtube"))
        self.assertIsNone(self.action("mau nulis, terus buka youtube"))

    def test_wanting_to_do_what_a_mode_is_for(self):
        for text, mode in (("lagi pengen nulis skripsi", "writing"), ("lanjut nulis bab 3", "writing"), ("kayaknya gua mau belajar dulu", "studying"),
                           ("mau santai dulu", "social"), ("i want to write my thesis", "writing"), ("time to study for the exam", "studying")):
            self.assertEqual(self.action(text), {"type": "mode", "mode": mode}, text)

    def test_a_project_comes_before_what_a_word_could_mean(self):
        self.assertEqual(self.action("mulai ngoding gudang"), {"type": "setup_project", "project": "gudang"})

    def test_finishing_work_on_a_project(self):
        for text in ("gua udah kelar kerja di toko online", "kelar kerja di toko online", "selesai toko online",
                     "udah beres toko online", "done with toko online", "tutup project toko online", "cabut dari toko online",
                     "udahan dulu sama toko online", "tutup dulu toko online", "toko online udah kelar", "selesai ngerjain toko online",
                     "capek, stop dulu kerja di toko online"):
            self.assertEqual(self.action(text), {"type": "close_project", "project": "toko online"}, text)

    def test_turning_a_project_off_in_other_words(self):
        for text in ("matikan gudang", "matikan project gudang", "matikan proyek gudang", "tolong matikan gudang",
                     "matikan gudang sekarang", "matikan gudang dong", "hentikan gudang", "nonaktifkan gudang",
                     "akhiri gudang", "turn off gudang", "kill gudang", "stopin gudang", "matikan server gudang",
                     "matiin server gudang", "stop server gudang", "matikan servernya gudang"):
            self.assertEqual(self.action(text), {"type": "close_project", "project": "gudang"}, text)

    def test_turning_a_project_on_in_other_words(self):
        for text in ("nyalakan gudang", "nyalain gudang", "hidupkan gudang", "hidupin gudang", "idupin gudang",
                     "jalankan gudang", "jalanin gudang", "aktifkan gudang", "run gudang", "nyalain server gudang",
                     "jalankan project gudang"):
            self.assertEqual(self.action(text), {"type": "setup_project", "project": "gudang"}, text)

    def test_those_words_about_something_else_arent_a_project(self):
        for text in ("matikan wifi", "matikan laptop", "kill semuanya", "hentikan musik", "jalankan tests", "run tests", "nyalain bluetooth"):
            self.assertIsNone(self.action(text), text)

    def test_a_servers_own_name_still_wins_over_those_words(self):
        watch = watch_with(("gudang :8000", 8000))
        self.assertEqual(self.action("matikan gudang", watch)["type"], "server")
        self.assertEqual(self.action("hentikan gudang", watch)["type"], "server")

    def test_starting_work_on_a_project(self):
        for text, name in (("siapin project gudang", "gudang"), ("set up gudnag", "gudnag"), ("mulai kerja di gudang", "gudang"),
                           ("gua mau kerja di toko online, siapin dong", "toko online"), ("kerja di gudang", "gudang"),
                           ("gas kerjain gudang", "gudang"), ("lanjut kerja di gudang", "gudang"), ("mulai ngerjain gudang", "gudang")):
            self.assertEqual(self.action(text), {"type": "setup_project", "project": name}, text)

    def test_a_project_it_doesnt_know_goes_to_the_model(self):
        self.assertIsNone(self.action("siapin project entah"))
        for text in ("cabut aja gua", "udahan dulu", "entah udah kelar"):
            self.assertIsNone(self.action(text), text)

    def test_a_sentence_that_starts_with_the_server(self):
        vite = watch_with(("vite :5173", 5173))
        for text, op in (("vite-nya restart dong", "restart"), ("vitenya restart", "restart"), ("vite down, nyalain lagi", "restart"),
                         ("vite kenapa", "status"), ("vite-nya matiin", "stop")):
            self.assertEqual(self.action(text, vite), {"type": "server", "op": op, "target": "vite"}, text)
        self.assertEqual(self.action("php-nya kenapa ya", self.php()), {"type": "server", "op": "status", "target": "php"})

    def test_only_the_servers_own_name_counts_as_the_start_of_a_sentence(self):
        watch = watch_with(("vite :5173", 5173))
        watch.items["unit0"]["command"] = "npm run dev"
        for text in ("run tests, fix the login", "dev fix", "vite tolong dong benerin login page yang rusak ini"):
            self.assertIsNone(self.action(text, watch), text)

    def test_a_projects_name_is_the_project_not_its_server(self):
        watch = watch_with(("vite :5173", 5173))
        watch.items["unit0"]["cwd"] = "/w/sistem-gudang"
        self.assertEqual(self.action("gudang udah kelar", watch), {"type": "close_project", "project": "gudang"})
        self.assertIsNone(self.action("gudang udah kelar", watch_with(("gudang :5173", 5173))))  # never stopped by a guess

    def test_stopping_for_now_isnt_stopping_everything(self):
        for text in ("stop dulu", "matiin dulu"):
            self.assertIsNone(self.action(text, self.php()), text)

    def test_the_server_thats_down(self):
        for text in ("server mati tuh, idupin", "restart server yang mati", "nyalain server yang down"):
            self.assertEqual(self.action(text, self.php()), {"type": "server", "op": "restart", "target": "down"}, text)
        self.assertEqual(self.action("server php mati", self.php())["op"], "status")  # saying so isn't asking for a restart

    def test_down_means_only_the_ones_that_are(self):
        watch = watch_with(("vite :5173", 5173), ("server :8000", 8000))
        watch.items["unit1"]["state"] = "down"
        self.assertEqual(watch.match("down"), ["unit1"])
        watch.items["unit1"]["state"] = "up"
        self.assertEqual(watch.match("down"), [])

    def test_a_server_by_name_in_a_sentence(self):
        self.assertEqual(self.action("server php gua kenapa? nyalain lagi dong", self.php()),
                         {"type": "server", "op": "restart", "target": "php"})
        self.assertEqual(self.action("server php kenapa?", self.php()), {"type": "server", "op": "status", "target": "php"})
        self.assertEqual(self.action("server php mati", self.php())["op"], "status")
        self.assertEqual(self.action("matiin server php", self.php())["op"], "stop")

    def test_every_server(self):
        self.assertEqual(self.action("matiin semua server", self.php()), {"type": "server", "op": "stop", "target": "all"})
        self.assertEqual(self.action("restart semua", self.php())["target"], "all")

    def test_saying_so_when_there_are_none(self):
        self.assertEqual(self.action("matiin semua server"), {"type": "server", "op": "stop", "target": "all"})

    def test_opening_a_site(self):
        self.assertEqual(self.action("buka youtube"), {"type": "open_url", "url": "https://www.youtube.com"})
        self.assertEqual(self.action("tolong buka stack overflow"), {"type": "open_url", "url": "https://stackoverflow.com"})
        self.assertEqual(self.action("open example.com/docs"), {"type": "open_url", "url": "https://example.com/docs"})

    def test_opening_an_app(self):
        with mock.patch.object(commands, "find_app", side_effect=lambda name: name if name == "org.gnome.Console.desktop" else None):
            self.assertEqual(self.action("buka terminal"), {"type": "open_app", "app": "org.gnome.Console.desktop"})
            self.assertIsNone(self.action("buka kalkulator"))
            self.assertIsNone(self.action("buka entah apa"))

    def test_opening_a_project_folder(self):
        self.assertEqual(self.action("buka folder project gudang"), {"type": "open_folder", "path": "gudang"})
        self.assertIsNone(self.action("buka folder ~/Downloads"))  # the model knows paths

    def test_bringing_the_desk_back(self):
        for text in ("balikin sesi sebelumnya", "balikin sesi", "restore", "restore session"):
            self.assertEqual(self.action(text), {"type": "restore_desk"}, text)

    def test_whats_running(self):
        for text, show in (("cek docker yg aktif?", "docker"), ("docker apa aja yang jalan", "docker"),
                           ("list container", "docker"), ("database apa yg aktif", "database"), ("cek db", "database"),
                           ("server apa aja yang nyala?", "all"), ("what's running on docker", "docker")):
            self.assertEqual(self.action(text), {"type": "servers_list", "show": show}, text)

    def test_doing_something_to_one_isnt_asking_whats_running(self):
        self.assertEqual(self.action("matiin semua server", self.php())["type"], "server")
        self.assertEqual(self.action("restart server php", self.php())["op"], "restart")
        self.assertNotEqual((self.action("liat log server php", self.php()) or {}).get("type"), "servers_list")

    def test_listing_what_runs_by_kind(self):
        watch = servers.Watch()
        for unit, kind, label, state in (("docker:app", "container", "app :8000", "up"), ("mysql.service", "service", "mysql", "up"),
                                         ("deskai-x", "server", "vite :5173", "down")):
            watch.items[unit] = {"unit": unit, "kind": kind, "label": label, "state": state, "port": None, "cwd": "",
                                 "since": 0, "command": ""}
        self.assertEqual(commands.running_list(watch, "docker", "id"), "Docker: app :8000 (jalan)")
        self.assertEqual(commands.running_list(watch, "database", "en"), "Database: mysql (up)")
        self.assertEqual(commands.running_list(watch, "all", "id").splitlines(),
                         ["Docker: app :8000 (jalan)", "Database: mysql (jalan)", "Server: vite :5173 (mati)"])
        self.assertEqual(commands.running_list(servers.Watch(), "docker", "id"), "")

    def test_a_database_in_docker_is_a_database(self):
        watch = servers.Watch()
        for unit, kind, label, image in (("docker:app", "container", "app :8000", "shop-app:latest"),
                                         ("docker:db", "container", "db :3309", "mysql:5.7"),
                                         ("docker:cache", "container", "cache", "docker.io/library/redis:7")):
            watch.items[unit] = {"unit": unit, "kind": kind, "label": label, "state": "up", "port": None, "cwd": "",
                                 "since": 0, "command": image}
        self.assertEqual(commands.running_list(watch, "database", "id"), "Database: cache (jalan, docker), db :3309 (jalan, docker)")
        self.assertEqual(commands.running_list(watch, "docker", "id"), "Docker: app :8000 (jalan), cache (jalan), db :3309 (jalan)")

    def test_how_long_you_focused(self):
        for text in ("berapa lama gua fokus hari ini?", "how long did I focus today?", "fokus hari ini berapa", "berapa jam kerja gua"):
            self.assertEqual(self.action(text), {"type": "focus_today"}, text)

    def test_a_modes_lists(self):
        for text, field, word, mode in (("blokir reddit di mode writing", "block", "reddit", "writing"), ("block reddit", "block", "reddit", None),
                                        ("izinin figma di building", "allow", "figma", "building"), ("tambahin figma ke mode building", "allow", "figma", "building"),
                                        ("tambahin tiktok ke yang diblokir di mode building", "block", "tiktok", "building")):
            self.assertEqual(self.action(text), {"type": "mode_list", "field": field, "word": word, "mode": mode}, text)

    def test_a_mode_it_doesnt_know_isnt_guessed(self):
        self.assertIsNone(self.action("blokir reddit di mode entah"))

    def test_break_reminders(self):
        self.assertEqual(self.action("matiin pengingat istirahat"), {"type": "breaks", "on": False})
        self.assertEqual(self.action("nyalain pengingat"), {"type": "breaks", "on": True})
        self.assertEqual(self.action("turn off reminders".replace("reminders", "reminder")), {"type": "breaks", "on": False})

    def test_anything_dangerous_never_takes_a_shortcut(self):
        for text in ("buka terminal terus sudo rm -rf", "restart sudo", "matiin semua server pake sudo", "tutup gudang rm -rf"):
            self.assertIsNone(self.action(text), text)


class TimeToday(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(commands.brain, "STATE_DIR", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        (Path(folder.name) / "stats").mkdir()
        self.config = commands.brain.Config(Path(__file__).resolve().parents[1] / "extension" / "deskai@ranggaeghapermana.github.io" / "modes.default.json")
        self.config.refresh()

    def record(self, modes):
        stats = commands.brain.STATE_DIR / "stats" / f"{time.strftime('%Y-%m-%d')}.json"
        stats.write_text(json.dumps({"modes": modes, "distractions": {}}))

    def test_being_away_isnt_counted_as_focus(self):
        self.record({"building": {"on_task": 28200, "neutral": 7200, "idle": 36720, "off_task": 900}})
        spent = commands.time_today(self.config)
        self.assertEqual(spent["focused"], {"Building": 590})
        self.assertEqual((spent["distracted"], spent["idle"]), (15, 612))

    def test_a_mode_that_isnt_for_focus_isnt_focus(self):
        self.record({"building": {"on_task": 600}, "social": {"neutral": 1800}})
        spent = commands.time_today(self.config)
        self.assertEqual(spent["focused"], {"Building": 10})
        self.assertEqual(spent["elsewhere"], 30)

    def test_the_answer_says_it_plainly(self):
        self.record({"building": {"on_task": 38400, "neutral": 240, "off_task": 120, "idle": 3540}})
        self.config.data = {**self.config.data, "language": "id"}
        answer = commands.focus_answer(self.config)
        self.assertIn("10 jam 44 menit", answer)
        self.assertIn("Distraksi 2 menit", answer)
        self.assertIn("idle 59 menit", answer)
        self.assertIn("(Building)", answer)

    def test_several_modes_are_each_told(self):
        self.record({"building": {"on_task": 3600}, "writing": {"on_task": 1800}})
        self.assertIn("1 h 30 min today: Writing 30 min, Building 1 h", commands.focus_answer(self.config).replace("Building 1 h, Writing 30 min", "Writing 30 min, Building 1 h"))

    def test_what_the_model_is_told_matches(self):
        self.record({"building": {"on_task": 6000, "idle": 6000}})
        line = commands.focus_today(self.config)
        self.assertTrue(line.startswith("100 minutes focused"), line)
        self.assertIn("100 minutes, not counted", line)

    def test_nothing_yet(self):
        self.assertEqual(commands.focus_today(self.config), "nothing recorded yet")
        self.assertIn("recorded", commands.focus_answer(self.config))

    def test_duration(self):
        for minutes, words in ((0, "0 menit"), (59, "59 menit"), (60, "1 jam"), (125, "2 jam 5 menit")):
            self.assertEqual(commands.duration(minutes, "id"), words)
        self.assertEqual(commands.duration(125, "en"), "2 h 5 min")


class Closing(unittest.TestCase):
    PROJECTS = {"sistem-gudang": "/w/sistem-gudang"}

    def action(self, text, watch=None):
        result = commands.quick(text, MODES, watch or watch_with(), self.PROJECTS)
        return result["actions"][0] if result else None

    def test_closing_a_project_by_name(self):
        for text in ("tutup gudang", "matiin gudang", "close gudang", "tutup project gudang", "selesai gudang", "matiin gudnag", "tutup kerjaan di gudang"):
            self.assertEqual(self.action(text)["type"], "close_project", text)

    def test_a_servers_own_name_means_the_server(self):
        watch = watch_with(("vite :5173", 5173))
        self.assertEqual(self.action("matiin vite", watch), {"type": "server", "op": "stop", "target": "vite"})

    def test_other_things_arent_projects(self):
        self.assertIsNone(self.action("tutup chrome"))

    def test_without_the_project_list_nothing_closes(self):
        self.assertIsNone(commands.quick("tutup gudang", MODES, watch_with()))


class WhatWasAsked(unittest.TestCase):
    PROJECTS = {"sistem-gudang": "/w/sistem-gudang", "toko-online": "/w/toko-online"}

    def kept(self, text, *actions):
        return [a["type"] for a in commands.only_what_was_asked({"actions": list(actions)}, text, self.PROJECTS)["actions"]]

    def test_the_odd_actions_a_small_model_really_picked_are_dropped(self):
        picked = ({"type": "mode", "mode": "studying"}, {"type": "server", "op": "stop", "target": "server:8000"})
        self.assertEqual(self.kept("jalanin rm -rf / di terminal", *picked), [])
        self.assertEqual(self.kept("berapa lama gua fokus hari ini?", *picked), [])

    def test_anything_with_a_dangerous_command_in_it_does_nothing(self):
        self.assertEqual(self.kept("sudo restart server", {"type": "server", "op": "restart", "target": "all"}), [])

    def test_stopping_needs_saying_so(self):
        action = {"type": "server", "op": "stop", "target": "all"}
        self.assertEqual(self.kept("matiin semua server", action), ["server"])
        self.assertEqual(self.kept("server gua kenapa", action), [])

    def test_restarting_needs_saying_so(self):
        action = {"type": "server", "op": "restart", "target": "php"}
        self.assertEqual(self.kept("server php nyalain lagi dong", action), ["server"])
        self.assertEqual(self.kept("server php kenapa", action), [])

    def test_a_mode_needs_asking_for(self):
        action = {"type": "mode", "mode": "writing"}
        self.assertEqual(self.kept("pindah ke mode nulis", action), ["mode"])
        self.assertEqual(self.kept("buka youtube", action), [])

    def test_setting_up_a_project_needs_naming_it(self):
        action = {"type": "setup_project", "project": "sistem-gudang"}
        self.assertEqual(self.kept("siapin gudnag dong", action), ["setup_project"])
        self.assertEqual(self.kept("siapin toko online", action), [])
        self.assertEqual(self.kept("halo", action), [])

    def test_opening_things_stays_allowed(self):
        self.assertEqual(self.kept("buka youtube", {"type": "open_url", "url": "https://www.youtube.com"}), ["open_url"])
        self.assertEqual(self.kept("buka terminal", {"type": "open_app", "app": "x"}), ["open_app"])

    def test_it_says_when_it_dropped_something(self):
        result = commands.only_what_was_asked({"say": "ok", "actions": [{"type": "mode", "mode": "x"}]}, "halo", self.PROJECTS)
        self.assertEqual(result["dropped"], 1)


class Unsure(unittest.TestCase):
    def unsure(self, text, actions=()):
        return commands.only_what_was_asked({"say": "x", "actions": list(actions)}, text, {})["unsure"]

    def test_words_with_no_action_behind_them_dont_count(self):
        self.assertTrue(self.unsure("cabut dari toko online"))
        self.assertTrue(self.unsure("jalanin rm -rf /", [{"type": "server", "op": "stop", "target": "all"}]))

    def test_an_answer_to_a_question_does(self):
        for text in ("hari ini gua produktif gak?", "kenapa server gua lambat", "what can you do"):
            self.assertFalse(self.unsure(text), text)

    def test_doing_something_does(self):
        self.assertFalse(self.unsure("buka youtube", [{"type": "open_url", "url": "https://www.youtube.com"}]))


class Checks(unittest.TestCase):
    def test_only_web_addresses_open(self):
        self.assertTrue(commands.safe_url("https://github.com/x"))
        self.assertTrue(commands.safe_url("http://127.0.0.1:8000"))
        for url in ("file:///etc/passwd", "javascript:alert(1)", "chrome://settings", "https://a b", "", None):
            self.assertFalse(commands.safe_url(url), url)

    def test_only_folders_in_your_home_open(self):
        with tempfile.TemporaryDirectory() as home:
            project = Path(home) / "work" / "app"
            project.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(commands.folder_path("~/work/app", {}), os.path.realpath(project))
                self.assertEqual(commands.folder_path("app", {"app": str(project)}), os.path.realpath(project))
                self.assertIsNone(commands.folder_path("/etc", {}))
                self.assertIsNone(commands.folder_path("~/../..", {}))
                self.assertIsNone(commands.folder_path("~/nope", {}))

    def test_a_project_by_a_rough_name(self):
        known = {"toko-besar": "/w/toko-besar", "toko-online": "/w/toko-online", "toko-offline": "/w/toko-offline"}
        self.assertEqual(commands.find_project("toko-besar", known), "/w/toko-besar")
        self.assertEqual(commands.find_project("besar", known), "/w/toko-besar")
        self.assertEqual(commands.find_project("/home/owner/besar", known), "/w/toko-besar")
        self.assertEqual(commands.find_project("Toko Online", known), "/w/toko-online")
        self.assertIsNone(commands.find_project("toko", known))  # two projects: don't guess
        self.assertEqual(commands.find_project("onlne", known), "/w/toko-online")  # a slip of the fingers
        self.assertEqual(commands.find_project("Toko Besr", known), "/w/toko-besar")
        self.assertIsNone(commands.find_project("buku", known))
        self.assertIsNone(commands.find_project("nope", known))
        self.assertIsNone(commands.find_project("", known))
        self.assertIsNone(commands.find_project(None, known))

    def test_a_typo_needs_one_clear_project(self):
        known = {"toko-online": "/w/a", "toko-onlain": "/w/b"}
        self.assertIsNone(commands.find_project("onlien", known))

    def test_an_app_by_a_rough_name(self):
        def app(app_id, name):
            return mock.Mock(get_id=lambda: app_id, get_name=lambda: name, should_show=lambda: True)
        apps = [app("org.gnome.Ptyxis.desktop", "Terminal"), app("google-chrome.desktop", "Google Chrome"),
                app("org.gnome.Nautilus.desktop", "Files")]
        with mock.patch.object(commands.Gio.AppInfo, "get_all", return_value=apps):
            self.assertEqual(commands.find_app("google-chrome.desktop"), "google-chrome.desktop")
            self.assertEqual(commands.find_app("org.gnome.Terminal.desktop"), "org.gnome.Ptyxis.desktop")
            self.assertEqual(commands.find_app("Files"), "org.gnome.Nautilus.desktop")
            self.assertEqual(commands.find_app("chrome"), "google-chrome.desktop")
            self.assertIsNone(commands.find_app("youtube"))
            self.assertIsNone(commands.find_app(""))

    def test_a_folder_a_model_made_up_finds_the_project(self):
        with tempfile.TemporaryDirectory() as home:
            project = Path(home) / "work" / "kasir"
            project.mkdir(parents=True)
            with mock.patch.dict(os.environ, {"HOME": home}):
                self.assertEqual(commands.folder_path("/home/owner/kasir", {"kasir": str(project)}), os.path.realpath(project))
                self.assertIsNone(commands.folder_path("", {"kasir": str(project)}))

    def test_the_schema_only_allows_listed_actions(self):
        schema = commands.COMMAND_SCHEMA["properties"]["actions"]["items"]
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("command", schema["properties"])
        self.assertNotIn("shell", schema["properties"]["type"]["enum"])


class Guide(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        for name, value in (("GUIDE_FILE", self.folder / "guide.json"), ("CHROME_PREFERENCES", self.folder / "Preferences")):
            patcher = mock.patch.object(guide, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def preferences(self, setting=None):
        exceptions = {f"{guide.WHATSAPP},*": {"setting": setting}} if setting else {}
        guide.CHROME_PREFERENCES.write_text(json.dumps(
            {"profile": {"content_settings": {"exceptions": {"notifications": exceptions}}}}))

    def test_the_tour_stops_when_finished(self):
        self.assertTrue(guide.due())
        guide.started()
        self.assertTrue(guide.due())
        guide.finished()
        self.assertFalse(guide.due())

    def test_the_tour_stops_when_ignored_twice(self):
        guide.started()
        guide.started()
        self.assertFalse(guide.due())

    def test_whatsapp_is_asked_about_once(self):
        self.preferences()
        self.assertTrue(guide.ask_whatsapp())
        self.assertFalse(guide.ask_whatsapp())

    def test_whatsapp_already_allowed_isnt_asked_about(self):
        self.preferences(guide.ALLOWED)
        self.assertFalse(guide.ask_whatsapp())

    def test_no_chrome_nothing_to_ask(self):
        self.assertFalse(guide.ask_whatsapp())


if __name__ == "__main__":
    unittest.main()
