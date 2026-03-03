#!/usr/bin/env python3
"""
track_props_outcomes.py
───────────────────────
Settles yesterday's NBA prop bets by fetching actual player stats
from the Odds API box scores endpoint.

Usage:
    python3 track_props_outcomes.py              # settles yesterday
    python3 track_props_outcomes.py --date 03/01 # settles specific date

What it does:
    1. Reads pending prop bets from output/ai_bankroll.json
    2. Fetches NBA box scores from Odds API
    3. Matches player stats to prop lines (over/under)
    4. Updates ai_bankroll.json with WIN/LOSS and P&L
    5. Logs results to props_performance_log.json
"""

import os
import json
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
BASE_URL     = "https://api.the-odds-api.com/v4"
SPORT        = "basketball_nba"
OUTPUT_DIR   = Path("output")
WEBSITE_DIR  = Path.home() / "Desktop" / "BBALLIQ"
BANKROLL_FILE = OUTPUT_DIR / "ai_bankroll.json"
PROPS_LOG_FILE    = Path("props_performance_log.json")
NBA_GAMES_LOG_FILE = Path("nba_games_performance_log.json")

STAT_MAP = {
    # What we store → what Odds API box score might call it
    "POINTS": ["points", "pts"],
    "REBOUNDS": ["rebounds", "reb", "total_rebounds"],
    "ASSISTS": ["assists", "ast"],
}


# ── ODDS API BOX SCORES ──────────────────────────────────────────

def fetch_nba_box_scores(date_str: str) -> list:
    """
    Fetch NBA player box scores for a given date.
    Uses the Odds API event scores endpoint with player stats.
    """
    if not ODDS_API_KEY:
        print("  ❌ No ODDS_API_KEY in .env")
        return []

    # First get events for that day
    url = f"{BASE_URL}/sports/{SPORT}/scores"
    params = {
        "apiKey":   ODDS_API_KEY,
        "daysFrom": 2,
    }
    resp = requests.get(url, params=params, timeout=15)
    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"  Odds API requests remaining: {remaining}")

    if resp.status_code != 200:
        print(f"  ❌ Scores API error {resp.status_code}")
        return []

    games = resp.json()
    completed = [g for g in games if g.get("completed")]
    print(f"  ✓ Found {len(completed)} completed NBA games")
    return completed


def fetch_event_player_stats(event_id: str) -> dict:
    """
    Fetch player-level stats for a specific event.
    Returns dict: {player_name: {points: X, rebounds: Y, assists: Z}}
    """
    url = f"{BASE_URL}/sports/{SPORT}/events/{event_id}/scores"
    params = {"apiKey": ODDS_API_KEY}
    resp = requests.get(url, params=params, timeout=15)

    if resp.status_code != 200:
        return {}

    data = resp.json()
    player_stats = {}

    # Parse player stats from box score response
    for team_data in data.get("scores", []):
        players = team_data.get("players", [])
        for player in players:
            name = player.get("name", "")
            stats = player.get("statistics", {})
            if name:
                player_stats[name.lower()] = {
                    "points":   stats.get("points", stats.get("pts", 0)),
                    "rebounds": stats.get("rebounds", stats.get("reb",
                                stats.get("total_rebounds", 0))),
                    "assists":  stats.get("assists", stats.get("ast", 0)),
                    "raw":      stats,
                }

    return player_stats


# ── NAME MATCHING ────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """Lowercase, strip punctuation for fuzzy matching."""
    import re
    return re.sub(r"[^a-z ]", "", name.lower().strip())


def find_player_stats(player_name: str, all_stats: dict) -> dict | None:
    """Find player stats using fuzzy name matching."""
    target = normalize_name(player_name)

    # Exact match first
    if target in all_stats:
        return all_stats[target]

    # Last name match
    target_last = target.split()[-1] if target.split() else target
    for key, stats in all_stats.items():
        if target_last in key:
            return stats

    # Partial first+last
    target_parts = target.split()
    for key, stats in all_stats.items():
        key_parts = key.split()
        if len(target_parts) >= 2 and len(key_parts) >= 2:
            if target_parts[-1] == key_parts[-1] and target_parts[0][0] == key_parts[0][0]:
                return stats

    return None


# ── SETTLEMENT ───────────────────────────────────────────────────

