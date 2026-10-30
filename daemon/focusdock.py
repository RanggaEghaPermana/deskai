#!/usr/bin/env python3
"""focusdock: control FocusDock from the terminal (or from your agent)."""

import argparse
import difflib
import json
import os
import shutil
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

import brain  # noqa: E402
import hooks  # noqa: E402
import routine  # noqa: E402
import session  # noqa: E402
from brain import BRAIN_NAME, BUS_NAME, OBJECT_PATH  # noqa: E402

UUID = "focusdock@ranggaeghapermana.github.io"


def bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def call(method, signature=None, args=None):
    params = GLib.Variant(signature, args) if signature else None
    try:
        result = bus().call_sync(BUS_NAME, OBJECT_PATH, BUS_NAME, method, params, None,
                                 Gio.DBusCallFlags.NONE, 5000, None)
    except GLib.Error as e:
        sys.exit(f"The dock isn't answering ({e.message}).\nIs the extension active? Try: focusdock doctor")
    return result.unpack()


def name_has_owner(name):
    result = bus().call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                             "NameHasOwner", GLib.Variant("(s)", (name,)), None,
                             Gio.DBusCallFlags.NONE, 2000, None)
    return result.unpack()[0]


def load_config():
    config = brain.Config()
    config.refresh()
    if not config.data["modes"]:
        sys.exit(f"{brain.MODES_FILE} is missing or broken. The extension creates it when it first runs.")
    return config


def minutes(seconds):
    return f"{seconds / 60:.0f}m" if seconds < 3600 else f"{seconds / 3600:.1f}j"


# ---- commands -------------------------------------------------------------

def cmd_status(_args):
    state = json.loads(call("GetState")[0])
    config = load_config()
    mode = config.mode(state["mode"]) or {"label": state["mode"]}
    focus = state["focus"]
    print(f"Mode   : {mode['label']} ({state['mode']})")
    print(f"Window : {focus['appName'] or '-'} · {focus['title'] or '-'}")
    print(f"Held   : {len(state['held'])} notifications")
    for held in state["held"]:
        print(f"         - {held['app']}: {held['title']}")
    print(f"Brain  : {'online' if state['brain'] else 'offline'} · backend {brain.Claude(config.llm).backend}")
    inbox = state.get("inbox", [])
    print(f"Inbox  : {len(inbox)} items")
    for item in inbox:
        print(f"         - [{item['kind']}] {item['title']}" + (f" ({item['window']})" if item.get("window") else ""))
    dock = state.get("dock", {})
    if "hover" in dock:
        held_up = [name for name, on in (
            ("pointer on it", dock["hover"] and dock.get("pointerRestMs", 0) < 3000),
            ("edge", dock.get("edgeArmed")), ("card", dock.get("card")), ("menu", dock.get("menu")),
            ("drag", dock.get("drag")), ("peek", dock.get("peekMs", 0) > 0)) if on]
        why = ", ".join(held_up) or ("no app window open" if not dock.get("appWindowOpen") else "-")
        print(f"Dock   : {'shown' if dock['shown'] else 'hidden'} · held up by: {why}")


def cmd_mode(args):
    config = load_config()
    if not args.name:
        current = json.loads(call("GetState")[0])["mode"]
        for key, mode in config.modes():
            mark = "●" if key == current else " "
            print(f"{mark} {key:<12} {mode['label']}")
        return
    if not call("SetMode", "(s)", (args.name,))[0]:
        sys.exit(f"No mode `{args.name}`. See: focusdock mode")


def cmd_toast(args):
    call("Toast", "(ssss)", (args.text, args.kind, "", ""))


def cmd_stats(args):
    day = args.date or time.strftime("%Y-%m-%d")
    path = brain.STATE_DIR / "stats" / f"{day}.json"
    if not path.exists():
        sys.exit(f"No data for {day} yet.")
    data = json.loads(path.read_text())
    config = load_config()
    print(f"FocusDock · {day}\n")
    for key, bucket in data["modes"].items():
        mode = config.mode(key) or {"label": key, "focus": False}
        total = sum(bucket.values())
        if total < 60:
            continue
        line = f"{mode['label']:<10} {minutes(total):>5}"
        if mode.get("focus"):
            judged = bucket.get("on_task", 0) + bucket.get("off_task", 0)
            score = bucket.get("on_task", 0) / judged * 100 if judged else 100
            line += (f"   focus {score:3.0f}%   on {minutes(bucket.get('on_task', 0))}"
                     f" · off {minutes(bucket.get('off_task', 0))}"
                     f" · neutral {minutes(bucket.get('neutral', 0))}")
        if bucket.get("idle"):
            line += f" · idle {minutes(bucket['idle'])}"
        print(line)
    top = list(data.get("distractions", {}).items())[:5]
    if top:
        print("\nBiggest distractions:")
        for label, seconds in top:
            print(f"  {minutes(seconds):>5}  {label}")


