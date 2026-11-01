"""Your projects with no AI and no Claude Code history: found in your home folder by what a project
has in it, and what each one runs read from its own files. Only names and paths are looked at here,
never what's in your code.
"""

import json
import os
import re
import time
from collections import deque
from pathlib import Path

ROOTS = ("Documents", "Dokumen", "Projects", "projects", "Project", "project", "Code", "code", "Dev", "dev", "Developer",
         "workspace", "Workspace", "repos", "src", "git", "www", "Desktop")
WEB_ROOTS = ("/var/www", "/srv/www", "/opt/lampp/htdocs")  # where a web server serves from
NOT_WORK = {"Downloads", "Unduhan", "Pictures", "Gambar", "Music", "Musik", "Videos", "Video", "Public", "Publik", "Templates",
            "Templat", "snap", "bin", "go", "Android", "flutter", "anaconda3", "miniconda3"}  # not where you'd keep a project
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
MARKERS = (".git", "package.json", "composer.json", "artisan", "manage.py", "pyproject.toml", "requirements.txt",
           "go.mod", "Cargo.toml", "pubspec.yaml", "pom.xml", "build.gradle", "Gemfile", *COMPOSE_FILES)
SKIPPED = {"node_modules", "vendor", "venv", "__pycache__", "dist", "build", "target"}
MAX_DEPTH = 3
MAX_FOLDERS = 3000  # a home folder full of files can't hold up the command bar
FRESH_S = 60        # the command bar asks with every request: once in a while is enough to look
SUBFOLDERS = ("frontend", "client", "web", "backend", "server", "api")  # a project split in two

_found = (0.0, [])


def forget():
    global _found
    _found = (0.0, [])


def _changed(folder):
    for path in (folder / ".git" / "index", folder):
        try:
            return path.stat().st_mtime
        except OSError:
            continue
    return 0


def has_compose(folder):
    return any((Path(folder) / name).is_file() for name in COMPOSE_FILES)


def _other_folders(home):
    """Whatever else is in your home folder, which may be a project itself: ~/skripsi, ~/kerja/toko."""
    try:
        return [Path(entry.path) for entry in os.scandir(home) if entry.is_dir(follow_symlinks=False)
                and entry.name[0] != "." and entry.name not in {*ROOTS, *NOT_WORK, *SKIPPED}]
    except OSError:
        return []


def find():
    """The projects in your home folder and where web servers serve from, the most recently touched first.
    The usual folders are looked through first, in case there's more than can be looked through."""
    global _found
    if time.monotonic() - _found[0] < FRESH_S and _found[0]:
        return _found[1]
    home = Path.home()
    found = []
    queue = deque((home / root, 0) for root in (*ROOTS, *WEB_ROOTS))
    queue.extend((folder, 1) for folder in _other_folders(home))
    looked = 0
    while queue and looked < MAX_FOLDERS:
        folder, depth = queue.popleft()
        looked += 1
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        if depth and any(entry.name in MARKERS for entry in entries):
            found.append(folder)
        elif depth < MAX_DEPTH:
            queue.extend((Path(entry.path), depth + 1) for entry in entries
                         if entry.name[0] != "." and entry.name not in SKIPPED and entry.is_dir(follow_symlinks=False))
    paths = [str(path) for path in sorted(found, key=_changed, reverse=True)]
    _found = (time.monotonic(), paths)
    return paths


def _web_port(script):
    named = re.search(r"(?:--port[= ]|-p[= ]?)(\d{2,5})\b", script)
    if named:
        return int(named.group(1))
    for pattern, port in ((r"\bvite\b", 5173), (r"\bnext\b", 3000), (r"\bnuxt\b", 3000), (r"\bastro\b", 4321),
                          (r"\breact-scripts\b", 3000), (r"\bng serve\b", 4200), (r"\bvue-cli-service\b", 8080)):
        if re.search(pattern, script):
            return port
    return None


def _run_script(folder, name):
    if (folder / "pnpm-lock.yaml").exists():
        return f"pnpm run {name}"
    if (folder / "yarn.lock").exists():
        return f"yarn {name}"
    if (folder / "bun.lockb").exists() or (folder / "bun.lock").exists():
        return f"bun run {name}"
    return "npm start" if name == "start" else f"npm run {name}"


def _servers_in(folder):
    found = []
    if (folder / "artisan").exists():
        found.append({"cwd": str(folder), "command": "php artisan serve", "port": 8000})
    if (folder / "manage.py").exists():
        python = next((f"{venv}/bin/python" for venv in (".venv", "venv") if (folder / venv / "bin" / "python").exists()), "python3")
        found.append({"cwd": str(folder), "command": f"{python} manage.py runserver", "port": 8000})
    try:
        scripts = json.loads((folder / "package.json").read_text()).get("scripts") or {}
    except (OSError, ValueError, AttributeError):
        scripts = {}
    if isinstance(scripts, dict):
        name = "dev" if "dev" in scripts else "start" if "start" in scripts else None
        if name and isinstance(scripts[name], str):
            found.append({"cwd": str(folder), "command": _run_script(folder, name), "port": _web_port(scripts[name])})
    return found


def servers(project):
    """The dev servers a project's own files say it has: [{cwd, command, port}]."""
    root = Path(project)
    return _servers_in(root) or [server for name in SUBFOLDERS for server in _servers_in(root / name)]
