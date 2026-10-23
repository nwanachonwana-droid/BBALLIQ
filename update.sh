#!/usr/bin/env bash
# UPDATE -- one command for the daily cycle: settle yesterday, archive
# it, regenerate every data file, push.
#
# WHY THIS EXISTS SEPARATELY FROM publish.sh
#   publish.sh regenerates and pushes. It does NOT grade, so running it
#   alone republishes yesterday's unsettled picks as though the night
#   had not happened. The cron does grade first (9:10) and publish
#   after (9:20); this is the same sequence in one place for when you
#   run it by hand.
#
#   Grading is separated from publishing on purpose. A failed grade
#   must not stop the publish -- tonight's card still needs to go out
#   -- and a failed publish must not look like a failed grade. Each
#   step reports for itself and the exit code is the publish's.
#
# usage
#   ./update.sh            # settle yesterday, publish, show the record
#   ./update.sh 20261022   # settle a specific day instead
set -uo pipefail
cd "$(dirname "$0")"

SLATE="$HOME/Downloads/slate.py"
DAY="${1:-}"

if [ ! -f "$SLATE" ]; then
  echo "! no slate.py at $SLATE -- run install.sh first"
  exit 1
fi

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# --- 1. settle ---------------------------------------------------------
# No --day settles everything still ungraded, which is what you want
# after a missed morning: two nights get caught up in one run.
step "grading"
if [ -n "$DAY" ]; then
  python3 "$SLATE" grade --day "$DAY" || echo "  ! grade failed - continuing"
else
  python3 "$SLATE" grade || echo "  ! grade failed - continuing"
fi

step "grading the Covers list"
python3 "$SLATE" covers || echo "  ! covers grade failed - continuing"

# --- 2. archive --------------------------------------------------------
# The CSVs are linked from the site, so they are deliverables and get
# rewritten before the publish copies them across.
step "archiving"
python3 "$SLATE" csv || echo "  ! csv failed - continuing"

# --- 3. publish --------------------------------------------------------
# publish.sh regenerates status/tonight/record/covers and pushes. Its
# exit code is this script's: it is the step that decides whether the
# site is actually current.
step "publishing"
./publish.sh
RC=$?

# --- 4. what the night did --------------------------------------------
# Printed after the push so a failed publish is the last thing on
# screen rather than being scrolled away by a results table.
if [ "$RC" -eq 0 ]; then
  step "the record"
  python3 "$SLATE" report || true
  step "tail shape"
  python3 "$SLATE" tails || true
  step "closing line value"
  python3 "$SLATE" clv || true
fi

exit "$RC"
