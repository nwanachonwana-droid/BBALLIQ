#!/usr/bin/env python3
"""DEFENCE -- rank the defences worth attacking, and say whether the
badness should hold.

WHY THIS INSTEAD OF A PRICING MODEL
  edges.py tried to beat posted lines with rate x minutes. Over three
  days it found its own calibration error four separate times: every
  row it liked was an over for a star or an under for a bench player,
  at roughly 9% of its own expectation either way -- smaller than the
  error bar on that expectation. A screen that disagrees with seven
  books by less than its own uncertainty is not finding anything.

  So this script prices nothing and never looks at a line. It ranks
  defences on the things that travel between games -- rim concession,
  three-point concession, volume -- and hands the result over. Player
  selection stays with possession data and film, where it belongs.

EARLY SEASON IS THE WHOLE PROBLEM
  Three games of team defence is noise; a defensive rating over three
  games swings ten points on schedule alone. So the ranking runs on
  LAST season's profile and prints this season's sample beside it,
  flagged when the two disagree. The sample earns weight as it
  accumulates and not before -- the same shrinkage edges.py needed for
  minutes, for the same reason.

PERSISTENCE
  "No reason to believe they will be better" is a roster question, and
  the part of it that lives in data is minutes continuity: of last
  season's top eight by minutes, how many have actually appeared for
  that team this season. 8/8 behind a bad defence is a defence with no
  personnel reason to improve. A low number is NOT evidence that a
  defence got better -- it is a flag to go watch.

  Points in the paint is an NBA.com tracking stat and nba.com answers
  403 from this network. Opponent FG% by shot distance is the better
  measure anyway: it says where a defence gets beaten rather than
  lumping every paint touch together.

usage
  python3 defense.py --discover     # FIRST: report real table ids and columns
  python3 defense.py --build        # cache the tables once discovery passes
  python3 defense.py               # the ranking
  python3 defense.py --team CLE    # one defence in detail
"""
import os, re, sys, json, math, time, argparse, subprocess, statistics
import datetime, unicodedata

DATA = os.path.expanduser('~/kalshi_data')
BREF = 'https://www.basketball-reference.com/leagues'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# Last completed season provides the profile; the in-progress season
# provides the small sample shown beside it. B-Ref names a season by
# the year it ends, so 2026-27 is NBA_2027.
BASE_SEASON = 2026
NOW_SEASON = 2027

# How many games of prior season one game of this season is worth,
# for the defensive ZONE profile. See patch notes: derived from the
# measured reliability of adjacent team metrics (9 to 25 games),
# placed at the noisy end because zone percentages are shooting
# stats. NOT measured for zones directly -- the cache holds them as
# season aggregates, so the within-team variance cannot be computed.
# w = g / (g + ZONE_PRIOR_GAMES).
ZONE_PRIOR_GAMES = 20.0

TEAM_CODE = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI',
    'Cleveland Cavaliers': 'CLE', 'Dallas Mavericks': 'DAL',
    'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GS', 'Houston Rockets': 'HOU',
    'Indiana Pacers': 'IND', 'Los Angeles Clippers': 'LAC',
    'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL',
    'Minnesota Timberwolves': 'MIN', 'New Orleans Pelicans': 'NO',
    'New York Knicks': 'NY', 'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI',
    'Phoenix Suns': 'PHX', 'Portland Trail Blazers': 'POR',
    'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SA',
    'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA',
    'Washington Wizards': 'WSH',
}
ALT_CODE = {'NYK': 'NY', 'SAS': 'SA', 'GSW': 'GS', 'NOP': 'NO',
            'WAS': 'WSH', 'PHO': 'PHX', 'BRK': 'BKN', 'CHO': 'CHA',
            'UTAH': 'UTA'}
VALID = set(TEAM_CODE.values())


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


def code(t):
    t = re.sub(r'\*$', '', (t or '').strip())
    return TEAM_CODE.get(t, ALT_CODE.get(t.upper(), t.upper()))


# --- fetching ---------------------------------------------------------
#
# Python's TLS handshake is fingerprinted and refused; curl's is not.
# That cost a morning on tracking.py, so every fetch here shells out.

def fetch(url, tries=3):
    last = ''
    for i in range(tries):
        p = subprocess.run(
            ['curl', '-sS', '--compressed', '-m', '45', '-A', UA, url],
            capture_output=True, text=True)
        if p.returncode == 0 and len(p.stdout) > 2000:
            return p.stdout
        last = (p.stderr or '').strip() or f'{len(p.stdout)} bytes'
        if i + 1 < tries:
            subprocess.run(['sleep', str(4 * (i + 1))])
    raise RuntimeError(f'{url}: {last}')


def tables(body):
    """{table_id: raw html} for every table on the page.

    B-Ref serves most of its tables inside HTML comments so that the
    page renders progressively. Strip the comment markers first or the
    tables are invisible -- bref.py learned this the hard way.
    """
    body = body.replace('<!--', '').replace('-->', '')
    out = {}
    for m in re.finditer(r'<table[^>]*\bid="([^"]+)"[^>]*>(.*?)</table>',
                         body, re.S):
        out[m.group(1)] = m.group(2)
    return out


def rows(html):
    """Parse one table body into [{data_stat: text}], skipping headers.

    Keyed by data-stat and never by position. ESPN reordered columns
    on us once and a silent off-by-one mis-graded every pick without
    raising anything; B-Ref will do the same eventually.
    """
    out = []
    # Capture the opening tag as well as the contents. Matching only
    # the contents put class="thead" in the discarded half, so the
    # repeated mid-table header rows parsed as a team called "Team".
    for attrs, tr in re.findall(r'<tr([^>]*)>(.*?)</tr>', html, re.S):
        if 'thead' in attrs or '<th scope="col"' in tr:
            continue
        cells = {}
        spanned = 0
        for tag, cattrs, stat, inner in re.findall(
                r'<(t[hd])((?:[^>]*?))\bdata-stat="([^"]+)"([^>]*>.*?)</\1>',
                tr, re.S):
            # B-Ref stacks headers: an over-header row of <th colspan=N>
            # group labels sits above the real column names. Those
            # cells carry data-stats of their own (ff_def, adjusted,
            # header_fg_pct), and counting them as data is why
            # advanced-team reported two columns and shooting-opponent
            # six. Skip any spanning cell, and drop a row that was
            # nothing but those.
            whole = cattrs + inner
            if re.search(r'\bcolspan="(?!1")', whole) or stat.startswith('header'):
                spanned += 1
                continue
            body_ = inner.split('>', 1)[1] if '>' in inner else inner
            cells[stat] = re.sub(r'<[^>]+>', '', body_).strip()
        if cells:
            out.append(cells)
    return out


def all_stats(rs):
    """Every data-stat appearing anywhere in a table, not just row 0.

    Reporting rows[0].keys() was the bug in the first discovery run:
    on any stacked-header table the first parsed row was the group
    labels, so the real columns were never printed and five fields
    came back NOT FOUND when they were all present.
    """
    seen = set()
    for r in rs:
        seen.update(r.keys())
    return sorted(seen)


def num(v):
    try:
        return float(re.sub(r'[^0-9.\-]', '', v or ''))
    except Exception:
        return None


# --- discovery --------------------------------------------------------
#
# The columns this script needs, as {our name: [candidate data-stats]}.
# B-Ref has renamed these before. Discovery reports which candidate
# actually matched so the parser is never guessing, and the ranking
# refuses to run while any required field is unresolved.

# Four zones, because that is what the data actually supports and
# because lumping them was the known flaw in the first FIT: a
# two-point share mixes rim attacks with midrange, and midrange is
# the one place a defence is not exploitable. ZONES maps our name to
# the B-Ref distance suffix. 16_xx is "16 ft to the arc".
ZONES = {'rim': '00_03', 'short': '03_10', 'mid1': '10_16', 'mid2': '16_xx'}

WANT = {
    'drtg':      (['ratings', 'per_poss-opponent'],
                  ['def_rtg_adj', 'def_rtg', 'opp_pts'],
                  'schedule-adjusted defensive rating'),
    'opp_pts':   (['per_game-opponent'], ['opp_pts'],
                  'opponent points per game'),
    'opp_fg3a':  (['per_game-opponent'], ['opp_fg3a'],
                  'opponent threes attempted per game'),
    'opp_fg3p':  (['per_game-opponent'], ['opp_fg3_pct'],
                  'opponent three-point pct'),
}
for _z, _sfx in ZONES.items():
    WANT[f'{_z}_pct'] = (['shooting-opponent'],
                         [f'opp_fg_pct_{_sfx}', f'fg_pct_{_sfx}'],
                         f'opponent FG% {_sfx.replace("_", "-")} ft')
    WANT[f'{_z}_share'] = (['shooting-opponent'],
                           [f'opp_pct_fga_{_sfx}', f'pct_fga_{_sfx}'],
                           f'share of opponent attempts {_sfx} ft')

# Per-player shot location, so FIT knows where a player's offence
# comes from rather than inferring it from a two/three split. Its own
# page, its own table id, so its own discovery -- and non-blocking: if
# it cannot be resolved, FIT falls back to two/three and says so.
PLAYER_SHOOTING = {
    'ps_name':  (['shooting', 'shooting_stats', 'per_game_shooting'],
                 ['name_display', 'player'], 'player name'),
    'ps_team':  (['shooting', 'shooting_stats', 'per_game_shooting'],
                 ['team_name_abbr', 'team_id', 'team'], 'team'),
    'ps_mp':    (['shooting', 'shooting_stats', 'per_game_shooting'],
                 ['mp'], 'minutes, to gate noisy rows'),
    'ps_three': (['shooting', 'shooting_stats', 'per_game_shooting'],
                 ['pct_fga_fg3a'], 'share of his attempts from three'),
}
for _z, _sfx in ZONES.items():
    PLAYER_SHOOTING[f'ps_{_z}'] = (
        ['shooting', 'shooting_stats', 'per_game_shooting'],
        [f'pct_fga_{_sfx}'], f'share of his attempts {_sfx} ft')

# Resolved the same way but never blocking. drtg_raw exists because
# def_rtg_adj is the WRONG column for an in-progress season: B-Ref's
# adjustment is nearly all prior until schedules balance, so three
# games in it returned the league average 110.2 for 21 of 24 teams and
# DRIFT became last season's rating with a minus sign. Adjusted is
# right for the completed baseline and wrong for the live sample, so
# each season now reads the column that suits it.
OPTIONAL = {
    'drtg_raw': (['ratings'], ['def_rtg'],
                 'unadjusted defensive rating, for the live sample'),
    'games':    (['per_game-opponent', 'per_game-team'], ['g'],
                 'games played, so the sample size is visible'),
}

