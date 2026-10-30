#!/usr/bin/env bash
# Removes what install.sh added. Leaves ~/.config/focusdock (your modes) and stats alone.
set -euo pipefail

UUID="focusdock@ranggaeghapermana.github.io"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

gnome-extensions disable "$UUID" 2>/dev/null || true
systemctl --user disable --now focusd.service 2>/dev/null || true
rm -f "$UNIT_DIR/focusd.service" "$HOME/.local/bin/focusdock"
rm -rf "$DATA/focusdock"
[ -L "$DATA/gnome-shell/extensions/$UUID" ] && rm "$DATA/gnome-shell/extensions/$UUID"
systemctl --user daemon-reload
echo "Removed. Your modes are still in ${XDG_CONFIG_HOME:-$HOME/.config}/focusdock."
