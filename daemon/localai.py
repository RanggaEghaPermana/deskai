#!/usr/bin/env python3
"""The local AI, when DeskAI is the one to get it: Ollama and its model, downloaded in the background.

It runs as a small service of its own, so closing the setup window or restarting the daemon doesn't stop it. Logging out
does, and it carries on from where it was at the next login. Only what's missing gets downloaded. DeskAI's own copy of Ollama
keeps everything in one folder, models too, and that goes when DeskAI is removed.

    localai.py    gets what's missing
"""

import hashlib
import http.client
import json
import os
import platform
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
SHARE = DATA / "deskai"
FOLDER = SHARE / "ollama"          # DeskAI's own Ollama: bin, lib and, as its home, .ollama with the models
BINARY = FOLDER / "bin" / "ollama"
STATE_FILE = SHARE / "local-ai.json"
WANTED = SHARE / "local-ai.wanted"  # there for as long as there's something to get: the service only starts if it is
UNITS = CONFIG / "systemd" / "user"
GETTER = "deskai-local-ai.service"
SERVER = "deskai-ollama.service"
MODES_FILE = CONFIG / "deskai" / "modes.json"
DEFAULTS_FILE = Path(__file__).resolve().parents[1] / "extension" / "deskai@ranggaeghapermana.github.io" / "modes.default.json"

# The release that was tried, with its checksum written down here: nobody gets a file that wasn't.
OLLAMA_URL = "https://github.com/ollama/ollama/releases/download/v0.40.13/"
OLLAMA_FILES = {  # by platform.machine(): name, sha256, bytes
    "x86_64": ("ollama-linux-amd64.tar.zst", "c9490e0f520be21271d969e6703f012268b1d39f8b70dd58854e5bfef7a75bb6", 1_525_346_571),
    "aarch64": ("ollama-linux-arm64.tar.zst", "913d9a28da6da86d551ce4ca531457767bb14d511cbc1d5ca93e9b3b8b9a8666", 1_582_415_903),
}
# Most of the archive is NVIDIA's libraries, gigabytes of them unpacked, for a GPU this computer may not have.
ONLY_FOR_NVIDIA = ("--exclude=lib/ollama/cuda_*", "--exclude=lib/ollama/mlx*")
NVIDIA_DEVICE = Path("/dev/nvidia0")
DEFAULT_URL = "http://127.0.0.1:11434"
MODEL = "qwen3:1.7b"
MODEL_BYTES = 1_359_293_444        # what the default model comes to; another one says how big it is once it starts
UNPACKED_BYTES = 200_000_000       # room for Ollama itself, once unpacked: 115 MB without NVIDIA's libraries
UNPACKED_NVIDIA_BYTES = 6_000_000_000  # and with them, going by how far the archive compresses: nobody here has measured it
SPARE_BYTES = 1_000_000_000        # a disk filled to the last byte is no good to anyone
MEMORY_NEEDED = 4_000_000_000      # the model takes about 2 GB, on top of what's already running
QUIET_S = 120                      # a download that says nothing for this long has gone
SAVE_EVERY_S = 1
SERVER_WAIT_S = 20
CHUNK = 1024 * 1024

NO_ROOM = (28, 122)                # a full disk, a used-up quota
NOT_ALLOWED = (1, 13, 30)          # not allowed, a read-only disk
OFFLINE = ("dial tcp", "no such host", "temporary failure", "connection refused", "connection reset", "i/o timeout",
           "tls handshake", "network is unreachable", "unexpected eof", "max retries")


class Stuck(Exception):
    """What waiting won't fix. The key is a sentence in both the setup window's and the cards' strings."""

    def __init__(self, key, said):
        super().__init__(said)
        self.key = key


class OnAMeteredConnection(Exception):
    """It tries again in a minute, like with no connection at all."""


def _systemctl(*args, timeout=30):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=timeout)


def read():
    """How it's going: {stage, done, total, trouble, said, ollama_ours, model_ours}, or {} if nothing's been asked for."""
    try:
        state = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _write(**changes):
    state = {**read(), **changes}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_name(f".{STATE_FILE.name}.{os.getpid()}")
    try:
        temp.write_text(json.dumps(state))
        temp.replace(STATE_FILE)
    finally:
        temp.unlink(missing_ok=True)


def _say(**changes):
    """How it ended. On a full disk even this can't be written, and the service still has to stop: no state, no WANTED, says enough."""
    try:
        _write(**changes)
    except OSError as e:
        print(f"can't write down how it went: {e}", file=sys.stderr)