# The offensive half. A game total needs both teams' attack as well as
# their defence, and all of it sits in advanced-team beside pace -- so
# this costs one more column map entry each and no extra fetch.
TEAM_OFF = {
    'off_rtg':  (['advanced-team'], ['off_rtg'],
                 'points scored per 100 possessions'),
    'three_rate': (['advanced-team'], ['fg3a_per_fga_pct'],
                   'share of their own attempts taken from three'),
    'efg':      (['advanced-team'], ['efg_pct'],
                 'effective field goal pct, their offence'),
    'opp_efg':  (['advanced-team'], ['opp_efg_pct'],
                 'effective field goal pct allowed'),
    'tov_pct':  (['advanced-team'], ['tov_pct'], 'their turnover rate'),
    'opp_tov':  (['advanced-team'], ['opp_tov_pct'],
                 'turnover rate they force'),
}

# Pace is optional: if advanced-team has no pace column it is derived
# below from the two per_game tables, so discovery does not block on
# it. Searching every table for a bare 'pace' is also how opp_pts
# first resolved to confs_standings_E -- a field must say which table
# it comes from or it will match the wrong one and print numbers that
# look fine.
PACE_COL = (['advanced-team'], ['pace'], 'possessions per 48')

# Ingredients for the derived pace: possessions per game by the
# standard estimator, averaged over both teams.
#   poss = FGA + 0.44*FTA - ORB + TOV
PACE_PARTS = {
    'tm_fga': ('per_game-team', 'fga'), 'tm_fta': ('per_game-team', 'fta'),
    'tm_orb': ('per_game-team', 'orb'), 'tm_tov': ('per_game-team', 'tov'),
    'op_fga': ('per_game-opponent', 'opp_fga'),
    'op_fta': ('per_game-opponent', 'opp_fta'),
    'op_orb': ('per_game-opponent', 'opp_orb'),
    'op_tov': ('per_game-opponent', 'opp_tov'),
}

# Standings tables carry opponent points too, and matching one is a
# silent wrong answer rather than a visible failure.
SKIP_TABLES = ('standings',)


def derive_pace(t):
    """Possessions per game from the box-score ingredients.

    Averaged over both teams because a possession is shared. Slightly
    understates a team with many overtime games, since this divides by
    games rather than by minutes -- worth knowing, not worth another
    table to fix.
    """
    try:
        tm = t['tm_fga'] + 0.44 * t['tm_fta'] - t['tm_orb'] + t['tm_tov']
        op = t['op_fga'] + 0.44 * t['op_fta'] - t['op_orb'] + t['op_tov']
    except (KeyError, TypeError):
        return None
    return round((tm + op) / 2.0, 1)


def scan(season=BASE_SEASON, verbose=True):
    """{(page, table_id): [data-stats]} across both league pages."""
    pages = {
        f'NBA_{season}.html': f'{BREF}/NBA_{season}.html',
        f'NBA_{season}_ratings.html': f'{BREF}/NBA_{season}_ratings.html',
        f'NBA_{season}_shooting.html': f'{BREF}/NBA_{season}_shooting.html',
    }
    seen = {}
    for name, url in pages.items():
        try:
            body = fetch(url)
        except Exception as e:
            if verbose:
                print(f'  X {name}: {e}')
            continue
        tb = tables(body)
        if verbose:
            print(f'  {name}: {len(tb)} tables')
        for tid, html in sorted(tb.items()):
            if any(s in tid.lower() for s in SKIP_TABLES):
                continue
            rs = rows(html)
            if not rs:
                continue
            stats = all_stats(rs)
            seen[(name, tid)] = stats
            if verbose:
                print(f'     {tid:26} {len(rs):>3} rows, {len(stats):>3} cols')
                print(f'       {", ".join(stats)}')
        if verbose:
            print()
    return seen


def resolve(seen):
    """(column map, unresolved keys). Each field searches only its own
    tables, in the order listed."""
    found, missing = {}, []
    fields = dict(WANT)
    fields['pace'] = PACE_COL
    fields.update(OPTIONAL)
    fields.update(PLAYER_SHOOTING)
    fields.update(TEAM_OFF)
    for key, (want_tables, cands, _) in fields.items():
        hit = None
        for wt in want_tables:
            for (page, tid), stats in seen.items():
                if tid != wt:
                    continue
                for c in cands:
                    if c in stats:
                        hit = (page, tid, c)
                        break
                if hit:
                    break
            if hit:
                break
        if hit:
            found[key] = hit
        elif (key not in OPTIONAL and key not in PLAYER_SHOOTING
              and key not in TEAM_OFF):
            missing.append(key)
    for key, (tid, col) in PACE_PARTS.items():
        for (page, t), stats in seen.items():
            if t == tid and col in stats:
                found[key] = (page, tid, col)
                break
    return found, missing


def discover(season=BASE_SEASON):
    print(f'discovering B-Ref structure for {season}\n')
    seen = scan(season)
    if not seen:
        print('no tables parsed from either page -- the fetch failed or '
              'B-Ref\nchanged its markup wholesale.')
        return 1
    found, missing = resolve(seen)

    print('required columns')
    fields = dict(WANT)
    fields['pace'] = PACE_COL
    fields.update(OPTIONAL)
    fields.update(PLAYER_SHOOTING)
    fields.update(TEAM_OFF)
    blocking = []
    for key, (want_tables, cands, desc) in fields.items():
        if key in found:
            _, tid, col = found[key]
            print(f'  v {key:12} -> {tid}.{col:22} ({desc})')
        elif key in TEAM_OFF:
            print(f'  - {key:12} not found; --totals will not run')
        elif key in PLAYER_SHOOTING:
            print(f'  - {key:12} not found; FIT falls back to the '
                  f'two/three split')
        elif key in OPTIONAL:
            print(f'  - {key:12} not found (optional); '
                  f'the column that uses it prints a dash')
        elif key == 'pace':
            ok = all(k in found for k in PACE_PARTS)
            mark, note = ('v', 'derived from the per_game tables') if ok \
                else ('X', 'and the ingredients are missing too')
            print(f'  {mark} {key:10} -> no pace column; {note}')
            if not ok:
                blocking.append(key)
        else:
            print(f'  X {key:10} not in {"/".join(want_tables)}, '
                  f'tried: {", ".join(cands)}')
            blocking.append(key)
    print()
    if blocking:
        print(f'{len(blocking)} field(s) unresolved. Paste this output back '
              'and I will map\nthem. The ranking stays disabled rather than '
              'reporting numbers off\nthe wrong column.')
        return 1
    json.dump(found, open(os.path.join(DATA, 'bref_defense_map.json'), 'w'),
              indent=1)
    print(f'all {len(found)} fields resolved and saved.\n'
          'next: python3 defense.py --build')
    return 0


def colmap():
    p = os.path.join(DATA, 'bref_defense_map.json')
    return json.load(open(p)) if os.path.exists(p) else {}


# --- building ---------------------------------------------------------

def build_players(season=BASE_SEASON):
    """Cache per-player shot location: {key: {team, rim, short, mid, three}}.

    Shares of his own attempts by zone, which is what FIT needs. With
    this, a midrange-heavy wing scores near zero against a defence
    that is soft at the rim, instead of collecting credit for every
    two he takes.
    """
    cm = colmap()
    need = [k for k in PLAYER_SHOOTING if k not in cm]
    if need:
        print(f'  ! player shooting unresolved ({", ".join(need)}); '
              'FIT will use the two/three split', file=sys.stderr)
        return {}
    tid = cm['ps_name'][1]
    try:
        body = fetch(f'{BREF}/NBA_{season}_shooting.html')
    except Exception as e:
        print(f'  ! player shooting: {e}', file=sys.stderr)
        return {}
    tb = tables(body)
    if tid not in tb:
        print(f'  ! table {tid} missing from the shooting page',
              file=sys.stderr)
        return {}
    out = {}
    for r in rows(tb[tid]):
        nm = r.get(cm['ps_name'][2])
        tm = r.get(cm['ps_team'][2])
        if not nm or not tm:
            continue
        t = code(tm)
        if t not in VALID:          # TOT/2TM rows and junk
            continue
        mp = num(r.get(cm['ps_mp'][2]))
        zones = {z: num(r.get(cm[f'ps_{z}'][2])) for z in ZONES}
        three = num(r.get(cm['ps_three'][2]))
        if three is None or any(v is None for v in zones.values()):
            continue
        # B-Ref gives each zone as a share of ALL attempts, so the two
        # ranges plus the three share already sum to one. Fold the two
        # midrange bands together: they behave alike and splitting
        # them only adds noise.
        key = re.sub(r'[^a-z ]', '', deaccent(nm).lower())
        out[f'{key}|{t}'] = {
            'player': nm, 'team': t, 'mp': mp,
            'rim': zones['rim'], 'short': zones['short'],
            'mid': (zones['mid1'] or 0) + (zones['mid2'] or 0),
            'three': three}
    p = os.path.join(DATA, f'player_shooting_{season}.json')
    json.dump(out, open(p, 'w'))
    print(f'  player shooting: {len(out)} player-team rows')
    return out


def corr(xs, ys):
    """Pearson correlation, or None when there is nothing to correlate."""
    pairs = [(x, y) for x, y in zip(xs, ys)
             if x is not None and y is not None]
    if len(pairs) < 8:
        return None
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    mx, my = statistics.fmean(xs2), statistics.fmean(ys2)
    sx = statistics.pstdev(xs2) or None
    sy = statistics.pstdev(ys2) or None
    if not sx or not sy:
        return None
    cov = statistics.fmean([(x - mx) * (y - my) for x, y in pairs])
    return cov / (sx * sy)


def persistence(blob, keys):
    """{component: year-over-year correlation} across completed seasons.

    This is what the weights should be, and the reason is the same one
    that made CLV worth building: weight a signal by how much of it
    actually carries forward, not by how much it feels like it should.
    Opponent midrange percentage is mostly shot quality and luck and
    barely repeats; rim and three-point concession are scheme and
    largely do. Equal weights asserted otherwise with no evidence.

    Measured league-wide, so roster turnover is folded in -- KEEP
    handles that per team, separately.
    """
    done = sorted(s for s in blob if s.isdigit() and int(s) < NOW_SEASON)
    out = {}
    for k in keys:
        cs = []
        for a_, b_ in zip(done, done[1:]):
            ta, tb_ = blob[a_], blob[b_]
            shared = sorted(set(ta) & set(tb_))
            c = corr([ta[t].get(k) for t in shared],
                     [tb_[t].get(k) for t in shared])
            if c is not None:
                cs.append(c)
        out[k] = round(statistics.fmean(cs), 3) if cs else None
    return out


