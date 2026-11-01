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
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "daemon"))

import docks  # noqa: E402
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
LAUNCHER = DATA / "applications" / f"{APP_ID}.Setup.desktop"
ICON = DATA / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"

# What the AI choices mean in modes.json's llm.backend.
BACKENDS = {"claude": "claude-cli", "api": "api", "ollama": "ollama", "none": "none"}
AGENT_DIRS = {"Claude Code": (".claude", "claude"), "Codex": (".codex", "codex"), "Gemini CLI": (".gemini", "gemini")}
# Packages by package manager, for what the checks can offer to install.
PACKAGES = {
    "atspi": {"apt-get": "gir1.2-atspi-2.0", "dnf": "at-spi2-core", "pacman": "at-spi2-core", "zypper": "typelib-1_0-Atspi-2_0"},
}


class SetupError(Exception):
    """A step failed, with a message worth showing."""


# ---- what's on this computer ------------------------------------------------------------

@dataclass
class Check:
    id: str
    ok: bool
    required: bool = False
    detail: str = ""           # what was found
    package: str = ""          # installable with install_package()


def run(*args, timeout=30, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, **kwargs)


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


def find_ollama():
    for candidate in [shutil.which("ollama"), HOME / ".local/bin/ollama"]:
        if candidate and os.access(candidate, os.X_OK):
            return str(candidate)
    return ""


def ollama_model():
    """The local model the default settings use."""
    defaults = read_json(ROOT / "extension" / UUID / "modes.default.json") or {}
    return (defaults.get("llm") or {}).get("ollama_model", "qwen3:1.7b")


def detected_agents():
    """The AI coding agents on this computer, by name."""
    return [label for label, (folder, exe) in AGENT_DIRS.items() if (HOME / folder).exists() or shutil.which(exe)]


def hooks_installed():
    """Whether any AI agent already has DeskAI's hooks in its settings."""
    for name in (".claude/settings.json", ".gemini/settings.json", ".codex/config.toml"):
        try:
            if "deskai" in (HOME / name).read_text() and "hook" in (HOME / name).read_text():
                return True
        except OSError:
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
        raise SetupError(f"Install {package} with your package manager.")
    command = {"pacman": ["pacman", "-S", "--noconfirm", package]}.get(pm, [pm, "install", "-y", package])
    result = run("pkexec", *command, timeout=600)
    if result.returncode != 0:
        raise SetupError((result.stderr or result.stdout).strip()[-400:] or f"Couldn't install {package}.")


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


def installed_version():
    try:
        return json.loads(INSTALLED.read_text()).get("version", "")
    except (OSError, ValueError):
        return ""


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def current_choices():
    """The settings an earlier install left, to start the setup window from."""
    modes = read_json(MODES_FILE) or {}
    backend = (modes.get("llm") or {}).get("backend", "")
    ai = {v: k for k, v in BACKENDS.items()}.get(backend, "")
    if backend == "auto":
        ai = "api" if has_api_key() else "claude" if find_claude() else "ollama" if find_ollama() else "none"
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
        raise SetupError("That API key has spaces in it.")
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


def _write_private_json(path, data):
    temp = path.with_suffix(".json.new")
    temp.touch(mode=0o600)
    temp.chmod(0o600)
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temp.replace(path)


