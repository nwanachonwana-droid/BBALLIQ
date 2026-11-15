#!/usr/bin/env python3
"""MINUTES -- the half of every projection that was never modelled.

THE GAP THIS CLOSES
  Every number defense.py produces comes out of one line:

      poss = pace * (mpg / 48.0) * (usg / 100.0)

  The rate side of that is careful work: zone shares weighted by their
  own year-over-year persistence, KEEP gating which defences can be
  read from last season at all, drift on role. The minutes side is a
  season average. `grep -ci spread defense.py` returns 0, and so does
  blowout, garbage and favourite.

  Minutes carries more variance than rate and -- this is the point --
  more KNOWABLE variance. A seventeen-point favourite's starters sit
  in the fourth quarter. A man whose team-mate is out plays a bigger
  role than his season average describes. Neither fact is subtle and
  neither was anywhere in the screen.

THREE THINGS, ONE PROJECT
  1. spread -> minutes. Blowout risk, from the posted spread.
  2. the defence about to get better. A soft eight-game sample
     produced while the rim protector was hurt is not a soft defence,
     and he may be active tonight.
  3. redistribution. Whose minutes move when a team-mate sits.

WHY EVERY COEFFICIENT HERE IS MEASURED AND NOT CHOSEN
  I refused to model redistribution once on the grounds that I would
  be inventing coefficients. That was wrong, and worth saying plainly:
  the games are already in ~/kalshi_data/season_logs.json. What
  happened to a player's minutes in the games a team-mate missed is a
  measurement. What happens to starters' minutes as the final margin
  grows is a measurement. Both are computed here from cached box
  scores, with the sample size printed beside every figure, and a
  small sample is reported as a small sample rather than smoothed into
  a number that looks authoritative.

  The one thing not measurable from this cache is the spread-to-margin
  relationship, because historical spreads were never stored. That
  constant is named SD_MARGIN, carries its justification at the point
  of use, and is a flag.

NORMALISATION IS IMPORTED, NEVER COPIED
  defense.py owns code(), deaccent() and norm_player(). Re-deriving
  them here is how the Jalen Williams collision happened: norm_name
  was fixed in bref.py and not carried across, and a team-mate's three
  points settled another man's under as a win. One definition, one
  place, imported.

usage
  python3 minutes.py --schedule          # build/refresh the game pairings
  python3 minutes.py --margin            # fit margin -> minutes from cache
  python3 minutes.py --lines 20261028    # spreads + totals, one API call
  python3 minutes.py --defence BOS       # is this defence about to improve?
  python3 minutes.py --redist DET        # whose minutes move tonight
  python3 minutes.py --all 20261028      # everything for a slate
"""
import os, re, sys, json, time, argparse, datetime, statistics, collections
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from defense import (DATA, BASE_SEASON, code, deaccent, norm_player,
                         fetch, slate, injury_map, inj_tag)
except Exception as e:                                    # pragma: no cover
    print(f'minutes.py needs defense.py beside it: {e}', file=sys.stderr)
    print('  both live in ~/Downloads -- run install.sh', file=sys.stderr)
    raise SystemExit(2)

LOGS = os.path.join(DATA, 'season_logs.json')
SCHED = os.path.join(DATA, 'schedule.json')
GAMELINES = os.path.join(DATA, 'game_lines.json')

API = 'https://api.the-odds-api.com/v4'
SPORT = 'basketball_nba'

# Standard deviation of the final margin around the pregame spread.
#
# This is the only figure here not measured from the cache, because
# historical spreads were never stored -- season_logs.json has what
# happened, not what was expected. The NBA value sits near 11-12 points
# and has for decades; 11.5 is the usual working number. It is used for
# one purpose only: turning a spread into P(this game is a blowout), so
# an error of half a point moves a probability by a couple of points
# and changes no call. Override it if you fit it later.
SD_MARGIN = 11.5

# What counts as a rotation player when measuring minutes effects. Below
# this the minutes are noise -- a man who played four minutes in a
# blowout and four in a close game tells you nothing about either.
ROTATION_MIN = 15.0


def load_logs():
    if not os.path.exists(LOGS):
        print(f'no {LOGS} -- run edges.py --build first', file=sys.stderr)
        return {}
    return json.load(open(LOGS)).get('days') or {}


# --- the schedule -----------------------------------------------------
#
# season_logs.json stores a player's team but not his opponent, so two
# teams playing on the same night cannot be paired from it. edges.py
# walks the scoreboard when it builds and throws the pairing away;
# refetching the whole season to recover it would cost sixty calls and
# rewrite a cache that is working. So the pairings are fetched once per
# day into their own file. A past day's schedule never changes, which
# makes this cache permanent rather than daily.

