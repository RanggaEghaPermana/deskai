"""Who has which port, so two things don't fight over one: what listens now, what Docker publishes, and
what is set to listen once started (Apache, the Compose projects, the system's database servers).

Nothing here needs root. Root's own sockets (Apache, MySQL) show up without a name, so the
configuration says whose they are.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import compose
import session

APACHE_CONFIG = Path("/etc/apache2")
# The ports a system service takes when it's installed, by its unit.
SERVICE_PORTS = {"mysql.service": [3306], "mariadb.service": [3306], "postgresql.service": [5432],
                 "redis-server.service": [6379], "mongod.service": [27017], "nginx.service": [80, 443]}
# Well-known listeners that aren't yours to start or stop: shown, never counted as a clash.
SYSTEM = {53: "DNS (systemd-resolved)", 631: "printer (CUPS)", 11434: "Ollama"}
WEB_PORTS = {80, 443, 3000, 4200, 5000, 5173, 8000, 8080, 8888}  # where a container's web server listens
EPHEMERAL = 32768  # Linux hands out ports from here up for programs that don't ask for one
VARIABLE = re.compile(r"\$\{(\w+)(?::?-([^}]*))?\}")


def _run(args, timeout=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def listening():
    """{port: program or ""}: the TCP ports something listens on, from ss. Root's show no program."""
    done = _run(["ss", "-ltnpH"])
    found = {}
    for line in (done.stdout if done else "").splitlines():
        fields = line.split()
        if len(fields) < 4 or ":" not in fields[3]:
            continue
        port = fields[3].rsplit(":", 1)[1]
        program = re.search(r'users:\(\("([^"]+)"', line)
        if port.isdigit():
            found.setdefault(int(port), program.group(1) if program else "")
    return found


def published():
    """{port: container}: what Docker publishes. Docker may forward with no listening socket, so ss misses it."""
    if not shutil.which("docker") or not session.docker_running():
        return {}
    done = _run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"])
    found = {}
    for line in (done.stdout if done else "").splitlines():
        name, _, ports = line.partition("\t")
        for port in re.findall(r":(\d+)->", ports):
            found.setdefault(int(port), name)
    return found


def compose_folders():
    """{container: the folder of the Compose project that started it}, from the label Compose puts on each one.
    Most Compose files don't name their containers, so the names in the file can't say whose a container is."""
    if not shutil.which("docker") or not session.docker_running():
        return {}
    done = _run(["docker", "ps", "--format", '{{.Names}}\t{{.Label "com.docker.compose.project.working_dir"}}'])
    found = {}
    for line in (done.stdout if done else "").splitlines():
        name, _, folder = line.partition("\t")
        if folder:
            found[name] = os.path.realpath(folder)
    return found


def _owner(container, found, folders):
    """The project a running container belongs to: Compose's own word, else its name in the Compose files."""
    folder = folders.get(container)
    labelled = next((name for name, path in found.items() if os.path.realpath(path) == folder), None)
    return labelled or compose.project_for(container, found)


def apache_ports():
    """The ports Apache listens on once started, from its configuration. Empty if it isn't installed."""
    files = [APACHE_CONFIG / "ports.conf", *sorted((APACHE_CONFIG / "sites-enabled").glob("*.conf"))]
    ports = set()
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        ports.update(int(port) for port in re.findall(r"^\s*Listen\s+(?:[\w.\[\]:]+:)?(\d+)", text, re.M))
    return sorted(ports)


def _resolve(text, env):
    return VARIABLE.sub(lambda m: env.get(m.group(1)) or m.group(2) or "", text)


def _env(folder):
    values = {}
    try:
        lines = (Path(folder) / ".env").read_text(errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"\s*\w+\s*", key):
            values[key.strip()] = value.strip().strip("'\"")
    return values


def project_ports(folder):
    """The host ports a Compose project publishes once started, with ${VAR:-default} from its .env."""
    return [host for host, _container in _mappings(folder)]