def build(seasons=(BASE_SEASON, NOW_SEASON)):
    cm = colmap()
    if not cm:
        print('run --discover first: the column map is not resolved, and '
              'parsing\nagainst guessed ids is how the d. mitchell collision '
              'happened.', file=sys.stderr)
        return 1
    # Merge into whatever is cached. A plain --build used to start
    # from {} and silently throw away the prior seasons that --history
    # had fetched, which took the measured weights with them and
    # dropped the screen back to equal weighting without saying so.
    blob = {}
    cache = os.path.join(DATA, 'defense.json')
    if os.path.exists(cache):
        try:
            blob = {k: v for k, v in json.load(open(cache)).items()
                    if not k.startswith('_')}
            keep = sorted(k for k in blob if k not in map(str, seasons))
            if keep:
                print(f'  keeping cached season(s): {", ".join(keep)}')
        except Exception:
            blob = {}
    for season in seasons:
        teams = {}
        for suffix in ('', '_ratings'):
            url = f'{BREF}/NBA_{season}{suffix}.html'
            try:
                body = fetch(url)
            except Exception as e:
                print(f'  ! {season}{suffix}: {e}', file=sys.stderr)
                continue
            tb = tables(body)
            for key, (page, tid, col) in cm.items():
                if tid not in tb:
                    continue
                for r in rows(tb[tid]):
                    name = (r.get('team') or r.get('team_name')
                            or r.get('team_id'))
                    if not name:
                        continue
                    t = code(name)
                    # Only rows that resolve to a real franchise. A
                    # name test alone let "League Average" and a
                    # stray "Team" header through as teams.
                    if t not in VALID:
                        continue
                    teams.setdefault(t, {})[key] = num(r.get(col))
            subprocess.run(['sleep', '4'])   # B-Ref throttles at speed
        for t, row in teams.items():
            if row.get('pace') is None:
                p = derive_pace(row)
                if p is not None:
                    row['pace'] = p
                    row['pace_derived'] = True
        got = sum(1 for r in teams.values() if r.get('drtg') is not None)
        print(f'  {season}: {len(teams)} teams, {got} with a defensive rating')
        blob[str(season)] = teams

    keys = (['drtg', 'pace', 'opp_pts', 'opp_fg3p', 'opp_fg3a']
            + [f'{z}_{s}' for z in ZONES for s in ('pct', 'share')])
    pers = persistence(blob, keys)
    done = sorted(s for s in blob if s.isdigit() and int(s) < NOW_SEASON)
    blob['_weights'] = pers
    blob['_weight_seasons'] = done
    if len([s for s in done]) >= 2:
        print('\n  year-over-year persistence (the weights):')
        for k in keys:
            v = pers.get(k)
            print(f'    {k:14} {"-" if v is None else f"{v:+.2f}"}')
    else:
        print('\n  only one completed season cached, so persistence cannot '
              'be\n  measured and the weights stay equal. '
              '--history 4 fixes that.')
    build_players(BASE_SEASON)
    json.dump(blob, open(os.path.join(DATA, 'defense.json'), 'w'))
    print(f'-> {os.path.join(DATA, "defense.json")}')
    return 0


# --- minutes continuity ----------------------------------------------

def continuity(top_n=8):
    """{team: (kept, of)} from data already on disk.

    bref_<base>.json holds last season's minutes by player and team;
    season_logs.json holds who has actually appeared this season and
    for whom. The intersection is the honest part of "no reason to
    expect improvement" -- it needs no scraping and no roster feed.
    """
    bp = os.path.join(DATA, f'bref_{BASE_SEASON}.json')
    lp = os.path.join(DATA, 'season_logs.json')
    if not (os.path.exists(bp) and os.path.exists(lp)):
        return {}
    bref = json.load(open(bp))
    logs = json.load(open(lp)).get('days') or {}

    seen = set()
    for day in logs.values():
        for r in day:
            seen.add((re.sub(r'[^a-z ]', '', (r.get('player') or '').lower()),
                      code(r.get('team'))))

    by_team = {}
    for name, entry in bref.items():
        key = re.sub(r'[^a-z ]', '', name.lower())
        for t, row in (entry.get('teams') or {}).items():
            if t.endswith('TM'):
                continue
            by_team.setdefault(code(t), []).append((row.get('mp') or 0, key))

    out = {}
    for t, players in by_team.items():
        players.sort(reverse=True)
        top = players[:top_n]
        kept = sum(1 for _, k in top if (k, t) in seen)
        out[t] = (kept, len(top))
    return out


# --- the ranking ------------------------------------------------------

def slate(day):
    """[(away, home)] for a date, from the ESPN scoreboard.

    The schedule comes from the schedule. covers2.py inferred the
    slate from whatever its scrape happened to contain and stamped
    rows with the scrape date, which put SGA and Brunson on the board
    a day after they played.
    """
    url = ('https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
           f'/scoreboard?dates={day}&limit=100')
    try:
        body = fetch(url, tries=2)
        data = json.loads(body)
    except Exception as e:
        print(f'  ! slate for {day} unavailable: {e}', file=sys.stderr)
        return []
    games = []
    for ev in data.get('events') or []:
        for c in ev.get('competitions') or []:
            cs = c.get('competitors') or []
            away = next((x for x in cs if x.get('homeAway') == 'away'), None)
            home = next((x for x in cs if x.get('homeAway') == 'home'), None)
            if away and home:
                games.append((code((away.get('team') or {}).get('abbreviation')),
                              code((home.get('team') or {}).get('abbreviation'))))
    return games


def roster(team, season=BASE_SEASON):
    """[player rows] for one team from the cached B-Ref player data.

    Rows for a combined-team split (anything ending in TM) are skipped
    -- a traded player's combined line describes a role split across
    two situations and belongs to neither.
    """
    p = os.path.join(DATA, f'bref_{season}.json')
    if not os.path.exists(p):
        return []
    out = []
    for name, entry in (json.load(open(p)) or {}).items():
        for t, row in (entry.get('teams') or {}).items():
            if t.endswith('TM') or code(t) != team:
                continue
            r = dict(row)
            r['player'] = name
            out.append(r)
    return out


def this_season():
    """{(player_key, team): mpg} from the ESPN box scores already cached.

    Used only to ask whether last season's role is still this player's
    role, and on which team. edges.py priced Khaman Maluach off 8.9
    minutes while the market had him starting; nothing here should
    repeat that silently.
    """
    p = os.path.join(DATA, 'season_logs.json')
    if not os.path.exists(p):
        return {}
    agg = {}
    for day in (json.load(open(p)).get('days') or {}).values():
        for r in day:
            k = (re.sub(r'[^a-z ]', '', deaccent(r.get('player')).lower()),
                 code(r.get('team')))
            agg.setdefault(k, []).append(r.get('min') or 0)
    return {k: (sum(v) / len(v), len(v)) for k, v in agg.items()}


def player_shooting(season=BASE_SEASON):
    p = os.path.join(DATA, f'player_shooting_{season}.json')
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception:
        return {}


def blend_mpg(cur, base_mpg, prior=8.0):
    """(minutes, weight on this season). Shrunk toward last season.

    Ported from edges.py, where it was written for exactly this and
    then not carried over -- the same omission as the Jalen Williams
    collision, fixed in one file and left in the other. POSS was being
    computed off last season's minutes alone, so Matas Buzelis showed
    14.1 possessions on a 29.2-minute baseline while actually playing
    21.0, and Day'Ron Sharpe held an 18.7-minute role profile while
    actually playing 30.

    Deliberately slow: two games get weight 0.2, because a two-game
    sample should not overturn a season. Gradual drift is what this
    handles. An outright role change is too large for shrinkage to
    catch in time, so role_drift() below flags that separately.
    """
    if not cur or not base_mpg:
        return (cur[0] if cur else base_mpg), 0.0
    f_mpg, g = cur
    if not f_mpg or not g:
        return base_mpg, 0.0
    w = g / (g + prior)
    return w * f_mpg + (1 - w) * base_mpg, w


def role_drift(cur, base_mpg):
    """This season's minutes over last season's, or None.

    The blend is too conservative to catch a role that changed
    outright, and the two failures point opposite ways: a player whose
    minutes collapsed cannot reach an over, and one whose minutes
    exploded is no longer the unreliable rotation piece an under
    depends on. So it is reported as a ratio and filtered by side.
    """
    if not cur or not base_mpg or not cur[0]:
        return None
    return cur[0] / base_mpg


INJ_URL = ('https://site.api.espn.com/apis/site/v2/sports/basketball'
           '/nba/injuries')
INJ_TTL = 1800          # seconds; a board re-run should not re-fetch


def injury_map(refresh=False):
    """{normalised name: {status, abbr, team, detail, back}}.

    From ESPN's league-wide injuries endpoint, confirmed by probe
    rather than assumed: one call covers all thirty teams and carries
    a status, a body part and a projected return date.

    Parsed by walking for any object holding both an athlete and a
    status, instead of indexing a fixed path. The shape found today
    was injuries[i].injuries[j], which is odd enough that it may not
    be stable, and a walk survives a reshuffle where an index would
    quietly return nothing.
    """
    cache = os.path.join(DATA, 'injuries.json')
    if not refresh and os.path.exists(cache):
        try:
            blob = json.load(open(cache))
            if time.time() - blob.get('fetched', 0) < INJ_TTL:
                return blob.get('players') or {}
        except Exception:
            pass
    try:
        js = json.loads(fetch(INJ_URL, tries=2))
    except Exception as e:
        print(f'  ! injuries unavailable: {e}', file=sys.stderr)
        return {}

    out = {}

    def walk(node, team=None, depth=0):
        if depth > 6:
            return
        if isinstance(node, dict):
            t = node.get('team')
            if isinstance(t, dict):
                team = (t.get('abbreviation') or t.get('shortDisplayName')
                        or team)
            ath, st = node.get('athlete'), node.get('status')
            if isinstance(ath, dict) and st:
                nm = (ath.get('displayName') or ath.get('fullName') or '')
                if nm:
                    det = node.get('details') or {}
                    typ = node.get('type') or {}
                    out[norm_player(nm)] = {
                        'name': nm, 'team': code(team) if team else None,
                        'status': st,
                        'abbr': (typ.get('abbreviation')
                                 or (det.get('fantasyStatus') or {})
                                 .get('abbreviation') or st[:3].upper()),
                        'detail': ' '.join(x for x in (det.get('side'),
                                                       det.get('type'),
                                                       det.get('detail'))
                                           if x) or None,
                        'back': det.get('returnDate'),
                    }
            for v in node.values():
                walk(v, team, depth + 1)
        elif isinstance(node, list):
            for v in node:
                walk(v, team, depth + 1)

    walk(js)
    os.makedirs(DATA, exist_ok=True)
    json.dump({'fetched': time.time(), 'players': out}, open(cache, 'w'))
    return out


def norm_player(n):
    return re.sub(r'[^a-z ]', '', deaccent(n or '').lower()).strip()


def inj_tag(info):
    """OUT / DD / GTD / '' -- short enough for a column."""
    if not info:
        return ''
    a = (info.get('abbr') or '').upper()
    if a in ('O', 'OUT'):
        return 'OUT'
    if a in ('DD', 'GTD', 'D'):
        return 'GTD'
    return a[:3] or '?'