def settle_prop_bet(bet: dict, all_player_stats: dict) -> str | None:
    """
    Determine WIN/LOSS for a prop bet given player stats.
    Returns 'WIN', 'LOSS', or None if unresolvable.
    """
    player    = bet.get("player", "")
    stat      = bet.get("stat", "").upper()
    line      = bet.get("line")
    direction = bet.get("direction", "").upper()

    if not player or not stat or line is None:
        # Try to parse from pick label e.g. "Christian Braun OVER 12.5 POINTS"
        pick = bet.get("pick", "")
        parts = pick.split()
        if len(parts) >= 4:
            # Find OVER/UNDER
            for i, p in enumerate(parts):
                if p in ("OVER", "UNDER"):
                    player    = " ".join(parts[:i])
                    direction = p
                    line      = float(parts[i+1])
                    stat      = parts[i+2] if i+2 < len(parts) else "POINTS"
                    break

    if not player:
        return None

    try:
        line = float(line)
    except (TypeError, ValueError):
        return None

    # Find player in box scores
    stats = find_player_stats(player, all_player_stats)
    if stats is None:
        return None

    # Get actual stat value
    stat_key = stat.lower().rstrip("s")  # "POINTS" → "point", "REBOUNDS" → "rebound"
    actual = None
    for candidate in [stat.lower(), stat_key, stat_key + "s"]:
        if candidate in stats:
            actual = stats[candidate]
            break
    if actual is None:
        # Try mapped keys
        for mapped_key in STAT_MAP.get(stat, []):
            if mapped_key in stats:
                actual = stats[mapped_key]
                break

    if actual is None:
        print(f"    ⚠️  Stat '{stat}' not found for {player} (available: {list(stats.keys())})")
        return None

    actual = float(actual)
    print(f"    {player}: {stat} actual={actual} line={line} direction={direction}")

    if direction == "OVER":
        return "WIN" if actual > line else "LOSS"
    elif direction == "UNDER":
        return "WIN" if actual < line else "LOSS"
    else:
        return None



# ── NBA GAME SETTLEMENT ──────────────────────────────────────────

def settle_nba_games(date_str: str, bankroll_data: dict, completed_games: list):
    """
    Settle NBA game picks (moneyline) for a given date.
    Matches picks from ai_bankroll.json against completed game scores.
    """
    pending = bankroll_data.get("pending_bets", [])
    game_bets = [b for b in pending
                 if b.get("type") in ("single", "game")
                 and b.get("sport") == "NBA"
                 and b.get("date") == date_str]

    if not game_bets:
        print(f"  No pending NBA game bets for {date_str}")
        return bankroll_data

    print(f"  Found {len(game_bets)} pending NBA game bets")

    settled = []
    still_pending = []

    for bet in game_bets:
        pick = bet.get("pick", "")
        matched_game = None

        # Find the game containing this team
        for game in completed_games:
            home = game.get("home_team", "")
            away = game.get("away_team", "")
            pick_norm = normalize_name(pick)
            if (pick_norm in normalize_name(home) or normalize_name(home) in pick_norm or
                pick_norm in normalize_name(away) or normalize_name(away) in pick_norm):
                matched_game = game
                break

        if not matched_game:
            print(f"  ⚠️  No game found for: {pick}")
            still_pending.append(bet)
            continue

        # Determine winner
        scores = matched_game.get("scores", [])
        if not scores or len(scores) < 2:
            print(f"  ⚠️  No scores for: {pick}")
            still_pending.append(bet)
            continue

        try:
            score_map = {s["name"]: int(s["score"]) for s in scores}
            actual_winner = max(score_map, key=score_map.get)
        except (KeyError, ValueError):
            still_pending.append(bet)
            continue

        winner_norm = normalize_name(actual_winner)
        pick_norm   = normalize_name(pick)
        result = "WIN" if (pick_norm in winner_norm or winner_norm in pick_norm) else "LOSS"

        score_str = " - ".join([f"{s['name'].split()[-1]} {s['score']}" for s in scores])
        print(f"  {'✅' if result == 'WIN' else '❌'} {pick} → {result} ({score_str})")

        bet["status"] = "settled"
        bet["result"] = result
        if result == "WIN":
            bet["pnl"] = round(bet.get("to_win", 0), 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] + bet["to_win"], 2)
            bankroll_data["wins"] += 1
        else:
            bet["pnl"] = round(-bet.get("stake", 0), 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] - bet["stake"], 2)
            bankroll_data["losses"] += 1

        bankroll_data["total_bets"] += 1
        settled.append(bet)

    # Update bankroll pending/settled
    other_pending = [b for b in pending
                     if not (b.get("type") in ("single","game")
                             and b.get("sport") == "NBA"
                             and b.get("date") == date_str)]
    bankroll_data["pending_bets"]  = other_pending + still_pending
    bankroll_data["settled_bets"].extend(settled)

    if settled:
        pnl = sum(b["pnl"] for b in settled)
        bankroll_data["daily_snapshots"].append({
            "date":    date_str,
            "bankroll": bankroll_data["current_bankroll"],
            "pnl":     round(pnl, 2),
            "bets":    len(settled),
            "wins":    sum(1 for b in settled if b["result"] == "WIN"),
            "losses":  sum(1 for b in settled if b["result"] == "LOSS"),
            "note":    "nba_games"
        })
        print(f"  NBA Game P&L for {date_str}: ${pnl:+.2f}")
        log_nba_games_performance(settled, date_str)

    return bankroll_data


