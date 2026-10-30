"""Agent hooks: how Claude Code, Codex and Gemini CLI tell the dock they're done or need you.

Their hook configs run `focusdock hook <agent>`, which turns the agent's payload into an inbox
event and sends it to the dock with this process's ancestors, so the dock can find the window
(terminal, IDE, app) the agent runs in. `focusdock hooks install` writes those configs, keeping a
backup of each file; `focusdock hooks remove` takes FocusDock's entries out again.
"""

import json
import os
import re
import shlex
import shutil
import sys
from pathlib import Path

import brain

MARK = "focusdock hook"
BACKUP_SUFFIX = ".focusdock-bak"

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
GEMINI_SETTINGS = Path.home() / ".gemini" / "settings.json"
CODEX_CONFIG = Path.home() / ".codex" / "config.toml"

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
            pass  # the dock isn't running
    if agent == "gemini":
        print("{}")


# ---- installing ----------------------------------------------------------------

def hook_command(agent):
    exe = shutil.which("focusdock") or str(Path(sys.argv[0]).resolve())
    return [exe, "hook", agent]


def _ours(handler):
    return MARK in str(handler.get("command", ""))


def _strip_hooks(settings):
    """Takes FocusDock's handlers out of a Claude Code or Gemini CLI settings object."""
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
        shutil.copy2(path, backup)  # the file as it was before FocusDock touched it
    return backup


def _edit_json(path, edit):
    data = json.loads(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} bukan object JSON")
    before = json.dumps(data, sort_keys=True)
    edit(data)
    if json.dumps(data, sort_keys=True) == before:
        return False
    _backup(path)
    brain.write_json_atomic(path, data)
    return True


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
            handler = {"type": "command", "command": command, "name": "focusdock", "timeout": 5000}
            hooks.setdefault(event, []).append({"hooks": [handler]})
    return _edit_json(GEMINI_SETTINGS, edit)


def _codex_lines():
    return CODEX_CONFIG.read_text().splitlines(keepends=True) if CODEX_CONFIG.exists() else []


def _codex_notify(lines):
    """Index of the top-level `notify = …` line, if any (top level ends at the first [table])."""
    for i, line in enumerate(lines):
        if line.startswith("["):
            return None
        if re.match(r"\s*notify\s*=", line):
            return i
    return None


def install_codex():
    lines = _codex_lines()
    at = _codex_notify(lines)
    line = f"notify = {json.dumps(hook_command('codex'))}  # FocusDock\n"
    if at is not None:
        if "focusdock" not in lines[at]:
            raise ValueError(f"{CODEX_CONFIG} already has its own `notify`; FocusDock won't overwrite it")
        if lines[at] == line:
            return False
        lines[at] = line
    else:
        first_table = next((i for i, l in enumerate(lines) if l.startswith("[")), len(lines))
        lines.insert(first_table, line)
    _backup(CODEX_CONFIG)
    CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CODEX_CONFIG.write_text("".join(lines))
    return True


def _codex_installed():
    lines = _codex_lines()
    at = _codex_notify(lines)
    return at is not None and "focusdock" in lines[at]


def remove_codex():
    if not _codex_installed():
        return False
    lines = _codex_lines()
    at = _codex_notify(lines)
    del lines[at]
    CODEX_CONFIG.write_text("".join(lines))
    return True


def _remove_json(path):
    return path.exists() and _edit_json(path, _strip_hooks)


AGENTS = {
    # name: (label, is it here?, installed?, install, remove, where)
    "claude": ("Claude Code", lambda: CLAUDE_SETTINGS.parent.exists() or shutil.which("claude"),
               lambda: CLAUDE_SETTINGS.exists() and _has_hooks(json.loads(CLAUDE_SETTINGS.read_text())),
               install_claude, lambda: _remove_json(CLAUDE_SETTINGS), CLAUDE_SETTINGS),
    "codex": ("Codex", lambda: CODEX_CONFIG.parent.exists() or shutil.which("codex"),
              _codex_installed, install_codex, remove_codex, CODEX_CONFIG),
    "gemini": ("Gemini CLI", lambda: GEMINI_SETTINGS.parent.exists() or shutil.which("gemini"),
               lambda: GEMINI_SETTINGS.exists() and _has_hooks(json.loads(GEMINI_SETTINGS.read_text())),
               install_gemini, lambda: _remove_json(GEMINI_SETTINGS), GEMINI_SETTINGS),
}
