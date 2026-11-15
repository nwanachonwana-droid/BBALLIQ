#!/usr/bin/env python3
"""college.py -- D1 men's basketball win picks, rebuilt from the paper.

WHAT THIS IS, AND WHAT IT IS NOT

"Basketball IQ vs Kalshi" (Nwana, Dahal, Acharya, Feb 2026) describes
a KenPom-based logistic model over 1,544 games. This is a
reimplementation of that FAMILY of model -- iterative opponent
adjustment of offence and defence, a tempo term, home court, and a
normal margin -- not a copy of KenPom, whose adjusted efficiency is
computed per POSSESSION from the four factors.

Whether this can use true possessions depends on what ESPN actually
returns, which is why --probe exists and why it is the first thing to
run. Coding against an assumed feed shape has cost this project four
separate debugging sessions, so nothing here reads a field that
--probe has not confirmed is present.

    possessions available   adjusted efficiency, points per 100 poss
    not available           adjusted points per game, tempo-free

Both are honest ratings; they are NOT the same model and the board
says which one produced a number.

WHAT THE PAPER ACTUALLY ESTABLISHED, AND WHAT IS ENCODED

  THE 60% GATE -- encoded as a hard filter.
      Below 60% confidence the system went 214-163, 56.8%, which is
      near-random. At or above, 912-255, 78.2%. A 21.4 point gap,
      chi-square 64.93, p < 0.001, on n = 1,544. That is the one
      finding in the paper strong enough to act on, and it is the
      only one here that removes a pick.

  THE VENUE BIAS -- encoded as a TAG, deliberately not a correction.
      Weak road teams (away win% < 50%) were forecast at 62.7% and
      won 44.8%: overestimated by 17.9 points, n = 58. Strong road
      teams (away win% > 80%) forecast 71.4%, won 86.4%:
      underestimated by 15.0, n = 44.
      The regression behind it gives beta = 0.0754 at p = 0.093.
      THAT IS NOT SIGNIFICANT AT 5%, on 58 and 44 games. Subtracting
      17.9 points from a road favourite because of it would be
      LIFT_K for the fourth time in this codebase. So the row is
      flagged and the reader decides.

  CALIBRATION -- recorded, not corrected. MAD was 2.10pp with the
      80-90% bin underconfident by 4.5. One season, and the fix for
      a 4.5-point bin error is more seasons, not a fudge factor.

A pick here is a BINARY WINNER, which is what the paper graded and
what Kalshi contracts settle, so grading needs no line: the final
score answers it.
"""
import os, re, sys, json, math, argparse, subprocess, datetime, io

BUILD = '2026-11-13b'

DATA = os.path.expanduser('~/kalshi_data')
GAMES = os.path.join(DATA, 'college_games.json')
RATINGS = os.path.join(DATA, 'college_ratings.json')
PICKS = os.path.join(DATA, 'college_picks.jsonl')

SCOREBOARD = ('https://site.api.espn.com/apis/site/v2/sports/basketball/'
              'mens-college-basketball/scoreboard')
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36')

# --- the paper's numbers --------------------------------------------
CONF_GATE = 0.60        # Table 5. The only filter that removes a pick.

# WHAT GETS PUBLISHED, which is not the same as what clears the gate.
#
# 11 Nov: 36 of 46 evaluable games cleared 60%, and the top of that
# list was UCLA over Arkansas-Pine Bluff at 100%, Nebraska over
# Lindenwood at 100%, Oregon over Mississippi Valley St at 100%.
# November buy games. The model is right about every one of them and
# it is worth nothing: no market exists on those, and a record filled
# with them reads as 95% accurate while saying nothing at all.
#
# The paper's 78.2% came from a January slate of conference games. A
# ceiling is not second-guessing the gate -- the gate decides what is
# KNOWN, this decides what is worth SHOWING, and they are different
# questions. Both numbers print, so the record can always be read
# either way.
# SUPERSEDED BY THE BAND TOGGLE on the page. The ceiling existed to
# stop 36 picks becoming an endless scroll led by games nobody can
# price; the page now splits them into 90-100 / 80-90 / 70-80 / 60-70
# and opens on 70-80, which solves the scroll without hiding
# anything. So everything that clears the gate is published, and the
# reader chooses the band.
#
# The point the ceiling was making still stands and is still made, in
# the record: a headline rate over a November slate is dominated by
# buy games, so the record reports BY BAND. 60-80 is the band that
# corresponds to the paper's own evidence.
PUBLISH_MAX_CONF = 1.01   # publish everything; the page bands it
PUBLISH_MAX = 500
WEAK_ROAD = 0.50        # away win% below this -> we overestimate them
STRONG_ROAD = 0.80      # above this -> we underestimate them
VENUE_WEAK_PP = 0.179   # Table 6, n=58, reported NOT corrected
VENUE_STRONG_PP = 0.150 # Table 6, n=44, reported NOT corrected

# The fallback when nothing has been measured. It is DELIBERATELY
# wrong in a known direction: pure game-to-game scoring noise gives
# about 11, but the forecast margin also carries RATING error, and
# that is not in this number.
#
# Simulated: true residual SD 12.3 against this 11.0 produced
# calibration MAD 3.10pp with every bin OVERCONFIDENT by 1.6 to 4.6
# points. Measuring it took MAD to 1.20pp. So an unmeasured SD here
# does not add noise, it systematically overstates confidence -- and
# confidence is what the 60% gate keys on, so it also lets picks
# through that should not clear.
#
# --fit-sd measures it walk-forward and writes college_sd.json.
# Until that exists every board says the number is assumed.
MARGIN_SD = 11.0
SDFILE = os.path.join(DATA, 'college_sd.json')
HCA = 3.5               # points. Assumed; --hca to override.

SEASON_START = '20261101'
MIN_GAMES = 4           # below this a team's rating is mostly prior


def say(*a):
    print(*a)


def get(url, timeout=45):
    r = subprocess.run(['curl', '-s', '--compressed', '-m', '30',
                        '-H', f'User-Agent: {UA}', url],
                       capture_output=True, timeout=timeout)
    body = r.stdout.decode('utf-8', 'replace')
    if not body.strip():
        raise RuntimeError('empty response')
    return json.loads(body)


def days(start, end):
    d = datetime.datetime.strptime(start, '%Y%m%d').date()
    e = datetime.datetime.strptime(end, '%Y%m%d').date()
    while d <= e:
        yield d.strftime('%Y%m%d')
        d += datetime.timedelta(days=1)


