#!/usr/bin/env python3
"""Sets DeskAI up for the current user, with no UI: the setup window drives this.

It works from wherever the app's files are: a clone, or /usr/share/deskai when installed
from the .deb. Nothing here needs root, except installing a missing package and removing the
polkit rule the workday routine may have added; both go through pkexec.

    installer.py --check            what's there and what's missing
    installer.py --dev              developer install: runs right from this clone
    installer.py --uninstall [--purge]
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import docks  # noqa: E402
import localai  # noqa: E402
import personalize  # noqa: E402

UUID = "deskai@ranggaeghapermana.github.io"
LOGIN_UUID = "deskai-login@ranggaeghapermana.github.io"
LOGIN_SCREEN_SCHEMA = "com.ubuntu.login-screen"
LOGIN_BACKGROUND = "#000000"
SHELL_MAJORS = (46, 50, 51)  # the GNOME versions this release is written for
SERVICE = "deskaid.service"
POLKIT_RULE = Path("/etc/polkit-1/rules.d/50-deskai-services.rules")
PAYLOAD = ("daemon", "extension", "setup", "VERSION", "LICENSE")

HOME = Path.home()
DATA = Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local/share")
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")
STATE = Path(os.environ.get("XDG_STATE_HOME") or HOME / ".local/state")
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or HOME / ".cache")
POLKIT_GENERATED = CACHE / "deskai" / POLKIT_RULE.name  # what `deskai routine allow-db` writes for you to install
SETUP_LOG = STATE / "deskai" / "setup.log"  # what went wrong, in the tools' own words

SHARE = DATA / "deskai"
APP_DIR = SHARE / "app"           # a copy of the app, when it isn't installed system-wide
VENV = SHARE / "venv"             # only when using an API key: it needs the anthropic package
INSTALLED = SHARE / "installed.json"
EXTENSION_LINK = DATA / "gnome-shell" / "extensions" / UUID
LOGIN_LINK = DATA / "gnome-shell" / "extensions" / LOGIN_UUID
COMMAND = HOME / ".local" / "bin" / "deskai"
UNIT = CONFIG / "systemd" / "user" / SERVICE
CONFIG_DIR = CONFIG / "deskai"
ENV_FILE = CONFIG_DIR / "env"
MODES_FILE = CONFIG_DIR / "modes.json"
ROUTINE_FILE = CONFIG_DIR / "routine.json"
APP_ID = "io.github.RanggaEghaPermana.DeskAI"
METAINFO = f"{APP_ID}.Setup.metainfo.xml"
METAINFO_INSTALLED = Path("/usr/share/metainfo") / METAINFO  # where the .deb keeps it: a clone and a download have it in packaging/
LANGUAGE_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
LAUNCHER = DATA / "applications" / f"{APP_ID}.Setup.desktop"
PRESETS_LAUNCHER = DATA / "applications" / f"{APP_ID}.Presets.desktop"
ICON = DATA / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"

# What the AI choices mean in modes.json's llm.backend.
BACKENDS = {"claude": "claude-cli", "api": "api", "ollama": "ollama", "none": "none"}
AGENT_DIRS = {"Claude Code": (".claude", "claude"), "Codex": (".codex", "codex"), "Gemini CLI": (".gemini", "gemini")}
# How DeskAI's hook reads in an agent's settings: deskai hook claude, or ["…/deskai", "hook", "codex"].
HOOK_COMMAND = re.compile(r"""deskai["']?[\s,]+["']?hook["']?[\s,]+["']?(?:claude|gemini|codex)\b""")
# Packages by package manager, for what the checks can offer to install.
PACKAGES = {
    "atspi": {"apt-get": "gir1.2-atspi-2.0", "dnf": "at-spi2-core", "pacman": "at-spi2-core", "zypper": "typelib-1_0-Atspi-2_0"},
}