def _write_config(app_dir, options):
    """modes.json with this language and AI choice, keeping any modes you already have."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    defaults = app_dir / "extension" / UUID / "modes.default.json"
    modes = read_json(MODES_FILE)
    if not modes:
        modes = json.loads(defaults.read_text())
        try:
            personalize.personalize(modes)
        except (OSError, ValueError, GLib.Error) as e:
            print(f"Keeping the default apps: {e}", file=sys.stderr)
    modes["language"] = options.language
    # Claude's login on "auto": if Claude ever stops working and Ollama is set up, it carries on with that.
    backend = "auto" if options.ai == "claude" and not has_api_key() else BACKENDS[options.ai]
    modes.setdefault("llm", {})["backend"] = backend
    _write_private_json(MODES_FILE, modes)
    routine = read_json(ROUTINE_FILE) or {}
    routine["enabled"] = options.routine
    _write_private_json(ROUTINE_FILE, routine)


def _make_venv(python):
    if not (VENV / "bin" / "python").exists():
        result = run(python, "-m", "venv", "--system-site-packages", str(VENV), timeout=180)
        if result.returncode != 0:
            raise SetupError("Python couldn't make a virtual environment. "
                             "On Ubuntu and Debian, install python3-venv and try again.\n" + result.stderr[-300:])
    result = run(str(VENV / "bin" / "pip"), "install", "--quiet", "--upgrade", "anthropic", timeout=300)
    if result.returncode != 0:
        raise SetupError("Couldn't download the anthropic package. Check your connection.\n" + result.stderr[-300:])


def _pull_ollama_model(model):
    """Downloads the local model, if it isn't here yet."""
    ollama = find_ollama()
    if not ollama:
        raise SetupError("Ollama isn't installed. Get it from ollama.com and try again.")
    result = run(ollama, "pull", model, timeout=3600)
    if result.returncode != 0:
        raise SetupError(f"Couldn't download {model}. Is Ollama running?\n" + (result.stderr or result.stdout)[-300:])


