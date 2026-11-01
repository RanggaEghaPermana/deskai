"""DeskAI's dock as your only dock.

While the dock is really there, the other docks and taskbars stay off, so you never have two on top of each other
and never end up with none. They're only turned off, in GNOME's own settings, never uninstalled. What was turned off
is written down, so removing DeskAI can put it back. This goes on for as long as DeskAI is here: one that comes back on,
from an update or a reset, goes off again, and the dock itself refuses to have one turned on (docks.js in the extension).
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

# The dock has the same list, in docks.js, and a test keeps them the same.
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


def _recorded():
    try:
        off = json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}
    return off if isinstance(off, dict) else {}


def turn_off_others(settings=None):
    """Turns off whichever other docks are on, and returns their names. It can run at any time: with none on it does nothing."""
    settings = settings or shell_settings()
    if not settings:
        return []
    enabled = settings.get_strv("enabled-extensions")
    disabled = settings.get_strv("disabled-extensions")
    from_session = _session_extensions()
    off = {uuid: uuid in enabled for uuid in OTHERS
           if uuid not in disabled and (uuid in enabled or uuid in from_session) and _installed(uuid)}
    if not off:
        return []

    # The first word on each is what counts: what you had before DeskAI was here.
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps({**off, **_recorded()}) + "\n")
    # GNOME's disabled list beats everything else, even the extensions a session turns on itself. It's the only one
    # changed: GNOME turns extensions off in steps, and a second change in the meantime can turn one back on halfway.
    settings.set_strv("disabled-extensions", [*disabled, *off])
    Gio.Settings.sync()
    return [OTHERS[uuid] for uuid in off]


def restore(settings=None):
    """Puts back what turn_off_others changed."""
    settings = settings or shell_settings()
    if not settings:
        return
    off = _recorded()
    if off:
        enabled = settings.get_strv("enabled-extensions")
        # Only what an older version took out of your own list has to go back in.
        missing = [uuid for uuid, yours in off.items() if yours and uuid not in enabled]
        if missing:
            settings.set_strv("enabled-extensions", [*enabled, *missing])
        settings.set_strv("disabled-extensions", [uuid for uuid in settings.get_strv("disabled-extensions") if uuid not in off])
        Gio.Settings.sync()
    RECORD.unlink(missing_ok=True)
