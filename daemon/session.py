"""Picking up where you left off: what was open before you logged out (or restarted) comes back as it was.

While you work, `snapshot()` records the desktop every half minute:
- every window, from the dock: its app, title, workspace, size and place, maximized or not, stacking;
- each terminal window's tabs in order (Ptyxis shows them to the accessibility tree), and in each
  tab its folder and the Claude Code conversation running there (Claude keeps
  ~/.claude/sessions/<pid>.json, and titles the tab after the conversation);
- DeskAI's dev servers, the database the routine starts, the folders open in Files, and the dock mode.
The last few snapshots are kept in ~/.local/state/deskai/session.json.

After a new login, `restore()` brings the richest of the final snapshots back: the database, the
servers, Chrome with its tabs, Files at the same folders, the other apps, every terminal window with
its tabs (conversations resumed with the same flags), and then every window where it was: the same
workspace, size and place, maximized or not, stacked the same, the one you were in on top.
"""

import datetime
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

import brain  # noqa: E402
import launcher  # noqa: E402
import routine  # noqa: E402
from brain import log  # noqa: E402

SESSION_FILE = brain.STATE_DIR / "session.json"
KEEP = 6                       # snapshots kept (three minutes at one per 30 s)
FINAL = 3                      # restore the richest of the last few: the very last may be mid-logout
MAX_AGE_S = 36 * 3600          # older than this, the desk has moved on
CLAUDE_SESSIONS = Path.home() / ".claude" / "sessions"
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
TERMINAL_AGENTS = {"ptyxis-agent"}
PTYXIS = "org.gnome.Ptyxis.desktop"
TAB_READER = Path(__file__).with_name("terminal_tabs.py")
# Apps restored their own way, not by a plain launch.
BROWSER_APPS = ("google-chrome.desktop", "com.google.Chrome.desktop", "chromium.desktop",
                "chromium_chromium.desktop", "chromium-browser.desktop")
SPECIAL_APPS = {PTYXIS, "org.gnome.Nautilus.desktop", *BROWSER_APPS}
# Claude puts a spinner or ✳ before a tab's title, and Ptyxis may add " — <command>" after it.
TITLE_GLYPHS = re.compile(r"^[^\w(\[]+")
# bash titles a tab "user@host: ~/folder".
SHELL_TITLE = re.compile(r"^[\w.-]+@[\w.-]+:\s*(.+)$")
APPEAR_S = 10                  # waiting for a new terminal window to show up
PLACE_S = 20                   # waiting for the other apps to open their windows (base)
PLACE_S_PER_WINDOW = 2         # extra seconds per non-terminal window; large sessions need more time
WINDOW_FIELDS = ("id", "app", "title", "workspace", "sticky", "monitor", "rect", "max_h", "max_v",
                 "maximized", "fullscreen", "minimized", "above", "stack", "focused")
# Claude flags that pick the conversation: replaced by --resume <the conversation> on restore.
CLAUDE_DROP = {"--resume", "-r", "--continue", "-c", "--session-id", "--fork-session", "-p", "--print"}
CLAUDE_VALUE_FLAGS = {"--resume", "-r", "--session-id", "--model", "--permission-mode", "--add-dir",
                      "--settings", "--agent", "--effort", "--mcp-config", "--append-system-prompt",
                      "--fallback-model", "--setting-sources", "--allowedTools", "--disallowedTools"}


