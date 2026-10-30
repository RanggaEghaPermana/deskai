#!/usr/bin/env bash
# Developer install: runs DeskAI right from this clone. Everyone else: ./setup.sh
set -euo pipefail
exec python3 "$(dirname "$(readlink -f "$0")")/setup/installer.py" --dev "$@"
