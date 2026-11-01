"""Restoring the desk. Run with: python3 -m unittest discover tests"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import session  # noqa: E402

CLAUDE = {"cwd": "/tmp", "claude": "aaa", "flags": [], "exe": "claude", "title": "Fix the login"}


def snapshot(name, when, **extra):
    return {"session": name, "time": when, "mode": None, "terminals": [{"window": None, "tabs": [{"cwd": "/tmp"}], "active": 0}],
            "servers": [], "databases": [], "chrome": False, "files": [], "apps": [], **extra}


class ToRestore(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(session, "SESSION_FILE", Path(folder.name) / "session.json")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, snapshots, restored=()):
        session.SESSION_FILE.write_text(json.dumps({"snapshots": snapshots, "restored": list(restored)}))

    def test_offers_the_previous_login(self):
        now = session.time.time()
        self.write([snapshot("boot1-3", now - 60)])
        self.assertEqual(session.to_restore("boot1-4")["session"], "boot1-3")

    def test_session_number_used_again_after_a_reboot(self):
        now = session.time.time()
        self.write([snapshot("boot2-3", now - 60)], restored=["boot1-3"])
        self.assertEqual(session.to_restore("boot2-7")["session"], "boot2-3")

    def test_not_offered_twice(self):
        now = session.time.time()
        self.write([snapshot("boot1-3", now - 60)], restored=["boot1-3"])
        self.assertIsNone(session.to_restore("boot1-4"))

    def test_not_offered_after_unlocking(self):
        now = session.time.time()
        self.write([snapshot("boot1-3", now - 60)])
        self.assertIsNone(session.to_restore("boot1-3"))

    def test_the_id_changes_with_the_boot(self):
        with mock.patch.object(session.subprocess, "run", return_value=mock.Mock(stdout="3\n")), \
                mock.patch.object(session.Path, "read_text", return_value="abcdef12-rest\n"):
            self.assertEqual(session.current_session(), "abcdef12-3")


class WindowShowing(unittest.TestCase):
    windows = [{"id": 5, "app": session.PTYXIS, "title": "◐ Fix the login — claude --dangerously-skip-permissions "},
               {"id": 6, "app": "google-chrome.desktop", "title": "Fix the login"}]

    def entry(self):
        return {"window": 0, "tabs": [dict(CLAUDE)], "active": 0}

    def test_finds_the_conversation_you_reopened(self):
        with mock.patch.object(session, "claude_running", return_value=True):
            self.assertEqual(session.window_showing(self.windows, self.entry(), set())["id"], 5)

    def test_a_window_is_only_used_once(self):
        with mock.patch.object(session, "claude_running", return_value=True):
            self.assertIsNone(session.window_showing(self.windows, self.entry(), {5}))

    def test_nothing_when_it_isnt_running(self):
        with mock.patch.object(session, "claude_running", return_value=False):
            self.assertIsNone(session.window_showing(self.windows, self.entry(), set()))


class BrowserCommand(unittest.TestCase):
    def command(self, launchers, windows=()):
        def new(app):
            line = launchers.get(app)
            return mock.Mock(get_commandline=lambda: line) if line else None
        with mock.patch.object(session.Gio.DesktopAppInfo, "new", side_effect=new):
            return session.browser_command({"windows": [{"app": app} for app in windows]})

    def test_keeps_the_flags_in_your_launcher(self):
        line = "/opt/google/chrome/chrome --force-device-scale-factor=1.5 %U"
        self.assertEqual(self.command({"google-chrome.desktop": line}),
                         ["/opt/google/chrome/chrome", "--force-device-scale-factor=1.5"])

    def test_uses_the_browser_you_had_open(self):
        launchers = {"google-chrome.desktop": "chrome %U", "chromium.desktop": "chromium %U"}
        self.assertEqual(self.command(launchers, windows=["chromium.desktop"]), ["chromium"])

    def test_flatpak_codes_are_left_out(self):
        line = "/usr/bin/flatpak run --command=/app/bin/chrome com.google.Chrome @@u %U @@"
        self.assertEqual(self.command({"com.google.Chrome.desktop": line}),
                         ["/usr/bin/flatpak", "run", "--command=/app/bin/chrome", "com.google.Chrome"])

    def test_no_browser(self):
        self.assertIsNone(self.command({}))


class RunByDeskAI(unittest.TestCase):
    PREFIX = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"

    def test_a_dev_server_it_started(self):
        self.assertTrue(session._run_by_deskai(self.PREFIX + "deskai-gudang-dev.service\n"))

    def test_not_a_server_you_started_in_a_terminal_it_opened(self):
        self.assertFalse(session._run_by_deskai(self.PREFIX + "app-deskai-ptyxis-1a2b3c4d.scope\n"))
        self.assertFalse(session._run_by_deskai(self.PREFIX + "app-deskai-gio-1a2b3c4d.scope/vte-spawn-1.scope\n"))

    def test_not_one_in_a_terminal_of_your_own(self):
        self.assertFalse(session._run_by_deskai(self.PREFIX + "app-gnome-org.gnome.Ptyxis-1234.scope\n"))
        self.assertFalse(session._run_by_deskai(self.PREFIX + "deskaid.service\n"))


class RestoreWithAnAppGone(unittest.TestCase):
    def test_the_rest_still_comes_back(self):
        snap = {"session": "s", "time": 0, "mode": None, "terminals": [], "servers": [], "databases": [], "chrome": True,
                "files": [], "apps": ["gone.desktop", "here.desktop"], "windows": []}
        progress = {}
        dock = mock.Mock()
        dock.windows.return_value = []
        with mock.patch.object(session, "Dock", return_value=dock), mock.patch.object(session.routine, "load", return_value={}), \
                mock.patch.object(session.routine, "tool_path", return_value="/usr/bin"), mock.patch.object(session.routine, "find_claude"), \
                mock.patch.object(session, "running", return_value=False), mock.patch.object(session, "mark_restored") as marked, \
                mock.patch.object(session.subprocess, "Popen"), mock.patch.object(session.time, "sleep"), \
                mock.patch.object(session.launcher, "open_app", side_effect=lambda app: app == "here.desktop"), \
                mock.patch.object(session, "browser_command", side_effect=TypeError("constructor returned NULL")):
            session.restore(snap, progress.__setitem__, lambda _m: None)
        self.assertEqual(progress, {"chrome": "failed", "gone": "failed", "here": "done"})
        marked.assert_called_once()


class Restore(unittest.TestCase):
    """The terminal part of a restore, with the dock and Ptyxis faked."""

    def run_restore(self, entry, windows, running):
        dock = mock.Mock()
        dock.windows.side_effect = lambda: list(windows)
        started = []

        def popen(args, **_kwargs):
            started.append(args)
            windows.append({"id": 100 + len(started), "app": session.PTYXIS, "title": "shell"})

        snap = {"session": "s", "time": 0, "mode": None, "terminals": [entry], "servers": [], "databases": [],
                "chrome": False, "files": [], "apps": [],
                "windows": [{"app": session.PTYXIS, "title": "old", "rect": [1, 2, 3, 4], "max_h": False, "max_v": False, "stack": 0}]}
        with mock.patch.object(session, "Dock", return_value=dock), mock.patch.object(session.routine, "load", return_value={"path": "/bin"}), \
                mock.patch.object(session, "claude_running", side_effect=lambda s: s in running), \
                mock.patch.object(session.shutil, "which", return_value="/usr/bin/ptyxis"), \
                mock.patch.object(session, "running", return_value=True), mock.patch.object(session, "mark_restored"), \
                mock.patch.object(session.subprocess, "Popen", side_effect=popen), mock.patch.object(session.time, "sleep"):
            session.restore(snap, lambda *_a: None, lambda _m: None)
        return dock, started

    def test_a_reopened_conversation_keeps_its_window(self):
        windows = [{"id": 5, "app": session.PTYXIS, "title": "✳ Fix the login"}]
        dock, started = self.run_restore({"window": 0, "tabs": [dict(CLAUDE)], "active": 0}, windows, {"aaa"})
        self.assertEqual(started, [])
        self.assertEqual(dock.place.call_args.args[0], 5)
        self.assertEqual(dock.place.call_args.args[1]["rect"], [1, 2, 3, 4])

    def test_a_closed_conversation_is_resumed(self):
        dock, started = self.run_restore({"window": 0, "tabs": [dict(CLAUDE)], "active": 0}, [], set())
        self.assertEqual(len(started), 1)
        self.assertIn("--resume aaa", started[0][-1])
        self.assertEqual(dock.place.call_args.args[0], 101)


if __name__ == "__main__":
    unittest.main()