def installed_apps():
    lines = []
    for app in Gio.AppInfo.get_all():
        if app.should_show() and app.get_id():
            lines.append(f"{app.get_id()} — {app.get_name()}")
    return "\n".join(sorted(lines))


def cmd_agent(args):
    config = brain.Config()
    config.refresh()
    claude = brain.Claude(config.llm)
    if not claude.available:
        sys.exit("Nggak ada backend Claude. Set ANTHROPIC_API_KEY atau install Claude Code (`claude`).")
    old_text = brain.MODES_FILE.read_text()
    prompt = (f"Current modes.json:\n```json\n{old_text}\n```\n\n"
              f"Installed apps (desktop id — name):\n{installed_apps()}\n\n"
              f"Request: {args.instruction}")
    print(f"Asking Claude ({claude.backend}, {claude.model})…", file=sys.stderr)
    try:
        result = claude.ask(brain.EDIT_SYSTEM, prompt, brain.EDIT_SCHEMA,
                            effort="medium", max_tokens=16000, timeout=300)
        new_config = json.loads(result["config_json"])
    except (brain.LLMError, ValueError, KeyError) as e:
        sys.exit(f"Failed: {e}")
    errors = brain.validate_config(new_config)
    if errors:
        sys.exit("Claude's result isn't valid, so it wasn't saved:\n  " + "\n  ".join(errors))

    new_text = json.dumps(new_config, indent=2, ensure_ascii=False) + "\n"
    if json.loads(old_text) == new_config:
        print(f"{result['summary']}\n(Nothing changed.)")
        return
    print(result["summary"], "\n")
    sys.stdout.writelines(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        "modes.json", "modes.json (baru)"))
    print()
    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("Not saved. Run again with --yes to apply.")
        if input("Apply? [y/N] ").strip().lower() not in ("y", "ya", "yes"):
            sys.exit("Cancelled.")
    shutil.copy2(brain.MODES_FILE, brain.MODES_FILE.with_suffix(".json.bak"))
    brain.write_json_atomic(brain.MODES_FILE, new_config)
    print(f"Saved (backup: {brain.MODES_FILE.with_suffix('.json.bak')}). The dock reloads by itself.")


def cmd_classify(args):
    config = load_config()
    key = args.mode or json.loads(call("GetState")[0])["mode"]
    mode = config.mode(key)
    if not mode:
        sys.exit(f"No mode `{key}`.")
    title = brain.normalize_title(args.title)
    ruled = brain.rule_verdict(mode, args.app_id, args.app, title, config.language)
    if ruled:
        print(json.dumps(ruled, ensure_ascii=False))
        return
    claude = brain.Claude(config.llm)
    if not claude.available:
        print(json.dumps(brain.verdict("neutral", source="default")))
        return
    prompt = brain.activity_prompt(key, mode, config.modes(), args.app_id, args.app, title)
    result = claude.ask(brain.activity_system(config.language), prompt, brain.ACTIVITY_SCHEMA)
    print(json.dumps({**result, "source": f"llm/{claude.backend}"}, ensure_ascii=False))


def send_inbox(event_json):
    bus().call_sync(BUS_NAME, OBJECT_PATH, BUS_NAME, "Inbox", GLib.Variant("(s)", (event_json,)), None,
                    Gio.DBusCallFlags.NO_AUTO_START, 1500, None)


def cmd_hook(args):
    # Codex passes its payload as an argument; Claude Code and Gemini CLI on stdin.
    payload = args.payload if args.payload is not None else sys.stdin.read()
    hooks.run(args.agent, payload, send_inbox)


def cmd_hooks(args):
    names = args.only or list(hooks.AGENTS)
    for name in names:
        label, present, installed, install, remove, where = hooks.AGENTS[name]
        if args.action == "status":
            state = "installed" if installed() else ("not installed" if present() else "not on this computer")
            print(f"{label:<12} {state}  ({where})")
            continue
        if args.action == "install" and not present():
            if args.only:
                print(f"{label:<12} skipped: not on this computer")
            continue
        try:
            changed = install() if args.action == "install" else remove()
        except (OSError, ValueError) as e:
            print(f"{label:<12} failed: {e}")
            continue
        verb = "installed" if args.action == "install" else "removed"
        print(f"{label:<12} {verb if changed else 'already ' + verb}  ({where})")
    if args.action == "install":
        print("\nAgent sessions already running pick the hooks up when reopened. "
              f"Backups of the original files: *{hooks.BACKUP_SUFFIX}")


