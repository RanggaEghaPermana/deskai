"""The workday routine: set the desk up the way you left it on your last workday.

`learn()` reads what you did on your last workday and turns it into steps:
  - the project you worked in (the folder your Claude Code sessions ran in, most activity
    during work hours) and the conversation you were last in, to resume;
  - the dev servers started for that project (the background commands Claude ran when you
    asked it to start the servers), and the database its .env points at, if it's a local
    system service;
  - the localhost page you used most that day in Chrome;
  - the dock mode you spent the most time in.
`run()` performs them: database first, servers as user services (so they keep running
and their logs are in the journal), the page once its server answers, Claude resumed in a
new terminal window. Steps that are already done (a server that already answers, a
running database) are skipped.

The plan is saved to ~/.config/deskai/routine.json. Edit it and set "pinned": true to
keep your own version instead of relearning each morning.
"""

import datetime
import json
import os
import pwd
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

import brain
import launcher
import projects as found_projects
from brain import log

ROUTINE_FILE = brain.CONFIG_DIR / "routine.json"
STATE_FILE = brain.STATE_DIR / "routine-state.json"
LOG_DIR = brain.STATE_DIR / "routine-logs"  # servers that couldn't run as user services
SHELL_WAIT_S = 5
SERVER_SETTLE_S = 2  # a server with no port to ask: still running after this long is as good as it gets

_shell_path = None
CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CHROME_HISTORY = Path.home() / ".config" / "google-chrome" / "Default" / "History"

SETTINGS = {
    "enabled": True,
    "days": [1, 2, 3, 4, 5],  # ISO weekdays: Monday to Friday
    "from": "07:00",
    "until": "17:00",
    "pinned": False,
}
WORK_HOURS = (6, 19)  # activity that counts as "work" when learning
LOOKBACK_DAYS = 10
PROJECT_LOOKBACK_DAYS = 60     # asked for a project by name: its last day could be a while back
SERVER_LOOKBACK_DAYS = 21

# Background commands that start a dev server (or a worker next to it).
DEV_SERVER = re.compile(
    r"^(PHP_CLI_SERVER_WORKERS=\d+ )?php[\d.]* (-S \S+|artisan (serve|queue:(work|listen)|reverb:start)\b)"
    r"|^(npm|pnpm|yarn|bun) (run )?dev\b|^npx vite\b|^vite\b|^composer run dev\b"
    r"|^(python3? )?manage\.py runserver\b|^uvicorn |^flask run\b|^rails s(erver)?\b")
# Local databases a project's .env can point at, and their system services.
DB_SERVICES = {"pgsql": ["postgresql.service"], "postgres": ["postgresql.service"],
               "mysql": ["mysql.service", "mariadb.service"], "mariadb": ["mariadb.service", "mysql.service"]}
DB_KEYS = re.compile(r"^(DB_CONNECTION|DB_HOST|DB_PORT)=(.*)$")


def local_tz():
    return datetime.datetime.now().astimezone().tzinfo


def parse_ts(value):
    try:
        return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(local_tz())
    except (AttributeError, ValueError):
        return None


def project_dirs():
    """Claude Code's per-folder transcript dirs, except your home folder (that's not a project)."""
    home = "-" + str(Path.home()).strip("/").replace("/", "-")
    return [d for d in CLAUDE_PROJECTS.glob("*") if d.is_dir() and d.name != home]


TS_RE = re.compile(r'"timestamp":"([^"]+)"')
CWD_RE = re.compile(r'"cwd":"([^"]+)"')
SESSION_RE = re.compile(r'"sessionId":"([^"]+)"')
PERM_RE = re.compile(r'"permissionMode":"([^"]+)"')


def project_folders():
    """Your projects, newest first: the folder each one's Claude Code sessions ran in → their transcripts."""
    found = {}
    dated = [(max((p.stat().st_mtime for p in d.glob("*.jsonl")), default=0), d) for d in project_dirs()]
    for _mtime, folder in sorted(dated, key=lambda pair: pair[0], reverse=True):
        for path in folder.glob("*.jsonl"):
            with open(path, encoding="utf-8", errors="replace") as f:
                cwd = next((m.group(1) for m in map(CWD_RE.search, f) if m), None)
            if cwd and os.path.isdir(cwd):
                found.setdefault(cwd, folder)
                break
    return found