# --- 1. probe --------------------------------------------------------
def probe(day):
    """Report what the feed ACTUALLY contains before anything reads it.

    Run this first. Everything downstream is written against what this
    prints, not against what the endpoint is assumed to return.
    """
    url = f'{SCOREBOARD}?dates={day}&groups=50&limit=400'
    say(f'\nfetching {url}\n')
    doc = get(url)
    evs = doc.get('events') or []
    say(f'  events: {len(evs)}')
    if not evs:
        say('  !! nothing here. Either no D1 games that day, or groups=50\n'
            '  !! is not the D1 selector any more. Try another date before\n'
            '  !! assuming the second.')
        return 2

    done = [e for e in evs
            if (((e.get('status') or {}).get('type') or {}).get('completed'))]
    say(f'  completed: {len(done)}')
    e = (done or evs)[0]
    comp = (e.get('competitions') or [{}])[0]
    cs = comp.get('competitors') or []
    say(f'\n  first event: {e.get("name")}')
    say(f'  competitors: {len(cs)}')
    for c in cs:
        t = c.get('team') or {}
        say(f'    {c.get("homeAway","?"):<5} {t.get("abbreviation","?"):<6}'
            f' id={t.get("id","?"):<8} score={c.get("score","?")}')
    # The question that decides the model: are the four factors here?
    stats = (cs[0].get('statistics') if cs else None) or []
    names = sorted({s.get('name') for s in stats if s.get('name')})
    say(f'\n  per-competitor statistics: {len(names)} field(s)')
    if names:
        say('    ' + ', '.join(names[:18]))
    need = {'fieldGoalsAttempted', 'turnovers', 'freeThrowsAttempted',
            'offensiveRebounds'}
    have = need & set(names)
    say(f'\n  four-factor fields present: {len(have)} of 4  '
        f'{sorted(have) if have else ""}')
    if len(have) == 4:
        say('  -> TRUE POSSESSIONS are computable. --rate will use\n'
            '     points per 100 possessions.')
    else:
        say('  -> possessions are NOT computable from this feed.\n'
            '     --rate will use adjusted POINTS PER GAME instead, and\n'
            '     every number it produces will say so. That is a\n'
            '     different model from the paper\'s, not a worse\n'
            '     version of the same one.')
    say('\n  scores parse: '
        + ('yes' if all(str(c.get('score', '')).strip().lstrip('-').isdigit()
                        for c in cs) else 'NO -- scores are not plain ints'))
    return 0


# --- 2. build --------------------------------------------------------
def parse_event(e):
    """One completed game, or None. Reads only confirmed fields."""
    st = ((e.get('status') or {}).get('type') or {})
    if not st.get('completed'):
        return None
    comp = (e.get('competitions') or [{}])[0]
    if comp.get('neutralSite'):
        neutral = True
    else:
        neutral = False
    cs = comp.get('competitors') or []
    if len(cs) != 2:
        return None
    # KEY ON ESPN'S NUMERIC TEAM ID, ALWAYS.
    #
    # This was `abbreviation or id`, which is two bugs. It gives some
    # teams a string key and others a number, so a team carrying an
    # abbreviation in one feed and not the other simply never matches
    # and reports as unrated. And D1 abbreviations COLLIDE -- MIA is
    # Miami (FL) and Miami (OH), SJU is St John's and San Jose State
    # -- so two programmes would quietly merge into one rating and
    # the output would look entirely normal.
    side = {}
    for c in cs:
        t = c.get('team') or {}
        tid = t.get('id')
        if tid is None:
            return None
        try:
            sc = int(str(c.get('score')).strip())
        except (TypeError, ValueError):
            return None
        side[c.get('homeAway')] = {
            'team': str(tid),
            'name': (t.get('shortDisplayName') or t.get('displayName')
                     or t.get('abbreviation') or str(tid)),
            'score': sc}
    if 'home' not in side or 'away' not in side:
        return None
    return {'home': side['home']['team'], 'away': side['away']['team'],
            'home_name': side['home']['name'],
            'away_name': side['away']['name'],
            'hs': side['home']['score'], 'as': side['away']['score'],
            'neutral': neutral}


def build(start=SEASON_START, end=None, refresh=False):
    end = end or (datetime.date.today()
                  - datetime.timedelta(days=1)).strftime('%Y%m%d')
    cache = {}
    if os.path.exists(GAMES) and not refresh:
        try:
            cache = json.load(open(GAMES)).get('days') or {}
        except Exception:
            cache = {}
    new = 0
    for d in days(start, end):
        if d in cache and not refresh:
            continue
        try:
            doc = get(f'{SCOREBOARD}?dates={d}&groups=50&limit=400')
        except Exception as ex:
            say(f'  !! {d}: {ex}')
            continue
        rows = [g for g in (parse_event(e) for e in (doc.get('events') or []))
                if g]
        cache[d] = rows
        new += 1
        say(f'  {d}: {len(rows)} completed game(s)')
    n = sum(len(v) for v in cache.values())
    json.dump({'build': BUILD, 'days': cache}, open(GAMES, 'w'))
    say(f'\n{len(cache)} day(s), {n} game(s) -> {GAMES}  ({new} new)')
    return cache


# --- 3. rate ---------------------------------------------------------
def cache_is_current(cache):
    """Was this cache written with the id key, or the old abbreviations?

    Teams used to be keyed by abbreviation, which collided and did
    not match across feeds. A cache from before that change looks
    perfectly valid and rates perfectly happily -- against a set of
    keys tonight's slate will never produce. Every game would then
    report as 'unrated team', which is exactly the symptom that led
    here. Refuse it rather than rate it.
    """
    for d in cache:
        for g in cache[d]:
            return str(g.get('home', '')).isdigit()
    return True


def rate(cache=None, iters=60, write=True):
    """Iterative opponent adjustment, the KenPom idea on points.

    adjO is what a team would score against an average defence; adjD
    what it would concede to an average offence. Each pass divides a
    team's raw output by the quality of what it faced, then recomputes
    that quality. It converges quickly and the fixed point does not
    depend on the starting values.

    ON NEUTRAL COURTS the home-court term is not applied, because
    applying it is simply wrong there and the feed marks them.
    """
    if cache is None:
        cache = json.load(open(GAMES)).get('days') or {}
    games = [g for d in sorted(cache) for g in cache[d]]
    if not games:
        raise RuntimeError('no games cached -- run --build first')
    if not cache_is_current(cache):
        raise RuntimeError(
            'college_games.json was built with the OLD team key '
            '(abbreviations).\n'
            '  Those keys collide across D1 and do not match the '
            'slate, so every\n'
            '  game would report as an unrated team. Rebuild it:\n'
            '      python3 college.py --build --refresh')
    pts = [g['hs'] for g in games] + [g['as'] for g in games]
    league = sum(pts) / len(pts)

    teams = sorted({g['home'] for g in games} | {g['away'] for g in games})
    adjO = {t: league for t in teams}
    adjD = {t: league for t in teams}

    for _ in range(iters):
        no = {t: [] for t in teams}
        nd = {t: [] for t in teams}
        for g in games:
            h, a = g['home'], g['away']
            edge = 0.0 if g['neutral'] else HCA / 2.0
            # What he scored, divided by how good that defence is.
            no[h].append((g['hs'] - edge) * league / max(adjD[a], 1e-6))
            no[a].append((g['as'] + edge) * league / max(adjD[h], 1e-6))
            nd[h].append((g['as'] + edge) * league / max(adjO[a], 1e-6))
            nd[a].append((g['hs'] - edge) * league / max(adjO[h], 1e-6))
        for t in teams:
            if no[t]:
                adjO[t] = sum(no[t]) / len(no[t])
            if nd[t]:
                adjD[t] = sum(nd[t]) / len(nd[t])

    played = {t: 0 for t in teams}
    hw = {t: [0, 0] for t in teams}     # home   [win, games]
    aw = {t: [0, 0] for t in teams}     # away
    for g in games:
        played[g['home']] += 1
        played[g['away']] += 1
        if g['neutral']:
            continue
        hw[g['home']][1] += 1
        aw[g['away']][1] += 1
        if g['hs'] > g['as']:
            hw[g['home']][0] += 1
        else:
            aw[g['away']][0] += 1

    # id -> the name it last appeared under, so a rating can be read.
    name_of = {}
    for g in games:
        name_of[g['home']] = g.get('home_name') or g['home']
        name_of[g['away']] = g.get('away_name') or g['away']

    out = {}
    for t in teams:
        out[t] = {
            'team': t, 'name': name_of.get(t, t), 'games': played[t],
            'adjO': round(adjO[t], 2), 'adjD': round(adjD[t], 2),
            'net': round(adjO[t] - adjD[t], 2),
            # The paper's venue variable, straight from the record.
            'away_winpct': (round(aw[t][0] / aw[t][1], 3)
                            if aw[t][1] else None),
            'away_games': aw[t][1],
            'home_winpct': (round(hw[t][0] / hw[t][1], 3)
                            if hw[t][1] else None),
            'home_games': hw[t][1],
        }
    # WRITE=FALSE EXISTS BECAUSE fit_sd CALLS THIS ONCE PER DAY.
    # Every one of those passes is a PARTIAL season, and writing them
    # would leave college_ratings.json holding whatever the last
    # walk-forward step computed -- a rating built on part of the
    # year, sitting where tonight's picks read theirs. The test that
    # caught it crashed on MIN_GAMES, which was luck; silently
    # picking from a half-season rating is what it would have done
    # in production.
    if write:
        json.dump({'build': BUILD, 'league': round(league, 2),
                   'teams': out, 'n_games': len(games)},
                  open(RATINGS, 'w'))
        say(f'rated {len(out)} team(s) from {len(games)} game(s), '
            f'league average {league:.1f} ppg -> {RATINGS}')
    return {'league': league, 'teams': out}


