"""`deskai hook`: what AI agents run to tell the dock they're done or need you. There's no dock while the screen
is locked, and what they say then has to wait for it. Run with: python3 -m unittest discover tests"""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

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


if __name__ == "__main__":
    unittest.main()