def print_plan(plan):
    learned = plan.get("learned_from")
    print(f"Workday routine · {plan.get('project', '-')}" + (f" (from {learned})" if learned else ""))
    days = "Mon Tue Wed Thu Fri Sat Sun".split()
    print(f"Offered: {' '.join(days[d - 1] for d in plan['days'])} {plan['from']}–{plan['until']}"
          f" · {'on' if plan.get('enabled') else 'off'}"
          + (" · pinned (not relearned)" if plan.get("pinned") else ""))
    for step in plan.get("steps", []):
        kind = step["type"]
        detail = {"mode": lambda: f"mode {step['mode']}",
                  "service": lambda: f"start {step['unit']}",
                  "server": lambda: f"{step['command']}   (in {step['cwd']})",
                  "url": lambda: f"open {step['url']}",
                  "claude": lambda: f"terminal: {' '.join(step['command'])}   (in {step['cwd']})"}[kind]()
        print(f"  - {routine.label(step):<24} {detail}")
    print(f"\nEdit it yourself: {routine.ROUTINE_FILE}  (set \"pinned\": true to keep your changes)")


def cmd_routine(args):
    action = args.action
    if action in ("on", "off", "pin", "unpin"):
        plan = routine.load()
        plan["enabled" if action in ("on", "off") else "pinned"] = action in ("on", "pin")
        routine.save(plan)
        print({"on": "Routine on.", "off": "Routine off.",
               "pin": "Routine pinned.", "unpin": "Routine is relearned every morning."}[action])
        return
    if action == "allow-db":
        units = sorted({s["unit"] for s in routine.load().get("steps", []) if s["type"] == "service"})
        if not units:
            sys.exit("The routine doesn't start any system service.")
        path = brain.CACHE_DIR / "50-focusdock-services.rules"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(routine.polkit_rule(os.environ.get("USER") or os.getlogin(), units))
        print(f"Polkit rule for {', '.join(units)} (start/stop/restart, only your user):\n")
        print(path.read_text())
        print(f"Install it (once, needs your password):\n  sudo install -m 644 {path} /etc/polkit-1/rules.d/50-focusdock-services.rules")
        return
    if action == "log":
        for path in sorted(routine.LOG_DIR.glob("*.log")):
            print(f"==> {path} (last lines)")
            print("".join(path.read_text(errors="replace").splitlines(keepends=True)[-15:]))
        os.execvp("journalctl", ["journalctl", "--user", "-u", "focusdock-*", "-n", "60", "--no-pager"])
    plan = routine.learn() if action == "learn" else routine.plan_for_today()
    if action == "learn" and plan:
        routine.save(plan)
    if not plan:
        sys.exit("No work activity to learn from yet (Claude Code sessions in a project, on a workday).")
    print_plan(plan)
    if action != "run":
        return

    print()
    marks = {"running": "…", "done": "✓", "skipped": "✓ (already running)", "failed": "✗"}

    def progress(step, state):
        if state != "running":
            print(f"  {marks[state]} {routine.label(step)}", flush=True)

    def set_mode(mode):
        try:
            call("SetMode", "(s)", (mode,))
        except SystemExit:
            pass  # the dock isn't running; the rest still works

    routine.run(plan, progress, set_mode)


def cmd_session(args):
    if args.action == "now":
        try:
            windows = json.loads(call("GetWindows")[0])
        except SystemExit:
            windows = None
        snap = session.snapshot(bus(), windows, json.loads(call("GetState")[0])["mode"])
        session.record(snap)
    else:
        snap = session.to_restore(session.current_session())
        if not snap:
            data = session.load()
            if not data.get("snapshots"):
                sys.exit("No snapshot yet. The daemon takes one every 30 seconds while you work.")
            snap = max(data["snapshots"], key=lambda s: s["time"])
            if args.action == "show":
                print("(No previous session waiting to be restored; this is the latest snapshot.)")
    print(f"Session {snap['session']} · {time.strftime('%a %H.%M', time.localtime(snap['time']))}"
          f" · mode {snap.get('mode') or '-'}")
    windows = snap.get("windows", [])

    def where(index):
        if index is None or index >= len(windows):
            return ""
        w = windows[index]
        if w.get("fullscreen"):
            size = "fullscreen"
        elif w.get("max_h", w.get("maximized")) and w.get("max_v", w.get("maximized")):
            size = "maximized"
        elif w.get("rect"):
            size = "{2}×{3} at {0},{1}".format(*w["rect"])
        else:
            size = "default size"
        return f"  [workspace {w.get('workspace', 0) + 1} · {size}{' · minimized' if w.get('minimized') else ''}]"

    for n, entry in enumerate(session.terminal_windows(snap), 1):
        print(f"  terminal  window {n}{where(entry.get('window'))}")
        for i, tab in enumerate(entry["tabs"]):
            what = f"claude --resume {tab['claude']} {' '.join(tab['flags'])}" if tab.get("claude") else "shell"
            mark = "*" if i == entry.get("active", 0) and len(entry["tabs"]) > 1 else " "
            print(f"           {mark} tab {i + 1}  {tab['cwd']}  ·  {what}")
    for d in snap["databases"]:
        print(f"  database  {d['unit']}")
    for s in snap["servers"]:
        print(f"  server    {s['command']}  ({s['cwd']})")
    if snap["chrome"]:
        print("  chrome    reopened with its last tabs")
    for uri in snap["files"]:
        print(f"  files     {uri}")
    for app in snap["apps"]:
        print(f"  app       {app}")
    others = [i for i, w in enumerate(windows) if w["app"] != session.PTYXIS]
    if others:
        print("  windows:")
        for i in sorted(others, key=lambda i: -windows[i].get("stack", 0)):
            focus = "  ← focused" if windows[i].get("focused") else ""
            print(f"    {windows[i]['app'].removesuffix('.desktop'):<28} {windows[i]['title'][:40]!r}{where(i)}{focus}")
    if args.action == "restore":
        print()
        session.restore(snap, lambda label, state: state != "running" and print(f"  {state:<8} {label}"),
                        lambda mode: call("SetMode", "(s)", (mode,)))


