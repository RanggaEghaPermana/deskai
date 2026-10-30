"""Prints Ptyxis' windows and their tabs, as its accessibility tree shows them, as JSON.

session.py runs this on its own with a timeout, so a terminal that's slow to answer never
holds up the brain. Output: [{"title": "<the window's title>", "tabs": ["<tab title>", ...]}]
in Ptyxis' order. A window with a single tab has no tab bar, so its "tabs" list is empty;
its title is that tab's.
"""

import json

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


def main():
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
