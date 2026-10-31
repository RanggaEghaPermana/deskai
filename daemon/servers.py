"""The dev servers and databases on your desk: what's up, and what just went down.

Everything DeskAI sees running gets watched until you stop it: its user services (deskai-*,
started by the routine, a restore or the command bar) and the routine's databases. `scan()`
looks at them every few seconds; one that stops answering is reported once, and the dock
offers to restart it or show its log.

Servers you started yourself in a project are listed too. Stopping one of those is usually you
pressing Ctrl+C, so it just leaves the list, with no alarm. So are running Docker containers and
the database servers on the system: a container you stop leaves the list, one that crashes is down.
"""

import difflib
import os
import re
import shlex
import subprocess
import threading
import time

import brain  # noqa: F401
import routine
import session
from brain import log

MANUAL = "manual"   # the kind of a server you started yourself
CONTAINER = "container"  # a Docker container
GRACE_S = 90        # a server just started gets this long to answer
MISSES = 2          # checks in a row without an answer before a running service counts as down
START_WAIT_S = 60   # how long a restart waits for the port
GIT_WAIT_S = 5      # how long closing a project waits to hear what isn't in git
STOP_SETTLE_S = 15  # a look that started before a stop may still see it running


class Watch:
    def __init__(self):
        self.items = {}  # unit → what's known about it
        self.stopped = {}  # unit → when you stopped it
        self.lock = threading.Lock()

    def _add(self, unit, kind, found, now):
        step = {**found, "type": "server" if kind == MANUAL else kind}
        self.items[unit] = {
            "unit": unit, "kind": kind, "label": routine.label(step), "command": found.get("command", ""),
            "cwd": found.get("cwd", ""), "port": found.get("port"), "state": "starting", "misses": 0,
            "started": now, "since": now,
        }

    def _scan_hand_started(self, now):
        found = {s["unit"]: s for s in session.hand_started()}
        with self.lock:
            for unit in [unit for unit, item in self.items.items() if item["kind"] == MANUAL and unit not in found]:
                del self.items[unit]
            for unit, server in found.items():
                if unit in self.stopped:
                    continue
                if unit not in self.items:
                    self._add(unit, MANUAL, server, now)
                self.items[unit].update(state="up", pid=server["pid"], command=server["command"])

    def scan(self):
        """Looks at everything once. Returns the items that just went down."""
        running = {s["unit"]: s for s in session.servers()}
        services = {d["unit"]: d for d in session.databases()}
        containers = {c["unit"]: c for c in session.containers()}
        now = time.time()
        down = []
        with self.lock:
            self.stopped = {unit: when for unit, when in self.stopped.items() if now - when < STOP_SETTLE_S}
            for unit, found in running.items():
                if unit not in self.items and unit not in self.stopped:
                    self._add(unit, "server", found, now)
            for unit, found in services.items():
                if unit not in self.items and unit not in self.stopped:
                    self._add(unit, "service", found, now)
            for unit, found in containers.items():
                if unit not in self.items and unit not in self.stopped:
                    self._add(unit, CONTAINER, found, now)
            gone = [unit for unit, item in self.items.items()
                    if item["kind"] == CONTAINER and unit not in containers and item["state"] != "down"]
        # A container you stopped yourself just leaves, like a server you pressed Ctrl+C on.
        for unit in gone:
            if session.container_stopped_by_you(container_name(unit)):
                self.forget(unit)
        self._scan_hand_started(now)
        with self.lock:
            items = [item for item in self.items.values() if item["kind"] != MANUAL]
        for item in items:
            active = item["unit"] in running or item["unit"] in services or item["unit"] in containers
            alive = routine.port_open(item["port"]) if item["port"] else active
            with self.lock:
                if self.items.get(item["unit"]) is not item:
                    continue  # stopped or forgotten meanwhile
                if alive:
                    state, item["misses"] = "up", 0
                elif not active:
                    state = "down"
                elif now - item["started"] < GRACE_S:
                    state = "starting"
                else:
                    item["misses"] += 1
                    state = "down" if item["misses"] >= MISSES else item["state"]
                if state != item["state"]:
                    item["since"] = now
                    if state == "down":
                        down.append(dict(item))
                item["state"] = state
        return down

    def summary(self):
        """What the dock shows, in a stable order."""
        with self.lock:
            return [{"unit": item["unit"], "kind": item["kind"], "label": item["label"], "state": item["state"],
                     "port": item["port"], "project": os.path.basename(item["cwd"]), "since": item["since"]}
                    for item in sorted(self.items.values(), key=lambda i: (i["kind"] == "server", i["label"]))]

    def get(self, unit):
        with self.lock:
            item = self.items.get(unit)
            return dict(item) if item else None

    def match(self, target):
        """The units a name refers to: "all", or part of a server's name, port or command."""
        target = (target or "").strip().lower()
        with self.lock:
            items = list(self.items.values())
        if target in ("", "all", "semua", "server", "servers"):
            return [i["unit"] for i in items]
        if target == "down":
            return [i["unit"] for i in items if i["state"] == "down"]
        words = [w for w in re.split(r"\s+", target) if w not in ("server", "the")] or [target]
        about = {i["unit"]: f"{i['label']} {i['unit']} {i['command']} {i['port'] or ''} {os.path.basename(i['cwd'])}".lower()
                 for i in items}
        exact = [unit for unit, text in about.items() if all(w in text for w in words)]
        if exact:
            return exact
        # A slip of the fingers: "restart gudnag".
        return [unit for unit, text in about.items()
                if all(difflib.get_close_matches(w, re.findall(r"[a-z0-9]+", text), cutoff=0.8) for w in words)]

    def forget(self, unit):
        with self.lock:
            self.items.pop(unit, None)

    def stop(self, unit):
        """Stops it for good: it's no longer expected to be up."""
        item = self.get(unit)
        if not item:
            return False
        with self.lock:
            self.items.pop(unit, None)
            self.stopped[unit] = time.time()
        if item["kind"] == MANUAL:
            return session.stop_hand_started(item)
        command = (["systemctl", "--user", "stop", unit] if item["kind"] == "server" else
                   ["docker", "stop", container_name(unit)] if item["kind"] == CONTAINER else ["systemctl", "stop", unit])
        return subprocess.run(command, capture_output=True, timeout=120).returncode == 0

    def hand_started_in(self, folder):
        """The servers you started yourself in this project."""
        with self.lock:
            return [dict(item) for item in self.items.values() if item["kind"] == MANUAL and session.inside(item["cwd"], folder)]

    def restart(self, unit):
        """Starts it again and waits for it to answer. Returns whether it did. One you started yourself
        comes back as DeskAI's own, since your terminal can't be asked to run it again."""
        item = self.get(unit)
        if not item:
            return False
        if item["kind"] == MANUAL and not self.stop(unit):
            return False
        with self.lock:
            live = self.items.get(unit)
            if live:
                live.update(state="starting", misses=0, started=time.time(), since=time.time())
        try:
            if item["kind"] in ("server", MANUAL):
                path = routine.load().get("path") or os.environ.get("PATH", "")
                routine.start_server(item, {**os.environ, "PATH": path})
            elif item["kind"] == CONTAINER:
                if subprocess.run(["docker", "restart", container_name(unit)], capture_output=True, timeout=120).returncode != 0:
                    return False
            elif subprocess.run(["systemctl", "restart", unit], capture_output=True, timeout=120).returncode != 0:
                return False  # no permission, or it failed to start
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("servers: can't restart %s: %s", unit, e)
            return False
        answered = routine.wait_port(item["port"], START_WAIT_S) if item["port"] else True
        with self.lock:
            live = self.items.get(unit)
            if live and answered:
                live.update(state="up", since=time.time())
        return answered


