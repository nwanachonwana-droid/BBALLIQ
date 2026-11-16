#!/usr/bin/env bash
# INSTALL -- put the newest download of each file where it belongs.
#
# THE PROBLEM
#   Safari never overwrites. A second download of sharpz_screen2.py
#   lands as "sharpz_screen2 (1).py", so the canonical name keeps
#   pointing at an older copy. Nothing errors: the scripts run, the
#   tarball extracts, and the page quietly stays one version behind.
#   That cost five rounds of debugging in one morning, every time
#   looking like a different bug.
#
#   So: for each file, take the newest match in ~/Downloads, move it to
#   the canonical name, and verify a string that only the current
#   version contains. A version check that cannot fail is not a check,
#   which is why each entry carries a marker rather than just a name.
#
#   AND IT HAS TO DO THAT TO ITSELF FIRST
#   It did not, for weeks. install.sh installed every script except
#   install.sh, so a new one landed as "install (2).sh" and the stale
#   copy kept running -- checking four scripts out of eleven and
#   reporting "5 ok" in a cheerful green v. Three files shipped into
#   that gap and were never moved into place; slate.py ran old code and
#   printed an old header, which looked like a bug in slate.py. The
#   blind spot was the exact failure this script exists to prevent,
#   aimed at the only file that could hide it.
#
#   So before anything else: find the newest install*.sh, and if it is
#   not this one, put it in place and hand over to it. The mv replaces
#   the inode rather than writing into it, so the running shell keeps
#   reading the copy it already opened, and SHARPZ_REEXEC stops a loop.
#
# usage
#   bash ~/Downloads/install.sh          # install and verify
#   bash ~/Downloads/install.sh --check  # verify only, change nothing
set -uo pipefail

DL="$HOME/Downloads"
SITE="$HOME/sharpz-site"
# CHANGELOG. Kept as COMMENTS, not inside the VERSION string.
#
#   VERSION used to carry this whole log, and on 02 Nov it grew a
#   pair of backticks and six double quotes: `not act` became
#   command substitution, the first "-" closed the assignment, and
#   the installer died on line 39 with "not: command not found"
#   then "VERSION: unbound variable". It had already re-exec'd
#   itself, so it LOOKED like it ran -- and three.py was never
#   moved into place. The next board printed build 02c and the old
#   four-leg slip, which read as the patch not working.
#
#   bash -n passes this: command substitution in a string is
#   valid syntax. Only running it, or scanning the line, finds it.
#   So VERSION is now SINGLE-quoted and one line long, and
#   selftest.py checks it for the characters that can execute.
#
#   2026-11-02d ONE LEG PER FINDING -- the per-game cap reshaped the
#   concentration instead of removing it: tonight was Durant+Jabari both
#   on BOS cannot guard the three and Vassell+Castle both on UTA cannot
#   guard the three, two findings sold as four legs. The slip now keys
#   on (opponent, zone), keeps the best expression of each and prints
#   what it held back; points_threes only ever caught this WITHIN a
#   player. SETTLE TABLE HID ITS ZEROS -- not act is true at act==0, so
#   a 0-rebound game printed as - and was dropped from the night's
#   average (1.175 became 1.256); level now reports
#   sum(actual)/sum(proj) per market beside the mean of ratios, PUBLISH
#   UNBROKEN: log_picks wrote no settler shell so slate.py's summarise()
#   raised KeyError 'result' and record.json stopped rebuilding for the
#   WHOLE file -- shell written now, plus --repair-log to backfill the
#   rows already on disk; PER_GAME now caps the SLIP as well as the
#   boards (last night was 3 of 4 picks in SA/UTA, which is two reads
#   logged as four), --settle DAY and --log-picks (the slip never
#   reached slate.jsonl, so two nights produced no W/L), market bracket
#   printed, site v26: computed picks lead the page, covers list removed
#   entirely, INJURY FILTER NEVER WORKED (dict compared to a string),
#   market term now fails loudly, MARKET-IMPLIED ENVIRONMENT,
#   frequency-only zones, stat-specific decay, FIT normalisation fixed,
#   fixed-set audit, header reports the APPLIED shrinkage, reliability
#   reports its own error bar and the shrinkage is floored, three.py
#   --log + --calibrate, double-count flag reads data not prose, ZONE
#   PROFILE BLENDS BOTH SEASONS, drift reported too, three.py --publish
#   + site v25 matchup boards (top 5 each), POINTS+THREES pairing with
#   the double-count flagged, 3P% was blended-makes over raw-attempts
#   (Draymond at 91%), now attempts x shrunk pct, StopIteration fix,
#   THREES board (last-season sample like points, modelled on attempts),
#   defense.py emits zone_z, --env projects reliability forward, shrinks
#   team environments by MEASURED reliability, build id on stdout, --env
#   league table + --min-leg, share sentence was inverted, rebound share
#   is a RATE not a count, output grouped by game, per-game cap,
#   volume+quality floors owned locally (betting filters off, volume
#   floors ON), minutes from the logs not the board (the intersection
#   was really defense.py filter) + minute-total audit,
#   slate-standardised, --audit-teams, separate matchup boards for
#   pts/reb/ast + PRA overlap, team assists aggregated, PRA market
#   (derived column, measured variance), build id printed on stdout,
#   conflict = projection on the wrong side of the line (NOT a 50%
#   floor), why_thin checks the schedule + nomination rate, per-market
#   footing, GRADES OFF (computed+logged, not published), card
#   --bias-pop, v24 bundle, py3.9-safe, fit-shrink, slate grades, board
#   day fix, card --log, game.py, selftest, build-id markers, hashed,
#   bash 3.2 safe

