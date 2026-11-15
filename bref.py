#!/usr/bin/env python3
"""
BREF -- role-gap screening from Basketball-Reference per-36 rates.

WHY THIS EXISTS
  tracking.py asks the right question -- what does this line require of
  him, and is that his normal role -- but it needs NBA tracking data,
  and Akamai has blocked this machine from every nba.com host. A tool
  that only works when one hostile server cooperates is not a tool.

  Basketball-Reference has no touches, no time of possession, no
  seconds per touch. Those are NBA tracking exclusives and there is no
  substitute for them. What it does have is per-36 production and
  minutes, which supports the same question in a different unit:

      required minutes = line / (per-36 rate) * 36

  Instead of "this line asks him for eleven more touches than he has
  ever averaged", it reads "this line asks him for forty-one minutes
  and he plays thirty-one". Arguably the better version, because
  minutes appear in every box score and touches do not, so the claim
  is checkable the next morning.

WHAT IT IS NOT
  Not a projection. A per-36 rate describes a settled role and knows
  nothing about tonight's opponent, foul trouble or blowout risk. A
  large positive gap is a question for the film, not a bet.

NOTE ON THE SOURCE
  Basketball-Reference buries several tables inside HTML comments, so
  the comment markers are stripped before parsing -- a parser that
  skips them silently reports an empty season. The site also rate
  limits at roughly twenty requests a minute, so this fetches two
  pages per season and caches the result.

usage
  python3 bref.py --refresh
  python3 bref.py
  python3 bref.py --season 2026 --min-gap 10
  python3 bref.py --columns          # check the page structure
"""
import os, re, sys, json, time, html, argparse, subprocess, unicodedata, collections

DATA = os.path.expanduser('~/kalshi_data')
PICKS = os.path.join(DATA, 'external_picks.jsonl')
BASE = 'https://www.basketball-reference.com/leagues'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/120.0.0.0 Safari/537.36')

# page -> (table id, the data-stat columns we keep)
PAGES = {
    'per_minute': ('per_minute_stats', [
        'name_display', 'team_name_abbr', 'pos', 'games', 'games_started',
        'mp', 'fg_per_minute_36', 'fga_per_minute_36', 'fg_pct',
        'fg3_per_minute_36', 'fg3a_per_minute_36', 'fg3_pct',
        'ft_per_minute_36', 'fta_per_minute_36', 'orb_per_minute_36',
        'trb_per_minute_36', 'ast_per_minute_36', 'tov_per_minute_36',
        'pts_per_minute_36']),
    'advanced': ('advanced', [
        'name_display', 'team_name_abbr', 'games', 'mp', 'usg_pct',
        'ts_pct', 'per', 'ws', 'bpm']),
}

# our market key -> the per-36 columns that sum to it
MARKET_36 = {
    'points':   ['pts_per_minute_36'],
    'rebounds': ['trb_per_minute_36'],
    'assists':  ['ast_per_minute_36'],
    'threes':   ['fg3_per_minute_36'],
    'pra':      ['pts_per_minute_36', 'trb_per_minute_36',
                 'ast_per_minute_36'],
    'pr':       ['pts_per_minute_36', 'trb_per_minute_36'],
    'pa':       ['pts_per_minute_36', 'ast_per_minute_36'],
    'ra':       ['trb_per_minute_36', 'ast_per_minute_36'],
}
ALIAS = {
    'pointsscored': 'points', 'totalrebounds': 'rebounds',
    'totalassists': 'assists', '3pointersmade': 'threes',
    'pts': 'points', 'reb': 'rebounds', 'ast': 'assists',
}

# B-Ref codes that differ from the book feed's codes
TEAM_CANON = {'BRK': 'BKN', 'CHO': 'CHA', 'PHO': 'PHX', 'NYK': 'NY',
              'NY': 'NY', 'SAS': 'SA', 'SA': 'SA', 'GSW': 'GS',
              'GS': 'GS', 'NOP': 'NO', 'NO': 'NO', 'WAS': 'WSH',
              'WSH': 'WSH', 'UTA': 'UTA', 'PHX': 'PHX'}


def canon(t):
    t = (t or '').strip().upper()
    return TEAM_CANON.get(t, t)


def fetch(page, season, timeout=30):
    url = f'{BASE}/NBA_{season}_{page}.html'
    r = subprocess.run(
        ['curl', '-s', '--compressed', '-m', str(timeout), '-H',
         f'User-Agent: {UA}', url],
        capture_output=True, timeout=timeout + 10)
    if r.returncode != 0:
        raise RuntimeError(f'curl exit {r.returncode} for {url}')
    body = r.stdout.decode('utf-8', 'replace')
    if len(body) < 50000:
        raise RuntimeError(f'{page}: only {len(body)} bytes - blocked '
                           f'or rate limited')
    return body


