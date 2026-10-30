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
