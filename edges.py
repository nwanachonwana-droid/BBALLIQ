#!/usr/bin/env python3
"""
EDGES -- two structural screens, built from box scores and rates.

THE TWO THESES
  A) A primary ball handler with a large possession share, meeting a
     defence that cannot take those possessions away. The bet is that
     volume plus a soft matchup produces a night above his line.

  B) A role player whose minutes are not guaranteed, coming off poor
     shooting, against a good defence. The bet is a shorter leash:
     fewer looks, earlier hook, under.

  These are opposite ends of the same idea -- that opportunity, not
  talent, is what a prop line misprices -- so they are screened from
  the same inputs and reported separately.

WHAT IS MEASURED, AND WHAT IS NOT
  Possession share comes from usage rate. Opportunity stability comes
  from minutes: how many, and how reliably. Recent form comes from the
  last games actually played.

  What you asked for and cannot have: double-team frequency, drives
  allowed, closest-defender data. Those are NBA tracking exclusives
  and that host blocks this machine. Team defensive rating and pace
  are the available proxies and they are cruder -- a defence can be
  excellent in aggregate and still be the wrong one to put on a
  downhill guard. Treat the matchup column as a hint, not a read.

THE MINUTES PROBLEM, STATED HONESTLY
  Thesis B needs minutes VARIANCE, and variance over three games is
  noise. So stability is measured two ways and the better one is used
  when it exists:

    early season  starts / appearances from last season. A player with
                  66 games and 13 starts has an unstable role by
                  definition. Crude, free, available today.
    later         the real standard deviation of minutes from this
                  season's box scores, once there are enough of them.

  The script says which one it used. A number whose provenance is
  hidden is worse than no number.

usage
  python3 edges.py --build            # cache this season's box scores
  python3 edges.py                    # screen tonight's lines
  python3 edges.py --thesis a
  python3 edges.py --day 20261021          # screen a past slate
  python3 edges.py --max-z 0.8            # loosen the sanity ceiling

THE SANITY CEILING
  The first version of this screen ranked Maxey points at +26.5
  against seven books and Maluach rebounds at -49.3 with a model
  probability of 0.0%. Both were its own stale inputs, not edges.
  Three columns exist to stop that happening quietly:

    dEXP   the disagreement in units of the stat, not probability
    zGAP   the same, over the stat's own game-to-game spread
    x      the minutes the market's price implies, over the baseline

  A row with a large x is a role change and goes to the watch list
  rather than the card -- the model cannot price a role it has never
  observed. A row with a large zGAP and a normal x is the model being
  wrong about a role it has observed. Neither is a bet.
"""
import os, re, sys, json, time, math, argparse, datetime, statistics, subprocess

DATA = os.path.expanduser('~/kalshi_data')
PICKS = os.path.join(DATA, 'external_picks.jsonl')
ESPN = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
SEASON_START = '20261020'          # opening night, 2026-27
BREF = 'https://www.basketball-reference.com/leagues'
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# B-Ref prints full team names; the book feed uses codes.
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


def code(t):
    t = (t or '').strip()
    return TEAM_CODE.get(t, ALT_CODE.get(t.upper(), t.upper()))


def team_ratings(season='2026', refresh=False):
    """Schedule-adjusted defensive rating per team, from B-Ref.

    def_rtg_adj rather than def_rtg: three games into a season nobody
    has played a balanced schedule, and the raw number mostly measures
    who they drew. Lower is a better defence.

    Pace is not on this page. It matters for thesis A -- more
    possessions is more opportunity -- and getting it means another
    table and another structure to verify, so it is deliberately left
    out rather than guessed at.
    """
    p = os.path.join(DATA, f'team_ratings_{season}.json')
    if os.path.exists(p) and not refresh:
        try:
            return json.load(open(p))
        except Exception:
            pass
    url = f'{BREF}/NBA_{season}_ratings.html'
    r = subprocess.run(['curl', '-s', '--compressed', '-m', '30',
                        '-H', f'User-Agent: {UA}', url],
                       capture_output=True, timeout=45)
    body = r.stdout.decode('utf-8', 'replace').replace('<!--', '').replace('-->', '')
    if len(body) < 40000:
        raise RuntimeError(f'ratings page: {len(body)} bytes - blocked?')
    m = re.search(r'<table[^>]*id="ratings"[^>]*>(.*?)</table>', body, re.S)
    if not m:
        raise RuntimeError('ratings table not found')
    out = {}
    for tr in re.findall(r'<tr[^>]*>(.*?)</tr>', m.group(1), re.S):
        cells = {}
        for stat, inner in re.findall(
                r'data-stat="([^"]+)"[^>]*>(.*?)</t[dh]>', tr, re.S):
            cells[stat] = re.sub(r'<[^>]+>', '', inner).strip()
        name = cells.get('team_name')
        if not name:
            continue
        try:
            drtg = float(cells.get('def_rtg_adj') or cells.get('def_rtg'))
        except (TypeError, ValueError):
            continue
        out[code(name)] = {'team': code(name), 'name': name, 'drtg': drtg}
    if not out:
        raise RuntimeError('no teams parsed from ratings table')
    ranked = sorted(out.values(), key=lambda x: x['drtg'])   # best first
    for i, t in enumerate(ranked, 1):
        out[t['team']]['rank'] = i
    os.makedirs(DATA, exist_ok=True)
    json.dump(out, open(p, 'w'), indent=1)
    return out