def cmd_doctor(_args):
    config = brain.Config()
    config.refresh()
    checks = []
    enabled = UUID in (Gio.Settings.new("org.gnome.shell").get_strv("enabled-extensions"))
    checks.append(("extension in enabled-extensions", enabled,
                   f"gnome-extensions enable {UUID}  (on Wayland: log out and back in first)"))
    checks.append(("dock on D-Bus", name_has_owner(BUS_NAME),
                   "log out and back in, then check: journalctl --user -b | grep -i focusdock"))
    checks.append(("brain (focusd) running", name_has_owner(BRAIN_NAME),
                   "systemctl --user restart focusd; journalctl --user -u focusd -f"))
    checks.append((f"modes.json valid ({brain.MODES_FILE})", bool(config.data["modes"]),
                   "see the errors in: journalctl --user -u focusd" if brain.MODES_FILE.exists()
                   else "not there yet; the extension creates it when it first runs"))
    backend = brain.Claude(config.llm).backend
    if config.llm["backend"] == "none":
        checks.append(("AI off (your choice): your allow and block lists decide", True, ""))
    else:
        checks.append((f"Claude backend: {backend}", backend != "none",
                       "set ANTHROPIC_API_KEY in ~/.config/focusdock/env or install Claude Code"))
    for label, ok, fix in checks:
        print(f"{'✓' if ok else '✗'} {label}")
        if not ok:
            print(f"    → {fix}")
    for name, (label, present, installed, *_rest) in hooks.AGENTS.items():
        if present():
            print(f"✓ {label} tells the dock when it's done" if installed()
                  else f"- {label} doesn't report to the dock (optional: focusdock hooks install --only {name})")


def main():
    parser = argparse.ArgumentParser(prog="focusdock", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="mode, active window, held notifications").set_defaults(func=cmd_status)

    p = sub.add_parser("mode", help="show or change the mode")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("toast", help="show a message above the dock")
    p.add_argument("text")
    p.add_argument("--kind", default="info", choices=["info", "nudge", "summary", "suggest"])
    p.set_defaults(func=cmd_toast)

    p = sub.add_parser("stats", help="today's focus recap")
    p.add_argument("date", nargs="?", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("agent", help='change modes in plain language, e.g. "add a Design mode"')
    p.add_argument("instruction")
    p.add_argument("-y", "--yes", action="store_true", help="apply without asking")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("classify", help="test classifying one window")
    p.add_argument("app")
    p.add_argument("title")
    p.add_argument("--app-id", default="")
    p.add_argument("--mode")
    p.set_defaults(func=cmd_classify)

    sub.add_parser("doctor", help="check every component").set_defaults(func=cmd_doctor)

    p = sub.add_parser("routine", help="workday routine: set up the project, servers, database and Claude like last workday")
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "learn", "run", "on", "off", "pin", "unpin", "log", "allow-db"])
    p.set_defaults(func=cmd_routine)

    p = sub.add_parser("session", help="the desk snapshot that comes back after a logout")
    p.add_argument("action", nargs="?", default="show", choices=["show", "now", "restore"])
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("hook", help="called by Claude Code / Codex / Gemini CLI hooks (not for typing)")
    p.add_argument("agent", choices=sorted(hooks.PARSERS))
    p.add_argument("payload", nargs="?", help="JSON from Codex; the others use stdin")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("hooks", help="install or remove the hooks that let AI agents tell the dock they're done")
    p.add_argument("action", choices=["install", "remove", "status"])
    p.add_argument("--only", nargs="+", choices=sorted(hooks.AGENTS))
    p.set_defaults(func=cmd_hooks)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
