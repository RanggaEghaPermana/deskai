#!/usr/bin/env bash
# Builds dist/focusdock_<version>_all.deb. It holds the app and the setup window; each user then
# opens FocusDock Setup, which does the per-user part (no root needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
APP_ID="io.github.RanggaEghaPermana.FocusDock"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/focusdock"
APP="$PKG/usr/share/focusdock"

mkdir -p "$APP" "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/usr/share/metainfo" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/doc/focusdock"
cp -r "$ROOT/daemon" "$ROOT/extension" "$ROOT/setup" "$ROOT/VERSION" "$APP/"
find "$APP" -type d -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/setup/$APP_ID.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/packaging/$APP_ID.Setup.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/$APP_ID.Setup.metainfo.xml" "$PKG/usr/share/metainfo/"
cat > "$PKG/usr/bin/focusdock-setup" <<'SCRIPT'
#!/bin/sh
exec /usr/bin/python3 /usr/share/focusdock/setup/focusdock_setup.py "$@"
SCRIPT
cat > "$PKG/usr/share/doc/focusdock/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: FocusDock
Source: https://github.com/RanggaEghaPermana/focusdock

Files: *
Copyright: 2026 Rangga Egha Permana
License: GPL-3+
 On Debian and Ubuntu, the full text is in /usr/share/common-licenses/GPL-3.
COPYRIGHT
chmod 755 "$PKG/usr/bin/focusdock-setup"
find "$PKG" -type d -exec chmod 755 {} +
find "$APP" -type f -exec chmod 644 {} +

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: focusdock
Version: $VERSION
Section: gnome
Priority: optional
Architecture: all
Maintainer: Rangga Egha Permana <RanggaEghaPermana@users.noreply.github.com>
Homepage: https://github.com/RanggaEghaPermana/focusdock
Depends: gnome-shell (>= 50~), gnome-shell (<< 51~), python3, python3-gi, python3-venv, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-atspi-2.0, systemd
Recommends: ptyxis
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Description: Dock for GNOME with focus modes, an inbox and desk restore
 FocusDock is a dock for GNOME 50 with focus modes, each with its own apps,
 allowed sites and notification rules, and an inbox for WhatsApp messages and
 AI agents. After a logout, your windows, terminals and servers come back.
 .
 After installing, open FocusDock Setup from the app grid and follow the steps.
CONTROL

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/focusdock_${VERSION}_all.deb"
fakeroot dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
echo "$OUT"
