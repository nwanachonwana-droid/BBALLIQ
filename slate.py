#!/usr/bin/env python3
"""
SLATE -- log published picks, grade them, archive to CSV.

WHY THIS EXISTS
  grade.py settles Kalshi markets against Kalshi candlesticks. The
  newsletter picks are sportsbook props at American odds, which that
  grader cannot see and never will. So Day 1 of "we publish the record
  at 30 bets" recorded nothing at all.

  This closes that. A pick is logged when it is published, graded from
  the official box score the next morning, and written to a CSV that is
  the archive of record.

THE RULE THAT MATTERS
  A pick is logged with the line and price as published, and the log is
  never edited afterwards. Grading only ever adds a result to a row
  that already exists. If a pick was not logged before tip it does not
  go in -- a record you can add to retroactively is not a record, and
  the whole point of the 30-bet threshold is that the number means
  something when it arrives.

GRADING
  Player props settle on the official box score via leaguegamelog,
  which returns every player's line for a date in one call. Moneylines
  settle on the same endpoint in team mode. No second source to
  reconcile and no scores feed to keep running.

  Units are at the published American price: a winner at -160 returns
  0.625, a winner at +118 returns 1.18, a loser returns -1, and a push
  returns 0. One unit staked per pick, flat. Flat staking is what makes
  the record legible -- with varying stakes a good month and a lucky
  one look the same.

usage
  python3 slate.py add --day 20261020 \
      --pick "Cade Cunningham|PRA|over|38.5|-106|core"
  python3 slate.py add --day 20261020 --from-file picks.txt
  python3 slate.py grade --day 20261020
  python3 slate.py report
  python3 slate.py csv
"""
import os, sys, csv, json, time, argparse, subprocess, datetime, collections
import re, math, statistics, unicodedata

DATA = os.path.expanduser('~/kalshi_data')
DL = os.path.expanduser('~/Downloads')
SLATE = os.path.join(DATA, 'slate.jsonl')
ARCHIVE = os.path.join(DATA, 'slate_archive.csv')

# Same headers that work for tracking.py. Python's TLS handshake gets
# flagged by Akamai whatever headers are attached, so every request
# here is handed to curl for the same reason.

# market -> how to settle it from a box score row
MARKETS = {
    'pra':      lambda r: r['PTS'] + r['REB'] + r['AST'],
    'points':   lambda r: r['PTS'],
    'rebounds': lambda r: r['REB'],
    'assists':  lambda r: r['AST'],
    'threes':   lambda r: r['FG3M'],
    'pr':       lambda r: r['PTS'] + r['REB'],
    'pa':       lambda r: r['PTS'] + r['AST'],
    'ra':       lambda r: r['REB'] + r['AST'],
}
ALIAS = {
    'p': 'points', 'pts': 'points', 'point': 'points',
    'r': 'rebounds', 'reb': 'rebounds', 'rebound': 'rebounds',
    'a': 'assists', 'ast': 'assists', 'assist': 'assists',
    '3s': 'threes', '3pt': 'threes', 'three': 'threes', 'fg3m': 'threes',
    'p+r+a': 'pra', 'pointsreboundsassists': 'pra',
    # the names Covers and the books print
    'pointsscored': 'points', 'totalrebounds': 'rebounds',
    'totalassists': 'assists', '3pointersmade': 'threes',
    'threepointersmade': 'threes', 'pointsrebounds': 'pr',
    'pointsassists': 'pa', 'reboundsassists': 'ra',
}
TEAM_MARKETS = {'ml', 'moneyline'}


def norm_market(m):
    k = ''.join(c for c in (m or '').lower() if c.isalnum() or c == '+')
    k = ALIAS.get(k, k)
    return k


def season_for(day):
    """NBA season string for a YYYYMMDD date. Oct-Jun spans two years."""
    y, m = int(day[:4]), int(day[4:6])
    start = y if m >= 9 else y - 1
    return f'{start}-{str(start + 1)[2:]}'


def curl_json(url, timeout=30, tries=3):
    cmd = ['curl', '-s', '--compressed', '-m', str(timeout)]
    cmd.append(url)
    last = None
    for attempt in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
            if r.returncode != 0:
                raise RuntimeError(f'curl exit {r.returncode}')
            if not r.stdout.strip():
                raise RuntimeError('empty response (throttled?)')
            return json.loads(r.stdout.decode())
        except Exception as e:
            last = e
            if attempt + 1 < tries:
                print(f'    {e}, retrying', flush=True)
                time.sleep(3 + attempt * 5)
    raise RuntimeError(str(last))


# ESPN's public API, not stats.nba.com.
#
# NBA sits behind Akamai, which flagged this machine after a handful of
# calls and now returns 403 for every nba.com host including the static
# CDN. A grader that runs nightly cannot depend on a host that blocks
# you for using it. ESPN serves the same box scores with no bot check,
# no headers and no throttle, so that is what settles picks. NBA is
# still needed for tracking data, which is a weekly pull and can wait
# for a cooperative moment.
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'


def _int(s):
    try:
        return int(str(s).strip())
    except Exception:
        return 0


def espn_scoreboard(day):
    return curl_json(f'{ESPN}/scoreboard?dates={day}')


def game_log(day, player=True):
    """Box score rows for a date, shaped like the old NBA response.

    Keeping the output shape means the grading code above did not have
    to change when the source did.
    """
    board = espn_scoreboard(day)
    events = board.get('events') or []
    if not events:
        return []

    if not player:
        out = []
        for e in events:
            comp = (e.get('competitions') or [{}])[0]
            if not (comp.get('status') or {}).get('type', {}).get('completed'):
                continue
            for c in comp.get('competitors') or []:
                t = c.get('team') or {}
                out.append({
                    'TEAM_ABBREVIATION': t.get('abbreviation'),
                    'TEAM_NAME': t.get('displayName'),
                    'TEAM_SHORT': t.get('shortDisplayName'),
                    'TEAM_LOCATION': t.get('location'),
                    'WL': 'W' if c.get('winner') else 'L',
                    'PTS': _int(c.get('score')),
                })
        return out

    rows = []
    for e in events:
        comp = (e.get('competitions') or [{}])[0]
        if not (comp.get('status') or {}).get('type', {}).get('completed'):
            print(f"    skipping {e.get('name')}: not final", flush=True)
            continue
        summary = curl_json(f"{ESPN}/summary?event={e['id']}")
        for side in (summary.get('boxscore') or {}).get('players') or []:
            for blk in side.get('statistics') or []:
                labels = blk.get('labels') or []
                # index by label name, never by position: ESPN has
                # changed the column order before and a silent off-by-
                # one would mis-grade every pick without erroring
                idx = {lab: i for i, lab in enumerate(labels)}
                for ath in blk.get('athletes') or []:
                    if ath.get('didNotPlay'):
                        continue
                    st = ath.get('stats') or []
                    if len(st) < len(labels):
                        continue
                    g = lambda k: (st[idx[k]] if k in idx else '0')
                    # '3PT' arrives as "made-attempted"
                    threes = str(g('3PT')).split('-')[0]
                    rows.append({
                        'PLAYER_NAME': (ath.get('athlete') or {})
                                       .get('displayName'),
                        'PTS': _int(g('PTS')), 'REB': _int(g('REB')),
                        'AST': _int(g('AST')), 'FG3M': _int(threes),
                        'MIN': _int(g('MIN')),
                        'TEAM': (side.get('team') or {}).get('abbreviation'),
                    })
    return rows


def short(name):
    parts = [p for p in ''.join(
        c if (c.isalpha() or c in " '-") else ' '
        for c in deaccent(name)).split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0].lower()
    return f'{parts[0][0].lower()}. {parts[-1].lower()}'


def deaccent(s):
    """Strip combining marks so the same player keys the same way.

    ESPN writes "Nikola Jokic", Basketball-Reference writes "Nikola
    Jokic" with the acute. Stripping non-ascii letters afterwards
    turned one into 'nikola jokic' and the other into 'nikola joki',
    so they never matched and he silently vanished from every lookup
    -- along with Doncic, Sengun, Porzingis, Vucevic and Nurkic.
    NFKD splits the accent off the letter; dropping the combining
    marks leaves the plain letter behind instead of deleting it.
    """
    return ''.join(c for c in unicodedata.normalize('NFKD', s or '')
                   if not unicodedata.combining(c))


def norm_name(name):
    """Full normalised name. The key everything should have used.

    short() reduces a name to initial-plus-surname, so 'Jalen
    Williams' and 'Jaylin Williams' -- teammates in Oklahoma City --
    both became 'j. williams'. The box-score index was a plain dict,
    so whichever of them appeared later silently overwrote the other,
    and a Jalen Williams under 18.5 was settled against Jaylin's 3
    points and booked as a winner. Nothing raised, nothing logged.

    bref.py hit the identical collision on Donovan and Davion Mitchell
    and was fixed with exactly this function. It was not carried over
    to this file, which is the one that writes permanent results.
    """
    n = re.sub(r"[^a-z ]", ' ', deaccent(name).lower())
    parts = [p for p in n.split()
             if p not in ('jr', 'sr', 'ii', 'iii', 'iv', 'v')]
    return ' '.join(parts)


