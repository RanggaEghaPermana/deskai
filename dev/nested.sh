#!/usr/bin/env bash
# Runs FocusDock inside a throwaway headless GNOME Shell with its own HOME,
# so it can be tested without touching the real session.
#   dev/nested.sh            start (foreground)
#   source dev/run/env       in another shell to talk to it
#   EXTRA_EXTENSIONS="uuid …" also load some of your installed extensions
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UUID="focusdock@ranggaeghapermana.github.io"
RUN="$ROOT/dev/run"
SIZE="${1:-1600x900}"

rm -rf "$RUN"
mkdir -p "$RUN/home" "$RUN/runtime"
chmod 700 "$RUN/runtime"

export REAL_HOME="$HOME"
export HOME="$RUN/home"
export XDG_CONFIG_HOME="$HOME/.config" XDG_DATA_HOME="$HOME/.local/share"
export XDG_CACHE_HOME="$HOME/.cache" XDG_STATE_HOME="$HOME/.local/state"
export XDG_RUNTIME_DIR="$RUN/runtime"
unset WAYLAND_DISPLAY DISPLAY DBUS_SESSION_BUS_ADDRESS

mkdir -p "$XDG_DATA_HOME/gnome-shell/extensions"
# Downloads, Videos and Pictures exist, as on a real desktop.
mkdir -p "$HOME/Downloads" "$HOME/Videos/Screencasts" "$HOME/Pictures/Screenshots" "$HOME/Documents" "$XDG_CONFIG_HOME"
cat > "$XDG_CONFIG_HOME/user-dirs.dirs" <<'DIRS'
XDG_DOWNLOAD_DIR="$HOME/Downloads"
XDG_VIDEOS_DIR="$HOME/Videos"
XDG_PICTURES_DIR="$HOME/Pictures"
XDG_DOCUMENTS_DIR="$HOME/Documents"
DIRS
ln -s "$ROOT/extension/$UUID" "$XDG_DATA_HOME/gnome-shell/extensions/$UUID"
ln -s "$ROOT/dev/testhelper@focusdock.dev" "$XDG_DATA_HOME/gnome-shell/extensions/testhelper@focusdock.dev"
ENABLED="\"$UUID\", \"testhelper@focusdock.dev\""
for extra in ${EXTRA_EXTENSIONS:-}; do
  ln -s "$REAL_HOME/.local/share/gnome-shell/extensions/$extra" "$XDG_DATA_HOME/gnome-shell/extensions/$extra"
  ENABLED="$ENABLED, \"$extra\""
done
export ENABLED

exec dbus-run-session -- bash -c '
  gsettings set org.gnome.shell disable-user-extensions false
  gsettings set org.gnome.shell enabled-extensions "[$ENABLED]"
  cat > "'"$RUN"'/env" <<ENV
export DBUS_SESSION_BUS_ADDRESS="$DBUS_SESSION_BUS_ADDRESS"
export XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" WAYLAND_DISPLAY=wayland-0
export XDG_CONFIG_HOME="$XDG_CONFIG_HOME" XDG_DATA_HOME="$XDG_DATA_HOME"
export XDG_CACHE_HOME="$XDG_CACHE_HOME" XDG_STATE_HOME="$XDG_STATE_HOME"
ENV
  exec gnome-shell --headless --wayland --no-x11 --virtual-monitor '"$SIZE"'
'
