"""DeskAI's dock as your only dock.

The first time the dock is really there, the other docks and taskbars go off, so you never end
up with none. They're only turned off, in GNOME's own settings, never uninstalled. What was
turned off is written down, so removing DeskAI can put it back, and it only happens once: a dock
you turn back on or install afterwards is your choice.
"""

import json
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

SCHEMA = "org.gnome.shell"
USER_EXTENSIONS = Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions"
SYSTEM_EXTENSIONS = Path("/usr/share/gnome-shell/extensions")
SESSION_MODES = Path("/usr/share/gnome-shell/modes")  # how Ubuntu turns on its own dock
RECORD = Path(GLib.get_user_state_dir()) / "deskai" / "docks-off.json"  # {uuid: was it in your own list}

OTHERS = {
    "ubuntu-dock@ubuntu.com": "Ubuntu Dock",
    "dash-to-dock@micxgx.gmail.com": "Dash to Dock",
    "dash-to-panel@jderose9.github.com": "Dash to Panel",
    "dash2dock-lite@icedman.github.com": "Dash2Dock",
    "cosmic-dock@system76.com": "COSMIC Dock",
    "aztaskbar@aztaskbar.gitlab.com": "App Icons Taskbar",
    "floatingDock@sun.wxg@gmail.com": "Floating Dock",
    "dock-from-dash@fthx": "Dock from Dash",
    "docka@devster3000": "Docka",
    "simple-taskbar@sultech": "Simple Taskbar",
    "rocketbar@chepkun.github.com": "Rocketbar",
    "window-list@gnome-shell-extensions.gcampax.github.com": "Window List",
}


def shell_settings():
    source = Gio.SettingsSchemaSource.get_default()
    return Gio.Settings.new(SCHEMA) if source and source.lookup(SCHEMA, True) else None


def _session_extensions():
    found = set()
    for path in SESSION_MODES.glob("*.json"):
        try:
            found.update(json.loads(path.read_text()).get("enabledExtensions", []))
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    return found


def _installed(uuid):
    return (USER_EXTENSIONS / uuid).is_dir() or (SYSTEM_EXTENSIONS / uuid).is_dir()


def turn_off_others(settings=None):
    """The first time: turns the other docks off, and returns their names."""
    settings = settings or shell_settings()
    if not settings or RECORD.exists():
        return []
    enabled = settings.get_strv("enabled-extensions")
    disabled = settings.get_strv("disabled-extensions")
    from_session = _session_extensions()
    off = {uuid: uuid in enabled for uuid in OTHERS
           if uuid not in disabled and (uuid in enabled or uuid in from_session) and _installed(uuid)}

    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(off) + "\n")
    # GNOME's disabled list beats everything else, even the extensions a session turns on itself.
    settings.set_strv("enabled-extensions", [uuid for uuid in enabled if uuid not in off])
    settings.set_strv("disabled-extensions", [*disabled, *off])
    Gio.Settings.sync()
    return [OTHERS[uuid] for uuid in off]


def restore(settings=None):
    """Puts back what turn_off_others changed, and lets it happen again after a reinstall."""
    settings = settings or shell_settings()
    if not settings:
        return
    try:
        off = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return
    if isinstance(off, dict):
        enabled = settings.get_strv("enabled-extensions")
        settings.set_strv("disabled-extensions", [uuid for uuid in settings.get_strv("disabled-extensions") if uuid not in off])
        settings.set_strv("enabled-extensions", [*enabled, *(uuid for uuid, yours in off.items() if yours and uuid not in enabled)])
        Gio.Settings.sync()
    RECORD.unlink(missing_ok=True)
