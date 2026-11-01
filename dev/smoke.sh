#!/usr/bin/env bash
# What only a real shell can show. Starts DeskAI's extensions in a throwaway GNOME Shell (dev/nested.sh), and
# tries cards, the busy card, the command bar, the mode dialog, Alt+Tab and maximizing, with animations on and
# off, looking for anything that doesn't work or logs an error. Your own session isn't touched.
# Exits 1 if anything failed.
set -uo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$ROOT"

UUID="deskai@ranggaeghapermana.github.io"
LOGIN="deskai-login@ranggaeghapermana.github.io"
RUN="$ROOT/dev/run"
ERRORS='JS ERROR|already disposed|Gjs-CRITICAL|GNOME Shell-CRITICAL|Extension .* had error'  # the last but one is logError()
ALT=65513
TAB=65289
LOG="$(mktemp)"
failed=0

passed() { echo "ok       $1"; }
broke() { echo "FAILED   $1"; failed=$((failed + 1)); }
skipped() { echo "skipped  $1"; }
expect() { if [ "$2" = "$3" ]; then passed "$1"; else broke "$1: wanted $3, got $2"; fi; }

shell_pids() {
  for pid in $(pgrep -x gnome-shell); do
    if tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | grep -qx "XDG_RUNTIME_DIR=$RUN/runtime"; then
      echo "$pid"
    fi
  done
}

stop_shell() {
  local pid i
  for pid in $(shell_pids); do kill "$pid" 2>/dev/null; done
  for i in $(seq 1 40); do [ -z "$(shell_pids)" ] && break; sleep 0.25; done
}

if [ -n "$(shell_pids)" ]; then
  echo "dev/nested.sh is already running from here: stop it first" >&2
  exit 2
fi
trap 'stop_shell; rm -f "$LOG"' EXIT

your_bus="${DBUS_SESSION_BUS_ADDRESS:-}"
rm -f "$RUN/env"
XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP=ubuntu:GNOME EXTRA_EXTENSIONS="$LOGIN" dev/nested.sh >"$LOG" 2>&1 &
nested=$!
for _ in $(seq 1 120); do [ -f "$RUN/env" ] && break; kill -0 "$nested" 2>/dev/null || break; sleep 0.5; done
if [ ! -f "$RUN/env" ]; then
  broke "the test shell starts"
  tail -n 20 "$LOG"
  exit 1
fi
# shellcheck disable=SC1091
source "$RUN/env"
if [ "$DBUS_SESSION_BUS_ADDRESS" = "$your_bus" ]; then
  echo "this would talk to your own session, so it stops here" >&2
  exit 3
fi
export HOME="$RUN/home" GSK_RENDERER=cairo
unset DISPLAY

ev() { gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.gnome.Shell.Eval "$1"; }
val() { ev "$1" | sed -E "s/^\(true, '\"?//; s/\"?'\)$//"; }
state() { val "Main.extensionManager.lookup('$1')?.state ?? 'not there'" 2>/dev/null; }

for _ in $(seq 1 120); do [ "$(state "$UUID")" = "1" ] && break; kill -0 "$nested" 2>/dev/null || break; sleep 0.5; done
if [ "$(state "$UUID")" != "1" ]; then
  broke "the dock loads"
  val "String(Main.extensionManager.lookup('$UUID')?.error ?? '')" 2>/dev/null | cut -c1-300
  grep -E "$ERRORS|libmutter-ERROR" "$LOG" | tail -n 10 | cut -c1-240
  exit 1
fi
passed "the dock loads, on GNOME $(gnome-shell --version | grep -oE '[0-9]+' | head -1)"
expect "the login extension loads" "$(state "$LOGIN")" 1

ev "(() => {
  const Clutter = imports.gi.Clutter, GLib = imports.gi.GLib;
  const seat = (global.stage.get_context ? global.stage.get_context().get_backend() : Clutter.get_default_backend()).get_default_seat();
  const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
  globalThis._T = {
    ext: Main.extensionManager.lookup('$UUID').stateObj,
    key: (k, down) => keyboard.notify_keyval(GLib.get_monotonic_time(), k, down ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED),
    wins: () => global.get_window_actors().map(a => a.meta_window).filter(w => !w.skip_taskbar),
    icon: () => _T.ext._card._body.get_first_child(),
    popup: () => Main.uiGroup.get_children().find(c => c instanceof _T.Switcher) ?? null,
  };
  import('resource:///org/gnome/shell/ui/switcherPopup.js').then(m => { _T.Switcher = m.SwitcherPopup; });
  return 'ok';
})()" >/dev/null
sleep 0.3