def rest_map(day):
    """{team: (days since last game, games in the previous 5 days)}.

    Derived from the box scores already cached, so it costs nothing
    and needs no feed. On 27 Oct the research had to tell us Boston
    was on the second night of a back-to-back and Brooklyn was playing
    a third game in four -- both facts were sitting in season_logs
    the whole time.

    Rest is one of the larger effects on minutes and efficiency in the
    sport, and it is deliberately reported rather than modelled: no
    coefficient here is measured, so the screen shows the spot and
    leaves the adjustment to you until there is enough logged to fit
    one honestly.
    """
    p = os.path.join(DATA, 'season_logs.json')
    if not os.path.exists(p):
        return {}
    try:
        days = json.load(open(p)).get('days') or {}
    except Exception:
        return {}
    played = {}
    for d, rows in days.items():
        if not d.isdigit() or d >= day:
            continue           # walk-forward: tonight is not evidence
        for r in rows:
            played.setdefault(code(r.get('team')), set()).add(d)

    def as_date(x):
        return datetime.date(int(x[:4]), int(x[4:6]), int(x[6:8]))

    today = as_date(day)
    out = {}
    for t, ds in played.items():
        dates = sorted(as_date(x) for x in ds)
        if not dates:
            continue
        gap = (today - dates[-1]).days
        recent = sum(1 for x in dates if 0 < (today - x).days <= 5)
        out[t] = (gap, recent)
    return out


def rest_tag(info):
    """Short label for a schedule spot, or '' when it is unremarkable."""
    if not info:
        return '?'
    gap, recent = info
    if gap <= 1:
        return 'B2B'
    if recent >= 3:
        return f'{recent}in5'
    if gap >= 3:
        return f'{gap}d off'
    return f'{gap}d'


def player_rows(opp, d, pace, zw, zwsum, now, shoot):
    """Score every player on `opp` against one defence profile.

    Pulled out of attack() so the whole-slate board and the single
    matchup cannot drift apart. One of them silently scoring players
    differently from the other is exactly the kind of divergence
    nobody notices until a result makes no sense.
    """
    rows_ = []
    for r in roster(opp):
        usg, mp, g = r.get('usg_pct'), r.get('mp'), r.get('games')
        fga, fg3a = r.get('fga_per_minute_36'), r.get('fg3a_per_minute_36')
        if not (usg and mp and g and fga):
            continue
        base_mpg = mp / g
        key = re.sub(r'[^a-z ]', '', deaccent(r['player']).lower())
        cur0 = now.get((key, opp))
        mpg, wnew = blend_mpg(cur0, base_mpg)
        drift = role_drift(cur0, base_mpg)
        poss = pace * (mpg / 48.0) * (usg / 100.0)
        sp = shoot.get(f'{key}|{opp}')
        if sp:
            shares = {z: (sp.get(z) or 0.0)
                      for z in ('rim', 'short', 'mid', 'three')}
            src = 'zones'
        else:
            # Fallback: the two/three split, which cannot separate a
            # rim attacker from a midrange wing. Everything inside the
            # arc is charged to 'short' as the least-wrong guess, and
            # the row is flagged so it is not read as a zone match.
            ts = (fg3a or 0) / fga if fga else 0.0
            shares = {'rim': 0.0, 'short': 1.0 - ts, 'mid': 0.0, 'three': ts}
            src = 'split'
        # FIT IS A WEIGHTED AVERAGE, AND THE WEIGHTS WERE NOT
        # NORMALISED.
        #
        # The numerator weights each zone's softness by persistence
        # AND by the player's share of attempts there. The shares
        # already sum to one, so dividing by sum(zw) divides by the
        # zone count a second time. Two consequences, and the second
        # is the serious one:
        #
        #   SCALE: FIT came out about 4x smaller than the z-scores it
        #   averages -- the card's whole range was -0.07 to +0.07
        #   against a softness z running -2 to +2. A regressor with
        #   almost no spread cannot produce an estimable slope no
        #   matter how many player-games are added, which is why the
        #   calibration SE sits at 0.17-0.38 on 959 games.
        #
        #   ORDERING: the compression is not constant. It depends on
        #   where the player shoots, so against a defence equally
        #   soft in every zone a rim-heavy man scored +0.29, a
        #   short-heavy man +0.32 and a three-heavy man +0.18. The
        #   matchup is identical for all three. It was ranking shot
        #   diets, not matchups.
        #
        # Dividing by sum(zw*shares) makes it the share-weighted mean
        # it was always meant to be: same units as the softness
        # z-score, and a defence that is +1.0 soft everywhere reads
        # +1.00 for everybody.
        #
        # Persistence still does its job -- it decides how much each
        # zone votes WITHIN the average -- it just no longer leaks
        # into the magnitude.
        _den = sum(zw[z] * shares[z] for z in zw)
        _num = {z: zw[z] * d.get(z, 0.0) * shares[z] for z in zw}
        fit = (sum(_num.values()) / _den) if _den > 0 else 0.0
        three_share = shares['three']
        # HOW CONCENTRATED IS THIS EDGE, not just where it points.
        #
        # via is an argmax, so it fires whether the winning zone
        # carries 85% of the edge or 22% of it. Keeping the label
        # without the weight is what let two rows be treated as the
        # same claim when one of them barely shoots from the zone
        # named on it.
        #
        # Share of the ABSOLUTE numerator, so a row whose zones
        # disagree in sign cannot report a weight above 1 or a
        # negative one.
        _abs = sum(abs(v) for v in _num.values())
        cur = now.get((key, opp))
        elsewhere = next((t for (k, t), _ in now.items()
                          if k == key and t != opp), None)
        rows_.append({
            'player': r['player'], 'pos': r.get('pos') or '-',
            'usg': usg, 'mpg': round(mpg, 1), 'poss': round(poss, 1),
            'base_mpg': round(base_mpg, 1), 'w_new': round(wnew, 2),
            'drift': None if drift is None else round(drift, 2),
            'three_share': round(three_share, 3), 'src': src,
            # scratch, consumed by the via_w block below and dropped
            # from anything that leaves this module
            '_num': _num, '_abs': _abs,
            'rim_share': round(shares['rim'], 3),
            'short_share': round(shares['short'], 3),
            'mid_share': round(shares['mid'], 3),
            'fg3_36': r.get('fg3_per_minute_36'),
            'pts_36': r.get('pts_per_minute_36'),
            'ast_36': r.get('ast_per_minute_36'),
            'fit': round(fit, 2),
            'now_mpg': None if not cur else round(cur[0], 1),
            'now_g': None if not cur else cur[1],
            'moved_to': elsewhere if not cur else None,
            # starts / appearances last season: a role that was not
            # guaranteed then is the under side of the thesis
            'role': (round((r.get('games_started') or 0) / g, 2)
                     if g else None),
            'team': opp,
        })
    return rows_


def totals(day, base, out_by_team, a, W):
    """Rank tonight's games by the total their profiles imply.

    The spine is the standard ratings-and-pace model, which is
    well-trodden and not mine:

        ppp(A) = off_rtg(A) + def_rtg(B) - league average
        total  = (ppp(A) + ppp(B)) x game pace / 100

    A team's offensive rating already says how efficiently it scores
    and its opponent's defensive rating how efficiently it concedes;
    subtracting the league mean once stops double-counting the
    average. Pace converts points-per-possession into points.

    Two columns are added to that because a rating is a season
    average and the question is tonight:

    3DIET -- whether this game is built to produce threes. It takes
    both offences' share of attempts from the arc and both defences'
    willingness to concede them, as league-relative scores. High means
    volume shooting into defences that allow it, which is the highest
    variance way a total goes over. Note the asymmetry: the defensive
    half of this is the component your own four seasons showed to be
    the LEAST persistent, so 3DIET is the softest column here.

    FIT -- the player screen aggregated. Every rotation player on each
    side is scored against the defence he is facing, weighted by the
    possessions he finishes, and both sides are summed. Positive means
    both rosters are shooting into zones their opponent gives up;
    negative is the bad-matchups-both-ways case that belongs under.
    This is the one column that is ours rather than inherited.
    """
    games = slate(day)
    if not games:
        print(f'no games found for {day}', file=sys.stderr)
        return 1
    need = ('off_rtg', 'three_rate')
    have = [t for t in base if all(base[t].get(k) is not None for k in need)]
    if len(have) < 20:
        print('team offence is not cached -- run:\n'
              '  python3 defense.py --discover\n'
              '  python3 defense.py --build --history 4',
              file=sys.stderr)
        return 1

    LG = statistics.fmean([base[t]['off_rtg'] for t in have])
    LG_PACE = statistics.fmean([base[t]['pace'] for t in have
                                if base[t].get('pace')])
    z_pace = zs([base[t].get('pace') for t in have])
    z_off = zs([base[t].get('off_rtg') for t in have])
    z_def = zs([base[t].get('drtg') for t in have])
    z_3rt = zs([base[t].get('three_rate') for t in have])

    now = this_season()
    shoot = player_shooting()
    inj = injury_map(getattr(a, 'refresh_injuries', False))
    zw = {k: W[k] for k in ('rim', 'short', 'mid', 'three')}
    zwsum = sum(zw.values()) or 1.0

    def side_fit(defence, opp, pace):
        """Possession-weighted mean FIT for `opp` against `defence`.

        Weighted, because a 22-possession star meeting a soft zone
        matters more to a total than a 6-possession reserve meeting
        the same one, and a flat average would let the bench outvote
        him three to one.
        """
        d = out_by_team.get(defence)
        if not d:
            return None
        # ruled-out players are excluded here too: a total built on a
        # roster that includes a man who is not playing is the same
        # error as a board row for him
        rs = [x for x in player_rows(opp, d, pace, zw, zwsum, now, shoot)
              if not x['moved_to'] and x['poss'] >= 5.0
              and inj_tag(inj.get(norm_player(x['player']))) != 'OUT']
        wsum = sum(x['poss'] for x in rs)
        if not wsum:
            return None
        return sum(x['fit'] * x['poss'] for x in rs) / wsum

    rows = []
    for away, home in games:
        A, H = base.get(away), base.get(home)
        if not A or not H or A.get('off_rtg') is None \
                or H.get('off_rtg') is None:
            continue
        pace = statistics.fmean([p for p in (A.get('pace'), H.get('pace'))
                                 if p]) or LG_PACE
        ppp_a = A['off_rtg'] + (H.get('drtg') or LG) - LG
        ppp_h = H['off_rtg'] + (A.get('drtg') or LG) - LG
        total = (ppp_a + ppp_h) * pace / 100.0
        lg_total = 2 * LG * LG_PACE / 100.0

        da = out_by_team.get(away) or {}
        dh = out_by_team.get(home) or {}
        # both offences' willingness to shoot threes, and both
        # defences' willingness to let them
        diet = statistics.fmean([
            z_3rt(A.get('three_rate')), z_3rt(H.get('three_rate')),
            dh.get('three', 0.0), da.get('three', 0.0)])
        fa = side_fit(home, away, pace)
        fh = side_fit(away, home, pace)
        fit = None if (fa is None and fh is None) else \
            sum(v for v in (fa, fh) if v is not None)
        keeps = [d.get('keep') for d in (da, dh) if d.get('keep') is not None]
        rows.append({
            'game': f'{away}@{home}', 'away': away, 'home': home,
            'total': round(total, 1), 'vs_lg': round(total - lg_total, 1),
            'pace': round(pace, 1),
            'pace_z': round(statistics.fmean([z_pace(A.get('pace')),
                                              z_pace(H.get('pace'))]), 2),
            'off_z': round(statistics.fmean([z_off(A.get('off_rtg')),
                                             z_off(H.get('off_rtg'))]), 2),
            'def_z': round(statistics.fmean([z_def(A.get('drtg')),
                                             z_def(H.get('drtg'))]), 2),
            'diet': round(diet, 2),
            'fit': None if fit is None else round(fit, 2),
            'fit_away': None if fa is None else round(fa, 2),
            'fit_home': None if fh is None else round(fh, 2),
            'keep_min': min(keeps) if keeps else None,
        })
    rows.sort(key=lambda x: -x['total'])
    if a.json:
        print(json.dumps({'day': day, 'league_total': round(2*LG*LG_PACE/100, 1),
                          'games': rows}, indent=1))
        return 0

    print(f'\nGAME TOTALS -- {day}, {len(rows)} game(s), '
          f'league baseline {2*LG*LG_PACE/100:.1f}')
    hdr = (f"{'#':>3} {'GAME':11}{'EXP':>7}{'vsLG':>7}{'PACE':>7}"
           f"{'PACE z':>8}{'OFF':>7}{'DEF':>7}{'3DIET':>7}{'FIT':>7}"
           f"{'KEEP':>6}  LEAN")
    print(hdr)
    print('-' * len(hdr))
    for i, x in enumerate(rows, 1):
        f = '-' if x['fit'] is None else f"{x['fit']:+.2f}"
        kp = '-' if x['keep_min'] is None else f"{x['keep_min']}/8"
        # The lean has to agree with itself: a high total on bad
        # matchups both ways is a disagreement, not a signal. And a
        # missing FIT is not a disagreement either -- saying 'mixed'
        # when the second opinion was never obtained reads as though
        # two sources differed when only one spoke.
        if x['fit'] is None:
            lean = ('OVER?' if x['vs_lg'] > 2 else
                    'UNDER?' if x['vs_lg'] < -2 else 'flat')
            lean += '  (no FIT: roster data missing)'
        elif x['vs_lg'] > 2 and x['fit'] > 0:
            lean = 'OVER'
        elif x['vs_lg'] < -2 and x['fit'] < 0:
            lean = 'UNDER'
        else:
            lean = 'mixed'
        print(f"{i:>3} {x['game']:11}{x['total']:>7.1f}{x['vs_lg']:>+7.1f}"
              f"{x['pace']:>7.1f}{x['pace_z']:>+8.2f}{x['off_z']:>+7.2f}"
              f"{x['def_z']:>+7.2f}{x['diet']:>+7.2f}{f:>7}{kp:>6}  {lean}")
    print("""
  EXP is the total both teams' season ratings imply at this game's
  pace; vsLG is that against the league baseline. OFF and DEF are
  both sides averaged and league-relative -- DEF positive means soft
  defences, so an under wants it negative. PACE z likewise.

  3DIET is both offences' three-point rate plus both defences'
  willingness to concede threes. Treat it as the softest column: its
  defensive half is the component that persists least between
  seasons.

  FIT is the player screen summed over both rosters, weighted by
  possessions -- positive means both sides are shooting into zones
  their opponent gives up. LEAN only fires when the total and FIT
  agree; when a high total sits on bad matchups that is a
  disagreement, not a signal, and it says mixed.

  No market total was consulted. Compare these to the posted number
  yourself -- a game can imply 231 and still be a bad over at 237.""")
    return 0