def web_port(folder):
    """The host port of a project's web page: the one published for a container port web servers use."""
    return next((host for host, container in _mappings(folder) if container in WEB_PORTS), None)


def _mappings(folder):
    """[(host port, container port)] a Compose project publishes."""
    text = compose._text(folder, compose.COMPOSE_FILES)
    env = {**os.environ, **_env(folder)}
    ports, lines = [], text.splitlines()
    for index, line in enumerate(lines):
        if not re.match(r"\s*ports:\s*$", line):
            continue
        indent = len(line) - len(line.lstrip())
        for entry in lines[index + 1:]:
            if not entry.strip() or entry.strip().startswith("#"):
                continue
            if len(entry) - len(entry.lstrip()) <= indent:
                break
            mapping = _resolve(entry.strip().lstrip("-").strip().strip("'\""), env).split("/")[0]
            parts = mapping.rsplit(":", 2)
            host = parts[-2] if len(parts) >= 2 else ""
            if host.isdigit() and parts[-1].isdigit() and int(host) not in [h for h, _c in ports]:
                ports.append((int(host), int(parts[-1])))
    return ports


def _installed_services():
    done = _run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--plain"])
    return {line.split()[0] for line in (done.stdout if done else "").splitlines() if line.strip()}


def _active(unit):
    done = _run(["systemctl", "is-active", "--quiet", unit])
    return bool(done) and done.returncode == 0


def claims(found):
    """[{port, who, running}]: everything that has a port, or takes one when started."""
    listens, docker, folders = listening(), published(), compose_folders()
    rows = []
    for name, folder in found.items():
        for port in project_ports(folder):
            # Running if Docker publishes this port for this project: infra may run its MySQL and not its
            # PostgreSQL, and staging/infra names infra's MySQL container too.
            rows.append({"port": port, "who": name,
                         "running": port in docker and _owner(docker[port], found, folders) == name})
    apache_running = _active("apache2.service")
    for port in apache_ports():
        rows.append({"port": port, "who": "apache2", "running": apache_running})
    installed = _installed_services()
    for unit, ports in SERVICE_PORTS.items():
        if unit in installed:
            running = _active(unit)
            rows += [{"port": port, "who": unit.removesuffix(".service"), "running": running} for port in ports]
    claimed = {row["port"] for row in rows}
    for port, program in {**listens, **docker}.items():
        # The ephemeral range is apps talking to themselves (an editor, a language server): not a port to plan around.
        if port not in claimed and port < EPHEMERAL:
            rows.append({"port": port, "who": SYSTEM.get(port) or program or "?", "running": True, "other": True})
    return sorted(rows, key=lambda row: (row["port"], row["who"]))


def clashes(rows):
    """{port: [who]} for each port that more than one thing wants while one of them holds it.
    Two services that are both off, like Apache and nginx installed side by side, are in nobody's way yet."""
    by_port, held = {}, set()
    for row in rows:
        if not row.get("other"):
            by_port.setdefault(row["port"], [])
            if row["who"] not in by_port[row["port"]]:
                by_port[row["port"]].append(row["who"])
            if row["running"]:
                held.add(row["port"])
    return {port: who for port, who in by_port.items() if len(who) > 1 and port in held}


def busy_for(name, found):
    """[(port, who)]: the ports this project needs that something else holds right now."""
    folder = found[name]
    mine = compose.containers_of(folder)
    listens, docker, folders = listening(), published(), compose_folders()
    taken = []
    for port in project_ports(folder):
        if port in docker:
            owner = _owner(docker[port], found, folders)
            if owner != name and docker[port] not in mine:
                taken.append((port, owner or docker[port]))
        elif port in listens:
            owner = listens[port] or next((row["who"] for row in claims(found)
                                            if row["port"] == port and row["running"] and row["who"] != name), "?")
            taken.append((port, owner))
    return taken