# How many games before this season's minutes variance is trusted over
# last season's starts ratio. Below this the standard deviation of a
# handful of games says more about the schedule than the role.
MIN_GAMES_FOR_SD = 6


def curl_json(url, timeout=25, tries=3):
    for attempt in range(tries):
        try:
            r = subprocess.run(['curl', '-s', '--compressed', '-m',
                                str(timeout), url],
                               capture_output=True, timeout=timeout + 10)
            if r.returncode != 0 or not r.stdout.strip():
                raise RuntimeError(f'curl exit {r.returncode}')
            return json.loads(r.stdout.decode())
        except Exception as e:
            if attempt + 1 == tries:
                raise RuntimeError(str(e))
            time.sleep(2 + attempt * 3)


def mins(v):
    """ESPN gives '34' or sometimes '34:45'. Both mean 34-ish."""
    s = str(v or '').strip()
    if not s:
        return 0.0
    head = s.split(':')[0]
    try:
        return float(head)
    except ValueError:
        return 0.0


def _int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def day_rows(day):
    """Every player's line for one date, from ESPN."""
    board = curl_json(f'{ESPN}/scoreboard?dates={day}')
    out = []
    for e in board.get('events') or []:
        comp = (e.get('competitions') or [{}])[0]
        if not (comp.get('status') or {}).get('type', {}).get('completed'):
            continue
        summary = curl_json(f"{ESPN}/summary?event={e['id']}")
        sides = (summary.get('boxscore') or {}).get('players') or []
        # THE OPPONENT, RECORDED. Without it a 14-game night is 28
        # teams in one day's rows with nothing to say who played whom,
        # so nothing downstream can compute a game margin -- which is
        # what blocked the blowout measurement on 04 Nov. Both sides
        # are right here in the same list; it was never recorded.
        abbrs = [(x.get('team') or {}).get('abbreviation') for x in sides]
        abbrs = [a for a in abbrs if a]
        for side in sides:
            team = (side.get('team') or {}).get('abbreviation')
            opp = next((a for a in abbrs if a and a != team), None)
            for blk in side.get('statistics') or []:
                labels = blk.get('labels') or []
                idx = {l: i for i, l in enumerate(labels)}
                for ath in blk.get('athletes') or []:
                    if ath.get('didNotPlay'):
                        continue
                    st = ath.get('stats') or []
                    if len(st) < len(labels):
                        continue
                    g = lambda k: st[idx[k]] if k in idx else '0'
                    name = (ath.get('athlete') or {}).get('displayName')
                    if not name:
                        continue
                    fg = str(g('FG')).split('-')
                    tp = str(g('3PT')).split('-')
                    out.append({
                        'day': day, 'player': name, 'team': team,
                        'opp': opp, 'game': e['id'],
                        'min': mins(g('MIN')), 'pts': _int(g('PTS')),
                        'reb': _int(g('REB')), 'ast': _int(g('AST')),
                        'fgm': _int(fg[0]), 'fga': _int(fg[1]) if len(fg) > 1 else 0,
                        'tpm': _int(tp[0]), 'tpa': _int(tp[1]) if len(tp) > 1 else 0,
                    })
    return out


def cache_path():
    return os.path.join(DATA, 'season_logs.json')


def build(force=False):
    """Walk every date from opening night to yesterday.

    One scoreboard call per date plus one summary per completed game --
    about thirty requests for a week of a season, and the whole league's
    game logs fall out of it. Dates already cached are skipped, so the
    daily refresh costs two or three calls.
    """
    blob = {'days': {}, 'built': None}
    p = cache_path()
    if os.path.exists(p) and not force:
        try:
            blob = json.load(open(p))
        except Exception:
            pass

    start = datetime.datetime.strptime(SEASON_START, '%Y%m%d').date()
    today = datetime.datetime.now().astimezone().date()
    d, added = start, 0
    while d < today:                       # yesterday is the last final day
        key = d.strftime('%Y%m%d')
        if key not in blob['days']:
            try:
                rows = day_rows(key)
            except Exception as e:
                print(f'  ! {key}: {e}', file=sys.stderr)
                d += datetime.timedelta(days=1)
                continue
            blob['days'][key] = rows
            added += 1
            print(f'  {key}: {len(rows)} player lines', flush=True)
            time.sleep(0.4)
        d += datetime.timedelta(days=1)

    blob['built'] = datetime.datetime.now().astimezone().isoformat()
    os.makedirs(DATA, exist_ok=True)
    json.dump(blob, open(p, 'w'))
    total = sum(len(v) for v in blob['days'].values())
    print(f'{len(blob["days"])} days, {total} player lines -> {p}'
          + (f'  ({added} new)' if added else '  (nothing new)'))
    return blob


