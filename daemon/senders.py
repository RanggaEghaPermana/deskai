"""Who gets through during a focus mode, by your word.

Without Claude to judge them, notifications that are neither plainly urgent nor plainly ads stay in
the tray. So the first time someone new lands there, the dock asks once, and the answer is kept in
~/.local/state/deskai/senders.json, on this computer only: names, never what they said.
"""

import json
import re
import time

import brain

SENDERS_FILE = brain.STATE_DIR / "senders.json"
ASKS_PER_DAY = 5    # an app with a new title on every message shouldn't keep asking
ASK_AGAIN_S = 86400  # no answer: it might have been a bad moment
QUIET_AFTER = 2      # held this many from an app, and let none through: stop asking about that app
FORGET_ASKED_S = 30 * 86400
ALWAYS, HOLD = "always", "hold"
COUNTER = re.compile(r"\s*[(\[]\d+[^)\]]*[)\]]\s*$")  # "Rina (3 pesan)" is still Rina


def name_of(app, title):
    return COUNTER.sub("", (title or "").strip()) or (app or "").strip()


def key_of(app, title):
    return f"{(app or '').strip().lower()}|{name_of(app, title).lower()}"


def can_take_whole_app(app, title):
    """"Everything from Slack" makes sense. "Everything from Chrome" would be every website."""
    return bool(app) and name_of(app, title).lower() != app.strip().lower() and not brain.BROWSER_NAMES.search(app)


class Senders:
    def __init__(self, path=SENDERS_FILE):
        self.path = path
        self.people = {}  # key → {"name", "app", "word"}
        self.apps = {}    # app in lowercase → {"name", "word"}
        self.asked = {}   # key → when
        self.seen = False
        self._read()

    def _stamp(self):
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_ino, stat.st_size

    def _read(self):
        """Takes in what's in the file if it isn't what was last seen there: `deskai senders --forget` runs apart from the daemon."""
        stamp = self._stamp()
        if stamp == self.seen:
            return
        self.seen = stamp
        try:
            data = json.loads(self.path.read_text()) if stamp else {}
        except (OSError, ValueError):
            data = None
        # Half-saved, or a typo in an editor: that mustn't take what you've said with it.
        if isinstance(data, dict):
            self.people = data.get("people", {})
            self.apps = data.get("apps", {})
            self.asked = data.get("asked", {})

    def save(self):
        now = time.time()
        self.asked = {key: when for key, when in self.asked.items() if now - when < FORGET_ASKED_S}
        brain.write_json_atomic(self.path, {"people": self.people, "apps": self.apps, "asked": self.asked})
        self.seen = self._stamp()

    def word(self, app, title):
        """ALWAYS, HOLD, or None if you haven't said. Your word on a person beats your word on their app."""
        self._read()
        person = self.people.get(key_of(app, title))
        if person:
            return person["word"]
        whole = self.apps.get((app or "").strip().lower())
        return whole["word"] if whole else None

    def remember(self, app, title, word, whole_app=False):
        self._read()
        if whole_app:
            self.apps[app.strip().lower()] = {"name": app.strip(), "word": word}
        else:
            self.people[key_of(app, title)] = {"name": name_of(app, title), "app": (app or "").strip(), "word": word}
        self.save()

    def forget(self, app, title, whole_app=False):
        """Takes back your word. Returns whether there was any."""
        self._read()
        found = self.apps.pop(app.strip().lower(), None) if whole_app else self.people.pop(key_of(app, title), None)
        self.asked.pop(key_of(app, title), None)
        self.save()
        return bool(found)

    def forget_name(self, name):
        """Takes back your word on anyone or any app by this name, for the command line."""
        self._read()
        wanted = name.strip().lower()
        people = [key for key, person in self.people.items() if person["name"].lower() == wanted]
        apps = [key for key, app in self.apps.items() if app["name"].lower() == wanted]
        for key in people:
            del self.people[key]
        for key in apps:
            del self.apps[key]
        self.save()
        return len(people) + len(apps)

    def listing(self):
        """[(name, app or "", word)], what's kept."""
        self._read()
        return [*((person["name"], person["app"], person["word"]) for person in self.people.values()),
                *((app["name"], "", app["word"]) for app in self.apps.values())]

    def _app_is_quiet(self, app):
        words = [person["word"] for person in self.people.values() if person["app"].lower() == (app or "").strip().lower()]
        return words.count(HOLD) >= QUIET_AFTER and ALWAYS not in words

    def should_ask(self, app, title, now=None):
        """Whether to ask about this sender now. Saying yes counts as having asked."""
        now = time.time() if now is None else now
        key = key_of(app, title)
        self._read()
        if self.word(app, title) or (key in self.asked and now - self.asked[key] < ASK_AGAIN_S) or self._app_is_quiet(app):
            return False
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        if sum(time.strftime("%Y-%m-%d", time.localtime(when)) == today for when in self.asked.values()) >= ASKS_PER_DAY:
            return False
        self.asked[key] = now
        self.save()
        return True
