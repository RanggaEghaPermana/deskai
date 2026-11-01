"""Agent hooks: how Claude Code, Codex and Gemini CLI tell the dock they're done or need you.

Their hook configs run `deskai hook <agent>`, which turns the agent's payload into an inbox
event and sends it to the dock with this process's ancestors, so the dock can find the window
(terminal, IDE, app) the agent runs in. `deskai hooks install` writes those configs, keeping a
backup of each file; `deskai hooks remove` takes DeskAI's entries out again.
"""

import json
import os
import re
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path

import brain

MARK = "deskai hook"
BACKUP_SUFFIX = ".deskai-bak"
CODEX_MARK = "# DeskAI"
GLUED_NOTIFY = re.compile(r"^(.*[^\s].*?)(notify = \[.*\]  # DeskAI)$", re.M)

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
GEMINI_SETTINGS = Path.home() / ".gemini" / "settings.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"
WAITING_FILE = brain.STATE_DIR / "inbox-waiting.jsonl"
WAITING_MAX_BYTES = 256 * 1024

# Claude Code: event → run in the background? The hook takes ~0.1 s, and a background one can be
# cut off when Claude exits right after (as `claude -p` does after its Stop), so only Notification,
# which fires while Claude waits on you anyway, runs in the background.
CLAUDE_EVENTS = {"SessionStart": False, "UserPromptSubmit": False, "Stop": False,
                 "Notification": True, "SessionEnd": False}
GEMINI_EVENTS = ["SessionStart", "BeforeAgent", "AfterAgent", "Notification", "SessionEnd"]

# Claude Code notifications that mean "come back". idle_prompt only repeats a Stop a minute later.
CLAUDE_ATTENTION = {"permission_prompt", "elicitation_dialog", "elicitation_url_dialog", "agent_needs_input"}


def clip(text, limit=240):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def ancestors():
    """PIDs from this process's parent up to init: the agent's window belongs to one of them."""
    pids, pid = [], os.getppid()
    while pid > 1 and len(pids) < 32:
        pids.append(pid)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            break
        pid = int(stat.rsplit(")", 1)[1].split()[1])  # "pid (comm) state ppid …"
    return pids


# ---- payloads → inbox events -------------------------------------------------

def last_claude_message(transcript):
    """The last thing Claude said, from the end of its transcript (JSON lines)."""
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except (OSError, TypeError):
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    return ""


def from_claude(data):
    if data.get("agent_id"):
        return None  # a subagent's turn; the main agent reports when it's done
    name = data.get("hook_event_name", "")
    base = {"source": "claude-code", "session": data.get("session_id", ""), "cwd": data.get("cwd", "")}
    if name == "SessionStart":
        return {**base, "event": "start"}
    if name == "UserPromptSubmit":
        return {**base, "event": "prompt"}
    if name == "SessionEnd":
        return {**base, "event": "end"}
    if name == "Stop":
        text = data.get("last_assistant_message") or last_claude_message(data.get("transcript_path"))
        return {**base, "event": "done", "text": clip(text)}
    if name == "Notification" and data.get("notification_type") in CLAUDE_ATTENTION:
        return {**base, "event": "attention", "text": clip(data.get("message", ""))}
    return None


def from_gemini(data):
    name = data.get("hook_event_name", "")
    base = {"source": "gemini", "session": data.get("session_id", ""), "cwd": data.get("cwd", "")}
    if name == "SessionStart":
        return {**base, "event": "start"}
    if name == "BeforeAgent":
        return {**base, "event": "prompt"}
    if name == "SessionEnd":
        return {**base, "event": "end"}
    if name == "AfterAgent":
        return {**base, "event": "done", "text": clip(data.get("prompt_response", ""))}
    if name == "Notification":
        return {**base, "event": "attention", "text": clip(data.get("message", ""))}
    return None


def from_codex(data):
    if data.get("type") != "agent-turn-complete":
        return None
    return {"source": "codex", "session": data.get("thread-id", ""), "cwd": data.get("cwd", ""),
            "event": "done", "text": clip(data.get("last-assistant-message", ""))}


PARSERS = {"claude": from_claude, "gemini": from_gemini, "codex": from_codex}