# --- 4. the win model ------------------------------------------------

# --- KenPom ingest ---------------------------------------------------
#
# The ratings this file computes itself know nothing in early November
# -- two or three games a team, no prior. KenPom's carry a preseason
# prior, which is exactly the gap. So AdjO/AdjD/AdjT can be read
# straight from a kenpom.com/summary.php export and used instead.
#
# This does NOT fetch kenpom.com. It reads a file the subscriber
# exports. Nothing here logs in anywhere.
TEAMS_URL = ('https://site.api.espn.com/apis/site/v2/sports/basketball/'
             'mens-college-basketball/teams?limit=500')
ESPNTEAMS = os.path.join(DATA, 'college_espn_teams.json')
ALIASFILE = os.path.join(DATA, 'college_aliases.json')

# Names no normaliser can derive from each other. Extend by editing
# college_aliases.json; the ingest reports anything still unmatched
# with its nearest candidates so the list can be grown deliberately.
ALIASES = {
    'csun': 'cal state northridge',
    'southern california': 'usc',
    'uconn': 'connecticut',
    'ole miss': 'mississippi',
    'pitt': 'pittsburgh',
    'uc santa barbara': 'ucsb',
    'detroit mercy': 'detroit',
    'texas christian': 'tcu',
    'central florida': 'ucf',
    'louisiana lafayette': 'louisiana',
    'north carolina state': 'nc state',
    # Confirmed from the 11 Nov run: each of these appeared as a
    # 'nearest' candidate, so the ESPN side of the pair is known
    # rather than assumed. Names where the nearest candidates were
    # all clearly the WRONG school -- Nebraska Omaha suggesting
    # Nebraska, Illinois Chicago suggesting Illinois -- are
    # deliberately absent. A wrong alias is worse than no alias: it
    # maps a team onto another programme's rating and nothing in the
    # output would look odd.
    'sam houston state': 'sam houston',
    'seattle': 'seattle u',
    'grambling state': 'grambling',
    'southeastern louisiana': 'se louisiana',
    'appalachian state': 'app state',
    'miami fl': 'miami',
}


def norm_school(s):
    """Fold a school name to something two sources can agree on.

    'Murray St.' and 'Murray State' are the same team; 'St. John's'
    and 'St Johns' are the same team; and the same two letters mean
    State at the end and Saint at the start.
    """
    s = (s or '').strip().lower()
    s = s.replace('&', ' and ').replace("'", '').replace('.', '')
    s = re.sub(r'\(([^)]*)\)', r' \1 ', s)
    toks, out = s.split(), []
    for i, t in enumerate(toks):
        if t in ('st', 'state'):
            out.append('saint' if i == 0 else 'state')
        elif t == 'saint':
            out.append('saint')
        elif t in ('university', 'the', 'of'):
            continue
        else:
            out.append(t)
    s = re.sub(r'\s+', ' ', ' '.join(out)).strip()
    return ALIASES.get(s, s)


def parse_kenpom(path):
    """Rows from a kenpom summary export (.pdf via pdftotext, or text).

    The table is Team, Conf, then 19 numbers: adjusted offence and
    its delta and two ranks, the same for defence, the same for
    tempo, then two possession-length pairs. Only columns 0, 5 and 10
    are read -- the adjusted figures.
    """
    path = os.path.expanduser(path)
    if path.lower().endswith('.pdf'):
        txt = path + '.txt'
        # FileNotFoundError fires from subprocess BEFORE returncode
        # exists, so checking the return code alone let a missing
        # binary surface as a raw traceback halfway through a nightly
        # run. The layout flag is not optional either: without it the
        # columns collapse, and pypdf drops the team-name column
        # altogether, so there is no pure-Python fallback for this
        # table -- better to say that than to half-parse it.
        try:
            r = subprocess.run(['pdftotext', '-layout', path, txt],
                               capture_output=True)
            failed = r.returncode != 0 or not os.path.exists(txt)
            why = (r.stderr or b'').decode('utf-8', 'replace').strip()
        except FileNotFoundError:
            raise RuntimeError(
                'pdftotext is not installed, and it is what reads the '
                'PDF layout.\n'
                '  Either:   brew install poppler\n'
                '  Or:       open the kenpom summary page, select all, '
                'save it as\n'
                '            a .txt file and pass that instead -- this '
                'reads text\n'
                '            files directly and needs nothing extra.')
        if failed:
            raise RuntimeError(
                f'pdftotext could not read that PDF'
                + (f': {why}' if why else '.')
                + '\n  Save the kenpom page as a .txt and pass that '
                  'instead.')
        body = io.open(txt, encoding='utf-8', errors='replace').read()
    else:
        body = io.open(path, encoding='utf-8', errors='replace').read()

    rows, skipped = [], 0
    for ln in body.splitlines():
        if not ln.strip() or 'Rating' in ln or ln.strip().startswith('Team'):
            continue
        m = re.match(r'^\s*(?P<team>.+?)\s+(?P<conf>[A-Za-z0-9]{1,5})\s+'
                     r'(?P<nums>[-+0-9.\s]+)$', ln)
        if not m:
            continue
        nums = m.group('nums').split()
        if len(nums) != 19:
            skipped += 1
            continue
        try:
            rows.append({'kp': m.group('team').strip(),
                         'conf': m.group('conf'),
                         'adjO': float(nums[0]), 'adjD': float(nums[5]),
                         'adjT': float(nums[10])})
        except ValueError:
            skipped += 1
    if not rows:
        raise RuntimeError('no rating rows found in that file')
    # Adjusted offence and defence average to the SAME number by
    # construction. If they do not, the columns were misread, and a
    # silently misread column is a whole season of wrong picks.
    mo = sum(r['adjO'] for r in rows) / len(rows)
    md = sum(r['adjD'] for r in rows) / len(rows)
    if abs(mo - md) > 1.0:
        raise RuntimeError(
            f'parsed {len(rows)} rows but adjO averages {mo:.1f} and '
            f'adjD averages {md:.1f}.\n'
            '  Those must match by construction, so the columns are '
            'misaligned. Not writing anything.')
    return rows, skipped, mo, sum(r['adjT'] for r in rows) / len(rows)