class Roster:
    """Box-score lookup that refuses rather than guesses.

    Full name first. The initial-plus-surname form is kept only as a
    fallback for subjects written that way, and ONLY when it resolves
    to one player -- if two share it, the lookup returns a problem
    instead of a row. A grader that picks one of two possible players
    is worse than one that stops and says it cannot tell, because the
    first kind writes a permanent result nobody re-reads.
    """

    def __init__(self, rows, key='PLAYER_NAME'):
        self.full, self.full_dupes = {}, set()
        self.abbr, self.abbr_dupes = {}, set()
        for r in rows:
            nm = r.get(key)
            if not nm:
                continue
            f = norm_name(nm)
            if f in self.full and self.full[f] is not r:
                self.full_dupes.add(f)
            self.full[f] = r
            s = short(nm)
            if s in self.abbr and self.abbr[s] is not r:
                self.abbr_dupes.add(s)
            self.abbr[s] = r

    def __len__(self):
        return len(self.full)

    def find(self, subject):
        """(row, problem). problem is None only on a single clean match."""
        f = norm_name(subject)
        if f in self.full_dupes:
            return None, f'two players in the box score are called "{f}"'
        if f in self.full:
            return self.full[f], None
        s = short(subject)
        if s in self.abbr_dupes:
            return None, (f'"{subject}" is ambiguous -- more than one '
                          f'player matches "{s}", and the full name is '
                          f'not in the box score')
        if s in self.abbr:
            return self.abbr[s], None
        return None, 'did not appear in the box score (DNP?)'


def payout(odds):
    """Units returned on a one-unit winner at American odds."""
    o = float(odds)
    return o / 100.0 if o > 0 else 100.0 / abs(o)


# Keys every reader in this file assumes with a bracket, and that a
# writer may legitimately not know about. Filled on load so one row
# from an older writer cannot kill grade, report, tails and clv all at
# once -- which is what KeyError: 'result' has now done twice.
ROW_SHELL = {
    'result': None, 'actual': None, 'units': None, 'graded_at': None,
    'tier': 'core', 'surface': None, 'status': None, 'grade': None,
    'p_mine': None, 'clv': None,
}

# Identity. A row missing any of these is CORRUPT, not merely written
# by an older writer, and defaulting it would turn a loud crash into a
# quietly mis-graded bet. Reported, never filled.
ROW_REQUIRED = ('day', 'subject', 'market', 'side', 'line', 'odds')


def load(quiet=False):
    if not os.path.exists(SLATE):
        return []
    out, bad, filled = [], [], 0
    for i, ln in enumerate(open(SLATE), 1):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            bad.append((i, 'unparseable'))
            continue
        miss = [k for k in ROW_REQUIRED if k not in r]
        if miss:
            bad.append((i, 'missing ' + ','.join(miss)))
            continue
        before = len(r)
        for k, v in ROW_SHELL.items():
            r.setdefault(k, v)
        if len(r) != before:
            filled += 1
        out.append(r)
    if not quiet and filled:
        print(f'  note: filled default fields on {filled} row(s) written '
              f'by an older\n  writer. Harmless -- they grade normally. '
              f'three.py --repair-log\n  writes them to disk if you want '
              f'them clean.')
    if not quiet and bad:
        print(f'  !! SKIPPED {len(bad)} CORRUPT row(s) in {SLATE}:')
        for i, why in bad[:6]:
            print(f'  !!   line {i}: {why}')
        print('  !! These are NOT graded and NOT counted. A row missing '
              'its own\n  !! identity cannot be settled, and guessing '
              'the gap would be worse.')
    return out


def save(rows):
    tmp = SLATE + '.tmp'
    with open(tmp, 'w') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')
    os.replace(tmp, SLATE)


def parse_pick(s, day):
    """subject | market | side | line | odds | [tier]"""
    parts = [p.strip() for p in s.split('|')]
    if len(parts) < 5:
        raise ValueError(
            f'need subject|market|side|line|odds[|tier], got: {s}')
    subject, market, side, line, odds = parts[:5]
    tier = parts[5].lower() if len(parts) > 5 else 'core'
    mk = norm_market(market)
    if mk not in MARKETS and mk not in TEAM_MARKETS:
        raise ValueError(f'unknown market "{market}" in: {s}\n'
                         f'  known: {", ".join(sorted(MARKETS))}, ml')
    side = side.lower()
    if mk in TEAM_MARKETS:
        side, line = 'win', 0.0
    elif side not in ('over', 'under'):
        raise ValueError(f'side must be over/under, got "{side}" in: {s}')
    return {
        'day': day, 'subject': subject, 'market': mk, 'side': side,
        'line': float(line), 'odds': int(odds), 'tier': tier,
        'logged_at': datetime.datetime.now().astimezone().isoformat(),
        'result': None, 'actual': None, 'units': None, 'graded_at': None,
    }


def cmd_add(a):
    specs = list(a.pick or [])
    if a.from_file:
        for ln in open(os.path.expanduser(a.from_file)):
            ln = ln.strip()
            if ln and not ln.startswith('#'):
                specs.append(ln)
    if not specs:
        print('nothing to add: pass --pick or --from-file', file=sys.stderr)
        return 2

    rows = load()
    have = {(r['day'], norm_name(r['subject']), r['market'], r['side'],
             r['line']) for r in rows}
    added = 0
    for s in specs:
        try:
            p = parse_pick(s, a.day)
        except ValueError as e:
            print(f'  ! {e}', file=sys.stderr)
            return 2
        k = (p['day'], norm_name(p['subject']), p['market'], p['side'], p['line'])
        if k in have:
            print(f"  = already logged: {p['subject']} {p['side']} "
                  f"{p['line']} {p['market']}")
            continue
        rows.append(p)
        have.add(k)
        added += 1
        print(f"  + {p['subject']:26}{p['side']:6}{p['line']:<7}"
              f"{p['market']:10}{p['odds']:+5d}  [{p['tier']}]")
    os.makedirs(DATA, exist_ok=True)
    save(rows)
    print(f'\n{added} logged to {SLATE}')
    return 0


def cmd_audit(a):
    """Re-settle every graded pick and report where it disagrees.

    The name collision that settled Jalen Williams against Jaylin
    Williams was silent: no error, no warning, a plausible number in
    the actual column and a win in the record. Nothing in the normal
    flow would ever have surfaced it. So the grader needs an
    independent pass that re-derives every result from the box score
    and compares, because the failure mode of a grader is not a crash,
    it is a number that looks fine.

    Read-only by default. --fix corrects a mismatch and keeps the old
    values in a 'regraded' field rather than overwriting them: the log
    is not edited to flatter a result, and a correction that erases
    its own history is indistinguishable from one.
    """
    rows = load()
    todo = [r for r in rows
            if r.get('result') in ('win', 'loss', 'push')
            and r.get('market') not in TEAM_MARKETS
            and (not a.day or r['day'] == a.day)]
    if not todo:
        print('nothing graded to audit'
              + (f' for {a.day}' if a.day else ''))
        return 0

    by_day = collections.defaultdict(list)
    for r in todo:
        by_day[r['day']].append(r)

    ok = bad = unver = 0
    changed = []
    for day, picks in sorted(by_day.items()):
        try:
            box = Roster(game_log(day, player=True))
        except Exception as e:
            print(f'{day}: box score fetch failed ({e}) -- '
                  f'{len(picks)} pick(s) unverified', file=sys.stderr)
            unver += len(picks)
            continue
        if not len(box):
            print(f'{day}: no box scores -- {len(picks)} unverified',
                  file=sys.stderr)
            unver += len(picks)
            continue
        for p in picks:
            row, problem = box.find(p['subject'])
            if problem:
                print(f"  ? {day} {p['subject']:24} {problem}")
                unver += 1
                continue
            res, act = settle(p['market'], p['side'], p['line'], row)
            if res is None:
                unver += 1
                continue
            if res == p['result'] and act == p.get('actual'):
                ok += 1
                continue
            bad += 1
            print(f"  X {day} {p['subject']:24}{p['side']:6}"
                  f"{p['line']:<7}{p['market']:10}\n"
                  f"      stored  actual {str(p.get('actual')):>6}  "
                  f"-> {p['result']}  {p.get('units', 0):+.3f}u\n"
                  f"      actual  actual {str(act):>6}  -> {res}  "
                  f"{(payout(p['odds']) if res == 'win' else (0.0 if res == 'push' else -1.0)):+.3f}u")
            changed.append((p, res, act))

    print(f'\n{ok} confirmed, {bad} WRONG, {unver} unverified')
    if bad and not a.fix:
        print('\nRe-run with --fix to correct them. The original values '
              'are kept\nin a `regraded` field -- the row is corrected, '
              'not rewritten.')
    if bad and a.fix:
        now = datetime.datetime.now().astimezone().isoformat()
        for p, res, act in changed:
            p['regraded'] = {
                'at': now, 'reason': 'name-collision audit',
                'was_actual': p.get('actual'), 'was_result': p.get('result'),
                'was_units': p.get('units')}
            p['actual'] = act
            p['result'] = res
            p['units'] = (payout(p['odds']) if res == 'win'
                          else (0.0 if res == 'push' else -1.0))
        save(rows)
        write_csv(rows)
        print(f'{bad} corrected; each carries a `regraded` record of '
              'what it said before.')
    return 1 if (bad and not a.fix) else 0


