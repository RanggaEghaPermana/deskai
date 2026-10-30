#!/usr/bin/env bash
# Evaluates JS inside the nested shell: dev/eval.sh 'global.display.focus_window?.title'
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/run/env"
gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
  --method org.gnome.Shell.Eval "$1"
