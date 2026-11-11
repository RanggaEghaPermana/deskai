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
THE_MODEL_FILLS_IN = {"app", "mode", "op", "path", "project", "request", "target", "type", "url"}  # none of them something to run
SHELLS = {"bash", "sh", "dash", "zsh", "fish"}
COMMAND_LINES = {"spawn_command_line_async", "spawn_command_line_sync", "create_from_commandline"}  # one string, parsed and run
SHELL_IN_THE_EXTENSIONS = r"""spawn_?command_?line|create_from_commandline|['"](?:ba|da|z|fi)?sh['"],\s*['"]-\w*c['"]"""
# Where a shell gets text to run on purpose, and which text: a server's command, from its project's files or your Claude
# Code sessions, the line for a new terminal, and a command you wrote in a preset. Never the model's words, so adding to this
# gets the same look as THE_MODEL_CAN.
SHELL_ON_PURPOSE = [("daemon/routine.py", "start_server", "step['command']"), ("daemon/routine.py", "start_server", "step['command']"),
                    ("daemon/routine.py", "terminal_args", "inner"), ("daemon/presets.py", "run_command", "command")]
RAW_FIELDS = {"error", "exception", "traceback"}
ON_A_CARD = {"_say", "_command_card", "card"}
PROGRAMS_EVERYWHERE = {"bash", "gnome-session-quit", "systemctl", "systemd-run"}  # on any desktop DeskAI runs on
RUNS = {"run", "Popen", "check_output", "check_call", "call"}
GLIB_STARTS = {"launch", "launch_uris", "launch_default_for_uri", "launch_default_for_uri_async", "launch_uris_as_manager"}
# What's started right from the daemon on purpose: a dev server when there's no user systemd to run it under, and so no scope either.
LEFT_IN_THE_DAEMON = [("daemon/routine.py", "start_server")]
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
        if shell or name in COMMAND_LINES or (module == "os" and name in ("system", "popen")) or (not module and name in ("eval", "exec")):
            found.append(call.lineno)
    return found


def _may_be_a_shell(program):
    """Anything not written out could be one: [shell, "-c", text]."""
    return not isinstance(program, ast.Constant) or Path(str(program.value)).name in SHELLS


def given_to_a_shell(source):
    """[(function, text)] for each list that has a shell run text not written out right there: ["bash", "-c", text]."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for items in ast.walk(tree):
        if not isinstance(items, (ast.List, ast.Tuple)):
            continue
        for at, flag in enumerate(items.elts[1:], 1):
            if not (isinstance(flag, ast.Constant) and re.fullmatch(r"-\w*c", str(flag.value)) and _may_be_a_shell(items.elts[at - 1])):
                continue
            text = items.elts[at + 1] if at + 1 < len(items.elts) else None
            if isinstance(text, ast.Constant):
                continue
            node = items
            while node in parents and not isinstance(node, ast.FunctionDef):
                node = parents[node]
            found.append((getattr(node, "name", ""), ast.unparse(text) if text else ""))
    return found


def raw_on_a_card(source):
    """[line] for each error that's caught and then put straight into what a card says."""
    found = []
    for handler in ast.walk(ast.parse(source)):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        for call in (node for statement in handler.body for node in ast.walk(statement)):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") in ON_A_CARD and any(
                    isinstance(name, ast.Name) and name.id == handler.name
                    for given in [*call.args, *(word.value for word in call.keywords)] for name in ast.walk(given)):
                found.append(call.lineno)
    return found


