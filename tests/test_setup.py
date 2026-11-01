"""What setup has to get right on each GNOME and Ubuntu it supports. Run with: python3 -m unittest discover tests"""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "setup"))

import installer  # noqa: E402

# What the tools really say. Those marked as measured came from running them without changing anything: apt-get -s, a user
# with no root, a network namespace with no network, an Ollama server turned off. The rest are the wording in the sources of
# apt, pkexec, systemctl, pip and venv, with the blanks filled in, since seeing them would take changing something.
APT_NOT_FOUND = "E: Unable to locate package gir1.2-atspi-2.0\n"  # measured
APT_NOT_ROOT = ("E: Could not open lock file /var/lib/dpkg/lock-frontend - open (13: Permission denied)\n"  # measured
                "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), are you root?\n")
APT_BUSY = ("E: Could not get lock /var/lib/dpkg/lock-frontend. It is held by process 2841 (unattended-upgr)\n"
            "N: Be aware that removing the lock file is not a solution and may break your system.\n"
            "E: Unable to acquire the dpkg frontend lock (/var/lib/dpkg/lock-frontend), is another process using it?\n")
APT_NO_ROOM = "E: You don't have enough free space in /var/cache/apt/archives/.\n"
APT_OFFLINE = ("E: Failed to fetch http://archive.ubuntu.com/ubuntu/pool/main/a/at-spi2-core/gir1.2-atspi-2.0_2.52.0_amd64.deb  "
               "Temporary failure resolving 'archive.ubuntu.com'\n")
APT_NO_CANDIDATE = "E: Package 'gir1.2-atspi-2.0' has no installation candidate\n"
APT_DPKG_FAILED = "E: Sub-process /usr/bin/dpkg returned an error code (1)\n"
PKEXEC_CLOSED = "Error executing command as another user: Request dismissed\n"
PKEXEC_REFUSED = "Error executing command as another user: Not authorized\n\nThis incident has been reported.\n"
PKEXEC_NO_AGENT = "Error executing command as another user: No authentication agent found.\n"
NO_VENV = ("The virtual environment was not created successfully because ensurepip is not\navailable.  On Debian/Ubuntu systems, "
           "you need to install the python3-venv\npackage using the following command.\n\n    apt install python3.12-venv\n\n"
           "You may need to use sudo with that command.  After installing the python3-venv\npackage, recreate your virtual "
           "environment.\n\nFailing command: /tmp/venv/bin/python3\n")
VENV_NOT_THERE = "Error: [Errno 2] No such file or directory: '/proc/deskai-venv'\n"  # measured
PIP_OFFLINE = ("WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by "  # measured
               "'NewConnectionError('<pip._vendor.urllib3.connection.HTTPSConnection object at 0x7ede64a47380>: Failed to establish "
               "a new connection: [Errno -3] Temporary failure in name resolution')': /simple/anthropic/\n"
               "ERROR: Could not find a version that satisfies the requirement anthropic (from versions: none)\n"
               "ERROR: No matching distribution found for anthropic\n")
PIP_NOTHING_THERE = ("ERROR: Could not find a version that satisfies the requirement anthropic (from versions: none)\n"  # measured
                     "ERROR: No matching distribution found for anthropic\n")
PIP_NO_ROOM = "ERROR: Could not install packages due to an OSError: [Errno 28] No space left on device\n"
PIP_NOT_ALLOWED = "ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied: '/usr/lib/python3/x'\n"
OLLAMA_BAR = ("\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \u280b \x1b[K\x1b[?25h\x1b[?2026l"  # measured, as all of Ollama's
              "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l\n")
OLLAMA_OFF = "Error: could not connect to ollama server, run 'ollama serve' to start it\n"
OLLAMA_NO_MODEL = OLLAMA_BAR + "Error: pull model manifest: file does not exist\n"
OLLAMA_OFFLINE = OLLAMA_BAR + ('Error: pull model manifest: Get "https://registry.ollama.ai/v2/library/qwen3/manifests/1.7b": dial tcp: lookup '
                               "registry.ollama.ai on 127.0.0.53:53: read udp 127.0.0.1:44053->127.0.0.53:53: read: connection refused\n")
OLLAMA_BAD_DOWNLOAD = OLLAMA_BAR + "Error: digest mismatch, file must be downloaded again: want sha256:aa, got sha256:bb\n"
NO_BUS = ("Failed to connect to user scope bus via local transport: $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR not defined "  # measured
          "(consider using --machine=<user>@.host --user to connect to bus of other user)\n")
UNIT_NOT_FOUND = "Failed to restart deskaid.service: Unit deskaid.service not found.\n"  # measured
UNIT_FAILED = ('Job for deskaid.service failed because the control process exited with error code.\nSee "systemctl --user status '
               'deskaid.service" and "journalctl --user -xeu deskaid.service" for details.\n')
HOOKS_NOT_ALL = ("Claude Code  failed: /home/you/.claude/settings.json isn't valid JSON (line 1, column 2)\n"  # measured
                 "Codex        failed: /home/you/.codex/config.toml already has its own `notify`; DeskAI won't overwrite it\n"
                 "Gemini CLI   installed  (/home/you/.gemini/settings.json)\n\n"
                 "Agent sessions already running pick the hooks up when reopened. Backups of the original files: *.deskai-bak\n")