def run(agent, payload, send):
    """The hook itself. Never fails and never prints: Claude Code reads a prompt hook's
    stdout as context, and Gemini CLI parses it as JSON."""
    try:
        event = PARSERS[agent](json.loads(payload or "{}"))
    except Exception:  # a hook must never break the agent
        event = None
    if event:
        event["pids"] = ancestors()
        try:
            send(json.dumps(event, ensure_ascii=False))
        except Exception:
            _wait_for_the_dock(event)
    if agent == "gemini":
        print("{}")


def _wait_for_the_dock(event):
    """There's no dock while the screen is locked. It reads this when it's back, and throws it away if the session
    ended instead. Readable only by you: it has what the agent said."""
    try:
        WAITING_FILE.parent.mkdir(parents=True, exist_ok=True)
        if WAITING_FILE.exists() and WAITING_FILE.stat().st_size > WAITING_MAX_BYTES:
            WAITING_FILE.unlink()  # nothing's coming for it: the dock has been off for a long time
        with os.fdopen(os.open(WAITING_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), "a") as f:
            f.write(json.dumps({**event, "at": time.time()}, ensure_ascii=False) + "\n")
    except OSError:
        pass  # a hook must never break the agent


# ---- installing ----------------------------------------------------------------

def hook_command(agent):
    exe = shutil.which("deskai") or str(Path(sys.argv[0]).resolve())
    return [exe, "hook", agent]


def _ours(handler):
    return MARK in str(handler.get("command", ""))


def _strip_hooks(settings):
    """Takes DeskAI's handlers out of a Claude Code or Gemini CLI settings object."""
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return
    for event in list(hooks):
        groups = []
        for group in hooks[event] if isinstance(hooks[event], list) else []:
            kept = [h for h in group.get("hooks", []) if not _ours(h)]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]


def _has_hooks(settings):
    hooks = settings.get("hooks")
    return isinstance(hooks, dict) and any(
        _ours(h) for groups in hooks.values() if isinstance(groups, list)
        for group in groups for h in group.get("hooks", []))


def _backup(path):
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)  # the file as it was before DeskAI touched it
    return backup


def said(error):
    """An error in words, without Python's [Errno 13]."""
    if isinstance(error, OSError) and error.strerror:
        return f"{error.filename}: {error.strerror}" if error.filename else error.strerror
    return str(error)


def _read(path):
    """An agent's own file, which can hold anything, so what's wrong with it is said in words. "" when it isn't there."""
    try:
        return path.read_text() if path.exists() else ""
    except UnicodeDecodeError as e:
        raise ValueError(f"{path} isn't text") from e


def _read_settings(path):
    text = _read(path)
    try:
        settings = json.loads(text) if text else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} isn't valid JSON (line {e.lineno}, column {e.colno})") from e
    if not isinstance(settings, dict):
        raise ValueError(f"{path} isn't a JSON object")
    return settings


def _checked(path, use, settings):
    """use(settings), for settings that are valid JSON but might not be what the agent's are laid out like."""
    try:
        return use(settings)
    except (AttributeError, TypeError) as e:
        raise ValueError(f"{path} isn't laid out the way DeskAI expects") from e


def _json_has_hooks(path):
    return _checked(path, _has_hooks, _read_settings(path))


def _write_settings(path, data):
    """Through a link, for settings kept with your dotfiles, and readable by whoever could before."""
    real = path.resolve()
    mode = stat.S_IMODE(real.stat().st_mode) if real.exists() else None
    brain.write_json_atomic(real, data)
    if mode is not None:
        real.chmod(mode)


