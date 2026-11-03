"""What your own words mean, once you've said so: the requests DeskAI didn't understand by itself.

When the lists can't place a request, the card can ask the AI, which guesses. Saying yes to its guess keeps the
request and what it meant in ~/.local/state/deskai/phrases.json, on this computer only. The next time you say it,
it's done at once, with no AI, and the local model sees the latest of them as examples of how you say things.

Only what can be a step of a request is kept (commands.STEPS): a mode, opening something, a server, setting up or
closing a project. Never a command, so nothing here can run anything the command bar couldn't anyway.
"""

import json
import time

import brain

PHRASES_FILE = brain.STATE_DIR / "phrases.json"
KEEP = 200           # the ones you've used least lately go first
AS_EXAMPLES = 8      # the most recent, for a local model to learn from
MAX_WORDS = 160      # a request this long is a paragraph, not something you'll say again


class Phrases:
    def __init__(self, path=PHRASES_FILE):
        self.path = path
        self.known = {}  # what you said, as commands.plain() has it → {"actions", "used", "words": as you typed it}
        self.seen = False
        self._read()

    def _stamp(self):
        try:
            stat = self.path.stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_ino, stat.st_size

    def _read(self):
        """Takes in the file if it isn't what was last seen there: `deskai phrases --forget` runs apart from the daemon."""
        stamp = self._stamp()
        if stamp == self.seen:
            return
        self.seen = stamp
        try:
            data = json.loads(self.path.read_text()) if stamp else {}
        except (OSError, ValueError):
            return  # half-saved, or a typo in an editor: that mustn't take what you've taught with it
        if isinstance(data, dict):
            self.known = {said: entry for said, entry in data.items()
                          if isinstance(said, str) and isinstance(entry, dict) and isinstance(entry.get("actions"), list)
                          and all(isinstance(action, dict) and isinstance(action.get("type"), str) for action in entry["actions"])}

    def save(self):
        newest = sorted(self.known.items(), key=lambda item: item[1].get("used", 0))[-KEEP:]
        self.known = dict(newest)
        brain.write_json_atomic(self.path, self.known)
        self.seen = self._stamp()

    def recall(self, said):
        """What you told DeskAI these words mean, or None. `said` as commands.plain() has it."""
        self._read()
        entry = self.known.get(said)
        if not entry:
            return None
        entry["used"] = time.time()
        try:
            self.save()
        except OSError:
            pass  # a full disk only costs the order they're kept in
        return [dict(action) for action in entry["actions"]]

    def remember(self, said, actions, words=""):
        """words: the request as you typed it, for showing. `said` is what it's looked up by."""
        if not said or len(said) > MAX_WORDS or not actions:
            return
        self._read()
        self.known[said] = {"actions": [dict(action) for action in actions], "used": time.time(), "words": (words or said)[:MAX_WORDS]}
        self.save()

    def forget(self, said):
        self._read()
        if self.known.pop(said, None) is None:
            return False
        self.save()
        return True

    def listing(self):
        """[(what you said, as you typed it, the actions)], the most recently used first."""
        self._read()
        return [(str(entry.get("words") or said), entry["actions"])
                for said, entry in sorted(self.known.items(), key=lambda item: -item[1].get("used", 0))]

    def examples(self):
        """The latest of them, the way the model's other examples are written."""
        return "\n".join(f'"{said}" -> {json.dumps(actions, ensure_ascii=False)}' for said, actions in self.listing()[:AS_EXAMPLES])