# What goes wrong, in the words of apt, pkexec, pip, venv, systemctl and Python, and the sentence in the setup
# window's STRINGS that says it plainly. The first that matches wins, and none has anything to fill in. Python's words can follow
# the desktop's language once GTK is loaded, so these go by its error numbers: 28 and 122 are a full disk and a used-up quota,
# 1, 13 and 30 are not being allowed and a read-only disk, and 2 is something missing.
TROUBLES = (
    (re.compile(r"request dismissed", re.I), "trouble_dismissed"),
    (re.compile(r"not authorized|no authentication agent", re.I), "trouble_not_allowed"),
    (re.compile(r"could not get lock|is another process using it", re.I), "trouble_package_busy"),
    (re.compile(r"unable to locate package|has no installation candidate", re.I), "trouble_package_missing"),
    (re.compile(r"\[Errno (28|122)\]|no space left|enough free space|quota exceeded", re.I), "trouble_no_room"),
    (re.compile(r"temporary failure (in name )?resol|could not resolve|could not connect to|dial tcp|no such host"
                r"|network is unreachable|connection refused|connection timed out|read timed out", re.I), "trouble_no_connection"),
    (re.compile(r"ensurepip", re.I), "trouble_venv"),
    (re.compile(r"connect to (\S+ scope )?bus", re.I), "trouble_no_session"),
    (re.compile(r"\[Errno (1|13|30)\]|permission denied|read-only file system|operation not permitted", re.I), "trouble_no_permission"),
    (re.compile(r"timed out", re.I), "trouble_too_long"),
    (re.compile(r"\[Errno 2\]", re.I), "trouble_not_there"),
)
# Extras: what goes wrong with these is said, but doesn't cost the rest. Codex having its own `notify` shouldn't keep the dock
# from turning on, a settings file that can't be read shouldn't leave DeskAI half removed, since trying again won't help, and
# DeskAI works without AI, so Ollama being off or out of reach shouldn't stop an install or leave an update half done.
CAN_FAIL = {"hooks", "ollama"}
SAID_KEEPS = 4000     # characters of what a tool said: the error comes last
LOG_KEEPS = 64 * 1024
EXTENSION_OFF_WAIT_S = 8


class SetupError(Exception):
    """A step failed. key and fields are the sentence the setup window says about it, from its STRINGS. What it holds as
    an exception's text is what happened in the tool's own words, for the log and the terminal, and never for the window."""

    def __init__(self, key, said, **fields):
        super().__init__(said)
        self.key, self.fields = key, fields
        self.logged = False


def _known_trouble(said):
    return next((key for words, key in TROUBLES if words.search(said)), "")


def _said(result):
    """What a tool said. Both its outputs, since venv says why it can't work on stdout."""
    said = "\n".join(part for part in (result.stdout, result.stderr) if part)
    return said.strip()[-SAID_KEEPS:] or f"exit code {result.returncode}"


def _tool_failed(key, result, **fields):
    """The SetupError for a tool that failed: what it said if that's something we know, or else key, about this step."""
    said = _said(result)
    known = _known_trouble(said)
    return SetupError(known, said) if known else SetupError(key, said, **fields)


def _write_log(entry):
    """Returns whether it worked. On a full disk it can't, and then stderr is all there is."""
    try:
        SETUP_LOG.parent.mkdir(parents=True, exist_ok=True)
        try:
            kept = SETUP_LOG.read_text(errors="replace")
        except FileNotFoundError:
            kept = ""
        # A file name that isn't valid text can't be written as it is, and shouldn't cost the rest of it.
        _write_private(SETUP_LOG, (kept + entry)[-LOG_KEEPS:].encode(errors="replace").decode())
        return True
    except (OSError, ValueError):
        print(entry, file=sys.stderr)
        return False


def report(error, step=""):
    """Writes down what went wrong, in the tool's own words, and returns the SetupError that says it plainly.
    A file, since the window shows only the plain sentence and is gone once it's closed, and a stranger's bug report needs this."""
    said = str(error) or repr(error)
    details = said
    if not isinstance(error, SetupError):
        details = "".join(traceback.format_exception(error)).strip()
        error = SetupError(_known_trouble(said) or "trouble_unknown", said)
    error.logged = _write_log(f"{datetime.now().isoformat(timespec='seconds')} DeskAI {version()} {step or '-'} {error.key}\n{details}\n\n")
    return error


