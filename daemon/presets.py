"""Presets: your own routines, the kind nothing could guess.

A preset is a list of steps you wrote, and the words you chose to start it. Typing those words runs the steps. They're
matched as they are, with no AI: the model doesn't hear about presets, so it can't start one, write one or change one.
A command in a preset can only have come from you, and even so it's shown to you once before it first runs, and again
if it changes. The window for writing them is presets_window.py.
"""

import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
from pathlib import Path

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib  # noqa: E402

import brain  # noqa: E402
import commands  # noqa: E402
import launcher  # noqa: E402
import routine  # noqa: E402
from brain import log  # noqa: E402

FILE = brain.CONFIG_DIR / "presets.json"
SEEN_FILE = brain.STATE_DIR / "presets-seen.json"  # the commands you've allowed, by hash
LOG_TAG = "deskai-preset"  # what a command says goes in the journal under this
KINDS = ("app", "url", "mode", "service", "command", "hand")
FIELDS = {"app": "app", "url": "url", "mode": "mode", "service": "unit", "command": "command", "hand": "text"}
MIN_KEYWORD = 2
# All of it has to fit on a card, since that's how you get to see it. Anything longer belongs in a script.
MAX_COMMAND = 500
MAX_HAND = 300
COMMAND_WAIT_S = 30  # after this it counts as something that keeps running, like a server or an app
SLIP = 0.75  # how alike a word and a keyword's word have to be for "did you mean"
SHORT_WORD = 3  # no slips in a word this short
EXTRA_WORDS = {"in", "on", "at", "to", "for", "the", "my", "di", "ke", "buat"}  # all that can be extra: "setup in discord", not "gak setup discord"
ROOM_ON_A_CARD = 7
UNIT = re.compile(r"[A-Za-z0-9][\w@.:-]*")
MARKS = {"running": "…", "done": "✓", "skipped": "✓", "failed": "✗"}


# ---- reading and writing ----------------------------------------------------

def clean_step(kind, value):
    """The step for what was entered, tidied up, or None if it can't be one: an address gets its https://."""
    value = (value or "").strip()
    if kind == "url" and value and "://" not in value:
        value = f"https://{value}"
    step = {"type": kind, FIELDS[kind]: value}
    return step if step_is_fine(step) else None


def _can_be_shown(text):
    """No line breaks, control characters or invisible ones. A card has to show you what it's asking about: one of these can
    blank it, or make a command read as something else."""
    return all(unicodedata.category(character)[0] != "C" for character in text)


def step_is_fine(step):
    kind = step.get("type") if isinstance(step, dict) else None
    if kind not in KINDS:
        return False
    value = step.get(FIELDS[kind])
    if not isinstance(value, str) or not value.strip() or not _can_be_shown(value):
        return False
    if kind == "url":
        return commands.safe_url(value)
    if kind == "service":
        return bool(UNIT.fullmatch(value))
    if kind == "command":
        return len(value) <= MAX_COMMAND
    if kind == "hand":
        return len(value) <= MAX_HAND
    return True


def _is_text_list(value):
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_preset(preset):
    return (isinstance(preset, dict) and isinstance(preset.get("name"), str) and _can_be_shown(preset["name"])
            and _is_text_list(preset.get("keywords")) and all(_can_be_shown(keyword) for keyword in preset["keywords"])
            and isinstance(preset.get("steps"), list))


def problems(items):
    """What's wrong with a list of presets, as [(which preset, card string, its fields)]. Empty if it can be saved."""
    found, names, keywords = [], {}, {}
    for at, preset in enumerate(items):
        if not _is_preset(preset):
            found.append((at, "presets_bad_preset", {}))
            continue
        name = preset["name"].strip()
        if not name:
            found.append((at, "presets_no_name", {}))
        elif name.lower() in names:
            found.append((at, "presets_same_name", {"name": name}))
        names[name.lower()] = at
        said = [keyword for keyword in preset["keywords"] if keyword.strip()]
        if not said:
            found.append((at, "presets_no_keyword", {"name": name}))
        elif len(said) != len(preset["keywords"]):  # the window leaves these out, so this is a file written by hand
            found.append((at, "presets_bad_preset", {}))
        for keyword in said:
            joined = _joined(keyword)
            if len(joined) < MIN_KEYWORD:
                found.append((at, "presets_short_keyword", {"keyword": keyword.strip()}))
            elif keywords.get(joined, at) != at:
                found.append((at, "presets_keyword_taken", {"keyword": keyword.strip(), "name": items[keywords[joined]]["name"]}))
            keywords.setdefault(joined, at)
        if not preset["steps"]:
            found.append((at, "presets_no_steps", {"name": name}))
        found += [(at, "presets_bad_step", {"n": n, "name": name}) for n, step in enumerate(preset["steps"], 1) if not step_is_fine(step)]
    return found


