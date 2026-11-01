"""Starting apps so they aren't part of the daemon.

deskaid is a systemd service, and whatever it starts stays in its cgroup however detached it is. Stopping or
restarting the service, or its crashing, would take all of that down with it: your browser, your terminals, your
conversations. So each app gets a systemd scope of its own, the way GNOME Shell starts them.
"""

import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

GIO_WAIT_S = 30  # it only has to start the app, not wait for it


def _user_systemd():
    """systemd-run's full path if there's a user systemd to ask for a scope. Not while testing in dev/nested.sh."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return shutil.which("systemd-run") if runtime and (Path(runtime) / "systemd" / "private").exists() else None


def own_scope(argv):
    """`argv`, set to run in a scope of its own. Its environment and working directory stay what they'd be."""
    systemd_run = _user_systemd()
    if not systemd_run:
        return list(argv)
    name = re.sub(r"[^a-zA-Z0-9_.]", "_", os.path.basename(argv[0]))
    return [systemd_run, "--user", "--scope", "--quiet", "--collect", f"--unit=app-deskai-{name}-{uuid.uuid4().hex[:8]}",
            "--", *argv]


def _started(argv):
    # Not captured: the app inherits these, and reading a pipe it holds open would wait until you close it.
    return subprocess.run(own_scope(argv), timeout=GIO_WAIT_S, check=False).returncode == 0


def app_info(app_id):
    """An installed app by its desktop id, or None."""
    try:
        return Gio.DesktopAppInfo.new(app_id)
    except TypeError:  # how newer PyGObject says there's no such app; older ones return None
        return None


def open_app(app_id):
    """Opens an installed app by its desktop id. False if there's no such app, or it wouldn't start."""
    info = app_info(app_id)
    if not info:
        return False
    return _started(["gio", "launch", info.get_filename()]) if shutil.which("gio") else info.launch([], None)


def open_uri(uri):
    """Opens a page, a file or a folder with the app for it. Raises if nothing could."""
    if not shutil.which("gio"):
        Gio.AppInfo.launch_default_for_uri(uri, None)
    elif not _started(["gio", "open", uri]):
        raise OSError(f"couldn't open {uri}")