def board(day, base, out_by_team, a, W):
    """Every player in every game tonight, split into overs and unders.

    This is the prediction board, not a card. It ignores price
    entirely and on purpose: we do not yet know whether the matchup
    read predicts anything, and we cannot find that out while also
    filtering on what the market will sell. Establish whether the
    signal exists, then let price decide what is worth betting.

    So nothing here is a bet. Each side is the screen's honest answer
    to "who does tonight's matchup favour", logged so it can be
    graded against the box score whether or not a dollar was risked.
    """
    games = slate(day)
    if not games:
        print(f'no games found for {day}', file=sys.stderr)
        return 1
    now = this_season()
    shoot = player_shooting()
    rest = rest_map(day)
    inj = injury_map(a.refresh_injuries)
    zw = {k: W[k] for k in ('rim', 'short', 'mid', 'three')}
    zwsum = sum(zw.values()) or 1.0

    allrows = []
    for away, home in games:
        for defence, opp in ((away, home), (home, away)):
            d = out_by_team.get(defence)
            if not d:
                continue
            pace = statistics.fmean([p for p in (
                (base.get(defence) or {}).get('pace'),
                (base.get(opp) or {}).get('pace')) if p]) or 100.0
            for x in player_rows(opp, d, pace, zw, zwsum, now, shoot):
                x['opp'] = defence
                x['game'] = f'{away}@{home}'
                x['opp_rank'] = d.get('rank')
                # KEEP travels with the row now. Every read this week
                # has needed it -- a +2.52 zone against a defence that
                # replaced six of its top eight is describing a roster
                # that no longer exists -- and cross-referencing it by
                # hand from another table is how it gets forgotten.
                x['opp_keep'] = d.get('keep')
                # AND SO DOES THE DEFENCE'S DRIFT.
                #
                # Every zone number on this row -- rim, short, mid,
                # three -- comes from LAST season. The only thing this
                # season contributes to the defensive table is
                # drtg_raw, and the difference between the two is
                # already computed as `drift`. It just never left the
                # table, so a board row could say a defence is soft
                # from three on last year's evidence while this year's
                # points-allowed says the opposite, and nothing on the
                # row would mention it.
                #
                # KEEP asks whether the PERSONNEL is the same. Drift
                # asks whether the RESULT is. They fail separately: a
                # team can keep eight of eight and still defend
                # fifteen points better under a new scheme.
                x['opp_drift'] = d.get('drift')
                x['opp_w_now'] = d.get('w_now')
                x['opp_now_games'] = d.get('now_games')
                x['opp_keep_of'] = d.get('keep_of')
                x['rest'] = rest_tag(rest.get(opp))
                x['opp_rest'] = rest_tag(rest.get(defence))
                ii = inj.get(norm_player(x['player']))
                x['inj'] = inj_tag(ii)
                x['inj_detail'] = (ii or {}).get('detail')
                x['inj_back'] = (ii or {}).get('back')
                x['pace'] = round(pace, 1)
                # which zone of this defence his shots actually meet,
                # so a reader can argue with the row rather than
                # taking the number on faith
                best = max(zw, key=lambda z: zw[z] * d.get(z, 0.0)
                           * x.get(f'{z}_share', 0.0))
                worst = min(zw, key=lambda z: zw[z] * d.get(z, 0.0)
                            * x.get(f'{z}_share', 0.0))
                x['via'] = best if x['fit'] >= 0 else worst
                x['via_z'] = round(d.get(x['via'], 0.0), 2)
                # see the patch note on via_w: the label without this
                # number is what made Durant and Jabari Smith look
                # like one finding.
                x['via_w'] = (round(abs(x['_num'].get(x['via'], 0.0))
                                    / x['_abs'], 3)
                              if x.get('_abs') else None)
                # scratch out, before anything serialises this row
                x.pop('_num', None)
                x.pop('_abs', None)
                # EVERY zone's softness, not just the dominant one.
                # via_z answers "which zone is carrying his FIT"; a
                # per-category board needs "how soft is this defence
                # from three" regardless of whether three happens to
                # be his best zone. The dict was already computed --
                # it just was not leaving the function.
                x['zone_z'] = {z: round(d.get(z, 0.0), 2) for z in zw}
                allrows.append(x)

    # A man who has not played is not a prediction. The drift filters
    # cannot catch him -- with no games there is no ratio, so None
    # passes both -- which left Coby White top of the overs having
    # missed every Chicago game so far. Distinguish "his team has not
    # played either" (fine, nothing is known yet) from "his team has
    # played and he did not" (he is out, or buried).
    played = {}
    for (k, t), (mpg, g) in now.items():
        played[t] = max(played.get(t, 0), g)
    missing = [x for x in allrows
               if not x['moved_to'] and x['now_g'] is None
               and played.get(x['team'], 0) > 0]
    live = [x for x in allrows
            if not x['moved_to']
            and not (x['now_g'] is None and played.get(x['team'], 0) > 0)]

    # KEEP was a column you had to remember to read, and on 27 Oct it
    # was not read: the top two rows of a threes screen were unders
    # against Brooklyn's -1.19 arc, taken from a roster at 3/8 that
    # this season concedes more threes than anyone. The number was not
    # stale by a little, it had the wrong sign. --min-keep now filters
    # here as well as on the summary table.
    if a.min_keep > 0:
        cut = [x for x in live
               if x.get('opp_keep') is not None
               and x['opp_keep'] < a.min_keep]
        live = [x for x in live if x not in cut]
        if cut:
            names = []
            for x in sorted(cut, key=lambda r: -abs(r['fit'])):
                tag = f"{x['opp']} ({x['opp_keep']}/8)"
                if tag not in names:
                    names.append(tag)
            print(f'  held out on KEEP < {a.min_keep}: '
                  + ', '.join(names[:8]))

    # OVERS: the matchup favours him and he handles enough of the ball
    # to express it. Sorted by fit, with the possession floor as a
    # gate rather than a term -- a perfect matchup for a man taking
    # six shots is not a prediction about anything.
    # A ruled-out player is not a prediction, he is a void row -- the
    # same reasoning that holds out anyone whose team has played
    # without him, except this knows it the morning it happens rather
    # than three games later.
    if not a.allow_out:
        gone = [x for x in live if x.get('inj') == 'OUT']
        live = [x for x in live if x.get('inj') != 'OUT']
        if gone:
            seen = []
            for x in sorted(gone, key=lambda r: -r['poss']):
                tag = f"{x['player']} ({x['team']})"
                if tag not in seen:
                    seen.append(tag)
            print('  ruled OUT, held out: ' + ', '.join(seen[:10]))

    if a.no_b2b:
        drop = [x for x in live if x.get('rest') == 'B2B']
        live = [x for x in live if x.get('rest') != 'B2B']
        if drop:
            print('  held out on back-to-back: '
                  + ', '.join(sorted({x['team'] for x in drop})))

    overs = [x for x in live if x['poss'] >= a.min_poss
             and x['fit'] >= a.min_fit
             and (x['drift'] is None or x['drift'] >= a.drift_lo)]
    overs.sort(key=lambda x: -x['fit'])

    # UNDERS: the mirror. The defence is strong where he shoots AND
    # his role is not guaranteed, because a starter on a bad night
    # still plays 34 minutes and gets there anyway. The possession
    # band is deliberately lower -- this is the rotation player, not
    # the star.
    unders = [x for x in live
              if x['fit'] <= -a.min_fit
              and a.under_poss_lo <= x['poss'] <= a.under_poss_hi
              and (x['role'] is None or x['role'] <= a.max_role)
              and (x['drift'] is None or x['drift'] <= a.drift_hi)]
    unders.sort(key=lambda x: (x['fit'], -(x['role'] or 0)))

    if a.json:
        print(json.dumps({'day': day, 'overs': overs[:a.top],
                          'unders': unders[:a.top]}, indent=1))
        return 0

    ZN = {'rim': 'the rim', 'short': '3-10 ft', 'mid': 'midrange',
          'three': 'the arc'}

    def table(title, rs, note, show_role=False):
        print(f'\n{title}')
        # NOW is on BOTH tables. It was only on the overs, which hid
        # whether an under candidate is even playing -- and a pick on
        # a man who is out is not a prediction, it is a void row.
        hdr = (f"{'#':>3} {'PLAYER':21}{'TM':>4}{'POS':>4}{'POSS':>6}"
               f"{'MPG':>6}{'NOW':>6}{'G':>3}{'x':>6}{'REST':>8}{'INJ':>5}"
               f"{'FIT':>7}{'VIA':>10}"
               f"{'OPPZ':>6}  {'OPP':>4}{'LG#':>5}{'KEEP':>6}{'PACE':>6}"
               + (f"{'ROLE':>6}" if show_role else ''))
        print(hdr)
        print('-' * len(hdr))
        if not rs:
            print('  nothing clears tonight')
        for i, x in enumerate(rs, 1):
            nm = ('-' if x['now_mpg'] is None else f"{x['now_mpg']:.1f}")
            ng = ('-' if x['now_g'] is None else str(x['now_g']))
            dr = ('-' if x['drift'] is None else f"x{x['drift']:.2f}")
            kp = ('-' if x.get('opp_keep') is None
                  else f"{x['opp_keep']}/{x['opp_keep_of']}")
            tail = (f"{(x['role'] if x['role'] is not None else 0):>6.2f}"
                    if show_role else '')
            print(f"{i:>3} {x['player'][:20]:21}{x['team']:>4}{x['pos']:>4}"
                  f"{x['poss']:>6.1f}{x['mpg']:>6.1f}{nm:>6}{ng:>3}"
                  f"{dr:>6}{(x.get('rest') or '?'):>8}"
                  f"{(x.get('inj') or '-'):>5}"
                  f"{x['fit']:>+7.2f}"
                  f"{ZN.get(x['via'], x['via']):>10}{x['via_z']:>+6.2f}  "
                  f"{x['opp']:>4}{x['opp_rank']:>5}{kp:>6}"
                  f"{x['pace']:>6.1f}{tail}")
        print(note)

    table(f"OVERS -- {day}, top {a.top}", overs[:a.top],
          f"""
  Sorted by FIT: how soft the defence is in the zones he actually
  shoots from, weighted by how much each zone repeats season to
  season. VIA names the zone carrying the row and OPPZ how soft that
  defence is there, so you can disagree with a specific claim rather
  than a number. POSS is possessions he finishes per game, floor
  {a.min_poss:.0f}. NOW is this season's minutes over G games -- a dash there
  means he has not played. KEEP is how many of that defence's top
  eight by minutes are still on it: below 5 and the zone number
  describes a roster that has mostly gone.""")

    table(f"UNDERS -- {day}, top {a.top}", unders[:a.top],
          f"""
  The mirror: the defence is STRONG where he shoots, and his role was
  not guaranteed last season. ROLE is starts over appearances -- below
  {a.max_role:.2f} by filter, because a locked-in starter plays 34 minutes
  on a bad night and clears his number anyway. Possessions between
  {a.under_poss_lo:.0f} and {a.under_poss_hi:.0f}: rotation players, not stars.""",
          show_role=True)

    seen_m = []
    for x in sorted(missing, key=lambda r: -r['poss']):
        tag = f"{x['player']} ({x['team']})"
        if tag not in seen_m:
            seen_m.append(tag)
    # NOT AN INJURY LIST, and it has to say so.
    #
    # The old heading read "Held out, team has played and they have
    # not", which is accurate and was still misread -- as a report
    # that these men are out tonight. They are simply absent from this
    # season's box scores, which can mean injury, a trade, a two-way
    # assignment or a name this cache spells differently. Reading it
    # as an injury report put "Coby White is out" into a pick
    # rationale when he was neither injured nor on the team in
    # question. The ESPN feed, printed above under "ruled OUT", is the
    # only line here that means someone is not playing.
    miss_note = ('' if not seen_m else
                 '\n  NO GAMES THIS SEASON (not an injury report -- could be '
                 'a trade, a\n  two-way, or a name spelt differently here; '
                 'the ruled OUT list above\n  is the injury feed): '
                 + '; '.join(seen_m[:8]))
    tonight_teams = sorted({t for g in games for t in g})

    def listing(tag):
        by = {}
        for v in inj.values():
            if inj_tag(v) != tag or v.get('team') not in tonight_teams:
                continue
            by.setdefault(v['team'], []).append(v)
        return by

    def show(by, heading):
        if not by:
            return
        print(heading)
        for t in sorted(by):
            who = '; '.join(
                f"{v['name']}"
                + (f" ({v['detail']})" if v.get('detail') else '')
                + (f", back {v['back'][:10]}" if v.get('back') else '')
                for v in by[t][:5])
            print(f'    {t:4} {who}')

    show(listing('OUT'),
         '\n  OUT tonight -- their possessions go somewhere, and the role '
         'column\n  will not catch it for several games:')
    # A game-time decision is a conditional role change for everyone
    # else on that roster. Daniss Jenkins ranked here on a 21.5-minute
    # baseline while being the man who starts, and plays 38, whenever
    # Ausar Thompson sits -- and Thompson was GTD, not OUT, so the
    # list above would never have mentioned him.
    show(listing('GTD'),
         '\n  GAME-TIME DECISIONS -- if one of these sits, his minutes move '
         'to a\n  team-mate who may be ranked above on a smaller role than '
         'he will have:')

    print(f"""
  {len(games)} game(s), {len(live)} players scored: {len(overs)} favourable, {len(unders)} unfavourable.
  Filters: KEEP >= {a.min_keep}, |FIT| >= {a.min_fit:.2f}, possessions >= {a.min_poss:.0f}.
  INJ is ESPN's status: OUT rows are removed (--allow-out keeps them),
  GTD is a game-time decision and is shown, not dropped.
  REST is his own team's spot: B2B is the second night of a back-to-back,
  NinN is games in the last five days. Reported, not modelled -- no
  coefficient for it has been measured yet. --no-b2b filters on it.
  An empty list is an answer. Set --min-keep 0 --min-fit 0 to see what was cut.
{miss_note}

  NO PRICE WAS CONSULTED. This is a prediction board, not a card --
  what the matchup says, before the market gets a vote. Grade it
  against the box score and we find out whether the read is worth
  anything; only then does a line matter.""")
    return 0


