#!/usr/bin/env bash
# Everything a change to DeskAI has to pass, in one command. It runs the project's tests, so read
# someone else's code before you run this on it. Exits 1 if anything failed.
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

log="$(mktemp)"
script="$(mktemp --suffix=.mjs)"
message="$(mktemp)"
trap 'rm -f "$log" "$script" "$message"' EXIT
failed=0

passed() { echo "ok       $1"; }
broke() { echo "FAILED   $1"; failed=$((failed + 1)); }
# A missing tool is said out loud, so nothing looks more checked than it was.
skipped() { echo "skipped  $1"; }

if python3 -m unittest discover -s tests >"$log" 2>&1; then
  passed "tests"
else
  broke "tests"
  tail -n 40 "$log"
fi

# 105 of the 109 are right today. Raise this as more are, and never lower it to get a change through.
if python3 evals/run.py --no-model --min-right 105 >"$log" 2>&1; then
  passed "the lists still understand what they should"
else
  broke "the lists no longer understand what they should"
  grep -E '✗|^total|Error' "$log" | cut -c1-160
fi

if command -v node >/dev/null; then
  bad=""
  for file in extension/*/*.js; do
    cp "$file" "$script"
    node --check "$script" >/dev/null 2>&1 || bad="$bad $file"
  done
  if [ -z "$bad" ]; then passed "the JavaScript parses"; else broke "the JavaScript doesn't parse:$bad"; fi
else
  skipped "the JavaScript: node isn't installed"
fi

bad=""
for file in dev/*.sh dev/hooks/* install.sh setup.sh uninstall.sh packaging/*.sh; do
  bash -n "$file" 2>/dev/null || bad="$bad $file"
done
if [ -z "$bad" ]; then passed "the shell scripts parse"; else broke "these shell scripts don't parse:$bad"; fi

if command -v appstreamcli >/dev/null; then
  if appstreamcli validate --no-net packaging/*.metainfo.xml >"$log" 2>&1; then
    passed "the app metadata is valid"
  else
    broke "the app metadata isn't valid"
    tail -n 20 "$log"
  fi
else
  skipped "the app metadata: appstreamcli isn't installed"
fi

if git rev-parse --verify -q origin/main >/dev/null 2>&1; then
  bad=""
  for commit in $(git rev-list origin/main..HEAD); do
    git log -1 --format=%B "$commit" >"$message"
    dev/hooks/commit-msg "$message" 2>/dev/null || bad="$bad $(git log -1 --format=%h "$commit")"
  done
  if [ -z "$bad" ]; then passed "no commit credits an AI"; else broke "these commits credit an AI:$bad"; fi
else
  skipped "the commit messages: there's no origin/main to compare with"
fi

# The tests above don't run the dock, and this takes a while, so it's for when the dock changed.
if git rev-parse --verify -q origin/main >/dev/null 2>&1; then
  changed="$(git diff --name-only origin/main -- extension dev/smoke.sh dev/nested.sh; git status --porcelain -- extension dev/smoke.sh dev/nested.sh)"
else
  changed="can't tell"
fi
if ! command -v gnome-shell >/dev/null; then
  skipped "the dock in a real shell: GNOME Shell isn't installed here"
elif [ -z "$changed" ]; then
  skipped "the dock in a real shell: it hasn't changed. To run it anyway: dev/smoke.sh"
elif dev/smoke.sh >"$log" 2>&1; then
  passed "the dock works in a real shell"
else
  broke "the dock in a real shell"
  grep -v '^ok ' "$log" | cut -c1-200
fi

if [ -n "${CI:-}" ] || ! git rev-parse --git-dir >/dev/null 2>&1; then
  skipped "private data: it's about what's on your own computer"
elif dev/private-scan.sh >"$log" 2>&1; then
  passed "no private data"
  grep '^note:' "$log"
else
  broke "private data"
  cat "$log"
fi

if [ "$failed" -gt 0 ]; then
  echo "$failed check(s) failed"
  exit 1
fi
echo "All checks passed"
