#!/usr/bin/env bash
# Screenshot of the nested shell: dev/shot.sh out.png
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/run/env"
out="$(realpath -m "${1:-shot.png}")"
gdbus call --session --dest org.gnome.Shell.Screenshot --object-path /org/gnome/Shell/Screenshot \
  --method org.gnome.Shell.Screenshot.Screenshot false false "$out" >/dev/null
echo "$out"