def schedule(days=None, refresh=False):
    """{day: [[away, home], ...]}, cached permanently per day."""
    blob = {}
    if os.path.exists(SCHED) and not refresh:
        try:
            blob = json.load(open(SCHED))
        except Exception:
            blob = {}
    want = sorted(days if days is not None else load_logs())
    missing = [d for d in want if d not in blob]
    for i, d in enumerate(missing):
        pairs = slate(d)
        blob[d] = [list(p) for p in pairs]
        print(f'  {d}: {len(pairs)} game(s)', flush=True, file=sys.stderr)
        if i + 1 < len(missing):
            time.sleep(0.4)
    if missing:
        os.makedirs(DATA, exist_ok=True)
        json.dump(blob, open(SCHED, 'w'))
        print(f'{len(blob)} day(s) cached -> {SCHED}  ({len(missing)} new)',
              file=sys.stderr)
    return blob


# --- per-game aggregation ---------------------------------------------

def games(before=None):
    """One record per game, built from cached box scores + schedule.

    `before` drops that day and everything after it, the same
    walk-forward rule the rest of the project runs on: a margin curve
    fitted on the game being screened would describe the result rather
    than predict it.
    """
    logs = load_logs()
    sched = schedule(days=list(logs), refresh=False) if logs else {}
    out = []
    for day in sorted(logs):
        if before and day >= before:
            continue
        rows = collections.defaultdict(list)
        for r in logs[day]:
            rows[code(r.get('team'))].append(r)
        for pair in sched.get(day) or []:
            away, home = code(pair[0]), code(pair[1])
            if away not in rows or home not in rows:
                continue
            rec = {'day': day, 'away': away, 'home': home, 'teams': {}}
            for t in (away, home):
                rs = rows[t]
                rec['teams'][t] = {
                    'pts': sum(x.get('pts') or 0 for x in rs),
                    'fga': sum(x.get('fga') or 0 for x in rs),
                    'fgm': sum(x.get('fgm') or 0 for x in rs),
                    'tpa': sum(x.get('tpa') or 0 for x in rs),
                    'tpm': sum(x.get('tpm') or 0 for x in rs),
                    'reb': sum(x.get('reb') or 0 for x in rs),
                    # assists were never aggregated here, which is why
                    # no assist matchup model could exist: the number a
                    # defence allows was not in the cache at team level
                    # even though every player row carries it.
                    'ast': sum(x.get('ast') or 0 for x in rs),
                    'min': sum(x.get('min') or 0 for x in rs),
                    'players': {norm_player(x.get('player')):
                                (x.get('min') or 0) for x in rs},
                }
            a, h = rec['teams'][away], rec['teams'][home]
            rec['margin'] = {away: a['pts'] - h['pts'],
                             home: h['pts'] - a['pts']}
            rec['total'] = a['pts'] + h['pts']
            out.append(rec)
    return out


def team_games(gs, team):
    return [g for g in gs if team in g['teams']]


# --- 1. spread -> minutes ---------------------------------------------

def margin_curve(gs, min_games=3):
    """Rotation players' minutes against the final margin, measured.

    Bucketed rather than regressed on purpose. The relationship is not
    a line -- nothing happens between a two-point game and an
    eight-point game, and then a cliff arrives somewhere past fifteen
    when the benches come in. A slope fitted through that averages the
    cliff away and reports a gentle trend that is wrong at both ends.

    Each player is compared against his OWN minutes, so a
    thirty-six-minute starter and a twenty-minute reserve contribute
    the same kind of number: the deviation, not the level.

    The baseline is his mean in COMPETITIVE games only -- decided by
    ten or fewer. Using his overall mean looked equivalent and is not:
    a man who plays thirty-two in close games and twenty-seven in
    blowouts has a mean of thirty, so the blowouts measure -3 and the
    close games +2, splitting one five-minute effect across two
    buckets and halving it. Anchoring on the competitive games makes
    the blowout number the whole effect and directly readable: minutes
    lost against a normal night.
    """
    # TWO PASSES, because one pass made the baseline inconsistent with
    # what it is a baseline FOR.
    #
    # The first version qualified a player AND averaged him over the
    # same filtered games: 15+ minutes only. But the deviation loop
    # below counts every appearance, including the twelve-minute
    # nights. Short games therefore sat in one population and not the
    # other, and the gap showed up as a uniform downward drift -- his
    # first real run put -0.60 and -0.09 in the two buckets that make
    # UP the baseline window, where the weighted mean is zero by
    # construction. It came to -0.293, so every figure in the table,
    # blowouts included, was that much too negative.
    #
    # So: pass one decides who is a rotation player, by the 15+ rule.
    # Pass two averages those players over ALL their competitive
    # appearances. Now the 0-5 and 6-10 buckets must come out near
    # zero, which turns the top of the table into a free check on the
    # bottom of it -- if they drift again, something else is wrong.
    rot = collections.defaultdict(int)
    for g in gs:
        if abs(g['margin'][g['away']]) > 10:
            continue
        for t, side in g['teams'].items():
            for k, m in side['players'].items():
                if m >= ROTATION_MIN:
                    rot[(k, t)] += 1
    qualified = {k for k, n in rot.items() if n >= min_games}

    comp = collections.defaultdict(list)
    for g in gs:
        if abs(g['margin'][g['away']]) > 10:
            continue
        for t, side in g['teams'].items():
            for k, m in side['players'].items():
                if (k, t) in qualified and m >= 1:
                    comp[(k, t)].append(m)
    mean = {k: statistics.fmean(v) for k, v in comp.items() if v}

    buckets = [('0-5', 0, 5), ('6-10', 6, 10), ('11-15', 11, 15),
               ('16-20', 16, 20), ('21+', 21, 999)]
    out = {}
    for label, lo, hi in buckets:
        devs, wins, losses = [], [], []
        for g in gs:
            am = abs(g['margin'][g['away']])
            if not (lo <= am <= hi):
                continue
            for t, side in g['teams'].items():
                won = g['margin'][t] > 0
                for k, m in side['players'].items():
                    mu = mean.get((k, t))
                    if mu is None or m < 1:
                        continue
                    devs.append(m - mu)
                    (wins if won else losses).append(m - mu)
        out[label] = {
            'n': len(devs),
            'dev': round(statistics.fmean(devs), 2) if devs else None,
            'winner': round(statistics.fmean(wins), 2) if wins else None,
            'loser': round(statistics.fmean(losses), 2) if losses else None,
        }
    return out, len(mean)