def cmd_tails(a):
    """Does the process actually catch right tails, or just coin-flip?

    Win rate cannot answer this and neither can CLV. Both treat a pick
    that missed by a point and one that was never close as the same
    loss, and a 40-point explosion and a one-point clear as the same
    win. But the whole reason for screening soft defences and heavy
    possession loads is to buy the chance of a blowup, and a bet on
    the right tail has a specific signature:

        the wins should be BIGGER than the losses.

    If margins come back symmetric, the selection is finding fair
    coins -- which may still be fine at the right price, but it is not
    the thesis, and it should stop being described as the thesis.
    Margins are relative to the line so a 30-point scorer and a
    2.5-assist role player can sit in the same average.

    TWO THINGS THE RATIO CANNOT DO, both of which it was read as doing
      1. It is not money. A binary pays the same for a 40-point
         explosion as for a one-point clear, so a ratio of 1.8 on a
         14-14 record is still a losing book. The P&L line below runs
         first now, because the ratio was being read as a result.
      2. It is not comparable across sides. An over's margin is capped
         at -100% -- a player cannot score less than zero -- while its
         upside has no cap at all. So a book of overs on a right-skewed
         stat produces a ratio above 1 with no skill whatsoever. The
         only honest reading is overs against unders: if the ratio
         survives on BOTH sides it is selection, and if it lives only
         on the overs it is the floor at zero.
    """
    rows = [r for r in load()
            if r.get('result') in ('win', 'loss')
            and r.get('market') not in TEAM_MARKETS
            and isinstance(r.get('actual'), (int, float))
            and r.get('line')]
    if a.day:
        rows = [r for r in rows if r['day'] == a.day]
    if a.market:
        rows = [r for r in rows if r['market'] == a.market]
    if getattr(a, 'tier', None):
        rows = [r for r in rows if r.get('tier', 'core') == a.tier]
    # Dates, because the tier label is not reliable history. Every pick
    # logged without a sixth field defaults to 'core', so the Covers-era
    # rows and the screen's rows can be the same tier and the breakdown
    # below prints nothing -- which looks exactly like a stale script
    # rather than an absent label. The day is always there.
    if getattr(a, 'since', None):
        rows = [r for r in rows if r['day'] >= a.since]
    if getattr(a, 'until', None):
        rows = [r for r in rows if r['day'] <= a.until]
    if not rows:
        print('no graded player picks to measure'
              + (f' for {a.day}' if a.day else ''))
        return 0

    for r in rows:
        # signed toward the bet, so an under that cleared is positive
        raw = r['actual'] - r['line']
        r['_margin'] = raw if r.get('side', 'over') == 'over' else -raw
        r['_rel'] = r['_margin'] / r['line'] * 100.0

    wins = [r for r in rows if r['_margin'] > 0]
    losses = [r for r in rows if r['_margin'] <= 0]
    w_mean = statistics.fmean([r['_rel'] for r in wins]) if wins else 0.0
    l_mean = statistics.fmean([abs(r['_rel']) for r in losses]) \
        if losses else 0.0
    ratio = (w_mean / l_mean) if l_mean else None

    if a.json:
        print(json.dumps({'n': len(rows), 'wins': len(wins),
                          'losses': len(losses),
                          'win_margin_pct': round(w_mean, 1),
                          'loss_margin_pct': round(l_mean, 1),
                          'ratio': None if ratio is None else round(ratio, 2),
                          'rows': [{k: v for k, v in r.items()
                                    if not k.startswith('_')}
                                   | {'rel_pct': round(r['_rel'], 1)}
                                   for r in rows]}, indent=1))
        return 0

    scope = []
    if a.day:
        scope.append(f'on {a.day}')
    for k, lbl in (('since', 'from'), ('until', 'to'), ('tier', 'tier'),
                   ('market', 'market')):
        v = getattr(a, k, None)
        if v:
            scope.append(f'{lbl} {v}')
    print(f'\nTAIL SHAPE -- {len(rows)} graded player picks'
          + (('  [' + ', '.join(scope) + ']') if scope else ''))

    # The money first. Everything below is shape, and shape was being
    # mistaken for performance: 14-14 reads like a coin flip and prices
    # like a loss, because the juice is the whole difference.
    staked = len(rows)
    ret = sum(payout(r['odds']) for r in rows
              if r['_margin'] > 0 and r.get('odds'))
    pnl = ret - len(losses)
    ev_break = 0.0
    if rows:
        avg_pay = statistics.fmean([payout(r['odds']) for r in rows
                                    if r.get('odds')] or [0.87])
        ev_break = 1.0 / (1.0 + avg_pay) * 100.0
    wr = len(wins) / len(rows) * 100.0
    # 95% normal-approximation band on the win rate. At n<50 this is
    # wide enough to contain both 'no edge' and 'good edge', and saying
    # so is the point -- a record inside the band has not measured
    # anything yet, however the margins look.
    half = 1.96 * (0.25 / len(rows)) ** 0.5 * 100.0
    print(f'  record {len(wins)}-{len(losses)}  ({wr:.0f}%), '
          f'{pnl:+.2f} units on {staked:.0f} flat')
    print(f'  break-even at the prices paid is {ev_break:.1f}%  '
          f'-- 95% band {max(0, wr-half):.0f}%..{min(100, wr+half):.0f}%')
    if wr - half < ev_break < wr + half:
        print('  break-even sits INSIDE the band: this sample cannot tell '
              'an edge\n  from no edge. Nothing here justifies sizing up.')

    print(f'\n  {len(wins)} won by {w_mean:.1f}% of the line on average')
    print(f'  {len(losses)} lost by {l_mean:.1f}% on average')
    if ratio is not None:
        print(f'  ratio {ratio:.2f}  ', end='')
        if len(rows) < 25:
            print(f'-- on {len(rows)} picks this is not yet a measurement.')
        elif ratio >= 1.25:
            print('-- wins are running bigger than losses, which is the '
                  'signature\n  the thesis predicts.')
        elif ratio <= 0.8:
            print('-- losses are running bigger, the opposite of the '
                  'thesis.\n  The selection may be buying overs that need '
                  'everything to break right.')
        else:
            print('-- margins are symmetric. The selection is finding fair\n'
                  '  coins rather than right tails, whatever the win rate '
                  'says.')

    buckets = [('blowup   >+25%', lambda v: v > 25),
               ('clear  0..+25%', lambda v: 0 < v <= 25),
               ('near miss -15..0', lambda v: -15 <= v <= 0),
               ('wipeout  <-15%', lambda v: v < -15)]
    print()
    for label, test in buckets:
        hits = [r for r in rows if test(r['_rel'])]
        bar = '#' * round(len(hits) / len(rows) * 40)
        print(f"  {label:18}{len(hits):>3}  {len(hits)/len(rows)*100:>4.0f}%  {bar}")

    # BY TIER -- not a breakdown, a correction.
    # The log holds two different processes: picks copied off the
    # Covers best-bets list, and picks the screen made. Pooling them
    # produces a ratio that describes neither, and the pooled 1.77 was
    # read as the model's signature when most of the sample was
    # somebody else's selections. Any tier with fewer than 25 rows is
    # marked for what it is -- a look, not a measurement.
    by_tier = collections.defaultdict(list)
    for r in rows:
        by_tier[r.get('tier', 'core')].append(r)
    if len(by_tier) > 1:
        print(f"\n  {'TIER':10}{'N':>4}{'REC':>8}{'UNITS':>8}"
              f"{'WIN%':>7}{'LOSS%':>7}{'RATIO':>7}")
        for t, sub in sorted(by_tier.items(), key=lambda kv: -len(kv[1])):
            sw = [r for r in sub if r['_margin'] > 0]
            sl = [r for r in sub if r['_margin'] <= 0]
            wm = statistics.fmean([r['_rel'] for r in sw]) if sw else 0.0
            lm = statistics.fmean([abs(r['_rel']) for r in sl]) if sl else 0.0
            u = sum(payout(r['odds']) for r in sw if r.get('odds')) - len(sl)
            rt = f'{wm/lm:7.2f}' if lm else '      -'
            print(f'  {t:10}{len(sub):>4}{f"{len(sw)}-{len(sl)}":>8}'
                  f'{u:>+8.2f}{wm:>7.1f}{lm:>7.1f}{rt}'
                  + ('   (too few to measure)' if len(sub) < 25 else ''))
        print('  Compare tiers, not the pooled line above it. The pooled\n'
              '  number mixes selection processes and belongs to none of '
              'them.')
    else:
        only = next(iter(by_tier))
        d = sorted(r['day'] for r in rows)
        print(f"\n  all {len(rows)} rows are tier '{only}', {d[0]} to "
              f"{d[-1]}, so this\n  cannot separate eras. If some of these "
              f"were copied off a best-bets\n  list and the rest came from "
              f"the screen, split them by date:\n"
              f"    slate.py tails --since YYYYMMDD\n"
              f"    slate.py tails --until YYYYMMDD\n"
              f"  and pass a tier when logging from here on so it is "
              f"recorded, not\n  reconstructed.")

    # OVERS vs UNDERS -- the confound, not a curiosity.
    # An over bottoms out at -100% and has no ceiling; an under is the
    # mirror, capped above. Pooling them manufactures whichever ratio
    # the book happens to lean toward, so the sides are reported apart
    # and the pooled number is only trustworthy where both agree.
    print()
    for side in ('over', 'under'):
        sub = [r for r in rows if r.get('side', 'over') == side]
        if not sub:
            continue
        sw = [r for r in sub if r['_margin'] > 0]
        sl = [r for r in sub if r['_margin'] <= 0]
        wm = statistics.fmean([r['_rel'] for r in sw]) if sw else 0.0
        lm = statistics.fmean([abs(r['_rel']) for r in sl]) if sl else 0.0
        rt = f'{wm/lm:5.2f}' if lm else '    -'
        floored = sum(1 for r in sub if r['_rel'] <= -99.0)
        note = ''
        if floored:
            note = (f'  <- {floored} at the floor, '
                    f'which is what caps this side')
        print(f'  {side:6}{len(sub):>3} picks  {len(sw)}-{len(sl)}  '
              f'win {wm:>5.1f}%  loss {lm:>5.1f}%  ratio {rt}{note}')
    sides = {r.get('side', 'over') for r in rows}
    if len(sides) < 2:
        print(f'  only {sides.pop()}s logged, so the pooled ratio above is '
              'not yet\n  separable from the floor at zero.')

    by_mkt = collections.defaultdict(list)
    for r in rows:
        by_mkt[r['market']].append(r['_rel'])
    print(f"\n  {'MARKET':12}{'N':>4}{'MEAN':>8}{'BEST':>8}{'WORST':>8}")
    for mk, vs in sorted(by_mkt.items(), key=lambda kv: -len(kv[1])):
        print(f'  {mk:12}{len(vs):>4}{statistics.fmean(vs):>+8.1f}'
              f'{max(vs):>+8.1f}{min(vs):>+8.1f}')
    print('\n  Margins are (actual - line) / line, signed toward the bet.\n'
          '  Read with the CLV report, not instead of it: CLV says the\n'
          '  price was right, this says the shape was.')
    return 0