VERSION='2026-11-13b TWO COLLEGE RECORDS. WON is the moneyline, did the team we picked win. MADE THE MARGIN is whether they reached or beat the number we published, rounded half-up like the NBA rule, so 8.1 needs 8 and 27.5 needs 28. Both are shown per pick and as headline totals, because a team can win the game and miss the number - Tulsa on 11 Nov won by 12 against a called 17. An AWAY pick stores a negative margin, so called and actual are flipped together or every road pick would grade backwards, and there is a check pinning that. Note this is OUR published number, not a book spread: no college lines are fetched, props_lines is NBA only'
CHECK=0
[ "${1:-}" = "--check" ] && CHECK=1

if [ -z "${SHARPZ_REEXEC:-}" ]; then
  mine=$(ls -t "$DL"/install*.sh 2>/dev/null | head -1)
  if [ -n "$mine" ] && [ "$mine" != "$DL/install.sh" ]; then
    echo "installer"
    echo "  > install.sh <- $(basename "$mine")  (the old one was running)"
    cp "$mine" "$DL/.install.new" && mv "$DL/.install.new" "$DL/install.sh" \
      && rm -f "$mine"
    SHARPZ_REEXEC=1 exec bash "$DL/install.sh" "$@"
  fi
fi

echo "installer version: $VERSION  (bash ${BASH_VERSION:-?})"
echo

# script : marker that must appear in the current version
#
# Pick a marker the PREVIOUS version did not have, or the check is
# worthless. 'AVG_SEC_PER_TOUCH' passed a stale tracking.py because
# both copies contained it; 'Accept-Encoding' only exists in the fixed
# one. A marker that cannot fail is not a check.
#
# AND THAT KEEPS HAPPENING, SO MARKERS ARE NO LONGER TRUSTED ALONE.
#   'minutes.py:defence_improving' passed a minutes.py from before two
#   estimator fixes, because every version has that function.
#   'slate.py:deaccent' waved through four separate revisions in one
#   day for the same reason, so `tails` twice printed an old header
#   that looked like a bug in tails. Both times the screen said a
#   cheerful green v and both times the file was stale.
#
#   The flaw is structural: the marker is chosen by whoever edited the
#   file, from memory, and a marker that fails is indistinguishable
#   from a marker that passes. The scripts that carry a BUILD stamp now
#   use THAT as their marker -- a string that changes every release by
#   definition, so it cannot be chosen badly. Bump BUILD in the script
#   and here together, and the check is exact. So every line now also carries the
#   file's first 6 hex of SHA-256 and whether it CHANGED since the
#   last run, recorded in .install_state. A hash cannot be chosen
#   badly. If a file says "unchanged" when you just downloaded it, the
#   download did not land -- whatever the marker says.
SCRIPTS=(
  "props_lines.py:for_picks"
  "bref.py:norm_name"
  "edges.py:THE OPPONENT, RECORDED"
  "defense.py:via_w"
  "minutes.py:2026-10-30a"
  "three.py:2026-11-16e"
  "boards.py:2026-10-28b"
  "card.py:2026-10-30g"
  "game.py:2026-10-28e"
  "kalshi_find.py:yes_bid_dollars"
  "slate.py:2026-11-05a"
  "tracking.py:subprocess.run"
  "sharpz_screen2.py:pgame"
  "site_data2.py:--json"
  "espn_probe.py:looks_like_injuries"
  "selftest.py:a three-zone points edge is flagged as one claim"
  "why_thin.py:nomination rate"
  "check_grades.py:does not follow from"
  "college.py:2026-11-13b"
  "nightly.sh:2026-11-16a-settle"
)

ok=0; bad=0
STATE="$DL/.install_state"
NOW=""

