"""Filling a new install's modes with the apps you use. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "setup"))

import installer  # noqa: E402
import personalize  # noqa: E402

CHROME = "google-chrome.desktop"
FIREFOX = "firefox.desktop"
CODE = "code.desktop"
CURSOR = "cursor.desktop"
TERMINAL = "org.gnome.Terminal.desktop"
WRITER = "libreoffice-writer.desktop"
DISCORD = "discord.desktop"
TELEGRAM = "telegram.desktop"
ANKI = "anki.desktop"
CLAUDE = "com.anthropic.Claude.desktop"

APPS = {
    CHROME: {"WebBrowser", "Network"},
    FIREFOX: {"WebBrowser", "Network"},
    CODE: {"Development", "IDE"},
    CURSOR: {"Development", "IDE"},
    TERMINAL: {"System", "TerminalEmulator"},
    WRITER: {"Office", "WordProcessor"},
    DISCORD: {"Network", "InstantMessaging"},
    TELEGRAM: {"Network", "Chat"},
    ANKI: {"Education"},
    CLAUDE: {"Network"},
}


def modes(**apps):
    names = {"writing": True, "building": True, "studying": True, "social": False}
    return {key: {"label": key.title(), "focus": focus, "apps": apps.get(key, [])} for key, focus in names.items()}


class Picking(unittest.TestCase):
    def pick(self, usage=None, browser=CHROME, apps=None, given=None):
        given = given or modes()
        personalize.fill(given, apps or APPS, usage or {}, browser)
        return {key: mode["apps"] for key, mode in given.items()}

    def test_each_app_where_it_belongs(self):
        picked = self.pick({CODE: 10, WRITER: 5, DISCORD: 7, ANKI: 3})
        self.assertIn(CODE, picked["building"])
        self.assertIn(WRITER, picked["writing"])
        self.assertIn(DISCORD, picked["social"])
        self.assertIn(ANKI, picked["studying"])
        self.assertNotIn(DISCORD, picked["building"])
        self.assertNotIn(CODE, picked["social"])

    def test_your_browser_in_every_mode(self):
        for mode in self.pick(browser=FIREFOX).values():
            self.assertIn(FIREFOX, mode)
            self.assertNotIn(CHROME, mode)

    def test_the_most_used_first(self):
        picked = self.pick({CODE: 3, CURSOR: 9, TERMINAL: 5, CHROME: 1})["building"]
        self.assertEqual(picked[:4], [CURSOR, TERMINAL, CODE, CHROME])

    def test_one_never_used_only_fills_in_when_few_were(self):
        self.assertNotIn(CODE, self.pick({CURSOR: 9, TERMINAL: 5})["building"])
        self.assertIn(CODE, self.pick({CURSOR: 9})["building"])

    def test_a_new_computer_with_no_usage_yet(self):
        picked = self.pick()["building"]
        self.assertIn(CHROME, picked)
        self.assertLessEqual(len(picked), 1 + personalize.UNUSED_FILLERS)

    def test_not_too_many_and_the_browser_stays(self):
        apps = {f"dev{index}.desktop": {"Development"} for index in range(20)} | {CHROME: {"WebBrowser"}}
        picked = self.pick({app: 5 for app in apps if app != CHROME}, apps=apps)["building"]
        self.assertEqual(len(picked), personalize.MAX_APPS)
        self.assertIn(CHROME, picked)

    def test_an_ai_app_helps_with_focused_work(self):
        picked = self.pick({CLAUDE: 4})
        for key in ("writing", "building", "studying"):
            self.assertIn(CLAUDE, picked[key])
        self.assertNotIn(CLAUDE, picked["social"])

    def test_a_default_thats_installed_stays_and_one_that_isnt_goes(self):
        given = modes(building=[TERMINAL, "not-here.desktop"])
        picked = self.pick({CODE: 9, CURSOR: 5}, given=given)["building"]
        self.assertIn(TERMINAL, picked)
        self.assertNotIn("not-here.desktop", picked)
        self.assertLess(picked.index(CURSOR), picked.index(TERMINAL))  # what you use comes first

    def test_no_browser_known(self):
        self.assertEqual(self.pick(browser=None, apps={WRITER: {"WordProcessor"}})["building"], [])

    def test_a_mode_it_doesnt_know_is_left_alone(self):
        given = {"kebun": {"label": "Kebun", "apps": ["x.desktop"]}}
        personalize.fill(given, APPS, {}, CHROME)
        self.assertEqual(given["kebun"]["apps"], ["x.desktop"])


class Usage(unittest.TestCase):
    def read(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "application_state"
            path.write_text(text)
            return personalize.usage(path)

    def test_what_gnome_keeps(self):
        text = '<application-state><context id=""><application id="code.desktop" score="12" last-seen="1"/>' \
               '<application id="x.desktop" score="not a number" last-seen="1"/></context></application-state>'
        self.assertEqual(self.read(text), {"code.desktop": 12.0})

    def test_a_broken_file(self):
        self.assertEqual(self.read("<application-state"), {})

    def test_no_file(self):
        self.assertEqual(personalize.usage(Path("/nowhere/application_state")), {})


class WritingConfig(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name, value in (("CONFIG_DIR", self.root), ("MODES_FILE", self.root / "modes.json"), ("ROUTINE_FILE", self.root / "routine.json")):
            patcher = mock.patch.object(installer, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in (("installed_apps", APPS), ("usage", {CURSOR: 9}), ("default_browser", FIREFOX)):
            patcher = mock.patch.object(personalize, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.options = mock.Mock(language="id", ai="none", routine=True)

    def written(self):
        installer._write_config(ROOT, self.options)
        return json.loads(installer.MODES_FILE.read_text())

    def test_a_new_install_gets_your_apps(self):
        apps = self.written()["modes"]["building"]["apps"]
        self.assertEqual(apps[0], CURSOR)
        self.assertIn(FIREFOX, apps)

    def test_modes_you_already_have_stay(self):
        installer.MODES_FILE.write_text(json.dumps({"modes": {"building": {"apps": ["mine.desktop"]}}}))
        self.assertEqual(self.written()["modes"]["building"]["apps"], ["mine.desktop"])

    def test_if_it_goes_wrong_the_defaults_are_still_written(self):
        personalize.installed_apps.side_effect = OSError("no")
        self.assertIn("apps", self.written()["modes"]["building"])


if __name__ == "__main__":
    unittest.main()
