#!/usr/bin/env bash
# Installs FocusDock for the current user: links the extension, sets up the
# brain's venv, the `focusdock` command and the focusd user service.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="focusdock@ranggaeghapermana.github.io"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
SHARE="$DATA/focusdock"
EXT_DIR="$DATA/gnome-shell/extensions"
BIN="$HOME/.local/bin"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/focusdock"

mkdir -p "$SHARE" "$EXT_DIR" "$BIN" "$UNIT_DIR" "$CONFIG"

echo "→ extension"
ln -sfn "$ROOT/extension/$UUID" "$EXT_DIR/$UUID"

echo "→ python venv + anthropic SDK"
python3 -m venv --system-site-packages "$SHARE/venv"
"$SHARE/venv/bin/pip" install --quiet --upgrade pip anthropic

echo "→ focusdock command"
cat > "$BIN/focusdock" <<EOF
#!/bin/sh
exec "$SHARE/venv/bin/python" "$ROOT/daemon/focusdock.py" "\$@"
EOF
chmod +x "$BIN/focusdock"

echo "→ focusd user service"
[ -f "$CONFIG/env" ] || cat > "$CONFIG/env" <<'EOF'
# Optional. With an API key the brain calls the Anthropic API directly;
# without one it uses the Claude Code CLI and your Claude login.
# ANTHROPIC_API_KEY=sk-ant-...
EOF
chmod 600 "$CONFIG/env"
# systemd user services don't get your shell PATH, and focusd needs `claude`.
cat > "$UNIT_DIR/focusd.service" <<EOF
[Unit]
Description=FocusDock brain (focusd)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$SHARE/venv/bin/python $ROOT/daemon/focusd.py
Environment=PATH=$PATH
EnvironmentFile=-$CONFIG/env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
EOF
systemctl --user daemon-reload
systemctl --user enable --now focusd.service

echo
echo "Done. On Wayland GNOME only picks up a new extension after you log out and back in, then:"
echo "  gnome-extensions enable $UUID"
echo "Check everything with: focusdock doctor"
echo "Let Claude Code, Codex and Gemini CLI tell the dock when they're done: focusdock hooks install"
