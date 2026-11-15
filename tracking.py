#!/usr/bin/env python3
"""
TRACKING -- NBA player tracking data, joined to tonight's prop lines.

WHAT THIS ANSWERS
  Not "will he go over". It answers: what does this line require of
  him, and is that his normal role?

  A points line is a claim about volume and efficiency together. Split
  them and the claim becomes checkable. A player scores roughly
  TOUCHES x PTS_PER_TOUCH, so a line L implies he needs L /
  PTS_PER_TOUCH touches tonight. Set that against the touches he
  actually gets and the line turns into a sentence: "this asks him for
  eleven more touches than he has ever averaged", or "this is his
  ordinary night".

  Multiplying touches by points-per-touch to PROJECT points would be
  circular -- it recovers his scoring average and nothing more. The
  division is the useful direction, because it isolates the part of
  the line that film can argue with: is the role changing tonight?

WHAT IT IS NOT
  Not a projection system and not an edge. Tracking rates describe
  what a player has done in a settled role. They know nothing about
  tonight's opponent, rest, or who is out -- all of which the book
  line already contains. Treat a large role gap as a question to take
  to the film, not a bet.

THE SEASON PROBLEM
  Early in a season the current year's tracking data is close to
  worthless: touches per game over two games has an enormous standard
  error. So the default is last season, which describes a settled
  role, and --blend adds the current season weighted by games played
  using the same shrinkage we use everywhere else:

      w = gp / (gp + K)

  with K set so that a player needs roughly K games before his new
  season outweighs his old one. Until then the prior carries it.

usage
  python3 tracking.py --refresh                 # pull and cache
  python3 tracking.py                           # rank tonight's props
  python3 tracking.py --blend 2026-27           # mix in this season
  python3 tracking.py --season 2025-26 --refresh
"""
import os, sys, re, json, time, argparse, subprocess

DATA = os.path.expanduser('~/kalshi_data')
PICKS = os.path.join(DATA, 'external_picks.jsonl')
BASE = 'https://stats.nba.com/stats/leaguedashptstats'

# stats.nba.com does not reject a thin header set -- it accepts the
# connection and then never answers, which looks like a network fault
# and is not one. Accept-Encoding is the one that matters: without it
# the request hangs until the client gives up. Verified by hand on
# 20 Oct 2026, where the identical request went from a 18s timeout to
# a 200 in 3.2s on adding the encoding and Sec-Fetch headers.
HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                   'AppleWebKit/537.36 (KHTML, like Gecko) '
                   'Chrome/120.0.0.0 Safari/537.36'),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept-Encoding': 'gzip, deflate',
    'Referer': 'https://www.nba.com/',
    'Origin': 'https://www.nba.com',
    'x-nba-stats-origin': 'stats',
    'x-nba-stats-token': 'true',
    'Connection': 'keep-alive',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-site',
}

# measure type -> the columns we keep from it
MEASURES = {
    'Possessions': ['TOUCHES', 'FRONT_CT_TOUCHES', 'TIME_OF_POSS',
                    'AVG_SEC_PER_TOUCH', 'AVG_DRIB_PER_TOUCH',
                    'PTS_PER_TOUCH', 'ELBOW_TOUCHES', 'POST_TOUCHES',
                    'PAINT_TOUCHES'],
    'Drives':      ['DRIVES', 'DRIVE_FGA', 'DRIVE_FG_PCT', 'DRIVE_FTA',
                    'DRIVE_PTS', 'DRIVE_PASSES', 'DRIVE_AST'],
    'Rebounding':  ['REB_CHANCES', 'REB_CHANCE_PCT', 'CONTESTED_REB',
                    'UNCONTESTED_REB', 'AVG_REB_DIST'],
    'Passing':     ['PASSES_MADE', 'AST', 'POTENTIAL_AST',
                    'AST_PTS_CREATED', 'AST_ADJ', 'AST_TO_PASS_PCT'],
}

# Shrinkage constant for blending this season into last. A player needs
# roughly this many games before the new season outweighs the old one.
# Touches settle fast relative to shooting, so this is lower than the
# K we use for team ratings.
BLEND_K = 12.0


