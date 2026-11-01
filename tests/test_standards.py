"""The rules a machine can check, across all of DeskAI's code. CONTRIBUTING.md has the ones that take a person.
Run with: python3 -m unittest discover tests"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import brain  # noqa: E402
import commands  # noqa: E402

# What the model can pick, and all it can pick. Adding to this is a decision about what DeskAI is, not a detail.
THE_MODEL_CAN = {"mode", "setup_project", "close_project", "restore_desk", "server", "open_app", "open_url", "open_folder", "edit_modes"}
ON_A_SERVER = {"restart", "stop", "log"}
PROGRAMS_EVERYWHERE = {"bash", "gnome-session-quit", "systemctl", "systemd-run"}  # on any desktop DeskAI runs on
RUNS = {"run", "Popen", "check_output", "check_call", "call"}
CATCHES_A_MISSING_PROGRAM = {"OSError", "FileNotFoundError", "Exception"}
GNOME_TRAPS = {
    r"\bvertical:\s*true|\borientation:\s*Clutter": "St.BoxLayout changed in GNOME 48: use VERTICAL_BOX",
    r"\.get_context\(\)\.get_backend\(|Clutter\.get_default_backend": "the stage has a context from GNOME 47: use defaultSeat()",
    r"\.(?:un)?maximize\(|\.set_(?:un)?maximize_flags\(": "maximizing changed in GNOME 49: use maximize() and unmaximize()",
    r"PACKAGE_VERSION": "use SHELL_MAJOR",
    r"rotation_angle_": "GNOME 46's ease() can't see two underscores in a name: use a Clutter.PropertyTransition",
    r"import[^;]*\bSTARTUP_ANIMATION_TIME\b[^;]*from": "GNOME 46 doesn't export it",
}


def python_files():
    return sorted(path for folder in ("daemon", "setup", "tests", "evals") for path in (ROOT / folder).rglob("*.py"))


def fields(text):
    return set(re.findall(r"\{(\w+)", str(text)))


def setup_strings():
    for node in ast.parse((ROOT / "setup" / "deskai_setup.py").read_text()).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "STRINGS":
            return ast.literal_eval(node.value)
    raise AssertionError("no STRINGS in setup/deskai_setup.py")


def _programs(command):
    if isinstance(command, ast.IfExp):
        return _programs(command.body) + _programs(command.orelse)
    if isinstance(command, ast.List) and command.elts and isinstance(command.elts[0], ast.Constant):
        return [command.elts[0].value]
    return []


def _catches(handler):
    if handler.type is None:
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(getattr(name, "attr", getattr(name, "id", "")) in CATCHES_A_MISSING_PROGRAM for name in names)


def unguarded(source):
    """[(line, program)] for each program run with nothing to notice it isn't installed: no try around it
    that would catch that, and no shutil.which() for it in the same function."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr in RUNS
                and getattr(call.func.value, "id", "") == "subprocess" and call.args):
            continue
        around, node = [], call
        while node in parents:
            node = parents[node]
            around.append(node)
        if any(isinstance(node, ast.Try) and any(_catches(handler) for handler in node.handlers) for node in around):
            continue
        function = next((node for node in around if isinstance(node, ast.FunctionDef)), tree)
        asked = {inner.args[0].value for inner in ast.walk(function)
                 if isinstance(inner, ast.Call) and getattr(inner.func, "attr", "") == "which"
                 and inner.args and isinstance(inner.args[0], ast.Constant)}
        found += [(call.lineno, program) for program in _programs(call.args[0])
                  if program not in PROGRAMS_EVERYWHERE and program not in asked]
    return found


def through_a_shell(source):
    """[line] for each way of handing a string to a shell or to Python to run."""
    found = []
    for call in ast.walk(ast.parse(source)):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "attr", getattr(call.func, "id", ""))
        module = getattr(getattr(call.func, "value", None), "id", "")
        shell = any(word.arg == "shell" and getattr(word.value, "value", False) is not False for word in call.keywords)
        if shell or (module == "os" and name in ("system", "popen")) or (not module and name in ("eval", "exec")):
            found.append(call.lineno)
    return found


