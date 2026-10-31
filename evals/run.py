#!/usr/bin/env python3
"""Scores the AI pipeline on set cases, the way the daemon really uses it: lists first, then the model.

    python3 evals/run.py [--model qwen3:1.7b] [--language id] [--min 0.85]

Needs Ollama running with the model. Run it before and after changing a prompt or a list.
"""

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "daemon"), str(ROOT / "evals")]

import brain  # noqa: E402
import cases  # noqa: E402
import commands  # noqa: E402
import servers  # noqa: E402
import sites  # noqa: E402

UUID = "deskai@ranggaeghapermana.github.io"
FIELD_CHECKS = {
    "project": lambda value: commands.find_project(value, cases.PROJECTS),
    "path": lambda value: commands.find_project(value, cases.PROJECTS),
}


def scored(section, name, fn, check, rows):
    start = time.monotonic()
    try:
        result = fn()
    except (brain.LLMError, ValueError, KeyError) as e:
        result = {"error": str(e)}
    seconds = time.monotonic() - start
    right, by, detail = check(result)
    rows.append({"section": section, "right": right, "by": by, "seconds": seconds})
    print(f"{'✓' if right else '✗'} {seconds:5.1f}s {section:<8} {by:<6} {name[:48]:<48} {'' if right else detail[:100]}", flush=True)


def windows(config, llm, rows):
    mode = config.mode("building")
    for app_id, app, title, wanted in cases.WINDOWS:
        def decide():
            ruled = brain.rule_verdict(mode, app_id, app, title, config.language)
            if ruled:
                return {"verdict": ruled["verdict"], "by": "list"}
            plain = sites.window_verdict(mode, app_id, title)
            if plain:
                return {"verdict": plain[0], "by": "list"}
            return {"verdict": "neutral", "by": "list"}  # a local model doesn't judge windows: see deskaid._classify
        scored("window", title, decide, lambda r, wanted=wanted: (r.get("verdict") in wanted, r.get("by", "-"), str(r)), rows)


def notifications(rows):
    for app, title, body, wanted in cases.NOTIFICATIONS:
        def decide():
            # A local model doesn't judge notifications either: what isn't plain waits in the tray.
            return {"surface": bool(sites.notification_verdict(app, title, body)), "by": "list"}
        scored("notif", f"{app}: {body}", decide, lambda r, wanted=wanted: (bool(r.get("surface")) == wanted, r.get("by", "-"), str(r)), rows)


def command_right(result, kind, fields):
    actions = result.get("actions", [])
    if kind == "nothing":
        return not actions
    if kind == "answer":
        return all(value in result.get("say", "") for value in fields.values())
    for action in actions:
        if action.get("type") != kind:
            continue
        ok = True
        for field, wanted in fields.items():
            value = str(action.get(field, ""))
            if field in FIELD_CHECKS:
                value = str(FIELD_CHECKS[field](value) or "")
            elif field == "app":
                value = next((line.lower() for line in cases.APPS.splitlines() if value and value.lower() in line.lower()), value.lower())
            ok = ok and wanted in value.lower()
        if ok:
            return True
    return False


def command_cases(config, llm, rows):
    watch = servers.Watch()
    for unit, label, command, port, state in cases.SERVERS:
        watch.items[unit] = {"unit": unit, "kind": "server", "label": label, "command": command, "cwd": "/projects/x", "port": port,
                             "state": state, "misses": 0, "started": 0, "since": 0}
    desk = commands.desk(config, "building", watch, True, known_projects=cases.PROJECTS, focus=cases.FOCUS, apps=cases.APPS)
    labels = [(key, mode["label"]) for key, mode in config.modes()]
    for text, (kind, fields) in cases.COMMANDS:
        def decide():
            quick = commands.quick(text, labels, watch, cases.PROJECTS)
            if quick:
                return {**quick, "by": "list"}
            answer = commands.interpret(llm, config.language, text, desk)
            return {**commands.only_what_was_asked(answer, text, cases.PROJECTS), "by": "model"}
        scored("command", text, decide, lambda r, kind=kind, fields=fields: (command_right(r, kind, fields), r.get("by", "-"), str(r)), rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=brain.LLM_DEFAULTS["ollama_model"])
    parser.add_argument("--language", default="id", choices=["en", "id"])
    parser.add_argument("--min", type=float, default=0.0, help="fail below this share of right answers")
    args = parser.parse_args()
    config = brain.Config(ROOT / "extension" / UUID / "modes.default.json")
    config.refresh()
    config.data = {**config.data, "language": args.language}
    llm = brain.LLM({**brain.LLM_DEFAULTS, "backend": "ollama", "ollama_model": args.model})
    problem = llm.problem()
    if problem:
        sys.exit(problem)
    rows = []
    windows(config, llm, rows)
    notifications(rows)
    command_cases(config, llm, rows)
    print()
    for section in ("window", "notif", "command"):
        part = [r for r in rows if r["section"] == section]
        by = Counter(r["by"] for r in part)
        model_times = sorted(r["seconds"] for r in part if r["by"] == "model") or [0]
        print(f"{section:<8} {sum(r['right'] for r in part)}/{len(part)} right · {by['list']} without the model"
              f" · model median {model_times[len(model_times) // 2]:.1f}s")
    score = sum(r["right"] for r in rows) / len(rows)
    print(f"total    {sum(r['right'] for r in rows)}/{len(rows)} = {score:.0%}")
    sys.exit(1 if score < args.min else 0)


if __name__ == "__main__":
    main()
