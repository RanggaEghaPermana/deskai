"""Choosing the AI, and carrying on when Claude stops working. Run with: python3 -m unittest discover tests"""

import io
import json
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import brain  # noqa: E402

SCHEMA = {"type": "object"}


def settings(backend="auto"):
    return {**brain.LLM_DEFAULTS, "backend": backend}


class Fixture(unittest.TestCase):
    """A computer where each of the three can be there or not."""

    def setUp(self):
        self.signed_in, self.cli, self.models = True, True, []
        for target, kwargs in (
                ((brain.shutil, "which"), {"side_effect": lambda _name: "/bin/claude" if self.cli else None}),
                ((brain.LLM, "_cli_signed_in"), {"side_effect": lambda: self.signed_in}),
                ((brain.LLM, "_ollama_models"), {"side_effect": lambda: self.models})):
            patcher = mock.patch.object(*target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(brain.os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        brain.os.environ.pop("ANTHROPIC_API_KEY", None)
        brain.os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)


class Picking(Fixture):
    def test_auto_prefers_claude_code(self):
        self.models = ["qwen3:1.7b"]
        self.assertEqual(brain.LLM(settings()).backend, "claude-cli")

    def test_auto_skips_a_claude_code_nobody_signed_in_to(self):
        self.signed_in, self.models = False, ["qwen3:1.7b"]
        self.assertEqual(brain.LLM(settings()).backend, "ollama")

    def test_auto_needs_the_model_downloaded(self):
        self.cli, self.models = False, ["something-else:1b"]
        self.assertEqual(brain.LLM(settings()).backend, "none")

    def test_auto_with_nothing(self):
        self.cli, self.models = False, None
        self.assertEqual(brain.LLM(settings()).backend, "none")

    def test_asking_for_ollama_gets_it_even_before_it_starts(self):
        self.models = None
        self.assertEqual(brain.LLM(settings("ollama")).backend, "ollama")

    def test_nothing_is_picked_when_its_off(self):
        self.models = ["qwen3:1.7b"]
        self.assertEqual(brain.LLM(settings("none")).backend, "none")

    def test_auto_notices_ollama_starting_later(self):
        self.cli, self.models = False, None
        llm = brain.LLM(settings())
        self.assertFalse(llm.available)
        self.models = ["qwen3:1.7b"]
        with mock.patch.object(brain.time, "monotonic", return_value=brain.time.monotonic() + brain.LOOK_AGAIN_NONE_S + 1):
            self.assertTrue(llm.available)
        self.assertEqual(llm.backend, "ollama")


class Fallback(Fixture):
    def cli_says(self, text):
        return mock.Mock(stdout=json.dumps({"is_error": True, "result": text}), stderr="")

    def test_claude_stopping_moves_to_the_local_model_and_later_back(self):
        self.models = ["qwen3:1.7b"]
        llm = brain.LLM(settings())
        with mock.patch.object(brain.subprocess, "run", return_value=self.cli_says("Not logged in · Please run /login")), \
                mock.patch.object(brain.LLM, "_ask_ollama", return_value={"ok": 1}) as local:
            self.assertEqual(llm.ask("s", "p", SCHEMA), {"ok": 1})
            self.assertEqual(llm.backend, "ollama")
            self.assertEqual(llm.ask("s", "p", SCHEMA), {"ok": 1})
            self.assertEqual(local.call_count, 2)
        with mock.patch.object(brain.time, "monotonic", return_value=brain.time.monotonic() + brain.LOOK_AGAIN_LOCAL_S + 1):
            self.assertTrue(llm.available)
        self.assertEqual(llm.backend, "claude-cli")

    def test_a_usage_limit_counts_too(self):
        self.models = ["qwen3:1.7b"]
        llm = brain.LLM(settings())
        with mock.patch.object(brain.subprocess, "run", return_value=self.cli_says("Claude usage limit reached")), \
                mock.patch.object(brain.LLM, "_ask_ollama", return_value={"ok": 1}):
            llm.ask("s", "p", SCHEMA)
        self.assertEqual(llm.backend, "ollama")

    def test_with_no_local_model_the_error_says_what_to_do(self):
        llm = brain.LLM(settings())
        with mock.patch.object(brain.subprocess, "run", return_value=self.cli_says("Not logged in")):
            with self.assertRaises(brain.LLMError) as caught:
                llm.ask("s", "p", SCHEMA)
        self.assertEqual(caught.exception.hint, "llm_claude_stopped")
        self.assertEqual(llm.backend, "claude-cli")

    def test_other_errors_dont_move_it(self):
        self.models = ["qwen3:1.7b"]
        llm = brain.LLM(settings())
        with mock.patch.object(brain.subprocess, "run", return_value=self.cli_says("something odd")):
            with self.assertRaises(brain.LLMError) as caught:
                llm.ask("s", "p", SCHEMA)
        self.assertIsNone(caught.exception.hint)
        self.assertEqual(llm.backend, "claude-cli")

    def test_choosing_claude_explicitly_stays_on_claude(self):
        self.models = ["qwen3:1.7b"]
        llm = brain.LLM(settings("claude-cli"))
        with mock.patch.object(brain.subprocess, "run", return_value=self.cli_says("Not logged in")):
            with self.assertRaises(brain.LLMError):
                llm.ask("s", "p", SCHEMA)
        self.assertEqual(llm.backend, "claude-cli")


class Ollama(Fixture):
    def setUp(self):
        super().setUp()
        self.models = ["qwen3:1.7b"]
        self.llm = brain.LLM(settings("ollama"))

    def test_the_request(self):
        reply = io.BytesIO(json.dumps({"message": {"content": '{"say": "hi"}'}}).encode())
        with mock.patch.object(brain.urllib.request, "urlopen", return_value=reply) as urlopen:
            self.assertEqual(self.llm.ask("sys", "prompt", SCHEMA, max_tokens=99), {"say": "hi"})
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/chat"))
        body = json.loads(request.data)
        self.assertEqual(body["model"], "qwen3:1.7b")
        self.assertEqual(body["format"], SCHEMA)
        self.assertFalse(body["stream"])
        self.assertFalse(body["think"])
        self.assertEqual(body["options"]["num_ctx"], brain.OLLAMA_CONTEXT)
        self.assertEqual(body["options"]["num_predict"], 99)
        self.assertEqual([m["role"] for m in body["messages"]], ["system", "user"])

    def test_not_running(self):
        with mock.patch.object(brain.urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(brain.LLMError) as caught:
                self.llm.ask("s", "p", SCHEMA)
        self.assertEqual(caught.exception.hint, "llm_ollama_down")

    def test_model_not_downloaded(self):
        error = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        with mock.patch.object(brain.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(brain.LLMError) as caught:
                self.llm.ask("s", "p", SCHEMA)
        self.assertEqual(caught.exception.hint, "llm_ollama_model")

    def test_still_downloading_isnt_a_problem_with_ollama(self):
        missing = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        self.addCleanup(missing.close)
        for error in (urllib.error.URLError("refused"), missing):
            with self.subTest(error), mock.patch.object(brain.localai, "percent", return_value=40), \
                    mock.patch.object(brain.urllib.request, "urlopen", side_effect=error), self.assertRaises(brain.LLMError) as caught:
                self.llm.ask("s", "p", SCHEMA)
            self.assertEqual((caught.exception.hint, caught.exception.params), ("llm_local_ai_getting", {"percent": 40}))

    def test_doctor_says_its_still_downloading(self):
        with mock.patch.object(brain.localai, "percent", return_value=40), mock.patch.object(self.llm, "_ollama_has_model", return_value=False):
            self.assertIn("40%", self.llm.problem())

    def test_no_fallback_loop(self):
        with mock.patch.object(brain.urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")) as urlopen:
            with self.assertRaises(brain.LLMError):
                self.llm.ask("s", "p", SCHEMA)
        self.assertEqual(urlopen.call_count, 1)


class Doctor(Fixture):
    def test_says_when_claude_is_signed_out(self):
        llm = brain.LLM(settings("claude-cli"))
        self.signed_in = False
        self.assertIn("signed in", llm.problem())

    def test_says_when_ollama_is_down_or_the_model_missing(self):
        self.models = None
        llm = brain.LLM(settings("ollama"))
        self.assertIn("isn't running", llm.problem())
        self.models = []
        self.assertIn("ollama pull qwen3:1.7b", llm.problem())
        self.models = ["qwen3:1.7b"]
        self.assertIsNone(llm.problem())

    def test_says_when_theres_nothing(self):
        self.cli, self.models = False, None
        self.assertIn("no AI found", brain.LLM(settings()).problem())


if __name__ == "__main__":
    unittest.main()