class OnlyWhatsOnTheList(unittest.TestCase):
    def test_the_model_picks_from_a_fixed_list(self):
        action = commands.COMMAND_SCHEMA["properties"]["actions"]["items"]["properties"]
        self.assertEqual(set(action["type"]["enum"]), THE_MODEL_CAN, "the list of what the model can do changed: see CONTRIBUTING.md")
        self.assertEqual(set(action["op"]["enum"]), ON_A_SERVER, "the list of what the model can do changed: see CONTRIBUTING.md")

    def test_nothing_goes_through_a_shell(self):
        found = [f"{path.relative_to(ROOT)}:{line}" for path in python_files() if path.parent.name in ("daemon", "setup")
                 for line in through_a_shell(path.read_text())]
        self.assertEqual(found, [], "run a list of arguments, with shell=False")

    def test_what_counts_as_a_shell(self):
        self.assertEqual(through_a_shell('subprocess.run(text, shell=True)'), [1])
        self.assertEqual(through_a_shell('os.system(text)'), [1])
        self.assertEqual(through_a_shell('os.popen(text)'), [1])
        self.assertEqual(through_a_shell('eval(text)'), [1])
        self.assertEqual(through_a_shell('subprocess.run(["ls"], shell=False)'), [])
        self.assertEqual(through_a_shell('subprocess.run(["ls"])'), [])
        self.assertEqual(through_a_shell('self.eval(text)'), [])


class BothLanguages(unittest.TestCase):
    def check(self, table):
        self.assertEqual(sorted(table["en"]), sorted(table["id"]), "the same strings in English and Indonesian")
        for key, text in table["en"].items():
            self.assertEqual(fields(text), fields(table["id"][key]), f"the same {{fields}} in {key}")

    def test_the_cards(self):
        self.check(brain.CARD_STRINGS)

    def test_setup(self):
        self.check(setup_strings())


class OutsidePrograms(unittest.TestCase):
    def test_a_missing_one_is_noticed(self):
        found = [f"{path.relative_to(ROOT)}:{line} {program}"
                 for path in python_files() if path.parent.name in ("daemon", "setup")
                 for line, program in unguarded(path.read_text())]
        self.assertEqual(found, [], "wrap these in a try that catches OSError, or ask shutil.which() first")

    def test_what_counts_as_noticing(self):
        self.assertEqual(unguarded('subprocess.run(["docker", "ps"])'), [(1, "docker")])
        self.assertEqual(unguarded('subprocess.Popen(["git"] if a else ["docker", "ps"])'), [(1, "git"), (1, "docker")])
        self.assertEqual(unguarded('subprocess.run(["systemctl", "stop", unit])'), [])
        self.assertEqual(unguarded('try:\n    subprocess.run(["docker"])\nexcept OSError:\n    pass'), [])
        self.assertEqual(unguarded('try:\n    subprocess.run(["docker"])\nexcept (ValueError, OSError):\n    pass'), [])
        self.assertEqual(unguarded('try:\n    subprocess.run(["docker"])\nexcept ValueError:\n    pass'), [(2, "docker")])
        self.assertEqual(unguarded('def f():\n    if shutil.which("docker"):\n        subprocess.run(["docker"])'), [])
        self.assertEqual(unguarded('def f():\n    if shutil.which("git"):\n        subprocess.run(["docker"])'), [(3, "docker")])


class GnomeVersions(unittest.TestCase):
    def test_what_differs_stays_in_compat(self):
        found = []
        for path in sorted((ROOT / "extension").glob("*/*.js")):
            if path.name == "compat.js":
                continue
            text = path.read_text()
            for pattern, why in GNOME_TRAPS.items():
                found += [f"{path.name}:{text.count(chr(10), 0, match.start()) + 1} {why}" for match in re.finditer(pattern, text)]
        self.assertEqual(found, [], "see compat.js")


class PythonVersions(unittest.TestCase):
    def test_everything_parses_on_the_oldest_python(self):
        # Ubuntu 24.04's.
        for path in python_files():
            try:
                ast.parse(path.read_text(), feature_version=(3, 12))
            except SyntaxError as e:
                self.fail(f"{path.relative_to(ROOT)}:{e.lineno} needs a newer Python than 3.12: {e.msg}")


if __name__ == "__main__":
    unittest.main()
