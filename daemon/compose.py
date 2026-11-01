"""Docker for the command bar: the images there are, and the Compose projects to start and stop by name.

A project is a folder with a Compose file: each one in ~/Docker-Project and a level below it, your
DeskAI projects that have one, and whatever Docker itself remembers. Starting one starts Docker
too when it's left to start on first use.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import session
from projects import COMPOSE_FILES, has_compose

ROOTS = [Path.home() / "Docker-Project", Path.home() / "docker"]  # where Compose projects are kept
UP_TIMEOUT_S = 600  # a first start may pull or build images
QUICK_TIMEOUT_S = 5  # what the command bar asks while you wait, on the daemon's main thread
CONTAINER_NAME = re.compile(r"^\s*container_name:\s*['\"]?([\w.-]+)", re.M)
IMAGE = re.compile(r"^\s*image:\s*['\"]?([\w./:@-]+)", re.M)


def _run(args, cwd=None, timeout=30):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def projects(project_folders=()):
    """{name: folder} of the Compose projects. A name is the folder's, or its path under ~/Docker-Project
    when two share a name ("infra" and "team/infra")."""
    found = {}
    for root in ROOTS:
        if not root.is_dir():
            continue
        for folder in sorted([*root.glob("*/"), *root.glob("*/*/")]):
            if has_compose(folder) and not any(part.startswith(".") for part in folder.relative_to(root).parts):
                found.setdefault(str(folder.relative_to(root)), str(folder))
    for folder in map(Path, project_folders):
        if has_compose(folder) and str(folder) not in found.values():
            found.setdefault(folder.name, str(folder))
    for item in _docker_ls():
        folder = str(Path(item.get("ConfigFiles", "").split(",")[0]).parent)
        if folder not in found.values() and Path(folder).is_dir():
            found.setdefault(item.get("Name") or Path(folder).name, folder)
    return found


def _docker_ls():
    """What `docker compose ls` knows, only when Docker is up: asking would start it."""
    if not shutil.which("docker") or not session.docker_running():
        return []
    done = _run(["docker", "compose", "ls", "-a", "--format", "json"], timeout=QUICK_TIMEOUT_S)
    try:
        return json.loads(done.stdout) if done.returncode == 0 else []
    except ValueError:
        return []


def states(found):
    """{name: "running" | "stopped"} for these projects: running if any of its containers is."""
    running = {str(Path(item.get("ConfigFiles", "").split(",")[0]).parent)
               for item in _docker_ls() if "running" in item.get("Status", "")}
    return {name: "running" if folder in running else "stopped" for name, folder in found.items()}


def _text(folder, names):
    for name in names:
        try:
            return (Path(folder) / name).read_text(errors="replace")
        except OSError:
            continue
    return ""


def containers_of(folder):
    """The container names a project's Compose file gives its services."""
    return set(CONTAINER_NAME.findall(_text(folder, COMPOSE_FILES)))


def needed(name, found):
    """{project: [containers]} this one needs running first, in order: the containers of other projects
    it reaches by name, like DB_HOST=shared-db_mysql in its .env or its Compose file."""
    folder = found[name]
    text = _text(folder, COMPOSE_FILES) + "\n" + _text(folder, (".env",))
    others = {other: containers_of(path) for other, path in found.items() if other != name}
    wanted = {}
    for container in sorted(set().union(*others.values()) - containers_of(folder)) if others else ():
        # A whole name: "${DB_HOST:-shared-db_mysql}" counts, "old-shared-db_mysql" doesn't.
        if re.search(rf"(?<![\w.])(?<!\w-){re.escape(container)}(?![\w.-])", text):
            owner = _owner(container, [other for other, names in others.items() if container in names], found)
            wanted.setdefault(owner, []).append(container)
    return wanted


def needs(name, found):
    return list(needed(name, found))


def _owner(container, owners, found):
    """Of the projects that define a container, the one it belongs to: Docker's word if it has one,
    else the one nearest the top (infra rather than an old copy in team/infra)."""
    if len(owners) > 1 and shutil.which("docker") and session.docker_running():
        done = _run(["docker", "inspect", "-f", '{{index .Config.Labels "com.docker.compose.project.working_dir"}}', container], timeout=QUICK_TIMEOUT_S)
        owned = [owner for owner in owners if found[owner] == done.stdout.strip()]
        if owned:
            return owned[0]
    return min(owners, key=lambda owner: (owner.count("/"), owner))


