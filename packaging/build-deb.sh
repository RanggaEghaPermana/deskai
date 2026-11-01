#!/usr/bin/env bash
# Builds dist/deskai_<version>_all.deb. It holds the app and the setup window, which opens by itself
# after installing and does the per-user part (no root needed).
set -euo pipefail

ROOT="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
VERSION="$(cat "$ROOT/VERSION")"
APP_ID="io.github.RanggaEghaPermana.DeskAI"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
PKG="$STAGE/deskai"
APP="$PKG/usr/share/deskai"

# What's committed, so what's in a package can be looked up. Changes that aren't would be left out without a word.
if [ -n "$(git -C "$ROOT" status --porcelain -- daemon extension setup packaging VERSION)" ]; then
  echo "These aren't committed, so they wouldn't be in the package. Commit them first:" >&2
  git -C "$ROOT" status --short -- daemon extension setup packaging VERSION >&2
  exit 1
fi

mkdir -p "$APP" "$PKG/DEBIAN" "$PKG/usr/bin" "$PKG/usr/share/applications" "$PKG/usr/share/metainfo" \
         "$PKG/usr/share/icons/hicolor/scalable/apps" "$PKG/usr/share/doc/deskai"
git -C "$ROOT" archive HEAD daemon extension setup VERSION | tar -x -C "$APP"
mkdir -p "$APP/packaging"
cp "$ROOT/packaging/deskai-sessions.sh" "$APP/packaging/"
cp "$ROOT/packaging/postinst" "$ROOT/packaging/prerm" "$PKG/DEBIAN/"
cp "$ROOT/setup/$APP_ID.svg" "$PKG/usr/share/icons/hicolor/scalable/apps/"
cp "$ROOT/packaging/$APP_ID.Setup.desktop" "$ROOT/packaging/$APP_ID.Presets.desktop" "$PKG/usr/share/applications/"
cp "$ROOT/packaging/$APP_ID.Setup.metainfo.xml" "$PKG/usr/share/metainfo/"
cat > "$PKG/usr/bin/deskai-setup" <<'SCRIPT'
#!/bin/sh
exec /usr/bin/python3 /usr/share/deskai/setup/deskai_setup.py "$@"
SCRIPT
cat > "$PKG/usr/bin/deskai-presets" <<'SCRIPT'
#!/bin/sh
exec /usr/bin/python3 /usr/share/deskai/daemon/presets_window.py "$@"
SCRIPT
cat > "$PKG/usr/share/doc/deskai/copyright" <<COPYRIGHT
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: DeskAI
Source: https://github.com/RanggaEghaPermana/deskai

Files: *
Copyright: 2026 Rangga Egha Permana
License: GPL-3+
 On Debian and Ubuntu, the full text is in /usr/share/common-licenses/GPL-3.
COPYRIGHT
chmod 755 "$PKG/usr/bin/deskai-setup" "$PKG/usr/bin/deskai-presets"
find "$PKG" -type d -exec chmod 755 {} +
find "$APP" -type f -exec chmod 644 {} +
chmod 755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

cat > "$PKG/DEBIAN/control" <<CONTROL
Package: deskai
Version: $VERSION
Section: gnome
Priority: optional
Architecture: all
Maintainer: Rangga Egha Permana <RanggaEghaPermana@users.noreply.github.com>
Homepage: https://github.com/RanggaEghaPermana/deskai
Depends: gnome-shell (>= 46~), gnome-shell (<< 52~), gnome-shell (<< 47~) | gnome-shell (>= 50~), python3, python3-gi, python3-venv, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-atspi-2.0, systemd, zstd
Recommends: ptyxis
Installed-Size: $(du -sk "$PKG/usr" | cut -f1)
Description: Dock for GNOME with focus modes, an inbox and desk restore
 DeskAI is a dock for GNOME 46, 50 and 51 with focus modes, each with its own apps,
 allowed sites and notification rules, and an inbox for WhatsApp messages and
 AI agents. After a logout, your windows, terminals and servers come back.
 .
 DeskAI Setup opens by itself after installing, and takes you through the rest.
CONTROL

mkdir -p "$ROOT/dist"
OUT="$ROOT/dist/deskai_${VERSION}_all.deb"
fakeroot dpkg-deb --build --root-owner-group "$PKG" "$OUT" >/dev/null
# The same file under a name that stays the same, for a link that always gets the latest release.
cp "$OUT" "$ROOT/dist/deskai_all.deb"
(cd "$ROOT/dist" && sha256sum "deskai_${VERSION}_all.deb" deskai_all.deb > SHA256SUMS)
echo "$OUT"
