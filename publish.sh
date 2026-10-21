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

# Generators live in ~/Downloads and keep colliding with their own
# earlier versions, so they carry explicit suffixes. Change them here
# and nowhere else -- an edit made in a shell gets overwritten the next
# time a bundle is extracted, which is exactly how this broke before.
STATUS_GEN="$HOME/Downloads/site_data2.py"
READS_GEN="$HOME/Downloads/sharpz_screen2.py"
SLATE_GEN="$HOME/Downloads/slate.py"
SLATE_CSV="$HOME/kalshi_data/slate_archive.csv"
COVERS_CSV="$HOME/kalshi_data/covers_archive.csv"

# Run one generator into one data file. On failure the published file
# is left alone and the REAL error is printed: the first version sent
# stderr to /dev/null, so a missing file and a crashed parser produced
# the same useless "failed" line and cost an hour of guessing.
generate() {
  local name="$1" script="$2" dest="$3"
  if [ ! -f "$script" ]; then
    WARN="$WARN
  ! $name: no such file - $script"
    return 1
  fi
  local out err
  out=$(mktemp); err=$(mktemp)
  if python3 "$script" --json > "$out" 2> "$err" \
     && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$out" 2>/dev/null; then
    mv "$out" "$dest"
    rm -f "$err"
    GEN="$GEN $name"
    return 0
  fi
  WARN="$WARN
  ! $name failed - keeping the published $dest
    $(tail -3 "$err" | sed 's/^/    /')"
  rm -f "$out" "$err"
  return 1
}

generate status  "$STATUS_GEN" data/status.json
generate tonight "$READS_GEN"  data/tonight.json

# The record. `report --json` takes a subcommand, so it does not fit
# the plain `--json` shape the other two share.
if [ -f "$SLATE_GEN" ]; then
  RT=$(mktemp); RE=$(mktemp)
  if python3 "$SLATE_GEN" report --json > "$RT" 2> "$RE" \
     && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$RT" 2>/dev/null; then
    mv "$RT" data/record.json
    GEN="$GEN record"
  else
    WARN="$WARN
  ! record failed - keeping the published record.json
    $(tail -3 "$RE" | sed 's/^/    /')"
    rm -f "$RT"
  fi
  rm -f "$RE"
  # the Covers study, reported from its archive rather than re-graded
  CT=$(mktemp); CE=$(mktemp)
  if python3 "$SLATE_GEN" covers-report --json > "$CT" 2> "$CE" \
     && python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$CT" 2>/dev/null; then
    mv "$CT" data/covers.json
    GEN="$GEN covers"
  else
    WARN="$WARN
  ! covers-report failed - keeping the published covers.json
    $(tail -3 "$CE" | sed 's/^/    /')"
    rm -f "$CT"
  fi
  rm -f "$CE"

  # the archives are deliverables, not by-products: they are linked
  # from the page, so they ship with every publish
  [ -f "$SLATE_CSV" ] && cp "$SLATE_CSV" data/slate_archive.csv
  [ -f "$COVERS_CSV" ] && cp "$COVERS_CSV" data/covers_archive.csv
else
  WARN="$WARN
  ! record: no such file - $SLATE_GEN"
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