def parse(body, table_id, keep):
    """Rows from one B-Ref table, keyed by data-stat.

    Comment markers are stripped first: B-Ref hides tables inside
    <!-- --> and a parser that respects the comments finds nothing and
    reports an empty season rather than an error.
    """
    body = body.replace('<!--', '').replace('-->', '')
    m = re.search(r'<table[^>]*id="%s"[^>]*>(.*?)</table>' % re.escape(table_id),
                  body, re.S)
    if not m:
        raise RuntimeError(f'table "{table_id}" not found')
    rows = []
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S):
        if 'data-stat="ranker"' not in tr and 'name_display' not in tr:
            continue
        cells = {}
        for stat, inner in re.findall(
                r'data-stat="([^"]+)"[^>]*>(.*?)</t[dh]>', tr, re.S):
            if stat not in keep:
                continue
            txt = html.unescape(re.sub(r'<[^>]+>', '', inner)).strip()
            cells[stat] = txt
        if cells.get('name_display'):
            rows.append(cells)
    return rows


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def cache_path(season):
    return os.path.join(DATA, f'bref_{season}.json')


def load_season(season, refresh=False):
    p = cache_path(season)
    if os.path.exists(p) and not refresh:
        return json.load(open(p))

    players = {}
    for i, (page, (tid, keep)) in enumerate(PAGES.items()):
        print(f'  fetching {page} {season} ...', flush=True)
        try:
            rows = parse(fetch(page, season), tid, keep)
        except Exception as e:
            if page == 'advanced':
                # usage rate is a bonus layer, not the spine
                print(f'    ! {e} - continuing without usage rate',
                      file=sys.stderr)
                continue
            raise
        for r in rows:
            name = r['name_display']
            team = canon(r.get('team_name_abbr'))
            d = players.setdefault(name, {'player': name, 'teams': {}})
            slot = d['teams'].setdefault(team, {'team': team})
            for k, v in r.items():
                if k in ('name_display', 'team_name_abbr'):
                    continue
                slot[k] = num(v) if k not in ('pos',) else v
        print(f'    {len(rows)} rows', flush=True)
        if i + 1 < len(PAGES):
            time.sleep(4)          # B-Ref rate limits hard

    os.makedirs(DATA, exist_ok=True)
    json.dump(players, open(p, 'w'), indent=1)
    print(f'  {len(players)} players -> {p}')
    return players


def pick_team_row(entry, game_teams):
    """The split that matches tonight's team, else the season total.

    A traded player has one row per team plus a combined "2TM" row.
    For "what is his role now" the current team's split is the honest
    one -- McCain's Philadelphia half says nothing about his Oklahoma
    City minutes. The book feed gives the two teams in the game, so
    whichever of his rows matches one of them is the right one.
    """
    teams = entry.get('teams') or {}
    for t in game_teams:
        if t in teams:
            return teams[t]
    combined = [v for k, v in teams.items() if k.endswith('TM')]
    if combined:
        return max(combined, key=lambda r: r.get('mp') or 0)
    return max(teams.values(), key=lambda r: r.get('mp') or 0) if teams else None


def norm_name(n):
    """Full name, flattened for matching.

    The primary key, because both sources carry full names and the
    initial-plus-surname form collides: short() maps Donovan Mitchell
    and Davion Mitchell to the same string, and the second silently
    overwrote the first -- which produced a 172% role gap for a player
    whose real rate clears the line comfortably. A key that can collide
    is not a key.
    """
    n = unicodedata.normalize('NFKD', n or '')
    n = ''.join(c for c in n if not unicodedata.combining(c))
    n = re.sub(r"[^a-z ]", ' ', n.lower())
    parts = [p for p in n.split()
             if p not in ('jr', 'sr', 'ii', 'iii', 'iv', 'v')]
    return ' '.join(parts)


def short(name):
    parts = [p for p in ''.join(
        c if (c.isalpha() or c in " '-.") else ' '
        for c in (name or '')).split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0].lower()
    return f'{parts[0][0].lower()}. {parts[-1].lower()}'


def norm_market(m):
    k = re.sub(r'[^a-z0-9]', '', (m or '').lower())
    return ALIAS.get(k, k)