def cmd_retier(a):
    """Move picks between tiers, which is how a pass is published.

    A pass is not the same thing as a failure, and until now the site
    had no way to tell them apart. publish.sh keeps the previous data
    file when a generator fails -- correct, because a crashed parser
    should not blank the page -- and the front end features every
    ungraded row dated today. So on a night the screen ran and nothing
    cleared the bar, rows logged earlier in the day stayed on the card
    and the honest "Nothing tonight." panel never appeared. The site
    looked like it was selling picks nobody stood behind.

    The 'log' tier is the answer and it was simply never applied:
    logged at the line and price, graded tomorrow, not featured
    tonight. Deleting the rows would be the dishonest fix -- it would
    make the record look like the pass never happened, and the whole
    argument of the log page is that nothing is removed when it loses.

    Preview by default. --apply writes, and keeps the previous tier in
    a 'retiered' field, for the same reason audit --fix does: a change
    that erases its own history cannot be told from a cover-up.
    """
    rows = load()
    want = [r for r in rows
            if r['day'] == a.day
            and (not a.subject
                 or norm_name(a.subject) in norm_name(r['subject']))
            and r.get('tier', 'core') != a.tier]
    if not want:
        here = [r for r in rows if r['day'] == a.day]
        if not here:
            print(f'no picks logged for {a.day}')
        else:
            print(f"all {len(here)} pick(s) for {a.day} are already "
                  f"tier '{a.tier}'")
        return 0

    graded = [r for r in want if r.get('result')]
    print(f"\n{a.day}: {len(want)} pick(s) -> tier '{a.tier}'")
    for r in want:
        mark = f"  {r.get('result') or 'pending':8}"
        print(f"  {r['subject']:26}{r['side']:6}{r['line']:<7}"
              f"{r['market']:10}{r['odds']:+5d}  "
              f"[{r.get('tier', 'core')} -> {a.tier}]{mark}")
    if graded:
        print(f'\n  {len(graded)} of these are already graded. Retiering '
              'does not change\n  the result or the units -- they stay in '
              'the record and the log.')
    if not a.apply:
        print('\nPreview only. Re-run with --apply to write.')
        return 0

    now = datetime.datetime.now().astimezone().isoformat()
    ids = {id(r) for r in want}
    for r in rows:
        if id(r) in ids:
            r.setdefault('retiered', []).append(
                {'from': r.get('tier', 'core'), 'to': a.tier, 'at': now})
            r['tier'] = a.tier
    save(rows)
    print(f"\nwrote {len(want)} row(s) to tier '{a.tier}' in {SLATE}")
    print('Now: cd ~/sharpz-site && ./publish.sh')
    return 0