def log_nba_games_performance(settled: list, date_str: str):
    """Append settled NBA game bets to nba_games_performance_log.json."""
    if not settled:
        return

    existing = []
    if NBA_GAMES_LOG_FILE.exists():
        with open(NBA_GAMES_LOG_FILE) as f:
            existing = json.load(f)

    for b in settled:
        existing.append({
            "date":      date_str,
            "pick":      b.get("pick", ""),
            "matchup":   b.get("matchup", ""),
            "model_prob": b.get("model_prob", ""),
            "edge_pp":   b.get("edge_pp", ""),
            "odds":      b.get("ml", ""),
            "stake":     b.get("stake", ""),
            "to_win":    b.get("to_win", ""),
            "result":    b.get("result", ""),
            "pnl":       b.get("pnl", ""),
        })

    with open(NBA_GAMES_LOG_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  ✓ NBA games log → {NBA_GAMES_LOG_FILE} ({len(existing)} total entries)")



def settle_parlays(date_str, bankroll_data, all_player_stats):
    pending = bankroll_data.get("pending_bets", [])
    parlay_bets = [b for b in pending
                   if b.get("type") == "parlay" and b.get("date") == date_str]
    if not parlay_bets:
        print(f"  No pending parlays for {date_str}")
        return bankroll_data
    print(f"  Found {len(parlay_bets)} pending parlays")
    settled = []
    still_pending = []
    for bet in parlay_bets:
        pick_str = bet.get("pick", "")
        legs = [leg.strip() for leg in pick_str.split("+")]
        leg_results = []
        for leg in legs:
            parts = leg.split()
            try:
                dir_idx = next(i for i,p in enumerate(parts) if p.upper() in ("OVER","UNDER"))
                player_name = " ".join(parts[:dir_idx])
                direction = parts[dir_idx].lower()
                line = float(parts[dir_idx+1])
                stat_word = parts[dir_idx+2].upper() if len(parts) > dir_idx+2 else "POINTS"
                stat_map = {"POINTS":"points","REBOUNDS":"rebounds","ASSISTS":"assists"}
                stat = stat_map.get(stat_word, "points")
            except (StopIteration, ValueError, IndexError):
                leg_results.append(None)
                continue
            player_stats = None
            for name, stats in all_player_stats.items():
                if normalize_name(player_name) in normalize_name(name) or normalize_name(name) in normalize_name(player_name):
                    player_stats = stats
                    break
            if player_stats is None:
                leg_results.append(None)
                continue
            actual = player_stats.get(stat)
            if actual is None:
                leg_results.append(None)
                continue
            if direction == "over":
                leg_results.append("WIN" if actual > line else "LOSS")
            else:
                leg_results.append("WIN" if actual < line else "LOSS")
        if None in leg_results:
            print(f"  ⚠️  Parlay unresolved (missing leg): {pick_str[:60]}")
            still_pending.append(bet)
            continue
        result = "WIN" if all(r == "WIN" for r in leg_results) else "LOSS"
        bet["status"] = "settled"
        bet["result"] = result
        if result == "WIN":
            bet["pnl"] = round(bet.get("to_win", 0), 2)
            bankroll_data["current_bankroll"] = round(bankroll_data["current_bankroll"] + bet["to_win"], 2)
            bankroll_data["wins"] += 1
        else:
            bet["pnl"] = round(-bet.get("stake", 0), 2)
            bankroll_data["current_bankroll"] = round(bankroll_data["current_bankroll"] - bet["stake"], 2)
            bankroll_data["losses"] += 1
        bankroll_data["total_bets"] += 1
        icon = "✅" if result == "WIN" else "❌"
        print(f"  {icon} PARLAY {result}: {pick_str[:70]}")
        settled.append(bet)
    other_pending = [b for b in pending
                     if not (b.get("type") == "parlay" and b.get("date") == date_str)]
    bankroll_data["pending_bets"] = other_pending + still_pending
    bankroll_data["settled_bets"].extend(settled)
    if settled:
        pnl = sum(b["pnl"] for b in settled)
        print(f"  Parlay P&L for {date_str}: ${pnl:+.2f}")
    return bankroll_data



def settle_props(date_str: str):
    print(f"\n🎯 Prop Tracker — Settling {date_str}")
    print("=" * 50)

    # Load bankroll
    if not BANKROLL_FILE.exists():
        print("  ❌ No bankroll file found.")
        return

    with open(BANKROLL_FILE) as f:
        bankroll_data = json.load(f)

    # Find pending prop bets for this date
    pending = bankroll_data.get("pending_bets", [])
    prop_bets = [b for b in pending
                 if b.get("type") == "prop" and b.get("date") == date_str]

    if not prop_bets:
        print(f"  No pending prop bets for {date_str}")
        return

    print(f"  Found {len(prop_bets)} pending prop bets")

    # Fetch NBA box scores
    print("\n① Fetching NBA box scores...")
    completed_games = fetch_nba_box_scores(date_str)

    if not completed_games:
        print("  ❌ No completed games — try again after games finish")
        return

    # Settle NBA game picks first (just needs scores, no player stats)
    print("\n② Settling NBA game picks...")
    bankroll_data = settle_nba_games(date_str, bankroll_data, completed_games)

    # Collect all player stats across all games
    print("\n③ Fetching player stats...")
    all_player_stats = {}
    for game in completed_games:
        event_id = game.get("id")
        if not event_id:
            continue
        game_label = f"{game.get('away_team','?')} @ {game.get('home_team','?')}"
        stats = fetch_event_player_stats(event_id)
        if stats:
            print(f"    ✓ {game_label}: {len(stats)} players")
            all_player_stats.update(stats)
        else:
            print(f"    ⚠️  No player stats for {game_label}")

    if not all_player_stats:
        print("\n  ❌ No player stats available from Odds API.")
        print("  ℹ️  The Odds API free tier may not include box scores.")
        print("  ℹ️  Falling back to manual settlement mode.")
        manual_settle(prop_bets, bankroll_data, date_str)
        return

    # Settle each prop bet
    print("\n④ Settling prop bets...")
    settled = []
    still_pending = []

    for bet in prop_bets:
        result = settle_prop_bet(bet, all_player_stats)
        if result is None:
            print(f"  ⚠️  Could not resolve: {bet['pick']}")
            still_pending.append(bet)
            continue

        bet["status"] = "settled"
        bet["result"] = result

        if result == "WIN":
            bet["pnl"] = round(bet["to_win"], 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] + bet["to_win"], 2)
            bankroll_data["wins"] += 1
        else:
            bet["pnl"] = round(-bet["stake"], 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] - bet["stake"], 2)
            bankroll_data["losses"] += 1

        bankroll_data["total_bets"] += 1
        icon = "✅" if result == "WIN" else "❌"
        print(f"  {icon} {bet['pick']} → {result} | P&L: ${bet['pnl']:+.2f}")
        settled.append(bet)

    # Settle parlays
    print("\n⑥ Settling parlays...")
    bankroll_data = settle_parlays(date_str, bankroll_data, all_player_stats)

    # Update pending/settled lists
    other_pending = [b for b in pending
                     if not (b.get("type") in ("prop", "parlay") and b.get("date") == date_str)]
    bankroll_data["pending_bets"]  = other_pending + still_pending
    bankroll_data["settled_bets"].extend(settled)

    if settled:
        pnl = sum(b["pnl"] for b in settled)
        bankroll_data["daily_snapshots"].append({
            "date":    date_str,
            "bankroll": bankroll_data["current_bankroll"],
            "pnl":     round(pnl, 2),
            "bets":    len(settled),
            "wins":    sum(1 for b in settled if b["result"] == "WIN"),
            "losses":  sum(1 for b in settled if b["result"] == "LOSS"),
            "note":    "props"
        })
        print(f"\n  Prop P&L for {date_str}: ${pnl:+.2f}")
        print(f"  Bankroll: ${bankroll_data['current_bankroll']:.2f}")

    bankroll_data["last_updated"] = datetime.now().strftime("%m/%d/%Y")

    with open(BANKROLL_FILE, "w") as f:
        json.dump(bankroll_data, f, indent=2)

    import shutil
    if WEBSITE_DIR.exists():
        shutil.copy(BANKROLL_FILE, WEBSITE_DIR / "ai_bankroll.json")
        print("  ✓ Bankroll updated on website")

    log_props_performance(settled, date_str)