windows=0
if command -v gnome-text-editor >/dev/null; then
  for n in 1 2; do
    gnome-text-editor --new-window >/dev/null 2>&1 &
    for _ in $(seq 1 40); do [ "$(val "_T.wins().length")" = "$n" ] && break; sleep 0.5; done
  done
  sleep 1
  windows="$(val "_T.wins().length")"
fi
[ "$windows" = "2" ] || skipped "Alt+Tab and maximizing: no two windows to try them on (is gnome-text-editor installed?)"

try_everything() {
  local how="$1" animated="$2" first second

  ev "_T.ext._showCard({id: 'smoke', kind: 'command', title: 'Hello', timeout: 30})" >/dev/null
  sleep 0.5
  expect "$how: a card opens" "$(val "_T.ext._card.shown")" true
  ev "_T.ext._closeCard()" >/dev/null
  sleep 0.5
  expect "$how: a card closes" "$(val "_T.ext._card.shown")" false

  ev "_T.ext._showCard({id: 'command', kind: 'command', title: 'On it', busy: true, timeout: 30})" >/dev/null
  sleep 0.5
  if [ "$animated" = true ]; then
    first="$(val "Math.round(_T.icon().rotation_angle_z)")"
    sleep 0.4
    second="$(val "Math.round(_T.icon().rotation_angle_z)")"
    if [ "$first" != "$second" ]; then passed "$how: the busy card's icon turns"; else broke "$how: the busy card's icon stays at $first"; fi
  else
    expect "$how: the busy card's icon stays still" "$(val "!!_T.icon().get_transition('rotation-angle-z')")" false
  fi
  ev "globalThis._I = _T.icon(); _T.ext._closeCard(); 'ok'" >/dev/null
  sleep 0.5
  expect "$how: it stops turning when it closes" "$(val "!!_I.get_transition('rotation-angle-z')")" false

  ev "_T.ext._openCommandBar()" >/dev/null
  sleep 0.5
  ev "_T.ext._commandBar.close()" >/dev/null
  sleep 0.4
  ev "_T.ext._openModeDialog(_T.ext._mode)" >/dev/null
  sleep 0.6
  expect "$how: the mode dialog opens" "$(val "!!_T.ext._dialog")" true
  ev "_T.ext._dialog?.close()" >/dev/null
  sleep 0.6

  [ "$windows" = "2" ] || return 0
  ev "_T.key($ALT, true)" >/dev/null
  sleep 0.1
  ev "_T.key($TAB, true); _T.key($TAB, false)" >/dev/null
  sleep 0.7
  expect "$how: Alt+Tab opens" "$(val "!!_T.popup()")" true
  ev "_T.key($ALT, false)" >/dev/null
  sleep 0.7
  expect "$how: Alt+Tab goes away" "$(val "!!_T.popup()")" false

  ev "_T.ext._desk._apply(_T.wins()[0], {max_h: true, max_v: true})" >/dev/null
  sleep 0.7
  expect "$how: a window maximizes" "$(val "_T.wins()[0].maximized_horizontally")" true
  ev "_T.ext._desk._apply(_T.wins()[0], {max_h: false, max_v: false, rect: [100, 100, 700, 500]})" >/dev/null
  sleep 0.7
  expect "$how: and goes back" "$(val "_T.wins()[0].maximized_horizontally")" false
}

if [ "$(val "imports.gi.St.Settings.get().enable_animations")" = true ]; then
  try_everything "animations on" true
  ev "imports.gi.St.Settings.get().inhibit_animations()" >/dev/null
  sleep 0.3
else
  skipped "with animations on: this shell has them off, as on software rendering"
fi
try_everything "animations off" false

logged="$(grep -E "$ERRORS" "$LOG" | sed -E 's/^.*(JS ERROR|Gjs-CRITICAL|GNOME Shell-CRITICAL)( \*\*: [0-9:.]+:)?/\1/' | sort | uniq -c | cut -c1-240 | head -n 14)"
if [ -z "$logged" ]; then
  passed "nothing logged an error"
else
  broke "errors in the log"
  printf '%s\n' "$logged"
fi

if [ "$failed" -gt 0 ]; then
  echo "$failed check(s) failed"
  exit 1
fi
echo "All checks passed"