def close_project(watch, folder):
    """Stops a project's servers, and its database unless a project that's still running uses it too.
    Returns what happened: [(label, "stopped" | "failed" | "kept", the project keeping it, or "")]."""
    watch.scan()
    running = session.servers()
    mine = [s for s in running if session.inside(s["cwd"], folder)]
    others = [s for s in running if not session.inside(s["cwd"], folder)]
    report = []
    for server in mine:
        ok = watch.stop(server["unit"])
        report.append((routine.label({"type": "server", **server}), "stopped" if ok else "failed", ""))
    database = routine.database_service(folder)
    if database and subprocess.run(["systemctl", "is-active", "--quiet", database["unit"]]).returncode == 0:
        label = routine.label({"type": "service", **database})
        sharing = next((os.path.basename(s["cwd"]) for s in others
                        if (routine.database_service(s["cwd"]) or {}).get("unit") == database["unit"]), "")
        if sharing:
            report.append((label, "kept", sharing))
        else:
            ok = watch.stop(database["unit"]) if watch.get(database["unit"]) else \
                subprocess.run(["systemctl", "stop", database["unit"]], capture_output=True, timeout=120).returncode == 0
            report.append((label, "stopped" if ok else "failed", ""))
    return report


def _git(folder, *args):
    """A git command's output in this folder, or None if it has no answer: not a repository, no git, too slow."""
    try:
        done = subprocess.run(["git", "--no-optional-locks", "-C", folder, *args], capture_output=True, text=True,
                              errors="replace", timeout=GIT_WAIT_S)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def git_state(folder):
    """What of a project is only on this computer: (files not committed, commits on no remote), or None if git
    can't say. Nothing is fetched, so it knows the remotes as of the last fetch or push."""
    status = _git(folder, "status", "--porcelain", "--untracked-files=all", "--", ".")
    if status is None:
        return None
    unpushed = 0
    if _git(folder, "remote"):
        unpushed = int(_git(folder, "rev-list", "--count", "HEAD", "--not", "--remotes") or 0)
    return len(status.splitlines()), unpushed


def container_name(unit):
    return unit.removeprefix("docker:")


def log_command(item):
    """The journal of a server or database, or a container's own log, following along."""
    if item["kind"] == CONTAINER:
        return shlex.join(["docker", "logs", "-n", "200", "-f", container_name(item["unit"])])
    scope = ["--user"] if item["kind"] == "server" else []
    return shlex.join(["journalctl", *scope, "-u", item["unit"], "-n", "200", "-f"])


def open_log(item):
    cwd = item["cwd"] or os.path.expanduser("~")
    subprocess.Popen(routine.terminal_args(cwd, log_command(item)), cwd=cwd, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def last_log_line(item, limit=160):
    """The last thing it said, which is usually why it stopped."""
    if item["kind"] == MANUAL:
        return ""  # what it says goes to your terminal
    scope = ["--user"] if item["kind"] == "server" else []
    command = (["docker", "logs", "-n", "12", container_name(item["unit"])] if item["kind"] == CONTAINER else
               ["journalctl", *scope, "-u", item["unit"], "-n", "12", "-o", "cat", "--no-pager"])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        out = result.stdout + result.stderr if item["kind"] == CONTAINER else result.stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    # What it said itself, not systemd's own lines around it.
    skip = (item["unit"], "Started ", "Stopped ", "Starting ", "Stopping ", "Consumed ")
    lines = [line.strip() for line in out.splitlines() if line.strip() and not line.startswith(skip)]
    return lines[-1][:limit] if lines else ""
