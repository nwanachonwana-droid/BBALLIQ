#!/usr/bin/env bash
# Daily publish. Run after grade.py, from inside the site repo.
#
# TWO INDEPENDENT HALVES, AND WHY THEY MUST STAY INDEPENDENT
#   data/status.json  our own priced run. Some days there isn't one.
#   data/tonight.json the outside reads. There is one every morning.
#
#   The first version of this script exited on a status failure, which
#   meant a broken half the page barely uses took the whole publish
#   down with it -- including tonight's table, which was the thing
#   worth pushing. Each half now fails on its own and the other still
#   goes out. A half that fails leaves yesterday's file in place: a
#   stale number carrying a visible timestamp beats a blank panel.
set -uo pipefail
cd "$(dirname "$0")"

GEN=""
WARN=""

# --- our own run ------------------------------------------------------
T1=$(mktemp)
if python3 ~/Downloads/site_data2.py --json > "$T1" 2>/dev/null \
   && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$T1" 2>/dev/null; then
  mv "$T1" data/status.json
  GEN="$GEN status"
else
  rm -f "$T1"
  WARN="$WARN
  ! site_data2.py failed - keeping the published status line"
fi

# --- tonight's outside reads -----------------------------------------
T2=$(mktemp)
if python3 ~/Downloads/sharpz_screen2.py --json > "$T2" 2>/dev/null \
   && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$T2" 2>/dev/null; then
  mv "$T2" data/tonight.json
  GEN="$GEN tonight"
else
  rm -f "$T2"
  WARN="$WARN
  ! sharpz_screen2.py failed - keeping yesterday's reads"
fi

[ -n "$WARN" ] && echo "$WARN"

if [ -z "$GEN" ]; then
  echo "both generators failed - nothing regenerated, not pushing"
  exit 1
fi
echo "regenerated:$GEN"

# --- push -------------------------------------------------------------
# -A then a staged check, so a brand new file counts as a change too;
# `git diff --quiet` alone only sees modifications to tracked files.
git add -A
if git diff --cached --quiet; then
  echo "nothing changed today"
  exit 0
fi
git commit -q -m "board update $(date +%Y-%m-%d)"
# Without `set -e` a failed push would fall through to the success
# line, which is the one message that must never be printed wrongly:
# it is the only thing telling you the site is current.
if git push -q; then
  echo "published $(date +%Y-%m-%d)"
else
  echo "COMMITTED BUT PUSH FAILED - the site is NOT updated."
  echo "Fix the connection, then: cd ~/sharpz-site && git push"
  exit 1
fi