def manual_settle(prop_bets: list, bankroll_data: dict, date_str: str):
    """
    Interactive manual settlement when API box scores unavailable.
    Prints each bet and asks for WIN/LOSS/SKIP input.
    """
    print("\n⌨️  MANUAL SETTLEMENT MODE")
    print("   Enter W (win), L (loss), or S (skip) for each prop:\n")

    settled = []
    still_pending = []

    for bet in prop_bets:
        direction = bet.get("direction", "?")
        stat      = bet.get("stat", "?")
        line      = bet.get("line", "?")
        player    = bet.get("player", bet.get("pick", "?"))
        print(f"  {player} {direction} {line} {stat}")
        print(f"  Stake: ${bet['stake']:.2f} | To win: ${bet['to_win']:.2f}")

        result_input = input("  Result (W/L/S): ").strip().upper()

        if result_input == "W":
            result = "WIN"
        elif result_input == "L":
            result = "LOSS"
        else:
            print("  → Skipped\n")
            still_pending.append(bet)
            continue

        bet["status"] = "settled"
        bet["result"] = result
        if result == "WIN":
            bet["pnl"] = round(bet["to_win"], 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] + bet["to_win"], 2)
            bankroll_data["wins"] += 1
        else:
            bet["pnl"] = round(-bet["stake"], 2)
            bankroll_data["current_bankroll"] = round(
                bankroll_data["current_bankroll"] - bet["stake"], 2)
            bankroll_data["losses"] += 1

        bankroll_data["total_bets"] += 1
        icon = "✅" if result == "WIN" else "❌"
        print(f"  {icon} → {result} | P&L: ${bet['pnl']:+.2f}\n")
        settled.append(bet)

    other_pending = [b for b in bankroll_data["pending_bets"]
                     if not (b.get("type") == "prop" and b.get("date") == date_str)]
    bankroll_data["pending_bets"]  = other_pending + still_pending
    bankroll_data["settled_bets"].extend(settled)
    bankroll_data["last_updated"]  = datetime.now().strftime("%m/%d/%Y")

    if settled:
        pnl = sum(b["pnl"] for b in settled)
        print(f"\n  Prop P&L: ${pnl:+.2f} | Bankroll: ${bankroll_data['current_bankroll']:.2f}")

    with open(BANKROLL_FILE, "w") as f:
        json.dump(bankroll_data, f, indent=2)

    import shutil
    if WEBSITE_DIR.exists():
        shutil.copy(BANKROLL_FILE, WEBSITE_DIR / "ai_bankroll.json")

    log_props_performance(settled, date_str)