def attack(defence, day, base, out_by_team, a, W):
    """Rank the opposing roster against one defence's specific softness.

    The ranking above says WHICH defence. This says who is placed to
    punish it, and the two are not the same question: Utah conceding
    threes at volume and Washington being beaten at the rim want
    different players, and a screen that ranked both rosters by raw
    usage would hand back the same names either way.

    POSS is the honest version of "enough possessions": team pace
    times his share of the floor times his usage while on it, which
    comes out as possessions he personally finishes per game. Touch
    and time-of-possession data is NBA.com tracking and that host
    answers 403, so this is the closest thing the cache supports.

    FIT weights the defence's softness by zone against where this
    player's shots actually come from -- rim, 3-10 ft, midrange,
    three -- with each zone weighted by how much it persists year to
    year. Midrange barely persists, so a midrange-heavy player earns
    almost nothing from a soft defence, which is the correct answer
    and used to be this function's worst false positive.

    Without the player shooting cache it falls back to the two/three
    split and says so in the output, because that version cannot tell
    a rim attacker from a midrange wing.
    """
    games = slate(day)
    opp = None
    for away, home in games:
        if defence == away:
            opp = home
        elif defence == home:
            opp = away
        if opp:
            break
    if not opp:
        print(f'{defence} is not playing on {day}', file=sys.stderr)
        return 1

    d = out_by_team.get(defence)
    if not d:
        print(f'no defensive profile for {defence}', file=sys.stderr)
        return 1
    # A possession is shared, so the game's pace is the average.
    pace = statistics.fmean([p for p in (
        (base.get(defence) or {}).get('pace'),
        (base.get(opp) or {}).get('pace')) if p]) or 100.0

    now = this_season()
    shoot = player_shooting()
    zw = {k: W[k] for k in ('rim', 'short', 'mid', 'three')}
    zwsum = sum(zw.values()) or 1.0
    rows_ = player_rows(opp, d, pace, zw, zwsum, now, shoot)

    rows_ = [x for x in rows_ if x['poss'] >= a.min_poss
             and not x['moved_to']]
    rows_.sort(key=lambda x: -x['fit'])
    if a.json:
        print(json.dumps({'defence': defence, 'opponent': opp,
                          'pace': round(pace, 1), 'players': rows_}, indent=1))
        return 0

    # Only zones that carry weight are named. Describing a defence as
    # soft in midrange when midrange is weighted at zero would be
    # reporting a weakness the ranking deliberately ignores.
    why = []
    for z, label in (('rim', 'gives up the rim'),
                     ('short', 'gives up the 3-10 ft floater'),
                     ('mid', 'gives up midrange'),
                     ('three', 'gives up threes')):
        if d.get(z, 0) > 0.3 and zw.get(z, 0) > 0.15:
            why.append(f'{label} ({d[z]:+.2f})')
    if d['pace_z'] > 0.3 and W.get('pace', 0) > 0.15:
        why.append(f"plays fast ({d['pace_z']:+.2f})")
    if not why:
        why.append('is not soft anywhere that persists -- low expectations')
    # Equal weights are the fallback, not a measurement, and they
    # change the ranking -- say so rather than printing 1.00 under a
    # sentence that explains what a low midrange weight means.
    flat = all(abs(v - 1.0) < 1e-9 for v in zw.values())
    wnote = ('\n  Those are NOT measured: no multi-season history is '
             'cached, so every\n  zone counts the same and FIT is '
             'weaker than it should be. Fix with:\n'
             '    python3 defense.py --build --history 4'
             if flat else
             '\n  A low midrange weight is the measurement saying midrange '
             'defence is\n  mostly noise, so a midrange-heavy player earns '
             'little from a soft\n  defence -- which is right, and used to '
             'be the worst false positive here.')
    keep = ('' if d.get('keep') is None
            else f', KEEP {d["keep"]}/{d["keep_of"]}')
    print(f'\nATTACK {defence} WITH {opp} -- {defence} {", ".join(why)}; '
          f'league rank {d["rank"]}/30{keep}')
    hdr = (f"{'PLAYER':21}{'POS':>4}{'USG':>6}{'MPG':>6}{'POSS':>6}  "
           f"{'RIM':>5}{'3-10':>5}{'MID':>5}{'3PT':>5}  {'PTS/36':>7}"
           f"{'AST/36':>7}{'FIT':>7}{'NOW':>6}{'G':>3}{'SRC':>7}")
    print(hdr)
    print('-' * len(hdr))
    if not rows_:
        print(f'  nobody on {opp} clears {a.min_poss} possessions per game')
    for x in rows_[:a.top]:
        nm = ('-' if x['now_mpg'] is None else f"{x['now_mpg']:.1f}")
        ng = ('-' if x['now_g'] is None else str(x['now_g']))
        print(f"{x['player'][:20]:21}{x['pos']:>4}{x['usg']:>6.1f}"
              f"{x['mpg']:>6.1f}{x['poss']:>6.1f}  "
              f"{x['rim_share']*100:>4.0f}%{x['short_share']*100:>4.0f}%"
              f"{x['mid_share']*100:>4.0f}%{x['three_share']*100:>4.0f}%  "
              f"{(x['pts_36'] or 0):>7.1f}{(x['ast_36'] or 0):>7.1f}"
              f"{x['fit']:>+7.2f}{nm:>6}{ng:>3}{x['src']:>7}")
    print(f"""
  POSS is possessions he finishes per game: game pace ({pace:.1f}) x his
  share of the floor x his usage while on it. That is the possession
  threshold, computed rather than eyeballed -- touch data is NBA.com
  tracking and that host is blocked.

  RIM/3-10/MID/3PT are shares of his own attempts by zone. FIT scores
  those against {defence}'s softness in each, weighted by how much
  each zone persists season to season: rim {zw['rim']:.2f}, 3-10 {zw['short']:.2f}, mid
  {zw['mid']:.2f}, three {zw['three']:.2f}.{wnote}

  SRC says zones when the shot-location cache covered him. 'split'
  means it did not and everything inside the arc was charged to 3-10:
  that row cannot tell a rim attacker from a midrange wing, so treat
  its FIT as provisional.

  USG and MPG are last season. NOW is this season's minutes over G
  games; a blank NOW with games played elsewhere means he moved and
  the row is dropped. A blank with no games is injured or yet to play
  -- check before betting the rate.""")
    return 0


