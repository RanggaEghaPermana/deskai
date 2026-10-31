"""DeskAI's dock taking the place of the others. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import docks  # noqa: E402

UBUNTU = "ubuntu-dock@ubuntu.com"
DASH = "dash-to-dock@micxgx.gmail.com"
LITE = "dash2dock-lite@icedman.github.com"
DOCKER = "docker@stickman_0x00.com"


class FakeSettings:
    def __init__(self, enabled=(), disabled=()):
        self.lists = {"enabled-extensions": list(enabled), "disabled-extensions": list(disabled)}

    def get_strv(self, key):
        return list(self.lists[key])

    def set_strv(self, key, value):
        self.lists[key] = list(value)

    @property
    def enabled(self):
        return self.lists["enabled-extensions"]

    @property
    def disabled(self):
        return self.lists["disabled-extensions"]


class Docks(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.user = root / "user"
        self.system = root / "system"
        self.modes = root / "modes"
        for path in (self.user, self.system, self.modes):
            path.mkdir()
        self.record = root / "state" / "docks-off.json"
        for name, value in (("USER_EXTENSIONS", self.user), ("SYSTEM_EXTENSIONS", self.system),
                            ("SESSION_MODES", self.modes), ("RECORD", self.record)):
            patcher = mock.patch.object(docks, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(docks.Gio.Settings, "sync")
        patcher.start()
        self.addCleanup(patcher.stop)

    def install(self, *uuids, where=None):
        for uuid in uuids:
            (where or self.user).joinpath(uuid).mkdir()

    def session_starts(self, *uuids):
        self.install(*uuids, where=self.system)
        (self.modes / "ubuntu.json").write_text(json.dumps({"enabledExtensions": list(uuids)}))


class TurningOff(Docks):
    def test_a_dock_the_session_itself_turns_on(self):
        self.session_starts(UBUNTU)
        settings = FakeSettings()
        self.assertEqual(docks.turn_off_others(settings), ["Ubuntu Dock"])
        self.assertEqual(settings.disabled, [UBUNTU])  # the one list that beats the session's own

    def test_a_dock_you_installed(self):
        self.install(DASH)
        settings = FakeSettings(enabled=["a@b", DASH, "c@d"])
        self.assertEqual(docks.turn_off_others(settings), ["Dash to Dock"])
        self.assertEqual((settings.enabled, settings.disabled), (["a@b", "c@d"], [DASH]))

    def test_all_of_them(self):
        self.session_starts(UBUNTU)
        self.install(DASH)
        settings = FakeSettings(enabled=[DASH])
        self.assertEqual(sorted(docks.turn_off_others(settings)), ["Dash to Dock", "Ubuntu Dock"])

    def test_one_already_off_isnt_mentioned(self):
        self.install(LITE)
        settings = FakeSettings(disabled=[LITE])
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual(json.loads(self.record.read_text()), {})

    def test_a_dock_installed_but_never_turned_on_isnt_mentioned(self):
        self.install(DASH)
        settings = FakeSettings()
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual(settings.disabled, [])

    def test_a_dock_listed_but_no_longer_installed_is_left_alone(self):
        settings = FakeSettings(enabled=[DASH])
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual(settings.enabled, [DASH])

    def test_other_extensions_stay_as_they_are(self):
        self.install(DOCKER)
        settings = FakeSettings(enabled=[DOCKER])
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual((settings.enabled, settings.disabled), ([DOCKER], []))

    def test_only_once(self):
        self.session_starts(UBUNTU)
        settings = FakeSettings()
        docks.turn_off_others(settings)
        settings.set_strv("disabled-extensions", [])  # you turned it back on yourself
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual(settings.disabled, [])

    def test_a_dock_installed_after_the_first_time_is_your_choice(self):
        docks.turn_off_others(FakeSettings())
        self.install(DASH)
        settings = FakeSettings(enabled=[DASH])
        self.assertEqual(docks.turn_off_others(settings), [])
        self.assertEqual(settings.enabled, [DASH])

    def test_a_broken_session_mode_file_doesnt_stop_it(self):
        self.install(DASH)
        (self.modes / "broken.json").write_text("{")
        (self.modes / "list.json").write_text("[]")
        self.assertEqual(docks.turn_off_others(FakeSettings(enabled=[DASH])), ["Dash to Dock"])

    def test_without_gnomes_settings(self):
        with mock.patch.object(docks, "shell_settings", return_value=None):
            self.assertEqual(docks.turn_off_others(), [])
        self.assertFalse(self.record.exists())  # nothing happened, so next time it still can


class Restoring(Docks):
    def off(self, settings):
        docks.turn_off_others(settings)
        return settings

    def test_everything_goes_back(self):
        self.session_starts(UBUNTU)
        self.install(DASH)
        settings = self.off(FakeSettings(enabled=["a@b", DASH]))
        docks.restore(settings)
        self.assertEqual((sorted(settings.enabled), settings.disabled), (["a@b", DASH], []))
        self.assertFalse(self.record.exists())

    def test_one_the_session_turned_on_isnt_added_to_your_list(self):
        self.session_starts(UBUNTU)
        settings = self.off(FakeSettings())
        docks.restore(settings)
        self.assertEqual((settings.enabled, settings.disabled), ([], []))

    def test_one_you_had_off_stays_off(self):
        self.session_starts(UBUNTU)
        self.install(LITE)
        settings = self.off(FakeSettings(disabled=[LITE]))
        docks.restore(settings)
        self.assertEqual(settings.disabled, [LITE])

    def test_changes_you_made_since_stay(self):
        self.session_starts(UBUNTU)
        settings = self.off(FakeSettings())
        settings.set_strv("enabled-extensions", ["new@you"])
        settings.set_strv("disabled-extensions", [UBUNTU, "other@you"])
        docks.restore(settings)
        self.assertEqual((settings.enabled, settings.disabled), (["new@you"], ["other@you"]))

    def test_nothing_recorded(self):
        settings = FakeSettings(enabled=["a@b"], disabled=[UBUNTU])
        docks.restore(settings)
        self.assertEqual((settings.enabled, settings.disabled), (["a@b"], [UBUNTU]))

    def test_a_broken_record(self):
        self.record.parent.mkdir(parents=True)
        self.record.write_text("{")
        settings = FakeSettings(disabled=[UBUNTU])
        docks.restore(settings)
        self.assertEqual(settings.disabled, [UBUNTU])

    def test_it_can_happen_again_after_a_reinstall(self):
        self.session_starts(UBUNTU)
        settings = self.off(FakeSettings())
        docks.restore(settings)
        self.assertEqual(docks.turn_off_others(settings), ["Ubuntu Dock"])


if __name__ == "__main__":
    unittest.main()