class OnlyWhatsOnTheList(unittest.TestCase):
    def test_the_model_picks_from_a_fixed_list(self):
        action = commands.COMMAND_SCHEMA["properties"]["actions"]["items"]["properties"]
        self.assertEqual(set(action["type"]["enum"]), THE_MODEL_CAN, "the list of what the model can do changed: see CONTRIBUTING.md")
        self.assertEqual(set(action["op"]["enum"]), ON_A_SERVER, "the list of what the model can do changed: see CONTRIBUTING.md")
        self.assertEqual(set(action), THE_MODEL_FILLS_IN, "the list of what the model can do changed: see CONTRIBUTING.md")

    def test_a_shell_only_gets_text_where_it_always_has(self):
        found = [(str(path.relative_to(ROOT)), *given) for path in python_files() if path.parent.name in ("daemon", "setup")
                 for given in given_to_a_shell(path.read_text())]
        self.assertEqual(sorted(found), sorted(SHELL_ON_PURPOSE), "a shell must never get the model's words: see SHELL_ON_PURPOSE")

    def test_what_counts_as_giving_a_shell_text(self):
        self.assertEqual(given_to_a_shell('["bash", "-c", text]'), [("", "text")])
        self.assertEqual(given_to_a_shell('("/bin/sh", "-lc", text)'), [("", "text")])
        self.assertEqual(given_to_a_shell('[shutil.which("bash"), "-c", text]'), [("", "text")])
        self.assertEqual(given_to_a_shell('["env", "bash", "-c", f"run {text}"]'), [("", "f'run {text}'")])
        self.assertEqual(given_to_a_shell('["bash", "-c"] + [text]'), [("", "")])
        self.assertEqual(given_to_a_shell('def run(text):\n    return ["zsh", "-ic", text]'), [("run", "text")])
        self.assertEqual(given_to_a_shell('[shell, "-lic", "echo written out"]'), [])
        self.assertEqual(given_to_a_shell('["git", "-c", setting, "status"]'), [])
        self.assertEqual(given_to_a_shell('["bash", script]'), [])

    def test_nor_do_the_extensions_hand_one_anything(self):
        found = [f"{path.name}:{text.count(chr(10), 0, match.start()) + 1}" for path in sorted((ROOT / "extension").glob("*/*.js"))
                 for text in [path.read_text()] for match in re.finditer(SHELL_IN_THE_EXTENSIONS, text, re.I)]
        self.assertEqual(found, [], "the daemon runs programs, from a list of arguments")

    def test_nothing_goes_through_a_shell(self):
        found = [f"{path.relative_to(ROOT)}:{line}" for path in python_files() if path.parent.name in ("daemon", "setup")
                 for line in through_a_shell(path.read_text())]
        self.assertEqual(found, [], "run a list of arguments, with shell=False")

    def test_what_counts_as_a_shell(self):
        self.assertEqual(through_a_shell('subprocess.run(text, shell=True)'), [1])
        self.assertEqual(through_a_shell('os.system(text)'), [1])
        self.assertEqual(through_a_shell('os.popen(text)'), [1])
        self.assertEqual(through_a_shell('eval(text)'), [1])
        self.assertEqual(through_a_shell('GLib.spawn_command_line_async(text)'), [1])
        self.assertEqual(through_a_shell('Gio.AppInfo.create_from_commandline(text, None, 0)'), [1])
        self.assertEqual(through_a_shell('subprocess.run(["ls"], shell=False)'), [])
        self.assertEqual(through_a_shell('subprocess.run(["ls"])'), [])
        self.assertEqual(through_a_shell('self.eval(text)'), [])


class PlainWords(unittest.TestCase):
    def test_no_card_has_room_for_a_raw_error(self):
        found = [key for table in brain.CARD_STRINGS.values() for key, text in table.items() if fields(text) & RAW_FIELDS]
        self.assertEqual(found, [], "say why in plain words: see commands.trouble")

    def test_no_error_goes_straight_on_a_card(self):
        found = [f"{path.relative_to(ROOT)}:{line}" for path in python_files() if path.parent.name == "daemon"
                 for line in raw_on_a_card(path.read_text())]
        self.assertEqual(found, [], "say why in plain words: see commands.trouble")

    def test_what_counts_as_raw(self):
        caught = 'try:\n    a()\nexcept OSError as e:\n    {}'
        self.assertEqual(raw_on_a_card(caught.format('self._say("x", why=str(e))')), [4])
        self.assertEqual(raw_on_a_card(caught.format('self._command_card(f"failed: {e}")')), [4])
        self.assertEqual(raw_on_a_card(caught.format('self.card({"title": str(e)})')), [4])
        self.assertEqual(raw_on_a_card(caught.format('self._command_failed(e)')), [])
        self.assertEqual(raw_on_a_card(caught.format('log.warning("failed: %s", e)')), [])
        self.assertEqual(raw_on_a_card(caught.format('self._say("x")')), [])