HOOKS_NOT_ALLOWED = "Claude Code  failed: /home/you/.claude/settings.json: Permission denied\n"  # measured
HOOKS_CRASHED = ('Traceback (most recent call last):\n  File "/usr/share/deskai/daemon/deskai.py", line 12, in <module>\n'
                 "ModuleNotFoundError: No module named 'hooks'\n")
RAW = ("[Errno", "Traceback", "Error:", "E: ", "ERROR", "/var/", "/usr/", "/tmp/", "journalctl", "\x1b")


def failed(code, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout, stderr)


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


class TurningTheExtensionsOff(unittest.TestCase):
    def said(self, *states):
        answers = iter(states)
        return mock.patch.object(installer, "run", side_effect=lambda *_a, **_k: mock.Mock(stdout=f"  Enabled: No\n  State: {next(answers)}\n"))

    def test_it_waits_for_gnome_to_finish(self):
        with self.said("DEACTIVATING", "ACTIVE", "INACTIVE") as run, mock.patch.object(installer.time, "sleep"):
            installer._wait_until_off("a")
        self.assertEqual(run.call_count, 3)

    def test_for_all_of_them(self):
        with self.said("INACTIVE", "ACTIVE", "INACTIVE", "INACTIVE") as run, mock.patch.object(installer.time, "sleep"):
            installer._wait_until_off("a", "b")
        self.assertEqual(run.call_count, 4)

    def test_not_for_ever(self):
        with mock.patch.object(installer, "run", return_value=mock.Mock(stdout="State: ACTIVE")), mock.patch.object(installer.time, "sleep"):
            installer._wait_until_off("a", timeout=0.01)

    def test_with_no_tool_to_ask_it_doesnt_wait(self):
        with mock.patch.object(installer, "run", side_effect=FileNotFoundError(2, "No such file", "gnome-extensions")), \
                mock.patch.object(installer.time, "sleep") as sleep:
            installer._wait_until_off("a")
        sleep.assert_not_called()

    def test_gone_counts_as_off(self):
        with mock.patch.object(installer, "run", return_value=mock.Mock(stdout="", stderr="Extension doesn't exist")), \
                mock.patch.object(installer.time, "sleep") as sleep:
            installer._wait_until_off("a")
        sleep.assert_not_called()

    def test_the_service_stops_before_the_docks_come_back(self):
        order = [step for step, _fn in installer.uninstall_steps()]
        self.assertLess(order.index("service"), order.index("extension"))

    def test_it_waits_before_it_gives_the_docks_back(self):
        seen = []
        shell = PuttingItBack.Settings({"enabled-extensions": [installer.UUID]})
        with mock.patch.object(installer.Gio.Settings, "new", return_value=shell), mock.patch.object(installer.Gio.Settings, "sync"), \
                mock.patch.object(installer, "_login_screen", return_value=None), \
                mock.patch.object(installer, "_wait_until_off", side_effect=lambda *_: seen.append("waited")), \
                mock.patch.object(installer.docks, "restore", side_effect=lambda _s: seen.append("docks back")):
            dict(installer.uninstall_steps())["extension"]()
        self.assertEqual(seen, ["waited", "docks back"])


class HooksAreYourCall(unittest.TestCase):
    def test_off_unless_theyre_there_already(self):
        source = (ROOT / "setup" / "deskai_setup.py").read_text()
        self.assertIn("self.hooks = installer.hooks_installed()", source)


