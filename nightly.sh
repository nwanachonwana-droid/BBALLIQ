#!/usr/bin/env bash
# BUILD: 2026-11-14a-day
# The whole nightly run, in the one order that works.
#
# WHY THIS EXISTS
#
# Every failure of the last week was a step left out or pointed at the
# wrong place, and each one printed something that looked like success:
#
#   06 Nov  three.py run from ~/sharpz-site, where it does not live.
#           Three commands printed "No such file or directory" and
#           publish.sh cheerfully pushed the PREVIOUS day's data.
#   07 Nov  --blowout --write-blowout never landed, so the measured
#           minutes haircut stayed off while the board kept printing
#           projections as though minutes were known.
#   08 Nov  edges.py --build was in none of the sequences, so
#           season_logs.json went stale, 15 correctly-stamped go-to
#           picks all graded to null, and the performance page read
#           empty for two days.
#
# A five-command sequence retyped nightly will keep producing those.
# This is the sequence, with the dependencies written down, each step
# checked for existence before it runs, and a summary at the end that
# states what actually happened rather than what was attempted.
#
# WHAT FAILS SOFT AND WHAT STOPS THE RUN
#   box scores   soft. Tonight's picks do not need them; only GRADING
#                does. A failure here leaves the performance page
#                stale and says so.
#   lines        soft. The slip needs them, the go-to five never has.
#   board        HARD. This is the thing being published. If it fails
#                there is nothing to push and pushing anyway is how
#                06 Nov put stale data on a live site.
#   push         loud. The only message that must never be wrong is
#                the one saying the site is current.
#
# Same philosophy as publish.sh: independent halves, fail soft where
# it is safe, and print the real error instead of swallowing it.
#
# THE DAY ARGUMENT
#
#   nightly.sh              build for today (the normal run)
#   nightly.sh 20261115     build for that day instead
#
# Added 14 Nov, 22:58, asked for tomorrow's board after the night's
# games had finished. Three of the four steps already took a day --
# props_lines.py and college.py as --day, three.py as a POSITIONAL --
# and this script passed none of them, so the only way to run ahead was
# four hand-typed commands. That is the exact shape of the 06-08 Nov
# failures, so it is a flag here instead.
#
# WHAT A FUTURE DAY CANNOT DO. edges.py --build walks to YESTERDAY and
# stops, because a day still being played has no final box scores. So
# running tomorrow's board tonight cannot grade tonight -- tonight's
# results do not exist to fetch yet. The override moves which slate
# gets PICKED; it cannot move which results are AVAILABLE. The run says
# so rather than printing a clean summary over a day that did not
# grade. Re-running the same day later is free: log_boards upserts on
# (day, market, player, team), so the second pass replaces rather than
# duplicates.

set -uo pipefail

DL="${SHARPZ_SCRIPTS:-$HOME/Downloads}"
SITE="${SHARPZ_SITE:-$HOME/sharpz-site}"

# --- the day, if one was asked for ---------------------------------
#
# Deliberately NOT bash arrays. macOS ships bash 3.2, where
# "${arr[@]}" on an EMPTY array is an unbound-variable error under
# set -u -- the script would die on the normal no-argument run, which
# is the run that matters most. A bare YYYYMMDD has no whitespace, so
# an unquoted scalar splits into exactly the right number of words:
# two when set, zero when empty.
DAY=""
if [ "$#" -gt 0 ]; then
  case "$1" in
    [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]) DAY="$1" ;;
    -h|--help)
      echo "usage: nightly.sh [YYYYMMDD]"; exit 0 ;;
    *)
      echo "nightly.sh: bad day '$1' - want 8 digits, e.g. 20261115" >&2
      exit 2 ;;
  esac
fi

DAYFLAG=""      # for props_lines.py and college.py
DAYPOS=""       # for three.py, whose day is positional
TODAY="$(date +%Y%m%d)"
if [ -n "$DAY" ]; then
  DAYFLAG="--day $DAY"
  DAYPOS="$DAY"
  printf '\n%s\n' "===================================================="
  echo "DAY OVERRIDE: building for $DAY (today is $TODAY)"
  if [ "$DAY" \> "$TODAY" ]; then
    echo
    echo "  $DAY is in the future. edges.py --build stops at yesterday,"
    echo "  so $TODAY's games cannot grade in this run -- their box"
    echo "  scores do not exist yet. Run again on $DAY to grade them."
  fi
  printf '%s\n' "===================================================="
fi

[ -f "$HOME/.sharpz_env" ] && . "$HOME/.sharpz_env"

BOX="skipped"; LINES="skipped"; BOARD="skipped"; PUSH="skipped"
COLLEGE="skipped"
FAILED=0

say() { printf '\n=== %s\n' "$1"; }

# Run one python step. Returns nonzero on failure but never exits, so
# the caller decides whether the step is fatal.
step() {
  local label="$1" script="$2"; shift 2
  if [ ! -f "$script" ]; then
    echo "  ! $label: no such file - $script"
    echo "    the scripts live in $DL, not in the site repo."
    return 1
  fi
  python3 "$script" "$@"
}

