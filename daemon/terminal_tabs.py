"""Prints Ptyxis' windows and their tabs, as its accessibility tree shows them, as JSON.

session.py runs this on its own with a timeout, so a terminal that's slow to answer never
holds up the brain. Output: [{"title": "<the window's title>", "tabs": ["<tab title>", ...]}]
in Ptyxis' order. A window with a single tab has no tab bar, so its "tabs" list is empty;
its title is that tab's.

With --close TITLE CLAUDE_PID SHELL_PID it ends that tab's processes instead, and closes its
window if the tab was the only one there. Ptyxis keeps a tab open when its shell dies from a
signal, to show what happened, so the window's own close button has to finish the job.
"""

import json
import os
import re
import signal
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402


def tabs_of(frame):
    """The tab bar's titles, in order, and how many terminals the window holds."""
    tabs, terminals = [], 0

    def walk(node, depth):
        nonlocal terminals
        role = node.get_role()
        if role == Atspi.Role.TERMINAL:
            terminals += 1
        elif role == Atspi.Role.PANEL and node.get_name():
            tabs.append(node.get_name())  # each tab in the tab bar is a named panel
        if depth < 40:
            for i in range(node.get_child_count()):
                child = node.get_child_at_index(i)
                if child:
                    walk(child, depth + 1)

    walk(frame, 0)
    return tabs, terminals


def frames():
    desktop = Atspi.get_desktop(0)
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if not app or app.get_name() != "ptyxis":
            continue
        for j in range(app.get_child_count()):
            frame = app.get_child_at_index(j)
            if frame and frame.get_role() == Atspi.Role.FRAME:
                yield frame


def close_button(frame):
    """The window's close button: the last button in it, whatever it's called in this language."""
    buttons = []

    def walk(node, depth):
        if node.get_role_name() == "button" and node.get_action_iface():
            buttons.append(node)
        if depth < 40:
            for i in range(node.get_child_count()):
                child = node.get_child_at_index(i)
                if child:
                    walk(child, depth + 1)

    walk(frame, 0)
    return next((b for b in buttons if b.get_name() in ("Close", "Tutup")), buttons[-1] if buttons else None)


def plain(title):
    return re.sub(r"^[^\w(\[]+", "", title.split(" — ")[0]).strip()


def close(title, claude_pid, shell_pid):
    Atspi.set_timeout(1500, 3000)
    found = [frame for frame in frames() if plain(frame.get_name() or "") == title] if title else []
    frame = found[0] if len(found) == 1 and len(tabs_of(found[0])[0]) <= 1 else None
    button = close_button(frame) if frame else None
    for pid, sent in ((shell_pid, signal.SIGHUP), (claude_pid, signal.SIGTERM)):
        try:
            os.kill(pid, sent)
        except OSError:
            pass
    if button:
        time.sleep(0.6)  # with nothing running there it closes without asking
        button.get_action_iface().do_action(0)
    print(json.dumps({"window_closed": bool(button)}))


def main():
    if len(sys.argv) == 5 and sys.argv[1] == "--close":
        close(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
        return
    Atspi.set_timeout(1500, 3000)
    desktop = Atspi.get_desktop(0)
    windows = []
    for i in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(i)
        if not app or app.get_name() != "ptyxis":
            continue
        for j in range(app.get_child_count()):
            frame = app.get_child_at_index(j)
            if not frame or frame.get_role() != Atspi.Role.FRAME:
                continue
            tabs, terminals = tabs_of(frame)
            if terminals:  # not the preferences window
                windows.append({"title": frame.get_name() or "", "tabs": tabs})
    print(json.dumps(windows, ensure_ascii=False))


if __name__ == "__main__":
    main()