def cmd_grades(a):
    """Did the grades mean anything? Record split by letter.

    THE QUESTION THIS ANSWERS, WHICH THE POOLED RECORD CANNOT
      Every row on the card carries a grade, and the grades claim
      different things: an A says the modelled chance sits five or
      more points above break-even, an F says it is below it. Pooling
      an A and an F into one win rate asks whether the card wins on
      average, which is not the claim being made. The claim is that
      the ORDER is real -- that A beats B beats C, and that F rows
      lose. One number cannot test an ordering.

    TWO THINGS ARE CHECKED
      Monotonicity: does the win rate fall as the grade does? That is
      the whole bet on the rubric, and it needs no outside benchmark.

      Calibration: each row also carries the probability it was given,
      so the band's average prediction can be compared against what
      happened. A band predicting 58% and returning 51% is not a bad
      band, it is an over-confident one, and the fix is different --
      shift the thresholds rather than abandon the grade.

    Measurement rows count here even though they never count toward
    the published record. They were never bet, so they belong nowhere
    near the money, but they are exactly the evidence about whether
    the letters work -- and there are an order of magnitude more of
    them.
    """
    rows = [r for r in load()
            if r.get('result') in ('win', 'loss') and r.get('grade')]
    if a.day:
        rows = [r for r in rows if r['day'] == a.day]
    if a.since:
        rows = [r for r in rows if r['day'] >= a.since]
    if not rows:
        print('no graded rows carry a grade yet.\n'
              'Rows logged by card.py --log carry one; rows added by hand '
              'do not.')
        return 0

    by = collections.defaultdict(list)
    for r in rows:
        by[r['grade']].append(r)

    if a.json:
        print(json.dumps({g: {'n': len(v),
                              'won': sum(1 for r in v if r['result'] == 'win')}
                          for g, v in sorted(by.items())}, indent=1))
        return 0

    print(f'\nBY GRADE -- {len(rows)} graded rows')
    print(f"  {'GRADE':6}{'N':>5}{'W-L':>8}{'WIN%':>8}{'SE':>7}"
          f"{'SAID':>8}{'MISS':>8}{'UNITS':>8}")
    order = 'ABCDF'
    seen = []
    for g in order:
        v = by.get(g)
        if not v:
            continue
        w = sum(1 for r in v if r['result'] == 'win')
        n = len(v)
        rate = w / n
        se = math.sqrt(rate * (1 - rate) / n) if n else 0.0
        said = [r['p_mine'] for r in v if r.get('p_mine') is not None]
        pred = statistics.fmean(said) if said else None
        u = sum(r.get('units') or 0.0 for r in v)
        ps = '      -' if pred is None else f'{pred*100:>7.1f}'
        ms = '      -' if pred is None else f'{(rate-pred)*100:>+7.1f}'
        print(f'  {g:6}{n:>5}{f"{w}-{n-w}":>8}{rate*100:>7.1f}%'
              f'{se*100:>6.1f}%{ps}{ms}{u:>+8.2f}')
        seen.append((g, n, rate, pred))

    if len(seen) < 2:
        print('\n  one grade only -- nothing to compare yet.')
        return 0

    # NOT a monotonicity check.
    #
    # Requiring every band to beat the one below it sounds like the
    # obvious test and is far too brittle: at sixty rows a band the
    # standard error is six points, so two bands five points apart
    # invert constantly. Run against data with a REAL ordering built
    # in -- A .60, B .55, C .50, F .44 -- the strict check cried
    # "NOT monotone" on an ordering that was true by construction.
    #
    # Two tests that survive that noise instead: a trend across all
    # the bands at once, and the top half against the bottom half,
    # where the gap the rubric claims is largest.
    print('\n  Is the order real?')
    idx = {g: i for i, g in enumerate(order)}
    xs, ys = [], []
    for g, v in by.items():
        for r in v:
            xs.append(idx[g])
            ys.append(1.0 if r['result'] == 'win' else 0.0)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx > 0 and len(xs) > 2:
        b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        resid = [y - (my + b * (x - mx)) for x, y in zip(xs, ys)]
        s2 = sum(r * r for r in resid) / (len(xs) - 2)
        se_b = math.sqrt(s2 / sxx)
        z = b / se_b if se_b else 0.0
        pts = -b * 100
        print(f'    trend: each grade step down costs {pts:+.1f} points of '
              f'win rate\n           ({z:+.2f} SE)')
        if z <= -2:
            print('           -- the grades carry real ordering information.')
        elif z >= 2:
            print('           -- BACKWARDS. Lower grades are winning more, '
                  'which is\n              a rubric fault, not a thin sample.')
        else:
            print('           -- not distinguishable from no ordering at all.')

    top = [r for g in ('A', 'B') for r in by.get(g, [])]
    bot = [r for g in ('D', 'F') for r in by.get(g, [])]
    if len(top) >= 10 and len(bot) >= 10:
        pt = sum(1 for r in top if r['result'] == 'win') / len(top)
        pb = sum(1 for r in bot if r['result'] == 'win') / len(bot)
        pool = ((sum(1 for r in top if r['result'] == 'win')
                 + sum(1 for r in bot if r['result'] == 'win'))
                / (len(top) + len(bot)))
        se = math.sqrt(pool * (1 - pool) * (1 / len(top) + 1 / len(bot)))
        zz = (pt - pb) / se if se else 0.0
        print(f'    A+B {pt*100:.1f}% ({len(top)}) vs D+F {pb*100:.1f}% '
              f'({len(bot)}): {pt*100-pb*100:+.1f} points, {zz:+.2f} SE')
        if abs(zz) < 2:
            need = math.ceil((1.96 / max(abs(pt - pb), 1e-6)) ** 2
                             * pool * (1 - pool) * 2)
            print(f'           -- not yet separable. At this gap it would '
                  f'take about\n              {need} rows a side.')
    small = [g for g, n, _r, _p in seen if n < 25]
    if small:
        worst = min(x[1] for x in seen)
        band = 1.96 * math.sqrt(0.25 / worst) * 100
        print(f'    Thin bands ({", ".join(small)}): at {worst} rows the 95% '
              f'band on a\n    win rate is about +/-{band:.0f} points.')

    print('\n  SAID is the average probability those rows were given; MISS\n'
          '  is actual minus predicted. A band that wins less than it said\n'
          '  is over-confident -- move the thresholds, do not bin the grade.')
    return 0


def cmd_grade(a):
    rows = load()
    todo = [r for r in rows
            if r['result'] is None and (not a.day or r['day'] == a.day)]
    if not todo:
        print('nothing ungraded' + (f' for {a.day}' if a.day else ''))
        return 0

    by_day = collections.defaultdict(list)
    for r in todo:
        by_day[r['day']].append(r)

    for day, picks in sorted(by_day.items()):
        need_team = any(p['market'] in TEAM_MARKETS for p in picks)
        need_player = any(p['market'] in MARKETS for p in picks)
        print(f'{day}: grading {len(picks)} picks')
        plog, tlog = {}, {}
        try:
            if need_player:
                plog = Roster(game_log(day, player=True))
            if need_team:
                for r in game_log(day, player=False):
                    for k in ('TEAM_ABBREVIATION', 'TEAM_NAME',
                              'TEAM_SHORT', 'TEAM_LOCATION'):
                        v = r.get(k)
                        if v:
                            tlog[v.upper()] = r
                            tlog[v.lower()] = r
        except Exception as e:
            print(f'  box score fetch failed: {e}', file=sys.stderr)
            print('  nothing graded for this day; rerun later',
                  file=sys.stderr)
            continue
        if need_player and not plog:
            print(f'  no player box scores for {day} -- too early, '
                  f'or no games', file=sys.stderr)
            continue

        for p in picks:
            if p['market'] in TEAM_MARKETS:
                t = (tlog.get(p['subject'].upper())
                     or tlog.get(p['subject'].lower()))
                if not t:
                    print(f"  ? {p['subject']}: no team game found")
                    continue
                won = (t.get('WL') == 'W')
                p['actual'] = t.get('WL')
                p['result'] = 'win' if won else 'loss'
            else:
                row, problem = plog.find(p['subject'])
                if problem:
                    # left ungraded on purpose: it will be retried on
                    # the next run, and an ungraded row is visibly
                    # missing where a wrong one is not
                    print(f"  ? {p['subject']}: {problem}")
                    continue
                try:
                    actual = MARKETS[p['market']](row)
                except KeyError as e:
                    print(f"  ? {p['subject']}: box score missing {e}")
                    continue
                p['actual'] = actual
                if actual == p['line']:
                    p['result'] = 'push'
                elif p['side'] == 'over':
                    p['result'] = 'win' if actual > p['line'] else 'loss'
                else:
                    p['result'] = 'win' if actual < p['line'] else 'loss'

            p['units'] = (payout(p['odds']) if p['result'] == 'win'
                          else (0.0 if p['result'] == 'push' else -1.0))
            p['graded_at'] = datetime.datetime.now().astimezone().isoformat()
            mark = {'win': 'W', 'loss': 'L', 'push': 'P'}[p['result']]
            print(f"  {mark} {p['subject']:26}{p['side']:6}{p['line']:<7}"
                  f"{p['market']:10}actual {str(p['actual']):>6}"
                  f"  {p['units']:+.3f}u")

    save(rows)
    write_csv(rows)
    return 0


def write_csv(rows):
    os.makedirs(DATA, exist_ok=True)
    cols = ['day', 'subject', 'market', 'side', 'line', 'odds', 'tier',
            'result', 'actual', 'units', 'logged_at', 'graded_at']
    with open(ARCHIVE, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r['day'], r['subject'])):
            w.writerow(r)
    return ARCHIVE


# Tiers that were actually put in front of a reader. A 'log' pick was
# logged at the line and price and graded like any other, but the card
# never featured it -- the screen ran on a night nothing cleared the
# bar and the honest panel said so.
#
# WHY THE HEADLINE EXCLUDES THEM
#   One number was doing two jobs. The record on the site answers
#   "what would following this have returned?", and a pick nobody
#   could have seen does not belong in that answer. Counting them
#   cuts both ways and one direction is worse than the other: a
#   withheld pick that lands flatters the published record with
#   something no subscriber ever got, which is the failure mode that
#   matters. So the headline is featured-only and every tier stays in
#   by_tier, where the whole book -- including the pass days -- is
#   still there to measure.
#
#   The log page keeps everything. Nothing is deleted and nothing is
#   hidden; the two questions just stop sharing one number.
UNFEATURED = {'log'}


def tally(graded):
    """Counts over rows ALREADY filtered to win/loss/push.

    Same two bracket accesses that broke summarise: result through a
    dict with no 'void' key, and r['units'] on a row that may not
    carry the key. Both are .get() here so one odd row cannot take
    down record.json for the whole file. Callers are expected to have
    filtered on RESULT_CODE, and a row that slips through is skipped
    rather than raising.
    """
    t = {'w': 0, 'l': 0, 'p': 0, 'u': 0.0}
    for r in graded:
        code = RESULT_CODE.get(r.get('result'))
        if code is None:
            continue
        t[code] += 1
        t['u'] += r.get('units') or 0.0
    return t


def _block(t):
    settled = t['w'] + t['l']
    return {
        'record': f"{t['w']}-{t['l']}" + (f"-{t['p']}" if t['p'] else ''),
        'wins': t['w'], 'losses': t['l'], 'pushes': t['p'],
        'units': round(t['u'], 3),
        'roi_pct': round(t['u'] / settled * 100, 2) if settled else None,
        'win_pct': round(t['w'] / settled * 100, 1) if settled else None,
    }


