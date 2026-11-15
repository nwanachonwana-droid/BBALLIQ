#!/usr/bin/env python3
"""Why did the card come back with four rows?

The candidates get lost in a fixed order, so the diagnosis walks it:
  games tonight      -- a Thursday is often a 2-3 game TV night
  prices stored      -- props_lines.py has to run before the card
  board candidates   -- who cleared KEEP, FIT and the possession floor
  names with a price -- the join. A long "no price" list is a
                        name-matching bug, not a thin slate.

AND ONE CHECK THAT IS NOT ABOUT THINNESS AT ALL
  Every board candidate must play for a team that is in tonight's
  games. That sounds too obvious to test. It is not: `--board` with no
  value silently defaults to today, so for a while every backfill
  boarded tonight's slate whatever day it was asked for, and the only
  symptom was a card that looked merely thin. The join hid it -- a
  candidate from the wrong night has no price tonight, so it dropped
  out quietly as "NOT priced" and looked like a name-matching miss.

  On 29 Oct the board nominated three players for a three-game slate
  and one of them was not on any of the six teams playing. That is
  either a stale board or a filter that never checked, and both are
  bugs. Either way it is not something to work out by recalling which
  team a player is on -- I have got that wrong twice this week. The
  roster question belongs to the schedule file, so ask the schedule.

  THE NOMINATION RATE, WHILE WE ARE HERE
  "The board is thin" and "the board is over-filtering" produce the
  same tiny card and want opposite fixes. The rate separates them: of
  the players who are priced AND playing tonight, what share cleared?
  On a 3-game night a low count is arithmetic. A low RATE is a filter
  that has been tuned on bigger slates.

usage: python3 why_thin.py 20261029
"""
import os, re, sys, json, subprocess, collections

DL = os.path.expanduser('~/Downloads')
if DL not in sys.path:
    sys.path.insert(0, DL)
from defense import slate, norm_player, code        # noqa: E402

day = sys.argv[1] if len(sys.argv) > 1 else None
if not day:
    import datetime
    day = datetime.datetime.now().astimezone().strftime('%Y%m%d')

games = slate(day)
print(f'games on {day}: {len(games)}')
for a, h in games:
    print(f'    {a} @ {h}')
tonight = {code(t) for g in games for t in g}

p = os.path.expanduser('~/kalshi_data/external_picks.jsonl')
mk, players = collections.Counter(), set()
if os.path.exists(p):
    for ln in open(p):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') != day:
            continue
        mk[r.get('market')] += 1
        players.add(norm_player(r.get('player')))
print(f'\nprices stored for {day}: {dict(mk)}')
print(f'distinct players priced: {len(players)}')

out = subprocess.run(
    [sys.executable, os.path.join(DL, 'defense.py'), '--board', day,
     '--json', '--top', '60'], capture_output=True, text=True).stdout
m = re.search(r'(?m)^\{', out or '')
b = json.loads(out[m.start():]) if m else {}
rows = (b.get('overs') or []) + (b.get('unders') or [])
names = {norm_player(r['player']) for r in rows}
print(f'\nboard candidates: {len(rows)}')
print(f'  of those, priced: {len(names & players)}')
missing = sorted(names - players)
print(f'  of those, NOT priced: {len(missing)}')
for n in missing[:15]:
    print(f'    {n}')

# --- the check that is not about thinness ----------------------------
print('\nevery candidate on a team that is playing?')
if not tonight:
    print('  ? no schedule for this day -- cannot check.')
else:
    stray = sorted({(norm_player(r.get('player')), code(r.get('team')))
                    for r in rows
                    if code(r.get('team')) not in tonight})
    if not stray:
        print(f'  v all {len(rows)} candidate(s) are on one of the '
              f'{len(tonight)} teams playing.')
    else:
        print(f'  X {len(stray)} candidate(s) are NOT on any team playing '
              f'tonight:')
        for n, t in stray:
            print(f'      {n}  ({t or "no team"})')
        print('    This is a BOARD bug, not a thin slate. A candidate from\n'
              '    a night that is not tonight has no price tonight, so it\n'
              '    leaves as "NOT priced" and reads as a name-matching\n'
              '    miss. Check that defense.py --board is honouring the day\n'
              '    it was given, and that the candidate filter intersects\n'
              '    with the schedule rather than trusting the cache.')

# --- selective, or over-filtering? -----------------------------------
# A count cannot tell these apart. A rate can.
if tonight:
    print('\nnomination rate')
    print(f'  priced players: {len(players)}')
    real = {n for n, t in ((norm_player(r.get('player')),
                            code(r.get('team'))) for r in rows)
            if t in tonight}
    cleared = len(real & players)
    if players:
        pc = cleared / len(players) * 100
        print(f'  of those, nominated and playing: {cleared}  ({pc:.0f}%)')
        print(f'  games tonight: {len(games)}')
        if len(games) <= 4:
            print('\n  -> a short slate. A small card here is arithmetic:\n'
                  '     the best matchup out of six defences is not the\n'
                  '     same claim as the best out of twenty-four, and the\n'
                  '     card should be read that way rather than as a\n'
                  '     stronger signal arriving on a quiet night.')
        elif pc < 5:
            print('\n  -> a full slate and almost nothing cleared. That is\n'
                  '     the filter, not the night. Loosen KEEP or the\n'
                  '     possession floor, or find out which one is binding.')

if len(missing) > len(names & players):
    print('\n  -> most candidates have no price. Either props_lines.py did '
          'not\n     cover these games, or the names are not matching.')
elif len(rows) < 6:
    print('\n  -> the board itself is thin: few players cleared KEEP, FIT '
          'and\n     the possession floor. Not a join problem.')
