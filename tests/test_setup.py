"""What setup has to get right on each GNOME and Ubuntu it supports. Run with: python3 -m unittest discover tests"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "setup"))

import installer  # noqa: E402


class PrivateFiles(unittest.TestCase):
    def test_only_you_can_read_them(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "modes.json"
            path.write_text("{}")
            path.chmod(0o664)
            installer._write_private_json(path, {"language": "id"})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class TheCommand(unittest.TestCase):
    """AI agents run it as a hook. Claude Code blocks your prompt on exit code 2, which is what Python exits
    with when its script is gone, as when the clone is moved or the package removed."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name) / "it's a folder"
        (self.folder / "app" / "daemon").mkdir(parents=True)
        self.command = self.folder / "deskai"
        patcher = mock.patch.object(installer, "COMMAND", self.command)
        patcher.start()
        self.addCleanup(patcher.stop)
        installer._write_command(self.folder / "app", sys.executable)

    def script(self, text):
        (self.folder / "app" / "daemon" / "deskai.py").write_text(text)

    def run_it(self, *args, given=""):
        return subprocess.run(["sh", str(self.command), *args], input=given, capture_output=True, text=True, timeout=30)

    def test_a_hook_with_the_files_gone_gets_in_nobodys_way(self):
        done = self.run_it("hook", "claude", given="{}")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_a_hook_with_python_gone_gets_in_nobodys_way(self):
        installer._write_command(self.folder / "app", "/nonexistent/python3")
        done = self.run_it("hook", "claude", given="{}")
        self.assertEqual((done.returncode, done.stdout, done.stderr), (0, "", ""))

    def test_a_hook_that_fails_gets_in_nobodys_way(self):
        self.script("import sys\nsys.exit(2)\n")
        self.assertEqual(self.run_it("hook", "claude").returncode, 0)

    def test_a_hook_gets_what_it_was_given(self):
        self.script("import sys\nprint(sys.argv[1:], sys.stdin.read())\n")
        self.assertEqual(self.run_it("hook", "claude", given="payload").stdout, "['hook', 'claude'] payload\n")

    def test_anything_else_says_what_happened_in_plain_words(self):
        done = self.run_it("status")
        self.assertEqual(done.returncode, 1)
        self.assertIn(str(self.folder / "app"), done.stderr)
        self.assertNotIn("Errno", done.stderr)

    def test_anything_else_keeps_its_own_exit_code(self):
        self.script("import sys\nprint(sys.argv[1:])\nsys.exit(3)\n")
        done = self.run_it("status", "--all")
        self.assertEqual((done.returncode, done.stdout), (3, "['status', '--all']\n"))


class Versions(unittest.TestCase):
    def gnome_check(self, version):
        with mock.patch.object(installer, "shell_version", return_value=version), \
                mock.patch.object(installer, "user_systemd", return_value=True), \
                mock.patch.object(installer, "has_typelib", return_value=True), \
                mock.patch.object(installer, "package_manager", return_value="apt"), \
                mock.patch.dict(installer.os.environ, {"XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}):
            return next(check for check in installer.check_system() if check.id == "gnome")

    def test_the_ones_written_for(self):
        for version in (46, 50, 51):
            self.assertTrue(self.gnome_check(version).ok, version)

    def test_the_others(self):
        for version in (42, 45, 47, 48, 49, 52):
            self.assertFalse(self.gnome_check(version).ok, version)


class DatabasePermission(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.generated = self.root / "50-deskai-services.rules"
        self.rule = self.root / "rules" / "50-deskai-services.rules"
        for name, value in (("POLKIT_RULE", self.rule), ("POLKIT_GENERATED", self.generated)):
            patcher = mock.patch.object(installer, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def removal(self):
        with mock.patch.object(installer, "run") as run:
            dict(installer.uninstall_steps())["polkit"]()
        return run

    def test_never_asked_for_it(self):
        self.assertFalse(installer.has_polkit_rule())
        self.removal().assert_not_called()

    def test_a_folder_only_root_can_read(self):
        # The rules folder is root's. Python 3.12 raises on it, and 3.14 says the file isn't there.
        with mock.patch.object(Path, "exists", side_effect=PermissionError("denied")):
            self.assertFalse(installer.has_polkit_rule())

    def test_it_can_be_seen(self):
        self.rule.parent.mkdir()
        self.rule.write_text("rule")
        self.assertTrue(installer.has_polkit_rule())

    def test_it_was_made_here_though_the_folder_cant_be_read(self):
        self.generated.write_text("rule")
        self.assertTrue(installer.has_polkit_rule())
        run = self.removal()
        self.assertEqual(run.call_args.args[:3], ("pkexec", "rm", "-f"))
        self.assertFalse(self.generated.exists())


class Spinner(unittest.TestCase):
    def setUp(self):
        import deskai_setup
        self.setup_window = deskai_setup

    def test_where_libadwaita_has_one(self):
        with mock.patch.object(self.setup_window, "Adw") as adw:
            self.assertEqual(self.setup_window.new_spinner(), adw.Spinner.return_value)

    def test_on_ubuntu_24_04(self):
        with mock.patch.object(self.setup_window, "Adw", spec=[]), mock.patch.object(self.setup_window, "Gtk") as gtk:
            spinner = self.setup_window.new_spinner()
        self.assertEqual(spinner, gtk.Spinner.return_value)
        spinner.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