RESULT_CODE = {'win': 'w', 'loss': 'l', 'push': 'p'}


def summarise(rows):
    """The record. VOIDS ARE NOT RESULTS.

    THE BUG THIS FIXES, found 16 Nov after 11 days of
    "record failed - keeping the published record.json".

    This read `graded = [r for r in rows if r['result']]` and then
    looked the result up in {'win','loss','push'}. three.py
    --void-dnp writes result='void' for a man who never dressed --
    truthy, so it passed the filter, and then the dict lookup raised
    KeyError: 'void'. One voided pick therefore stopped record.json
    rebuilding for the WHOLE file, every night, and publish.sh
    reported only "record failed" because the exception never
    surfaced further.

    A void is not a bet that lost, it is a bet that never happened,
    so it is excluded from the record and counted separately rather
    than mapped to anything. Unknown result strings are counted too
    instead of crashing: a new vocabulary word in one row must not be
    able to take down the record again.
    """
    graded = [r for r in rows if r.get('result') in RESULT_CODE]
    voided = [r for r in rows if r.get('result') == 'void']
    odd = [r for r in rows
           if r.get('result') and r.get('result') not in RESULT_CODE
           and r.get('result') != 'void']
    if odd:
        seen = sorted({str(r.get('result')) for r in odd})
        print(f'  ! {len(odd)} row(s) carry an unknown result '
              f'({", ".join(seen)}) and are excluded',
              file=sys.stderr)
    featured = [r for r in graded
                if r.get('tier', 'core') not in UNFEATURED]
    by_tier = collections.defaultdict(
        lambda: {'w': 0, 'l': 0, 'p': 0, 'u': 0.0})
    for r in graded:
        t = by_tier[r.get('tier', 'core')]
        t[RESULT_CODE[r['result']]] += 1
        t['u'] += r.get('units') or 0.0

    feat_rows = [r for r in rows if r.get('tier', 'core') not in UNFEATURED]
    out = {
        'logged': len(feat_rows), 'graded': len(featured),
        # a VOID is not pending: it never happened. Counting it as
        # pending is what made "to_go" never reach zero.
        'pending': len(feat_rows) - len(featured)
                   - len([r for r in feat_rows
                          if r.get('result') == 'void']),
        'unlocks_at': 30, 'to_go': max(0, 30 - len(featured)),
        'by_tier': {k: {'record': f"{v['w']}-{v['l']}"
                        + (f"-{v['p']}" if v['p'] else ''),
                        'units': round(v['u'], 3),
                        'featured': k not in UNFEATURED}
                    for k, v in sorted(by_tier.items())},
        'days': sorted({r['day'] for r in feat_rows if r.get('day')}),
        'generated': datetime.datetime.now().astimezone().isoformat(),
    }
    # the published figures: featured only, and the keys keep their old
    # names so record.json stays the same shape the page already reads
    out.update(_block(tally(featured)))
    # the whole book, for his own P&L -- every tier, nothing dropped
    out['all_tiers'] = _block(tally(graded)) | {
        'logged': len(rows), 'graded': len(graded),
        'pending': len(rows) - len(graded) - len(voided)}
    # COUNTED, NOT HIDDEN. A void dropped silently looks like a pick
    # that was never logged, which is the ambiguity that made this
    # failure take 11 days to find.
    out['voided'] = len(voided)
    return out


# BUILD STAMP -- printed to stderr on every run.
#
# Four separate round-trips today were spent reading output from a file
# that had never been replaced: a tails header without its P&L line, a
# minutes run without its self-check, a boards run still labelling the
# miss rate as opponent FG. Every time the numbers looked plausible,
# and every time the only way to catch it was for me to notice a
# missing line by eye. install.sh hashes files now, but that only
# helps if it is run, and a marker string shared by two versions
# catches nothing at all.
#
# So each script states what it is. stderr rather than stdout, so
# --json output stays machine-readable while a terminal paste always
# carries the build.
BUILD = '2026-11-16a'


def _stamp(name):
    import sys as _s
    print(f'[{name} build {BUILD}]', file=_s.stderr)

def cmd_report(a):
    rows = load()
    if not rows:
        print(f'nothing logged yet at {SLATE}')
        return 0
    s = summarise(rows)
    if a.json:
        s['picks'] = sorted(rows, key=lambda r: (r['day'], r['subject']))
        print(json.dumps(s, indent=1))
        return 0

    a_ = s['all_tiers']
    print(f"PUBLISHED (what the site claims, featured tiers only)")
    print(f"  logged {s['logged']}   graded {s['graded']}   "
          f"pending {s['pending']}")
    print(f"  record {s['record']}   {s['units']:+.2f}u", end='')
    if s['roi_pct'] is not None:
        print(f"   ROI {s['roi_pct']:+.1f}%   win {s['win_pct']}%")
    else:
        print()
    if a_['graded'] != s['graded']:
        print(f"\nYOUR BOOK (every tier, including the picks you did not "
              f"feature)")
        print(f"  logged {a_['logged']}   graded {a_['graded']}   "
              f"pending {a_['pending']}")
        print(f"  record {a_['record']}   {a_['units']:+.2f}u", end='')
        if a_['roi_pct'] is not None:
            print(f"   ROI {a_['roi_pct']:+.1f}%   win {a_['win_pct']}%")
        else:
            print()
    print()
    for tier, v in s['by_tier'].items():
        flag = '' if v['featured'] else '   not featured'
        print(f"  {tier:14}{v['record']:>10}{v['units']:+8.2f}u{flag}")

    if s['graded'] < 30:
        print(f"\n{s['to_go']} more graded picks before the record goes on")
        print("the site. Early win rate is noise in either direction --")
        print("at this count a 3-0 start and a 0-3 start say the same thing.")
    return 0


# ---------------------------------------------------------------------
# CLOSING LINE VALUE
#
# Win rate needs roughly 750 bets to separate a 53% edge from a coin
# flip. CLV needs about 30, because it is nearly deterministic: if the
# market keeps moving toward the side you took, you are finding
# information before it is priced, and that is true whether or not the
# particular bet won.
#
# WHAT IS COMPARED, AND WHY NOT THE PRICE YOU PAID
#   The obvious metric -- your price against the closing price -- does
#   not work here. Your logged odds are one book's number WITH vig; the
#   closing figure from props_lines is a de-vigged consensus across
#   seven or eight books. Comparing them would subtract the vig from
#   every bet and report a loss on a card that beat the market.
#
#   So both sides are de-vigged consensus: the first reading of that
#   market on the day you picked, against the last reading before tip.
#   That measures the thing that matters -- did the market move toward
#   my side after I took it -- independently of what any one book
#   charged you.
#
# WHEN THE LINE ITSELF MOVES
#   A probability at 22.5 and a probability at 23.5 are not the same
#   quantity, so those rows report the line movement instead and are
#   excluded from the probability average. Line movement toward your
#   side is good news on its own: taking over 22.5 and watching it
#   close at 23.5 means you got a number the market no longer offers.
CLV_ARCHIVE = os.path.join(DATA, 'clv_archive.csv')

# Two captures less than this far apart are the same reading taken
# twice, not an open and a close.
MIN_CLV_GAP_H = 3.0


def capture_gap(o, c):
    """Hours between two book readings, or None if unstamped."""
    a, b = o.get('captured'), c.get('captured')
    if not a or not b:
        return None
    try:
        ta = datetime.datetime.fromisoformat(a)
        tb = datetime.datetime.fromisoformat(b)
    except Exception:
        return None
    return abs((tb - ta).total_seconds()) / 3600.0


def book_series(day):
    """First and last de-vigged reading per player+market, in file order.

    props_lines appends, so file order is time order: the first row for
    a market is the earliest capture that day and the last is the most
    recent. No timestamp needed and none is recorded per row.
    """
    first, last, seen = {}, {}, collections.Counter()
    if not os.path.exists(PICKS_LOG):
        return first, last, seen
    for ln in open(PICKS_LOG):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('kind') != 'book_prop' or r.get('day') != day:
            continue
        if r.get('prob_over') is None:
            continue
        k = (norm_name(r.get('player')), norm_market(r.get('market')))
        first.setdefault(k, r)
        last[k] = r
        seen[k] += 1
    return first, last, seen


def implied(odds):
    """American price -> implied probability, vig included."""
    o = float(odds)
    return 100.0 / (o + 100.0) if o > 0 else (-o) / ((-o) + 100.0)


