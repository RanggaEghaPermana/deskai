"""A new install's modes filled with the apps you really use, with no AI: what's installed, what
kind of app each one says it is, and how much GNOME has seen you use it. It stays on this computer.
"""

import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

MAX_APPS = 7
UNUSED_FILLERS = 2  # apps you've never opened only fill a mode that has hardly anything else
ENOUGH_USED = 2

KINDS = {
    "building": {"Development", "IDE", "TerminalEmulator", "Database"},
    "writing": {"WordProcessor", "TextEditor", "Publishing"},
    "studying": {"Education", "Science", "Dictionary"},
    "social": {"InstantMessaging", "Chat", "IRCClient", "Telephony", "VideoConference"},
}
FOCUSED = ("building", "writing", "studying")
ASSISTANT = re.compile(r"claude|chatgpt|gemini|copilot|perplexity", re.I)  # no category of their own


def installed_apps():
    """{desktop id: its categories} of the apps shown in the app grid."""
    return {info.get_id(): set((info.get_categories() or "").split(";")) - {""}
            for info in Gio.AppInfo.get_all() if info.should_show() and info.get_id()}


def usage(path=None):
    """{desktop id: how much you use it}, from what GNOME keeps for its Frequent list."""
    path = path or Path(GLib.get_user_data_dir()) / "gnome-shell" / "application_state"
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return {}
    scores = {}
    for app in root.iter("application"):
        try:
            scores[app.get("id")] = float(app.get("score"))
        except (TypeError, ValueError):
            continue
    return scores


def default_browser():
    info = Gio.AppInfo.get_default_for_type("x-scheme-handler/https", False)
    return info.get_id() if info else None


def _picked(key, mode, apps, scores, browser):
    fits = {app for app, categories in apps.items() if categories & KINDS[key]}
    if key in FOCUSED:
        fits |= {app for app in apps if ASSISTANT.search(app)}
    defaults = {app for app in mode.get("apps", []) if app in apps}
    fits |= defaults
    fits.discard(browser)
    by_use = sorted(fits, key=lambda app: (-scores.get(app, 0), app not in defaults, app))
    used = [app for app in by_use if scores.get(app, 0) > 0]
    unused_defaults = [app for app in by_use if app in defaults and app not in used]
    fillers = [app for app in by_use if app not in used and app not in defaults][:UNUSED_FILLERS]
    picked = used + unused_defaults + ([] if len(used) + len(unused_defaults) >= ENOUGH_USED else fillers)
    if browser in apps:
        room = picked[:MAX_APPS - 1]
        picked = sorted([*room, browser], key=lambda app: -scores.get(app, 0))
    return picked[:MAX_APPS]


def fill(modes, apps, scores, browser):
    """Sets the apps of the modes it knows, in place."""
    for key, mode in modes.items():
        if key in KINDS:
            mode["apps"] = _picked(key, mode, apps, scores, browser)


def personalize(config):
    fill(config["modes"], installed_apps(), usage(), default_browser())