def tonights_lines(day=None):
    if not os.path.exists(PICKS):
        return [], None
    rows = []
    for ln in open(PICKS):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('kind') == 'book_prop':
            rows.append(r)
    days = sorted({r.get('day') for r in rows if r.get('day')})
    day = day or (days[-1] if days else None)
    rows = [r for r in rows if r.get('day') == day]
    # later rows win: props_lines may be run more than once a day and
    # the newest line is the live one
    best = {}
    for r in rows:
        best[(short(r.get('player')), norm_market(r.get('market')))] = r
    return list(best.values()), day


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--season', default='2026',
                    help='B-Ref season end year (2026 = 2025-26)')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--day', help='YYYYMMDD, default the latest logged')
    ap.add_argument('--min-gap', type=float, default=0.0,
                    help='only show gaps of at least this %%')
    ap.add_argument('--columns', action='store_true',
                    help='check the page structure and exit')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if a.columns:
        for page, (tid, keep) in PAGES.items():
            try:
                body = fetch(page, a.season)
                body2 = body.replace('<!--', '').replace('-->', '')
                ids = re.findall(r'<table[^>]*id="([^"]+)"', body2)
                print(f'\n{page}: {len(body)} bytes')
                print(f'  tables: {ids[:6]}')
                print(f'  want "{tid}": '
                      + ('FOUND' if tid in ids else 'MISSING'))
                if tid in ids:
                    rows = parse(body, tid, keep)
                    have = set().union(*[set(r) for r in rows[:5]]) if rows else set()
                    missing = [k for k in keep
                               if k not in have and k != 'name_display']
                    print(f'  parsed {len(rows)} rows')
                    if missing:
                        print(f'  MISSING COLUMNS: {missing}')
                    else:
                        print('  all expected columns present')
                    if rows:
                        print(f'  sample: {rows[0]}')
            except Exception as e:
                print(f'\n{page}: FAILED - {e}')
            time.sleep(4)
        return 0

    try:
        players = load_season(a.season, a.refresh)
    except Exception as e:
        print(f'fetch failed: {e}', file=sys.stderr)
        print('B-Ref rate limits at ~20 req/min; wait a minute and retry.',
              file=sys.stderr)
        return 1

    by_name = {norm_name(k): v for k, v in players.items()}
    by_short = collections.defaultdict(list)
    for k, v in players.items():
        by_short[short(k)].append(v)
    lines, day = tonights_lines(a.day)
    if not lines:
        print(f'no book lines in {PICKS}')
        return 1

    out = []
    for r in lines:
        mk = norm_market(r.get('market'))
        cols = MARKET_36.get(mk)
        if not cols:
            continue
        game_teams = [canon(r.get('away')), canon(r.get('home'))]
        entry = by_name.get(norm_name(r.get('player')))
        if not entry:
            # fall back to initial+surname, but only when the team
            # settles which player it is
            cands = by_short.get(short(r.get('player')), [])
            cands = [c for c in cands
                     if set(c.get('teams') or {}) & set(game_teams)]
            if len(cands) != 1:
                if cands or by_short.get(short(r.get('player'))):
                    print(f"  ? {r.get('player')}: ambiguous name, skipped",
                          file=sys.stderr)
                continue
            entry = cands[0]
        row = pick_team_row(entry, game_teams)
        if not row:
            continue
        rate36 = sum((row.get(c) or 0) for c in cols)
        g, mp = row.get('games'), row.get('mp')
        if not rate36 or not g or not mp:
            continue
        mpg = mp / g
        need_min = r['line'] / rate36 * 36.0
        gap = (need_min - mpg) / mpg * 100.0
        # A rate earned elsewhere describes a role he no longer has.
        # Jaylen Brown's 30.0 per-36 came as Boston's first option; in
        # Philadelphia behind James and Embiid it means much less, and
        # an unflagged -32% gap invites exactly the wrong read.
        moved = row.get('team') not in game_teams
        out.append({
            'player': r['player'], 'team': row.get('team'), 'moved': moved,
            'game': f"{r.get('away')}@{r.get('home')}",
            'market': mk, 'line': r['line'],
            'rate36': round(rate36, 2), 'mpg': round(mpg, 1),
            'need_min': round(need_min, 1), 'gap_pct': round(gap, 1),
            'usg': row.get('usg_pct'), 'gp': int(g),
            'prob_over': r.get('prob_over'),
        })

    out = [o for o in out if abs(o['gap_pct']) >= a.min_gap]
    out.sort(key=lambda o: o['gap_pct'])

    if a.json:
        print(json.dumps({'day': day, 'season': a.season, 'rows': out},
                         indent=1))
        return 0

    print(f'\n{day}: {len(out)} lines with a {a.season} baseline\n')
    hdr = (f"{'PLAYER':24}{'TM':5}{'MARKET':10}{'LINE':>6}{'PER36':>7}"
           f"{'MPG':>6}{'NEEDS':>7}{'GAP':>8}{'USG':>7}{'GP':>4}")
    print(hdr)
    print('-' * len(hdr))
    for o in out:
        usg = f"{o['usg']:.1f}" if o['usg'] else '-'
        tm = (o['team'] or '?') + ('*' if o.get('moved') else '')
        print(f"{o['player'][:23]:24}{tm:5}{o['market']:10}"
              f"{o['line']:>6}{o['rate36']:>7.2f}{o['mpg']:>6.1f}"
              f"{o['need_min']:>7.1f}{o['gap_pct']:>7.1f}%{usg:>7}{o['gp']:>4}")

    print('\nPER36  his production rate per 36 minutes, last season')
    print('MPG    minutes he actually averaged')
    print('NEEDS  minutes this line requires at that rate')
    print('GAP    how much more (or less) the line is asking for')
    print('USG    usage rate: share of team possessions he used')
    print('*      baseline is from a DIFFERENT team than tonight: the')
    print('       rate describes a role he no longer has, so the gap')
    print('       is not comparable. Read those rows with care.')
    print('\nSorted most negative first: a large negative gap means the')
    print('line is well inside his established rate, which is where the')
    print('overs live. Large positive means the line needs a bigger role')
    print('than he has had -- a question for the film, not a bet, since')
    print('these rates know nothing about tonight.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
