"""The desktop portal, right after logging out and straight back in.

On Ubuntu, logging out wakes the portal up again (snapd-desktop-integration asks for it) while the
session is still going down. Its GNOME half then can't start ("Dependency failed"), and for minutes
after the next login the portal doesn't answer apps asking for your dark mode and text size: GTK 4 apps
like Files and Ptyxis open white with small text, and Chrome waits for it before opening at all.
Restarting the portal fixes it. This looks once, when DeskAI starts at login.
"""

import subprocess

from gi.repository import Gio, GLib

FRONT = "xdg-desktop-portal.service"
GNOME = "xdg-desktop-portal-gnome.service"
ASK_MS = 2500  # a portal that works answers in milliseconds; a stuck one doesn't answer at all


def unit_state(unit):
    """(LoadState, ActiveState) of one of your services, or None if systemd can't say."""
    try:
        out = subprocess.run(["systemctl", "--user", "show", "-p", "LoadState", "-p", "ActiveState", unit],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    values = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    if "LoadState" not in values:
        return None
    return values["LoadState"], values.get("ActiveState", "")


def gnome_half_down(state=unit_state):
    """The GNOME part of the portal is installed but not running."""
    gnome = state(GNOME)
    return gnome is not None and gnome[0] == "loaded" and gnome[1] in ("inactive", "failed")


def stuck(state=unit_state):
    """The portal is up without its GNOME part: what logging straight back in leaves behind. While it's
    still starting, the GNOME part is "activating", not down."""
    front = state(FRONT)
    return front is not None and front[1] == "active" and gnome_half_down(state)


def restart(log):
    log.warning("portal: up without its GNOME part after logging back in, restarting it")
    # --no-block only queues the restart: a stuck portal can take a while to stop, and DeskAI starting
    # up shouldn't wait for it.
    try:
        subprocess.run(["systemctl", "--user", "--no-block", "restart", GNOME, FRONT],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("portal: can't restart it: %s", e)


def heal(bus, log, state=unit_state, fix=restart):
    """Restarts the portal at once if it's in the stuck state; otherwise asks it for the color scheme,
    and restarts it if no answer comes while its GNOME part is down. A portal that's only slow, or
    missing, or without the Settings part, is left alone."""
    if stuck(state):
        fix(log)
        return

    def answered(connection, result):
        try:
            connection.call_finish(result)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.TIMED_OUT) and gnome_half_down(state):
                fix(log)

    bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
             "org.freedesktop.portal.Settings", "ReadOne",
             GLib.Variant("(ss)", ("org.freedesktop.appearance", "color-scheme")),
             None, Gio.DBusCallFlags.NONE, ASK_MS, None, answered)
