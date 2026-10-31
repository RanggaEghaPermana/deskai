#!/usr/bin/env python3
"""How the window pipeline does on your own past windows, against the verdicts Claude gave them.

Reads ~/.cache/deskai/verdicts.json on this computer and prints only numbers, never a title. The worst
mistake is a false alarm, flagging as a distraction something that isn't: it interrupts you for nothing.
Each window is judged by a classifier trained on all the others.

    python3 evals/real.py
"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

import brain  # noqa: E402
import learned  # noqa: E402
import sites  # noqa: E402


def main():
    try:
        verdicts = json.loads((brain.CACHE_DIR / "verdicts.json").read_text())
    except (OSError, ValueError):
        sys.exit("No verdicts from Claude on this computer yet.")
    config = brain.Config()
    config.refresh()
    modes = {config.fingerprint(key): key for key, _mode in config.modes()}
    focus = next((key for key, mode in config.modes() if mode.get("focus")), None)
    rows = []
    for key, value in verdicts.items():
        parts = key.split("|")
        if len(parts) >= 3 and not (len(parts) == 4 and parts[1] == "ollama") and not parts[-2].startswith("window:"):
            rows.append((modes.get(parts[0], "building" if config.mode("building") else focus), parts[-2], parts[-1], value["verdict"]))
    tally, who = Counter(), Counter()
    for index, (mode_key, app, title, truth) in enumerate(rows):
        mode = config.mode(mode_key)
        ruled = brain.rule_verdict(mode, app, app, title, config.language)
        plain = sites.window_verdict(mode, app, title)
        with tempfile.TemporaryDirectory() as folder:
            others = learned.Learned(Path(folder) / "x.json")
            others.examples = [{"mode": m, "app": a, "title": t, "verdict": v, "source": "claude", "time": 0}
                               for i, (m, a, t, v) in enumerate(rows) if i != index]
            guess = others.predict(mode_key, app, title)
        answer, by = (ruled["verdict"], "lists") if ruled else (plain[0], "built-in") if plain else \
            (guess[0], "classifier") if guess else ("neutral", "no opinion")
        who[by] += 1
        tally["right" if answer == truth else "false alarm" if answer == "off_task" else
              "missed distraction" if truth == "off_task" else "other mix-up"] += 1
    print(f"{len(rows)} windows · decided by: {dict(who)}")
    print(f"agrees with Claude: {tally['right']}/{len(rows)}")
    print(f"false alarms: {tally['false alarm']} · missed distractions: {tally['missed distraction']} · "
          f"neutral and on-task mixed up: {tally['other mix-up']}")


if __name__ == "__main__":
    main()