def _simple(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _untagged(image):
    return image.rsplit(":", 1)[0] if ":" in image.rsplit("/", 1)[-1] else image


def project_for(word, found):
    """The project a container or an image belongs to: "shared-db_mysql", "shop-app", "mysql:5.7".
    From the Compose files (container_name, image), else from the labels Compose put on an image it built."""
    wanted = _simple(_untagged(word))
    if not wanted:
        return None
    owners = [name for name, folder in found.items()
              if any(_simple(c) == wanted for c in containers_of(folder))
              or any(_simple(_untagged(i)) == wanted for i in IMAGE.findall(_text(folder, COMPOSE_FILES)))]
    if not owners and shutil.which("docker") and session.docker_running():
        image = word if ":" in word else f"{word}:latest"
        done = _run(["docker", "image", "inspect", image, "--format", '{{index .Config.Labels "com.docker.compose.project"}}'], timeout=QUICK_TIMEOUT_S)
        label = _simple(done.stdout.strip()) if done.returncode == 0 else ""
        owners = [name for name in found if label and _simple(name) == label] or \
                 [name for name in found if label and _simple(name).startswith(label)]
    return _owner(word, owners, found) if owners else None


def image_exists(word):
    """Only when Docker is up: every "start something" asks this, and asking would start it."""
    if not shutil.which("docker") or not session.docker_running():
        return False
    return _run(["docker", "image", "inspect", word if ":" in word else f"{word}:latest"], timeout=QUICK_TIMEOUT_S).returncode == 0


def dependents(name, found):
    """The projects that need this one."""
    return [other for other in found if other != name and name in needs(other, found)]


def _running_containers():
    if not shutil.which("docker") or not session.docker_running():
        return set()
    return set(_run(["docker", "ps", "--format", "{{.Names}}"]).stdout.split())


def start(name, found, op="up"):
    """Starts or restarts a project, first starting the containers it needs from other projects if they
    aren't running: only those, so infra's MySQL comes up for shop but its PostgreSQL stays as it was.
    Returns (ok, the last thing Docker said, [the projects it started something in])."""
    started = []
    running = _running_containers()
    for other, containers in needed(name, found).items():
        missing = [container for container in containers if container not in running]
        if not missing:
            continue
        done = _run(["docker", "start", *missing], timeout=120)
        if done.returncode != 0:  # never created yet: Compose creates them, with the rest of that project
            ok, said = act(found[other], "up")
            if not ok:
                return False, f"{other}: {said}", started
        started.append(other)
    ok, said = act(found[name], op)
    return ok, said, started


def act(folder, op):
    """Starts ("up"), stops or restarts a project. Returns (ok, the last thing Docker said)."""
    command = {"up": ["up", "-d"], "stop": ["stop"], "restart": ["restart"]}[op]
    done = _run(["docker", "compose", *command], cwd=folder, timeout=UP_TIMEOUT_S if op != "stop" else 120)
    said = [line.strip() for line in (done.stderr + done.stdout).splitlines() if line.strip()]
    return done.returncode == 0, said[-1][:200] if said else ""


def images():
    """[{name, size, used, running}]: the images there are, newest first; used if a container (even a stopped
    one) is made from it. Starts Docker if it's left to start on first use: only when you ask."""
    if not shutil.which("docker"):
        return []
    listed = _run(["docker", "images", "--format", "json"])
    containers = _run(["docker", "ps", "-a", "--format", "json"])
    made = {}
    for line in containers.stdout.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        # Docker leaves ":latest" off the image of a container: "blog-site-app".
        image = info.get("Image", "")
        if ":" not in image.rsplit("/", 1)[-1] and not image.startswith("sha256:"):
            image += ":latest"
        made[image] = made.get(image, False) or info.get("State") == "running"
    found = []
    for line in listed.stdout.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        name = f"{info.get('Repository')}:{info.get('Tag')}"
        used = name in made or info.get("ID", "") in made
        found.append({"name": name, "size": info.get("Size", ""), "used": used, "running": made.get(name, False)})
    return found