def read(path=None):
    """(the presets, whether the file is broken). No file just means there are none yet."""
    try:
        data = json.loads(Path(path or FILE).read_text())
    except FileNotFoundError:
        return [], False
    except (OSError, ValueError, RecursionError) as e:
        log.warning("presets.json unreadable: %s", e)
        return [], True
    items = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(items, list) or problems(items):
        log.warning("presets.json has something wrong with it")
        return [], True
    return items, False


def save(items, path=None):
    """Writes the presets, keeping a file it couldn't read next to it, since that's someone's work."""
    path = Path(path or FILE)
    if read(path)[1]:
        shutil.copy2(path, path.with_name(f"{path.name}.broken-{time.strftime('%Y%m%d-%H%M%S')}"))
    brain.write_json_atomic(path, {"presets": items})


class Store:
    """presets.json, read again whenever the file changes. A broken file keeps the last good copy, and `broken` says so."""

    def __init__(self, path=None):
        self.path = Path(path or FILE)
        self.items = []
        self.broken = False
        self.told = False  # that it's broken, once for each time it breaks
        self._seen = None

    def refresh(self):
        try:
            stat = self.path.stat()
        except OSError:
            self.items, self.broken, self._seen = [], False, None
            return
        seen = (stat.st_mtime_ns, stat.st_ino, stat.st_size)
        if seen == self._seen:
            return
        self._seen = seen
        items, broken = read(self.path)
        self.broken = broken
        self.told = self.told and broken
        if not broken:
            self.items = items


# ---- which preset was meant --------------------------------------------------

def _normal(text):
    return " ".join(text.lower().split()).rstrip(".!").strip()


def _joined(text):
    """"set up discord" and "setup discord" are the same words to you, so they're the same here."""
    return _normal(text).replace(" ", "")


def named(text, items):
    """The preset these words are a keyword of, politeness aside. This one's exact, so it runs without asking."""
    said = {_joined(text), _joined(commands._trimmed(text))} - {""}  # "dong" alone is nothing, whatever a file has in it
    return next((preset for preset in items if said.intersection(_joined(keyword) for keyword in preset["keywords"])), None)


def _words(text):
    return _normal(text).replace("set up", "setup").split()


def _close(word, wanted):
    if word == wanted or len(wanted) <= SHORT_WORD:
        return word == wanted
    if abs(len(word) - len(wanted)) >= 2 and (word in wanted or wanted in word):
        return False  # a word of its own: "disconnect" isn't a slip for "connect"
    return difflib.SequenceMatcher(None, word, wanted).ratio() >= SLIP


def _a_slip(said, wanted):
    """Word by word, not as a whole: "tutup discord" looks a lot like "setup discord", and asks for something else."""
    if len(said) == len(wanted):
        return all(_close(word, want) for word, want in zip(said, wanted))
    if len(said) != len(wanted) + 1:
        return False
    return any(said[at] in EXTRA_WORDS and _a_slip(said[:at] + said[at + 1:], wanted) for at in range(len(said)))


def near(text, items):
    """(preset, keyword) if these words look like a slip for one keyword and no other. Only ever asked about, never run."""
    words = commands._trimmed(text)
    if commands.TOLD_NOT_TO.search(words) or commands.NOT_A_REQUEST.search(words):
        return None
    said = _words(words)
    found = [(preset, keyword) for preset in items for keyword in preset["keywords"]
             if keyword.strip() and _a_slip(said, _words(keyword))]
    return found[0] if len({preset["name"] for preset, _keyword in found}) == 1 else None


def understood_otherwise(keywords, modes, watch, known_projects):
    """Those of these keywords that already mean something to the command bar: "setup toko" with a project called toko."""
    return [keyword for keyword in keywords
            if (quick := commands.quick(keyword, modes, watch, known_projects)) and quick["actions"][0]["type"] != "left_alone"]


# ---- commands you've seen ----------------------------------------------------

def _hash(command):
    return hashlib.sha256(command.encode()).hexdigest()


