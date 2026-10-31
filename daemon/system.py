"""The computer's own room: memory, disk space, and the caches that are safe to clear.

Everything here runs as you. Clearing only touches caches that the tools that made them fill again
on their own: never your files, the Trash, Docker's volumes or tagged images, or anything that
needs root.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

LOW_DISK_BYTES = 5 * 1000**3   # less free than this on a disk is running out...
LOW_DISK_SHARE = 0.05          # ...and so is less than this share of it
LOW_MEMORY_SHARE = 0.10        # less memory available than this is running out
WORTH_CLEARING = 1000**2       # a cache smaller than this isn't worth a mention

HOME = Path.home()
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")
# (id, what it is, folder): caches their tools fill again on their own.
CACHE_DIRS = [
    ("thumbnails", "thumbnail", CACHE / "thumbnails"),
    ("npm", "npm", HOME / ".npm" / "_cacache"),
    ("yarn", "yarn", CACHE / "yarn"),
    ("pip", "pip", CACHE / "pip"),
    ("composer", "composer", CACHE / "composer"),
]
DOCKER_BUILD = ("docker-build", "Docker build cache")
# What `ps` calls a program, when that's not what you'd call it.
PROGRAM_NAMES = {"llama-server": "Ollama", "ollama": "Ollama", "code": "VS Code", "gnome-shell": "GNOME Shell"}
DOCKER_SIZE = re.compile(r"Reclaimable:\s*([\d.]+)\s*([kMGT]?B)", re.I)


def run(*args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None


def human(size):
    """1.2 GB, 350 MB: as Files and Docker count them."""
    for unit, scale in (("GB", 1000**3), ("MB", 1000**2), ("kB", 1000)):
        if size >= scale:
            return f"{size / scale:.1f} {unit}".replace(".0 ", " ")
    return f"{size} B"


def memory():
    """{total, available, swap_total, swap_used} in bytes, from the kernel."""
    try:
        values = {line.split(":")[0]: int(line.split()[1]) * 1024 for line in Path("/proc/meminfo").read_text().splitlines()}
    except (OSError, ValueError, IndexError):
        return None
    return {"total": values["MemTotal"], "available": values["MemAvailable"],
            "swap_total": values.get("SwapTotal", 0), "swap_used": values.get("SwapTotal", 0) - values.get("SwapFree", 0)}


def heaviest_apps(count=3):
    """[(name, bytes)]: the programs holding the most memory, each one's processes counted together."""
    done = run("ps", "-eo", "rss=,comm=", timeout=5)
    totals = {}
    for line in (done.stdout if done else "").splitlines():
        rss, _, name = line.strip().partition(" ")
        if rss.isdigit() and name:
            name = PROGRAM_NAMES.get(name.strip(), name.strip())
            totals[name] = totals.get(name, 0) + int(rss) * 1024
    return sorted(totals.items(), key=lambda item: -item[1])[:count]


def disks():
    """[{path, total, free}]: the disk of the system and the one of your home, once each."""
    found, seen = [], set()
    for path in ("/", str(HOME)):
        try:
            stat = os.statvfs(path)
            device = os.stat(path).st_dev
        except OSError:
            continue
        if device not in seen:
            seen.add(device)
            found.append({"path": path, "total": stat.f_blocks * stat.f_frsize, "free": stat.f_bavail * stat.f_frsize})
    return found


def low_disks():
    return [disk for disk in disks() if disk["free"] < LOW_DISK_BYTES or disk["free"] < disk["total"] * LOW_DISK_SHARE]


def memory_low(info=None):
    info = info or memory()
    return bool(info) and info["available"] < info["total"] * LOW_MEMORY_SHARE


def _size(path):
    if not path.is_dir() or path.is_symlink():
        return 0
    done = run("du", "-sb", str(path), timeout=30)
    try:
        return int(done.stdout.split()[0]) if done and done.returncode == 0 else 0
    except (ValueError, IndexError):
        return 0


def _docker_build_cache():
    if not shutil.which("docker"):
        return 0
    done = run("docker", "builder", "du", timeout=30)
    match = DOCKER_SIZE.search(done.stdout) if done and done.returncode == 0 else None
    if not match:
        return 0
    scale = {"b": 1, "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4}[match.group(2).lower()]
    return int(float(match.group(1)) * scale)


def clearable():
    """[{id, label, size}]: the caches worth clearing, biggest first. Takes a few seconds: run it off the main thread."""
    found = [{"id": key, "label": label, "size": _size(path)} for key, label, path in CACHE_DIRS]
    found.append({"id": DOCKER_BUILD[0], "label": DOCKER_BUILD[1], "size": _docker_build_cache()})
    return sorted((item for item in found if item["size"] >= WORTH_CLEARING), key=lambda item: -item["size"])


def _empty(folder):
    """Removes what's inside a cache folder, keeping the folder: its tool expects it there."""
    for entry in folder.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)


def clear(ids):
    """Clears the caches with these ids. Returns the bytes it freed."""
    freed = 0
    for key, _label, path in CACHE_DIRS:
        if key in ids and path.is_dir() and not path.is_symlink():
            before = _size(path)
            _empty(path)
            freed += max(0, before - _size(path))
    if DOCKER_BUILD[0] in ids:
        before = _docker_build_cache()
        run("docker", "builder", "prune", "-f", timeout=300)
        freed += max(0, before - _docker_build_cache())
    return freed