def season_form(before=None):
    """Per-player aggregates from the cached box scores.

    `before` excludes that day and everything after it. Screening a
    past slate without it would let the form numbers include the very
    games being screened -- the screen would know Tuesday's result
    while picking Tuesday, and every filter would look brilliant.
    Walk-forward or the backtest is worthless -- same rule as the sigma
    fitting.
    """
    p = cache_path()
    if not os.path.exists(p):
        return {}
    blob = json.load(open(p))
    by = {}
    for day in sorted(blob.get('days') or {}):
        if before and day >= before:
            continue
        for r in blob['days'][day]:
            d = by.setdefault(r['player'], {
                'player': r['player'], 'team': r['team'], 'games': 0,
                'min': [], 'pts': [], 'fga': [], 'fgm': [],
                'tpa': [], 'tpm': [], 'days': []})
            d['team'] = r['team']
            d['games'] += 1
            for k in ('min', 'pts', 'fga', 'fgm', 'tpa', 'tpm'):
                d[k].append(r[k])
            d['days'].append(day)
    out = {}
    for name, d in by.items():
        m = d['min']
        mpg = sum(m) / len(m) if m else 0.0
        sd = statistics.pstdev(m) if len(m) > 1 else None
        fga = sum(d['fga'])
        fgm = sum(d['fgm'])
        tpa = sum(d['tpa'])
        tpm = sum(d['tpm'])
        # last two games played, which is what "cold lately" means
        k = min(2, len(d['fga']))
        r_fga, r_fgm = sum(d['fga'][-k:]), sum(d['fgm'][-k:])
        out[name] = {
            'team': d['team'], 'games': d['games'],
            'mpg': round(mpg, 1),
            'mp_sd': None if sd is None else round(sd, 1),
            'mp_cv': None if (sd is None or not mpg) else round(sd / mpg, 3),
            'mp_min': min(m) if m else 0, 'mp_max': max(m) if m else 0,
            'fga_pg': round(fga / d['games'], 1) if d['games'] else 0,
            'fg_pct': round(fgm / fga, 3) if fga else None,
            'fg3_pct': round(tpm / tpa, 3) if tpa else None,
            'recent_fg_pct': round(r_fgm / r_fga, 3) if r_fga else None,
            'pts_pg': round(sum(d['pts']) / d['games'], 1) if d['games'] else 0,
            # model_sd() reads '<stat>_sd' and nothing ever wrote one,
            # so its player-specific branch could never fire and every
            # SD came from the heuristic. Only points has per-game
            # values in the cache (day_rows stores min/pts/fga/fgm/
            # tpa/tpm), so only points can override it; rebounds and
            # assists stay on the heuristic until the cache carries
            # them.
            'pts_sd': (round(statistics.pstdev(d['pts']), 1)
                       if len(d['pts']) > 1 else None),
        }
    return out


# --- the B-Ref layer, reused from bref.py's cache ---------------------

def bref_cache(season='2026'):
    p = os.path.join(DATA, f'bref_{season}.json')
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception:
        return {}


def bref_row(entry, teams):
    tms = (entry or {}).get('teams') or {}
    for t in teams:
        if t in tms:
            return tms[t]
    combined = [v for k, v in tms.items() if k.endswith('TM')]
    if combined:
        return max(combined, key=lambda r: r.get('mp') or 0)
    return max(tms.values(), key=lambda r: r.get('mp') or 0) if tms else None


def norm_name(n):
    n = re.sub(r"[^a-z ]", ' ', (n or '').lower())
    parts = [p for p in n.split()
             if p not in ('jr', 'sr', 'ii', 'iii', 'iv', 'v')]
    return ' '.join(parts)


def norm_market(m):
    k = re.sub(r'[^a-z0-9]', '', (m or '').lower())
    return {'pointsscored': 'points', 'totalrebounds': 'rebounds',
            'totalassists': 'assists', '3pointersmade': 'threes',
            'pointsrebounds': 'pr', 'pointsassists': 'pa',
            'reboundsassists': 'ra'}.get(k, k)


PER36 = {
    'points': ['pts_per_minute_36'], 'rebounds': ['trb_per_minute_36'],
    'assists': ['ast_per_minute_36'], 'threes': ['fg3_per_minute_36'],
    'pra': ['pts_per_minute_36', 'trb_per_minute_36', 'ast_per_minute_36'],
    'pr': ['pts_per_minute_36', 'trb_per_minute_36'],
    'pa': ['pts_per_minute_36', 'ast_per_minute_36'],
    'ra': ['trb_per_minute_36', 'ast_per_minute_36'],
}


def tonights_lines(day):
    if not os.path.exists(PICKS):
        return []
    best = {}
    for ln in open(PICKS):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('kind') != 'book_prop' or r.get('day') != day:
            continue
        best[(norm_name(r.get('player')), norm_market(r.get('market')))] = r
    return list(best.values())


