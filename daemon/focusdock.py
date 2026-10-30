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
        sys.exit(f"Dock nggak nyaut ({e.message}).\nExtension-nya udah aktif? Coba: focusdock doctor")
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
        sys.exit(f"{brain.MODES_FILE} belum ada atau rusak. Extension bikin file ini pas pertama aktif.")
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
    print(f"Ditahan: {len(state['held'])} notifikasi")
    for held in state["held"]:
        print(f"         - {held['app']}: {held['title']}")
    print(f"Brain  : {'online' if state['brain'] else 'offline'} · backend {brain.Claude(config.llm).backend}")
    inbox = state.get("inbox", [])
    print(f"Inbox  : {len(inbox)} kabar")
    for item in inbox:
        print(f"         - [{item['kind']}] {item['title']}" + (f" ({item['window']})" if item.get("window") else ""))
    dock = state.get("dock", {})
    if "hover" in dock:
        held_up = [name for name, on in (
            ("pointer on it", dock["hover"] and dock.get("pointerRestMs", 0) < 3000),
            ("edge", dock.get("edgeArmed")), ("card", dock.get("card")), ("menu", dock.get("menu")),
            ("drag", dock.get("drag")), ("peek", dock.get("peekMs", 0) > 0)) if on]
        why = ", ".join(held_up) or ("no app window open" if not dock.get("appWindowOpen") else "-")
        print(f"Dock   : {'tampil' if dock['shown'] else 'ngumpet'} · ditahan oleh: {why}")


def cmd_mode(args):
    config = load_config()
    if not args.name:
        current = json.loads(call("GetState")[0])["mode"]
        for key, mode in config.modes():
            mark = "●" if key == current else " "
            print(f"{mark} {key:<12} {mode['label']}")
        return
    if not call("SetMode", "(s)", (args.name,))[0]:
        sys.exit(f"Mode `{args.name}` nggak ada. Lihat: focusdock mode")


def cmd_toast(args):
    call("Toast", "(ssss)", (args.text, args.kind, "", ""))


def cmd_stats(args):
    day = args.date or time.strftime("%Y-%m-%d")
    path = brain.STATE_DIR / "stats" / f"{day}.json"
    if not path.exists():
        sys.exit(f"Belum ada data buat {day}.")
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
            line += (f"   fokus {score:3.0f}%   on {minutes(bucket.get('on_task', 0))}"
                     f" · off {minutes(bucket.get('off_task', 0))}"
                     f" · netral {minutes(bucket.get('neutral', 0))}")
        if bucket.get("idle"):
            line += f" · idle {minutes(bucket['idle'])}"
        print(line)
    top = list(data.get("distractions", {}).items())[:5]
    if top:
        print("\nDistraksi terbesar:")
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
    print(f"Nanya Claude ({claude.backend}, {claude.model})…", file=sys.stderr)
    try:
        result = claude.ask(brain.EDIT_SYSTEM, prompt, brain.EDIT_SCHEMA,
                            effort="medium", max_tokens=16000, timeout=300)
        new_config = json.loads(result["config_json"])
    except (brain.LLMError, ValueError, KeyError) as e:
        sys.exit(f"Gagal: {e}")
    errors = brain.validate_config(new_config)
    if errors:
        sys.exit("Hasil dari Claude nggak valid, nggak gue simpan:\n  " + "\n  ".join(errors))

    new_text = json.dumps(new_config, indent=2, ensure_ascii=False) + "\n"
    if json.loads(old_text) == new_config:
        print(f"{result['summary']}\n(Nggak ada yang berubah.)")
        return
    print(result["summary"], "\n")
    sys.stdout.writelines(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        "modes.json", "modes.json (baru)"))
    print()
    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("Belum disimpan. Jalanin ulang pakai --yes buat nerapin.")
        if input("Terapin? [y/N] ").strip().lower() not in ("y", "ya", "yes"):
            sys.exit("Batal.")
    shutil.copy2(brain.MODES_FILE, brain.MODES_FILE.with_suffix(".json.bak"))
    brain.write_json_atomic(brain.MODES_FILE, new_config)
    print(f"Tersimpan (backup: {brain.MODES_FILE.with_suffix('.json.bak')}). Dock langsung reload.")