def settings():
    """(the server's address, the model), as modes.json has them."""
    for path in (MODES_FILE, DEFAULTS_FILE):
        try:
            llm = json.loads(path.read_text())["llm"]
            return str(llm.get("ollama_url") or DEFAULT_URL).rstrip("/"), str(llm.get("ollama_model") or MODEL)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue
    return DEFAULT_URL, MODEL


def models(url):
    """The models the server there has, or None if nothing's answering."""
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=1) as response:
            return [model["name"] for model in json.load(response).get("models", [])]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, http.client.HTTPException):
        return None


def _has_model(names, model):
    return model in (names or []) or f"{model}:latest" in (names or [])


def find_ollama():
    for candidate in [shutil.which("ollama"), HOME / ".local/bin/ollama", BINARY]:
        if candidate and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def _memory():
    try:
        return os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError):
        return MEMORY_NEEDED  # can't tell, so it doesn't stand in the way


def _free_room():
    folder = SHARE
    while not folder.exists():
        folder = folder.parent
    return shutil.disk_usage(folder).free


def offer():
    """What choosing the local AI takes on this computer: {"download": bytes to download, "why_not": the string saying why
    it can't be had here, or ""}."""
    url, model = settings()
    answering = models(url)
    if _has_model(answering, model):
        return {"download": 0, "why_not": ""}
    needs_ollama = answering is None and url == DEFAULT_URL and not find_ollama()
    size = OLLAMA_FILES.get(platform.machine(), ("", "", 0))[2] if needs_ollama else 0
    download = MODEL_BYTES + size
    if needs_ollama and platform.machine() not in OLLAMA_FILES:
        return {"download": 0, "why_not": "ai_not_this_computer"}
    if needs_ollama and not shutil.which("zstd"):
        return {"download": 0, "why_not": "ai_needs_zstd"}
    if _memory() < MEMORY_NEEDED:
        return {"download": 0, "why_not": "ai_small_memory"}
    unpacked = (UNPACKED_NVIDIA_BYTES if NVIDIA_DEVICE.exists() else UNPACKED_BYTES) if needs_ollama else 0
    if _free_room() < download + unpacked + SPARE_BYTES:
        return {"download": download, "why_not": "ai_no_room"}
    return {"download": download, "why_not": ""}


def metered():
    """Whether this connection is paid for by the gigabyte, as far as the desktop knows. If it can't tell, it isn't."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        return Gio.NetworkMonitor.get_default().get_network_metered()
    except (ImportError, ValueError):
        return False


def percent():
    """How far along the download is, or None if nothing's being downloaded right now."""
    if not WANTED.exists():
        return None
    state = read()
    return min(99, int(100 * state.get("done", 0) / state["total"])) if state.get("total") else 0


# ---- getting it ---------------------------------------------------------------------------------

_saved = {"at": 0.0, "stage": ""}


def _progress(stage, done, total):
    now = time.monotonic()
    if now - _saved["at"] >= SAVE_EVERY_S or stage != _saved["stage"]:
        _saved.update(at=now, stage=stage)
        _write(stage=stage, done=done, total=total, trouble="")


def _download_ollama():
    """Downloads Ollama's archive, carrying on from what's there, and returns it once it's been checked."""
    name, sha256, size = OLLAMA_FILES[platform.machine()]
    FOLDER.mkdir(parents=True, exist_ok=True)
    archive = FOLDER / name
    have = archive.stat().st_size if archive.exists() else 0
    if have < size:
        request = urllib.request.Request(OLLAMA_URL + name, headers={"Range": f"bytes={have}-"} if have else {})
        with urllib.request.urlopen(request, timeout=QUIET_S) as response, archive.open("ab" if have else "wb") as out:
            if have and response.status != 206:  # it won't carry on from there, so it starts over
                out.truncate(0)
                have = 0
            while chunk := response.read(CHUNK):
                out.write(chunk)
                have += len(chunk)
                _progress("ollama", have, size + MODEL_BYTES)
    # A connection that closes partway ends the reading like the end of the file does. What's there is good, for carrying on from.
    if have < size:
        raise ConnectionError(f"{name} stopped at {have} of {size} bytes")
    with archive.open("rb") as whole:
        if hashlib.file_digest(whole, "sha256").hexdigest() != sha256:
            archive.unlink()
            raise Stuck("trouble_bad_download", f"{name} isn't the file it should be")
    return archive