def repeated_keys(source):
    """The keys said twice in a dict written out: only the last one counts, and nothing says so."""
    found = []
    for table in ast.walk(ast.parse(source)):
        if isinstance(table, ast.Dict):
            keys = [key.value for key in table.keys if isinstance(key, ast.Constant)]
            found += sorted({key for key in keys if keys.count(key) > 1})
    return found


class BothLanguages(unittest.TestCase):
    def test_no_string_is_said_twice(self):
        for name in ("daemon/brain.py", "setup/deskai_setup.py"):
            self.assertEqual(repeated_keys((ROOT / name).read_text()), [], name)

    def test_what_counts_as_said_twice(self):
        self.assertEqual(repeated_keys('{"a": 1, "b": 2, "a": 3}'), ["a"])
        self.assertEqual(repeated_keys('{"a": 1, "b": 2}'), [])

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


def starts_outside_a_scope(source):
    """[(function, line)] for each thing started in a way that leaves it in the daemon's cgroup, where stopping
    or restarting the daemon takes it down: any Popen not given launcher.own_scope(), and GLib starting an app."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "attr", "")
        wrapped = bool(call.args) and getattr(getattr(call.args[0], "func", None), "attr", "") == "own_scope"
        if name in GLIB_STARTS or (name == "Popen" and not wrapped):
            node = call
            while node in parents and not isinstance(node, ast.FunctionDef):
                node = parents[node]
            found.append((getattr(node, "name", ""), call.lineno))
    return found


class AppsOutliveTheDaemon(unittest.TestCase):
    def test_everything_the_daemon_starts_gets_a_scope(self):
        found = []
        for path in sorted((ROOT / "daemon").glob("*.py")):
            if path.name != "launcher.py":
                found += [(f"daemon/{path.name}", function) for function, _line in starts_outside_a_scope(path.read_text())
                          if (f"daemon/{path.name}", function) not in LEFT_IN_THE_DAEMON]
        self.assertEqual(found, [], "see daemon/launcher.py")

    def test_the_check_itself(self):
        self.assertEqual(starts_outside_a_scope('def f():\n    subprocess.Popen(["ptyxis"])'), [("f", 2)])
        self.assertEqual(starts_outside_a_scope('def f():\n    subprocess.Popen(launcher.own_scope(["ptyxis"]))'), [])
        self.assertEqual(starts_outside_a_scope('def f():\n    info.launch([], None)'), [("f", 2)])
        self.assertEqual(starts_outside_a_scope('def f():\n    Gio.AppInfo.launch_default_for_uri(uri, None)'), [("f", 2)])


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


def _for_the_log(name, parents):
    """Whether a caught error is only going to installer.report() or to stderr. Any other use of it is how text gets out."""
    node = name
    while node in parents and not isinstance(node, ast.Call):
        node = parents[node]
    if not isinstance(node, ast.Call):
        return False
    called = getattr(node.func, "attr", getattr(node.func, "id", ""))
    if called == "report":
        return name in node.args
    return called == "print" and any(word.arg == "file" and ast.unparse(word.value) == "sys.stderr" for word in node.keywords)


def raw_in_setup(source):
    """[line] for each error that's caught in Setup and used for anything but the log: that's text a person could read."""
    found = []
    for handler in ast.walk(ast.parse(source)):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        parents = {child: node for statement in handler.body for node in ast.walk(statement) for child in ast.iter_child_nodes(node)}
        found += [node.lineno for statement in handler.body for node in ast.walk(statement)
                  if isinstance(node, ast.Name) and node.id == handler.name and not _for_the_log(node, parents)]
    return found


def setup_failures(source):
    """[(line, key or None if it isn't written out, {fields given})] for each failure Setup raises, except in the two
    functions that pass on a key they were given or looked up."""
    tree = ast.parse(source)
    passing_on = {node for helper in ast.walk(tree) if isinstance(helper, ast.FunctionDef) and helper.name in ("_tool_failed", "report")
                  for node in ast.walk(helper)}
    found = []
    for call in ast.walk(tree):
        if isinstance(call, ast.Call) and call not in passing_on and getattr(call.func, "id", "") in ("SetupError", "_tool_failed"):
            key = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else None
            found.append((call.lineno, key, {word.arg for word in call.keywords}))
    return found


class SetupInPlainWords(unittest.TestCase):
    RAW = re.compile(r"\[Errno|Traceback|\bE: |\bERROR\b|exit code|stderr|stdout")

    def setUp(self):
        sys.path.insert(0, str(ROOT / "setup"))
        self.addCleanup(sys.path.remove, str(ROOT / "setup"))
        import installer
        self.installer = installer
        self.strings = setup_strings()
        self.source = (ROOT / "setup" / "installer.py").read_text()
        self.troubles = {key for key in self.strings["en"] if key.startswith("trouble_")}

    def test_no_error_is_turned_into_text(self):
        found = [f"{path.relative_to(ROOT)}:{line}" for path in python_files() if path.parent.name == "setup"
                 for line in raw_in_setup(path.read_text())]
        self.assertEqual(found, [], "give it to installer.report(): the window says STRINGS, and the log gets the tool's words")

    def test_what_counts_as_raw(self):
        caught = 'try:\n    a()\nexcept OSError as e:\n    {}'
        self.assertEqual(raw_in_setup(caught.format('error = str(e)')), [4])
        self.assertEqual(raw_in_setup(caught.format('progress(step, "failed", str(e))')), [4])
        self.assertEqual(raw_in_setup(caught.format('progress(step, "failed", f"{e}")')), [4])
        self.assertEqual(raw_in_setup(caught.format('toast(title=e)')), [4])
        self.assertEqual(raw_in_setup(caught.format('error = e')), [4])
        self.assertEqual(raw_in_setup(caught.format('error = report(str(e))')), [4])
        self.assertEqual(raw_in_setup(caught.format('print(f"failed: {e}")')), [4])
        self.assertEqual(raw_in_setup(caught.format('error = installer.report(e)')), [])
        self.assertEqual(raw_in_setup(caught.format('progress(step, "failed", report(e, step))')), [])
        self.assertEqual(raw_in_setup(caught.format('print(f"Keeping: {e}", file=sys.stderr)')), [])
        self.assertEqual(raw_in_setup(caught.format('pass')), [])

    def test_every_failure_is_a_sentence_in_the_window(self):
        found = setup_failures(self.source)
        self.assertGreater(len(found), 5)
        for line, key, given in found:
            self.assertIn(key, self.troubles, f"setup/installer.py:{line}: a key in STRINGS, not a sentence or a tool's words")
            self.assertEqual(fields(self.strings["en"][key]), given, f"setup/installer.py:{line}: what {key} needs filled in")

    def test_what_counts_as_a_failure_with_a_sentence(self):
        self.assertEqual(setup_failures('raise SetupError("trouble_no_key", "said")'), [(1, "trouble_no_key", set())])
        self.assertEqual(setup_failures('raise _tool_failed("trouble_model", result, model=model)'), [(1, "trouble_model", {"model"})])
        self.assertEqual(setup_failures('raise SetupError(result.stderr, "said")'), [(1, None, set())])
        self.assertEqual(setup_failures('def _tool_failed(key, result):\n    return SetupError(key, "said")'), [])

    def test_what_a_tool_says_leads_to_a_sentence_with_nothing_to_fill_in(self):
        for words, key in self.installer.TROUBLES:
            self.assertIn(key, self.troubles, words.pattern)
            self.assertEqual(fields(self.strings["en"][key]), set(), f"{key}: any tool can say this, so there's nothing to fill it with")

    def test_every_sentence_gets_used(self):
        used = set(re.findall(r"trouble_\w+", self.source + (ROOT / "daemon" / "localai.py").read_text()
                              + (ROOT / "setup" / "deskai_setup.py").read_text().split("LANGUAGES =")[1]))
        self.assertEqual(self.troubles - used, set())
        self.assertEqual(used - self.troubles, set())

    def test_the_sentences_themselves_are_plain(self):
        for language, table in self.strings.items():
            for key in self.troubles:
                self.assertIsNone(self.RAW.search(table[key]), f"{language} {key}")
                self.assertLessEqual(fields(table[key]), {"package", "model", "path"}, f"{language} {key}: no room for a raw error")


class OneSizeInOnePlace(unittest.TestCase):
    """What the dock places by hand is sized where it's placed. A second size in the stylesheet, for one look only, is
    how the inbox pill's icons came to hang out of it on Liquid Glass."""

    PLACED_BY_HAND = ("deskai-inbox-face", "deskai-mode-progress")

    def test_the_stylesheet_doesnt_size_them_too(self):
        css = (ROOT / "extension" / "deskai@ranggaeghapermana.github.io" / "stylesheet.css").read_text()
        found = []
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
            for name in self.PLACED_BY_HAND:
                if re.search(rf"\.{name}\b", selector) and re.search(r"(?<![-\w])(?:min-|max-)?(?:width|height)\s*:", body):
                    found.append(" ".join(selector.split()))
        self.assertEqual(found, [], "set the size in extension.js, where it's placed")

    def test_each_look_has_its_sizes(self):
        source = (ROOT / "extension" / "deskai@ranggaeghapermana.github.io" / "extension.js").read_text()
        sizes = source.split("const INBOX_FACES = {")[1].split("};")[0]
        for look in ("classic", "glass"):
            face, step, icon = map(int, re.search(rf"{look}: \{{face: (\d+), step: (\d+), icon: (\d+)\}}", sizes).groups())
            self.assertLess(icon, face, look)
            self.assertLess(step, face, f"{look}: the ones behind still show")


class GlassStaysReadable(unittest.TestCase):
    """Glass shows what's behind it, and the ink on it is one color. Every see-through material says how dark and how bright
    the view through it may get (glass_adapt in glass.js). These are the shader's own sums, done here for each material over
    white, black, gray and strong colors: what's written on it has the contrast small text needs, 4.5 to 1."""

    EXTENSION = ROOT / "extension" / "deskai@ranggaeghapermana.github.io"
    WEIGHTS = (0.2126, 0.7152, 0.0722)
    BEHIND = {"white": (1, 1, 1), "black": (0, 0, 0), "gray": (0.5, 0.5, 0.5), "magenta": (0.875, 0.125, 0.875), "green": (0.1, 0.9, 0.2),
              "yellow": (1, 0.9, 0.1), "blue": (0.1, 0.2, 1), "red": (0.95, 0.1, 0.1)}

    @classmethod
    def luminance(cls, color):
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in color]
        return sum(weight * v for weight, v in zip(cls.WEIGHTS, linear))

    @classmethod
    def contrast(cls, one, other):
        high, low = sorted((cls.luminance(one), cls.luminance(other)), reverse=True)
        return (high + 0.05) / (low + 0.05)

    @classmethod
    def through(cls, behind, tint, alpha, saturation, brightness, low, high):
        """The color of the glass over `behind`, as CODE in glass.js works it out for the flat of the glass."""
        luma = sum(weight * v for weight, v in zip(cls.WEIGHTS, behind))
        c = [min(1, max(0, (luma + (v - luma) * saturation) * brightness)) for v in behind]
        m = sum(weight * v ** 2.2 for weight, v in zip(cls.WEIGHTS, c)) ** (1 / 2.2)
        if m > high:
            c = [v * ((high + (m - high) * 0.2) / m) for v in c]
        elif m < low:
            c = [1 - (1 - v) * ((1 - low + (low - m) * 0.2) / (1 - m)) for v in c]
        return [min(1, max(0, v)) * (1 - alpha) + t * alpha for v, t in zip(c, tint)]

    def materials(self):
        """{(scheme, name): what through() takes}, with the cards' and command bar's "float" worked out as theme.js does."""
        block = (self.EXTENSION / "theme.js").read_text().split("const MATERIAL = {")[1].split("\n};")[0]
        scheme, found = None, {}
        for line in block.splitlines():
            named = re.match(r"\s*(light|dark): \{$", line)
            scheme = named.group(1) if named else scheme
            material = re.match(r"\s*(clear|tinted|sheet): \{tint: rgb\((\d+), (\d+), (\d+), ([\d.]+)\).*saturation: ([\d.]+), "
                                r"brightness: ([\d.]+).*adapt: \[([\d.]+), ([\d.]+)\]", line)
            if material:
                r, g, b, alpha, saturation, brightness, low, high = map(float, material.groups()[1:])
                found[(scheme, material.group(1))] = {"tint": (r / 255, g / 255, b / 255), "alpha": alpha, "saturation": saturation,
                                                      "brightness": brightness, "low": low, "high": high}
            floating = re.match(r"\s*float: ([\d.]+)", line)
            if floating:
                edge = float(floating.group(1))
                found[(scheme, "float")] = {**found[(scheme, "clear")], "alpha": found[(scheme, "clear")]["alpha"] + 0.16,
                                            "low": 0 if scheme == "dark" else edge, "high": edge if scheme == "dark" else 1}
        return found

    def ink(self, selector):
        css = (self.EXTENSION / "stylesheet.css").read_text()
        color = re.search(rf"^{re.escape(selector)} \{{ color: (#[0-9a-f]{{6}}); \}}", css, re.M).group(1)
        return tuple(int(color[at:at + 2], 16) / 255 for at in (1, 3, 5))

    def worst(self, material, ink):
        return min((self.contrast(ink, self.through(behind, **material)), name) for name, behind in self.BEHIND.items())

    def test_every_material_is_there(self):
        self.assertEqual(sorted(self.materials()), sorted((scheme, name) for scheme in ("dark", "light") for name in ("clear", "float", "sheet", "tinted")))

    def test_whats_written_on_it_reads_over_anything(self):
        for (scheme, name), material in self.materials().items():
            ratio, behind = self.worst(material, self.ink(f".deskai-lg-{scheme}"))
            self.assertGreaterEqual(ratio, 4.5, f"{scheme} {name} over {behind}")

    def test_so_do_the_smaller_words(self):
        # On the dock's clear glass they're a shade further from it than on a menu's or a card's.
        for (scheme, name), material in self.materials().items():
            selector = f".deskai-lg-{scheme} .deskai-mode-time" if name == "clear" else f".deskai-lg-{scheme} .deskai-inbox-item-meta"
            ratio, behind = self.worst(material, self.ink(selector))
            self.assertGreaterEqual(ratio, 4.5, f"{scheme} {name} over {behind}")

    def test_it_would_notice(self):
        # With nothing holding the view, white ink on dark glass over a white page is what was reported: about 1.5 to 1.
        material = {**self.materials()[("dark", "clear")], "low": 0, "high": 1}
        self.assertLess(self.worst(material, self.ink(".deskai-lg-dark"))[0], 2)

    def test_menus_are_glass_of_their_own_and_not_nearly_solid(self):
        for (scheme, name), material in self.materials().items():
            if name == "sheet":
                self.assertLessEqual(material["alpha"], 0.6, f"{scheme}: a menu this solid is a panel, beside a dock you can see through")

    def test_the_shader_does_these_sums(self):
        source = (self.EXTENSION / "glass.js").read_text()
        for line in ("return pow(dot(pow(c, vec3(2.2)), vec3(0.2126, 0.7152, 0.0722)), 1.0 / 2.2);",
                     "return c * ((adapt.y + (m - adapt.y) * 0.2) / m);",
                     "return vec3(1.0) - (vec3(1.0) - c) * ((1.0 - adapt.x + (adapt.x - m) * 0.2) / (1.0 - m));",
                     "c = clamp(glass_adapt(c), 0.0, 1.0);", "uniform vec2 adapt;", "this._set('adapt', look.adapt ?? [0, 1]);"):
            self.assertIn(line, source)
        # Both ways of drawing the glass send every uniform on these two lists, and a new one starts out set.
        self.assertIn("'adapt'", source.split("const UNIFORMS = [")[1].split("];")[0])
        self.assertIn("adapt: [0, 1]", source.split("const STARTING = {")[1].split("};")[0])


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