def _service_path():
    """systemd user services don't get your shell's PATH, and the brain needs `claude`."""
    claude = find_claude()
    dirs = [os.path.dirname(claude) if claude else "", str(COMMAND.parent),
            *(os.environ.get("PATH", "").split(":")), "/usr/local/bin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys(d for d in dirs if d))


def _write_service(app_dir, python):
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
    for command in (("daemon-reload",), ("enable", SERVICE), ("restart", SERVICE)):
        result = run("systemctl", "--user", *command)
        if result.returncode != 0:
            raise SetupError(f"systemctl --user {' '.join(command)} failed: {result.stderr.strip()[-300:]}")


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
    """The setup window in the app grid, to update or remove later. The .deb ships its own."""
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
    ICON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(app_dir / "setup" / f"{APP_ID}.svg", ICON)


def _hooks(action):
    env = {**os.environ, "PATH": f"{COMMAND.parent}:{os.environ.get('PATH', '')}"}
    result = run(str(COMMAND), "hooks", action, env=env, timeout=60)
    if result.returncode != 0:
        raise SetupError((result.stderr or result.stdout).strip()[-300:])


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


def _enable_extension():
    """Turns the extensions on for the next login: GNOME only reads new extensions when it starts."""
    settings = Gio.Settings.new("org.gnome.shell")
    ours = (UUID, LOGIN_UUID)
    enabled = settings.get_strv("enabled-extensions")
    settings.set_strv("enabled-extensions", [*enabled, *(u for u in ours if u not in enabled)])
    settings.set_strv("disabled-extensions", [u for u in settings.get_strv("disabled-extensions") if u not in ours])
    settings.set_boolean("disable-user-extensions", False)
    # Ubuntu fills the screen with this before the desktop appears: gray by default, and a flash at login.
    login_screen = _login_screen()
    if login_screen and login_screen.get_user_value("background-color") is None:
        login_screen.set_string("background-color", LOGIN_BACKGROUND)
    Gio.Settings.sync()


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
                raise SetupError("No API key entered.")
        if options.ai == "api":
            _make_venv(python)

    def local_model():
        if options.ai == "ollama":
            _pull_ollama_model(ollama_model())

    def settings():
        if not options.keep_config:
            _write_config(state["app_dir"], options)

    def service():
        # An existing venv stays in use: it may hold the SDK this install relies on.
        chosen = str(VENV / "bin" / "python") if (VENV / "bin" / "python").exists() else python
        _write_command(state["app_dir"], chosen)
        _write_service(state["app_dir"], chosen)

    def hooks():
        if options.keep_config:
            return
        if options.hooks:
            _hooks("install")
        elif hooks_installed():
            _hooks("remove")

    def finish():
        _enable_extension()
        if not (options.dev or is_system_payload() or linked_here()):
            _write_launcher(state["app_dir"], python)
        INSTALLED.parent.mkdir(parents=True, exist_ok=True)
        INSTALLED.write_text(json.dumps({"version": version()}) + "\n")

    return [("app", app), ("ai", ai), ("ollama", local_model), ("settings", settings), ("service", service),
            ("hooks", hooks), ("finish", finish)]


def uninstall_steps(purge=False):
    """[(id, function)]: what removing DeskAI does. Keeps your modes and stats unless purge."""

    def extension():
        settings = Gio.Settings.new("org.gnome.shell")
        settings.set_strv("enabled-extensions",
                          [u for u in settings.get_strv("enabled-extensions") if u not in (UUID, LOGIN_UUID)])
        docks.restore(settings)
        login_screen = _login_screen()
        if login_screen and login_screen.get_string("background-color") == LOGIN_BACKGROUND:
            login_screen.reset("background-color")
        Gio.Settings.sync()

    def hooks():
        if COMMAND.exists():
            _hooks("remove")

    def service():
        run("systemctl", "--user", "disable", "--now", SERVICE)
        UNIT.unlink(missing_ok=True)
        run("systemctl", "--user", "daemon-reload")

    def files():
        for path in (COMMAND, LAUNCHER, ICON):
            path.unlink(missing_ok=True)
        for link in (EXTENSION_LINK, LOGIN_LINK):
            if link.is_symlink():
                link.unlink()
            elif link.exists():
                shutil.rmtree(link)
        shutil.rmtree(SHARE, ignore_errors=True)

    def polkit():
        # The workday routine's permission to start your database, if you ever gave it.
        if has_polkit_rule():
            run("pkexec", "rm", "-f", str(POLKIT_RULE), timeout=120)
            POLKIT_GENERATED.unlink(missing_ok=True)

    def settings():
        if purge:
            for folder in (CONFIG_DIR, STATE / "deskai", CACHE / "deskai"):
                shutil.rmtree(folder, ignore_errors=True)

    return [("extension", extension), ("hooks", hooks), ("service", service), ("files", files),
            ("polkit", polkit), ("settings", settings)]


def run_steps(steps, progress):
    """progress(step id, running|done|failed, error text). Stops at the first failure; returns success."""
    for step_id, fn in steps:
        progress(step_id, "running", "")
        try:
            fn()
        except (SetupError, OSError, subprocess.SubprocessError, ValueError) as e:
            progress(step_id, "failed", str(e))
            return False
        progress(step_id, "done", "")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--purge", action="store_true", help="with --uninstall: your modes and stats too")
    args = parser.parse_args()

    def show(step_id, state, error):
        if state != "running":
            print(f"{'✓' if state == 'done' else '✗'} {step_id}" + (f": {error}" if error else ""), flush=True)

    if args.check:
        for check in check_system():
            print(f"{'✓' if check.ok else '✗' if check.required else '-'} {check.id}  {check.detail}")
        return
    if args.uninstall:
        sys.exit(0 if run_steps(uninstall_steps(args.purge), show) else 1)
    if not args.dev:
        parser.error("use the setup window for a normal install (./setup.sh), or --dev for a developer one")
    choices = current_choices()
    # A developer install starts from whatever AI is around, and keeps the settings already there.
    options = Options(dev=True, keep_config=bool(read_json(MODES_FILE)), language=choices["language"] or "en",
                      ai=choices["ai"] or ("claude" if find_claude() else "none"))
    if not run_steps(install_steps(options), show):
        sys.exit(1)
    print("\nLog out and back in: GNOME only picks up an extension when it starts. Then: deskai doctor")
    print("Let Claude Code, Codex and Gemini CLI tell the dock when they're done: deskai hooks install")


if __name__ == "__main__":
    main()