def current_session():
    """Your desktop's login: a new one after every login, the same after unlocking.

    logind numbers sessions from the start again after a reboot, so the boot is part of it."""
    try:
        out = subprocess.run(["loginctl", "show-user", str(os.getuid()), "-p", "Display", "--value"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()[:8]
    except (OSError, subprocess.SubprocessError):
        return ""
    return f"{boot}-{out}" if out else ""


# ---- reading the desktop --------------------------------------------------------------

def processes():
    """pid → (ppid, comm) for your processes."""
    table = {}
    uid = os.getuid()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != uid:
                continue
            stat = Path(f"/proc/{entry.name}/stat").read_text()
        except OSError:
            continue
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        ppid = int(stat[stat.rindex(")") + 2:].split()[1])
        table[int(entry.name)] = (ppid, comm)
    return table


def cmdline(pid):
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace").split("\0")[:-1]
    except OSError:
        return []


def cwd_of(pid):
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def claude_flags(args):
    """The flags Claude was started with, minus the ones that pick a conversation."""
    kept, i = [], 0
    while i < len(args):
        arg = args[i]
        if arg in CLAUDE_VALUE_FLAGS:
            if arg not in CLAUDE_DROP:
                kept += args[i:i + 2]
            i += 2
            continue
        if arg.startswith("-") and arg not in CLAUDE_DROP:
            kept.append(arg)
        i += 1  # positional arguments (a prompt) aren't kept
    return kept


def ai_title(session_id, _cache={}):
    """The title Claude gave a conversation: it's what Claude puts in the terminal tab's title."""
    for path in CLAUDE_PROJECTS.glob(f"*/{session_id}.jsonl"):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if _cache.get(path, (None,))[0] == size:
            return _cache[path][1]
        title = ""
        with open(path, "rb") as f:
            f.seek(max(0, size - 512 * 1024))  # Claude writes it again every turn: the tail has it
            for line in reversed(f.read().splitlines()):
                if b'"ai-title"' in line or b'"custom-title"' in line:
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    title = entry.get("customTitle") or entry.get("aiTitle") or ""
                    if title:
                        break
        _cache[path] = (size, title)
        return title
    return ""


def clean_title(title):
    """A tab's title without Claude's spinner before it or Ptyxis' command after it."""
    return TITLE_GLYPHS.sub("", title.split(" — ")[0]).strip()


def shell_tabs(table):
    """Every shell in a terminal tab: its folder, and the Claude conversation running in it, if any."""
    children = {}
    for pid, (ppid, _comm) in table.items():
        children.setdefault(ppid, []).append(pid)
    agents = [pid for pid, (_ppid, comm) in table.items() if comm in TERMINAL_AGENTS]
    tabs = []
    for agent in agents:
        for shell in sorted(children.get(agent, [])):
            tab = {"pid": shell, "cwd": cwd_of(shell)}
            stack = list(children.get(shell, []))
            while stack:
                pid = stack.pop()
                info = CLAUDE_SESSIONS / f"{pid}.json"
                if info.exists():
                    try:
                        data = json.loads(info.read_text())
                    except (OSError, ValueError):
                        data = {}
                    args = cmdline(pid)
                    if data.get("sessionId") and data.get("kind", "interactive") == "interactive":
                        tab.update({"cwd": data.get("cwd") or tab["cwd"], "claude": data["sessionId"],
                                    "flags": claude_flags(args[1:]), "title": ai_title(data["sessionId"]),
                                    "exe": args[0] if args and os.path.isabs(args[0]) else routine.find_claude()})
                        break
                stack.extend(children.get(pid, []))
            if tab["cwd"]:
                tabs.append(tab)
    return tabs


def ptyxis_windows():
    """[(window title, [tab titles in order])] from Ptyxis' accessibility tree, or None."""
    try:
        out = subprocess.run([sys.executable, "-I", str(TAB_READER)], capture_output=True, text=True,
                             timeout=6).stdout
        return [(w["title"], w["tabs"] or [w["title"]]) for w in json.loads(out)]
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


def terminals(table, windows):
    """Each terminal window and its tabs in order: [{"window": <index in windows>, "tabs": [...], "active": i}].

    A tab finds its shell by its title: a Claude conversation by the title Claude gives it, a plain
    shell by the folder bash puts in it. Shells nobody claimed fill the tabs left (an editor or a
    server running there), and any still left over get a window of their own.
    """
    free = shell_tabs(table)
    mine = [i for i, w in enumerate(windows) if w.get("app") == PTYXIS]
    frames = ptyxis_windows() if mine or free else None
    if frames is None:  # no accessibility: one tab per window, as far as we can tell
        frames = [(windows[i]["title"], [windows[i]["title"]]) for i in mine]

    def claim(title):
        name = clean_title(title)
        match = SHELL_TITLE.match(name)
        folder = os.path.expanduser(match.group(1).strip()) if match else None
        for shell in free:
            if shell.get("claude") and shell.get("title") and clean_title(shell["title"]) == name or \
                    folder and not shell.get("claude") and shell["cwd"] == folder:
                free.remove(shell)
                return shell
        return None

    found, taken = [], set()
    for title, titles in frames:
        index = next((i for i in mine if i not in taken and windows[i]["title"] == title), None)
        taken.add(index)
        found.append({"window": index, "tabs": [claim(t) for t in titles],
                      "active": titles.index(title) if title in titles else 0})
    for entry in found:
        entry["tabs"] = [tab or (free.pop(0) if free else {"cwd": str(Path.home())}) for tab in entry["tabs"]]
    found += [{"window": None, "tabs": [shell], "active": 0} for shell in free]
    for entry in found:
        for tab in entry["tabs"]:
            tab.pop("pid", None)
    return found


def terminal_windows(snap):
    """The snapshot's terminal windows (older snapshots kept a flat list of tabs: a window each)."""
    return [t if "tabs" in t else {"window": None, "tabs": [t], "active": 0} for t in snap.get("terminals", [])]


def all_tabs(snap):
    return [tab for entry in terminal_windows(snap) for tab in entry["tabs"]]


def servers():
    """DeskAI's dev servers that are running (started by the routine or a restore)."""
    try:
        out = subprocess.run(["systemctl", "--user", "list-units", "deskai-*", "--state=active",
                              "--no-legend", "--plain"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for unit in (line.split()[0] for line in out.splitlines() if line.strip()):
        props = subprocess.run(["systemctl", "--user", "show", unit, "-p", "Description", "-p", "WorkingDirectory"],
                               capture_output=True, text=True, timeout=5).stdout
        values = dict(line.split("=", 1) for line in props.splitlines() if "=" in line)
        command = values.get("Description", "").removeprefix("DeskAI: ")
        if command and values.get("WorkingDirectory"):
            found.append({"cwd": values["WorkingDirectory"], "command": command, "port": routine.guess_port(command),
                          "unit": unit})
    return found


def databases():
    """The routine's system services (your database) that are running, and any database server
    installed on the system that's running, whether a project uses it or not."""
    units = [s for s in routine.load().get("steps", []) if s["type"] == "service"]
    found = [s for s in units if subprocess.run(["systemctl", "is-active", "--quiet", s["unit"]]).returncode == 0]
    known = {s["unit"] for s in found}
    return found + [{"type": "service", "unit": unit} for unit in system_databases() if unit not in known]


# Database servers by their systemd unit. postgresql.service is only Ubuntu's umbrella over the clusters,
# so it's each postgresql@<version>-<cluster>.service that counts.
DATABASE_UNITS = re.compile(r"^(?:mysql|mariadb|redis-server|redis|valkey-server|mongod|postgresql@[\w.-]+)\.service$")


def system_databases():
    """The database servers running as system services."""
    try:
        out = subprocess.run(["systemctl", "list-units", "--type=service", "--state=active", "--no-legend", "--plain"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[0] for line in out.splitlines() if line.strip() and DATABASE_UNITS.match(line.split()[0])]


DOCKER_PORT = re.compile(r"(?:[\d.]+|\[::\]):(\d+)->")


def docker_running():
    """Whether Docker's daemon is up: the system's, or your own (rootless, Docker Desktop). Asking docker
    itself would start it when it's left to start on first use."""
    try:
        return (subprocess.run(["systemctl", "is-active", "--quiet", "docker.service"], timeout=5).returncode == 0 or
                subprocess.run(["systemctl", "--user", "is-active", "--quiet", "docker.service", "docker-desktop.service"],
                               timeout=5).returncode == 0)
    except (OSError, subprocess.SubprocessError):
        return False


def containers():
    """The Docker containers that are running, from the docker command, so with your own rights:
    nothing if Docker isn't installed, isn't running, or you aren't in the docker group."""
    if not shutil.which("docker") or not docker_running():
        return []
    try:
        out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{json .}}"],
                             capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        name = info.get("Names", "").split(",")[0]
        if not name:
            continue
        ports = DOCKER_PORT.findall(info.get("Ports", ""))
        labels = dict(pair.split("=", 1) for pair in info.get("Labels", "").split(",") if "=" in pair)
        found.append({"unit": f"docker:{name}", "name": name, "command": info.get("Image", ""),
                      "port": int(ports[0]) if ports else None,
                      "cwd": labels.get("com.docker.compose.project.working_dir", "")})
    return found


def container_stopped_by_you(name):
    """Whether a container that's gone exited the way `docker stop` or Ctrl+C leaves it, rather than crashing."""
    if not docker_running():
        return True  # Docker itself was stopped, and its containers with it
    try:
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.ExitCode}} {{.State.OOMKilled}}", name],
                             capture_output=True, text=True, timeout=5).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return True
    if len(out) != 2:
        return True  # removed, as `docker compose down` does
    return out[1] == "false" and out[0] in ("0", "130", "137", "143")


def files_locations(bus):
    try:
        result = bus.call_sync("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                               "org.freedesktop.DBus.Properties", "Get",
                               GLib.Variant("(ss)", ("org.freedesktop.FileManager1", "OpenLocations")),
                               GLib.VariantType("(v)"), Gio.DBusCallFlags.NO_AUTO_START, 2000, None)
        return [uri for uri in result.unpack()[0] if uri.startswith("file://")]
    except GLib.Error:
        return []  # Files isn't running


def snapshot(bus, dock_windows, mode):
    table = processes()
    windows = [{k: w[k] for k in WINDOW_FIELDS if k in w} for w in dock_windows or [] if w.get("app")]
    apps = sorted({w["app"] for w in windows} - SPECIAL_APPS)
    return {
        "time": time.time(),
        "session": current_session(),
        "mode": mode,
        "windows": windows,
        "terminals": terminals(table, windows),
        "servers": servers(),
        "databases": databases(),
        "chrome": any(comm == "chrome" for _ppid, comm in table.values()),
        "files": files_locations(bus),
        "apps": apps,
    }


def weight(snap):
    return (len(all_tabs(snap)) + len(snap.get("windows", [])) + len(snap["servers"]) + len(snap["apps"]) +
            len(snap["files"]) + int(snap["chrome"]))


# ---- remembering ----------------------------------------------------------------------

def load():
    try:
        return json.loads(SESSION_FILE.read_text())
    except (OSError, ValueError):
        return {"snapshots": []}


def record(snap):
    data = load()
    snaps = data.get("snapshots", [])
    # This session's latest few, and the previous sessions' final ones (until restored or replaced).
    others = [s for s in snaps if s.get("session") != snap["session"]][-KEEP:]
    mine = [s for s in snaps if s.get("session") == snap["session"]][-(KEEP - 1):] + [snap]
    data["snapshots"] = others + mine
    brain.write_json_atomic(SESSION_FILE, data)


def to_restore(session, again=False):
    """The previous session's desk, if it's recent and not restored yet (or even so, when asked `again`)."""
    data = load()
    previous = [s for s in data.get("snapshots", []) if s.get("session") and s["session"] != session]
    if not previous:
        return None
    last_session = max(previous, key=lambda s: s["time"])["session"]
    if last_session in data.get("restored", []) and not again:
        return None
    final = sorted((s for s in previous if s["session"] == last_session), key=lambda s: s["time"])[-FINAL:]
    best = max(final, key=lambda s: (weight(s), s["time"]))
    if time.time() - best["time"] > MAX_AGE_S or weight(best) == 0:
        return None
    return best


def mark_restored(snap):
    data = load()
    data["restored"] = (data.get("restored", []) + [snap["session"]])[-10:]
    brain.write_json_atomic(SESSION_FILE, data)


def summary(snap):
    """Short labels of what a restore brings back."""
    parts = []
    tabs = all_tabs(snap)
    claudes = [t for t in tabs if t.get("claude")]
    if claudes:
        parts.append(f"claude ×{len(claudes)}" if len(claudes) > 1 else "claude")
    plain = len(tabs) - len(claudes)
    if plain:
        parts.append(f"terminal ×{plain}" if plain > 1 else "terminal")
    parts += [routine.label({"type": "service", **d}) for d in snap["databases"]]
    parts += [routine.label({"type": "server", **s}) for s in snap["servers"]]
    if snap["chrome"]:
        parts.append("chrome")
    parts += [f"files: {Path(GLib.filename_from_uri(uri)[0]).name}" for uri in snap["files"][:2]]
    parts += [app.removesuffix(".desktop").split(".")[-1].split("_")[0] for app in snap["apps"]]
    return parts


# ---- bringing it back -----------------------------------------------------------------

claude_running = routine.claude_running


def inside(path, folder):
    return path == folder or path.startswith(folder.rstrip(os.sep) + os.sep)


# ---- servers you started yourself -----------------------------------------------------

PROJECT_MARKS = (".git", "package.json", "composer.json", "artisan", "pyproject.toml", "requirements.txt", "manage.py",
                 "Cargo.toml", "go.mod", "Gemfile")
HIGHEST_DEV_PORT = 32767  # above this, it's an editor's or a tool's own port, not something you'd open
NOT_A_DEV_SERVER = re.compile(r"\b(claude|code|cursor|antigravity|chrome|chromium|firefox|ollama|language-server|langserver|"
                              r"tsserver|intelephense|gopls|rust-analyzer|pylsp|mcp|copilot|jetbrains|idea|deskaid?)\b", re.I)
SHELLS = {"bash", "sh", "zsh", "fish", "dash"}
STOP_WAIT_S = 4


def project_root(path):
    """The project a folder belongs to: the nearest folder above it that looks like one, inside your home."""
    here, home = Path(path), Path.home()
    if here == home or not here.is_relative_to(home) or any(part.startswith(".") for part in here.relative_to(home).parts):
        return None
    for folder in (here, *here.parents):
        if folder == home:
            return None
        if any((folder / mark).exists() for mark in PROJECT_MARKS):
            return str(folder)
    return None


def listeners():
    """The TCP ports your own processes listen on, as {port: pid}."""
    try:
        out = subprocess.run(["ss", "-ltnpH"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found = {}
    for line in out.splitlines():
        parts, pid = line.split(), re.search(r"pid=(\d+)", line)
        if len(parts) >= 4 and pid and parts[3].rsplit(":", 1)[-1].isdigit():
            found.setdefault(int(parts[3].rsplit(":", 1)[-1]), int(pid.group(1)))
    return found


def _command_line(pid):
    return [part.decode(errors="replace") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part]


def _run_by_deskai(cgroup):
    """Whether a process's cgroup is one of the services DeskAI runs dev servers as. Not just anything with its name
    in: a terminal it opened for you has a scope of its own, and that's where you start your own servers."""
    return "/deskai-" in cgroup


def hand_started():
    """Dev servers running from a project that DeskAI didn't start: [{unit, cwd, command, port, pid}].
    `cwd` is the project, and `command` what you typed, if that's still around ("npm run dev", not the node under it)."""
    found = []
    for port, pid in listeners().items():
        if port > HIGHEST_DEV_PORT:
            continue
        try:
            root = project_root(os.readlink(f"/proc/{pid}/cwd"))
            words = _command_line(pid)
            if not root or _run_by_deskai(Path(f"/proc/{pid}/cgroup").read_text()) or NOT_A_DEV_SERVER.search(" ".join(words)):
                continue
        except (OSError, ProcessLookupError):
            continue  # gone already, or not yours to look at
        try:
            leader = _command_line(os.getpgid(pid))
        except (OSError, ProcessLookupError):
            leader = []  # what started it has gone, and the server carries on
        typed = leader if leader and os.path.basename(leader[0]) not in SHELLS else words
        found.append({"unit": f"manual-{port}", "cwd": root, "command": shlex.join(typed)[:300], "port": port, "pid": pid})
    return found


def _whole_job_is_safe(pid):
    """Whether stopping everything in its process group only stops the server. A job typed into a terminal has a
    group of its own, but one started by a tool can share the tool's, and that must never go down with it."""
    pgid, sid = os.getpgid(pid), os.getsid(pid)
    if pgid in (sid, os.getpgrp()):
        return False
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if os.getpgid(int(entry.name)) == pgid and NOT_A_DEV_SERVER.search(" ".join(_command_line(entry.name))):
                return False
        except (OSError, ProcessLookupError):
            continue
    return True


def stop_hand_started(item):
    """Stops a server you started yourself, if it's still the same one on that port. Returns whether the port's free."""
    port, pid = item["port"], item["pid"]
    if listeners().get(port) != pid:
        return port not in listeners()  # something else took the port since: not ours to stop
    try:
        if _whole_job_is_safe(pid):
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError) as e:
        log.warning("servers: can't stop :%s: %s", port, e)
    deadline = time.monotonic() + STOP_WAIT_S
    while time.monotonic() < deadline:
        if port not in listeners():
            return True
        time.sleep(0.2)
    return False


def project_terminals(folder):
    """The terminal tabs with a Claude Code conversation open in this project: [(Claude's pid, the shell's pid, conversation)]."""
    table = processes()
    found = []
    for info in CLAUDE_SESSIONS.glob("*.json"):
        try:
            pid, data = int(info.stem), json.loads(info.read_text())
        except (OSError, ValueError):
            continue
        if pid not in table or not data.get("sessionId") or not inside(data.get("cwd", ""), folder):
            continue
        shell = pid
        while shell in table and table.get(table[shell][0], (0, ""))[1] not in TERMINAL_AGENTS:
            shell = table[shell][0]
        if shell in table:
            found.append((pid, shell, data["sessionId"]))
    return found


def close_terminal(claude_pid, shell_pid, conversation):
    """Closes the tab: its conversation is on disk and can be resumed. False if it's no longer that tab."""
    try:
        if json.loads((CLAUDE_SESSIONS / f"{claude_pid}.json").read_text()).get("sessionId") != conversation:
            return False
    except (OSError, ValueError):
        return False
    try:
        done = subprocess.run([sys.executable, "-I", str(TAB_READER), "--close", clean_title(ai_title(conversation)),
                               str(claude_pid), str(shell_pid)], capture_output=True, timeout=10)
        if done.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    # No way to reach the window from here: end its processes at least, and the tab says so.
    try:
        os.kill(shell_pid, signal.SIGHUP)
        os.kill(claude_pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def running(comm):
    return any(c == comm for _ppid, c in processes().values())


class Dock:
    """The dock's window calls, made from the restore's thread."""

    def __init__(self):
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, method, signature=None, args=None):
        try:
            return self.bus.call_sync(brain.BUS_NAME, brain.OBJECT_PATH, brain.BUS_NAME, method,
                                      GLib.Variant(signature, args) if signature else None, None,
                                      Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
        except GLib.Error as e:
            log.warning("restore: dock %s failed: %s", method, e.message)
            return None

    def windows(self):
        raw = self._call("GetWindows")
        return json.loads(raw) if raw else None

    def place(self, window_id, spec):
        return self._call("PlaceWindow", "(us)", (window_id, json.dumps(spec)))

    def select_tab(self, window_id, index):
        return self._call("SelectTab", "(uu)", (window_id, index))

    def raise_windows(self, ids):
        return self._call("RaiseWindows", "(s)", (json.dumps(ids),))


def browser_command(snap):
    """How you start your browser: its launcher's command with your flags in it, as if you'd clicked it."""
    used = [w["app"] for w in snap.get("windows", []) if w.get("app") in BROWSER_APPS]
    for app in [*used, *BROWSER_APPS]:
        info = launcher.app_info(app)
        command = info.get_commandline() if info else None
        if command:
            # Without the launcher's field codes (and Flatpak's, around them).
            return [arg for arg in GLib.shell_parse_argv(command)[1] if not re.fullmatch(r"%[a-zA-Z]|@@u?", arg)]
    return None


def place_spec(saved):
    """How the dock should put a window back (older snapshots only knew maximized or not)."""
    spec = {k: saved[k] for k in ("workspace", "sticky", "monitor", "rect", "fullscreen", "minimized", "above")
            if k in saved}
    spec["max_h"] = saved.get("max_h", saved.get("maximized", False))
    spec["max_v"] = saved.get("max_v", saved.get("maximized", False))
    return spec


def wait_for_window(dock, app, known, timeout=APPEAR_S):
    """The first window of that app that isn't one of the known ids, once it shows up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new = [w for w in dock.windows() or [] if w["app"] == app and w["id"] not in known]
        if new:
            return min(new, key=lambda w: w["id"])
        time.sleep(0.25)
    return None


def window_showing(windows, entry, taken):
    """The open terminal window showing this entry's conversation, when you reopened it yourself."""
    names = {clean_title(tab.get("title") or ai_title(tab["claude"])) for tab in entry["tabs"]
             if tab.get("claude") and claude_running(tab["claude"])} - {""}
    return next((w for w in windows if w["app"] == PTYXIS and w["id"] not in taken and clean_title(w["title"]) in names),
                None)


def title_match(old, new):
    """2 for the same title, 1 for the same once counters and spinners are gone ("(13) WhatsApp")."""
    if old == new:
        return 2
    plain = lambda t: re.sub(r"^\(\d+\)\s*", "", clean_title(t)).casefold()  # noqa: E731
    return 1 if plain(old) == plain(new) else 0


def pair_windows(old_windows, new_windows):
    """Saved windows with the app's windows now: by title first, then in the order they opened."""
    pairs, left = [], sorted(new_windows, key=lambda w: w["id"])
    todo = sorted(old_windows, key=lambda w: w.get("id", 0))  # a snapshot doesn't list them in that order
    for score in (2, 1):
        for old in list(todo):
            new = next((n for n in left if title_match(old["title"], n["title"]) >= score), None)
            if new:
                pairs.append((old, new))
                left.remove(new)
                todo.remove(old)
    pairs += zip(todo, left)
    return pairs


def restore(snap, progress, set_mode, labels=None):
    """progress(label, state) with state in running, done, skipped, failed."""
    labels = {"windows": "windows", **(labels or {})}
    plan = routine.load()
    env = {**os.environ, "PATH": plan.get("path") or routine.tool_path(routine.find_claude())}
    dock = Dock()
    saved = snap.get("windows", [])
    placed = {}  # index in saved → the id of the window that took its place
    at_start = dock.windows()
    have_dock = at_start is not None and all("id" in w for w in at_start)  # a dock that can place windows

    def step(label, fn):
        progress(label, "running")
        try:
            progress(label, fn() or "done")
        except Exception as e:  # one thing that won't come back mustn't keep the rest from coming back
            log.warning("restore: %s failed: %s", label, e)
            progress(label, "failed")

    if snap.get("mode"):
        set_mode(snap["mode"])

    # The database and the servers, the routine's way (skipped when already up).
    steps = ([{"type": "service", **d} for d in snap["databases"]] +
             [{"type": "server", **s} for s in snap["servers"]])
    if steps:
        routine.run({**plan, "steps": steps}, lambda s, state: progress(routine.label(s), state), set_mode)

    if snap["chrome"]:
        def chrome():
            if running("chrome"):
                return "skipped"
            command = browser_command(snap)
            if not command:
                return "failed"
            subprocess.Popen(launcher.own_scope([*command, "--restore-last-session"]), env=env, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        step("chrome", chrome)

    if snap["files"]:
        def files():
            Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
                "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1",
                "ShowFolders", GLib.Variant("(ass)", (snap["files"], "")), None, Gio.DBusCallFlags.NONE, 10000, None)
        step("files", files)

    open_apps = {w["app"] for w in at_start or []}
    for app in snap["apps"]:
        def launch(app=app):
            if app in open_apps:
                return "skipped"  # already up (it started with the session)
            return None if launcher.open_app(app) else "failed"
        step(app.removesuffix(".desktop").split(".")[-1].split("_")[0], launch)

    windows = terminal_windows(snap)
    if windows:
        def terminal():
            ptyxis = shutil.which("ptyxis")
            if not ptyxis:
                return "failed"
            # Ptyxis would also reopen its own saved tabs, as plain shells next to the
            # resumed conversations. With nothing open yet, set its saved session aside.
            saved_session = Path.home() / ".config" / "org.gnome.Ptyxis" / "session.gvariant"
            if saved_session.exists() and not running("ptyxis"):
                saved_session.replace(saved_session.with_name("session.gvariant.deskai-bak"))

            def open_tab(tab, where):
                cwd = tab["cwd"] if os.path.isdir(tab["cwd"]) else str(Path.home())
                command = []
                if tab.get("claude"):
                    resume = shlex.join([tab["exe"], "--resume", tab["claude"], *tab["flags"]])
                    command = ["-x", f"bash -c {shlex.quote(resume + '; exec bash')}"]
                subprocess.Popen(launcher.own_scope([ptyxis, where, "-d", cwd, *command]), env=env, cwd=cwd, start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            taken = set()
            for entry in windows:
                # Conversations you already reopened stay where they are: resuming one twice
                # would fork it. Their window just goes back to its place.
                tabs = [tab for tab in entry["tabs"] if not (tab.get("claude") and claude_running(tab["claude"]))]
                now_open = dock.windows() or [] if have_dock else []
                win = window_showing(now_open, entry, taken)
                reused = bool(win)
                if not win:
                    if not tabs:
                        continue
                    known = {w["id"] for w in now_open}
                    open_tab(tabs[0], "--new-window")
                    win = wait_for_window(dock, PTYXIS, known) if have_dock else None
                    tabs = tabs[1:]
                if win:
                    taken.add(win["id"])
                for tab in tabs:
                    # A new tab goes to the terminal window in front: make sure it's this one.
                    if win:
                        dock.raise_windows([win["id"]])
                    time.sleep(0.3 if win else 0.8)
                    open_tab(tab, "--tab")
                    time.sleep(0.6)
                if not win:
                    time.sleep(0.8)  # no dock to watch: one window at a time, by the clock
                    continue
                index = entry.get("window")
                if index is not None and index < len(saved):
                    placed[index] = win["id"]
                    dock.place(win["id"], place_spec(saved[index]))
                active = entry.get("active", 0)
                count = len(entry["tabs"])
                if not reused and count > 1 and active != count - 1 and gsetting_is("org.gnome.Ptyxis.Shortcuts",
                                                                                    f"focus-tab-{active + 1}",
                                                                                    f"<alt>{active + 1}"):
                    dock.select_tab(win["id"], active)
                    time.sleep(0.4)
        step("terminal", terminal)

    if saved and have_dock:
        step(labels["windows"], lambda: place_windows(dock, saved, placed))

    mark_restored(snap)
    if snap["servers"]:
        routine.write_state(done=datetime.date.today().isoformat())  # the routine's work is back too


def gsetting_is(schema, key, value):
    source = Gio.SettingsSchemaSource.get_default()
    if not source or not source.lookup(schema, True):
        return False
    return Gio.Settings.new(schema).get_string(key) == value


def place_windows(dock, saved, placed, timeout=None):
    """Puts the other apps' windows back once they've opened them, then stacks everything as it was."""
    wanted = {i: w for i, w in enumerate(saved) if i not in placed and w["app"] != PTYXIS}
    if timeout is None:
        timeout = max(PLACE_S, len(wanted) * PLACE_S_PER_WINDOW)
    deadline = time.monotonic() + timeout
    while wanted and time.monotonic() < deadline:
        current = [w for w in dock.windows() or [] if w["id"] not in placed.values()]
        for app in {w["app"] for w in wanted.values()}:
            mine = [(i, w) for i, w in wanted.items() if w["app"] == app]
            theirs = [w for w in current if w["app"] == app]
            # Until the app has opened as many windows as it had (Chrome restores them one by one).
            if not theirs or len(theirs) < len(mine) and time.monotonic() < deadline - 3:
                continue
            by_title = {id(w): i for i, w in mine}
            for old, new in pair_windows([w for _i, w in mine], theirs):
                i = by_title[id(old)]
                spec = place_spec(old)
                # Only the same window gets this back. On another it stays over your desk, and GNOME won't
                # focus what opens under it, so wl-paste waits forever and pasting a picture hangs.
                if not title_match(old["title"], new["title"]):
                    spec.pop("above", None)
                dock.place(new["id"], spec)
                placed[i] = new["id"]
                del wanted[i]
        if wanted:
            time.sleep(0.5)
    # Bottom to top as before; the window you were in last, on top and focused.
    if placed and all("stack" in saved[i] for i in placed):
        order = sorted(placed, key=lambda i: (bool(saved[i].get("focused")), saved[i]["stack"]))
        time.sleep(1)  # after the placing settles
        dock.raise_windows([placed[i] for i in order])
    for w in wanted.values():
        log.info("restore: no window came back for %s (%s)", w["app"], w["title"])
    return "done" if placed else "failed"