def _unpack(archive):
    staging = FOLDER / "unpacking"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    _write(stage="unpacking", trouble="")
    skip = () if NVIDIA_DEVICE.exists() else ONLY_FOR_NVIDIA
    try:
        result = subprocess.run(["tar", "--zstd", "-xf", str(archive), "-C", str(staging), *skip], capture_output=True, text=True,
                                env={**os.environ, "LC_ALL": "C"})
    except OSError as e:
        raise Stuck("trouble_download", f"tar: {e}") from e
    if result.returncode != 0:
        said = result.stderr.strip()[-2000:]
        raise OSError(28, said) if "No space left" in said else Stuck("trouble_download", said)
    for name in ("lib", "bin"):  # the program last: once it's there, all of it is
        shutil.rmtree(FOLDER / name, ignore_errors=True)
        (staging / name).rename(FOLDER / name)
    shutil.rmtree(staging, ignore_errors=True)
    archive.unlink()


def _write_server_unit():
    """Ollama's home is its own folder, so nothing of it lands in yours, and it only listens on this computer."""
    UNITS.mkdir(parents=True, exist_ok=True)
    (UNITS / SERVER).write_text(f"""[Unit]
Description=Ollama, the local AI, for DeskAI

[Service]
ExecStart={BINARY} serve
Environment=HOME={FOLDER}
Environment=OLLAMA_HOST=127.0.0.1:11434
Environment=OLLAMA_NO_CLOUD=1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
""")
    _systemctl("daemon-reload")
    _systemctl("enable", SERVER)


def _start_server(url):
    """DeskAI's own, or the one you set up yourself if it's a service of yours. Nothing else is DeskAI's to start.
    Returns the models it has."""
    for unit in (SERVER, "ollama.service"):
        _systemctl("start", "--no-block", unit)
    deadline = time.monotonic() + SERVER_WAIT_S
    while time.monotonic() < deadline:
        names = models(url)
        if names is not None:
            return names
        time.sleep(0.5)
    raise Stuck("trouble_ollama_off", f"nothing answers at {url}")


def _failed_pull(said):
    words = said.lower()
    if "file does not exist" in words:
        return Stuck("trouble_model_missing", said)
    if "no space left" in words:
        return OSError(28, said)
    if any(word in words for word in OFFLINE):
        return ConnectionError(said)
    return Stuck("trouble_download", said)


def _pull(url, model, already):
    """Has the server download the model: it keeps what it's got so far, and carries on from there next time."""
    body = json.dumps({"model": model, "stream": True}).encode()
    request = urllib.request.Request(f"{url}/api/pull", data=body, headers={"Content-Type": "application/json"})
    layers = {}
    try:
        with urllib.request.urlopen(request, timeout=QUIET_S) as response:
            for line in response:
                event = json.loads(line)
                if event.get("error"):
                    raise _failed_pull(str(event["error"]))
                if event.get("digest") and event.get("total"):
                    layers[event["digest"]] = (event.get("completed", 0), event["total"])
                    _progress("model", already + sum(done for done, _ in layers.values()),
                              already + sum(total for _, total in layers.values()))
    except urllib.error.HTTPError as e:
        with e:
            raise _failed_pull(e.read().decode(errors="replace")) from e


def _bring_up_ollama(url):
    """Nothing's answering: gets Ollama if it isn't here, and starts it. Returns the models it has."""
    found = find_ollama()
    if url != DEFAULT_URL:
        raise Stuck("trouble_ollama_off", f"nothing answers at {url}, and that's not for DeskAI to start")
    if not found:
        if platform.machine() not in OLLAMA_FILES:
            raise Stuck("trouble_download", f"no Ollama for {platform.machine()}")
        _write(ollama_ours=True)
        _unpack(_download_ollama())
        found = str(BINARY)
    if Path(found) == BINARY:
        _write_server_unit()
    return _start_server(url)


def _get():
    # You may have said yes on this connection when you chose it. Coming back to a paid one later isn't that.
    if metered() and not read().get("any_connection"):
        raise OnAMeteredConnection("waiting for a connection that isn't metered")
    url, model = settings()
    names = models(url)
    if names is None:
        names = _bring_up_ollama(url)
    if not _has_model(names, model):
        _write(model_ours=model, model_url=url)
        _pull(url, model, OLLAMA_FILES[platform.machine()][2] if read().get("ollama_ours") else 0)


def _is_offline(error):
    if isinstance(error, urllib.error.HTTPError):
        return error.code >= 500 or error.code == 429
    return isinstance(error, (urllib.error.URLError, ConnectionError, TimeoutError, ssl.SSLError, http.client.HTTPException))


