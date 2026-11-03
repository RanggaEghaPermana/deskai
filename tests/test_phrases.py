"""What your own words mean, once you've said yes to the AI's guess. Run with: python3 -m unittest discover tests"""

import argparse
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import phrases  # noqa: E402

MODE = [{"type": "mode", "mode": "building"}]
SETUP = [{"type": "setup_project", "project": "toko-online"}]


class Phrases(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "phrases.json"
        self.known = phrases.Phrases(self.path)

    def test_what_you_said_yes_to_comes_back(self):
        self.known.remember("gas kerja", MODE)
        self.assertEqual(phrases.Phrases(self.path).recall("gas kerja"), MODE)

    def test_nothing_for_words_never_taught(self):
        self.assertIsNone(self.known.recall("gas kerja"))

    def test_only_you_can_read_them(self):
        self.known.remember("gas kerja", MODE)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_saying_it_again_means_the_latest(self):
        self.known.remember("gas kerja", MODE)
        self.known.remember("gas kerja", SETUP)
        self.assertEqual(self.known.recall("gas kerja"), SETUP)

    def test_forgetting(self):
        self.known.remember("gas kerja", MODE)
        self.assertTrue(self.known.forget("gas kerja"))
        self.assertIsNone(phrases.Phrases(self.path).recall("gas kerja"))
        self.assertFalse(self.known.forget("gas kerja"))

    def test_forgetting_from_the_command_line_reaches_the_daemon(self):
        self.known.remember("gas kerja", MODE)
        phrases.Phrases(self.path).forget("gas kerja")  # `deskai phrases --forget`, in a process of its own
        self.assertIsNone(self.known.recall("gas kerja"))

    def test_the_most_recently_used_first(self):
        with mock.patch.object(phrases.time, "time", side_effect=[1, 2, 3, 4, 5, 6]):
            self.known.remember("satu", MODE)
            self.known.remember("dua", SETUP)
            self.known.recall("satu")
        self.assertEqual([said for said, _actions in self.known.listing()], ["satu", "dua"])

    def test_it_keeps_the_ones_used_lately(self):
        with mock.patch.object(phrases, "KEEP", 2):
            for at, said in enumerate(("satu", "dua", "tiga")):
                with mock.patch.object(phrases.time, "time", return_value=at):
                    self.known.remember(said, MODE)
        self.assertEqual(sorted(said for said, _actions in self.known.listing()), ["dua", "tiga"])

    def test_a_paragraph_isnt_kept(self):
        self.known.remember("kata " * 60, MODE)
        self.assertEqual(self.known.listing(), [])

    def test_nor_a_guess_of_nothing(self):
        self.known.remember("gas kerja", [])
        self.assertEqual(self.known.listing(), [])

    def test_a_broken_file_takes_nothing_with_it(self):
        self.known.remember("gas kerja", MODE)
        self.path.write_text("{")
        self.assertEqual(self.known.recall("gas kerja"), MODE)

    def test_what_isnt_a_request_is_left_out(self):
        self.path.write_text(json.dumps({"ok": {"actions": MODE}, "no actions": {}, "odd": {"actions": ["mode"]}, "typed": {"actions": [{"mode": "x"}]}}))
        self.assertEqual([said for said, _actions in phrases.Phrases(self.path).listing()], ["ok"])

    def test_a_full_disk_still_does_it(self):
        self.known.remember("gas kerja", MODE)
        with mock.patch.object(phrases.brain, "write_json_atomic", side_effect=OSError(28, "No space left on device")):
            self.assertEqual(self.known.recall("gas kerja"), MODE)

    def test_the_latest_teach_a_model_by_example(self):
        with mock.patch.object(phrases, "AS_EXAMPLES", 2):
            for at, said in enumerate(("satu", "dua", "tiga")):
                with mock.patch.object(phrases.time, "time", return_value=at):
                    self.known.remember(said, MODE)
            examples = self.known.examples()
        self.assertEqual(examples.splitlines(), ['"tiga" -> [{"type": "mode", "mode": "building"}]', '"dua" -> [{"type": "mode", "mode": "building"}]'])

    def test_nothing_taught_nothing_to_show(self):
        self.assertEqual(self.known.examples(), "")


if __name__ == "__main__":
    unittest.main()


class FromTheTerminal(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "phrases.json"
        import deskai
        self.deskai = deskai
        kept = phrases.Phrases  # the same module as deskai's, so its own name for it is about to change
        patcher = mock.patch.object(deskai.phrases, "Phrases", lambda path=None: kept(path or self.path))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_it(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            self.deskai.cmd_phrases(argparse.Namespace(forget=args[1] if args else None))
        return out.getvalue()

    def test_listing_them_as_you_typed_them(self):
        phrases.Phrases(self.path).remember("kerja", MODE + SETUP, "gas kerja")
        self.assertEqual(self.run_it(), "gas kerja  →  mode building, setup_project toko-online\n")

    def test_nothing_yet(self):
        self.assertIn("Nothing yet", self.run_it())

    def test_forgetting_however_its_typed(self):
        import commands
        phrases.Phrases(self.path).remember(commands.plain("gas ngoding"), MODE, "gas ngoding")
        self.assertIn("Forgot", self.run_it("--forget", "Gas  Ngoding!"))
        self.assertEqual(phrases.Phrases(self.path).listing(), [])

    def test_forgetting_what_isnt_there(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_it("--forget", "gas kerja")
        self.assertIn("Nothing kept", str(stopped.exception.code))


class TeachingFromTheCard(unittest.TestCase):
    def test_my_presets_already_open_takes_the_words_too(self):
        import presets_window
        app, window = mock.Mock(), mock.Mock()
        app.get_active_window.return_value = window
        line = mock.Mock(get_arguments=lambda: ["w.py", "--keyword", "gas kerja"])
        self.assertEqual(presets_window.PresetsApp.on_command_line(app, None, line), 0)
        window.new_preset.assert_called_once_with("gas kerja")
        window.present.assert_called_once_with()

    def test_my_presets_starts_a_new_one_with_your_words(self):
        import presets_window
        self.assertEqual(presets_window.keyword_given(["w.py", "--keyword", " gas kerja "]), ("gas kerja", ["w.py"]))
        self.assertEqual(presets_window.keyword_given(["w.py"]), ("", ["w.py"]))
        self.assertEqual(presets_window.keyword_given(["w.py", "--keyword"]), ("", ["w.py", "--keyword"]))
        self.assertEqual(len(presets_window.keyword_given(["w.py", "--keyword", "kata " * 40])[0]), presets_window.KEYWORD_GIVEN)