def _seen_commands():
    try:
        seen = json.loads(SEEN_FILE.read_text())
    except (OSError, ValueError, RecursionError):
        return []
    return seen if _is_text_list(seen) else []


def unseen_commands(preset):
    """The commands in this preset you haven't allowed yet, as they'll run."""
    seen = set(_seen_commands())
    commands_here = [step["command"] for step in preset["steps"] if step["type"] == "command"]
    return [command for command in dict.fromkeys(commands_here) if _hash(command) not in seen]


def allow(command):
    brain.write_json_atomic(SEEN_FILE, [*_seen_commands(), _hash(command)])


# ---- doing it ----------------------------------------------------------------

def run_command(command):
    """Runs a command you wrote, as if from your terminal but without one, in your home folder and its own scope. What it
    says goes in the journal. Returns the step's state and why it failed. Something still going after a while, like a
    server or an app, is left to it, and the next step gets its turn."""
    env = {**os.environ, "PATH": routine.shell_path() or os.environ.get("PATH", "")}
    env.pop("ANTHROPIC_API_KEY", None)  # DeskAI's, from its own settings, and nothing you put in your command
    to_journal = ["systemd-cat", "-t", LOG_TAG] if shutil.which("systemd-cat") else []
    process = subprocess.Popen(launcher.own_scope([*to_journal, "bash", "-c", command]), cwd=Path.home(), env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        code = process.wait(timeout=COMMAND_WAIT_S)
    except subprocess.TimeoutExpired:
        threading.Thread(target=process.wait, name="preset-command", daemon=True).start()
        return "done", ""
    return ("done", "") if code == 0 else ("failed", "preset_why_command")


def _do(step, set_mode):
    kind = step["type"]
    if kind == "app":
        return ("done", "") if launcher.open_app(step["app"]) else ("failed", "preset_why_app")
    if kind == "url":
        launcher.open_uri(step["url"])
        return "done", ""
    if kind == "mode":
        return ("done", "") if set_mode(step["mode"]) else ("failed", "preset_why_mode")
    if kind == "service":
        if commands.service_active(step["unit"]):
            return "skipped", ""
        started, said = commands.systemctl(step["unit"], "start")
        return ("done", "") if started else ("failed", commands.trouble(said))
    return run_command(step["command"])


def run(preset, progress, set_mode, your_turn, cancelled=lambda: False):
    """Does a preset's steps in order, stopping at the first one that fails, since the next may be counting on it.
    progress(where, state, why) with state in running, done, skipped, failed. set_mode(mode) says if there's such a mode.
    your_turn(where, step) waits for a step done by hand, and says whether to go on."""
    for at, step in enumerate(preset["steps"]):
        if cancelled():
            return
        progress(at, "running", "")
        if step["type"] == "hand":
            if not your_turn(at, step):
                return
            progress(at, "done", "")
            continue
        try:
            state, why = _do(step, set_mode)
        except (OSError, subprocess.SubprocessError, ValueError, GLib.Error) as e:
            log.warning("preset: step %d of %s failed: %s", at + 1, preset["name"], e)
            state, why = "failed", "preset_why_step"
        progress(at, state, why)
        if state == "failed":
            return


# ---- in words ----------------------------------------------------------------

def _clip(text, room=60):
    text = " ".join(text.split())
    return text if len(text) <= room else f"{text[:room - 1]}…"


def label(step, say, mode_label=lambda mode: mode):
    """One short line for a step. say(key, **fields) is what puts it in your language."""
    kind = step["type"]
    if kind == "app":
        info = launcher.app_info(step["app"])
        return say("preset_step_app", name=info.get_name() if info else step["app"])
    if kind == "url":
        return say("preset_step_url", url=_clip(re.sub(r"^https?://", "", step["url"])))
    if kind == "mode":
        return say("preset_step_mode", mode=mode_label(step["mode"]))
    if kind == "service":
        return say("preset_step_service", name=commands.service_label(step["unit"]))
    if kind == "command":
        return f"$ {_clip(step['command'])}"
    return _clip(step["text"])


def progress_lines(labels, marks):
    """The steps with how each went, as many as fit on a card, around the one it's on."""
    lines = [f"{MARKS.get(marks.get(at), '·')} {text}" for at, text in enumerate(labels)]
    if len(lines) <= ROOM_ON_A_CARD:
        return lines
    start = min(max(max(marks, default=0) - ROOM_ON_A_CARD // 2, 0), len(lines) - ROOM_ON_A_CARD)
    return lines[start:start + ROOM_ON_A_CARD]