def log_props_performance(settled: list, date_str: str):
    """Append settled props to props_performance_log.json."""
    if not settled:
        return

    existing = []
    if PROPS_LOG_FILE.exists():
        with open(PROPS_LOG_FILE) as f:
            existing = json.load(f)

    for b in settled:
        existing.append({
            "date":      date_str,
            "player":    b.get("player", ""),
            "stat":      b.get("stat", ""),
            "line":      b.get("line", ""),
            "direction": b.get("direction", ""),
            "pick":      b.get("pick", ""),
            "projection": b.get("projection", ""),
            "edge_pp":   b.get("edge_pp", ""),
            "model_prob": b.get("model_prob", ""),
            "odds":      b.get("ml", ""),
            "stake":     b.get("stake", ""),
            "to_win":    b.get("to_win", ""),
            "result":    b.get("result", ""),
            "pnl":       b.get("pnl", ""),
        })

    with open(PROPS_LOG_FILE, "w") as f:
        json.dump(existing, f, indent=2)
    print(f"  ✓ Props log → {PROPS_LOG_FILE} ({len(existing)} total entries)")


if __name__ == "__main__":
    import sys
    date_str = None
    if "--date" in sys.argv:
        idx = sys.argv.index("--date")
        if idx + 1 < len(sys.argv):
            date_str = sys.argv[idx + 1]
    if not date_str:
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime("%m/%d")

    settle_props(date_str)