def url(measure, season):
    return (f'{BASE}?College=&Conference=&Country=&DateFrom=&DateTo='
            f'&Division=&DraftPick=&DraftYear=&GameScope=&Height='
            f'&LastNGames=0&LeagueID=00&Location=&Month=0'
            f'&OpponentTeamID=0&Outcome=&PORound=0&PerMode=PerGame'
            f'&PlayerExperience=&PlayerOrTeam=Player&PlayerPosition='
            f'&PtMeasureType={measure}&Season={season}&SeasonSegment='
            f'&SeasonType=Regular+Season&StarterBench=&TeamID=0'
            f'&VsConference=&VsDivision=&Weight=')


def fetch(measure, season, tries=3, timeout=25):
    """One measure type, fetched by shelling out to curl.

    WHY NOT urllib
      The same URL with the same headers returns in about three
      seconds under curl and times out every time under urllib. The
      headers are not the difference. stats.nba.com sits behind Akamai,
      which fingerprints the TLS handshake itself, and Python's
      handshake does not look like a browser's whatever headers are
      bolted on top. Curl's does.

      So this shells out. It is not elegant, and it is the difference
      between data and no data. curl ships with macOS, handles the
      gzip we ask for via --compressed, and has already been verified
      against this exact endpoint by hand.
    """
    cmd = ['curl', '-s', '--compressed', '-m', str(timeout)]
    for k, v in HEADERS.items():
        if k == 'Accept-Encoding':
            continue          # --compressed sets this correctly
        cmd += ['-H', f'{k}: {v}']
    cmd.append(url(measure, season))

    last = None
    for attempt in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout + 10)
            if r.returncode != 0:
                raise RuntimeError(f'curl exit {r.returncode}: '
                                   f'{r.stderr.decode()[:200]}')
            if not r.stdout.strip():
                raise RuntimeError('empty response (stalled or blocked)')
            payload = json.loads(r.stdout.decode('utf-8'))
            rs = payload['resultSets'][0]
            cols = rs['headers']
            return [dict(zip(cols, row)) for row in rs['rowSet']]
        except FileNotFoundError:
            raise RuntimeError('curl is not installed') from None
        except Exception as e:
            last = e
            if attempt + 1 < tries:
                print(f'    {measure}: {e}, retrying', flush=True)
                time.sleep(2 + attempt * 3)
    raise RuntimeError(f'{measure} {season}: {last}')


def cache_path(season):
    return os.path.join(DATA, f'nba_tracking_{season.replace("-", "")}.json')


def load_season(season, refresh=False, gap=20.0):
    """Fetch the measures we do not already have, one at a time.

    RESUMABLE ON PURPOSE
      stats.nba.com throttles hard and without warning -- a dozen calls
      in twenty minutes is enough to get stonewalled for a while. The
      first version fetched all four measures and wrote the cache only
      at the end, so a failure on the third threw away the two that had
      succeeded and the next attempt started from nothing. That turns a
      flaky endpoint into an unusable one.

      Now each measure is written as it lands and recorded in
      `_done`. A run that dies halfway costs you the measures it did
      not reach and nothing else; run it again and it picks up where it
      stopped. Four shaky windows are as good as one clean one.
    """
    p = cache_path(season)
    blob = {'players': {}, '_done': []}
    if os.path.exists(p) and not refresh:
        try:
            loaded = json.load(open(p))
            # tolerate the older flat format
            blob = (loaded if isinstance(loaded.get('players'), dict)
                    else {'players': loaded, '_done': list(MEASURES)})
        except Exception:
            pass

    todo = [m for m in MEASURES if m not in blob['_done']]
    if not todo:
        return blob['players']

    if blob['_done']:
        print(f'  have {", ".join(blob["_done"])}; '
              f'need {", ".join(todo)}')
    os.makedirs(DATA, exist_ok=True)

    for i, measure in enumerate(todo):
        print(f'  fetching {measure} {season} ...', flush=True)
        try:
            rows = fetch(measure, season)
        except Exception as e:
            json.dump(blob, open(p, 'w'), indent=1)
            got = ', '.join(blob['_done']) or 'nothing'
            raise RuntimeError(
                f'{e}\n  kept: {got}\n'
                f'  rerun the same command later to resume') from None
        keep = MEASURES[measure]
        for row in rows:
            name = row.get('PLAYER_NAME')
            if not name:
                continue
            d = blob['players'].setdefault(name, {
                'player': name, 'team': row.get('TEAM_ABBREVIATION'),
                'gp': row.get('GP'), 'min': row.get('MIN')})
            for k in keep:
                if k in row:
                    d[k] = row[k]
        blob['_done'].append(measure)
        json.dump(blob, open(p, 'w'), indent=1)
        print(f'    got {len(rows)} players, cached', flush=True)
        if i + 1 < len(todo):
            print(f'    waiting {gap:.0f}s before the next one', flush=True)
            time.sleep(gap)

    print(f'  {len(blob["players"])} players -> {p}')
    return blob['players']