def blowout_prob(spread, sd=SD_MARGIN, threshold=15.0):
    """P(|final margin| >= threshold) for a game at this spread.

    Normal around the spread. Both tails count: a seventeen-point
    favourite can be blown out itself, and the benches empty either
    way, so a one-sided calculation understates the minutes risk.
    """
    import math
    s = abs(float(spread))

    def phi(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    # P(margin > thr) + P(margin < -thr), margin ~ N(s, sd)
    return (1.0 - phi((threshold - s) / sd)) + phi((-threshold - s) / sd)


def minutes_risk(spread, curve, threshold=15.0):
    """(P(blowout), E[dev] vs a competitive game, EXCESS vs an average one).

    The third figure is the one to apply, and the difference is not
    cosmetic. E[dev] is measured against a competitive-game baseline,
    so it is negative at every spread -- a pick'em game still carries
    a one-in-five chance of turning into a blowout. But defense.py's
    minutes come from a SEASON AVERAGE, which already contains that
    season's blowouts. Subtracting E[dev] from a season average would
    charge the player for blowout risk twice and move every projection
    down, which is not a correction, just a thumb on the scale.

    So EXCESS is E[dev | this spread] minus E[dev | a league-average
    game], where the second term is the same curve weighted by how
    often each margin bucket actually occurred. That is the quantity a
    season average does not already know: positive for a close game,
    negative for a mismatch, and zero for a typical one.
    """
    hi = [curve[b] for b in ('16-20', '21+') if curve.get(b)]
    lo = [curve[b] for b in ('0-5', '6-10', '11-15') if curve.get(b)]
    hi = [c for c in hi if c['dev'] is not None and c['n'] >= 20]
    lo = [c for c in lo if c['dev'] is not None and c['n'] >= 20]
    if not hi or not lo:
        return None, None, None
    wh = sum(c['n'] for c in hi)
    wl = sum(c['n'] for c in lo)
    dev_hi = sum(c['dev'] * c['n'] for c in hi) / wh
    dev_lo = sum(c['dev'] * c['n'] for c in lo) / wl
    p = blowout_prob(spread, threshold=threshold)
    e = p * dev_hi + (1 - p) * dev_lo
    # the average game, as this season has actually played out
    p_base = wh / (wh + wl)
    e_base = p_base * dev_hi + (1 - p_base) * dev_lo
    return p, e, e - e_base


# --- 2. the defence about to get better -------------------------------

def def_profile(gs, team, only_days=None, skip_days=None):
    """What `team` allowed, per game, over a subset of its games.

    Zone splits are not available here -- they come from
    Basketball-Reference season tables, which are season aggregates
    and cannot be sliced by game. So this reports the coarse measures
    that ARE per-game: points, field-goal percentage, three-point
    volume and accuracy allowed, and a possessions estimate. Coarse is
    enough for the question being asked, which is whether the sample
    was produced by a different team.
    """
    rows = []
    for g in team_games(gs, team):
        if only_days is not None and g['day'] not in only_days:
            continue
        if skip_days and g['day'] in skip_days:
            continue
        opp = g['home'] if g['away'] == team else g['away']
        o = g['teams'][opp]
        poss = o['fga'] + 0.44 * max(0, o['pts'] - 2 * o['fgm'] - o['tpm'])
        rows.append({
            'opp_pts': o['pts'],
            'opp_fg_pct': (o['fgm'] / o['fga'] * 100) if o['fga'] else None,
            'opp_fg3a': o['tpa'],
            'opp_fg3_pct': (o['tpm'] / o['tpa'] * 100) if o['tpa'] else None,
            'poss': poss,
            'opp_pts_100': (o['pts'] / poss * 100) if poss else None,
        })
    if not rows:
        return None
    out = {'n': len(rows)}
    for k in ('opp_pts', 'opp_fg_pct', 'opp_fg3a', 'opp_fg3_pct',
              'opp_pts_100'):
        vals = [r[k] for r in rows if r[k] is not None]
        out[k] = round(statistics.fmean(vals), 1) if vals else None
    return out


def def_rating_sd(gs, team):
    """Per-game SD of what this team allows per 100, from its own games.

    Measured rather than assumed, so a team with wild variance is held
    to a wider bar than a steady one.
    """
    vals = []
    for g in team_games(gs, team):
        opp = g['home'] if g['away'] == team else g['away']
        o = g['teams'][opp]
        poss = o['fga'] + 0.44 * max(0, o['pts'] - 2 * o['fgm'] - o['tpm'])
        if poss:
            vals.append(o['pts'] / poss * 100)
    if len(vals) < 3:
        return None
    return statistics.stdev(vals)


def defence_improving(gs, team, inj, min_each=2):
    """Was this defence's bad sample produced without someone who is back?

    The original requirement for the whole screen was teams with "no
    reason to believe their defense will be significantly better".
    KEEP answers that for roster turnover between seasons. It says
    nothing about a team whose anchor missed the last eight games and
    is active tonight -- which is the same trap wearing different
    clothes, and the one the injury feed can actually see.

    For every player who has missed games for this team, the team's
    defence is measured over the games he missed against the games he
    played. A player still listed OUT is skipped: his absence is
    already priced into tonight.
    """
    tg = team_games(gs, team)
    if not tg:
        return []
    played = collections.defaultdict(set)
    for g in tg:
        for k, m in g['teams'][team]['players'].items():
            if m and m > 0:
                played[k].add(g['day'])
    all_days = {g['day'] for g in tg}
    minutes = collections.defaultdict(list)
    for g in tg:
        for k, m in g['teams'][team]['players'].items():
            minutes[k].append(m or 0)

    by_key = {}
    for k, v in (inj or {}).items():
        if code(v.get('team')) == team:
            by_key[norm_player(v.get('name'))] = v

    out = []
    for k, days in played.items():
        missed = all_days - days
        if len(missed) < min_each or len(days) < min_each:
            continue
        mpg = statistics.fmean([m for m in minutes[k] if m > 0] or [0])
        if mpg < ROTATION_MIN:
            continue
        info = by_key.get(k)
        tag = inj_tag(info) if info else None
        if tag == 'OUT':
            continue            # already absent tonight, nothing to warn
        with_him = def_profile(gs, team, only_days=days)
        without = def_profile(gs, team, only_days=missed)
        if not with_him or not without:
            continue
        a, b = with_him.get('opp_pts_100'), without.get('opp_pts_100')
        swing = round(b - a, 1) if (a is not None and b is not None) else None
        # HOW MANY STANDARD ERRORS, not how many points.
        #
        # A team's defensive rating swings about 11 per 100 from game
        # to game, so the difference of a two-game mean and another
        # two-game mean carries a standard error near 11 all by
        # itself. The first version flagged anything past 4.0 as "the
        # matchup is a mirage" and duly reported three findings at
        # 2g-vs-2g -- +18.3, -13.4, -10.9 -- of which the largest was
        # 1.66 SE. All three were noise, printed in the voice of a
        # conclusion. A fixed points threshold cannot tell those from
        # a real signal; a standard error can.
        sd = def_rating_sd(gs, team)
        se = None
        if sd and with_him['n'] and without['n']:
            se = (sd ** 2 / with_him['n'] + sd ** 2 / without['n']) ** 0.5
        out.append({
            'player': k, 'mpg': round(mpg, 1), 'status': tag or 'active',
            'detail': (info or {}).get('detail'),
            'games_with': with_him['n'], 'games_without': without['n'],
            'with': with_him, 'without': without, 'swing': swing,
            'se': None if se is None else round(se, 1),
            'z': None if (se in (None, 0) or swing is None)
                 else round(swing / se, 2),
        })
    out.sort(key=lambda r: -(r['z'] if r['z'] is not None else -9))
    return out


# --- 3. redistribution ------------------------------------------------

def absence_delta(gs, team, absent_key, min_each=2):
    """Each team-mate's minutes with and without `absent_key`, measured.

    No coefficients. The games where he sat are in the cache; what
    everyone else did in them is a fact. Sample sizes are printed
    because they are usually small, and a two-game delta is a hint
    rather than a projection -- which is a reason to show n, not a
    reason to leave the measurement out.
    """
    tg = team_games(gs, team)
    with_days, without_days = set(), set()
    for g in tg:
        m = g['teams'][team]['players'].get(absent_key, 0) or 0
        (with_days if m > 0 else without_days).add(g['day'])
    if len(without_days) < min_each or len(with_days) < min_each:
        return None

    agg = collections.defaultdict(lambda: {'with': [], 'without': []})
    for g in tg:
        side = g['teams'][team]
        bucket = 'with' if g['day'] in with_days else 'without'
        for k, m in side['players'].items():
            if k == absent_key:
                continue
            agg[k][bucket].append(m or 0)

    out = []
    for k, v in agg.items():
        if len(v['with']) < min_each or len(v['without']) < min_each:
            continue
        a = statistics.fmean(v['with'])
        b = statistics.fmean(v['without'])
        if max(a, b) < ROTATION_MIN * 0.5:
            continue
        out.append({'player': k, 'with': round(a, 1), 'without': round(b, 1),
                    'delta': round(b - a, 1),
                    'n_with': len(v['with']), 'n_without': len(v['without'])})
    out.sort(key=lambda r: -r['delta'])
    return {'absent': absent_key, 'games_without': len(without_days),
            'games_with': len(with_days), 'rows': out}


def redistribution(gs, team, inj, tags=('OUT', 'GTD')):
    """Measured minutes movement for every absent-or-doubtful team-mate."""
    out = []
    for v in (inj or {}).values():
        if code(v.get('team')) != team:
            continue
        tag = inj_tag(v)
        if tag not in tags:
            continue
        k = norm_player(v.get('name'))
        d = absence_delta(gs, team, k)
        if d:
            d['status'] = tag
            d['name'] = v.get('name')
            d['detail'] = v.get('detail')
            out.append(d)
    return out


# --- the posted lines -------------------------------------------------
#
# One call to the bulk endpoint returns spreads AND totals for the whole
# slate, rather than the per-event endpoint props_lines.py needs for
# player markets. Two credits for the night instead of two per game --
# and the totals half closes a separate bug: defense.py --totals has
# been comparing its model against a league baseline rather than the
# number actually posted, which is how a 225.4 model on a 219.5 line
# got called an under when it was a five-point over.

class ApiError(RuntimeError):
    pass


def _get(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        h = dict(e.headers)
        if e.code == 401:
            raise ApiError('401 - key rejected. Is ODDS_API_KEY set? '
                           '(. ~/.sharpz_env)') from None
        if e.code == 429:
            raise ApiError('429 - out of credits. remaining='
                           f"{h.get('x-requests-remaining')}") from None
        raise ApiError(f'HTTP {e.code}: {body}') from None
    except urllib.error.URLError as e:
        raise ApiError(f'could not reach the API: {e.reason}') from None


def game_lines(day, refresh=False, regions='us'):
    """{team: {'spread': float, 'total': float, 'opp': team}} for a day.

    Spread is from that team's point of view: negative means favoured.
    Cached per day -- a second run in the same evening costs nothing,
    which matters on a metered key.
    """
    blob = {}
    if os.path.exists(GAMELINES) and not refresh:
        try:
            blob = json.load(open(GAMELINES))
        except Exception:
            blob = {}
    if day in blob and not refresh:
        return blob[day]

    key = os.environ.get('ODDS_API_KEY', '').strip()
    if not key:
        raise ApiError('ODDS_API_KEY is not set.  . ~/.sharpz_env')
    url = (f'{API}/sports/{SPORT}/odds?apiKey={key}&regions={regions}'
           f'&markets=spreads,totals&oddsFormat=american')
    payload, hdrs = _get(url)
    rem = hdrs.get('x-requests-remaining')

    out = {}
    for ev in payload:
        # FILTER BY TIP TIME, or the table is quietly wrong.
        #
        # This endpoint returns every upcoming event, not the requested
        # day's. Without this the first run mixed three nights together
        # and -- because the result is keyed by team -- a later event
        # overwrote an earlier one, so ATL showed MIA as its opponent
        # while MIA was listed against BOS, and LAL appeared as the
        # opponent of POR, PHI and LAC at once. Nothing errored; it
        # printed 26 tidy rows of nonsense. props_lines.py has always
        # done this and the logic simply was not carried across.
        #
        # Compared in the machine's own zone: a 7pm Eastern tip is
        # tonight, and 23:00 UTC is not.
        ct = ev.get('commence_time')
        if not ct:
            continue
        when = (datetime.datetime.fromisoformat(ct.replace('Z', '+00:00'))
                .astimezone())
        if when.strftime('%Y%m%d') != day:
            continue
        home, away = code(ev.get('home_team')), code(ev.get('away_team'))
        # median across books, because one book hanging a stale number
        # should not decide whether a man's minutes get flagged
        spreads = collections.defaultdict(list)
        totals = []
        for bk in ev.get('bookmakers') or []:
            for mk in bk.get('markets') or []:
                if mk.get('key') == 'spreads':
                    for o in mk.get('outcomes') or []:
                        t = code(o.get('name'))
                        if o.get('point') is not None:
                            spreads[t].append(float(o['point']))
                elif mk.get('key') == 'totals':
                    for o in mk.get('outcomes') or []:
                        if o.get('point') is not None:
                            totals.append(float(o['point']))
        tot = round(statistics.median(totals), 1) if totals else None
        for t, opp in ((home, away), (away, home)):
            if not spreads.get(t):
                continue
            # a team cannot play twice in a night, so a second write is
            # a filter failure rather than a schedule quirk -- say so
            # instead of silently keeping whichever arrived last
            if t in out and out[t]['opp'] != opp:
                print(f"  ! {t} appears against both {out[t]['opp']} and "
                      f"{opp} on {day} -- tip-time filter failed, "
                      f"not trusting this row", file=sys.stderr)
                out[t] = dict(out[t], suspect=True)
                continue
            out[t] = {'spread': round(statistics.median(spreads[t]), 1),
                      'total': tot, 'opp': opp, 'home': t == home,
                      'tip': when.strftime('%H:%M')}
    blob[day] = out
    os.makedirs(DATA, exist_ok=True)
    json.dump(blob, open(GAMELINES, 'w'))
    print(f'  {len(out)//2 if out else 0} game(s) priced'
          + (f', {rem} API credits left' if rem else ''), file=sys.stderr)
    return out


# --- reporting --------------------------------------------------------

def show_margin(curve, n_players):
    print(f'\nMARGIN -> MINUTES  (measured, {n_players} rotation players)')
    print(f"  {'MARGIN':>8}{'N':>7}{'ALL':>8}{'WINNER':>9}{'LOSER':>8}")
    for label in ('0-5', '6-10', '11-15', '16-20', '21+'):
        c = curve.get(label) or {}
        f = lambda v: '    -' if v is None else f'{v:+5.2f}'
        print(f"  {label:>8}{c.get('n', 0):>7}{f(c.get('dev')):>8}"
              f"{f(c.get('winner')):>9}{f(c.get('loser')):>8}")
    chk = [(curve.get(b) or {}) for b in ('0-5', '6-10')]
    cn = sum(c.get('n', 0) for c in chk)
    if cn:
        drift = sum((c.get('dev') or 0) * c.get('n', 0) for c in chk) / cn
        flag = '' if abs(drift) < 0.15 else '   << should be ~0, estimator bug'
        print(f'  check: baseline-window buckets average {drift:+.3f}{flag}')
    print("  Minutes against each player's own baseline in COMPETITIVE\n"
          '  games (decided by 10 or fewer), so a blowout figure reads as\n'
          '  minutes lost against a normal night. The winner/loser split\n'
          '  matters: a favourite resting starters and a beaten team\n'
          '  emptying its bench are not the same bet.')
    # The winner/loser gap is the part worth acting on, so it gets
    # stated rather than left for the reader to spot in two columns.
    hi = [curve.get(l) or {} for l in ('16-20', '21+')]
    wn = [c['winner'] for c in hi if c.get('winner') is not None]
    ls = [c['loser'] for c in hi if c.get('loser') is not None]
    if wn and ls:
        w, l = statistics.fmean(wn), statistics.fmean(ls)
        if abs(l) > abs(w) * 1.4:
            print(f'  ** In blowouts the LOSING side sheds {l:+.2f} against '
                  f'{w:+.2f} for the\n     winner. A beaten team empties its '
                  f'bench harder than a winning one\n     rests starters, so '
                  f'an under on a big underdog is the stronger\n     side of '
                  f'the same fact -- which is the opposite of the usual\n'
                  f'     instinct about favourites resting stars.')
        elif abs(w) > abs(l) * 1.4:
            print(f'  ** In blowouts the WINNING side sheds {w:+.2f} against '
                  f'{l:+.2f} for the\n     loser: favourites rest starters '
                  f'harder than beaten teams pull\n     theirs.')

    thin = [l for l in ('16-20', '21+')
            if (curve.get(l) or {}).get('n', 0) < 20]
    if thin:
        ns = ', '.join(f"{l} (n={(curve.get(l) or {}).get('n', 0)})"
                       for l in thin)
        print(f'  Thin so far: {ns}. N counts player-games, not games, and\n'
              '  a player needs 3 competitive games to earn a baseline at '
              'all --\n  so in late October most of the league has not '
              'qualified. The shape\n  above is suggestive and the '
              'magnitudes are not yet trustworthy.')


def show_defence(rows, team, n_games=None, min_each=2):
    print(f'\nDEFENCE ABOUT TO IMPROVE? -- {team}')
    if not rows:
        # "nobody qualified" and "it is too early for anybody to
        # qualify" look identical and mean opposite things. A split
        # needs min_each games on each side, so it is arithmetically
        # impossible until the team has played 2*min_each -- saying
        # that with the number is the difference between a finding and
        # a shrug.
        if n_games is not None and n_games < 2 * min_each:
            print(f'  {team} has played {n_games} games. A with/without '
                  f'split needs {min_each}\n  on each side, so no player '
                  f'CAN qualify yet -- this is the calendar,\n  not a '
                  f'clean bill of health. Re-run once teams reach '
                  f'{2 * min_each}+ games.')
        else:
            print(f'  nobody on this roster has both missed and played '
                  f'{min_each}+ games'
                  + (f' in {n_games}' if n_games else '')
                  + '.\n  Nothing to warn about.')
        return
    for r in rows:
        w, o = r['with'], r['without']
        print(f"  {r['player']:24}{r['mpg']:>5.1f} mpg   {r['status']}"
              + (f"  ({r['detail']})" if r.get('detail') else ''))
        print(f"      with him    {r['games_with']:>2}g  "
              f"{w['opp_pts_100']} allowed/100   FG {w['opp_fg_pct']}%  "
              f"3PA {w['opp_fg3a']}")
        print(f"      without him {r['games_without']:>2}g  "
              f"{o['opp_pts_100']} allowed/100   FG {o['opp_fg_pct']}%  "
              f"3PA {o['opp_fg3a']}")
        if r['swing'] is None:
            continue
        z, se = r['z'], r['se']
        if z is None:
            print(f"      {r['swing']:+.1f}/100 without him, significance "
                  f"unknown (needs 3+ games\n      to estimate this team's "
                  f"game-to-game variance).")
        elif z >= 2.0:
            print(f"      >> {r['swing']:+.1f}/100 worse without him -- "
                  f"{z:.1f} SE (SE {se:.1f}).\n         If the soft numbers "
                  f"came from those games and he plays, the\n         "
                  f"matchup is a mirage.")
        else:
            need = round(2.0 * se, 0) if se else None
            print(f"      {r['swing']:+.1f}/100 without him, but only "
                  f"{z:.1f} SE (SE {se:.1f}) --\n      indistinguishable "
                  f"from noise. This split would need "
                  f"{need:+.0f}/100\n      at these sample sizes to mean "
                  f"anything.")


def show_redist(blocks, team, n_games=None, min_each=2):
    print(f'\nREDISTRIBUTION -- {team}')
    if not blocks:
        if n_games is not None and n_games < 2 * min_each:
            print(f'  {team} has played {n_games} games -- too few for any '
                  f'with/without\n  split. Not measurable yet rather than '
                  f'nothing to measure.')
        else:
            print('  no absent team-mate has enough games on both sides to\n'
                  '  measure. The role column is the only guide tonight.')
        return
    for b in blocks:
        print(f"  {b['name']} {b['status']}"
              + (f" ({b['detail']})" if b.get('detail') else '')
              + f"  -- {b['games_without']}g without, "
                f"{b['games_with']}g with")
        if not b['rows']:
            print('      nobody else has enough games on both sides')
            continue
        for r in b['rows'][:6]:
            flag = '  <<' if abs(r['delta']) >= 4 else ''
            print(f"      {r['player']:24}{r['with']:>6.1f} -> "
                  f"{r['without']:>5.1f}   {r['delta']:+5.1f} min"
                  f"   (n {r['n_with']}/{r['n_without']}){flag}")
        print('      Measured, not modelled. Small n is a hint, not a '
              'projection.')


def show_lines(lines, curve, day):
    print(f'\nPOSTED LINES -- {day}')
    if not lines:
        print('  nothing priced yet')
        return
    print(f"  {'TEAM':5}{'OPP':5}{'SPREAD':>8}{'TOTAL':>7}"
          f"{'P(blow)':>9}{'E[min]':>8}{'APPLY':>8}")
    for t, v in sorted(lines.items(), key=lambda kv: kv[1]['spread']):
        p, dev, excess = minutes_risk(v['spread'], curve)
        ps = '      -' if p is None else f'{p*100:>6.0f}%'
        ds = '      -' if dev is None else f'{dev:+6.2f}'
        xs = '      -' if excess is None else f'{excess:+6.2f}'
        flag = '  <<' if (excess is not None and excess <= -1.0) else ''
        print(f"  {t:5}{v['opp']:5}{v['spread']:>+8.1f}"
              f"{(v['total'] or 0):>7.1f}{ps:>9}{ds:>8}{xs:>8}{flag}")
    print('  P(blow) is P(final margin 15+), both tails, normal around the\n'
          f'  spread with SD {SD_MARGIN}. E[min] is the expected deviation '
          'against a\n  competitive game. APPLY is the only column to add '
          'to a season-average\n  minutes figure: E[min] minus what an '
          'average game already costs, so\n  blowout risk is not charged '
          'twice. Negative means fewer minutes than\n  the season average '
          'implies; positive means more.')


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
BUILD = '2026-10-30a'


def _stamp(name):
    import sys as _s
    print(f'[{name} build {BUILD}]', file=_s.stderr)

def main():
    ap = argparse.ArgumentParser(
        description='game-specific minutes: spread, returning defenders, '
                    'redistribution')
    ap.add_argument('--schedule', action='store_true',
                    help='build/refresh the game pairings cache')
    ap.add_argument('--margin', action='store_true',
                    help='fit margin -> minutes from cached box scores')
    ap.add_argument('--lines', metavar='DAY',
                    help='fetch + cache spreads and totals for a day')
    ap.add_argument('--defence', metavar='TEAM',
                    help='is this defence about to get better?')
    ap.add_argument('--redist', metavar='TEAM',
                    help='whose minutes move with team-mates out')
    ap.add_argument('--all', metavar='DAY',
                    help='everything for one slate')
    ap.add_argument('--before', metavar='DAY',
                    help='walk-forward: ignore this day and later')
    ap.add_argument('--min-baseline', type=int, default=3,
                    dest='min_baseline',
                    help='competitive games a player needs before his '
                         'minutes count toward the curve (default 3, '
                         'auto-relaxed to 2 early in a season)')
    ap.add_argument('--refresh', action='store_true',
                    help='bypass the caches')
    ap.add_argument('--json', action='store_true')
    _stamp('minutes.py')
    a = ap.parse_args()

    if not any((a.schedule, a.margin, a.lines, a.defence, a.redist, a.all)):
        ap.print_help()
        return 2

    if a.schedule:
        schedule(refresh=a.refresh)
        if not (a.margin or a.lines or a.defence or a.redist or a.all):
            return 0

    gs = games(before=a.before)
    if not gs:
        print('no games could be paired. Run --schedule, and check that\n'
              'edges.py --build has populated season_logs.json.',
              file=sys.stderr)
        return 1
    curve, n_players = margin_curve(gs, min_games=a.min_baseline)
    # EARLY SEASON: widen the baseline rather than lower the gate.
    #
    # The blowout buckets stay empty not because blowouts are rare but
    # because almost nobody has earned a baseline: three competitive
    # games, when teams have played three or four. Dropping the n>=20
    # requirement to force a number out would be the wrong repair --
    # the gate is what stops a four-game average from steering a bet.
    # Asking for two baseline games instead of three admits far more
    # players; each deviation is noisier, but the bucket mean averages
    # hundreds of them, so the mean gets BETTER, not worse. Done
    # automatically and announced, because a figure computed under
    # different rules than the ones documented is how this project
    # has lost mornings before.
    relaxed = None
    if a.min_baseline > 2:
        hi_n = sum((curve.get(b) or {}).get('n', 0) for b in ('16-20', '21+'))
        if hi_n < 40:
            c2, p2 = margin_curve(gs, min_games=2)
            n2 = sum((c2.get(b) or {}).get('n', 0) for b in ('16-20', '21+'))
            if n2 > hi_n:
                relaxed = (hi_n, n2)
                curve, n_players = c2, p2
    out = {'games': len(gs), 'curve': curve, 'rotation_players': n_players,
           'baseline_games': 2 if relaxed else a.min_baseline}

    day = a.all or a.lines
    teams = []
    if a.all:
        for pair in slate(a.all):
            teams += [code(pair[0]), code(pair[1])]
    for t in (a.defence, a.redist):
        if t:
            teams.append(code(t))

    inj = injury_map(refresh=a.refresh) if teams else {}

    # shown whenever the lines are, because show_lines refers to this
    # table and a column that says "from the measured curve above" with
    # no curve above it is just a number to take on faith
    if (a.margin or a.all or a.lines) and not a.json:
        print(f'{len(gs)} games paired from cache')
        if relaxed:
            print(f'  baseline relaxed to 2 competitive games: the blowout '
                  f'buckets held\n  {relaxed[0]} player-games at 3, '
                  f'{relaxed[1]} at 2. The n>=20 gate below is unchanged.')
        show_margin(curve, n_players)

    if day:
        try:
            lines = game_lines(day, refresh=a.refresh)
            out['lines'] = lines
            if not a.json:
                show_lines(lines, curve, day)
        except ApiError as e:
            print(f'  ! lines unavailable: {e}', file=sys.stderr)

    played = collections.Counter()
    for g in gs:
        for t in g['teams']:
            played[t] += 1

    # In --all mode only the teams with something to say get a block.
    # The first run printed 24 identical "nothing to warn about"
    # paragraphs, 48 blocks for one slate, and the one real finding
    # would have been invisible inside it. Asked about a single team,
    # the empty answer is the answer and still prints.
    def sweep(which, fn, show):
        if which:
            t = code(which)
            res = fn(gs, t, inj)
            out.setdefault(show.__name__, {})[t] = res
            if not a.json:
                show(res, t, played.get(t))
            return
        if not a.all:
            return
        quiet = []
        for t in sorted(set(teams)):
            res = fn(gs, t, inj)
            out.setdefault(show.__name__, {})[t] = res
            if res:
                if not a.json:
                    show(res, t, played.get(t))
            else:
                quiet.append(t)
        if quiet and not a.json:
            label = ('defences' if show is show_defence
                     else 'rosters')
            print(f'\n  {len(quiet)} {label} with nothing measurable: '
                  f"{', '.join(quiet)}")
            few = [t for t in quiet if played.get(t, 0) < 4]
            if few:
                print(f'  {len(few)} of those have played under 4 games, so '
                      f'a with/without\n  split is not yet arithmetically '
                      f'possible for anyone on them.')

    sweep(a.defence, defence_improving, show_defence)
    sweep(a.redist, redistribution, show_redist)

    if a.json:
        print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