def scan_sessions(since, folders=None):
    """Every transcript entry since `since`: (local time, cwd, session id, permission mode, file)."""
    entries = []
    for folder in project_dirs() if folders is None else folders:
        for path in folder.glob("*.jsonl"):
            if datetime.datetime.fromtimestamp(path.stat().st_mtime, local_tz()) < since:
                continue
            perm = None
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    ts = TS_RE.search(line)
                    cwd = CWD_RE.search(line)
                    if not ts or not cwd:
                        continue
                    when = parse_ts(ts.group(1))
                    if not when or when < since:
                        continue
                    session = SESSION_RE.search(line)
                    mode = PERM_RE.search(line)
                    if mode:
                        perm = mode.group(1)
                    entries.append((when, cwd.group(1), session.group(1) if session else path.stem, perm, path))
    return entries


def last_workday(entries, today, days):
    """The most recent workday before today with work-hours activity in a project."""
    for back in range(1, LOOKBACK_DAYS + 1):
        day = today - datetime.timedelta(days=back)
        if day.isoweekday() not in days:
            continue
        if any(e[0].date() == day and WORK_HOURS[0] <= e[0].hour < WORK_HOURS[1] for e in entries):
            return day
    return None


def server_commands(project, since):
    """The dev servers last started for a project: the background commands of the latest batch."""
    folder = CLAUDE_PROJECTS / ("-" + project.strip("/").replace("/", "-"))
    found = []  # (time, command)
    for path in folder.glob("*.jsonl"):
        if datetime.datetime.fromtimestamp(path.stat().st_mtime, local_tz()) < since:
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"run_in_background":true' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                when = parse_ts(entry.get("timestamp"))
                for block in entry.get("message", {}).get("content", []) or []:
                    if not isinstance(block, dict) or block.get("name") != "Bash":
                        continue
                    command = block.get("input", {}).get("command", "").strip()
                    if when and command:
                        found.append((when, command))
    servers = []
    for when, command in sorted(found):
        if "\n" in command:
            continue
        # "kill 123; cd /project && php8.4 -S …": the server is the last part, run where the cd went.
        cwd, parts = project, re.split(r"\s*(?:&&|;)\s*", command.strip())
        for part in parts[:-1]:
            cd = re.match(r"^cd\s+(\S+)$", part)
            if cd:
                cwd = os.path.expanduser(cd.group(1))
        bare = parts[-1]
        if not DEV_SERVER.search(bare) or not os.path.isdir(cwd):
            continue
        servers.append({"when": when, "cwd": os.path.realpath(cwd), "command": bare, "port": guess_port(bare)})
    if not servers:
        return []
    # The day servers were last started, and for each port (or kind of server) the last command
    # that day: when Claude had to restart one differently (say php8.4, for the database driver),
    # that's the one that worked.
    day = servers[-1]["when"].date()
    latest = {}
    for s in servers:
        if s["when"].date() == day:
            latest[s["port"] or re.sub(r"[\d.]+", "", s["command"].split()[0])] = s
    return [{"cwd": s["cwd"], "command": s["command"], "port": s["port"]} for s in latest.values()]


def guess_port(command):
    m = re.search(r"(?:-S\s+[\w.]+:|--port[= ])(\d{2,5})", command)
    if m:
        return int(m.group(1))
    if re.search(r"\b(npm|pnpm|yarn|bun) (run )?dev\b|\bvite\b", command):
        return 5173
    if "artisan serve" in command:
        return 8000
    if "runserver" in command:
        return 8000
    return None


def database_service(project):
    """The system service of the local database the project's .env uses. Reads only the DB_ keys."""
    env = Path(project) / ".env"
    if not env.exists():
        return None
    values = {}
    for line in env.read_text(errors="replace").splitlines():
        m = DB_KEYS.match(line.strip())
        if m:
            values[m.group(1)] = m.group(2).strip().strip("'\"")
    if values.get("DB_HOST", "127.0.0.1") not in ("127.0.0.1", "localhost", "::1"):
        return None
    port = int(values["DB_PORT"]) if values.get("DB_PORT", "").isdigit() else None
    if values.get("DB_CONNECTION") in ("pgsql", "postgres"):
        cluster = postgres_cluster(port or 5432)
        if cluster:
            return {"unit": cluster, "port": port or 5432}
    for unit in DB_SERVICES.get(values.get("DB_CONNECTION", ""), []):
        if subprocess.run(["systemctl", "cat", unit], capture_output=True).returncode == 0:
            return {"unit": unit, "port": int(values["DB_PORT"]) if values.get("DB_PORT", "").isdigit() else None}
    return None