def blend(prior, cur, k=BLEND_K):
    """Weight this season by games played, fall back to last season."""
    out = {}
    for name, p in prior.items():
        c = cur.get(name)
        if not c:
            out[name] = dict(p, _w=0.0, _src='prior')
            continue
        gp = c.get('gp') or 0
        w = gp / (gp + k) if gp else 0.0
        d = dict(p)
        for key in set(p) | set(c):
            a, b = p.get(key), c.get(key)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                d[key] = (1 - w) * a + w * b
        d['_w'] = round(w, 3)
        d['_src'] = 'blend'
        d['team'] = c.get('team') or p.get('team')
        out[name] = d
    for name, c in cur.items():          # rookies, no prior
        if name not in out:
            out[name] = dict(c, _w=1.0, _src='current-only')
    return out


def short(name):
    n = re.sub(r"[^A-Za-z'\- ]", ' ', name or '').strip()
    parts = [p for p in n.split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0].lower()
    return f'{parts[0][0].lower()}. {parts[-1].lower()}'


def norm_market(m):
    return re.sub(r'[^a-z]', '', (m or '').lower())


MARKET_KIND = {
    'pointsscored': 'pts', 'points': 'pts',
    'totalrebounds': 'reb', 'rebounds': 'reb',
    'totalassists': 'ast', 'assists': 'ast',
    'pra': 'pra',
}


def tonights_props(day=None):
    """Book lines first; a Covers writer line is the fallback."""
    if not os.path.exists(PICKS):
        return []
    rows = []
    for ln in open(PICKS):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if day and r.get('day') != day:
            continue
        rows.append(r)
    days = sorted({r.get('day') for r in rows if r.get('day')})
    if not day and days:
        rows = [r for r in rows if r.get('day') == days[-1]]

    best = {}
    for r in rows:
        kind = MARKET_KIND.get(norm_market(r.get('market')))
        if not kind or r.get('line') is None:
            continue
        key = (short(r.get('player')), kind)
        rank = {'book_prop': 0, 'prop_projection': 1, 'prop': 2}.get(
            r.get('kind'), 9)
        if rank == 9:
            continue
        if key not in best or rank < best[key][0]:
            best[key] = (rank, r, kind)
    return [(r, kind) for _rank, r, kind in best.values()]