class PuttingItBack(unittest.TestCase):
    """What setup found changed goes back when DeskAI is removed, with GNOME's settings faked."""

    class Settings:
        def __init__(self, values, unset=()):
            self.values = dict(values)
            self.unset = set(unset)

        def get_strv(self, key):
            return list(self.values.get(key, []))

        def set_strv(self, key, value):
            self.values[key] = list(value)

        def get_boolean(self, key):
            return self.values.get(key, False)

        def set_boolean(self, key, value):
            self.values[key] = value

        def get_string(self, key):
            return self.values.get(key, "")

        def set_string(self, key, value):
            self.values[key] = value
            self.unset.discard(key)

        def get_user_value(self, key):
            return None if key in self.unset else self.values.get(key)

        def reset(self, key):
            self.values.pop(key, None)
            self.unset.add(key)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.shell = self.Settings({"enabled-extensions": ["other@x"], "disabled-extensions": [installer.LOGIN_UUID], "disable-user-extensions": True})
        self.login = self.Settings({}, unset={"background-color"})
        for owner, name, value in ((installer, "INSTALLED", self.root / "installed.json"), (installer, "_login_screen", lambda: self.login),
                                   (installer.Gio.Settings, "new", lambda _schema: self.shell), (installer.Gio.Settings, "sync", lambda: None),
                                   (installer.docks, "restore", lambda _settings: None), (installer, "CONFIG", self.root / "config"),
                                   (installer, "version", lambda: "9.9"), (installer, "_wait_until_off", lambda *_: None),
                                   (installer, "linked_here", lambda: True), (installer, "is_system_payload", lambda: False)):
            patcher = mock.patch.object(owner, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def install(self):
        dict(installer.install_steps(installer.Options()))["finish"]()

    def remove(self):
        dict(installer.uninstall_steps())["extension"]()

    def test_extensions_that_were_off_go_off_again(self):
        self.install()
        self.assertIs(self.shell.values["disable-user-extensions"], False)
        self.remove()
        self.assertIs(self.shell.values["disable-user-extensions"], True)

    def test_extensions_that_were_on_stay_on(self):
        self.shell.values["disable-user-extensions"] = False
        self.install()
        self.remove()
        self.assertIs(self.shell.values["disable-user-extensions"], False)

    def test_an_update_doesnt_think_it_found_them_on(self):
        self.install()
        self.install()
        self.remove()
        self.assertIs(self.shell.values["disable-user-extensions"], True)

    def test_a_login_color_you_chose_stays(self):
        self.login = self.Settings({"background-color": installer.LOGIN_BACKGROUND})
        self.install()
        self.remove()
        self.assertEqual(self.login.get_string("background-color"), installer.LOGIN_BACKGROUND)

    def test_one_it_set_goes(self):
        self.install()
        self.assertEqual(self.login.get_string("background-color"), installer.LOGIN_BACKGROUND)
        self.remove()
        self.assertIsNone(self.login.get_user_value("background-color"))

    def test_an_install_from_before_this_was_noted(self):
        installer.INSTALLED.write_text('{"version": "0.4.1"}')
        self.login = self.Settings({"background-color": installer.LOGIN_BACKGROUND})
        self.remove()
        self.assertIsNone(self.login.get_user_value("background-color"))

    def test_deskai_login_goes_on_the_first_time_only(self):
        self.install()
        self.assertIn(installer.LOGIN_UUID, self.shell.values["enabled-extensions"])
        self.shell.values["enabled-extensions"].remove(installer.LOGIN_UUID)
        self.install()
        self.assertNotIn(installer.LOGIN_UUID, self.shell.values["enabled-extensions"])
        self.assertIn(installer.UUID, self.shell.values["enabled-extensions"])

    def test_a_record_that_isnt_one(self):
        installer.INSTALLED.write_text("[1]")
        self.assertEqual(installer.install_record(), {})
        self.assertEqual(installer.installed_version(), "")

    def files(self):
        for name in ("COMMAND", "LAUNCHER", "ICON", "EXTENSION_LINK", "LOGIN_LINK", "SHARE"):
            patcher = mock.patch.object(installer, name, self.root / "gone" / name)
            patcher.start()
            self.addCleanup(patcher.stop)
        dict(installer.uninstall_steps())["files"]()

    def test_a_terminal_session_set_aside_comes_back(self):
        session = self.root / "config" / "org.gnome.Ptyxis" / "session.gvariant"
        session.parent.mkdir(parents=True)
        session.with_name("session.gvariant.deskai-bak").write_text("old tabs")
        self.files()
        self.assertEqual(session.read_text(), "old tabs")
        self.assertFalse(session.with_name("session.gvariant.deskai-bak").exists())

    def test_but_not_over_one_it_has_now(self):
        session = self.root / "config" / "org.gnome.Ptyxis" / "session.gvariant"
        session.parent.mkdir(parents=True)
        session.write_text("new tabs")
        session.with_name("session.gvariant.deskai-bak").write_text("old tabs")
        self.files()
        self.assertEqual(session.read_text(), "new tabs")
        self.assertFalse(session.with_name("session.gvariant.deskai-bak").exists())


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
        with mock.patch.object(installer, "run", return_value=mock.Mock(returncode=0)) as run:
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
        with mock.patch.object(installer, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
            dict(installer.uninstall_steps())["polkit"]()
        self.assertEqual(run.call_args.args[:3], ("pkexec", "rm", "-f"))
        self.assertFalse(self.generated.exists())

    def test_closing_the_password_window_leaves_the_note_for_trying_again(self):
        self.generated.write_text("rule")
        dismissed = mock.Mock(returncode=126, stdout="", stderr="Error executing command as another user: Request dismissed\n")
        with mock.patch.object(installer, "run", return_value=dismissed), self.assertRaises(installer.SetupError) as error:
            dict(installer.uninstall_steps())["polkit"]()
        self.assertEqual(error.exception.key, "trouble_dismissed")
        self.assertTrue(self.generated.exists())
        self.assertTrue(installer.has_polkit_rule())

    def test_when_it_fails_for_another_reason_it_says_so(self):
        self.generated.write_text("rule")
        with mock.patch.object(installer, "run", return_value=mock.Mock(returncode=1, stdout="", stderr="rm: something odd\n")), \
                self.assertRaises(installer.SetupError) as error:
            dict(installer.uninstall_steps())["polkit"]()
        self.assertEqual(error.exception.key, "trouble_polkit_left")


class PlainFailures(unittest.TestCase):
    """Whatever a tool says, the window gets a sentence, and the log gets what it said."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.log = self.root / "state" / "deskai" / "setup.log"
        self.patch(installer, "SETUP_LOG", self.log)
        self.patch(installer, "VENV", self.root / "venv")
        import deskai_setup
        self.strings = deskai_setup.STRINGS

    def patch(self, *args, **kwargs):
        patcher = mock.patch.object(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def check(self, step, key, said, **fields):
        """Runs a step that fails as it would for someone: through run_steps, for what the window and the log get."""
        reported = []
        self.assertFalse(installer.run_steps([("step", step)], lambda _id, state, error: reported.append(error) if state == "failed" else None))
        error = reported[0]
        self.assertEqual((error.key, error.fields), (key, fields))
        self.assertIn(said.strip(), self.log.read_text(), "the tool's own words stay in the log")
        for language in ("en", "id"):
            sentence = self.strings[language][key].format(**fields)
            for raw in RAW:
                self.assertNotIn(raw, sentence)
            self.assertNotIn(said.strip(), sentence)
        return error

    def test_installing_a_package(self):
        self.patch(installer, "package_manager", return_value="apt-get")
        self.patch(installer.shutil, "which", return_value="/usr/bin/pkexec")
        for code, said, key in ((100, APT_NOT_FOUND, "trouble_package_missing"), (100, APT_NO_CANDIDATE, "trouble_package_missing"),
                                (100, APT_NOT_ROOT, "trouble_no_permission"), (100, APT_BUSY, "trouble_package_busy"),
                                (100, APT_NO_ROOM, "trouble_no_room"), (100, APT_OFFLINE, "trouble_no_connection"),
                                (126, PKEXEC_CLOSED, "trouble_dismissed"), (127, PKEXEC_REFUSED, "trouble_not_allowed"),
                                (127, PKEXEC_NO_AGENT, "trouble_not_allowed")):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(code, stderr=said)):
                self.check(lambda: installer.install_package("gir1.2-atspi-2.0"), key, said)

    def test_a_package_that_failed_some_other_way(self):
        self.patch(installer, "package_manager", return_value="apt-get")
        self.patch(installer.shutil, "which", return_value="/usr/bin/pkexec")
        with mock.patch.object(installer, "run", return_value=failed(100, stderr=APT_DPKG_FAILED)):
            self.check(lambda: installer.install_package("at-spi2-core"), "trouble_package", APT_DPKG_FAILED, package="at-spi2-core")

    def test_a_tool_that_says_nothing(self):
        self.patch(installer, "package_manager", return_value="apt-get")
        self.patch(installer.shutil, "which", return_value="/usr/bin/pkexec")
        with mock.patch.object(installer, "run", return_value=failed(100)):
            self.check(lambda: installer.install_package("x"), "trouble_package", "exit code 100", package="x")

    def test_no_way_to_install_a_package(self):
        self.patch(installer, "package_manager", return_value="")
        self.check(lambda: installer.install_package("x"), "trouble_no_installer", "package manager: none", package="x")

    def test_the_api_key(self):
        self.check(lambda: installer._write_env("sk-ant-secret words"), "trouble_key_spaces", "the API key has spaces")
        self.assertNotIn("sk-ant-secret", self.log.read_text(), "the key itself is never written down")
        self.patch(installer, "has_api_key", return_value=False)
        step = dict(installer.install_steps(installer.Options(ai="api")))["ai"]
        self.check(step, "trouble_no_key", "no API key")

    def test_making_the_virtual_environment(self):
        for said, key in ((VENV_NOT_THERE, "trouble_not_there"), ("Error: Command returned non-zero exit status 1.\n", "trouble_venv")):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(1, stderr=said)):
                self.check(lambda: installer._make_venv("python3"), key, said)

    def test_it_says_why_on_stdout_when_python3_venv_is_missing(self):
        with mock.patch.object(installer, "run", return_value=failed(1, stdout=NO_VENV, stderr="")):
            self.check(lambda: installer._make_venv("python3"), "trouble_venv", NO_VENV)

    def test_downloading_the_anthropic_package(self):
        (self.root / "venv" / "bin").mkdir(parents=True)
        (self.root / "venv" / "bin" / "python").touch()
        for said, key in ((PIP_OFFLINE, "trouble_no_connection"), (PIP_NOTHING_THERE, "trouble_anthropic"),
                          (PIP_NO_ROOM, "trouble_no_room"), (PIP_NOT_ALLOWED, "trouble_no_permission")):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(1, stderr=said)):
                self.check(lambda: installer._make_venv("python3"), key, said)

    def test_downloading_the_local_model(self):
        self.patch(installer, "find_ollama", return_value="/usr/bin/ollama")
        for said, key, words, fields in ((OLLAMA_OFF, "trouble_ollama_off", "could not connect", {}),
                                         (OLLAMA_NO_MODEL, "trouble_model_missing", "file does not exist", {}),
                                         (OLLAMA_OFFLINE, "trouble_no_connection", "dial tcp", {}),
                                         (OLLAMA_BAD_DOWNLOAD, "trouble_model", "digest mismatch", {"model": "qwen3:1.7b"})):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(1, stderr=said)):
                self.check(lambda: installer._pull_ollama_model("qwen3:1.7b"), key, words, **fields)
        self.assertNotIn("\x1b", self.log.read_text(), "no progress bar in the log")

    def test_ollama_isnt_installed(self):
        self.patch(installer, "find_ollama", return_value="")
        self.check(lambda: installer._pull_ollama_model("qwen3:1.7b"), "trouble_no_ollama", "no ollama")

    def test_a_long_download_keeps_what_it_said_last(self):
        self.patch(installer, "find_ollama", return_value="/usr/bin/ollama")
        with mock.patch.object(installer, "run", return_value=failed(1, stderr=OLLAMA_BAR * 100000 + OLLAMA_OFF)):
            error = self.check(lambda: installer._pull_ollama_model("x"), "trouble_ollama_off", "could not connect")
        self.assertLessEqual(len(str(error)), installer.SAID_KEEPS)

    def test_starting_the_service(self):
        self.patch(installer, "UNIT", self.root / "deskaid.service")
        for said, key in ((NO_BUS, "trouble_no_session"), (UNIT_NOT_FOUND, "trouble_service"), (UNIT_FAILED, "trouble_service")):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(1, stderr=said)):
                self.check(lambda: installer._write_service(self.root, "python3"), key, said)

    def test_connecting_the_ai_agents(self):
        for said in (HOOKS_NOT_ALL, HOOKS_NOT_ALLOWED, HOOKS_CRASHED):
            with self.subTest(said), mock.patch.object(installer, "run", return_value=failed(1, stdout=said)):
                self.check(lambda: installer._hooks("install"), "trouble_hooks", said)
                self.check(lambda: installer._hooks("remove"), "trouble_hooks_left", said)

    def rest_of_the_steps(self, steps):
        """The real hooks step, among the rest of the real order, with those only noting that they ran."""
        ran = []
        steps = [(step_id, fn if step_id == "hooks" else mock.Mock(side_effect=lambda name=step_id: ran.append(name))) for step_id, fn in steps]
        return steps, ran

    def test_hooks_that_failed_dont_stop_an_install(self):
        steps, ran = self.rest_of_the_steps(installer.install_steps(installer.Options(hooks=True)))
        reported = []
        with mock.patch.object(installer, "run", return_value=failed(1, stdout=HOOKS_NOT_ALL)):
            self.assertTrue(installer.run_steps(steps, lambda step_id, state, error: reported.append((step_id, error.key)) if state == "failed" else None))
        self.assertEqual(reported, [("hooks", "trouble_hooks")])
        self.assertEqual(ran[-1], "finish", "the dock still turns on")
        self.assertIn("already has its own `notify`", self.log.read_text())

    def test_hooks_that_failed_dont_leave_deskai_half_removed(self):
        command = self.root / "deskai"
        command.touch()
        self.patch(installer, "COMMAND", command)
        steps, ran = self.rest_of_the_steps(installer.uninstall_steps())
        with mock.patch.object(installer, "run", return_value=failed(1, stdout=HOOKS_NOT_ALL)):
            self.assertTrue(installer.run_steps(steps, lambda *_: None))
        self.assertEqual(ran, ["service", "extension", "files", "polkit", "settings"])
        for name in ("LAUNCHER", "ICON", "EXTENSION_LINK", "LOGIN_LINK", "SHARE"):
            self.patch(installer, name, self.root / "gone" / name)
        dict(installer.uninstall_steps())["files"]()
        self.assertFalse(command.exists())
        self.assertIn("isn't valid JSON", self.log.read_text(), "what it said is still there to read")

    def test_an_agents_file_that_isnt_text_doesnt_stop_the_window_opening(self):
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        settings.write_bytes(b"\xff\xfe")
        self.patch(installer, "HOME", self.root)
        self.assertFalse(installer.hooks_installed())

    def test_hooks_are_only_there_if_a_command_runs_them(self):
        self.patch(installer, "HOME", self.root)
        settings = self.root / ".claude" / "settings.json"
        settings.parent.mkdir()
        codex = self.root / ".codex" / "config.toml"
        codex.parent.mkdir()
        for text in ('{"note": "deskai and a hook"}', '{"hooks": {"Stop": [{"command": "/home/you/deskai-tools/hook.sh"}]}}'):
            settings.write_text(text)
            self.assertFalse(installer.hooks_installed(), text)
        settings.write_text('{"hooks": {"Stop": [{"hooks": [{"command": "/home/you/.local/bin/deskai hook claude"}]}]}}')
        self.assertTrue(installer.hooks_installed())
        settings.unlink()
        codex.write_text('notify = ["/home/you/.local/bin/deskai", "hook", "codex"]  # DeskAI\n')
        self.assertTrue(installer.hooks_installed())

    def test_only_the_extras_can_fail_without_stopping_the_rest(self):
        self.assertEqual(installer.CAN_FAIL, {"hooks", "ollama"})

    def test_what_python_says(self):
        for said, key in ((PermissionError(13, "Permission denied", "/home/you/.config"), "trouble_no_permission"),
                          (PermissionError(13, "Izin ditolak", "/home/you/.config"), "trouble_no_permission"),  # GTK sets the language
                          (OSError(1, "Operation not permitted"), "trouble_no_permission"),
                          (OSError(30, "Read-only file system", "/home/you"), "trouble_no_permission"),
                          (OSError(28, "No space left on device"), "trouble_no_room"),
                          (OSError(28, "Tidak ada ruang"), "trouble_no_room"),
                          (OSError(122, "Disk quota exceeded"), "trouble_no_room"),
                          (FileNotFoundError(2, "No such file or directory", "systemctl"), "trouble_not_there"),
                          (shutil.Error([("/a", "/b", "[Errno 28] No space left on device: '/b'")]), "trouble_no_room"),
                          (shutil.Error([("/a", "/b", "[Errno 13] Permission denied: '/a'")]), "trouble_no_permission"),
                          (subprocess.TimeoutExpired(["ollama", "pull", "x"], 3600), "trouble_too_long"),
                          (ValueError("Expecting value: line 1 column 1 (char 0)"), "trouble_unknown"),
                          (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"), "trouble_unknown"),
                          (KeyError("a bug"), "trouble_unknown"),
                          (installer.GLib.Error("the settings are gone"), "trouble_unknown")):
            def step(error=said):
                raise error
            with self.subTest(said):
                self.check(step, key, str(said))

    def test_a_bug_is_written_down_with_where_it_happened(self):
        def step():
            raise KeyError("a bug")
        self.check(step, "trouble_unknown", "a bug")
        self.assertIn("Traceback", self.log.read_text())

    def test_removing_gets_as_far_as_it_can(self):
        ran, failed_steps = [], []

        def broken():
            raise OSError(13, "Permission denied", "/home/you/x")
        steps = [("extension", broken), ("hooks", lambda: ran.append("hooks")), ("service", broken), ("files", lambda: ran.append("files"))]
        self.assertFalse(installer.run_steps(steps, lambda step_id, state, _error: failed_steps.append(step_id) if state == "failed" else None, keep_going=True))
        self.assertEqual(ran, ["hooks", "files"])
        self.assertEqual(failed_steps, ["extension", "service"])

    def test_it_says_when_all_of_it_worked(self):
        self.assertTrue(installer.run_steps([("a", lambda: None), ("b", lambda: None)], lambda *_: None, keep_going=True))

    def test_the_terminal_removes_as_much_too(self):
        def step():
            raise OSError(13, "Permission denied", "/home/you/x")
        ran = []
        with mock.patch.object(installer, "uninstall_steps", return_value=[("extension", step), ("files", lambda: ran.append("files"))]), \
                mock.patch.object(sys, "argv", ["installer.py", "--uninstall"]), redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as exit_code:
            installer.main()
        self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(ran, ["files"])

    def test_an_update_with_no_connection_keeps_the_sdk_it_has(self):
        venv = self.root / "venv"
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").touch()
        self.patch(installer, "VENV", venv)

        def offline(*args, **_kwargs):
            return failed(1, stderr="Could not resolve host") if "install" in args else failed(0)
        with mock.patch.object(installer, "run", side_effect=offline):
            installer._make_venv("python3")

        def no_sdk(*args, **_kwargs):
            return failed(1, stderr="Could not resolve host") if "install" in args else failed(1)
        with mock.patch.object(installer, "run", side_effect=no_sdk), self.assertRaises(installer.SetupError):
            installer._make_venv("python3")

    def test_a_model_thats_here_isnt_downloaded_again(self):
        self.patch(installer, "find_ollama", lambda: "ollama")
        with mock.patch.object(installer, "run", return_value=mock.Mock(returncode=0, stdout="NAME\nsmall:1b  abc  1 GB\n", stderr="")) as run:
            installer._pull_ollama_model("small:1b")
        self.assertEqual([call.args[1] for call in run.call_args_list], ["list"])

    def test_ollama_being_off_doesnt_stop_an_install(self):
        self.assertIn("ollama", installer.CAN_FAIL)

    def test_it_stops_at_the_first_failure(self):
        ran = []

        def broken():
            raise OSError(28, "No space left on device")
        steps = [("a", lambda: ran.append("a")), ("b", broken), ("c", lambda: ran.append("c"))]
        self.assertFalse(installer.run_steps(steps, lambda *_: None))
        self.assertEqual(ran, ["a"])
        self.assertIn(" b trouble_no_room\n", self.log.read_text())

    def test_only_you_can_read_the_log(self):
        installer.report(OSError(28, "No space left on device"))
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)
        self.log.chmod(0o644)
        installer.report(OSError(28, "No space left on device"))
        self.assertEqual(self.log.stat().st_mode & 0o777, 0o600)

    def test_the_log_stays_small_and_keeps_the_latest(self):
        for n in range(40):
            installer.report(installer.SetupError("trouble_unknown", f"attempt {n}: " + "x" * installer.SAID_KEEPS))
        text = self.log.read_text()
        self.assertLessEqual(len(text), installer.LOG_KEEPS)
        self.assertIn("attempt 39", text)
        self.assertNotIn("attempt 0:", text)

    def test_a_file_name_that_isnt_text_doesnt_cost_the_log(self):
        error = installer.report(FileNotFoundError(2, "No such file or directory", "/home/you/caf\udce9"))
        self.assertTrue(error.logged)
        self.assertIn("/home/you/caf", self.log.read_text())

    def test_when_the_log_cant_be_written_the_words_go_to_stderr(self):
        with redirect_stderr(io.StringIO()) as stderr, mock.patch.object(installer, "_write_private", side_effect=OSError(28, "No space left on device")):
            error = installer.report(OSError(28, "No space left on device"))
        self.assertEqual(error.key, "trouble_no_room")
        self.assertFalse(error.logged)
        self.assertIn("No space left on device", stderr.getvalue())

    def test_where_the_log_is_doesnt_show_your_username(self):
        self.patch(installer, "SETUP_LOG", installer.HOME / ".local/state/deskai/setup.log")
        self.assertEqual(installer.log_path(), "~/.local/state/deskai/setup.log")
        self.patch(installer, "SETUP_LOG", Path("/somewhere/else/setup.log"))
        self.assertEqual(installer.log_path(), "/somewhere/else/setup.log")

    def test_removing_deskai_removes_the_log(self):
        installer.report(OSError(28, "No space left on device"))
        self.patch(installer, "COMMAND", self.root / "deskai")
        dict(installer.uninstall_steps())["hooks"]()
        self.assertFalse(self.log.exists())

    def test_the_terminal_gets_the_tools_own_words(self):
        def step():
            raise installer._tool_failed("trouble_service", failed(1, stderr=UNIT_NOT_FOUND))
        with mock.patch.object(installer, "uninstall_steps", return_value=[("service", step)]), \
                mock.patch.object(sys, "argv", ["installer.py", "--uninstall"]), redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit) as exit_code:
            installer.main()
        self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(out.getvalue(), f"✗ service: {UNIT_NOT_FOUND}")

    def test_the_terminal_says_when_something_didnt_get_done_in_a_developer_install_too(self):
        def step():
            raise installer.SetupError("trouble_hooks", HOOKS_NOT_ALLOWED.strip())
        with mock.patch.object(installer, "install_steps", return_value=[("hooks", step), ("finish", lambda: None)]), \
                mock.patch.object(sys, "argv", ["installer.py", "--dev"]), redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit) as exit_code:
            installer.main()
        self.assertEqual(exit_code.exception.code, 1)
        self.assertIn("Log out and back in", out.getvalue())

    def test_the_terminal_says_when_something_didnt_get_done(self):
        def step():
            raise installer.SetupError("trouble_hooks_left", HOOKS_NOT_ALLOWED.strip())
        with mock.patch.object(installer, "uninstall_steps", return_value=[("hooks", step), ("files", lambda: None)]), \
                mock.patch.object(sys, "argv", ["installer.py", "--uninstall"]), redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(SystemExit) as exit_code:
            installer.main()
        self.assertEqual(exit_code.exception.code, 1)
        self.assertIn("✓ files", out.getvalue())


class ToolsLanguage(unittest.TestCase):
    """What a tool says has to be in the words TROUBLES knows, whatever language the desktop is in."""
    SHOW = "import os; print(os.environ['LC_ALL'], os.environ.get('MINE', ''))"

    def test_its_own(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "it_IT.UTF-8", "LANGUAGE": "it"}):
            self.assertEqual(installer.run(sys.executable, "-c", self.SHOW).stdout.split(), ["C"])

    def test_with_a_chosen_environment_too(self):
        said = installer.run(sys.executable, "-c", self.SHOW, env={**os.environ, "MINE": "here"}).stdout.split()
        self.assertEqual(said, ["C", "here"])


class WindowSaysIt(unittest.TestCase):
    """The window itself, with no display: what ends up on the labels and toasts, in the language chosen."""

    def setUp(self):
        import deskai_setup
        self.setup_window = deskai_setup
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patches = [mock.patch.object(installer, "SETUP_LOG", Path(folder.name) / "setup.log"),
                   mock.patch.object(deskai_setup.GLib, "idle_add", side_effect=lambda fn, *args: fn(*args)),
                   mock.patch.object(deskai_setup.threading, "Thread", side_effect=lambda target, daemon: types.SimpleNamespace(start=target)),
                   mock.patch.object(deskai_setup, "Adw"), mock.patch.object(deskai_setup, "Gtk"), mock.patch.object(deskai_setup, "new_spinner")]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = mock.MagicMock()
        self.window.lang = "id"
        self.window.t = lambda key, **params: deskai_setup.SetupWindow.t(self.window, key, **params)
        self.window.trouble = lambda error: deskai_setup.SetupWindow.trouble(self.window, error)
        self.window.log_note = lambda error: deskai_setup.SetupWindow.log_note(self.window, error)
        self.window.why = lambda error: deskai_setup.SetupWindow.why(self.window, error)
        self.window.done_text = lambda key: deskai_setup.SetupWindow.done_text(self.window, key)
        self.window.work_list.get_first_child.return_value = None

    def work(self, fn):
        self.setup_window.SetupWindow.run_work(self.window, [("service", fn)], "title", mock.Mock(), mock.Mock())

    def test_a_failed_step(self):
        def step():
            raise installer._tool_failed("trouble_service", failed(1, stderr=UNIT_FAILED))
        self.work(step)
        self.window.work_error.set_label.assert_called_once_with("Layanan latar DeskAI nggak mau nyala.")
        said = self.window.work_log.set_label.call_args.args[0]
        self.assertEqual(said, f"Detail buat laporan bug ada di {installer.log_path()}.")
        self.window.work_log.set_visible.assert_called_with(True)
        self.window.work_title.set_label.assert_called_with("Ada yang gagal")

    def test_it_doesnt_point_at_a_log_it_couldnt_write(self):
        def step():
            raise OSError(28, "No space left on device")
        with mock.patch.object(installer, "_write_private", side_effect=OSError(28, "No space left on device")), redirect_stderr(io.StringIO()):
            self.work(step)
        self.window.work_error.set_label.assert_called_once_with("Ruang disknya nggak cukup. Kosongin dulu, terus coba lagi.")
        self.window.work_log.set_visible.assert_called_with(False)

    def extras_failed(self, action, finished):
        """Runs the window with a hooks step that fails, and then finishes the way it would."""
        def step():
            with mock.patch.object(installer, "run", return_value=failed(1, stdout=HOOKS_NOT_ALL)):
                installer._hooks(action)
        self.setup_window.SetupWindow.run_work(self.window, [("hooks", step)], "title", lambda: finished(self.window), mock.Mock())

    def test_the_done_page_says_what_the_extras_didnt_do(self):
        self.extras_failed("install", self.setup_window.SetupWindow.install_finished)
        said = self.window.done_status.set_description.call_args.args[0]
        self.assertTrue(said.startswith("Logout terus login lagi"), said)
        self.assertIn("Sebagian AI agent mungkin nggak ngabarin ke dock", said)
        self.assertIn(installer.log_path(), said)
        self.window.show_page.assert_called_with("done")

    def test_so_does_the_one_after_removing(self):
        with mock.patch.object(installer, "is_system_payload", return_value=False):
            self.extras_failed("remove", self.setup_window.SetupWindow.remove_finished)
        said = self.window.done_status.set_description.call_args.args[0]
        self.assertTrue(said.startswith("Dock-nya udah mati"), said)
        self.assertIn("Hook DeskAI mungkin masih ada", said)

    def test_a_done_page_with_nothing_to_add(self):
        self.setup_window.SetupWindow.run_work(self.window, [("hooks", lambda: None)], "title",
                                               lambda: self.setup_window.SetupWindow.install_finished(self.window), mock.Mock())
        self.window.done_status.set_description.assert_called_once_with(self.setup_window.STRINGS["id"]["done_text"])

    def test_it_doesnt_point_at_a_log_thats_been_deleted(self):
        def step():
            raise installer.SetupError("trouble_hooks_left", "said")
        self.setup_window.SetupWindow.run_work(self.window, [("hooks", step), ("settings", installer.SETUP_LOG.unlink)], "title",
                                               lambda: self.setup_window.SetupWindow.remove_finished(self.window), mock.Mock())
        said = self.window.done_status.set_description.call_args.args[0]
        self.assertIn("Hook DeskAI mungkin masih ada", said)
        self.assertNotIn(installer.log_path(), said)

    def install_package(self, **how):
        row = mock.Mock()
        self.window.check_rows = {"atspi": (row, mock.Mock())}
        with mock.patch.object(installer, "install_package", **how):
            self.setup_window.SetupWindow.on_install_package(self.window, mock.Mock(), "atspi", "x")
        self.window.run_checks.assert_called_once_with()
        self.setup_window.Adw.Toast.assert_not_called()  # one line only, and it goes away
        return row

    def test_a_package_that_wouldnt_install(self):
        row = self.install_package(side_effect=installer._tool_failed("trouble_package", failed(100, stderr=APT_BUSY), package="x"))
        row.set_subtitle.assert_called_once_with("Ada program lain yang lagi masang software. Tunggu sampai selesai, terus coba lagi. "
                                                 f"Detail buat laporan bug ada di {installer.log_path()}.")

    def test_a_package_row_doesnt_point_at_a_log_it_couldnt_write(self):
        with mock.patch.object(installer, "_write_private", side_effect=OSError(28, "No space left on device")), redirect_stderr(io.StringIO()):
            row = self.install_package(side_effect=OSError(28, "No space left on device"))
        row.set_subtitle.assert_called_once_with("Ruang disknya nggak cukup. Kosongin dulu, terus coba lagi.")

    def test_a_package_that_installed_on_the_second_try(self):
        row = self.install_package(return_value=None)
        row.set_subtitle.assert_called_once_with("Baca tab terminal, biar tiap tab balik lagi setelah logout")

    def test_a_check_that_blew_up(self):
        self.window.check_rows = {}
        self.window.page = "check"
        self.setup_window.in_background(lambda: {}["bug"], lambda result, error: self.setup_window.SetupWindow.show_checks(self.window, result, error))
        self.setup_window.Adw.Toast.assert_called_once_with(title="Ini masalah yang DeskAI nggak kenal.", timeout=8)

    def test_a_bug_doesnt_leave_the_window_waiting(self):
        finished = mock.Mock(return_value=None)
        self.setup_window.in_background(lambda: {}["bug"], finished)
        result, error = finished.call_args.args
        self.assertIsNone(result)
        self.assertEqual(error.key, "trouble_unknown")

    def test_it_all_went_well(self):
        finished = mock.Mock(return_value=None)
        self.setup_window.in_background(lambda: "done", finished)
        finished.assert_called_once_with("done", None)


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