def cmd_classify(args):
    config = load_config()
    key = args.mode or json.loads(call("GetState")[0])["mode"]
    mode = config.mode(key)
    if not mode:
        sys.exit(f"Mode `{key}` nggak ada.")
    title = brain.normalize_title(args.title)
    ruled = brain.rule_verdict(mode, args.app_id, args.app, title)
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
            state = "terpasang" if installed() else ("belum" if present() else "nggak ada di laptop ini")
            print(f"{label:<12} {state}  ({where})")
            continue
        if args.action == "install" and not present():
            if args.only:
                print(f"{label:<12} dilewati: belum ke-install")
            continue
        try:
            changed = install() if args.action == "install" else remove()
        except (OSError, ValueError) as e:
            print(f"{label:<12} gagal: {e}")
            continue
        verb = "dipasang" if args.action == "install" else "dilepas"
        print(f"{label:<12} {verb if changed else 'udah ' + verb}  ({where})")
    if args.action == "install":
        print("\nSesi agent yang lagi jalan baca hook-nya pas dibuka ulang. "
              f"Backup file aslinya: *{hooks.BACKUP_SUFFIX}")


def print_plan(plan):
    learned = plan.get("learned_from")
    print(f"Rutinitas kerja · {plan.get('project', '-')}" + (f" (dari {learned})" if learned else ""))
    days = "Sen Sel Rab Kam Jum Sab Min".split()
    print(f"Ditawarin: {' '.join(days[d - 1] for d in plan['days'])} {plan['from']}–{plan['until']}"
          f" · {'nyala' if plan.get('enabled') else 'mati'}"
          + (" · dikunci (nggak dipelajarin ulang)" if plan.get("pinned") else ""))
    for step in plan.get("steps", []):
        kind = step["type"]
        detail = {"mode": lambda: f"mode {step['mode']}",
                  "service": lambda: f"nyalain {step['unit']}",
                  "server": lambda: f"{step['command']}   (di {step['cwd']})",
                  "url": lambda: f"buka {step['url']}",
                  "claude": lambda: f"terminal: {' '.join(step['command'])}   (di {step['cwd']})"}[kind]()
        print(f"  - {routine.label(step):<24} {detail}")
    print(f"\nAtur sendiri: {routine.ROUTINE_FILE}  (set \"pinned\": true biar nggak ketimpa)")