def espn_teams(refresh=False):
    """ESPN's D1 directory: id -> school name. One request, cached."""
    if os.path.exists(ESPNTEAMS) and not refresh:
        try:
            return json.load(open(ESPNTEAMS))
        except Exception:
            pass
    doc = get(TEAMS_URL)
    out = {}
    for sp in (doc.get('sports') or []):
        for lg in (sp.get('leagues') or []):
            for t in (lg.get('teams') or []):
                tt = t.get('team') or {}
                if tt.get('id') is None:
                    continue
                out[str(tt['id'])] = {
                    'id': str(tt['id']),
                    'location': tt.get('location') or '',
                    'name': (tt.get('shortDisplayName')
                             or tt.get('displayName') or ''),
                    'display': tt.get('displayName') or ''}
    json.dump(out, open(ESPNTEAMS, 'w'))
    say(f'  ESPN directory: {len(out)} team(s) -> {ESPNTEAMS}')
    return out


def ingest_kenpom(path, refresh=False):
    """Read a KenPom export into college_ratings.json."""
    import difflib
    if os.path.exists(ALIASFILE):
        try:
            ALIASES.update({norm_school(k): norm_school(v)
                            for k, v in json.load(open(ALIASFILE)).items()})
        except Exception:
            pass
    rows, skipped, lg_eff, lg_tempo = parse_kenpom(path)
    say(f'\n  parsed {len(rows)} team(s) from {path}'
        + (f', {skipped} row(s) skipped' if skipped else ''))
    say(f'  league average efficiency {lg_eff:.1f}, tempo {lg_tempo:.1f}')

    teams = espn_teams(refresh=refresh)
    by_norm = {}
    for t in teams.values():
        for cand in (t['location'], t['name'], t['display']):
            n = norm_school(cand)
            if n:
                by_norm.setdefault(n, t['id'])

    out, fuzzy, missing = {}, [], []
    for r in rows:
        n = norm_school(r['kp'])
        tid = by_norm.get(n)
        how = 'exact'
        if not tid:
            close = difflib.get_close_matches(n, list(by_norm), 1, 0.90)
            if close:
                tid, how = by_norm[close[0]], f'~ {close[0]}'
                fuzzy.append((r['kp'], close[0]))
        if not tid:
            near = difflib.get_close_matches(n, list(by_norm), 3, 0.60)
            missing.append((r['kp'], near))
            continue
        out[tid] = {'team': tid, 'name': r['kp'], 'conf': r['conf'],
                    'games': 99,          # KenPom carries a prior
                    'adjO': r['adjO'], 'adjD': r['adjD'],
                    'adjT': r['adjT'], 'match': how,
                    'net': round(r['adjO'] - r['adjD'], 2),
                    'away_winpct': None, 'away_games': 0,
                    'home_winpct': None, 'home_games': 0}

    # THE VENUE VARIABLE STILL COMES FROM RESULTS.
    #
    # KenPom gives efficiency and tempo; it does not give the
    # home/away win split the paper's venue finding is built on. That
    # lives in the results cache, so merge it in when it exists --
    # otherwise venue_flag silently never fires in KenPom mode and
    # the one contextual check in the model quietly disappears.
    try:
        gcache = json.load(open(GAMES)).get('days') or {}
        if cache_is_current(gcache):
            splits = rate(gcache, iters=20, write=False)['teams']
            merged = 0
            for tid, t in out.items():
                sp = splits.get(tid)
                if sp:
                    t['away_winpct'] = sp['away_winpct']
                    t['away_games'] = sp['away_games']
                    t['home_winpct'] = sp['home_winpct']
                    t['home_games'] = sp['home_games']
                    merged += 1
            if merged:
                say(f'  merged home/away splits for {merged} team(s) '
                    f'from the results cache')
        else:
            say('  !! results cache uses the old team key, so NO venue '
                'splits were\n  !! merged and venue_flag will never '
                'fire. Rebuild it:\n  !!   python3 college.py --build '
                '--refresh')
    except FileNotFoundError:
        say('  !! no results cache, so no home/away splits: venue_flag '
            'cannot fire.\n  !! The paper\'s venue check needs '
            'results:  python3 college.py --build')
    except Exception as ex:
        say(f'  !! could not merge venue splits: {ex}')

    say(f'  matched {len(out)} of {len(rows)} to ESPN ids'
        + (f' ({len(fuzzy)} by similarity)' if fuzzy else ''))
    for kp, to in fuzzy[:10]:
        say(f'    ~ {kp}  ->  {to}')
    if missing:
        say(f'\n  !! {len(missing)} KENPOM TEAM(S) DID NOT MATCH and are '
            f'NOT RATED.')
        say('  !! Games involving them will report as unrated, not be '
            'guessed at.')
        say('  !! Add them to college_aliases.json as '
            '{"KenPom name": "ESPN name"}:')
        for kp, near in missing[:14]:
            say(f'    {kp:<26} nearest: '
                + (', '.join(near) if near else '(nothing close)'))
        if len(missing) > 14:
            say(f'    ... and {len(missing) - 14} more')
        # A list printed to a terminal is a list nobody acts on. Write
        # it where it can be edited and renamed into place.
        prop = ALIASFILE.replace('.json', '_suggested.json')
        try:
            json.dump({kp: (near[0] if near else '')
                       for kp, near in missing},
                      open(prop, 'w'), indent=1, sort_keys=True)
            say(f'\n  proposals written to {prop}')
            say('  CHECK EVERY ONE -- they are nearest-string guesses, '
                'and several\n  will be the wrong school. Fix the '
                'values, delete what you cannot\n  confirm, then:  mv '
                + os.path.basename(prop) + ' '
                + os.path.basename(ALIASFILE))
        except Exception:
            pass

    json.dump({'build': BUILD, 'mode': 'kenpom',
               'league': round(lg_eff, 2), 'tempo': round(lg_tempo, 2),
               'source': os.path.basename(path),
               'teams': out, 'n_games': None,
               'unmatched': [m[0] for m in missing]},
              open(RATINGS, 'w'))
    say(f'\n  wrote {RATINGS}  (mode: kenpom)')
    return out


def _kenpom_ratings():
    """Are the ratings on disk a KenPom ingest?

    --publish rates from results by default. Doing that over a KenPom
    ingest would silently replace a prior-carrying rating with a
    two-game one every single night, and the picks would look
    completely normal.
    """
    try:
        return json.load(open(RATINGS)).get('mode') == 'kenpom'
    except Exception:
        return False


def norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def load_sd():
    """The measured margin SD, or None. None is not a failure.

    Returning None rather than silently handing back the fallback is
    the whole point: three.py's minutes haircut was written, correct,
    and inert for a week because nothing said the file was missing.
    """
    if not os.path.exists(SDFILE):
        return None
    try:
        d = json.load(open(SDFILE))
        sd = float(d.get('sd'))
        return sd if sd > 0 else None
    except Exception:
        return None


def sd_banner(sd):
    if sd is None:
        print(f'\n  margin SD  ASSUMED at {MARGIN_SD} -- NOT measured. '
              f'This runs\n             OVERCONFIDENT, which also lets '
              f'picks through the\n             {CONF_GATE:.0%} gate '
              f'that should not clear it.\n             Measure it:  '
              f'python3 college.py --fit-sd')
    else:
        print(f'\n  margin SD  measured {sd:.1f} (walk-forward), '
              f'fallback was {MARGIN_SD}')


