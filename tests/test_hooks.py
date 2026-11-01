"""`deskai hook`, `deskai hooks` and what `deskai doctor` says about agents. There's no dock while the screen is locked, and
what agents say then has to wait for it. Their settings are their own files and can hold anything: Setup goes by what the
command says, and people send doctor's output with a bug. Run with: python3 -m unittest discover tests"""

import io
import json
import os
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import deskai  # noqa: E402
import hooks  # noqa: E402

NEEDS_YOU = json.dumps({"hook_event_name": "Notification", "notification_type": "permission_prompt", "session_id": "s1",
                        "cwd": "/w/app", "message": "Butuh izin kamu"})


def no_dock(_event):
    raise OSError("The name is not activatable")


class WithNoDock(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "state" / "inbox-waiting.jsonl"
        patcher = mock.patch.object(hooks, "WAITING_FILE", self.file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def waiting(self):
        return [json.loads(line) for line in self.file.read_text().splitlines()]

    def test_with_a_dock_nothing_waits(self):
        sent = []
        hooks.run("claude", NEEDS_YOU, sent.append)
        self.assertEqual(json.loads(sent[0])["event"], "attention")
        self.assertFalse(self.file.exists())

    def test_what_it_says_waits_for_the_dock(self):
        hooks.run("claude", NEEDS_YOU, no_dock)
        event = self.waiting()[0]
        self.assertEqual((event["source"], event["event"], event["session"], event["text"]), ("claude-code", "attention", "s1", "Butuh izin kamu"))
        self.assertGreater(event["at"], 0)
        self.assertTrue(event["pids"])

    def test_in_the_order_it_was_said(self):
        hooks.run("claude", NEEDS_YOU, no_dock)
        hooks.run("claude", json.dumps({"hook_event_name": "SessionEnd", "session_id": "s1"}), no_dock)
        self.assertEqual([event["event"] for event in self.waiting()], ["attention", "end"])

    def test_only_you_can_read_it(self):
        hooks.run("claude", NEEDS_YOU, no_dock)
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)

    def test_it_doesnt_grow_for_ever(self):
        self.file.parent.mkdir()
        self.file.write_text("x" * (hooks.WAITING_MAX_BYTES + 1))
        hooks.run("claude", NEEDS_YOU, no_dock)
        self.assertEqual(len(self.waiting()), 1)

    def test_something_that_says_nothing_doesnt_wait(self):
        hooks.run("claude", json.dumps({"hook_event_name": "Something"}), no_dock)
        self.assertFalse(self.file.exists())

    def test_nowhere_to_write_never_breaks_the_agent(self):
        with mock.patch.object(hooks, "WAITING_FILE", Path("/proc/nope/inbox-waiting.jsonl")):
            hooks.run("claude", NEEDS_YOU, no_dock)

    def test_it_prints_only_what_the_agent_needs(self):
        for agent, payload, printed in (("claude", NEEDS_YOU, ""), ("gemini", "{}", "{}\n")):
            out = io.StringIO()
            with redirect_stdout(out):
                hooks.run(agent, payload, no_dock)
            self.assertEqual(out.getvalue(), printed)


# What's in the file, and what has to be said about it. Valid JSON goes as far as the shape of what's in it.
BROKEN = (
    (b"{bad json", "isn't valid JSON (line 1, column 2)"),
    (b"[1, 2]", "isn't a JSON object"),
    (b'{"hooks": {"Stop": [1]}}', "isn't laid out the way DeskAI expects"),
    (b'{"hooks": {"Stop": [{"hooks": [1]}]}}', "isn't laid out the way DeskAI expects"),
    (b"\xff\xfe", "isn't text"),
)
PYTHON_TALKING = ("Traceback", "[Errno", "codec", "char 1", "AttributeError", "object has no attribute", "bukan")


class AgentFiles(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.claude = self.root / ".claude" / "settings.json"
        self.gemini = self.root / ".gemini" / "settings.json"
        self.codex = self.root / ".codex" / "config.toml"
        for path in (self.claude, self.gemini, self.codex):
            path.parent.mkdir()
        for name, value in (("CLAUDE_SETTINGS", self.claude), ("GEMINI_SETTINGS", self.gemini), ("CODEX_CONFIG", self.codex)):
            patcher = mock.patch.object(hooks, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def command(self, action):
        """(exit code, what it printed), from the command itself."""
        with redirect_stdout(io.StringIO()) as out:
            try:
                deskai.cmd_hooks(types.SimpleNamespace(only=None, action=action))
            except SystemExit as e:
                return e.code, out.getvalue()
        return 0, out.getvalue()

    def doctor(self):
        with redirect_stdout(io.StringIO()) as out:
            deskai.print_agents()
        return out.getvalue()

    def assert_plain(self, said):
        for words in PYTHON_TALKING:
            self.assertNotIn(words, said)

    def test_broken_settings_are_said_by_everything_that_reads_them(self):
        for content, why in BROKEN:
            with self.subTest(content):
                self.claude.write_bytes(content)
                for action, line in (("status", "can't tell"), ("install", "failed"), ("remove", "failed")):
                    code, said = self.command(action)
                    self.assertEqual(code, 1, action)
                    self.assertIn(f"Claude Code  {line}: {self.claude} {why}", said)
                    self.assert_plain(said)
                said = self.doctor()
                self.assertIn(f"✗ Can't tell whether Claude Code reports to the dock\n    → {self.claude} {why}\n", said)
                self.assert_plain(said)
                self.assertEqual(self.claude.read_bytes(), content, "a file it can't use is left as it is")
                self.assertFalse(self.claude.with_name(self.claude.name + hooks.BACKUP_SUFFIX).exists())

    def test_the_other_agents_still_get_done(self):
        for content, _why in BROKEN:
            with self.subTest(content):
                self.claude.write_bytes(content)
                self.gemini.write_text("{}")
                self.assertIn(f"Gemini CLI   installed  ({hooks.AGENTS['gemini'][5]})", self.command("install")[1])
                self.assertIn("deskai", self.gemini.read_text())
                self.assertEqual(self.claude.read_bytes(), content)
                self.assertIn("✓ Gemini CLI tells the dock", self.doctor())
                self.assertIn(f"Gemini CLI   removed  ({hooks.AGENTS['gemini'][5]})", self.command("remove")[1])
                self.assertNotIn("deskai", self.gemini.read_text())

    def test_codex_too(self):
        self.codex.write_bytes(b"\xff\xfe")
        for action, line in (("status", "can't tell"), ("install", "failed"), ("remove", "failed")):
            code, said = self.command(action)
            self.assertEqual(code, 1, action)
            self.assertIn(f"Codex        {line}: {self.codex} isn't text", said)
        self.assertIn(f"    → {self.codex} isn't text\n", self.doctor())

    def test_codex_with_its_own_notify(self):
        self.codex.write_text('notify = ["my-own-notifier"]\n')
        code, said = self.command("install")
        self.assertEqual(code, 1)
        self.assertIn("already has its own `notify`", said)
        self.assertIn("my-own-notifier", self.codex.read_text())
        self.assertEqual(self.command("remove")[0], 0, "nothing of DeskAI's to take out")

    @unittest.skipIf(os.geteuid() == 0, "root can read anything")
    def test_a_file_you_cant_read(self):
        self.claude.write_text("{}")
        self.claude.chmod(0)
        self.addCleanup(self.claude.chmod, 0o600)
        for action, line in (("status", "can't tell"), ("install", "failed"), ("remove", "failed")):
            code, said = self.command(action)
            self.assertEqual(code, 1, action)
            self.assertIn(f"Claude Code  {line}: {self.claude}: Permission denied", said)
            self.assert_plain(said)
        self.assertIn(f"    → {self.claude}: Permission denied\n", self.doctor())

    def test_a_folder_only_root_can_read_on_python_3_12(self):
        # 3.12 raises here, and 3.14 says the file isn't there.
        self.claude.write_text("{}")
        with mock.patch.object(Path, "exists", side_effect=PermissionError(13, "Permission denied", str(self.claude))):
            said = self.command("status")[1]
        self.assertIn(f"{self.claude}: Permission denied", said)
        self.assert_plain(said)

    def test_writing_that_fails_is_said_in_words_too(self):
        self.claude.write_text("{}")
        with mock.patch.object(hooks.brain, "write_json_atomic", side_effect=PermissionError(13, "Permission denied", str(self.claude))):
            code, said = self.command("install")
        self.assertEqual(code, 1)
        self.assertIn(f"failed: {self.claude}: Permission denied", said)
        self.assert_plain(said)

    def test_all_is_well(self):
        self.claude.write_text('{"theme": "dark"}')
        self.gemini.write_text("{}")
        self.assertEqual(self.command("status")[0], 0)
        self.assertEqual(self.command("install")[0], 0)
        self.assertIn('"theme": "dark"', self.claude.read_text())
        said = self.doctor()
        self.assertIn("✓ Claude Code tells the dock", said)
        self.assertNotIn("✗", said)
        self.assertEqual(self.command("remove")[0], 0)
        self.assertNotIn("deskai", self.claude.read_text())

    def install_and_remove(self):
        self.assertEqual(self.command("install")[0], 0)
        self.assertEqual(self.command("remove")[0], 0)

    def backups(self):
        return list(self.root.rglob(f"*{hooks.BACKUP_SUFFIX}"))

    def test_removing_puts_a_file_back_as_it_was(self):
        original = '{"theme":   "dark",\n    "list": [1, 2, 3], "name": "caf\\u00e9"}\n\n'
        self.claude.write_text(original)
        self.claude.chmod(0o644)
        self.install_and_remove()
        self.assertEqual(self.claude.read_text(), original)
        self.assertEqual(self.claude.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.backups(), [])

    def test_your_other_changes_stay(self):
        self.claude.write_text('{"theme": "dark"}')
        self.assertEqual(self.command("install")[0], 0)
        settings = json.loads(self.claude.read_text())
        settings["theme"] = "light"
        self.claude.write_text(json.dumps(settings))
        self.assertEqual(self.command("remove")[0], 0)
        self.assertEqual(json.loads(self.claude.read_text()), {"theme": "light"})
        self.assertEqual(len(self.backups()), 1, "it may have changed by something other than you, so the copy stays")

    def test_a_file_that_wasnt_there_goes_again(self):
        self.install_and_remove()
        self.assertFalse(self.claude.exists())

    def test_a_link_stays_a_link(self):
        real = self.root / "dotfiles.json"
        real.write_text('{"theme":   "dark"}')
        real.chmod(0o640)
        self.claude.symlink_to(real)
        self.assertEqual(self.command("install")[0], 0)
        self.assertTrue(self.claude.is_symlink())
        self.assertIn("deskai", real.read_text())
        self.assertEqual(real.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.command("remove")[0], 0)
        self.assertTrue(self.claude.is_symlink())
        self.assertEqual(real.read_text(), '{"theme":   "dark"}')
        self.assertEqual(real.stat().st_mode & 0o777, 0o640)

    def test_codex_without_a_newline_at_the_end(self):
        self.codex.write_text('model = "o3"')
        self.assertEqual(self.command("install")[0], 0)
        lines = self.codex.read_text().splitlines()
        self.assertEqual(lines[0], 'model = "o3"')
        self.assertTrue(lines[1].startswith("notify = "), lines)
        self.assertEqual(self.command("remove")[0], 0)
        self.assertEqual(self.codex.read_text(), 'model = "o3"')

    def test_a_codex_file_an_older_version_broke_is_put_right(self):
        command = '["/home/you/.local/bin/deskai", "hook", "codex"]'
        for broken, fixed in ((f'model = "o3"notify = {command}  # DeskAI\n', 'model = "o3"'),
                              (f'a = 1\nmodel = "o3"notify = {command}  # DeskAI\n', 'a = 1\nmodel = "o3"')):
            with self.subTest(broken):
                self.codex.write_text(broken)
                self.assertTrue(hooks._codex_installed())
                self.assertEqual(self.command("remove")[0], 0)
                self.assertEqual(self.codex.read_text().strip(), fixed)
        self.codex.write_text(f'model = "o3"notify = {command}  # DeskAI\n')
        self.assertEqual(self.command("install")[0], 0)
        lines = self.codex.read_text().splitlines()
        self.assertEqual(lines[0], 'model = "o3"')
        self.assertTrue(lines[1].startswith("notify = "))
        self.assertEqual(len(lines), 2)

    def test_a_notify_of_your_own_on_a_line_of_its_own_isnt_touched(self):
        self.codex.write_text('notify = ["mine"]\nmodel = "o3"\n')
        self.assertFalse(hooks._codex_installed())

    def test_codex_comes_back_as_it_was(self):
        for original in ('model = "o3"\n\n[tools]\nx = 1\n', "[tools]\nx = 1\n", ""):
            with self.subTest(original):
                self.codex.write_text(original)
                self.install_and_remove()
                self.assertEqual(self.codex.read_text(), original)
                self.assertEqual(self.backups(), [])

    def test_a_notify_of_your_own_with_the_word_in_it(self):
        self.codex.write_text('notify = ["/home/you/deskai-tools/notify"]\n')
        self.assertFalse(hooks._codex_installed())
        code, said = self.command("install")
        self.assertEqual(code, 1)
        self.assertIn("already has its own `notify`", said)
        self.assertEqual(self.command("remove")[0], 0)
        self.assertIn("deskai-tools", self.codex.read_text())

    def test_an_empty_file_is_as_good_as_none(self):
        self.claude.write_text("")
        self.assertEqual(self.command("status")[0], 0)
        self.assertEqual(self.command("install")[0], 0)
        self.assertIn("deskai", self.claude.read_text())

    def test_what_an_error_says(self):
        self.assertEqual(hooks.said(PermissionError(13, "Permission denied", "/a")), "/a: Permission denied")
        self.assertEqual(hooks.said(OSError(28, "No space left on device")), "No space left on device")
        self.assertEqual(hooks.said(ValueError("in words")), "in words")
        self.assertEqual(hooks.said(OSError("no number")), "no number")


if __name__ == "__main__":
    unittest.main()