def cmd_routine(args):
    action = args.action
    if action in ("on", "off", "pin", "unpin"):
        plan = routine.load()
        plan["enabled" if action in ("on", "off") else "pinned"] = action in ("on", "pin")
        routine.save(plan)
        print({"on": "Rutinitas nyala.", "off": "Rutinitas mati.",
               "pin": "Rutinitas dikunci.", "unpin": "Rutinitas dipelajarin ulang tiap pagi."}[action])
        return
    if action == "allow-db":
        units = sorted({s["unit"] for s in routine.load().get("steps", []) if s["type"] == "service"})
        if not units:
            sys.exit("Rutinitasnya nggak nyalain service sistem apa pun.")
        path = brain.CACHE_DIR / "50-focusdock-services.rules"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(routine.polkit_rule(os.environ.get("USER") or os.getlogin(), units))
        print(f"Aturan polkit buat {', '.join(units)} (start/stop/restart, cuma user lu):\n")
        print(path.read_text())
        print(f"Pasang (sekali, butuh password):\n  sudo install -m 644 {path} /etc/polkit-1/rules.d/50-focusdock-services.rules")
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
        sys.exit("Belum ada aktivitas kerja yang bisa dipelajarin (sesi Claude Code di sebuah project, hari kerja).")
    print_plan(plan)
    if action != "run":
        return

    print()
    marks = {"running": "…", "done": "✓", "skipped": "✓ (udah jalan)", "failed": "✗"}

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
                sys.exit("Belum ada snapshot. Daemon motret tiap 30 detik selama lu kerja.")
            snap = max(data["snapshots"], key=lambda s: s["time"])
            if args.action == "show":
                print("(Belum ada sesi sebelumnya yang nunggu dibalikin; ini snapshot terakhir.)")
    print(f"Sesi {snap['session']} · {time.strftime('%a %H.%M', time.localtime(snap['time']))}"
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
            size = "{2}×{3} di {0},{1}".format(*w["rect"])
        else:
            size = "ukuran bawaan"
        return f"  [workspace {w.get('workspace', 0) + 1} · {size}{' · minimized' if w.get('minimized') else ''}]"

    for n, entry in enumerate(session.terminal_windows(snap), 1):
        print(f"  terminal  jendela {n}{where(entry.get('window'))}")
        for i, tab in enumerate(entry["tabs"]):
            what = f"claude --resume {tab['claude']} {' '.join(tab['flags'])}" if tab.get("claude") else "shell"
            mark = "*" if i == entry.get("active", 0) and len(entry["tabs"]) > 1 else " "
            print(f"           {mark} tab {i + 1}  {tab['cwd']}  ·  {what}")
    for d in snap["databases"]:
        print(f"  database  {d['unit']}")
    for s in snap["servers"]:
        print(f"  server    {s['command']}  ({s['cwd']})")
    if snap["chrome"]:
        print("  chrome    dibuka lagi dengan tab terakhir")
    for uri in snap["files"]:
        print(f"  files     {uri}")
    for app in snap["apps"]:
        print(f"  app       {app}")
    others = [i for i, w in enumerate(windows) if w["app"] != session.PTYXIS]
    if others:
        print("  jendela:")
        for i in sorted(others, key=lambda i: -windows[i].get("stack", 0)):
            focus = "  ← fokus" if windows[i].get("focused") else ""
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
    checks.append(("extension di enabled-extensions", enabled,
                   f"gnome-extensions enable {UUID}  (di Wayland: logout/login dulu sekali)"))
    checks.append(("dock jalan di D-Bus", name_has_owner(BUS_NAME),
                   "logout/login, lalu cek: journalctl --user -b | grep -i focusdock"))
    checks.append(("brain (focusd) jalan", name_has_owner(BRAIN_NAME),
                   "systemctl --user restart focusd; journalctl --user -u focusd -f"))
    checks.append((f"modes.json valid ({brain.MODES_FILE})", bool(config.data["modes"]),
                   "cek pesan error di journalctl --user -u focusd" if brain.MODES_FILE.exists()
                   else "belum ada; extension bikin otomatis pas pertama aktif"))
    backend = brain.Claude(config.llm).backend
    checks.append((f"backend Claude: {backend}", backend != "none",
                   "set ANTHROPIC_API_KEY di ~/.config/focusdock/env atau install Claude Code"))
    for name, (label, present, installed, *_rest) in hooks.AGENTS.items():
        if present():
            checks.append((f"hook {label} ngabarin dock", installed(), f"focusdock hooks install --only {name}"))
    for label, ok, fix in checks:
        print(f"{'✓' if ok else '✗'} {label}")
        if not ok:
            print(f"    → {fix}")


def main():
    parser = argparse.ArgumentParser(prog="focusdock", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="mode, window aktif, notifikasi yang ditahan").set_defaults(func=cmd_status)

    p = sub.add_parser("mode", help="lihat atau ganti mode")
    p.add_argument("name", nargs="?")
    p.set_defaults(func=cmd_mode)

    p = sub.add_parser("toast", help="munculin pesan di atas dock")
    p.add_argument("text")
    p.add_argument("--kind", default="info", choices=["info", "nudge", "summary", "suggest"])
    p.set_defaults(func=cmd_toast)

    p = sub.add_parser("stats", help="rekap fokus hari ini")
    p.add_argument("date", nargs="?", help="YYYY-MM-DD")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("agent", help='ubah mode pakai bahasa biasa, mis. "tambahin mode Design"')
    p.add_argument("instruction")
    p.add_argument("-y", "--yes", action="store_true", help="langsung terapin tanpa nanya")
    p.set_defaults(func=cmd_agent)

    p = sub.add_parser("classify", help="tes klasifikasi satu window")
    p.add_argument("app")
    p.add_argument("title")
    p.add_argument("--app-id", default="")
    p.add_argument("--mode")
    p.set_defaults(func=cmd_classify)

    sub.add_parser("doctor", help="cek semua komponen").set_defaults(func=cmd_doctor)

    p = sub.add_parser("routine", help="rutinitas kerja: siapin project, server, DB, Claude kayak kemarin")
    p.add_argument("action", nargs="?", default="show",
                   choices=["show", "learn", "run", "on", "off", "pin", "unpin", "log", "allow-db"])
    p.set_defaults(func=cmd_routine)

    p = sub.add_parser("session", help="snapshot meja kerja buat dibalikin abis logout")
    p.add_argument("action", nargs="?", default="show", choices=["show", "now", "restore"])
    p.set_defaults(func=cmd_session)

    p = sub.add_parser("hook", help="dipanggil hook Claude Code / Codex / Gemini CLI (bukan buat diketik)")
    p.add_argument("agent", choices=sorted(hooks.PARSERS))
    p.add_argument("payload", nargs="?", help="JSON dari Codex; yang lain lewat stdin")
    p.set_defaults(func=cmd_hook)

    p = sub.add_parser("hooks", help="pasang/lepas hook biar AI agent ngabarin dock pas selesai")
    p.add_argument("action", choices=["install", "remove", "status"])
    p.add_argument("--only", nargs="+", choices=sorted(hooks.AGENTS))
    p.set_defaults(func=cmd_hooks)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