def clv_rows(day):
    first, last, seen = book_series(day)
    out = []
    for p in load():
        if p.get('day') != day or p.get('market') == 'ml':
            continue
        k = (norm_name(p['subject']), p['market'])
        o, c = first.get(k), last.get(k)
        if not o or not c:
            out.append(dict(p, clv=None, note='no book line captured'))
            continue
        gap = capture_gap(o, c)
        if seen[k] < 2 or (gap is not None and gap < MIN_CLV_GAP_H):
            why = ('captured once; no closing line' if seen[k] < 2
                   else f'readings only {gap:.1f}h apart; no close yet')
            # One reading is an open with no close, and two readings an
            # hour apart are the same thing: the morning run twice. Both
            # score zero, which would quietly pad the average with picks
            # nobody measured.
            out.append(dict(p, clv=None, open_prob=None, close_prob=None,
                            moved_line=False, note=why))
            continue
        side_open = (o['prob_over'] if p['side'] == 'over'
                     else 1 - o['prob_over'])
        side_close = (c['prob_over'] if p['side'] == 'over'
                      else 1 - c['prob_over'])
        moved_line = (c['line'] != o['line'])
        # for an over, the line drifting up means you hold a number the
        # market no longer offers; for an under, drifting down
        line_clv = ((c['line'] - o['line']) if p['side'] == 'over'
                    else (o['line'] - c['line']))
        out.append({
            'day': day, 'subject': p['subject'], 'market': p['market'],
            'side': p['side'], 'line': p['line'], 'odds': p['odds'],
            'tier': p.get('tier', 'core'),
            'open_line': o['line'], 'close_line': c['line'],
            'open_prob': round(side_open * 100, 2),
            'close_prob': round(side_close * 100, 2),
            'clv': None if moved_line else round((side_close - side_open) * 100, 2),
            'line_clv': round(line_clv, 1) if moved_line else 0.0,
            'moved_line': moved_line,
            'entry_implied': round(implied(p['odds']) * 100, 2),
            'result': p.get('result'), 'units': p.get('units'),
            'note': 'line moved; probabilities not comparable'
                    if moved_line else '',
        })
    return out