def postgres_cluster(port):
    """The unit of the Debian/Ubuntu PostgreSQL cluster on `port`. The postgresql.service umbrella
    only starts clusters set to "auto" in their start.conf; a "manual" one needs its own unit."""
    try:
        out = subprocess.run(["pg_lsclusters", "--no-header"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        fields = line.split()  # Ver Cluster Port Status Owner Data-directory Log-file
        if len(fields) >= 3 and fields[2] == str(port):
            return f"postgresql@{fields[0]}-{fields[1]}.service"
    return None


def chrome_page(day, ports):
    """The localhost page on one of `ports` you visited most that day."""
    if not CHROME_HISTORY.exists() or not ports:
        return None
    start = datetime.datetime.combine(day, datetime.time(), local_tz())
    to_chrome = lambda t: int((t.timestamp() + 11644473600) * 1_000_000)  # microseconds since 1601
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "History"
        shutil.copy2(CHROME_HISTORY, copy)  # Chrome keeps it locked
        db = sqlite3.connect(copy)
        try:
            rows = db.execute(
                "SELECT u.url FROM visits v JOIN urls u ON u.id = v.url "
                "WHERE (u.url LIKE 'http://localhost:%' OR u.url LIKE 'http://127.0.0.1:%') "
                "AND v.visit_time >= ? AND v.visit_time < ?",
                (to_chrome(start), to_chrome(start + datetime.timedelta(days=1)))).fetchall()
        finally:
            db.close()
    counts = Counter()
    for (url,) in rows:
        m = re.match(r"^(http://(?:localhost|127\.0\.0\.1):(\d+)(/[^?#]*)?)", url)
        if m and int(m.group(2)) in ports:
            counts[m.group(1)] += 1
    return counts.most_common(1)[0][0] if counts else None


def usual_mode(day):
    """The focus mode you spent most time in that day (or the nearest earlier day the dock knows)."""
    modes = None
    days = [day - datetime.timedelta(days=back) for back in range(LOOKBACK_DAYS)]
    days += sorted({datetime.date.fromisoformat(p.stem) for p in (brain.STATE_DIR / "stats").glob("*.json")
                    if p.stem < datetime.date.today().isoformat()} - set(days), reverse=True)
    for when in days:
        path = brain.STATE_DIR / "stats" / f"{when.isoformat()}.json"
        try:
            modes = json.loads(path.read_text())["modes"]
            break
        except (OSError, ValueError, KeyError):
            continue
    if not modes:
        return None
    config = brain.Config()
    config.refresh()
    focus = {key: sum(bucket.values()) for key, bucket in modes.items() if (config.mode(key) or {}).get("focus")}
    return max(focus, key=focus.get) if focus else None


def claude_running(session_id):
    """Whether that conversation is already open somewhere (resuming it twice would fork it)."""
    for info in (Path.home() / ".claude" / "sessions").glob("*.json"):
        try:
            if json.loads(info.read_text()).get("sessionId") == session_id and Path(f"/proc/{info.stem}").exists():
                return True
        except (OSError, ValueError):
            continue
    return False


def find_claude():
    for candidate in [shutil.which("claude"), str(Path.home() / ".local/bin/claude"),
                      *sorted(map(str, Path.home().glob(".nvm/versions/node/*/bin/claude")), reverse=True)]:
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    return "claude"


def shell_path():
    """PATH as your terminal has it, which is where fnm, Volta, asdf and the like put themselves. Asked once:
    your shell's startup files can take a moment. It answers in a file, past anything they print."""
    global _shell_path
    if _shell_path is None:
        answered = ""
        try:
            with tempfile.NamedTemporaryFile("r") as answer:
                subprocess.run([pwd.getpwuid(os.getuid()).pw_shell, "-lic", 'printf %s "$PATH" > "$DESKAI_ANSWER"'],
                               env={**os.environ, "DESKAI_ANSWER": answer.name}, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, timeout=SHELL_WAIT_S)
                answered = answer.read().strip()
        except (OSError, KeyError, subprocess.SubprocessError) as e:
            log.warning("routine: can't ask your shell for its PATH: %s", e)
        _shell_path = answered
    return _shell_path


def tool_path(claude):
    """PATH for the servers and Claude: the node install Claude lives in, what your terminal has, then
    wherever else a tool may be if your shell had nothing to say."""
    node = sorted(Path.home().glob(".nvm/versions/node/*/bin"), key=lambda path: [int(n) for n in re.findall(r"\d+", path.parent.name)])
    dirs = [os.path.dirname(claude), *shell_path().split(":"), *(str(path) for path in node[-1:]), str(Path.home() / ".local/bin"),
            str(Path.home() / ".bun/bin"), "/usr/local/sbin", "/usr/local/bin", "/usr/sbin", "/usr/bin", "/sbin", "/bin",
            *os.environ.get("PATH", "").split(":")]
    return ":".join(dict.fromkeys(d for d in dirs if d))


# ---- the plan ------------------------------------------------------------------

def load():
    try:
        data = json.loads(ROUTINE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    return {**SETTINGS, **data}


def save(plan):
    brain.write_json_atomic(ROUTINE_FILE, plan)


def learn(today=None, project=None):
    """A fresh plan from your last workday, or from your last day in `project`, keeping your settings.
    None when there's nothing to go on."""
    plan = load()
    today = today or datetime.date.today()
    back = PROJECT_LOOKBACK_DAYS if project else LOOKBACK_DAYS
    since = datetime.datetime.combine(today - datetime.timedelta(days=back), datetime.time(), local_tz())
    if project:
        folder = project_folders().get(project)
        entries = [e for e in scan_sessions(since, [folder]) if e[1] == project] if folder else []
        if not entries:
            return _plan_from_files(plan, project, today)
        day = max(e[0] for e in entries).date()
    else:
        entries = scan_sessions(since)
        day = last_workday(entries, today, plan["days"])
        if not day:
            last = read_state().get("last_project")
            return _plan_from_files(plan, last, today) if last and os.path.isdir(last) else None
        work = [e for e in entries if e[0].date() == day and WORK_HOURS[0] <= e[0].hour < WORK_HOURS[1]]
        project = Counter(e[1] for e in work).most_common(1)[0][0]
    # The conversation you were in last in that project (what `claude --continue` would pick).
    last = max((e for e in entries if e[1] == project), key=lambda e: e[0])

    claude = find_claude()
    steps = []
    mode = usual_mode(day)
    if mode:
        steps.append({"type": "mode", "mode": mode})
    db = database_service(project)
    if db:
        steps.append({"type": "service", **db})
    servers = server_commands(project, since - datetime.timedelta(days=SERVER_LOOKBACK_DAYS - LOOKBACK_DAYS))
    for server in servers:
        steps.append({"type": "server", **server})
    steps += _compose_step(project, servers)
    page = chrome_page(day, {s["port"] for s in servers if s["port"]})
    if page:
        steps.append({"type": "url", "url": page})
    claude_args = ["--resume", last[2]]
    if last[3] == "bypassPermissions":
        claude_args.append("--dangerously-skip-permissions")  # how you ran it
    steps.append({"type": "claude", "cwd": project, "command": [claude, *claude_args]})

    plan.update({"learned_from": day.isoformat(), "project": project,
                 "path": tool_path(claude), "steps": steps})
    return plan


def _compose_step(project, servers):
    """A project with a Compose file and no servers of its own runs through Docker."""
    return [{"type": "compose", "cwd": project}] if not servers and found_projects.has_compose(project) else []


def _plan_from_files(plan, project, today):
    """For a project Claude Code never saw: what its own files say it runs, then a terminal there."""
    steps = []
    mode = usual_mode(today)
    if mode:
        steps.append({"type": "mode", "mode": mode})
    db = database_service(project)
    if db:
        steps.append({"type": "service", **db})
    servers = found_projects.servers(project)
    steps += [{"type": "server", **server} for server in servers]
    steps += _compose_step(project, servers)
    port = next((server["port"] for server in servers if server["port"]), None)
    if port:
        steps.append({"type": "url", "url": f"http://127.0.0.1:{port}"})
    steps.append({"type": "terminal", "cwd": project})
    plan.update({"learned_from": today.isoformat(), "project": project, "path": tool_path(find_claude()), "steps": steps})
    return plan


def plan_for_today():
    """Today's plan: your pinned one, or relearned from your last workday (and saved)."""
    plan = load()
    if not plan.get("pinned") or not plan.get("steps"):
        learned = learn()
        if learned:
            plan = learned
            save(plan)
    return plan if plan.get("steps") else None


# ---- when to offer ----------------------------------------------------------------

def read_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def write_state(**values):
    brain.write_json_atomic(STATE_FILE, {**read_state(), **values})


def due(now=None):
    """Whether to offer the routine now: a workday, within the hours, not done or snoozed."""
    plan = load()
    now = now or datetime.datetime.now()
    if not plan.get("enabled") or now.isoweekday() not in plan["days"]:
        return False
    if not (plan["from"] <= now.strftime("%H:%M") < plan["until"]):
        return False
    state = read_state()
    if state.get("done") == now.date().isoformat():
        return False
    return time.time() >= state.get("snoozed_until", 0)


def polkit_rule(user, units):
    """A polkit rule letting `user`, at the machine, start and stop these system services without a password."""
    return f"""// DeskAI workday routine: let {user} start and stop the database it needs
// without typing a password each morning. Only these units, only start/stop/restart,
// only this user. (Not "local && active": the routine runs from the user's systemd
// manager, which polkit doesn't count as the active session.) Remove this file to undo.
polkit.addRule(function (action, subject) {{
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        subject.user == {json.dumps(user)} &&
        {json.dumps(units)}.indexOf(action.lookup("unit")) >= 0 &&
        ["start", "stop", "restart"].indexOf(action.lookup("verb")) >= 0) {{
        return polkit.Result.YES;
    }}
}});
"""


# ---- doing it ---------------------------------------------------------------------

def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def crashed(unit):
    """Whether a server's service has ended with an error, so it won't answer however long it's given. Not knowing
    counts as no, and so does ending without one: a command may just start something and return."""
    try:
        if subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], timeout=5).returncode == 0:
            return False
        said = subprocess.run(["journalctl", "--user", "-u", unit, "-n", "6", "-o", "cat", "--no-pager"],
                              capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Failed with result" in said.rsplit("Started ", 1)[-1]  # not an earlier run's


def wait_port(port, timeout=60, unit=None):
    """Whether the port answers in time. With the service behind it, gives up as soon as that has crashed."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return True
        if unit and crashed(unit):
            return False
        time.sleep(0.5)
    return False


def unit_name(project, command):
    slug = re.sub(r"[^a-z0-9]+", "-", f"{Path(project).name}-{command.split()[-1] if command else 'server'}".lower())
    return f"deskai-{slug.strip('-')[:60]}"


def start_server(step, env):
    """Starts a dev server as a user service, so it keeps running and its logs are in the journal.
    Returns the service's name, or None if it had to run as a plain process."""
    unit = unit_name(step["cwd"], step["command"])
    subprocess.run(["systemctl", "--user", "reset-failed", unit], capture_output=True)
    subprocess.run(["systemctl", "--user", "stop", unit], capture_output=True)
    result = subprocess.run(
        ["systemd-run", "--user", "--quiet", "--collect", f"--unit={unit}",
         f"--description=DeskAI: {step['command']}", f"--working-directory={step['cwd']}",
         f"--setenv=PATH={env['PATH']}", "bash", "-c", step["command"]],
        capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        # No user systemd to run it under: a plain background process, logging to a file.
        log.warning("routine: %s as a user service failed (%s), starting it directly", unit, result.stderr.strip())
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / f"{unit}.log", "ab") as out:
            subprocess.Popen(["bash", "-c", step["command"]], cwd=step["cwd"], env=env,
                             stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)
        return None
    return unit


def _settle(server, unit, progress):
    """Waits to see a server come up and says how it went: its port answers, or with no port to ask, it hasn't
    crashed a moment later."""
    if server.get("port"):
        up = wait_port(server["port"], 90, unit)
    else:
        time.sleep(SERVER_SETTLE_S)
        up = not (unit and crashed(unit))
    progress(server, "done" if up else "failed")
    return up


def _compose_up(project):
    """Starts a project through Docker, with what it needs from other projects first."""
    import compose  # not at the top: compose needs session, and session needs this module
    if not shutil.which("docker"):
        return False
    found = compose.projects([*found_projects.find(), project])
    ok, said, *_rest = compose.start(next(name for name, folder in found.items() if folder == project), found)
    if not ok:
        log.warning("routine: Docker couldn't start %s: %s", project, said)
    return ok


def terminal_args(cwd, inner):
    """A new terminal window at `cwd` running the shell command `inner`."""
    terminal = shutil.which("ptyxis") or shutil.which("gnome-terminal")
    if terminal and terminal.endswith("ptyxis"):
        return [terminal, "--new-window", "-d", cwd, "-x", f"bash -c {shlex.quote(inner)}"]
    return [terminal or "x-terminal-emulator", f"--working-directory={cwd}", "--", "bash", "-c", inner]


def already_set_up(plan):
    """Every server of the plan already answers."""
    ports = [s["port"] for s in plan["steps"] if s["type"] == "server" and s.get("port")]
    return bool(ports) and all(port_open(p) for p in ports)


def label(step):
    kind = step["type"]
    if kind == "service":
        return step["unit"].removesuffix(".service")
    if kind == "container":
        return f"{step['name']} :{step['port']}" if step.get("port") else step["name"]
    if kind == "server":
        command = step["command"]
        name = ("vite" if re.search(r"\b(dev|vite)\b", command) else
                "queue" if "queue:" in command else "reverb" if "reverb" in command else "server")
        return f"{name} :{step['port']}" if step.get("port") else name
    if kind == "url":
        return re.sub(r"^https?://", "", step["url"])
    if kind == "compose":
        return "docker compose"
    if kind in ("claude", "terminal"):
        return kind
    return step.get("mode", kind)


def run(plan, progress, set_mode, cancelled=lambda: False):
    """Performs the plan. progress(step, state) with state in running, done, skipped, failed.
    Stops before the next thing it would start or open once cancelled() says so."""
    env = {**os.environ, "PATH": plan.get("path") or os.environ.get("PATH", "")}
    servers = []
    for step in plan["steps"]:
        if cancelled():
            return
        kind = step["type"]
        progress(step, "running")
        try:
            if kind == "mode":
                set_mode(step["mode"])
                progress(step, "done")
            elif kind == "service":
                if subprocess.run(["systemctl", "is-active", "--quiet", step["unit"]]).returncode == 0:
                    progress(step, "skipped")
                    continue
                # Not ours to start silently: GNOME asks for your password (polkit) if it needs to.
                ok = subprocess.run(["systemctl", "start", step["unit"]], timeout=120).returncode == 0
                if ok and step.get("port"):
                    ok = wait_port(step["port"], 30)
                progress(step, "done" if ok else "failed")
            elif kind == "server":
                if step.get("port") and port_open(step["port"]):
                    progress(step, "skipped")
                    continue
                servers.append((step, start_server(step, env)))
            elif kind == "compose":
                progress(step, "done" if _compose_up(step["cwd"]) else "failed")
            elif kind == "url":
                # Open it once its server answers.
                port = int(re.search(r":(\d+)", step["url"]).group(1))
                down = {server.get("port") for server, unit in servers if not _settle(server, unit, progress)}
                servers = []
                answered = port not in down and wait_port(port, 30)
                if cancelled():
                    return
                if answered:
                    subprocess.Popen(launcher.own_scope(["gio", "open", step["url"]]), env=env, start_new_session=True)
                    progress(step, "done")
                else:
                    progress(step, "failed")
            elif kind == "claude":
                resumes = step["command"][step["command"].index("--resume") + 1] if "--resume" in step["command"] else None
                if resumes and claude_running(resumes):
                    progress(step, "skipped")
                    continue
                command = " ".join(shlex.quote(part) for part in step["command"])
                inner = f"{command}; exec bash"
                subprocess.Popen(launcher.own_scope(terminal_args(step["cwd"], inner)), env=env, cwd=step["cwd"], start_new_session=True)
                progress(step, "done")
            elif kind == "terminal":
                subprocess.Popen(launcher.own_scope(terminal_args(step["cwd"], "exec bash")), env=env, cwd=step["cwd"], start_new_session=True)
                progress(step, "done")
        except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as e:
            log.warning("routine: %s failed: %s", kind, e)
            progress(step, "failed")
    for server, unit in servers:  # no page waited on them
        _settle(server, unit, progress)
    write_state(done=datetime.date.today().isoformat())