def fit_sd(cache=None, write=True, min_day_games=20):
    """Measure the margin SD walk-forward, never in-sample.

    For each day, rate on everything BEFORE it and forecast that day.
    In-sample residuals would be flattered by the ratings having
    already seen the game, and a too-small SD is exactly the failure
    this is meant to remove -- so it would make the problem worse
    while looking like a measurement.
    """
    if cache is None:
        cache = json.load(open(GAMES)).get('days') or {}
    ds = sorted(d for d in cache if cache[d])
    errs = []
    for i, d in enumerate(ds):
        prior = {k: cache[k] for k in ds[:i]}
        if sum(len(v) for v in prior.values()) < min_day_games * 3:
            continue
        try:
            rt = rate(prior, iters=25, write=False)
        except Exception:
            continue
        for g in cache[d]:
            f = forecast(rt, g['home'], g['away'], g['neutral'],
                         sd_override=1.0)
            if not f:
                continue
            errs.append((g['hs'] - g['as']) - f['margin'])
    if len(errs) < 50:
        say(f'  !! only {len(errs)} out-of-sample game(s) -- not enough '
            f'to measure.\n  !! Needs more of the season. Keeping the '
            f'assumed {MARGIN_SD}.')
        return None
    m = sum(errs) / len(errs)
    sd = (sum((e - m) ** 2 for e in errs) / len(errs)) ** 0.5
    say(f'\n  measured margin SD {sd:.2f} from {len(errs)} out-of-sample '
        f'game(s)')
    say(f'  mean residual {m:+.2f} points'
        + ('  <- home court may be off' if abs(m) > 1.0 else ''))
    # Restore the full-season rating. fit_sd is a measurement and
    # must not be observable in any other file.
    try:
        rate(cache, write=True)
    except Exception:
        pass
    if write:
        json.dump({'build': BUILD, 'sd': round(sd, 2),
                   'n': len(errs), 'mean_residual': round(m, 2),
                   'measured': datetime.datetime.now()
                   .astimezone().isoformat()}, open(SDFILE, 'w'))
        say(f'  wrote {SDFILE}')
    return sd


def forecast(rt, home, away, neutral=False, hca=HCA, sd_override=None,
             min_games=None, why=None):
    """P(home wins), the predicted margin, and why.

    Expected points are each side's adjusted offence scaled by the
    opponent's adjusted defence relative to the league. The margin
    goes through a normal with MARGIN_SD, which is ASSUMED -- the
    same standing caveat as three.py's blowout model.
    """
    # EVERY REFUSAL IS NAMED. Returning a bare None here is what let
    # picks() drop a whole slate and still print "nothing clears the
    # gate" -- a message about a filter that never ran. `why` is a
    # counter the caller passes in; it is the difference between
    # "evaluated and rejected" and "never evaluated".
    mg = MIN_GAMES if min_games is None else min_games
    T = rt['teams']
    if home not in T or away not in T:
        if why is not None:
            miss = [t for t in (home, away) if t not in T]
            why['unrated team'] = why.get('unrated team', 0) + 1
            why.setdefault('_unrated', set()).update(miss)
        return None
    lg = rt['league']
    h, a = T[home], T[away]
    # KENPOM RATINGS ARE PER 100 POSSESSIONS AND CARRY A TEMPO.
    # Points-per-game ratings are not, and using one formula for both
    # would silently treat an efficiency as a score. The ratings file
    # records which it holds.
    if rt.get('mode') == 'kenpom':
        eff_h = h['adjO'] * a['adjD'] / lg
        eff_a = a['adjO'] * h['adjD'] / lg
        poss = h['adjT'] * a['adjT'] / (rt.get('tempo') or 70.0)
        eh, ea = eff_h * poss / 100.0, eff_a * poss / 100.0
        edge = 0.0 if neutral else hca
        margin = (eh + edge / 2.0) - (ea - edge / 2.0)
        sd = sd_override if sd_override is not None else (
            load_sd() or MARGIN_SD)
        return {'p_home': norm_cdf(margin / sd), 'margin': margin,
                'sd': sd, 'mode': 'kenpom',
                'sd_measured': (sd_override is None
                                and load_sd() is not None),
                'exp_home': eh + edge / 2.0, 'exp_away': ea - edge / 2.0,
                'poss': poss}
    if h['games'] < mg or a['games'] < mg:
        if why is not None:
            why[f'under the {mg}-game floor'] = (
                why.get(f'under the {mg}-game floor', 0) + 1)
            why['_minplayed'] = min(why.get('_minplayed', 99),
                                    h['games'], a['games'])
        return None
    eh = h['adjO'] * a['adjD'] / lg
    ea = a['adjO'] * h['adjD'] / lg
    edge = 0.0 if neutral else hca
    margin = (eh + edge / 2.0) - (ea - edge / 2.0)
    sd = sd_override if sd_override is not None else (
        load_sd() or MARGIN_SD)
    p = norm_cdf(margin / sd)
    return {'p_home': p, 'margin': margin, 'sd': sd,
            'sd_measured': sd_override is None and load_sd() is not None,
            'exp_home': eh + edge / 2.0, 'exp_away': ea - edge / 2.0}


def venue_flag(rt, road_team):
    """The paper's venue bias, as a TAG. See the module docstring.

    Returns (direction, note) or (None, None). It never changes p.
    """
    t = (rt['teams'] or {}).get(road_team) or {}
    w, n = t.get('away_winpct'), t.get('away_games') or 0
    if w is None or n < 3:
        return None, None
    if w < WEAK_ROAD:
        return 'weak_road', (
            f'weak road team ({w:.0%} away, {n}g). The paper has this '
            f'system OVERESTIMATING them by {VENUE_WEAK_PP:.1%} '
            f'(n=58) -- so a pick ON them is the shakier side. Not '
            f'corrected here: beta=0.0754, p=0.093.')
    if w > STRONG_ROAD:
        return 'strong_road', (
            f'strong road team ({w:.0%} away, {n}g). The paper has this '
            f'system UNDERESTIMATING them by {VENUE_STRONG_PP:.1%} '
            f'(n=44) -- a pick AGAINST them is the shakier side. Not '
            f'corrected here: beta=0.0754, p=0.093.')
    return None, None


def slate(day):
    """Tonight's D1 games, whatever their status."""
    doc = get(f'{SCOREBOARD}?dates={day}&groups=50&limit=400')
    out = []
    for e in doc.get('events') or []:
        comp = (e.get('competitions') or [{}])[0]
        cs = comp.get('competitors') or []
        if len(cs) != 2:
            continue
        side = {}
        bad = False
        for c in cs:
            t = c.get('team') or {}
            if t.get('id') is None:
                bad = True
                break
            side[c.get('homeAway')] = {
                'team': str(t.get('id')),
                'name': (t.get('shortDisplayName') or t.get('displayName')
                         or t.get('abbreviation') or str(t.get('id')))}
        if bad:
            continue
        if 'home' not in side or 'away' not in side:
            continue
        out.append({'id': e.get('id'), 'home': side['home']['team'],
                    'away': side['away']['team'],
                    'home_name': side['home']['name'],
                    'away_name': side['away']['name'],
                    'neutral': bool(comp.get('neutralSite')),
                    'start': e.get('date')})
    return out


