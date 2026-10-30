#!/usr/bin/env bash
# Removes FocusDock for this user. Keeps your modes and stats; --purge removes those too.
set -euo pipefail
exec python3 "$(dirname "$(readlink -f "$0")")/setup/installer.py" --uninstall "$@"