def _edit_json(path, edit, keep_a_copy=True):
    data = _read_settings(path)
    before = json.dumps(data, sort_keys=True)
    _checked(path, edit, data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    if keep_a_copy:
        _backup(path)
    _write_settings(path, data)
    return True


def _put_back(path, meaning):
    """DeskAI's part is out again. If nothing else in the file changed meanwhile it goes back to exactly what it was, down to
    its spaces, and the copy goes. If it wasn't there before, it goes. meaning(text) is what a file's text comes down to."""
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if not backup.exists():
        if not meaning(_read(path)):
            path.unlink()
        return
    try:
        if meaning(_read(backup)) != meaning(_read(path)):
            return  # the copy stays: the file has changed since, and maybe not by you
    except ValueError:  # it can't tell
        return
    shutil.copy2(backup, path)
    backup.unlink()


def install_claude():
    command = shlex.join(hook_command("claude"))

    def edit(settings):
        _strip_hooks(settings)
        hooks = settings.setdefault("hooks", {})
        for event, background in CLAUDE_EVENTS.items():
            handler = {"type": "command", "command": command, "timeout": 5}
            if background:
                handler["async"] = True
            hooks.setdefault(event, []).append({"hooks": [handler]})
    return _edit_json(CLAUDE_SETTINGS, edit)


def install_gemini():
    command = shlex.join(hook_command("gemini"))

    def edit(settings):
        _strip_hooks(settings)
        hooks = settings.setdefault("hooks", {})
        for event in GEMINI_EVENTS:
            handler = {"type": "command", "command": command, "name": "deskai", "timeout": 5000}
            hooks.setdefault(event, []).append({"hooks": [handler]})
    return _edit_json(GEMINI_SETTINGS, edit)


def _codex_lines():
    # Before 0.5, DeskAI's line could end up on the end of a last line with no newline, which broke the file. This takes them apart.
    text = GLUED_NOTIFY.sub(r"\1\n\2", _read(CODEX_CONFIG))
    return text.splitlines(keepends=True)


def _codex_notify(lines):
    """Index of the top-level `notify = …` line, if any (top level ends at the first [table])."""
    for i, line in enumerate(lines):
        if line.startswith("["):
            return None
        if re.match(r"\s*notify\s*=", line):
            return i
    return None


def _our_notify(line):
    """DeskAI's own line says so. Yours may have the word in it too: ~/deskai-tools/notify."""
    return line.rstrip().endswith(CODEX_MARK)


def install_codex():
    lines = _codex_lines()
    at = _codex_notify(lines)
    line = f"notify = {json.dumps(hook_command('codex'))}  {CODEX_MARK}\n"
    if at is not None:
        if not _our_notify(lines[at]):
            raise ValueError(f"{CODEX_CONFIG} already has its own `notify`; DeskAI won't overwrite it")
        if lines[at] == line:
            return False
        lines[at] = line
    else:
        first_table = next((i for i, l in enumerate(lines) if l.startswith("[")), len(lines))
        # The line before it may be the last of a file with no newline at its end.
        if first_table and not lines[first_table - 1].endswith("\n"):
            lines[first_table - 1] += "\n"
        lines.insert(first_table, line)
    _backup(CODEX_CONFIG)
    CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG.write_text("".join(lines))
    return True


def _codex_installed():
    lines = _codex_lines()
    at = _codex_notify(lines)
    return at is not None and _our_notify(lines[at])


def remove_codex():
    if not _codex_installed():
        return False
    lines = _codex_lines()
    at = _codex_notify(lines)
    del lines[at]
    CODEX_CONFIG.write_text("".join(lines))
    _put_back(CODEX_CONFIG, str.strip)
    return True


def _remove_json(path):
    if not path.exists() or not _edit_json(path, _strip_hooks, keep_a_copy=False):
        return False
    _put_back(path, lambda text: json.loads(text or "{}"))
    return True


AGENTS = {
    # name: (label, is it here?, installed?, install, remove, where)
    "claude": ("Claude Code", lambda: CLAUDE_SETTINGS.parent.exists() or shutil.which("claude"),
               lambda: _json_has_hooks(CLAUDE_SETTINGS),
               install_claude, lambda: _remove_json(CLAUDE_SETTINGS), CLAUDE_SETTINGS),
    "codex": ("Codex", lambda: CODEX_CONFIG.parent.exists() or shutil.which("codex"),
              _codex_installed, install_codex, remove_codex, CODEX_CONFIG),
    "gemini": ("Gemini CLI", lambda: GEMINI_SETTINGS.parent.exists() or shutil.which("gemini"),
               lambda: _json_has_hooks(GEMINI_SETTINGS),
               install_gemini, lambda: _remove_json(GEMINI_SETTINGS), GEMINI_SETTINGS),
}