# NO ASSOCIATIVE ARRAYS.
#
# The previous version opened with `declare -A WAS`, which is bash 4.
# macOS still ships bash 3.2 as /bin/bash, where that is an invalid
# option -- and worse, the follow-up `WAS["slate.py"]` then treats WAS
# as an INDEXED array and evaluates the subscript as arithmetic, so
# "slate.py" becomes a syntax error in an expression. The run limped
# to "1 ok" and looked like twelve failed downloads when nothing was
# wrong with any of them. It passed every test here because this
# machine runs 5.2.
#
# So the previous hashes are looked up out of the state file with awk,
# which behaves the same everywhere. The version is printed below for
# the same reason: a shell-compatibility failure should announce
# itself rather than be inferred from a wrong total.
prev_hash() {
  [ -f "$STATE" ] || return 0
  awk -v n="$1" '$1 == n { print $2; exit }' "$STATE" 2>/dev/null
}

shorthash() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" 2>/dev/null | cut -c1-6
  else
    cksum "$1" 2>/dev/null | cut -d' ' -f1
  fi
}

newest() {  # newest file matching a glob, or empty
  local pat="$1" f
  f=$(ls -t $pat 2>/dev/null | head -1)
  [ -n "$f" ] && [ -f "$f" ] && printf '%s' "$f"
}

echo "scripts"
for entry in "${SCRIPTS[@]}"; do
  name="${entry%%:*}"; marker="${entry#*:}"
  # EXTENSION-AWARE. This was hardcoded to .py, so nightly.sh could
  # not be installed by the same loop that installs everything else
  # -- and a runner that has to be placed by hand is a runner that
  # will be out of date exactly when it matters.
  ext="${name##*.}"
  stem="${name%.*}"
  src=$(newest "$DL/$stem*.$ext")
  if [ -z "$src" ]; then
    echo "  ? $name: nothing in Downloads - skipped"
    continue
  fi
  if [ "$CHECK" = 0 ] && [ "$src" != "$DL/$name" ]; then
    mv "$src" "$DL/$name"
    echo "  > $name <- $(basename "$src")"
  fi
  h=$(shorthash "$DL/$name")
  NOW="$NOW$name $h
"
  was=$(prev_hash "$name")
  if [ -z "$was" ]; then
    chg="new"
  elif [ "$was" = "$h" ]; then
    chg="unchanged"
  else
    chg="UPDATED"
  fi
  # A runner that is not executable is a runner that fails on the one
  # night nobody has the patience to debug it.
  case "$name" in *.sh) chmod +x "$DL/$name" 2>/dev/null ;; esac
  if grep -q -- "$marker" "$DL/$name" 2>/dev/null; then
    echo "  v $name  (has '$marker')  $h $chg"; ok=$((ok+1))
  else
    echo "  X $name  MISSING '$marker' - out of date  $h $chg"
    bad=$((bad+1))
  fi
done
printf '%s' "$NOW" > "$STATE" 2>/dev/null

echo
echo "site bundle"
TAR=$(newest "$DL/sharpz-site-v*.tar.gz")
if [ -z "$TAR" ]; then
  echo "  ? no sharpz-site-v*.tar.gz in Downloads - skipped"
elif [ ! -d "$SITE" ]; then
  echo "  X $SITE does not exist"
  bad=$((bad+1))
else
  if [ "$CHECK" = 0 ]; then
    tar xzf "$TAR" -C "$SITE" && echo "  > extracted $(basename "$TAR")"
  fi
  # the marker lives in the generated page, not the archive name, so a
  # stale tarball with a new-looking filename still fails here.
  #
  # AND IT IS THE VERSION, not a feature string. "CARD_MONO" was in
  # both v20 and v21, so a v20 bundle verified green while v21 was
  # current and the panel it was supposed to remove stayed on the
  # page. Same failure the script markers had: a string two versions
  # share cannot tell them apart. Bump this with the bundle.
  # TWO PAGES NOW. log.html was checked here and is no longer in the
  # bundle, so checking it would fail every install from here on --
  # the site check has to describe the site that exists.
  if grep -q "SITE BUILD v43" "$SITE/index.html" 2>/dev/null \
     && grep -q "SITE BUILD v43" "$SITE/performance.html" 2>/dev/null \
     && grep -q "auditing the grader" "$SITE/update.sh"; then
    chmod +x "$SITE/update.sh" 2>/dev/null
    echo "  v index.html and performance.html are v43 (the five, and the college calls by confidence band, and whether they were right), update.sh audits"
    ok=$((ok+1))
  else
    echo "  X index.html is stale - $(basename "$TAR") was an old build"
    bad=$((bad+1))
  fi
fi

echo
if [ "$bad" -gt 0 ]; then
  echo "$ok ok, $bad STALE. Re-download the ones marked X, then run again."
  exit 1
fi
echo "$ok ok. Now: cd $SITE && ./publish.sh"