def log_path():
    """Where the log is, the way you'd say it: a screenshot of the window shouldn't give your username away."""
    return f"~/{SETUP_LOG.relative_to(HOME)}" if SETUP_LOG.is_relative_to(HOME) else str(SETUP_LOG)


# ---- what's on this computer ------------------------------------------------------------

@dataclass
class Check:
    id: str
    ok: bool
    required: bool = False
    detail: str = ""           # what was found
    package: str = ""          # installable with install_package()


def run(*args, timeout=30, env=None, **kwargs):
    # In the language TROUBLES knows: on an Italian desktop apt says "Pacchetto x non trovato".
    env ={**(os.environ if env is None else env), "LC_ALL": "C"}
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env, **kwargs)


def shell_version():
    """GNOME Shell's major version, or None."""
    try:
        out = run("gnome-shell", "--version", timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)(?:\.\w+)*", out)
    return int(match.group(1)) if match else None


def package_manager():
    return next((pm for pm in ("apt-get", "dnf", "pacman", "zypper") if shutil.which(pm)), "")


def has_typelib(name, version):
    try:
        gi.require_version(name, version)
        return True
    except ValueError:
        return False


def user_systemd():
    try:
        return run("systemctl", "--user", "show-environment", timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_claude():
    for candidate in [shutil.which("claude"), HOME / ".local/bin/claude",
                      *sorted(HOME.glob(".nvm/versions/node/*/bin/claude"), reverse=True)]:
        if candidate and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def detected_agents():
    """The AI coding agents on this computer, by name."""
    return [label for label, (folder, exe) in AGENT_DIRS.items() if (HOME / folder).exists() or shutil.which(exe)]


def hooks_installed():
    """Whether any AI agent already has DeskAI's hooks in its settings: a command running `deskai hook`, not just those words."""
    for name in (".claude/settings.json", ".gemini/settings.json", ".codex/config.toml"):
        try:
            if HOOK_COMMAND.search((HOME / name).read_text()):
                return True
        except (OSError, ValueError):  # not text: the agent's own file can hold anything
            continue
    return False


def check_system():
    version = shell_version()
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    pm = package_manager()
    atspi = has_typelib("Atspi", "2.0")
    return [
        Check("gnome", version in SHELL_MAJORS and "GNOME" in desktop.upper(), required=True,
              detail=f"GNOME {version}" if version else desktop or "?"),
        Check("systemd", user_systemd(), required=True),
        Check("atspi", atspi, package="" if atspi else PACKAGES["atspi"].get(pm, "")),
        Check("ptyxis", bool(shutil.which("ptyxis"))),
        Check("chrome", bool(shutil.which("google-chrome") or shutil.which("google-chrome-stable"))),
    ]


def install_package(package):
    """Installs a system package through the password dialog. Raises SetupError."""
    pm = package_manager()
    if not pm or not shutil.which("pkexec"):
        raise SetupError("trouble_no_installer", f"package manager: {pm or 'none'}, pkexec: {shutil.which('pkexec') or 'none'}", package=package)
    command = {"pacman": ["pacman", "-S", "--noconfirm", package]}.get(pm, [pm, "install", "-y", package])
    result = run("pkexec", *command, timeout=600)
    if result.returncode != 0:
        raise _tool_failed("trouble_package", result, package=package)


# ---- what's set up already --------------------------------------------------------------

def is_system_payload():
    return ROOT.is_relative_to("/usr")


def linked_here():
    """This clone is already what's running: a developer install to keep as it is."""
    return EXTENSION_LINK.is_symlink() and EXTENSION_LINK.resolve() == (ROOT / "extension" / UUID).resolve() \
        and not is_system_payload()


def is_installed():
    return UNIT.exists() or EXTENSION_LINK.exists() or EXTENSION_LINK.is_symlink()


def version():
    try:
        return (ROOT / "VERSION").read_text().strip()
    except OSError:
        return "0"


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def install_record():
    """What the first install found, for removing DeskAI to put back, and the version that's there."""
    record = read_json(INSTALLED)
    return record if isinstance(record, dict) else {}


def installed_version():
    return install_record().get("version", "")


def _version_parts(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))


