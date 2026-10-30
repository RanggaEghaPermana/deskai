#!/usr/bin/env bash
# Opens the setup window from a download of FocusDock.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if ! python3 -c 'import gi; gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")' 2>/dev/null; then
  echo "The setup window needs GTK 4 and libadwaita for Python:"
  echo "  Ubuntu, Debian: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
  echo "  Fedora:         sudo dnf install python3-gobject gtk4 libadwaita"
  echo "  Arch:           sudo pacman -S python-gobject gtk4 libadwaita"
  exit 1
fi
exec python3 setup/focusdock_setup.py "$@"
