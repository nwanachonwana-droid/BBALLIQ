#!/bin/bash
# morning_routine.sh
# Run each morning: logs yesterday's results + generates today's picks + pushes both to GitHub
#
# Usage:
#   ./morning_routine.sh                          # yesterday's results + today's picks
#   ./morning_routine.sh --today 02/25            # specify today's date
#   ./morning_routine.sh --yesterday 02/24        # specify yesterday's date manually

set -e

TODAY=""
YESTERDAY=""

# Parse args
while [[ "$#" -gt 0 ]]; do
    case $1 in
        --today) TODAY="$2"; shift ;;
        --yesterday) YESTERDAY="$2"; shift ;;
    esac
    shift
done

# Default: infer dates
if [ -z "$TODAY" ]; then
    TODAY=$(date +%m/%d)
fi
if [ -z "$YESTERDAY" ]; then
    YESTERDAY=$(date -v-1d +%m/%d 2>/dev/null || date -d "yesterday" +%m/%d)
fi

WEBSITE_DIR="$HOME/Desktop/BBALLIQ"

echo "🏀 Basketball IQ — Morning Routine"
echo "===================================="
echo "  Yesterday: $YESTERDAY"
echo "  Today:     $TODAY"
echo ""

# ── Step 1: Log yesterday's results ───────────────────────────────────────────
echo "① Logging yesterday's results ($YESTERDAY)..."
python3 track_outcomes.py --date "$YESTERDAY"

# ── Step 1b: Settle NBA props ─────────────────────────────────────────────────
echo ""
echo "① Settling NBA props ($YESTERDAY)..."
python3 track_props_outcomes.py --date "$YESTERDAY"

# ── Step 1c: Settle AI Bettor bets ────────────────────────────────────────────
echo ""
echo "① Settling AI Bettor bets ($YESTERDAY)..."
python3 ai_bettor.py --settle --date "$YESTERDAY"

# ── Step 2: Generate today's picks ────────────────────────────────────────────
echo ""
echo "② Generating today's picks ($TODAY)..."
python3 run_daily.py --date "$TODAY"

# ── Step 2b: Generate NBA props ───────────────────────────────────────────────
echo ""
echo "② Generating NBA props ($TODAY)..."
python3 fetch_props_nba.py
python3 nba_props_model.py
# Save dated copy so we never lose props history
cp output/nba_props_today.json "output/nba_props_$(date +%Y-%m-%d).json"

# ── Step 2c: Generate NBA game picks ──────────────────────────────────────────
echo ""
echo "② Generating NBA game picks ($TODAY)..."
python3 fetch_vegas_nba.py
python3 nba_baseline.py
python3 nba_step2.py
python3 nba_step3.py

# ── Step 3: Generate today's parlays ──────────────────────────────────────────
echo ""
echo "③ Generating today's parlays..."
python3 parlay_builder.py --date "$TODAY"

# ── Step 3b: Place AI Bettor bets ─────────────────────────────────────────────
echo ""
echo "③ Placing AI Bettor bets ($TODAY)..."
python3 ai_bettor.py --bet --date "$TODAY"

# ── Step 4: Push everything to GitHub ─────────────────────────────────────────
echo ""
echo "④ Pushing to GitHub..."

if [ ! -d "$WEBSITE_DIR" ]; then
    echo "  ⚠️  Website repo not found at $WEBSITE_DIR"
    exit 1
fi

cp output/latest_picks.json "$WEBSITE_DIR/latest_picks.json"
cp output/latest_picks.json "$WEBSITE_DIR/output/latest_picks.json"

# Copy NBA props
if [ -f "output/nba_props_today.json" ]; then
    cp output/nba_props_today.json "$WEBSITE_DIR/nba_props_today.json"
    cp output/nba_props_today.json "$WEBSITE_DIR/output/nba_props_today.json"
fi

# Copy NBA picks
if [ -f "output/nba_picks_today.json" ]; then
    cp output/nba_picks_today.json "$WEBSITE_DIR/nba_picks_today.json"
    cp output/nba_picks_today.json "$WEBSITE_DIR/output/nba_picks_today.json"
fi

# Copy props performance log
if [ -f "props_performance_log.json" ]; then
    cp props_performance_log.json "$WEBSITE_DIR/props_performance_log.json"
fi

# Copy NBA games performance log
if [ -f "nba_games_performance_log.json" ]; then
    cp nba_games_performance_log.json "$WEBSITE_DIR/nba_games_performance_log.json"
fi

# Copy parlays to both root and output
if [ -f "output/latest_parlays.json" ]; then
    cp output/latest_parlays.json "$WEBSITE_DIR/output/latest_parlays.json"
fi

# Copy performance log if it exists
if [ -f "performance_log.json" ]; then
    cp performance_log.json "$WEBSITE_DIR/performance_log.json"
fi

# Copy parlays if they exist
if [ -f "output/latest_parlays.json" ]; then
    cp output/latest_parlays.json "$WEBSITE_DIR/latest_parlays.json"
fi

# Copy AI bankroll if it exists
if [ -f "output/ai_bankroll.json" ]; then
    cp output/ai_bankroll.json "$WEBSITE_DIR/ai_bankroll.json"
fi

cd "$WEBSITE_DIR"
git add latest_picks.json performance_log.json latest_parlays.json ai_bankroll.json 2>/dev/null || git add latest_picks.json
git commit -m "$(date '+%Y-%m-%d'): picks + results + parlays"
git push

echo ""
echo "✅ Done! Live at https://nwanachonwana-droid.github.io/BBALLIQ/"