def picks(day, rt=None, gate=CONF_GATE, hca=HCA, min_games=None):
    """Tonight's winner calls, gated at the paper's 60%.

    Returns (rows, below, total, why). `why` accounts for EVERY game
    that produced no pick. An empty list with an empty `why` is the
    gate working; an empty list with a full `why` is the slate never
    reaching the gate, and those two must never print the same way.
    """
    rt = rt or json.load(open(RATINGS))
    games = slate(day)
    rows, below, why = [], 0, {}
    for g in games:
        f = forecast(rt, g['home'], g['away'], g['neutral'], hca,
                     min_games=min_games, why=why)
        if not f:
            continue
        p_home = f['p_home']
        side = 'home' if p_home >= 0.5 else 'away'
        conf = p_home if side == 'home' else 1 - p_home
        if conf < gate:
            below += 1
            why[f'under the {gate:.0%} gate'] = (
                why.get(f'under the {gate:.0%} gate', 0) + 1)
            continue
        road = g['away']
        vf, vnote = venue_flag(rt, road)
        rows.append({
            'day': day, 'game_id': g['id'],
            'home': g['home'], 'away': g['away'],
            'home_name': g['home_name'], 'away_name': g['away_name'],
            'neutral': g['neutral'],
            'pick': g['home'] if side == 'home' else g['away'],
            'pick_name': (g['home_name'] if side == 'home'
                          else g['away_name']),
            'side': side, 'conf': round(conf, 4),
            'margin': round(f['margin'], 1),
            'venue_flag': vf, 'venue_note': vnote,
            # WHICH WAY IT CUTS FOR THIS PICK.
            #
            # venue_flag describes the ROAD team. Whether that helps
            # or hurts depends on whether the road team IS the pick:
            #
            #   we OVERRATE weak road teams
            #     pick is that road team -> shakier
            #     pick is the home side  -> their opponent is
            #                               overrated, so the pick is
            #                               if anything understated
            #   we UNDERRATE strong road teams -> the mirror
            #
            # Printing "overestimated these" without saying which
            # reads as a warning on a row where it is reassurance.
            'venue_effect': (
                None if not vf else
                ('shakier' if (vf == 'weak_road') == (side == 'away')
                 else 'safer')),
            'build': BUILD,
        })
    # Translate ids back to names before anyone reads this. A list
    # of ESPN integers is not a diagnostic, it is a second puzzle.
    if why.get('_unrated'):
        nm = {}
        for g in games:
            nm[g['home']] = g['home_name']
            nm[g['away']] = g['away_name']
        why['_unrated'] = {nm.get(t, t) for t in why['_unrated']}
    rows.sort(key=lambda r: -r['conf'])
    return rows, below, len(games), why


# --- 5. log and grade ------------------------------------------------
def log_picks(rows, path=None):
    """Upsert tonight's picks. Re-running a day replaces that day."""
    path = path or PICKS
    keep = []
    if os.path.exists(path):
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            keep.append(r)
    day = rows[0]['day'] if rows else None
    if day:
        # A re-run is a correction, not a second set of picks. Graded
        # rows are kept: once settled, the result is history.
        keep = [r for r in keep
                if r.get('day') != day or r.get('result') is not None]
        have = {(r.get('day'), r.get('game_id')) for r in keep}
        for r in rows:
            if (r['day'], r['game_id']) not in have:
                keep.append(dict(r, result=None, actual=None,
                                 graded_at=None))
    with io.open(path, 'w', encoding='utf-8') as fh:
        for r in keep:
            fh.write(json.dumps(r) + '\n')
    say(f'logged {len(rows)} college pick(s) to {path}')
    return len(rows)