def newer(version_, than):
    """Whether one version comes after another. An unknown one comes before everything."""
    return _version_parts(version_) > _version_parts(than)


def update_from():
    """The version this one would replace, or None if there's nothing to update. Only a finished install says which it is:
    one that was interrupted has to go through the whole of setup, to ask what it never got to ask."""
    there = installed_version()
    return there if there and there != version() else None


def whats_new(since, language):
    """[(version, [what's new in it])] for the releases after `since`, newest first, in your language where they have
    it. Empty if the list can't be read: it shouldn't stand in the way of an update."""
    root = None
    for path in (ROOT / "packaging" / METAINFO, METAINFO_INSTALLED):
        try:
            root = ElementTree.parse(path).getroot()
            break
        except (OSError, ElementTree.ParseError):
            continue
    if root is None:
        return []
    releases = []
    for release in root.findall("./releases/release"):
        items = release.findall("./description/ul/li")
        chosen = [item for item in items if item.get(LANGUAGE_ATTRIBUTE) == language] or \
                 [item for item in items if item.get(LANGUAGE_ATTRIBUTE) is None]
        if newer(release.get("version", ""), since):
            releases.append((release.get("version", ""), [" ".join("".join(item.itertext()).split()) for item in chosen]))
    releases.sort(key=lambda found: _version_parts(found[0]), reverse=True)
    return releases


def read_modes():
    """What's in modes.json, or {} if it isn't an object: gone, or broken."""
    modes = read_json(MODES_FILE)
    return modes if isinstance(modes, dict) else {}


def current_choices():
    """The settings an earlier install left, to start the setup window from."""
    modes = read_modes()
    llm = modes.get("llm")
    backend = llm.get("backend", "") if isinstance(llm, dict) else ""
    ai = {v: k for k, v in BACKENDS.items()}.get(backend, "")
    if backend == "auto":
        ai = "api" if has_api_key() else "claude" if find_claude() else "ollama" if localai.find_ollama() else "none"
    routine = read_json(ROUTINE_FILE) or {}
    return {"language": modes.get("language", ""), "ai": ai, "routine": routine.get("enabled", False)}


def has_api_key():
    try:
        return any(re.match(r"\s*ANTHROPIC_API_KEY=\S", line) for line in ENV_FILE.read_text().splitlines())
    except OSError:
        return False


# ---- installing -------------------------------------------------------------------------

@dataclass
class Options:
    language: str = "en"
    ai: str = "none"             # claude, api, ollama, none
    api_key: str = ""            # only when ai is api; empty keeps the saved one
    hooks: bool = False
    routine: bool = False
    dev: bool = False            # run from this clone, with no copy
    keep_config: bool = False    # leave the AI, language and routine settings as they are
    update: bool = False         # what's running keeps running until the next login, when the new dock and service come up together
    log: list = field(default_factory=list)


def _copy_app():
    """A copy of the app that doesn't depend on where it was downloaded."""
    staging = SHARE / "app.new"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for name in PAYLOAD:
        source = ROOT / name
        if source.is_dir():
            shutil.copytree(source, staging / name, ignore=ignore)
        elif source.exists():
            shutil.copy2(source, staging / name)
    shutil.rmtree(APP_DIR, ignore_errors=True)
    staging.rename(APP_DIR)
    return APP_DIR


def _link(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.symlink_to(target)


def _write_env(key):
    """Saves the API key where only you can read it."""
    if re.search(r"\s", key):
        raise SetupError("trouble_key_spaces", "the API key has spaces in it")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        lines = ENV_FILE.read_text().splitlines()
    except OSError:
        lines = []
    lines = [line for line in lines if not re.match(r"\s*#?\s*ANTHROPIC_API_KEY=", line)]
    fd = os.open(ENV_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*lines, f"ANTHROPIC_API_KEY={key}"]) + "\n")
    ENV_FILE.chmod(0o600)


