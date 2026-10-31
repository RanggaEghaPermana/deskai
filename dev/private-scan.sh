#!/usr/bin/env bash
# Looks for what mustn't reach this public repository: your home path, and the words in
# .git/info/private-words (one per line; it lives in .git, so it's never committed).
#   dev/private-scan.sh              the tracked files
#   dev/private-scan.sh FILE...      these files; a .deb is unpacked first
# Prints where, not what, so its output is safe to paste. Exits 1 if it found anything.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

words="$(git rev-parse --git-path info/private-words)"
patterns="$(mktemp)"
unpacked="$(mktemp -d)"
trap 'rm -rf "$patterns" "$unpacked"' EXIT
printf '%s\n' "$HOME" > "$patterns"
if [ -s "$words" ]; then
  grep -v '^[[:space:]]*$' "$words" >> "$patterns"
else
  echo "note: $words is empty or missing, so only your home path is checked" >&2
fi

found=0
if [ $# -eq 0 ]; then
  git grep -q -I -i -F -f "$patterns" && { git grep -l -I -i -F -f "$patterns"; found=1; }
else
  for file in "$@"; do
    case "$file" in
      *.deb)
        dpkg-deb -R "$file" "$unpacked/deb"
        grep -r -l -i -F -f "$patterns" "$unpacked/deb" | sed "s#^$unpacked/deb/#$file: #" && found=1
        rm -rf "$unpacked/deb"
        ;;
      *)
        grep -l -i -F -f "$patterns" "$file" && found=1
        ;;
    esac
  done
fi

if [ "$found" = 1 ]; then
  echo "private data found in the files above" >&2
  exit 1
fi
echo "clean"