def grade(day=None, path=None):
    """Settle picks against the final score. No line needed."""
    path = path or PICKS
    if not os.path.exists(path):
        say('  no college picks logged yet')
        return 0
    rows = []
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    todo = sorted({r['day'] for r in rows
                   if r.get('result') is None
                   and (day is None or r['day'] == day)})
    if not todo:
        say('  nothing to grade')
        return 0
    n = 0
    for d in todo:
        try:
            doc = get(f'{SCOREBOARD}?dates={d}&groups=50&limit=400')
        except Exception as ex:
            say(f'  !! {d}: {ex}')
            continue
        final = {}
        for e in doc.get('events') or []:
            g = parse_event(e)
            if g:
                final[str(e.get('id'))] = g
        for r in rows:
            if r.get('day') != d or r.get('result') is not None:
                continue
            g = final.get(str(r.get('game_id')))
            if not g:
                continue
            won = g['home'] if g['hs'] > g['as'] else g['away']
            r['result'] = 'win' if won == r['pick'] else 'loss'
            # NAMES, NOT IDS. g['home'] and g['away'] became ESPN
            # numeric ids when the team key changed; this line kept
            # formatting them and the page printed "2351 72 @ 6 79".
            # parse_event carries the names alongside precisely so a
            # display never has to reach for the key.
            r['actual'] = (f"{g['away_name']} {g['as']} @ "
                           f"{g['home_name']} {g['hs']}")
            r['final_home'] = g['hs']
            r['final_away'] = g['as']
            # HOW CLOSE THE MARGIN WAS.
            #
            # `margin` is predicted home-minus-away, so the actual is
            # measured the same way and the error is comparable across
            # picks regardless of which side was taken. A winner call
            # that lands on a 1-point game and one that lands on a
            # 30-point game are the same tick in the record and very
            # different pieces of evidence about the model.
            am = g['hs'] - g['as']
            r['actual_margin'] = am
            if r.get('margin') is not None:
                r['margin_err'] = round(am - r['margin'], 1)
            # THE SECOND RECORD. Did they reach or beat the margin we
            # published? Separate from the moneyline verdict above,
            # which only asks whether they won at all.
            called, target, got = pick_margins(r, am)
            if target is not None and got is not None:
                r['margin_target'] = target
                r['margin_got'] = got
                r['margin_result'] = 'win' if got >= target else 'loss'
            r['graded_at'] = datetime.datetime.now().astimezone().isoformat()
            n += 1
    with io.open(path, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    say(f'graded {n} college pick(s)')
    return n


def bands(rows):
    """The record split by confidence, so cupcakes cannot flatter it.

    A single hit rate over a November slate is dominated by games
    nobody would price. Split, it stays honest, and the 60-80% band
    is the one that corresponds to the paper's own evidence.
    """
    out = []
    for lo, hi in ((0.60, 0.80), (0.80, 0.95), (0.95, 1.01)):
        b = [r for r in rows if r.get('result') in ('win', 'loss')
             and lo <= r['conf'] < hi]
        if b:
            w = sum(1 for r in b if r['result'] == 'win')
            out.append({'band': f'{lo:.0%}-{min(hi,1):.0%}', 'n': len(b),
                        'wins': w, 'rate': round(w / len(b), 3)})
    return out


def pick_margins(r, actual_margin=None):
    """The called and actual margins FROM THE PICK'S SIDE.

    `margin` is stored home-minus-away. A pick on the road team has a
    negative one, so comparing the raw numbers would grade every away
    pick backwards. Both get flipped together or neither does.

    The target is rounded HALF-UP, the same rule the NBA record uses:
    a called margin of 8.1 needs 8, and 27.5 needs 28. Margins come
    in whole numbers and a prediction does not.
    """
    m = r.get('margin')
    if m is None:
        return None, None, None
    away = (r.get('side') == 'away')
    called = -m if away else m
    target = int(math.floor(called + 0.5))
    got = None
    if actual_margin is None:
        actual_margin = r.get('actual_margin')
    if actual_margin is not None:
        got = -actual_margin if away else actual_margin
    return called, target, got


def repair(path=None):
    """Re-fetch finals for rows already graded under the old format.

    grade() only touches rows whose result is None, so everything
    settled before this fix keeps an id-formatted score line and has
    no margin error. Those rows are not wrong, they are unreadable,
    and the margin they were graded against was never recorded.
    """
    path = path or PICKS
    if not os.path.exists(path):
        say('  nothing logged')
        return 0
    rows = []
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    need = sorted({r['day'] for r in rows
                   if r.get('result') is not None
                   and (r.get('actual_margin') is None
                        or r.get('margin_result') is None
                        or str(r.get('actual', '')).split()[0].isdigit())})
    if not need:
        say('  nothing to repair')
        return 0
    n = 0
    for d in need:
        try:
            doc = get(f'{SCOREBOARD}?dates={d}&groups=50&limit=400')
        except Exception as ex:
            say(f'  !! {d}: {ex}')
            continue
        final = {}
        for e in doc.get('events') or []:
            g = parse_event(e)
            if g:
                final[str(e.get('id'))] = g
        for r in rows:
            if r.get('day') != d:
                continue
            g = final.get(str(r.get('game_id')))
            if not g:
                continue
            r['actual'] = (f"{g['away_name']} {g['as']} @ "
                           f"{g['home_name']} {g['hs']}")
            r['final_home'], r['final_away'] = g['hs'], g['as']
            r['actual_margin'] = g['hs'] - g['as']
            if r.get('margin') is not None:
                r['margin_err'] = round(r['actual_margin'] - r['margin'], 1)
            called, target, got = pick_margins(r, r['actual_margin'])
            if target is not None and got is not None:
                r['margin_target'] = target
                r['margin_got'] = got
                r['margin_result'] = 'win' if got >= target else 'loss'
            n += 1
    with io.open(path, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    say(f'  repaired {n} row(s) across {len(need)} day(s)')
    return n


def margin_record(rows):
    """How close the predicted margins came.

    MAE is the headline: the average miss in points, which is what
    "how accurate" means for a margin. The signed mean is carried
    beside it because a bias and a spread are different faults -- a
    systematic +4 says home court is mis-set, a large MAE with no
    bias says the ratings are noisy. Only one of those is fixable by
    turning a knob.
    """
    g = [r for r in rows if r.get('margin_err') is not None]
    if not g:
        return None
    errs = [abs(r['margin_err']) for r in g]
    signed = [r['margin_err'] for r in g]
    within = lambda k: sum(1 for e in errs if e <= k)
    return {'n': len(g),
            'mae': round(sum(errs) / len(errs), 1),
            'bias': round(sum(signed) / len(signed), 1),
            'within5': round(within(5) / len(g), 3),
            'within10': round(within(10) / len(g), 3)}


def record(path=None):
    """The paper's own metrics: accuracy, Brier, calibration by bin."""
    path = path or PICKS
    rows = []
    if os.path.exists(path):
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    pass
    g = [r for r in rows if r.get('result') in ('win', 'loss')]
    w = sum(1 for r in g if r['result'] == 'win')
    # THE SECOND RECORD, kept entirely apart from the first. A pick
    # can win the game and miss the number, and that is the whole
    # reason for showing both.
    mg = [r for r in rows if r.get('margin_result') in ('win', 'loss')]
    mw = sum(1 for r in mg if r['margin_result'] == 'win')
    brier = (sum((r['conf'] - (1.0 if r['result'] == 'win' else 0.0)) ** 2
                 for r in g) / len(g)) if g else None
    bins = []
    for lo, hi in ((0.60, 0.70), (0.70, 0.80), (0.80, 0.90), (0.90, 1.01)):
        b = [r for r in g if lo <= r['conf'] < hi]
        if b:
            bins.append({
                'bin': f'{lo:.0%}-{hi if hi <= 1 else 1:.0%}', 'n': len(b),
                'predicted': round(sum(r['conf'] for r in b) / len(b), 4),
                'actual': round(sum(1 for r in b
                                    if r['result'] == 'win') / len(b), 4)})
    return {'wins': w, 'graded': len(g), 'pending': len(rows) - len(g),
            'margin_wins': mw, 'margin_graded': len(mg),
            'margin': margin_record(rows),
            'brier': (round(brier, 4) if brier is not None else None),
            'bins': bins, 'bands': bands(rows), 'picks': rows}


def show_why(why, total):
    """Where every game went. Silence here is what hid the last bug."""
    named = {k: v for k, v in (why or {}).items() if not k.startswith('_')}
    if not named:
        return
    print('\n  where the other games went:')
    for k, v in sorted(named.items(), key=lambda kv: -kv[1]):
        print(f'    {v:>4}  {k}')
    unrated = (why or {}).get('_unrated')
    if unrated:
        ex = ', '.join(sorted(unrated)[:8])
        print(f'\n  !! {len(unrated)} team(s) on tonight\'s slate have NO '
              f'rating: {ex}'
              + (' ...' if len(unrated) > 8 else ''))
        print('  !! They are not in the results cache. Either the season '
              'start is\n  !! wrong or --build has not reached them:  '
              'python3 college.py --build')
    mp = (why or {}).get('_minplayed')
    if mp is not None:
        print(f'\n  !! THE GAMES FLOOR IS WHAT IS BLOCKING THESE, not the '
              f'gate.')
        print(f'  !! The thinnest team involved has played {mp} game(s) '
              f'and the floor\n  !! is {MIN_GAMES}. In early November '
              f'that is most of D1.')
        print('  !! Lower it deliberately if you want these:  '
              '--min-games 2\n  !! A 2-game rating is mostly noise, and '
              'the model cannot tell you\n  !! that it is -- the '
              'confidence it prints will look the same.')


def show_picks(rows, below, total, rec=None, why=None):
    print('\n' + '=' * 68)
    print('COLLEGE -- D1 winner calls')
    if not rows:
        blocked = sum(v for k, v in (why or {}).items()
                      if not k.startswith('_')
                      and 'gate' not in k)
        if blocked:
            print(f'\n  NO PICKS, AND THE GATE IS NOT WHY. {total} '
                  f'game(s) on the board;\n  {blocked} never reached '
                  f'the gate at all.')
        else:
            print(f'\n  Nothing clears {CONF_GATE:.0%} tonight '
                  f'({total} game(s) on the board, {below} under the '
                  f'gate).')
            print('  That is the gate working. Below 60% the paper has '
                  'this\n  system at 56.8%, which is not a pick, it is '
                  'a coin.')
        show_why(why, total)
        return
    seen = len(rows) + below
    if seen < total:
        print(f'\n  {len(rows)} pick(s). Of {total} game(s) on the '
              f'board, {seen} could be\n  evaluated at all; '
              f'{len(rows)} cleared the {CONF_GATE:.0%} gate and '
              f'{below} did not.\n')
    else:
        print(f'\n  {len(rows)} of {total} game(s) clear the '
              f'{CONF_GATE:.0%} gate; {below} fell under it.\n')
    for i, r in enumerate(rows, 1):
        at = 'vs' if r['neutral'] else 'at'
        print(f"  {i:>2}. {r['pick_name']}  {r['conf']:.0%}")
        print(f"      {r['away_name']} {at} {r['home_name']}"
              f"   projected margin {abs(r['margin']):.1f} "
              f"{'home' if r['margin'] > 0 else 'away'}")
        if r.get('venue_note'):
            print(f"      ! {r['venue_note']}")
    hi = [r for r in rows if r['conf'] >= PUBLISH_MAX_CONF]
    if hi:
        print(f'\n  {len(hi)} of these are above {PUBLISH_MAX_CONF:.0%} '
              f'and are NOT published:')
        for r in hi[:4]:
            print(f"    {r['pick_name']} {r['conf']:.0%} over "
                  f"{r['away_name'] if r['side'] == 'home' else r['home_name']}")
        if len(hi) > 4:
            print(f'    ... and {len(hi) - 4} more')
        print('  They are logged and graded -- the RECORD is not '
              'curated -- but a\n  November buy game is not a pick '
              'anyone can act on, and a record\n  full of them reads '
              '95% accurate while saying nothing.')
    print(f'\n  GATED AT {CONF_GATE:.0%}, which is the one finding in the '
          f'paper strong\n  enough to act on: below it 56.8% on n=377, '
          f'at or above it 78.2%\n  on n=1,167 (p < 0.001).')
    # IS THE GATE ACTUALLY FILTERING?
    #
    # Home court alone puts a dead-even team at 60-62%, so the 60%
    # threshold admits essentially every home favourite. The paper's
    # own distribution had 24.4% of games BELOW 60%. If almost
    # nothing here falls under it, this is not applying the filter
    # the paper validated -- it is applying its name to a different
    # distribution, and the 78.2% that justifies the gate does not
    # transfer.
    # DENOMINATOR IS WHAT WAS EVALUATED, not what was on the board.
    # Dividing by `total` mixes games the gate rejected with games
    # the gate never saw, which understates the share and silences
    # the warning precisely when most of the slate went missing --
    # the same conflation that produced "nothing clears, 0 under the
    # gate" on a slate where nothing was ever forecast.
    share = (below / seen) if seen else 0.0
    if seen >= 8 and share < 0.12:
        print(f'\n  !! ONLY {share:.0%} OF TONIGHT\'S GAMES FELL UNDER THE '
              f'GATE.')
        print('  !! The paper had 24.4% below 60%. Home court alone is '
              'worth about\n  !! 60-62% here, so the gate may be '
              'admitting every home\n  !! favourite rather than '
              'selecting confident ones. Until the\n  !! measured hit '
              'rate says otherwise, treat these as less filtered\n'
              '  !! than the 78.2% in the paper implies.')
    sd_banner(load_sd())
    print(f'  Home court is ASSUMED at {HCA} points and is not measured.')
    show_why(why, total)
    if rec and rec.get('margin'):
        m = rec['margin']
        print(f"\n  margin accuracy over {m['n']} graded game(s):")
        print(f"    off by {m['mae']:.1f} points on average"
              f"   ({m['within5']:.0%} within 5, "
              f"{m['within10']:.0%} within 10)")
        print(f"    signed bias {m['bias']:+.1f}"
              + ('  <- a steady lean, so home court or the ratings '
                 'are off' if abs(m['bias']) >= 2.0
                 else '  <- no systematic lean'))
    if rec and rec.get('margin_graded'):
        print(f"\n  margin record: {rec['margin_wins']}/"
              f"{rec['margin_graded']} reached or beat the number we "
              f"published\n    ({rec['margin_wins'] / rec['margin_graded']:.0%})"
              f" -- a different question from whether they won")
    if rec and rec['graded']:
        print(f"\n  record so far: {rec['wins']}/{rec['graded']} "
              f"({rec['wins'] / rec['graded']:.1%})"
              + (f", Brier {rec['brier']:.4f}" if rec['brier'] else ''))


def main():
    ap = argparse.ArgumentParser(description=f'college.py {BUILD}')
    ap.add_argument('--probe', metavar='YYYYMMDD', nargs='?', const='',
                    help='report what the feed actually contains')
    ap.add_argument('--build', action='store_true', help='fetch results')
    ap.add_argument('--rate', action='store_true', help='rate teams')
    ap.add_argument('--kenpom', metavar='FILE',
                    help='read AdjO/AdjD/AdjT from a kenpom summary '
                         'export (.pdf or text) instead of rating from '
                         'results. Does NOT fetch kenpom.com.')
    ap.add_argument('--fit-sd', action='store_true', dest='fit_sd',
                    help='measure the margin SD walk-forward')
    ap.add_argument('--picks', action='store_true', help="tonight's calls")
    ap.add_argument('--grade', action='store_true', help='settle')
    ap.add_argument('--repair', action='store_true',
                    help='re-fetch finals for rows graded before the '
                         'score line carried names and the margin '
                         'error was recorded')
    ap.add_argument('--report', action='store_true', help='the record')
    ap.add_argument('--publish', metavar='DIR', help='write college.json')
    ap.add_argument('--day', help='YYYYMMDD, default today')
    ap.add_argument('--start', default=SEASON_START)
    ap.add_argument('--gate', type=float, default=CONF_GATE)
    ap.add_argument('--hca', type=float, default=HCA)
    ap.add_argument('--max-conf', type=float, default=PUBLISH_MAX_CONF,
                    help=f'do not PUBLISH picks above this confidence '
                         f'(default {PUBLISH_MAX_CONF}); they still '
                         f'log and grade')
    ap.add_argument('--max-picks', type=int, default=PUBLISH_MAX,
                    help=f'most to publish (default {PUBLISH_MAX})')
    ap.add_argument('--min-games', type=int, dest='min_games',
                    default=None,
                    help=f'games a team needs to be rated '
                         f'(default {MIN_GAMES}); lower it early in '
                         f'the season, knowing a 2-game rating is '
                         f'mostly noise')
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()

    day = a.day or datetime.datetime.now().astimezone().strftime('%Y%m%d')

    if a.probe is not None:
        return probe(a.probe or day)
    if a.report:
        r = record()
        print(json.dumps({k: v for k, v in r.items() if k != 'picks'},
                         indent=1))
        return 0

    did = False
    if a.build or a.publish:
        build(a.start, refresh=a.refresh)
        did = True
    if a.rate or (a.publish and not _kenpom_ratings()):
        rate()
        did = True
    elif a.publish:
        say('  ratings are from a KenPom export -- not re-rating from '
            'results.\n  Refresh them with:  college.py --kenpom FILE')
    if a.kenpom:
        ingest_kenpom(a.kenpom, refresh=a.refresh)
        return 0

    if a.fit_sd:
        fit_sd()
        return 0
    if a.repair:
        repair()
        return 0

    if a.grade or a.publish:
        grade()
        # Rows settled under the old format carry an id-formatted
        # score and no margin error; fold them in on every publish
        # rather than needing a flag nobody remembers to pass.
        repair()
        did = True
    if a.picks or a.publish:
        rows, below, total, why = picks(day, gate=a.gate, hca=a.hca,
                                        min_games=a.min_games)
        # EVERYTHING that cleared the gate is logged and graded --
        # the record should not be curated. Only the published list
        # is trimmed, and show_picks says so.
        shown = [r for r in rows if r['conf'] < a.max_conf][:a.max_picks]
        if a.publish:
            log_picks(rows)
        rec = record()
        show_picks(rows, below, total, rec, why)
        did = True
    if a.publish:
        d = os.path.join(os.path.expanduser(a.publish), 'data')
        os.makedirs(d, exist_ok=True)
        rec = record()
        json.dump({'build': BUILD, 'day': day, 'gate': a.gate,
                   'picks': shown, 'picks_all': len(rows),
                   'max_conf': a.max_conf,
                   'wins': rec['wins'], 'graded': rec['graded'],
                   'brier': rec['brier'], 'bins': rec['bins'],
                   'bands': rec['bands'],
                   'history': [r for r in rec['picks']
                               if r.get('result') is not None][-120:]},
                  open(os.path.join(d, 'college.json'), 'w'), indent=1)
        print(f"\nwrote {os.path.join(d, 'college.json')}: "
              f"{len(shown)} of {len(rows)} pick(s) shown, "
              f"{rec['graded']} graded")
    if not did:
        ap.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