def zs(vals):
    ok = [v for v in vals if v is not None]
    if len(ok) < 3:
        return lambda v: 0.0
    mu = statistics.fmean(ok)
    sd = statistics.pstdev(ok) or 1.0
    return lambda v: 0.0 if v is None else (v - mu) / sd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--discover', action='store_true',
                    help='report real table ids and column names, resolve '
                         'the column map, and change nothing else')
    ap.add_argument('--build', action='store_true', help='cache the tables')
    ap.add_argument('--history', type=int, default=0, metavar='N',
                    help='also cache N seasons before the baseline, so the '
                         'component weights can be measured from how much '
                         'each one actually persists year to year')
    ap.add_argument('--team', help='one defence in detail')
    ap.add_argument('--totals', nargs='?', const='today', default=None,
                    metavar='YYYYMMDD',
                    help='rank tonight\'s games by the total their '
                         'profiles imply, with pace, shot diet and the '
                         'aggregated player matchups broken out')
    ap.add_argument('--board', nargs='?', const='today', default=None,
                    metavar='YYYYMMDD',
                    help='every player in every game tonight, split into '
                         'overs and unders. Ignores price entirely')
    ap.add_argument('--under-poss-lo', type=float, default=6.0,
                    help='--board unders: possession floor')
    ap.add_argument('--under-poss-hi', type=float, default=16.0,
                    help='--board unders: possession ceiling, above which '
                         'he is a starter and plays through a bad night')
    ap.add_argument('--drift-lo', type=float, default=0.75,
                    help='--board overs: drop a player whose minutes have '
                         'fallen below this fraction of last season -- he '
                         'cannot reach an over from a role he lost')
    ap.add_argument('--drift-hi', type=float, default=1.25,
                    help='--board unders: drop a player whose minutes have '
                         'grown past this multiple -- he is no longer the '
                         'unreliable rotation piece the under relies on')
    ap.add_argument('--max-role', type=float, default=0.70,
                    help='--board unders: starts/appearances at or below '
                         'this counts as a role that is not guaranteed')
    ap.add_argument('--attack', metavar='TEAM',
                    help='rank the opposing roster against this defence, '
                         'matched to the weakness it actually has')
    ap.add_argument('--day', help='YYYYMMDD for --attack, default today')
    ap.add_argument('--allow-out', action='store_true',
                    help='keep players ESPN lists as OUT. Off by default')
    ap.add_argument('--refresh-injuries', action='store_true',
                    help='re-fetch the injury feed instead of using the '
                         'cached copy (30 minutes old at most)')
    ap.add_argument('--no-b2b', action='store_true',
                    help='drop players whose own team is on the second '
                         'night of a back-to-back. Off by default: the '
                         'effect is real but its size here is unmeasured, '
                         'so the spot is shown rather than acted on')
    # 0.05 was set against the OLD compressed FIT, where the whole
    # league ran -0.07 to +0.07 -- so it cut at about 0.012 SD of
    # defensive softness, which is nothing. On the corrected scale
    # the same intent is roughly 0.20 SD. Rescaled by the measured
    # 4.2x compression rather than re-guessed.
    ap.add_argument('--min-fit', type=float, default=0.21,
                    help='drop rows whose matchup score is smaller than '
                         'this either way. A hand-set floor, not a '
                         'measurement: it is where the numbers stopped '
                         'carrying an argument I was willing to make. '
                         '--min-fit 0 shows everything')
    ap.add_argument('--min-poss', type=float, default=14.0,
                    help='possessions finished per game a player must '
                         'clear to be listed by --attack')
    ap.add_argument('--tonight', nargs='?', const='today', default=None,
                    metavar='YYYYMMDD',
                    help='only defences playing that day, shown per game '
                         'with the side to attack named')
    ap.add_argument('--top', type=int, default=12, help='rows to print')
    # Default None, not 1.0: unset means "use the measured persistence",
    # and an explicit number overrides it. Defaulting to 1.0 made the
    # equal weighting invisible and unfalsifiable.
    ap.add_argument('--w-rim', type=float, default=None,
                    help='weight on rim concession (default: measured)')
    ap.add_argument('--w-short', type=float, default=None,
                    help='weight on 3-10 ft concession (default: measured)')
    ap.add_argument('--w-mid', type=float, default=None,
                    help='weight on midrange concession (default: measured, '
                         'and it measures low)')
    ap.add_argument('--w-three', type=float, default=None,
                    help='weight on three-point concession')
    ap.add_argument('--w-pace', type=float, default=None,
                    help='weight on pace (volume)')
    ap.add_argument('--w-rtg', type=float, default=None,
                    help='weight on overall defensive rating')
    ap.add_argument('--min-keep', type=int, default=5,
                    help='drop any defence retaining fewer than this many '
                         'of last season\'s top-8 minutes players. Applies '
                         'to --tonight and --board. Defaults to 5, which '
                         'is where last season stops describing the team; '
                         '--min-keep 0 shows everything')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    os.makedirs(DATA, exist_ok=True)
    if a.discover:
        return discover()
    if a.build:
        seasons = [BASE_SEASON - i for i in range(a.history, 0, -1)]
        seasons += [BASE_SEASON, NOW_SEASON]
        return build(tuple(seasons))

    p = os.path.join(DATA, 'defense.json')
    if not os.path.exists(p):
        print('no cache. run:  python3 defense.py --discover  then --build',
              file=sys.stderr)
        return 1
    blob = json.load(open(p))
    base = blob.get(str(BASE_SEASON)) or {}
    now = blob.get(str(NOW_SEASON)) or {}
    if not base:
        print(f'no {BASE_SEASON} data in the cache', file=sys.stderr)
        return 1
    keep = continuity()

    teams = sorted(base)
    pers = blob.get('_weights') or {}

    def zf(key):
        return zs([base[t].get(key) for t in teams])

    zc = {k: zf(k) for k in
          ['drtg', 'pace', 'opp_fg3p', 'opp_fg3a']
          + [f'{z}_{s}' for z in ZONES for s in ('pct', 'share')]}

    def season_mix(t):
        """(weight on this season, games played) for one team."""
        nb = now.get(t) or {}
        g = nb.get('games') or 0
        if not g:
            return 0.0, 0
        return g / (g + ZONE_PRIOR_GAMES), g

    def blended(t, col):
        """One raw column, last season and this, mixed by games played.

        Both seasons are z-scored against the SAME base-season league
        spread before mixing. z-scoring this season against its own
        four-game spread would inflate it -- that spread is mostly
        sampling noise, as the reliability table showed -- and mixing
        an inflated z with an honest one is worse than using either
        alone.
        """
        b = base.get(t) or {}
        nb = now.get(t) or {}
        bv, nv = b.get(col), nb.get(col)
        if bv is None:
            return None if nv is None else zc[col](nv)
        if nv is None:
            return zc[col](bv)
        w, _g = season_mix(t)
        return (1 - w) * zc[col](bv) + w * zc[col](nv)

    def zone_blend(b, *cols, team=None):
        """One zone's z-score, its parts weighted by their persistence.

        Averaging percentage and share 50/50 was wrong. Measured over
        four seasons, the SHARE a defence concedes repeats far better
        than the PERCENTAGE it allows, in every zone without
        exception: rim .76 vs .56, 3-10 ft .83 vs .64, midrange .54
        vs .21, threes .46 vs .12. A defence controls where you shoot
        from, not whether it drops.

        The equal average therefore diluted signal with noise
        everywhere, and worst on threes, where opponent 3P% at +0.12
        is essentially nothing and was carrying half the component.
        """
        num_ = den = 0.0
        for c in cols:
            w = pers.get(c)
            w = 1.0 if w is None else max(0.0, w)
            # team given -> mix last season with this one; otherwise
            # last season alone, which is what every caller outside
            # the board table still wants.
            v = (blended(team, c) if team is not None
                 else zc[c](b.get(c)))
            if v is None:
                continue
            num_ += w * v
            den += w
        return num_ / den if den else 0.0

    def pw(*keys):
        """How much a blended zone carries, for weighting it against
        the other zones.

        The self-weighted mean sum(w^2)/sum(w) rather than the plain
        mean, because zone_blend already leans the composite toward
        its most persistent part -- so the composite inherits more
        persistence than a flat average of the parts would suggest.
        Threes come out at .39 this way against .29 flat, which is
        the honest number now that the 3P% half barely counts.

        Negative correlation is a component anti-predicting itself,
        which is noise and not an inverted signal, so it floors at
        zero and that component stops voting.
        """
        vs = [max(0.0, pers[k]) for k in keys if pers.get(k) is not None]
        if not vs:
            return 1.0
        tot = sum(vs)
        return (sum(v * v for v in vs) / tot) if tot else 0.0

    W = {
        'rim':   a.w_rim if a.w_rim is not None
        else pw('rim_share'),
        'short': a.w_short if a.w_short is not None
        else pw('short_share'),
        'mid':   a.w_mid if a.w_mid is not None
        else pw('mid1_share', 'mid2_share'),
        'three': a.w_three if a.w_three is not None
        else pw('opp_fg3a'),
        'pace':  a.w_pace if a.w_pace is not None else pw('pace'),
        'rtg':   a.w_rtg if a.w_rtg is not None else pw('drtg'),
    }
    measured = bool(pers) and all(
        getattr(a, f'w_{k}') is None for k in W)

    out = []
    for t in teams:
        b = base[t]
        # FREQUENCY ONLY. The percentage a defence allows barely
        # persists -- measured here at rim .56, 3-10 .64, midrange
        # .21, three .12 -- and the three-point figure in particular
        # is close to a coin: The Power Rank found opponent 3P% in one
        # season explains under 3% of the variance in the next.
        #
        # Franks, Miller, Bornn and Goldsberry (AoAS 2015) separate a
        # defender's effect on shot FREQUENCY from shot EFFICIENCY,
        # and frequency is the half that holds. Our own persistence
        # numbers say the same: shares .76 and .83 against those
        # percentages. The persistence weighting already downweighted
        # the percentages, but downweighted noise is still noise
        # sitting in the average, and it was diluting the half that
        # works.
        comp = {
            'rim': zone_blend(b, 'rim_share', team=t),
            'short': zone_blend(b, 'short_share', team=t),
            'mid': zone_blend(b, 'mid1_share', 'mid2_share', team=t),
            'three': zone_blend(b, 'opp_fg3a', team=t),
            # pace and defensive rating blend too. These are the two
            # that move most in an offseason -- a new coach changes
            # pace in week one -- and leaving them on last season
            # while the zones blended would have been the worst of
            # both: a profile half-updated without saying which half.
            'pace': (blended(t, 'pace')
                     if blended(t, 'pace') is not None
                     else zc['pace'](b.get('pace'))),
            'rtg': (blended(t, 'drtg')
                    if blended(t, 'drtg') is not None
                    else zc['drtg'](b.get('drtg'))),
        }
        # Kept separately so --team can answer "is this team's number
        # volume or percentage?" -- a zone driven by the percentage
        # half is mostly last season's luck.
        parts = {f'{k}_z': round(zc[k](b.get(k)), 2) for k in
                 ('rim_pct', 'rim_share', 'short_pct', 'short_share',
                  'opp_fg3p', 'opp_fg3a')}
        wsum = sum(W.values())
        score = sum(W[k] * comp[k] for k in W) / (wsum or 1)
        rim, three = comp['rim'], comp['three']
        pace, rtg = comp['pace'], comp['rtg']
        nb = now.get(t) or {}
        # Unadjusted on both sides of the subtraction. Comparing this
        # season's adjusted number to last season's compares a prior
        # to a measurement, which is what produced 110.2 across the
        # board; and comparing raw to adjusted would fold the schedule
        # correction into the difference.
        now_d = nb.get('drtg_raw')
        base_d = b.get('drtg_raw') if b.get('drtg_raw') is not None \
            else b.get('drtg')
        drift = (None if (now_d is None or base_d is None)
                 else now_d - base_d)
        k, of = keep.get(t, (None, None))
        out.append({'team': t, 'score': round(score, 2),
                    'rim': round(rim, 2), 'three': round(three, 2),
                    'short': round(comp['short'], 2),
                    'mid': round(comp['mid'], 2),
                    'pace_z': round(pace, 2), 'rtg_z': round(rtg, 2),
                    'drtg': b.get('drtg'), 'pace': b.get('pace'),
                    'rim_pct': b.get('rim_pct'), 'rim_share': b.get('rim_share'),
                    'opp_fg3p': b.get('opp_fg3p'), 'opp_fg3a': b.get('opp_fg3a'),
                    'opp_pts': b.get('opp_pts'),
                    'now_drtg': now_d, 'now_drtg_adj': nb.get('drtg'),
                    'now_games': nb.get('games'), 'drift': drift,
                    # how much of this row's profile is THIS season
                    'w_now': round(season_mix(t)[0], 3),
                    'keep': k, 'keep_of': of, **parts})
    out.sort(key=lambda x: -x['score'])
    for i, x in enumerate(out, 1):
        x['rank'] = i

    if a.totals:
        day = (a.totals if a.totals != 'today' else
               a.day or datetime.datetime.now()
               .astimezone().strftime('%Y%m%d'))
        return totals(day, base, {x['team']: x for x in out}, a, W)

    if a.board:
        day = (a.board if a.board != 'today' else
               a.day or datetime.datetime.now()
               .astimezone().strftime('%Y%m%d'))
        return board(day, base, {x['team']: x for x in out}, a, W)

    if a.attack:
        day = a.day or datetime.datetime.now() \
            .astimezone().strftime('%Y%m%d')
        return attack(code(a.attack), day, base,
                      {x['team']: x for x in out}, a, W)

    if a.team:
        t = code(a.team)
        x = next((r for r in out if r['team'] == t), None)
        if not x:
            print(f'no data for {t}', file=sys.stderr)
            return 1
        print(json.dumps(x, indent=1))
        return 0

    rows_ = [x for x in out
             if a.min_keep <= 0 or (x['keep'] or 0) >= a.min_keep]

    if a.tonight:
        day = (a.tonight if a.tonight != 'today' else
               datetime.datetime.now()
               .astimezone().strftime('%Y%m%d'))
        games = slate(day)
        if not games:
            print(f'no games found for {day}', file=sys.stderr)
            return 1
        by = {x['team']: x for x in out}
        pairs = []
        for away, home in games:
            for defender, attacker in ((away, home), (home, away)):
                x = by.get(defender)
                if x:
                    pairs.append((x['score'], defender, attacker,
                                  f'{away}@{home}', x))
        pairs.sort(reverse=True)
        if a.json:
            print(json.dumps([{'attack': d, 'with': o, 'game': g, **x}
                              for _, d, o, g, x in pairs], indent=1))
            return 0
        print(f'\nATTACK THESE DEFENCES -- {day}, {len(games)} games')
        hdr = (f"{'#':>3} {'GAME':10}{'DEF':>5}{'LG#':>5}{'SCORE':>7}"
               f"{'RIM':>7}{'3PT':>7}{'PACE':>7}{'RTG':>7}  {'DRTG':>6}"
               f"{'NOW':>7}{'G':>3}{'DRIFT':>7}{'KEEP':>6}  ATTACK WITH")
        print(hdr)
        print('-' * len(hdr))
        for i, (_, deff, atk, game, x) in enumerate(pairs, 1):
            kp = ('-' if x['keep'] is None else f"{x['keep']}/{x['keep_of']}")
            dr = ('-' if x['drift'] is None else f"{x['drift']:+.1f}")
            nd = ('-' if x['now_drtg'] is None else f"{x['now_drtg']:.1f}")
            ng = ('-' if x['now_games'] is None else f"{int(x['now_games'])}")
            print(f"{i:>3} {game:10}{deff:>5}{x['rank']:>5}{x['score']:>+7.2f}"
                  f"{x['rim']:>+7.2f}{x['three']:>+7.2f}{x['pace_z']:>+7.2f}"
                  f"{x['rtg_z']:>+7.2f}  {x['drtg']:>6.1f}{nd:>7}{ng:>3}"
                  f"{dr:>7}{kp:>6}  {atk} possessions")
        print("""
  Both sides of every game, worst defence first. DEF is who to attack,
  LG# its rank among all 30, ATTACK WITH the roster to pick from --
  which is where your possession data comes in.

  NOW is this season's UNADJUSTED defensive rating over G games, and
  DRIFT is NOW minus the same unadjusted number last season. At three
  or four games DRIFT is mostly schedule: it wants about fifteen
  before it says anything, and until then the ranking is carried by
  last season plus KEEP.""")
        return 0

    if a.json:
        print(json.dumps(rows_, indent=1))
        return 0

    print(f'\nDEFENCES WORTH ATTACKING -- {BASE_SEASON-1}-'
          f'{str(BASE_SEASON)[2:]} profile, {NOW_SEASON-1}-'
          f'{str(NOW_SEASON)[2:]} sample alongside')
    ws = '  '.join(f'{k} {v:.2f}' for k, v in W.items())
    if measured:
        yrs = blob.get('_weight_seasons') or []
        print(f'  weights from year-over-year persistence'
              f'{f" ({yrs[0]}-{yrs[-1]})" if yrs else ""}: {ws}')
    else:
        print(f'  weights: {ws}'
              + ('' if pers else '  [equal -- no history cached, '
                                 'try --build --history 4]'))
    hdr = (f"{'#':>3} {'TM':4}{'SCORE':>7}{'RIM':>7}{'3PT':>7}{'PACE':>7}"
           f"{'RTG':>7}  {'DRTG':>6}{'PACE':>7}{'RIM%':>7}{'3P%':>7}"
           f"{'3PA':>6}{'OPPP':>7}{'NOW':>7}{'G':>3}{'DRIFT':>7}{'KEEP':>6}")
    print(hdr)
    print('-' * len(hdr))
    for x in rows_[:a.top]:
        def f(v, w=7, d=1, pct=False):
            if v is None:
                return f"{'-':>{w}}"
            return f"{v*100:>{w}.{d}f}" if pct else f"{v:>{w}.{d}f}"
        kp = ('-' if x['keep'] is None else f"{x['keep']}/{x['keep_of']}")
        dr = ('-' if x['drift'] is None else f"{x['drift']:+.1f}")
        ng = ('-' if x['now_games'] is None else f"{int(x['now_games'])}")
        print(f"{x['rank']:>3} {x['team']:4}{x['score']:>+7.2f}"
              f"{x['rim']:>+7.2f}{x['three']:>+7.2f}{x['pace_z']:>+7.2f}"
              f"{x['rtg_z']:>+7.2f}  {f(x['drtg'], 6)}{f(x['pace'])}"
              f"{f(x['rim_pct'], 7, 1, True)}{f(x['opp_fg3p'], 7, 1, True)}"
              f"{f(x['opp_fg3a'], 6)}{f(x['opp_pts'])}"
              f"{f(x['now_drtg'])}{ng:>3}{dr:>7}{kp:>6}")

    print("""
  SCORE and the z-columns are league-relative; positive means a softer
  defence. Each zone blends the opponent FG% allowed there with the
  share of attempts conceded -- a defence can be bad at the rim by
  surrendering good looks or by surrendering many. PACE is
  possessions, the volume multiplier rather than a weakness.

  SCORE weights each component by its own year-over-year persistence
  rather than equally, so a component that does not repeat stops
  voting. --team TM prints all four zones including 3-10 ft and
  midrange; --w-rim and friends override a weight by hand.

  The raw columns are last season. NOW is this season's UNADJUSTED
  defensive rating over G games and DRIFT is the change against the
  same unadjusted number -- adjusted is nearly all prior this early
  and reported the league average for almost every team. Even
  unadjusted, DRIFT at three or four games is mostly schedule; it
  wants about fifteen before it carries information.

  KEEP is how many of last season's top eight by minutes have appeared
  for that team this season. A soft defence at 8/8 has no personnel
  reason to firm up. A low KEEP means the data cannot tell you, not
  that the defence improved -- that one is yours and the film's.

  Nothing here is a pick and no line was consulted. Take the ranking,
  apply possession data to find who on the other side actually absorbs
  the usage, and price it yourself.""")
    return 0


if __name__ == '__main__':
    sys.exit(main())
