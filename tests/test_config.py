"""modes.json: no mistake in it takes anything down, you hear about it, and the dock and the daemon go by the same
rules. Run with: python3 -m unittest discover tests"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import brain  # noqa: E402

EXTENSION = ROOT / "extension" / "deskai@ranggaeghapermana.github.io"
DEFAULTS = json.loads((EXTENSION / "modes.default.json").read_text())
# Everything the rules look at, so each of them gets tried.
FULL = {**DEFAULTS, "breaks": {"enabled": True, "after_minutes": 90}, "room_warnings": {"enabled": True},
        "llm": {**DEFAULTS["llm"], "ollama_url": "http://127.0.0.1:11434"},
        "dock": {**DEFAULTS["dock"], "icon_size": 38, "autohide": "windows"},
        "modes": {**DEFAULTS["modes"], "custom": {"label": "Custom", "description": "Mine", "icon": "x-symbolic", "color": "#abc", "focus": True,
                                                  "apps": ["a.desktop"], "allow": ["a"], "block": ["b"], "on_distraction": "minimize",
                                                  "nudge_after": 60, "renudge_after": 120, "exit": {"delay": 0, "per_press": 3},
                                                  "notifications": {"policy": "filter", "allow_apps": ["a"], "keywords": ["k"]}}}}
WRONG = (None, 0, -1, 90, 1.5, True, False, "", "90", "x", [], [1], ["x"], {}, {"a": 1})


def places(value, path=()):
    """The path of everything in a config."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield (*path, key)
            yield from places(inner, (*path, key))
    elif isinstance(value, list):
        for index, inner in enumerate(value):
            yield (*path, index)
            yield from places(inner, (*path, index))


def with_value(path, wrong):
    config = copy.deepcopy(FULL)
    inner = config
    for step in path[:-1]:
        inner = inner[step]
    inner[path[-1]] = wrong
    return config


def without(path):
    config = copy.deepcopy(FULL)
    inner = config
    for step in path[:-1]:
        inner = inner[step]
    del inner[path[-1]]
    return config


def every_mistake():
    yield from (None, 5, "x", [], {}, {"modes": None}, {"modes": []}, {"modes": {}}, {"modes": 5})
    for path in places(FULL):
        for wrong in WRONG:
            yield with_value(path, wrong)
        yield without(path)


class Mistakes(unittest.TestCase):
    def test_what_ships_is_fine(self):
        self.assertEqual(brain.validate_config(DEFAULTS), [])
        self.assertEqual(brain.validate_config(FULL), [])

    def test_no_mistake_makes_it_fall_over(self):
        for config in every_mistake():
            self.assertIsInstance(brain.validate_config(config), list)

    def test_what_it_says_about_them(self):
        for change, said in (({"dock": None}, "dock must be an object"), ({"llm": None}, "llm must be an object"),
                             ({"inbox": 5}, "inbox must be an object"), ({"breaks": []}, "breaks must be an object"),
                             ({"default_mode": []}, "default_mode"), ({"breaks": {"after_minutes": "90"}}, "breaks.after_minutes")):
            self.assertIn(said, "; ".join(brain.validate_config({**FULL, **change})), change)
        for change, said in (({"notifications": None}, "modes.custom.notifications must be an object"),
                             ({"exit": None}, "modes.custom.exit must be an object"), ({"icon": 5}, "modes.custom.icon")):
            config = {**FULL, "modes": {**FULL["modes"], "custom": {**FULL["modes"]["custom"], **change}}}
            self.assertIn(said, "; ".join(brain.validate_config(config)), change)

    @unittest.skipUnless(shutil.which("gjs"), "needs gjs")
    def test_the_dock_goes_by_the_same_rules(self):
        configs = list(every_mistake())
        with tempfile.TemporaryDirectory() as folder:
            given = Path(folder) / "configs.json"
            given.write_text(json.dumps(configs))
            script = Path(folder) / "check.js"
            script.write_text(f"""
                import GLib from 'gi://GLib';
                import {{isValidConfig}} from 'file://{EXTENSION / "config.js"}';
                const [, bytes] = GLib.file_get_contents('{given}');
                print(JSON.stringify(JSON.parse(new TextDecoder().decode(bytes)).map(isValidConfig)));
            """)
            done = subprocess.run(["gjs", "-m", str(script)], capture_output=True, text=True, timeout=60, check=True)
        for config, dock_takes_it in zip(configs, json.loads(done.stdout), strict=True):
            self.assertEqual(not brain.validate_config(config), dock_takes_it, json.dumps(config)[:300])


class Reading(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "modes.json"
        self.config = brain.Config(self.path)

    def write(self, value):
        if isinstance(value, bytes):
            self.path.write_bytes(value)
        else:
            self.path.write_text(value if isinstance(value, str) else json.dumps(value))

    def test_broken_from_the_start(self):
        self.write('{"modes": {\n  "a": }')
        self.assertFalse(self.config.refresh())
        self.assertEqual(self.config.problem, ("config_not_json", {"line": 2, "column": 8}))
        self.assertIn("line 2, column 8", self.config.problem_in_words("en"))
        self.assertIn("baris 2, kolom 8", self.config.problem_in_words("id"))
        self.assertEqual(self.config.data["modes"], {})

    def test_not_even_text(self):
        self.write(b"\xff\xfe\x00")
        self.assertFalse(self.config.refresh())
        self.assertEqual(self.config.problem, ("config_unreadable", {}))

    def test_a_wrong_value_keeps_the_last_good_copy_and_says_where(self):
        self.write(FULL)
        self.assertTrue(self.config.refresh())
        self.write({**FULL, "language": "id", "breaks": {"after_minutes": "90"}})
        self.assertFalse(self.config.refresh())
        self.assertEqual(self.config.language, "en")
        self.assertEqual(self.config.problem[0], "config_bad_value")
        self.assertEqual(self.config.problem[1]["field"], "breaks.after_minutes")
        self.assertIn("breaks.after_minutes", self.config.problem_in_words())

    def test_where_it_says_for_each_mistake(self):
        for change, field in (({"dock": None}, "dock"), ({"default_mode": "nope"}, "default_mode"),
                              ({"default_mode": "a", "modes": {"a": {"color": 5}}}, "modes.a.color"),
                              ({"default_mode": "Bad Id", "modes": {"Bad Id": {}}}, "modes.Bad")):
            self.write({**FULL, **change})
            self.config.refresh()
            self.assertEqual(self.config.problem[1]["field"], field, change)
        self.write("[1]")
        self.config.refresh()
        self.assertEqual(self.config.problem[1]["field"], "modes.json")
        self.write({"modes": []})
        self.config.refresh()
        self.assertEqual(self.config.problem[1]["field"], "modes")

    def test_fixing_it_takes_the_problem_away(self):
        self.write("{")
        self.config.refresh()
        self.write(FULL)
        self.assertTrue(self.config.refresh())
        self.assertIsNone(self.config.problem)
        self.assertEqual(self.config.problem_in_words(), "")

    def test_so_does_removing_it(self):
        self.write("{")
        self.config.refresh()
        self.path.unlink()
        self.config.refresh()
        self.assertIsNone(self.config.problem)

    def test_llm_null_never_gets_to_the_code_that_reads_it(self):
        self.write({**FULL, "llm": None})
        self.assertFalse(self.config.refresh())
        self.assertIn("backend", self.config.llm)


if __name__ == "__main__":
    unittest.main()