def role_gap(stat, kind, line):
    """What the line asks for, against what he normally gets.

    Returns (asked, normal, label) or None when we cannot form the
    comparison honestly for that market.
    """
    if kind == 'pts':
        ppt = stat.get('PTS_PER_TOUCH')
        touches = stat.get('TOUCHES')
        if not ppt or not touches:
            return None
        return (line / ppt, touches, 'touches')
    if kind == 'reb':
        ch = stat.get('REB_CHANCES')
        pct = stat.get('REB_CHANCE_PCT')
        if not ch or not pct:
            return None
        return (line / pct, ch, 'reb chances')
    if kind == 'ast':
        pot = stat.get('POTENTIAL_AST')
        ast = stat.get('AST')
        if not pot or not ast:
            return None
        # Assists are the one leg a player only half controls: he
        # creates the shot, a team-mate has to make it. POTENTIAL_AST
        # is the part that is his. Dividing the line by his team-mates'
        # finishing rate gives the passes the line needs him to create.
        rate = ast / pot
        if not rate:
            return None
        return (line / rate, pot, 'potential ast')
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2025-26',
                    help='base season (default 2025-26)')
    ap.add_argument('--blend', help='current season to mix in, e.g. 2026-27')
    ap.add_argument('--refresh', action='store_true', help='re-fetch')
    ap.add_argument('--gap', type=float, default=20.0,
                    help='seconds between endpoints (default 20; raise '
                         'it if you get throttled)')
    ap.add_argument('--day', help='YYYYMMDD')
    ap.add_argument('--min-gap', type=float, default=0.0,
                    help='only show role gaps of at least this %%')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--columns', action='store_true',
                    help='print the real column names each endpoint '
                         'returns, and which of ours are missing')
    a = ap.parse_args()

    # The column names below were written from memory of this API, not
    # from a verified response. A wrong name fails silently -- the
    # market is skipped and nothing says why -- so this prints what the
    # endpoint actually returns and flags any name we expect and do not
    # get. Run it first; it is the cheapest way to find out I was wrong.
    if a.columns:
        bad = 0
        for measure, keep in MEASURES.items():
            print(f'checking {measure} ...', flush=True)
            try:
                rows = fetch(measure, a.season)
            except Exception as e:
                print(f'{measure}: FETCH FAILED - {e}')
                bad += 1
                continue
            have = set(rows[0]) if rows else set()
            missing = [k for k in keep if k not in have]
            print(f'\n{measure}  ({len(rows)} players)')
            print('  returns: ' + ', '.join(sorted(have)))
            if missing:
                print('  MISSING (we ask for these): ' + ', '.join(missing))
                bad += 1
            else:
                print('  all expected columns present')
            time.sleep(1.2)
        print('\nSend me any MISSING lines and I will correct the names.'
              if bad else '\nEvery column name checks out.')
        return 0 if not bad else 1

    print(f'tracking: base {a.season}'
          + (f', blending {a.blend}' if a.blend else ''))
    try:
        stats = load_season(a.season, a.refresh, a.gap)
        if a.blend:
            cur = load_season(a.blend, a.refresh, a.gap)
            stats = blend(stats, cur)
    except Exception as e:
        print(f'fetch failed: {e}', file=sys.stderr)
        print('stats.nba.com throttles hard; wait a minute and retry.',
              file=sys.stderr)
        return 1

    by_short = {short(k): v for k, v in stats.items()}
    props = tonights_props(a.day)
    if not props:
        print(f'no props in {PICKS}')
        return 0

    out = []
    for r, kind in props:
        st = by_short.get(short(r.get('player')))
        if not st:
            continue
        rg = role_gap(st, kind, r['line'])
        if not rg:
            continue
        asked, normal, label = rg
        if not normal:
            continue
        gap_pct = (asked - normal) / normal * 100.0
        out.append({
            'player': r['player'], 'team': st.get('team'),
            'market': kind.upper(), 'line': r['line'],
            'source': r.get('kind'),
            'asked': round(asked, 1), 'normal': round(normal, 1),
            'unit': label, 'gap_pct': round(gap_pct, 1),
            'touches': st.get('TOUCHES'),
            'sec_per_touch': st.get('AVG_SEC_PER_TOUCH'),
            'drives': st.get('DRIVES'),
            'drive_fga': st.get('DRIVE_FGA'),
            'drive_fta': st.get('DRIVE_FTA'),
            'gp': st.get('gp'), 'w_current': st.get('_w'),
        })

    out = [o for o in out if abs(o['gap_pct']) >= a.min_gap]
    out.sort(key=lambda o: -abs(o['gap_pct']))

    if a.json:
        print(json.dumps({'season': a.season, 'blend': a.blend,
                          'rows': out}, indent=1))
        return 0

    print(f'\n{len(out)} props with tracking data, ranked by role gap\n')
    hdr = (f"{'PLAYER':24}{'TM':4}{'MKT':5}{'LINE':>6}"
           f"{'ASKS':>8}{'USUAL':>8}{'GAP':>8}  "
           f"{'TOUCH':>6}{'S/TCH':>6}{'DRV':>6}{'DRVFGA':>7}")
    print(hdr)
    print('-' * len(hdr))
    for o in out:
        print(f"{o['player'][:23]:24}{(o['team'] or '?'):4}{o['market']:5}"
              f"{o['line']:>6}{o['asked']:>8.1f}{o['normal']:>8.1f}"
              f"{o['gap_pct']:>7.1f}%  "
              f"{(o['touches'] or 0):>6.1f}{(o['sec_per_touch'] or 0):>6.2f}"
              f"{(o['drives'] or 0):>6.1f}{(o['drive_fga'] or 0):>7.1f}")

    print('\nASKS  = line divided by his rate, i.e. the volume this line needs')
    print('USUAL = what he actually averages of that volume')
    print('GAP   = how much more (or less) the line is asking for')
    print('\nA large positive gap means the line requires a bigger role than')
    print('he normally has. That is a question for the film, not a bet: the')
    print('book already knows tonight\'s injuries and rotation, and these')
    print('rates do not.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