def cmd_clv(a):
    days = sorted({r['day'] for r in load()})
    if a.day:
        days = [a.day]
    if not days:
        print(f'nothing logged at {SLATE}')
        return 1

    allrows = []
    for d in days:
        allrows.extend(clv_rows(d))
    if a.json:
        print(json.dumps({'rows': allrows}, indent=1))
        return 0

    scored = [r for r in allrows if r.get('clv') is not None]
    if allrows:
        cols = ['day', 'subject', 'market', 'side', 'line', 'odds', 'tier',
                'open_line', 'close_line', 'open_prob', 'close_prob',
                'clv', 'line_clv', 'moved_line', 'entry_implied',
                'result', 'units', 'note']
        with open(CLV_ARCHIVE, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
            w.writeheader()
            for r in allrows:
                w.writerow(r)

    hdr = (f"{'DAY':9}{'PICK':30}{'OPEN':>7}{'CLOSE':>7}{'CLV':>8}"
           f"{'LINE':>14}")
    print(hdr)
    print('-' * len(hdr))
    for r in allrows:
        if r.get('clv') is None and not r.get('moved_line'):
            pick = f"{r['subject'][:18]} {r['side'][:1]}{r['line']}"
            n = r.get('note') or ''
            why = ('no line' if 'no book line' in n
                   else 'too close' if 'apart' in n else 'no close')
            print(f"{r['day']:9}{pick[:29]:30}"
                  f"{'-':>7}{'-':>7}{'-':>8}{why:>14}")
            continue
        pick = f"{r['subject'][:18]} {r['side'][:1]}{r['line']}"
        ln = (f"{r['open_line']}->{r['close_line']}"
              if r['moved_line'] else f"{r['line']}")
        clv = '-' if r['clv'] is None else f"{r['clv']:+.2f}"
        print(f"{r['day']:9}{pick[:29]:30}{r['open_prob']:>7.1f}"
              f"{r['close_prob']:>7.1f}{clv:>8}{ln:>14}")

    print()
    if scored:
        avg = sum(r['clv'] for r in scored) / len(scored)
        beat = sum(1 for r in scored if r['clv'] > 0)
        print(f"{len(scored)} picks with a comparable close   "
              f"average CLV {avg:+.2f} points   beat the close "
              f"{beat}/{len(scored)}")
        if len(scored) < 30:
            print(f"\n{30 - len(scored)} more before this means anything. "
                  f"CLV settles in about 30\nobservations where win rate "
                  f"needs closer to 750 -- but 30 is still 30.")
        elif avg > 0:
            print('\nPositive average CLV over this many picks is the '
                  'first real\nevidence the process finds information '
                  'before the market does.')
        else:
            print('\nNegative average CLV means the market moves away '
                  'from your\nside after you take it. That is the signal '
                  'to change the process,\nand it arrives long before '
                  'the win rate would say so.')
    moved = [r for r in allrows if r.get('moved_line')]
    if moved:
        good = sum(1 for r in moved if r['line_clv'] > 0)
        print(f"\n{len(moved)} picks had the line move: {good} toward "
              f"your side, {len(moved) - good} away.")
    if allrows:
        print(f'\n-> {CLV_ARCHIVE}')
    return 0


def cmd_csv(a):
    rows = load()
    if not rows:
        print(f'nothing logged yet at {SLATE}')
        return 0
    p = write_csv(rows)
    print(f'{len(rows)} rows -> {p}')
    return 0


# ---------------------------------------------------------------------
# COVERS SLATE
#
# Our own card is three picks a night, which means the question "does
# this process work" is years from an answer. Covers publishes twenty,
# on two surfaces that disagree with each other, and grading all of
# them answers a sharper question much sooner: does agreement between a
# writer and a projection model predict anything, or is a corroborated
# pick just a pick?
#
# That is the whole premise of the comparison -- not that Covers has an
# edge, but that watching where expert reads converge and diverge tells
# us something about reads in general, ours included. It is worth
# nothing at all unless the results are kept.
#
# Nothing here is a bet and none of it touches the published record.
# These are other people's picks, graded as a study.
COVERS_ARCHIVE = os.path.join(DATA, 'covers_archive.csv')
PICKS_LOG = os.path.join(DATA, 'external_picks.jsonl')


def _load_screener():
    """Import the screener so the status rule has one definition.

    Re-implementing CORROBORATED/CONFLICT/UNBACKED here would let the
    page and the grades drift apart, and then a disagreement between
    them would be a bug nobody could see.
    """
    import importlib.util
    path = os.path.join(DL, 'sharpz_screen2.py')
    if not os.path.exists(path):
        raise RuntimeError(f'need the screener at {path}')
    spec = importlib.util.spec_from_file_location('sharpz_screen2', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def settle(market, side, line, row):
    """(result, actual) for one over/under against a box score row."""
    mk = norm_market(market)
    if mk not in MARKETS:
        return None, None
    try:
        actual = MARKETS[mk](row)
    except KeyError:
        return None, None
    if actual == line:
        return 'push', actual
    if side == 'over':
        return ('win' if actual > line else 'loss'), actual
    return ('win' if actual < line else 'loss'), actual


def cmd_covers(a):
    sc = _load_screener()
    rows = [json.loads(l) for l in open(PICKS_LOG) if l.strip()]
    days = sorted({r.get('day') for r in rows if r.get('day')})
    day = a.day or (days[-1] if days else None)
    if not day:
        print(f'nothing in {PICKS_LOG}')
        return 1
    today = [r for r in rows if r.get('day') == day]

    # ungated: the EV and distance filters decide what goes on the
    # page, not what gets studied. A pick we declined to show still
    # tells us whether the surface that made it was right.
    kept, cut, nw, np_ = sc.screen(today, 0.0, 0.0)

    print(f'{day}: {nw} writer picks, {np_} projections')
    try:
        box = Roster(game_log(day, player=True))
    except Exception as e:
        print(f'box score fetch failed: {e}', file=sys.stderr)
        return 1
    if not box:
        print(f'no box scores for {day} yet', file=sys.stderr)
        return 1

    out = []
    seen_proj = set()
    for tag, w, pr, _why in kept:
        brow, _p = box.find(w.get('player'))
        if not brow:
            continue
        res, act = settle(w.get('market'), w.get('side'), w.get('line'), brow)
        if res:
            out.append({'day': day, 'surface': 'writer', 'status': tag,
                        'player': w.get('player'), 'market': w.get('market'),
                        'side': w.get('side'), 'line': w.get('line'),
                        'actual': act, 'result': res,
                        'ev_pct': (pr or {}).get('ev_pct'),
                        'projection': (pr or {}).get('projection')})
        if pr:
            k = (norm_name(pr.get('player')), norm_market(pr.get('market')),
                 pr.get('side'), pr.get('line'))
            if k not in seen_proj:
                seen_proj.add(k)
                res2, act2 = settle(pr.get('market'), pr.get('side'),
                                    pr.get('line'), brow)
                if res2:
                    out.append({'day': day, 'surface': 'projection',
                                'status': tag, 'player': pr.get('player'),
                                'market': pr.get('market'),
                                'side': pr.get('side'), 'line': pr.get('line'),
                                'actual': act2, 'result': res2,
                                'ev_pct': pr.get('ev_pct'),
                                'projection': pr.get('projection')})

    # projections nobody wrote up: the model's own record, unaided
    for r in today:
        if r.get('kind') != 'prop_projection':
            continue
        k = (norm_name(r.get('player')), norm_market(r.get('market')),
             r.get('side'), r.get('line'))
        if k in seen_proj:
            continue
        seen_proj.add(k)
        brow, _p = box.find(r.get('player'))
        if not brow:
            continue
        res, act = settle(r.get('market'), r.get('side'), r.get('line'), brow)
        if res:
            out.append({'day': day, 'surface': 'projection',
                        'status': 'NO_WRITER', 'player': r.get('player'),
                        'market': r.get('market'), 'side': r.get('side'),
                        'line': r.get('line'), 'actual': act, 'result': res,
                        'ev_pct': r.get('ev_pct'),
                        'projection': r.get('projection')})

    if not out:
        print('nothing gradeable (no players matched the box score)')
        return 1

    # merge into the archive, replacing this day
    prior = []
    if os.path.exists(COVERS_ARCHIVE):
        with open(COVERS_ARCHIVE) as f:
            prior = [r for r in csv.DictReader(f) if r.get('day') != day]
    cols = ['day', 'surface', 'status', 'player', 'market', 'side', 'line',
            'actual', 'result', 'ev_pct', 'projection']
    with open(COVERS_ARCHIVE, 'w', newline='') as f:
        wtr = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        wtr.writeheader()
        for r in prior + out:
            wtr.writerow(r)

    allrows = prior + out
    report_covers(out, 'tonight')
    if prior:
        print()
        report_covers(allrows, 'all days')
    print(f'\n{len(out)} rows for {day} -> {COVERS_ARCHIVE} '
          f'({len(allrows)} total)')
    return 0


def _tally(sel):
    w = sum(1 for r in sel if r['result'] == 'win')
    l = sum(1 for r in sel if r['result'] == 'loss')
    p = sum(1 for r in sel if r['result'] == 'push')
    n = w + l
    return {'n': len(sel), 'w': w, 'l': l, 'p': p,
            'hit': round(w / n * 100, 1) if n else None}


def covers_summary(rows):
    """The cross-tab as data, for the site.

    Reported from the archive rather than from a single run, so the
    page shows every night graded so far and not just the last one.
    """
    out = {'generated': datetime.datetime.now().astimezone().isoformat(),
           'days': sorted({r['day'] for r in rows}),
           'settled': sum(1 for r in rows
                          if r['result'] in ('win', 'loss')),
           'unlocks_at': 100, 'surfaces': [], 'statuses': []}
    for s in ('writer', 'projection'):
        sel = [r for r in rows if r['surface'] == s]
        if sel:
            out['surfaces'].append(dict(_tally(sel), name=s))
    for st in ('CORROBORATED', 'CONFLICT', 'UNBACKED', 'NO_WRITER'):
        sel = [r for r in rows if r['status'] == st]
        if sel:
            out['statuses'].append(dict(_tally(sel), name=st))
    conf = [r for r in rows if r['status'] == 'CONFLICT']
    if conf:
        out['conflicts'] = {
            s: _tally([r for r in conf if r['surface'] == s])
            for s in ('writer', 'projection')
            if any(r['surface'] == s for r in conf)}
    out['to_go'] = max(0, 100 - out['settled'])
    return out


def cmd_covers_report(a):
    if not os.path.exists(COVERS_ARCHIVE):
        print(json.dumps(covers_summary([])) if a.json
              else f'nothing graded yet at {COVERS_ARCHIVE}')
        return 0
    with open(COVERS_ARCHIVE) as f:
        rows = list(csv.DictReader(f))
    if a.json:
        print(json.dumps(covers_summary(rows), indent=1))
        return 0
    report_covers(rows, 'all days')
    return 0


def report_covers(rows, label):
    def tally(sel):
        w = sum(1 for r in sel if r['result'] == 'win')
        l = sum(1 for r in sel if r['result'] == 'loss')
        p = sum(1 for r in sel if r['result'] == 'push')
        n = w + l
        return w, l, p, (w / n * 100 if n else None)

    print(f'\n{label}')
    print(f"  {'SURFACE':14}{'N':>4}{'W-L-P':>10}{'HIT':>8}")
    print('  ' + '-' * 34)
    for s in ('writer', 'projection'):
        sel = [r for r in rows if r['surface'] == s]
        if not sel:
            continue
        w, l, p, hit = tally(sel)
        print(f'  {s:14}{len(sel):>4}{f"{w}-{l}-{p}":>10}'
              + (f'{hit:>7.1f}%' if hit is not None else f'{"-":>8}'))

    print(f"\n  {'STATUS':14}{'N':>4}{'W-L-P':>10}{'HIT':>8}")
    print('  ' + '-' * 34)
    for st in ('CORROBORATED', 'CONFLICT', 'UNBACKED', 'NO_WRITER'):
        sel = [r for r in rows if r['status'] == st]
        if not sel:
            continue
        w, l, p, hit = tally(sel)
        print(f'  {st:14}{len(sel):>4}{f"{w}-{l}-{p}":>10}'
              + (f'{hit:>7.1f}%' if hit is not None else f'{"-":>8}'))

    # CONFLICT is the one that pays for the exercise: writer and model
    # on opposite sides of the same number, so one of them is wrong and
    # over time we find out which surface to believe.
    conf = [r for r in rows if r['status'] == 'CONFLICT']
    if conf:
        cw = [r for r in conf if r['surface'] == 'writer']
        cp = [r for r in conf if r['surface'] == 'projection']
        if cw and cp:
            w1, l1, _, h1 = tally(cw)
            w2, l2, _, h2 = tally(cp)
            print(f'\n  on conflicts: writers {w1}-{l1}'
                  + (f' ({h1:.0f}%)' if h1 is not None else '')
                  + f', model {w2}-{l2}'
                  + (f' ({h2:.0f}%)' if h2 is not None else ''))

    n = sum(1 for r in rows if r['result'] in ('win', 'loss'))
    if n < 100:
        print(f'\n  {n} settled. Treat every split above as noise until '
              f'this passes 100.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    sub = ap.add_subparsers(dest='cmd', required=True)

    p = sub.add_parser('add', help='log published picks')
    p.add_argument('--day', required=True, help='YYYYMMDD')
    p.add_argument('--pick', action='append',
                   help='"subject|market|side|line|odds[|tier]"')
    p.add_argument('--from-file', help='one pick per line, # for comments')
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser('grade', help='settle against the box score')
    p.add_argument('--day', help='YYYYMMDD, default every ungraded day')
    p.set_defaults(fn=cmd_grade)

    p = sub.add_parser('report', help='the record so far')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser('csv', help='write the archive')
    p.set_defaults(fn=cmd_csv)

    p = sub.add_parser('audit', help='re-settle graded picks and report '
                                     'any that disagree with the box score')
    p.add_argument('--day', help='one day, default every graded day')
    p.add_argument('--fix', action='store_true',
                   help='correct mismatches, keeping the old values')
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser('retier', help='move picks between tiers '
                                      '(log = keep but do not feature)')
    p.add_argument('--day', required=True, help='YYYYMMDD')
    p.add_argument('--tier', default='log',
                   help="target tier, default 'log'")
    p.add_argument('--subject', help='only picks whose name contains this')
    p.add_argument('--apply', action='store_true', help='write the change')
    p.set_defaults(fn=cmd_retier)

    p = sub.add_parser('grades',
                       help='record split by card grade -- is the order real?')
    p.add_argument('--day')
    p.add_argument('--since', help='YYYYMMDD')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_grades)

    p = sub.add_parser('tails', help='are the wins bigger than the losses?')
    p.add_argument('--day', help='one day, default all graded')
    p.add_argument('--market', help='one market only')
    p.add_argument('--tier', help='one tier only, e.g. board')
    p.add_argument('--since', help='YYYYMMDD, first day to include')
    p.add_argument('--until', help='YYYYMMDD, last day to include')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_tails)

    p = sub.add_parser('clv', help='closing line value on logged picks')
    p.add_argument('--day', help='YYYYMMDD, default every logged day')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_clv)

    p = sub.add_parser('covers-report',
                       help='report the Covers study from the archive')
    p.add_argument('--json', action='store_true')
    p.set_defaults(fn=cmd_covers_report)

    p = sub.add_parser('covers',
                       help="grade Covers' whole slate as a study")
    p.add_argument('--day', help='YYYYMMDD, default the latest logged')
    p.set_defaults(fn=cmd_covers)

    a = ap.parse_args()
    _stamp('slate.py')
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
