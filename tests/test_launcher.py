"""Apps have to outlive the daemon: stopping a service stops everything in its cgroup, however detached.
Run with: python3 -m unittest discover tests"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import launcher  # noqa: E402


class OwnScope(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.runtime = Path(folder.name)

    def scoped(self, argv):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime)}), \
                mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/systemd-run"):
            return launcher.own_scope(argv)

    def with_user_systemd(self):
        (self.runtime / "systemd").mkdir()
        (self.runtime / "systemd" / "private").touch()

    def test_it_gets_a_scope_of_its_own(self):
        self.with_user_systemd()
        command = self.scoped(["/usr/bin/ptyxis", "--new-window", "-d", "/w/app"])
        self.assertEqual(command[:5], ["/usr/bin/systemd-run", "--user", "--scope", "--quiet", "--collect"])
        self.assertEqual(command[-5:], ["--", "/usr/bin/ptyxis", "--new-window", "-d", "/w/app"])

    def test_each_scope_has_a_name_systemd_takes_and_of_its_own(self):
        self.with_user_systemd()
        names = [next(arg for arg in self.scoped(["/opt/My App!/run me"]) if arg.startswith("--unit=")) for _ in range(2)]
        self.assertRegex(names[0], r"^--unit=app-deskai-run_me-[0-9a-f]{8}$")
        self.assertNotEqual(*names)

    def test_with_no_user_systemd_it_just_runs(self):
        self.assertEqual(self.scoped(["ptyxis"]), ["ptyxis"])

    def test_with_no_runtime_folder_it_just_runs(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            self.assertEqual(launcher.own_scope(["ptyxis"]), ["ptyxis"])


class Opening(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(launcher, "own_scope", side_effect=lambda argv: ["scope", *argv]),
                        mock.patch.object(launcher.shutil, "which", return_value="/usr/bin/gio")):
            patcher.start()
            self.addCleanup(patcher.stop)

    def done(self, code=0):
        return mock.patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], code))

    def installed(self):
        info = mock.Mock()
        info.get_filename.return_value = "/usr/share/applications/x.desktop"
        return mock.patch.object(launcher.Gio.DesktopAppInfo, "new", return_value=info)

    def test_an_app(self):
        with self.installed(), self.done() as run:
            self.assertTrue(launcher.open_app("x.desktop"))
        self.assertEqual(run.call_args.args[0], ["scope", "gio", "launch", "/usr/share/applications/x.desktop"])

    def test_nothing_is_captured(self):
        # The app inherits what gio has: reading a pipe it holds open would wait until you close the app.
        with self.installed(), self.done() as run:
            launcher.open_app("x.desktop")
        self.assertFalse({"stdout", "stderr", "capture_output"} & set(run.call_args.kwargs))

    def test_an_app_that_wont_start(self):
        with self.installed(), self.done(1):
            self.assertFalse(launcher.open_app("x.desktop"))

    def test_an_app_thats_gone(self):
        # PyGObject raises for this on Ubuntu 26.04 and returns None on 24.04.
        for how in ({"side_effect": TypeError("constructor returned NULL")}, {"return_value": None}):
            with mock.patch.object(launcher.Gio.DesktopAppInfo, "new", **how), self.done() as run:
                self.assertFalse(launcher.open_app("gone.desktop"))
            run.assert_not_called()

    def test_a_page(self):
        with self.done() as run:
            launcher.open_uri("https://example.com")
        self.assertEqual(run.call_args.args[0], ["scope", "gio", "open", "https://example.com"])

    def test_a_page_that_wont_open_says_so(self):
        with self.done(1), self.assertRaises(OSError):
            launcher.open_uri("https://example.com")

    def test_with_no_gio_glib_does_it(self):
        with mock.patch.object(launcher.shutil, "which", return_value=None), self.done() as run, \
                mock.patch.object(launcher.Gio.AppInfo, "launch_default_for_uri") as default:
            launcher.open_uri("https://example.com")
        default.assert_called_once()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
