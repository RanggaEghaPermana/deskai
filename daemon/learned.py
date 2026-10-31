"""What DeskAI has learned from you: which windows are work and which aren't, in each mode.

Every correction you make is kept in ~/.local/state/deskai/learned.json, on this computer only. A
correction applies to that exact window from then on. All of them together train a small Naive Bayes
classifier on the words in window titles: a few kilobytes, nothing to load, an answer in under a
millisecond. It only speaks when it's sure and otherwise leaves the decision to the model, so it
grows with use. Verdicts Claude gave earlier count too, for less than your own word.
"""

import json
import math
import re
import time

import brain

LEARNED_FILE = brain.STATE_DIR / "learned.json"
REVIEW_FILE = brain.STATE_DIR / "review.json"
REVIEW_PER_DAY = 3
REVIEW_MIN_S = 300      # a window has to matter before it's worth asking about
KEEP = 3000
CLASSES = ("on_task", "off_task", "neutral")
WEIGHTS = {"you": 5.0, "claude": 1.0}
MIN_EVIDENCE = 3.0      # of a verdict, before it's ever given
CONFIDENT = 0.9
SEEN_WORD = 2.0         # a title needs this much known evidence to go on at all
TOKEN = re.compile(r"[^\W_]{2,}", re.UNICODE)
STOP_WORDS = frozenset("the and for with from your you this that are was dan yang di ke dari untuk dengan ini itu "
                       "google chrome firefox new tab http https www com".split())


def tokens(app_id, title):
    words = [word for word in TOKEN.findall((title or "").lower()) if word not in STOP_WORDS]
    return [*dict.fromkeys(words), f"app:{app_id}"] if app_id else list(dict.fromkeys(words))


def simple_title(title):
    return re.sub(r"\s+", " ", (title or "").strip().lower())


class Learned:
    def __init__(self, path=LEARNED_FILE):
        self.path = path
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        self.examples = data.get("examples", [])
        self.seeded = data.get("seeded", False)
        self._models = {}

    def save(self):
        brain.write_json_atomic(self.path, {"seeded": self.seeded, "examples": self.examples[-KEEP:]})

    def add(self, mode, app_id, title, verdict, source="you", save=True):
        if verdict not in CLASSES or not title:
            return
        key = simple_title(title)
        # The latest word on a window replaces any before it, except that Claude never overrules you.
        same = [e for e in self.examples if e["mode"] == mode and simple_title(e["title"]) == key]
        if source != "you" and any(e["source"] == "you" for e in same):
            return
        self.examples = [e for e in self.examples if e not in same]
        self.examples.append({"mode": mode, "app": app_id, "title": title, "verdict": verdict, "source": source,
                              "time": round(time.time())})
        self._models.pop(mode, None)
        if save:
            self.save()

    def forget(self, mode, title):
        key = simple_title(title)
        before = len(self.examples)
        self.examples = [e for e in self.examples if not (e["mode"] == mode and simple_title(e["title"]) == key)]
        if len(self.examples) != before:
            self._models.pop(mode, None)
            self.save()

    def correction(self, mode, title):
        """What you said about this exact window in this mode, if you did."""
        key = simple_title(title)
        for example in reversed(self.examples):
            if example["source"] == "you" and example["mode"] == mode and simple_title(example["title"]) == key:
                return example["verdict"]
        return None

    def _model(self, mode):
        model = self._models.get(mode)
        if model is None:
            model = {"total": dict.fromkeys(CLASSES, 0.0), "words": dict.fromkeys(CLASSES, 0.0), "counts": {}}
            for example in self.examples:
                if example["mode"] != mode:
                    continue
                weight = WEIGHTS.get(example["source"], 1.0)
                model["total"][example["verdict"]] += weight
                for token in tokens(example.get("app"), example["title"]):
                    model["words"][example["verdict"]] += weight
                    counts = model["counts"].setdefault(token, dict.fromkeys(CLASSES, 0.0))
                    counts[example["verdict"]] += weight
            self._models[mode] = model
        return model

    def predict(self, mode, app_id, title):
        """(verdict, how sure) when it's sure, else None."""
        model = self._model(mode)
        total = sum(model["total"].values())
        if not total:
            return None
        words = tokens(app_id, title)
        known = [word for word in words if word in model["counts"] and not word.startswith("app:")]
        if sum(sum(model["counts"][word].values()) for word in known) < SEEN_WORD:
            return None  # nothing in this title it has seen enough of
        vocabulary = max(len(model["counts"]), 1)
        scores = {}
        for verdict in CLASSES:
            if not model["total"][verdict]:
                continue
            score = math.log(model["total"][verdict] / total)
            for word in words:
                if word in model["counts"]:
                    score += math.log((model["counts"][word][verdict] + 0.5) / (model["words"][verdict] + 0.5 * vocabulary))
            scores[verdict] = score
        best = max(scores, key=scores.get)
        top = max(scores.values())
        sure = math.exp(scores[best] - top) / sum(math.exp(s - top) for s in scores.values())
        if sure < CONFIDENT or model["total"][best] < MIN_EVIDENCE:
            return None
        return best, sure

    def seed_from_verdicts(self, cache, config):
        """Claude's earlier verdicts, for the modes they can still be traced to. Never the local model's."""
        modes = {config.fingerprint(key): key for key, _mode in config.modes()}
        added = 0
        for key, value in cache.items():
            parts = key.split("|")
            if len(parts) < 3 or parts[0] not in modes or (len(parts) == 4 and parts[1] == "ollama"):
                continue
            app_id, title = parts[-2], parts[-1]
            self.add(modes[parts[0]], app_id, title, value.get("verdict"), source="claude", save=False)
            added += 1
        self.seeded = True
        self.save()
        return added


class Unsure:
    """Today's windows it had no opinion on, by time spent: the ones worth asking you about, a few a day.
    The windows are only kept in memory. All that's saved is how many it has asked today."""

    def __init__(self, path=REVIEW_FILE):
        self.path = path
        self.day = None
        self.seen = {}

    def _today(self):
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.day = day
            self.seen = {}
        return day

    def note(self, mode, app_id, app_name, title, seconds):
        self._today()
        if title:
            entry = self.seen.setdefault((mode, simple_title(title)), {
                "mode": mode, "app_id": app_id, "app_name": app_name, "title": title, "seconds": 0.0})
            entry["seconds"] += seconds

    def asked_today(self):
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return 0
        return data.get("asked", 0) if data.get("day") == self._today() else 0

    def next(self, mode, known):
        """The window most worth asking about in this mode, taking it off the list. None if there isn't one."""
        if self.asked_today() >= REVIEW_PER_DAY:
            return None
        waiting = [entry for entry in self.seen.values() if entry["mode"] == mode and entry["seconds"] >= REVIEW_MIN_S
                   and not known.correction(mode, entry["title"])]
        if not waiting:
            return None
        entry = max(waiting, key=lambda e: e["seconds"])
        del self.seen[(mode, simple_title(entry["title"]))]
        brain.write_json_atomic(self.path, {"day": self._today(), "asked": self.asked_today() + 1})
        return entry