def phi(z):
    """Standard normal CDF, no scipy."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def model_sd(mu, market, form):
    """Game-to-game spread of a counting stat.

    The player's own standard deviation when there are enough games to
    mean anything; otherwise a heuristic. NBA scoring runs roughly a
    third of the mean in standard deviation, and low-count stats
    behave closer to Poisson, so the larger of the two is the safer
    assumption -- understating variance would manufacture edges at
    every extreme line.
    """
    if form and form.get('games', 0) >= 6:
        key = {'points': 'pts'}.get(market)
        if key and form.get(f'{key}_sd'):
            return form[f'{key}_sd']
    return max(math.sqrt(max(mu, 0.5)), 0.33 * mu)


# Markets that count single events, where a Poisson shape is the right
# one. Points is NOT one of them -- points arrive in twos and threes
# and a scorer's game-to-game spread is driven by minutes and usage,
# not by counting noise, so it keeps the normal. Nor are the
# composites, which are sums of three such things.
UNIT_COUNT = {'rebounds', 'assists', 'threes'}


def poisson_tail(mu, k):
    """P(X >= k) for Poisson(mu), summed term by term.

    k is small wherever this is used -- only below the variance
    crossover, so the line is in single digits -- which makes the
    direct sum exact and cheap. No incomplete gamma needed.
    """
    if mu <= 0:
        return 1.0 if k <= 0 else 0.0
    term = math.exp(-mu)
    cum = term
    for i in range(1, max(k, 1)):
        term *= mu / i
        cum += term
    return max(0.0, min(1.0, 1.0 - cum))


def model_prob_over(mu, line, market, form):
    """(P(over), which shape was used).

    Two regimes, and the switch is not a new parameter -- it is the
    crossover already inside model_sd(). Below it sqrt(mu) wins the
    max(), which means the model is already asserting Poisson
    variance; it was then applying that variance to a symmetric normal
    curve, which is incoherent and badly wrong in the tail.

    A normal at mu = 1.7 assists against a 1.5 line gave 41.7%. The
    Poisson gives 50.7%. The market said 48.2%. So the 'edge' that put
    Jock Landale on thesis B was the distribution's left skew, not a
    disagreement with anyone -- and since thesis B hunts low-minute
    players, every line it looks at sits in exactly the region where a
    normal understates the right tail. That bias manufactured unders
    for the whole screen.

    Above the crossover the spread comes from minutes and usage
    varying rather than from counting noise, 0.33*mu wins the max(),
    and the normal is the right shape.

    The half-point continuity correction stays on the normal branch:
    'over 1.5' is P(2 or more), and treating it as P(X > 1.5) on a
    continuous curve quietly shifts the answer. The Poisson branch
    does not need it -- it is already discrete, and takes the first
    integer strictly above the line, so a whole-number line pushes
    rather than wins.
    """
    sd = model_sd(mu, market, form)
    if sd <= 0:
        return None, None
    if market in UNIT_COUNT and mu > 0 and math.sqrt(mu) >= 0.33 * mu:
        return poisson_tail(mu, int(math.floor(line)) + 1), 'pois'
    return 1.0 - phi((line + 0.5 - mu) / sd), 'norm'


def prob_over(mu, line, market, form):
    """P(over) alone, for callers that do not care about the shape."""
    return model_prob_over(mu, line, market, form)[0]


def stability(form, bref):
    """(score 0-1, label, how). Higher means a more reliable role.

    Two sources, and the one in use is named rather than blended,
    because a blended number hides which half is doing the work.
    """
    if form and form.get('games', 0) >= MIN_GAMES_FOR_SD \
            and form.get('mp_cv') is not None:
        cv = form['mp_cv']
        return max(0.0, min(1.0, 1 - cv / 0.5)), f'cv {cv:.2f}', 'sd'
    if bref:
        g, gs = bref.get('games'), bref.get('games_started')
        if g:
            ratio = (gs or 0) / g
            return ratio, f'{int(gs or 0)}/{int(g)} st', 'starts'
    return 0.5, '-', 'none'


# --- the three repairs the first backtest forced ---------------------
#
# The first version of this screen ranked Maxey points at +26.5 against
# a seven-book consensus, and Khaman Maluach rebounds at -49.3 with a
# model probability of 0.0%. Kalshi's ladder independently priced the
# Maxey line near 54%, so the screen was not finding edges, it was
# reporting its own stale inputs as edges. Three causes, one each:
#
#   1. minutes taken straight off a two-game sample
#   2. disagreement measured in probability, where it is unreadable
#   3. no upper bound, so the worst data errors sorted to the top
#
# All three push the same way: the model treats last season's rate as
# truth, the market prices this season's role, and every sharp
# divergence between them is a role change rather than a mispricing.

def blend_mpg(form, base_mpg, prior=8.0):
    """(minutes, source). This season's minutes shrunk toward last.

    Taking the season average straight turned one 40-minute opener
    into a 40-minute expectation, which inflated every star on the
    board: Maxey's EXP came out at 29.8 against a 28.3 season average.
    Weight on the new sample is games/(games+prior), so after one game
    last season still carries nearly all of it, and after twenty the
    new role has taken over.

    This deliberately does the WRONG thing for a player whose role
    genuinely changed overnight -- it drags him back toward a role he
    no longer has. That case is not priceable from rates at all, so
    role_shift() below catches it and the screen declines the bet
    instead of pretending the shrunk number is good.
    """
    f_mpg = (form or {}).get('mpg')
    g = (form or {}).get('games') or 0
    if not f_mpg or not g:
        return base_mpg, 'prior'
    if not base_mpg:
        return f_mpg, 'season'
    w = g / (g + prior)
    return w * f_mpg + (1 - w) * base_mpg, f'w{w:.2f}'


def market_mu(line, prob, market, form, iters=48):
    """The mean the market's de-vigged probability implies.

    A probability gap is not a quantity anyone can check. '+26.5
    points of probability' hides whether the disagreement is half a
    point of production or eight, because the same gap means different
    things at a steep line and a flat one. 'The market expects 24 and
    we expect 28' can be argued about, and settled by a box score.

    P(over) is monotone in mu so bisection is enough. sd is recomputed
    at every step because it is itself a function of the mean -- hold
    it fixed and the inversion stops being the inverse of the model.
    """
    if prob is None or not (0.0 < prob < 1.0):
        return None
    lo, hi = 0.0, max(line * 3.0 + 3.0, line + 30.0)
    if (prob_over(hi, line, market, form) or 0) < prob:
        return None          # market is above anything this model reaches
    for _ in range(iters):
        mid = (lo + hi) / 2.0
        p = prob_over(mid, line, market, form)
        if p is None:
            return None
        if p < prob:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def role_shift(mkt_mu, rate36, base_mpg, min_events=4.0):
    """(minutes the market implies, ratio to baseline).

    min_events is a guard, not a tuning knob. The ratio is mkt_mu
    divided by a per-36 rate, so when that rate is small the division
    amplifies noise without limit: on 21 Oct it reported Keon Ellis at
    37.2 implied minutes off a 1.5-ASSIST line and a 20.5-minute
    baseline, and four of the seven watch rows that night were assists
    markets for non-handlers. A tenth of an assist in the market's
    expectation moved implied minutes by two. Below a few expected
    events the quantity means nothing and is not reported.

    Run the market's expectation back through the player's own rate to
    get the minutes load it implies. If the market prices Maluach for
    about nine rebounds and his rate says that takes twenty-seven
    minutes when he averaged nine, the screen has not found a
    mispriced line -- it has found a player whose role changed, which
    is the more useful thing and the thing the film work starts from.

    The model cannot price a role it has never observed. It can notice
    that the market is pricing one, and hand it over.
    """
    if not (mkt_mu and rate36 and base_mpg) or mkt_mu < min_events:
        return None, None
    implied = mkt_mu / rate36 * 36.0
    return implied, implied / base_mpg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', action='store_true',
                    help='cache this season\'s box scores')
    ap.add_argument('--force', action='store_true', help='rebuild all days')
    ap.add_argument('--day', help='YYYYMMDD, default today')
    ap.add_argument('--season', default='2026', help='B-Ref baseline season')
    ap.add_argument('--thesis', choices=['a', 'b'], help='only one screen')
    ap.add_argument('--min-usg', type=float, default=24.0,
                    help='thesis A: minimum usage rate')
    ap.add_argument('--min-mpg', type=float, default=29.0,
                    help='thesis A: minimum minutes')
    ap.add_argument('--max-mpg', type=float, default=29.0,
                    help='thesis B: maximum minutes')
    ap.add_argument('--soft-rank', type=int, default=16,
                    help='thesis A: opponent defence must rank this or '
                         'worse (1 = best defence, 30 = worst)')
    ap.add_argument('--tough-rank', type=int, default=14,
                    help='thesis B: opponent defence must rank this or '
                         'better')
    ap.add_argument('--min-edge', type=float, default=3.0,
                    help='probability points of disagreement with the '
                         'market required to list a row')
    ap.add_argument('--max-edge', type=float, default=12.0,
                    help='probability points of disagreement above which '
                         'the model, not the market, is assumed wrong. '
                         'Against a 7-book consensus a real edge is 2-5 '
                         'points; 26 is a stale input')
    ap.add_argument('--mpg-prior', type=float, default=8.0,
                    help='games of last-season weight when shrinking '
                         'this season\'s minutes (higher = slower to '
                         'believe a new sample)')
    ap.add_argument('--min-events', type=float, default=4.0,
                    help='expected events below which the implied-'
                         'minutes ratio is too noisy to report (a '
                         '1.5-assist line divided by a tiny per-36 rate '
                         'gives nonsense)')
    ap.add_argument('--max-z', type=float, default=0.5,
                    help='ceiling on the disagreement measured in the '
                         'stat\'s own standard deviations. A multi-book '
                         'consensus is not half a standard deviation '
                         'wrong; past this the model is')
    ap.add_argument('--role-lo', type=float, default=0.85,
                    help='flag a player as a role change when the '
                         'market implies below this fraction of his '
                         'baseline minutes')
    ap.add_argument('--role-hi', type=float, default=1.20,
                    help='and above this multiple')
    ap.add_argument('--allow-moved', action='store_true',
                    help='include players whose rate came from another '
                         'team (off by default: stale role)')
    ap.add_argument('--refresh-teams', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    if a.build:
        build(a.force)
        return 0

    day = a.day or datetime.datetime.now().astimezone().strftime('%Y%m%d')
    lines = tonights_lines(day)
    if not lines:
        print(f'no book lines for {day} in {PICKS}')
        return 1
    try:
        rt = team_ratings(a.season, a.refresh_teams)
    except Exception as e:
        print(f'  ! team ratings unavailable: {e}', file=sys.stderr)
        rt = {}
    # a past slate is screened on what was known the morning of, so
    # the cutoff is the screened day itself
    form = season_form(before=day)
    bref = bref_cache(a.season)
    by_bref = {norm_name(k): v for k, v in bref.items()}
    by_form = {norm_name(k): v for k, v in form.items()}
    if not form:
        print('no season box scores cached yet -- run --build first.\n'
              'Falling back to last season only.\n', file=sys.stderr)

    rows = []
    for r in lines:
        mk = norm_market(r.get('market'))
        cols = PER36.get(mk)
        if not cols:
            continue
        key = norm_name(r.get('player'))
        b = bref_row(by_bref.get(key), [r.get('away'), r.get('home')])
        f = by_form.get(key)
        if not b and not f:
            continue
        rate36 = sum((b or {}).get(c) or 0 for c in cols)
        g = (b or {}).get('games')
        mp = (b or {}).get('mp')
        base_mpg = (mp / g) if (g and mp) else None
        mpg, mpg_src = blend_mpg(f, base_mpg, a.mpg_prior)
        if not rate36 or not mpg:
            continue
        need = r['line'] / rate36 * 36.0
        gap = (need - mpg) / mpg * 100.0
        stab, stab_txt, stab_how = stability(f, b)
        # the player's own team decides which side of the game is the
        # opponent; the book feed only names the fixture
        own = code((f or {}).get('team') or (b or {}).get('team') or '')
        away, home = code(r.get('away')), code(r.get('home'))
        opp = home if own == away else (away if own == home else None)
        orate = rt.get(opp) if opp else None
        # a rate earned on another team describes a role he no longer
        # has. bref.py flags these; the screen must too, because the
        # biggest apparent edges are exactly these rows.
        bteam = code((b or {}).get('team') or '')
        moved = bool(bteam) and bteam not in (away, home) \
            and not bteam.endswith('TM')
        mu = rate36 * mpg / 36.0
        mp, shape = model_prob_over(mu, r['line'], mk, f)
        mkt = r.get('prob_over')
        edge = None if (mp is None or mkt is None) else (mp - mkt) * 100.0
        # the same disagreement, said in units of the stat, plus what
        # minutes load the market's number implies for this player
        mmu = market_mu(r['line'], mkt, mk, f)
        dmu = None if mmu is None else mu - mmu
        imp_mpg, role_ratio = role_shift(mmu, rate36, base_mpg or mpg,
                                         a.min_events)
        # the disagreement in units of this stat's own game-to-game
        # spread, which is the only form of it that compares across
        # markets. Jock Landale rebounds showed a -15.4 probability
        # edge on 0.8 rebounds of disagreement; SGA points showed
        # +5.0 on 1.3. Probability makes a low line look like a
        # mispricing because a small shift crosses a lot of the
        # distribution there, not because anyone disagrees much.
        sd = model_sd(mu, mk, f)
        zgap = None if (dmu is None or not sd) else dmu / sd
        usg = (b or {}).get('usg_pct')
        ast36 = (b or {}).get('ast_per_minute_36') or 0
        cold = None
        if f and f.get('recent_fg_pct') is not None \
                and f.get('fg_pct') is not None:
            cold = round(f['recent_fg_pct'] - f['fg_pct'], 3)
        rows.append({
            'player': r['player'], 'game': f"{r.get('away')}@{r.get('home')}",
            'market': mk, 'line': r['line'], 'rate36': round(rate36, 2),
            'mpg': round(mpg, 1), 'need': round(need, 1),
            'gap': round(gap, 1), 'usg': usg, 'ast36': ast36,
            'mu': round(mu, 1), 'model_p': None if mp is None else round(mp*100, 1),
            'mkt_p': None if mkt is None else round(mkt*100, 1),
            'edge': None if edge is None else round(edge, 1),
            'moved': moved,
            'stab': round(stab, 2), 'stab_txt': stab_txt,
            'stab_how': stab_how, 'games': (f or {}).get('games', 0),
            'cold': cold, 'recent_fg': (f or {}).get('recent_fg_pct'),
            'own': own, 'opp': opp,
            'opp_drtg': (orate or {}).get('drtg'),
            'opp_rank': (orate or {}).get('rank'),
            'prob_over': r.get('prob_over'),
            'mkt_mu': None if mmu is None else round(mmu, 1),
            'dmu': None if dmu is None else round(dmu, 1),
            'sd': None if not sd else round(sd, 2),
            'zgap': None if zgap is None else round(zgap, 2),
            'shape': shape,
            'base_mpg': None if base_mpg is None else round(base_mpg, 1),
            'imp_mpg': None if imp_mpg is None else round(imp_mpg, 1),
            'role_ratio': None if role_ratio is None else round(role_ratio, 2),
            'mpg_src': mpg_src,
        })

    def role_stable(x):
        """True when the market and the baseline agree about the role.

        A row failing this is not a bet either way. The model's whole
        expectation is rate x minutes, and if the market is pricing
        different minutes than the baseline records, the expectation
        is built on a role that no longer exists -- in whichever
        direction. Those rows go to the watch list.
        """
        rr = x['role_ratio']
        return rr is None or (a.role_lo <= rr <= a.role_hi)

    def plausible(x):
        """Disagreement inside the band where it can be real.

        Below --min-edge it is noise. Above --max-edge the model is
        the broken thing: a 7-book consensus that has been shopped all
        day is not 26 points wrong, and every time this screen has
        said so it has been the screen's own input that was stale.

        --max-z is the same ceiling said properly. The probability
        band cannot be set once and mean one thing, because the same
        number of points means a lot at a low line and little at a
        high one. zGAP is the disagreement over the stat's own spread,
        so one threshold holds across rebounds and PRA alike.
        """
        e, z = x['edge'], x['zgap']
        if e is None or z is None:
            return False
        return a.min_edge <= abs(e) <= a.max_edge and abs(z) <= a.max_z

    # --- thesis A: high-usage handler, line inside his rate ----------
    a_base = [x for x in rows
              if x['market'] in ('points', 'threes', 'pra', 'pa')
              and (x['usg'] or 0) >= a.min_usg
              and x['mpg'] >= a.min_mpg
              and x['ast36'] >= 3.5
              and (x['edge'] is not None and x['edge'] > 0)
              and (a.allow_moved or not x['moved'])
              and (x['opp_rank'] is None or x['opp_rank'] >= a.soft_rank)]
    a_rows = [x for x in a_base if plausible(x) and role_stable(x)]
    a_rows.sort(key=lambda x: -(x['edge'] or 0))

    # --- thesis B: unreliable minutes, cold, line needs more ---------
    b_base = [x for x in rows
              if x['mpg'] <= a.max_mpg
              and x['stab'] < 0.6
              and (x['cold'] is None or x['cold'] <= 0)
              and (a.allow_moved or not x['moved'])
              and (x['edge'] is not None and x['edge'] < 0)
              and (x['opp_rank'] is None or x['opp_rank'] <= a.tough_rank)]
    b_rows = [x for x in b_base if plausible(x) and role_stable(x)]
    b_rows.sort(key=lambda x: (x['edge'] or 0))

    # --- what the two screens threw away, and why --------------------
    # A row dropped silently is a row that comes back as a surprise.
    dropped = [x for x in (a_base + b_base)
               if not (plausible(x) and role_stable(x))]
    d_role = [x for x in dropped if not role_stable(x)]
    d_big = [x for x in dropped if role_stable(x) and x['edge'] is not None
             and (abs(x['edge']) > a.max_edge
                  or (x['zgap'] is not None and abs(x['zgap']) > a.max_z))]
    d_small = [x for x in dropped if role_stable(x)
               and x['edge'] is not None and abs(x['edge']) < a.min_edge]

    # --- role change watch: one row per player -----------------------
    # The market's view of a player's role is a fact about him, not
    # about the particular market he happens to be quoted in, so the
    # widest-diverging line stands in for all of his.
    watch = {}
    for x in sorted(d_role,
                    key=lambda r: -abs(math.log(max(r['role_ratio'], 1e-6)))):
        watch.setdefault(x['player'], x)
    watch = list(watch.values())

    if a.json:
        print(json.dumps({'day': day, 'thesis_a': a_rows,
                          'thesis_b': b_rows, 'role_watch': watch,
                          'dropped': {'role': len(d_role),
                                      'implausible': len(d_big),
                                      'noise': len(d_small)}}, indent=1))
        return 0

    def table(title, rs, note):
        print(f'\n{title}')
        hdr = (f"{'PLAYER':20}{'MKT':8}{'LINE':>6}{'WE':>6}{'THEY':>7}"
               f"{'dEXP':>7}{'zGAP':>7}{'MODEL':>7}{'D':>2}{'MKT':>7}"
               f"{'EDGE':>7}{'STABILITY':>11}{'OPP D':>13}")
        print(hdr)
        print('-' * len(hdr))
        if not rs:
            print('  nothing clears the filters tonight')
        for x in rs[:18]:
            od = ('-' if x['opp_drtg'] is None
                  else f"{x['opp']} {x['opp_drtg']:.1f} #{x['opp_rank']}")
            nm = x['player'][:18] + ('*' if x['moved'] else '')
            they = '-' if x['mkt_mu'] is None else f"{x['mkt_mu']:.1f}"
            dm = '-' if x['dmu'] is None else f"{x['dmu']:+.1f}"
            zg = '-' if x['zgap'] is None else f"{x['zgap']:+.2f}"
            print(f"{nm[:19]:20}{x['market']:8}"
                  f"{x['line']:>6}{x['mu']:>6.1f}{they:>7}{dm:>7}{zg:>7}"
                  f"{x['model_p']:>6.1f}%"
                  f"{('P' if x['shape'] == 'pois' else 'n'):>2}"
                  f"{x['mkt_p']:>6.1f}%{x['edge']:>+7.1f}"
                  f"{x['stab_txt']:>11}{od:>13}")
        print(note)

    cols = ('\n  WE is rate x shrunk minutes. THEY is the mean the '
            'market\'s\n  price implies, recovered by inverting the same '
            'model. dEXP is\n  the disagreement in units of the stat and '
            'zGAP is that over the\n  stat\'s own spread -- those are the '
            'columns to argue about. EDGE\n  is the same disagreement in '
            'probability, which is the shape it\n  took when it was '
            'reporting 0.8 rebounds as a 15-point edge.\n  D is the '
            'distribution: P = Poisson, used on a unit-count market\n  '
            'below the variance crossover; n = normal. Points and the\n  '
            'composites are always n -- their spread comes from minutes '
            'and\n  usage varying, not from counting noise.')

    if a.thesis in (None, 'a'):
        table('THESIS A -- primary handler, line inside his rate',
              a_rows,
              '\n  usage >= %.0f, minutes >= %.0f, assists/36 >= 3.5, a\n'
              '  defence ranked #%d or worse, and the model between %.0f\n'
              '  and %.0f points ABOVE the market. Sorted by that gap.'
              % (a.min_usg, a.min_mpg, a.soft_rank, a.min_edge, a.max_edge)
              + cols)

    if a.thesis in (None, 'b'):
        table('THESIS B -- unreliable minutes, cold, line needs more',
              b_rows,
              '\n  minutes <= %.0f, a role that is not guaranteed, shooting\n'
              '  no better than his own baseline lately, a defence ranked\n'
              '  #%d or better, and the model between %.0f and %.0f points\n'
              '  BELOW the market. Sorted by that gap.'
              % (a.max_mpg, a.tough_rank, a.min_edge, a.max_edge)
              + cols)

    # --- the watch list ----------------------------------------------
    print('\nROLE CHANGE WATCH -- the market is pricing a role the '
          'baseline does not have')
    hdr = (f"{'PLAYER':20}{'MKT':9}{'LINE':>6}{'THEY':>7}"
           f"{'BASE':>7}{'IMPLIED':>9}{'x':>6}   READ")
    print(hdr)
    print('-' * 86)
    if not watch:
        print('  no divergence outside %.2f-%.2f of baseline minutes'
              % (a.role_lo, a.role_hi))
    for x in watch[:14]:
        rr = x['role_ratio']
        read = ('bigger role than the baseline' if rr > 1
                else 'smaller role than the baseline')
        print(f"{x['player'][:19]:20}{x['market']:9}{x['line']:>6}"
              f"{(x['mkt_mu'] or 0):>7.1f}{(x['base_mpg'] or 0):>7.1f}"
              f"{(x['imp_mpg'] or 0):>9.1f}{rr:>6.2f}   {read}")
    print('\n  Not picks. These are the players whose minutes the market\n'
          '  has repriced since the baseline was earned, found by running\n'
          '  THEY back through the player\'s own per-36 rate to see what\n'
          '  minutes load it would take. The model cannot price a role it\n'
          '  has never seen, so it hands these over for the film instead\n'
          '  of betting them. Maluach was top of this list on the 21st,\n'
          '  correctly -- Mark Williams\' shoulder had made him a starter.')

    print('\ndropped: %d on role divergence, %d as implausible '
          '(|edge| > %.0f or |zGAP| > %.2f), %d as noise (|edge| < %.0f)'
          % (len(d_role), len(d_big), a.max_edge, a.max_z,
             len(d_small), a.min_edge))
    if d_big:
        # named, because a row that disappears without a name comes
        # back as a surprise in a week
        seen = []
        for x in sorted(d_big, key=lambda r: -abs(r['zgap'] or 0)):
            tag = f"{x['player']} {x['market']} ({x['zgap']:+.2f} sd)"
            if tag not in seen:
                seen.append(tag)
        print('  implausible: ' + '; '.join(seen[:8]))

    used = sorted({d for d in (json.load(open(cache_path())).get('days')
                   if os.path.exists(cache_path()) else {}) if d < day})
    print(f"\nForm from {len(used)} prior day(s)"
          + (f": {used[0]}..{used[-1]}" if used else " (none -- last "
             "season's rates only)") + f", screening {day}.")
    how = {x['stab_how'] for x in rows}
    print(f"\nStability from: {', '.join(sorted(how)) or 'none'}."
          f"  ('starts' = last season's starts/appearances; 'sd' = this\n"
          f"  season's minutes variance, used once a player has "
          f"{MIN_GAMES_FOR_SD}+ games.)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
