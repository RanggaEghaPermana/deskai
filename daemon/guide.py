"""The first-run guide, and asking once for WhatsApp's notifications.

The dock shows a short tour the first couple of times it comes up, until you've seen it or
skipped it. WhatsApp Web can only reach the dock's inbox if Chrome may show its notifications,
and it only asks by itself once: so the first time you open it without that, the dock offers
to open the setting.
"""

import json
from pathlib import Path

import brain

GUIDE_FILE = brain.STATE_DIR / "guide.json"
STEPS = ("modes", "inbox", "command")
MAX_SHOWN = 2  # started this many times without being finished: enough, stop offering
CHROME_PREFERENCES = Path.home() / ".config" / "google-chrome" / "Default" / "Preferences"
WHATSAPP = "https://web.whatsapp.com:443"
WHATSAPP_SETTINGS = "chrome://settings/content/siteDetails?site=https%3A%2F%2Fweb.whatsapp.com"
ALLOWED = 1


def read():
    try:
        return json.loads(GUIDE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def write(**values):
    brain.write_json_atomic(GUIDE_FILE, {**read(), **values})


def due():
    state = read()
    return not state.get("done") and state.get("shown", 0) < MAX_SHOWN


def started():
    write(shown=read().get("shown", 0) + 1)


def finished():
    write(done=True)


def whatsapp_blocked():
    """Whether Chrome is around and WhatsApp Web can't show notifications there yet."""
    try:
        prefs = json.loads(CHROME_PREFERENCES.read_text())
    except (OSError, ValueError):
        return False  # no Chrome profile to look at
    exceptions = prefs.get("profile", {}).get("content_settings", {}).get("exceptions", {}).get("notifications", {})
    setting = next((v.get("setting") for k, v in exceptions.items() if k.split(",")[0] == WHATSAPP), None)
    return setting != ALLOWED


def ask_whatsapp():
    """Whether to offer the setting now: at most once ever."""
    if read().get("whatsapp_asked"):
        return False
    write(whatsapp_asked=True)
    return whatsapp_blocked()