def _write_private(path, text):
    temp = path.with_name(path.name + ".new")
    temp.touch(mode=0o600)
    temp.chmod(0o600)
    temp.write_text(text)
    temp.replace(path)


def _write_private_json(path, data):
    _write_private(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _keep_broken_config():
    """Starting over replaces the file, and one that can't be read may still have your own modes in it."""
    if MODES_FILE.exists() and MODES_FILE.stat().st_size:
        kept = MODES_FILE.with_name(f"{MODES_FILE.name}.broken-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(MODES_FILE, kept)
        print(f"modes.json can't be read, so the old one is kept as {kept}", file=sys.stderr)


def _write_config(app_dir, options):
    """modes.json with this language and AI choice, keeping any modes you already have."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    defaults = app_dir / "extension" / UUID / "modes.default.json"
    modes = read_modes()
    if not modes:
        _keep_broken_config()
        modes = json.loads(defaults.read_text())
        try:
            personalize.personalize(modes)
        except (OSError, ValueError, GLib.Error) as e:
            print(f"Keeping the default apps: {e}", file=sys.stderr)
    modes["language"] = options.language
    # Claude's login on "auto": if Claude ever stops working and Ollama is set up, it carries on with that.
    backend = "auto" if options.ai == "claude" and not has_api_key() else BACKENDS[options.ai]
    modes["llm"] = {**(modes["llm"] if isinstance(modes.get("llm"), dict) else {}), "backend": backend}
    _write_private_json(MODES_FILE, modes)
    routine = read_json(ROUTINE_FILE) or {}
    routine["enabled"] = options.routine
    _write_private_json(ROUTINE_FILE, routine)


def _make_venv(python):
    if not (VENV / "bin" / "python").exists():
        result = run(python, "-m", "venv", "--system-site-packages", str(VENV), timeout=180)
        if result.returncode != 0:
            raise _tool_failed("trouble_venv", result)
    result = run(str(VENV / "bin" / "pip"), "install", "--quiet", "--upgrade", "anthropic", timeout=300)
    # With no connection, the one already there will do.
    if result.returncode != 0 and run(str(VENV / "bin" / "python"), "-c", "import anthropic").returncode != 0:
        raise _tool_failed("trouble_anthropic", result)


def _service_path():
    """systemd user services don't get your shell's PATH, and the brain needs `claude`."""
    claude = find_claude()
    dirs = [os.path.dirname(claude) if claude else "", str(COMMAND.parent),
            *(os.environ.get("PATH", "").split(":")), "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(d for d in dirs if d))


def _write_service(app_dir, python, restart=True):
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    deskaid = app_dir / "daemon" / "deskaid.py"
    UNIT.write_text(f"""[Unit]
Description=DeskAI brain (deskaid)
PartOf=graphical-session.target
After=graphical-session.target
ConditionPathExists={deskaid}

[Service]
ExecStart={python} {deskaid}
Environment=PATH={_service_path()}
EnvironmentFile=-{ENV_FILE}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
""")
    for command in (("daemon-reload",), ("enable", SERVICE), ("restart" if restart else "start", SERVICE)):
        result = run("systemctl", "--user", *command)
        if result.returncode != 0:
            raise _tool_failed("trouble_service", result)


def _write_command(app_dir, python):
    COMMAND.parent.mkdir(parents=True, exist_ok=True)
    python, script, folder = shlex.quote(python), shlex.quote(f"{app_dir}/daemon/deskai.py"), shlex.quote(str(app_dir))
    COMMAND.write_text(f"""#!/bin/sh
# AI agents run this as a hook, and some take exit code 2 to mean "stop what you're doing". That's what Python
# exits with when it can't find its script, so a hook always exits 0: DeskAI going away can't block them.
if [ "$1" = hook ]; then
  {python} {script} "$@" 2>/dev/null
  exit 0
fi
if [ ! -f {script} ]; then
  echo "DeskAI's files aren't in "{folder}" anymore. Move them back, or run DeskAI Setup again." >&2
  exit 1
fi
exec {python} {script} "$@"
""")
    COMMAND.chmod(0o755)


def _write_launcher(app_dir, python):
    """The setup window in the app grid, to update or remove later, and the one for presets. The .deb ships its own."""
    LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
    LAUNCHER.write_text(f"""[Desktop Entry]
Type=Application
Name=DeskAI Setup
Comment=Install, update or remove DeskAI
Exec={python} {app_dir}/setup/deskai_setup.py
Icon={APP_ID}
Terminal=false
Categories=Settings;GNOME;GTK;
StartupNotify=true
""")
    PRESETS_LAUNCHER.write_text(f"""[Desktop Entry]
Type=Application
Name=My Presets
Name[id]=Preset Pribadi
Comment=Your own routines, started by words you choose
Comment[id]=Kebiasaan lu sendiri, dimulai dengan kata yang lu pilih
Exec={python} {app_dir}/daemon/presets_window.py
Icon={APP_ID}
Terminal=false
Categories=Utility;GNOME;GTK;
StartupNotify=true
""")
    ICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app_dir / "setup" / f"{APP_ID}.svg", ICON)


def _hooks(action):
    env = {**os.environ, "PATH": f"{COMMAND.parent}:{os.environ.get('PATH', '')}"}
    result = run(str(COMMAND), "hooks", action, env=env, timeout=60)
    if result.returncode != 0:
        # These whatever it said, since the rest carries on without this step: they say what's missing because of it.
        raise SetupError("trouble_hooks", _said(result)) if action == "install" else SetupError("trouble_hooks_left", _said(result))


def _login_screen():
    """Ubuntu's own settings for the screen before the desktop appears. None elsewhere."""
    source = Gio.SettingsSchemaSource.get_default()
    return Gio.Settings.new(LOGIN_SCREEN_SCHEMA) if source and source.lookup(LOGIN_SCREEN_SCHEMA, True) else None


def has_polkit_rule():
    """Whether the rule may be installed. Only root can look in its folder, so having made it counts too."""
    def visible(path):
        try:
            return path.exists()
        except OSError:  # Python 3.12 raises on a folder it can't read, and 3.14 says the file isn't there
            return False

    return visible(POLKIT_RULE) or visible(POLKIT_GENERATED)


def dock_is_running():
    """Whether GNOME has the dock loaded, in this session. If it can't be asked, it's taken not to."""
    try:
        return "State: ACTIVE" in run("gnome-extensions", "info", UUID, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False


def replace_docks_for_next_login():
    """Just before the login that brings the dock up: the other docks go now, or both would be there for a moment
    at that login. Returns whether any did, so they can come back if you don't log out after all."""
    if dock_is_running():
        return False
    return bool(docks.turn_off_others())


def _enable_extension():
    """Turns the extensions on for the next login: GNOME only reads new extensions when it starts. Returns what it found
    changed, for removing DeskAI to put back. An update leaves DeskAI Login as you left it, since you can turn that off.
    Each list is only written if it's different: GNOME goes through its extensions each time one is, and two at once can overlap."""
    settings = Gio.Settings.new("org.gnome.shell")
    ours = (UUID,) if installed_version() else (UUID, LOGIN_UUID)
    enabled = settings.get_strv("enabled-extensions")
    missing = [u for u in ours if u not in enabled]
    if missing:
        settings.set_strv("enabled-extensions", [*enabled, *missing])
    disabled = settings.get_strv("disabled-extensions")
    if any(u in disabled for u in ours):
        settings.set_strv("disabled-extensions", [u for u in disabled if u not in ours])
    found = {"extensions_were_off": settings.get_boolean("disable-user-extensions")}
    if found["extensions_were_off"]:
        settings.set_boolean("disable-user-extensions", False)
    # Ubuntu fills the screen with this before the desktop appears: gray by default, and a flash at login.
    login_screen = _login_screen()
    found["login_color_was_unset"] = bool(login_screen and login_screen.get_user_value("background-color") is None)
    if found["login_color_was_unset"]:
        login_screen.set_string("background-color", LOGIN_BACKGROUND)
    Gio.Settings.sync()
    return found


def install_steps(options):
    """[(id, function)]: what an install does, in order, for the UI to show."""
    python = sys.executable
    state = {"app_dir": ROOT}

    def app():
        if not (options.dev or is_system_payload() or linked_here()):
            state["app_dir"] = _copy_app()
        _link(state["app_dir"] / "extension" / UUID, EXTENSION_LINK)
        _link(state["app_dir"] / "extension" / LOGIN_UUID, LOGIN_LINK)

    def ai():
        if options.ai == "api" and not options.keep_config:
            if options.api_key:
                _write_env(options.api_key)
            elif not has_api_key():
                raise SetupError("trouble_no_key", "no API key entered, and none saved")
        if options.ai == "api":
            _make_venv(python)

    def local_model():
        # Only starts it: the download goes on in the background, after this window and across a logout.
        if options.ai == "ollama" and not options.keep_config and not localai.want(python, state["app_dir"]):
            raise SetupError("trouble_service", f"systemctl couldn't start {localai.GETTER}")

    def settings():
        if not options.keep_config:
            _write_config(state["app_dir"], options)
            # With Claude's login on "auto", DeskAI's Ollama is what it falls back on, so that one keeps running.
            if options.ai != "ollama":
                localai.stop(server=options.ai != "claude")

    def service():
        # An existing venv stays in use: it may hold the SDK this install relies on.
        chosen = str(VENV / "bin" / "python") if (VENV / "bin" / "python").exists() else python
        _write_command(state["app_dir"], chosen)
        _write_service(state["app_dir"], chosen, restart=not options.update)

    def hooks():
        if options.keep_config:
            return
        if options.hooks:
            _hooks("install")
        elif hooks_installed():
            _hooks("remove")

    def finish():
        record = install_record()
        found = _enable_extension()
        if not (options.dev or is_system_payload() or linked_here()):
            _write_launcher(state["app_dir"], python)
        INSTALLED.parent.mkdir(parents=True, exist_ok=True)
        # What it found goes by the first install: by an update, those are DeskAI's own settings.
        INSTALLED.write_text(json.dumps({**record, **({} if record else found), "version": version()}) + "\n")

    return [("app", app), ("ai", ai), ("ollama", local_model), ("settings", settings), ("service", service),
            ("hooks", hooks), ("finish", finish)]


def _wait_until_off(*uuids, timeout=EXTENSION_OFF_WAIT_S):
    """Until GNOME has finished turning these off. It does that in steps, and a change to its extension settings in the
    meantime has it turn back on one it's still in the middle of: half there, holding the dock's name, until the next login."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if not any("State: ACTIVE" in said or "State: DEACTIVATING" in said
                       for said in (run("gnome-extensions", "info", uuid, timeout=5).stdout for uuid in uuids)):
                return
        except (OSError, subprocess.SubprocessError):
            return
        time.sleep(0.2)


def uninstall_steps(purge=False):
    """[(id, function)]: what removing DeskAI does. Keeps your modes, presets and stats unless purge."""

    def extension():
        record = install_record()
        settings = Gio.Settings.new("org.gnome.shell")
        settings.set_strv("enabled-extensions",
                          [u for u in settings.get_strv("enabled-extensions") if u not in (UUID, LOGIN_UUID)])
        Gio.Settings.sync()
        _wait_until_off(UUID, LOGIN_UUID)
        if record.get("extensions_were_off"):
            settings.set_boolean("disable-user-extensions", True)
        docks.restore(settings)
        login_screen = _login_screen()
        # An install from before this was noted set it if it was black, and black you chose is yours from now on.
        if login_screen and record.get("login_color_was_unset", True) and login_screen.get_string("background-color") == LOGIN_BACKGROUND:
            login_screen.reset("background-color")
        Gio.Settings.sync()

    def hooks():
        if COMMAND.exists():
            _hooks("remove")
        # Only now: if that failed, the rest still gets removed, and this is where what it said can still be read.
        SETUP_LOG.unlink(missing_ok=True)

    def service():
        run("systemctl", "--user", "disable", "--now", SERVICE)
        UNIT.unlink(missing_ok=True)
        run("systemctl", "--user", "daemon-reload")

    def local_ai():
        localai.remove()

    def files():
        for path in (COMMAND, LAUNCHER, PRESETS_LAUNCHER, ICON):
            path.unlink(missing_ok=True)
        for link in (EXTENSION_LINK, LOGIN_LINK):
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                shutil.rmtree(link)
        shutil.rmtree(SHARE, ignore_errors=True)
        # What a restore set aside. Ptyxis keeps its session up to date, so if it has one again, that's the one that counts.
        session = CONFIG / "org.gnome.Ptyxis" / "session.gvariant"
        set_aside = session.with_name(f"{session.name}.deskai-bak")
        if set_aside.exists():
            if session.exists():
                set_aside.unlink()
            else:
                set_aside.replace(session)

    def polkit():
        # The workday routine's permission to start your database, if you ever gave it. If you close the password
        # window it stays, and so does the note that it's there, for trying again.
        if has_polkit_rule():
            result = run("pkexec", "rm", "-f", str(POLKIT_RULE), timeout=120)
            if result.returncode != 0:
                raise _tool_failed("trouble_polkit_left", result)
            POLKIT_GENERATED.unlink(missing_ok=True)

    def settings():
        if purge:
            for folder in (CONFIG_DIR, STATE / "deskai", CACHE / "deskai"):
                shutil.rmtree(folder, ignore_errors=True)

    # The service goes first: with it still running, the dock going and coming back looks like a new install to it, and it
    # turns off the docks this has just given back.
    return [("service", service), ("local_ai", local_ai), ("extension", extension), ("hooks", hooks), ("files", files),
            ("polkit", polkit), ("settings", settings)]


def run_steps(steps, progress, keep_going=False):
    """progress(step id, running|done|failed, the SetupError or ""). Stops at the first failure, except of what's in CAN_FAIL,
    or with keep_going of anything: removing DeskAI gets as far as it can, whatever failed. Returns whether all that
    had to work did."""
    ok = True
    for step_id, fn in steps:
        progress(step_id, "running", "")
        try:
            fn()
        except Exception as e:  # anything: the window can't say why it's stuck on a step that never ends
            progress(step_id, "failed", report(e, step_id))
            if step_id in CAN_FAIL:
                continue
            ok = False
            if not keep_going:
                return False
            continue
        progress(step_id, "done", "")
    return ok


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge", action="store_true", help="with --uninstall: your modes, presets and stats too")
    args = parser.parse_args()

    failed = []

    # The tool's own words, unlike the window: install.sh and uninstall.sh are for developers, in a terminal, and the
    # window's sentences come with GTK, which this path doesn't need.
    def show(step_id, state, error):
        if state == "failed":
            failed.append(step_id)
        if state != "running":
            print(f"{'✓' if state == 'done' else '✗'} {step_id}" + (f": {error}" if error else ""), flush=True)

    if args.check:
        for check in check_system():
            print(f"{'✓' if check.ok else '✗' if check.required else '-'} {check.id}  {check.detail}")
        return
    if args.uninstall:
        # Uninstalling the package runs this for everyone logged in, and this may be someone who never set DeskAI up, or
        # already removed it. There's nothing of theirs to put back, and no note of what they had.
        if not is_installed():
            return
        run_steps(uninstall_steps(args.purge), show, keep_going=True)
        sys.exit(1 if failed else 0)
    if not args.dev:
        parser.error("use the setup window for a normal install (./setup.sh), or --dev for a developer one")
    choices = current_choices()
    # A developer install starts from whatever AI is around, and keeps the settings already there.
    options = Options(dev=True, keep_config=bool(read_modes()), language=choices["language"] or "en",
                      ai=choices["ai"] or ("claude" if find_claude() else "none"))
    if not run_steps(install_steps(options), show):
        sys.exit(1)
    print("\nLog out and back in: GNOME only picks up an extension when it starts. Then: deskai doctor")
    print("Let Claude Code, Codex and Gemini CLI tell the dock when they're done: deskai hooks install")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