# --- 1. box scores. Grading needs these and nothing else does. ------
say "box scores  (edges.py --build)"
if step "box scores" "$DL/edges.py" --build; then
  BOX="ok"
else
  BOX="FAILED - yesterday's picks will not grade"
  echo "  ! season_logs.json is now behind. The performance page will"
  echo "  ! show blank rows until this succeeds."
fi

# --- 2. lines. The slip needs them; the go-to five does not. --------
#
# THE FOUR BOARD MARKETS, NAMED EXPLICITLY.
#
# props_lines.py defaults to --markets pra, and every sequence anyone
# typed this week called it bare. So it bought PRA lines nightly while
# the board asks posted_line() for 'Points Scored', '3-Pointers Made',
# 'Total Rebounds' and 'Total Assists' -- none of which were ever
# fetched. The run printed "1 market(s)", "+ MIL@NY pra: 10 players",
# and "appended 65 rows", all of which look like success, and then the
# board said NO LINES and 7 days went by with board rows and no
# gradeable bet.
#
# 4 markets x games, at one credit each. Six games is 24 credits
# against a balance near 19,800, so the budget is not the constraint
# the --markets default was protecting.
say "lines  (props_lines.py $DAYFLAG --markets pts,threes,reb,ast)"
if step "lines" "$DL/props_lines.py" $DAYFLAG \
        --markets pts,threes,reb,ast; then
  LINES="ok"
else
  LINES="FAILED - no slip tonight, picks unaffected"
fi

# --- 2b. college. Independent of everything above. ------------------
#
# Soft, and genuinely independent: college.py has its own data (ESPN
# D1 results), its own ratings and its own gate, and shares nothing
# with the NBA board but the output directory. A bad night here must
# not cost the NBA picks, and a bad night there must not cost these.
#
# --publish does the lot: fetch results, rate, grade yesterday, pick
# tonight. The 60% gate is applied inside and an empty list is a
# RESULT, not a failure -- below that threshold the paper has the
# system at 56.8%, which is a coin, not a pick.
say "college  (college.py $DAYFLAG --publish)"
if step "college" "$DL/college.py" $DAYFLAG --publish "$SITE"; then
  COLLEGE="ok"
else
  COLLEGE="FAILED - college section will be stale"
fi

# --- 3. the board. Hard dependency: this is the deliverable. --------
say "board  (three.py $DAYPOS --publish)"
if step "board" "$DL/three.py" $DAYPOS --publish "$SITE"; then
  BOARD="ok"
else
  BOARD="FAILED"
  FAILED=1
  echo "  ! Nothing was written for the site, so NOT pushing."
  echo "  ! Pushing now would put yesterday's data on a live page"
  echo "  ! under today's date, which is what happened on 06 Nov."
fi

# --- 4. push -------------------------------------------------------
if [ "$BOARD" = "ok" ]; then
  say "publish  (publish.sh)"
  if [ -x "$SITE/publish.sh" ] || [ -f "$SITE/publish.sh" ]; then
    if ( cd "$SITE" && bash ./publish.sh ); then
      PUSH="ok"
    else
      PUSH="FAILED - the site is NOT updated"
      FAILED=1
    fi
  else
    PUSH="FAILED - no $SITE/publish.sh"
    FAILED=1
  fi
fi

# --- summary -------------------------------------------------------
printf '\n%s\n' "===================================================="
echo "RUN SUMMARY"
printf '  %-12s %s\n' "built for" "${DAY:-$TODAY}${DAY:+  (override)}"
printf '  %-12s %s\n' "box scores" "$BOX"
printf '  %-12s %s\n' "lines"      "$LINES"
printf '  %-12s %s\n' "college"    "$COLLEGE"
printf '  %-12s %s\n' "board"      "$BOARD"
printf '  %-12s %s\n' "publish"    "$PUSH"
if [ "$FAILED" = "0" ] && [ "$BOX" = "ok" ] && [ "$LINES" = "ok" ] \
   && [ "$COLLEGE" = "ok" ] && [ -n "$DAY" ] && [ "$DAY" \> "$TODAY" ]; then
  # EVERY STEP RAN, AND THE PERFORMANCE PAGE IS STILL A DAY BEHIND.
  # Both halves are true and the old message only said the first, so
  # a clean run would have claimed "yesterday graded" on a night when
  # yesterday provably could not have.
  echo "
  All five steps ran. $DAY's NBA and college picks are live.
  $TODAY is NOT graded -- its box scores do not exist yet. Run
  nightly.sh again on $DAY to grade it."
elif [ "$FAILED" = "0" ] && [ "$BOX" = "ok" ] && [ "$LINES" = "ok" ] \
   && [ "$COLLEGE" = "ok" ]; then
  echo "
  All five steps ran. NBA and college picks are live, yesterday graded."
elif [ "$FAILED" = "0" ]; then
  echo "
  Published, but read the FAILED line(s) above -- something the page
  shows is stale even though the push succeeded."
else
  echo "
  THE SITE WAS NOT UPDATED. Fix the FAILED step and run this again."
fi
exit "$FAILED"
