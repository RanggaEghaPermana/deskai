"""The command bar and the first-run guide. Run with: python3 -m unittest discover tests"""

import json
import os
import sys
import tempfile
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