def _trouble(error):
    number = getattr(error, "errno", None)
    return error.key if isinstance(error, Stuck) else "trouble_no_room" if number in NO_ROOM \
        else "trouble_cant_write" if number in NOT_ALLOWED else "trouble_download"


def _give_back_room():
    """A full disk is worse than having no AI: what this has downloaded so far goes, so the rest of the computer keeps working."""
    for archive in FOLDER.glob("*.tar.zst"):
        archive.unlink(missing_ok=True)
    shutil.rmtree(FOLDER / "unpacking", ignore_errors=True)
    for partial in (FOLDER / ".ollama" / "models" / "blobs").glob("*-partial*"):
        partial.unlink(missing_ok=True)


def get():
    """Gets what's missing. Returns the exit code the service goes by: anything but 0 has it try again in a minute."""
    try:
        _get()
    except OnAMeteredConnection as e:
        print(f"local AI: {e}", file=sys.stderr)
        _say(stage="metered", trouble="")
        return 75
    except Exception as e:  # anything: nobody's watching this, and it has to end up saying something
        print(f"local AI: {e!r}", file=sys.stderr)
        if _is_offline(e):
            _say(stage="waiting", trouble="trouble_no_connection")
            return 75
        trouble = _trouble(e)
        if trouble == "trouble_no_room":
            _give_back_room()
        _say(stage="failed", trouble=trouble, said=False)
        WANTED.unlink(missing_ok=True)
        return 0
    _say(stage="ready", trouble="", said=False)
    WANTED.unlink(missing_ok=True)
    return 0


# ---- what the rest of DeskAI asks of it ----------------------------------------------------------

def want(python, app_dir):
    """Starts getting whatever's missing, in the background. Returns False if the service wouldn't start."""
    url, model = settings()
    if _has_model(models(url), model):
        return True
    SHARE.mkdir(parents=True, exist_ok=True)
    WANTED.touch()
    _write(stage="starting", done=0, total=0, trouble="", said=True, any_connection=metered())
    UNITS.mkdir(parents=True, exist_ok=True)
    (UNITS / GETTER).write_text(f"""[Unit]
Description=DeskAI getting the local AI
ConditionPathExists={WANTED}
StartLimitIntervalSec=0

[Service]
ExecStart={python} {app_dir}/daemon/localai.py
Nice=10
Restart=on-failure
RestartSec=60

[Install]
WantedBy=default.target
""")
    results = [_systemctl(*command) for command in (("daemon-reload",), ("enable", GETTER), ("start", "--no-block", GETTER))]
    return all(result.returncode == 0 for result in results)


def try_again():
    """After a failure: the card's button. What's been got so far stays."""
    WANTED.touch()
    _write(stage="starting", trouble="", said=True, any_connection=metered())
    _systemctl("start", "--no-block", GETTER)


def stop(server=False):
    """You chose something else: no more downloading, and with server, no Ollama of DeskAI's running for nothing."""
    WANTED.unlink(missing_ok=True)
    if (UNITS / GETTER).exists():
        _systemctl("stop", GETTER)
    if server and (UNITS / SERVER).exists():
        _systemctl("disable", "--now", SERVER)


def news():
    """What the dock hasn't said yet, once there's something to say: the state, or None."""
    state = read()
    return state if state.get("stage") in ("ready", "failed") and state.get("said") is False else None


def told():
    _write(said=True)


def there_is_something():
    """Whether removing DeskAI has any of this to take away."""
    return STATE_FILE.exists() or (UNITS / GETTER).exists() or (UNITS / SERVER).exists() or FOLDER.exists()


def _delete_model(url, model):
    request = urllib.request.Request(f"{url}/api/delete", data=json.dumps({"model": model}).encode(), method="DELETE",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30):
        pass


def remove():
    """What DeskAI got goes, and nothing else: a model it downloaded into your own Ollama, but not your Ollama."""
    state = read()
    for unit in (GETTER, SERVER):
        if (UNITS / unit).exists():
            _systemctl("disable", "--now", unit)
            (UNITS / unit).unlink(missing_ok=True)
    _systemctl("daemon-reload")
    # The one it downloaded, wherever that was, whatever modes.json says by now: nothing that's yours.
    if state.get("model_ours") and state.get("model_url") and not state.get("ollama_ours"):
        try:
            _delete_model(state["model_url"], state["model_ours"])
        except (OSError, http.client.HTTPException) as e:
            print(f"can't delete the local model: {e}", file=sys.stderr)
    shutil.rmtree(FOLDER, ignore_errors=True)
    STATE_FILE.unlink(missing_ok=True)
    WANTED.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(get())
