#!/usr/bin/env python3
"""GAME -- graded board for team totals and spreads.

WHY THESE TWO ARE DIFFERENT FROM PLAYER PROPS, IN BOTH DIRECTIONS
  Better: a game total is the sum of ten men's output, so it is
  proportionally far steadier than any one of them. A 230-point total
  carries a standard deviation near 18 -- under 8% -- against roughly
  32% for a 25-point scorer. The same edge in percentage terms is
  worth several times more here, and the projection is built from
  team aggregates rather than from four games of one player's minutes.

  Worse: these are the sharpest markets on the board. A side or total
  takes far more money than a player prop and gets corrected faster.
  The defensive matchup signal the player board ranks on was measured
  at 546 player-games and did not predict production; a team rating
  built on four to eight games has no stronger claim. So this is
  written to MEASURE first.

WHAT IT DOES
  Projects each team's points from its own offence and its opponent's
  defence, both expressed per 100 possessions and both shrunk toward
  the league mean by games played, then scales by the pace the pair
  should produce. Adds a home-court edge measured from the cache
  rather than assumed. Compares the result to the posted number and
  grades it on the same rubric as the player card.

WHAT MAKES IT HONEST
  --calibrate regresses what actually happened on what was projected,
  walk-forward, with the projection for a game built only from games
  before it. Three numbers come out, and each answers a question:

    slope  1.0 means a point of projection is worth a point of
           outcome. Below 1 the projection is too confident and
           should be shrunk toward the mean; above 1, too timid.
    bias   actual minus projected on average. A systematic miss,
           correctable at once -- the player card's points
           projection was running 7% hot and nobody could see it.
    sigma  the residual spread, which is what turns a disagreement
           into a probability. Assumed numbers here would make every
           grade fiction.

  If the slope is not distinguishable from zero, the model has no
  information and the board says so instead of grading anything.

usage
  python3 game.py --calibrate            # measure, before trusting it
  python3 game.py --day 20261028         # the board
  python3 game.py --day 20261028 --publish ~/sharpz-site
"""
import os, re, sys, json, math, argparse, statistics, collections, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from defense import DATA, code, slate
    from minutes import games, game_lines, ApiError
    from card import grade_row, BREAKEVEN, MAX_GAP, PICK_GRADES
except Exception as e:                                    # pragma: no cover
    print(f'game.py needs defense.py, minutes.py and card.py beside it: {e}',
          file=sys.stderr)
    raise SystemExit(2)

BUILD = '2026-10-28e'
GAMECAL = os.path.join(DATA, 'game_calibration.json')

# Games before a team's own rating outweighs the league mean. The same
# shrinkage constant the rest of the project uses; the calibration's
# slope is what says whether it is right, since a slope under 1 means
# the ratings are still too confident and want more shrinking.
PRIOR_GAMES = 8.0

# Games the calibration needs before this board will grade anything.
#
# Forty was not enough and the output was not merely weak, it was
# inverted: the fit came back at slope -1.12, z -2.00, which says team
# strength ANTI-predicts margin. Applied as a correction that flipped
# every projection, so a 13-point road favourite became a home pick
# graded B at 86%, and all twelve spreads graded B -- a model finding a
# large edge in every game on a slate has found nothing.
#
# A negative slope is a sign error or noise, never a signal to bet
# against your own model: the prior that team strength predicts margin
# is among the most robust in sports, and |z| = 2.00 on 40 games is
# a one-in-twenty coin landing on its edge. So the gate now requires a
# POSITIVE slope, two standard errors, and a sample that could plausibly
# show one.
MIN_CAL_GAMES = 120


def _stamp():
    print(f'[game.py build {BUILD}]', file=sys.stderr)


def poss(side):
    """Possessions, estimated the standard way from the box score."""
    return side['fga'] + 0.44 * max(
        0, side['pts'] - 2 * side['fgm'] - side['tpm'])


def ratings(gs, before=None):
    """{team: off/100, def/100, pace} shrunk toward the league mean.

    Shrinkage rather than raw season-to-date, because four games of a
    hot offence is mostly four games of hot shooting. Each team's own
    figure gets weight g/(g+8); the rest is the league. A team with
    one game is almost entirely league average, which is the honest
    description of what is known about it.
    """
    agg = collections.defaultdict(
        lambda: {'pf': 0.0, 'pa': 0.0, 'poss': 0.0, 'g': 0})
    for g in gs:
        if before and g['day'] >= before:
            continue
        for t in g['teams']:
            opp = g['home'] if g['away'] == t else g['away']
            me, ot = g['teams'][t], g['teams'][opp]
            p = (poss(me) + poss(ot)) / 2.0
            if p <= 0:
                continue
            a = agg[t]
            a['pf'] += me['pts']
            a['pa'] += ot['pts']
            a['poss'] += p
            a['g'] += 1
    if not agg:
        return {}, None
    lg_off = (sum(a['pf'] for a in agg.values())
              / sum(a['poss'] for a in agg.values()) * 100)
    lg_pace = statistics.fmean([a['poss'] / a['g'] for a in agg.values()])
    out = {}
    for t, a in agg.items():
        w = a['g'] / (a['g'] + PRIOR_GAMES)
        off = a['pf'] / a['poss'] * 100
        dfn = a['pa'] / a['poss'] * 100
        pace = a['poss'] / a['g']
        out[t] = {
            'g': a['g'],
            'off': w * off + (1 - w) * lg_off,
            'def': w * dfn + (1 - w) * lg_off,
            'pace': w * pace + (1 - w) * lg_pace,
            'w': round(w, 2), 'off_raw': round(off, 1), 'def_raw': round(dfn, 1),
        }
    return out, {'off': lg_off, 'pace': lg_pace}


def home_edge(gs, before=None):
    """Home margin, measured. Not the 3.0 everyone quotes from memory."""
    v = [g['margin'][g['home']] for g in gs
         if not (before and g['day'] >= before)]
    if len(v) < 10:
        return None, None, len(v)
    m = statistics.fmean(v)
    se = statistics.stdev(v) / math.sqrt(len(v))
    return m, se, len(v)


def project(home, away, rt, lg, hedge):
    """(total, margin from the home side) for one matchup."""
    if not (rt.get(home) and rt.get(away) and lg):
        return None, None
    h, a = rt[home], rt[away]
    pace = (h['pace'] + a['pace']) / 2.0
    # each side's points: its own offence against the other's defence,
    # both already shrunk, averaged so neither dominates
    h100 = (h['off'] + a['def']) / 2.0
    a100 = (a['off'] + h['def']) / 2.0
    hp = h100 * pace / 100.0
    ap = a100 * pace / 100.0
    edge = (hedge or 0.0) / 2.0
    return hp + ap, (hp - ap) + 2 * edge


# --- calibration ------------------------------------------------------

def calibrate(gs, min_prior=3):
    """Walk-forward: regress outcome on projection for totals and margins."""
    tot, mar = [], []
    days = sorted({g['day'] for g in gs})
    for g in gs:
        if days.index(g['day']) < min_prior:
            continue
        rt, lg = ratings(gs, before=g['day'])
        he, _se, _n = home_edge(gs, before=g['day'])
        pt, pm = project(g['home'], g['away'], rt, lg, he)
        if pt is None:
            continue
        tot.append((pt, g['total']))
        mar.append((pm, g['margin'][g['home']]))

    def fit(pairs, label):
        if len(pairs) < 20:
            return {'label': label, 'n': len(pairs), 'ok': False}
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        slope = sxy / sxx if sxx else 0.0
        inter = my - slope * mx
        resid = [y - (inter + slope * x) for x, y in zip(xs, ys)]
        sigma = statistics.stdev(resid) if len(resid) > 1 else None
        s2 = (sum(r * r for r in resid) / (len(pairs) - 2))
        se = math.sqrt(s2 / sxx) if sxx > 0 else None
        ss_tot = sum((y - my) ** 2 for y in ys)
        r2 = 1 - sum(r * r for r in resid) / ss_tot if ss_tot else 0.0
        return {'label': label, 'ok': True, 'n': len(pairs),
                'slope': round(slope, 3), 'intercept': round(inter, 2),
                'se': None if se is None else round(se, 3),
                'z': None if not se else round(slope / se, 2),
                'bias': round(my - mx, 2),
                'sigma': None if sigma is None else round(sigma, 2),
                'r2': round(r2, 3)}

    return {'total': fit(tot, 'total'), 'margin': fit(mar, 'margin'),
            'generated': datetime.datetime.now().astimezone().isoformat()}


def show_calibration(c):
    for k in ('total', 'margin'):
        f = c[k]
        print(f'\n{k.upper()}')
        if not f.get('ok'):
            print(f'  only {f["n"]} games with a walk-forward projection. '
                  f'Not measurable yet.')
            continue
        print(f"  {f['n']} games, walk-forward")
        print(f"  slope {f['slope']:+.3f}"
              + (f"  SE {f['se']:.3f}  z {f['z']:+.2f}" if f.get('se') else '')
              + f"   R2 {f['r2']:.3f}")
        print(f"  bias {f['bias']:+.2f} points   residual sigma "
              f"{f['sigma']}")
        if usable(f):
            if f['slope'] < 0.6:
                print(f"  ** PREDICTS, but the projection is over-confident: "
                      f"a point of it\n     is worth {f['slope']:.2f} of "
                      f"outcome, so it is shrunk before grading.")
            else:
                print('  ** PREDICTS. The fitted line is applied as written.')
        elif f.get('ok') and f.get('slope', 0) < 0:
            print(f"  ** ANTI-PREDICTIVE at this sample ({f['slope']:+.2f}, "
                  f"z {f['z']:+.2f}).\n     Taken at face value this says "
                  f"bet against the model. It almost\n     certainly says "
                  f"the sample is too small: team strength predicting\n"
                  f"     margin is about as settled as sports modelling "
                  f"gets. REFUSED.")
        else:
            print(f'  ** NOT USABLE: {why_unusable(f)}.\n'
                  f'     Nothing in this market is gradeable and the board\n'
                  f'     will publish no picks for it.')


def load_calibration():
    if not os.path.exists(GAMECAL):
        return None
    try:
        return json.load(open(GAMECAL))
    except Exception:
        return None


# --- the board --------------------------------------------------------

def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def usable(f):
    """Is this fit safe to put a probability on?

    Three conditions, and the middle one is the one that matters:
      - enough games that a slope could be measured at all
      - a POSITIVE slope. A negative one means betting against your
        own model, which no sample this size justifies.
      - two standard errors away from zero.
    """
    if not f or not f.get('ok') or f.get('z') is None:
        return False
    if (f.get('n') or 0) < MIN_CAL_GAMES:
        return False
    if f['slope'] <= 0:
        return False
    return f['z'] >= 2.0


def why_unusable(f):
    if not f or not f.get('ok'):
        return 'not enough games to fit'
    if (f.get('n') or 0) < MIN_CAL_GAMES:
        return (f"{f['n']} games fitted, {MIN_CAL_GAMES} needed before a "
                f"slope means anything")
    if f['slope'] <= 0:
        return (f"slope {f['slope']:+.2f} is NEGATIVE -- the model "
                f"anti-predicts, which is noise or a sign error, never a "
                f"reason to bet against it")
    if f['z'] < 2.0:
        return f"slope {f['slope']:+.2f} is only {f['z']:+.2f} SE from zero"
    return 'unusable'


def board(day, gs, lines, cal, breakeven=BREAKEVEN, max_gap=MAX_GAP):
    """One row per side of each market, graded."""
    rt, lg = ratings(gs, before=day)
    he, he_se, he_n = home_edge(gs, before=day)
    rows = []
    seen = set()
    for t, ln in (lines or {}).items():
        opp = ln.get('opp')
        if not opp or not ln.get('home'):
            continue                      # one row per game, from the home side
        home, away = t, opp
        if (home, away) in seen:
            continue
        seen.add((home, away))
        pt, pm = project(home, away, rt, lg, he)
        if pt is None:
            continue

        for market, proj, posted in (
                ('total', pt, ln.get('total')),
                ('spread', pm, None if ln.get('spread') is None
                 else -float(ln['spread']))):
            if posted is None:
                continue
            # the calibration measures 'margin'; this market is posted
            # as a 'spread'. Two names for one quantity, and the lookup
            # silently returned nothing, so every spread row came back
            # ungradeable while the calibration said it predicted.
            f = (cal or {}).get(
                {'spread': 'margin'}.get(market, market)) or {}
            if not usable(f):
                sigma, adj = None, proj
            else:
                sigma = f.get('sigma')
                # THE FITTED LINE, BOTH WAYS.
                #
                # E[actual | projected] is intercept + slope x
                # projected, and that is the best estimate whatever the
                # slope happens to be. The first version applied it
                # only when the slope fell below 0.6 -- catching a
                # projection that was too confident and silently
                # ignoring one that was too timid. A test fixture came
                # back at 2.05, meaning the real margin swung twice as
                # far as the model said, and the code left it
                # compressed because 2.05 is not less than 0.6.
                adj = f.get('intercept', 0.0) + f['slope'] * proj
            if not sigma:
                rows.append({'home': home, 'away': away, 'market': market,
                             'proj': round(adj, 1), 'line': round(posted, 1),
                             'grade': 'F', 'p_mine': None, 'gap': None,
                             'why': [f'not gradeable: {why_unusable(f)}'],
                             'context': f'projects {adj:.1f} against '
                                        f'{posted:.1f}',
                             'shown': True, 'pick': False, 'flag': '-'})
                continue
            # Normal rather than the gamma the player card uses: a game
            # total is a sum over twenty players and is near symmetric,
            # where one man's points are a bounded count with a long
            # right tail.
            p_over = 1.0 - norm_cdf((posted - adj) / sigma)
            for side, p in (('over', p_over), ('under', 1 - p_over)):
                if market == 'spread':
                    side = 'home' if side == 'over' else 'away'
                rows.append({
                    'home': home, 'away': away, 'market': market,
                    'side': side, 'line': round(posted, 1),
                    'proj': round(adj, 1), 'sigma': sigma,
                    'p_mine': round(p, 4), 'p_mkt': None,
                    'gap': round(p - 0.5, 4),
                    'g': min(rt[home]['g'], rt[away]['g']),
                    'books': None, 'keep': None, 'flag': '-',
                    'short': 0.0, 'opp_short': 0.0,
                    'context': (f'projects {adj:.1f} against {posted:.1f}'
                                f'; residual sigma {sigma}'),
                })
    for r in rows:
        if r.get('p_mine') is None:
            continue
        r['grade'], r['why'] = grade_row(r, breakeven)
        r['shown'] = abs(r['gap']) <= max_gap
        r['pick'] = r['grade'] in PICK_GRADES and r['shown']
    rows.sort(key=lambda r: -(r.get('p_mine') or 0))
    return rows, {'home_edge': None if he is None else round(he, 2),
                  'home_edge_se': None if he_se is None else round(he_se, 2),
                  'home_games': he_n}


def show(rows, day, meta, cal):
    print(f'\nGAME BOARD -- {day}')
    he = meta.get('home_edge')
    if he is not None:
        print(f"  home edge measured at {he:+.2f} points "
              f"(SE {meta['home_edge_se']}, {meta['home_games']} games)")
    if not rows:
        print('  nothing priced, or no team has enough games to rate.')
        return
    ungraded = [r for r in rows if r.get('p_mine') is None]
    graded = [r for r in rows if r.get('p_mine') is not None]
    if ungraded:
        # printed, then carry on. The first version returned here, so a
        # single ungradeable market hid every graded row behind it.
        mk = sorted({r['market'] for r in ungraded})
        print(f"\n  not gradeable ({', '.join(mk)}) -- the model has not "
              f"been shown\n  to predict {'them' if len(mk) > 1 else 'it'} "
              f"at this sample:")
        for r in ungraded[:6]:
            print(f"    {r['away']}@{r['home']:4} {r['market']:7}"
                  f" projects {r['proj']:>6.1f} against {r['line']:>6.1f}")
    if not graded:
        return
    print(f"\n  {'GAME':12}{'MARKET':8}{'SIDE':7}{'LINE':>7}{'PROJ':>7}"
          f"{'EDGE':>7}{'MINE':>7}  GRADE")
    for r in graded:
        print(f"  {r['away']+'@'+r['home']:12}{r['market']:8}{r['side']:7}"
              f"{r['line']:>7.1f}{r['proj']:>7.1f}"
              f"{r['proj']-r['line']:>+7.1f}{r['p_mine']*100:>6.1f}%"
              f"  {r['grade']}")
    print('\n  PROJ is each side\'s offence against the other\'s defence, per\n'
          '  100, shrunk toward the league by games played, scaled by the\n'
          '  pace the pair should produce, plus a measured home edge.\n'
          '  MINE comes from the residual spread the calibration measured,\n'
          '  not an assumed one.')


def main():
    ap = argparse.ArgumentParser(description='team totals and spreads')
    ap.add_argument('--day')
    ap.add_argument('--calibrate', action='store_true')
    ap.add_argument('--breakeven', type=float, default=BREAKEVEN)
    ap.add_argument('--max-gap', type=float, default=MAX_GAP, dest='max_gap')
    ap.add_argument('--publish', metavar='DIR')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    _stamp()
    if not a.day and not a.calibrate:
        ap.error('--day is required unless you are calibrating')

    gs = games(before=a.day)
    if not gs:
        print('no games paired. Run: python3 minutes.py --schedule',
              file=sys.stderr)
        return 1

    if a.calibrate:
        c = calibrate(gs)
        json.dump(c, open(GAMECAL, 'w'), indent=1)
        if a.json:
            print(json.dumps(c, indent=1))
        else:
            show_calibration(c)
            print(f'\nsaved -> {GAMECAL}')
        return 0

    cal = load_calibration()
    if cal is None:
        print('  ! no calibration yet -- run: python3 game.py --calibrate',
              file=sys.stderr)
    try:
        lines = game_lines(a.day)
    except ApiError as e:
        print(f'  ! lines unavailable: {e}', file=sys.stderr)
        return 1
    rows, meta = board(a.day, gs, lines, cal, a.breakeven, a.max_gap)
    out = {'day': a.day, 'rows': rows, 'meta': meta,
           'calibration': cal, 'breakeven': a.breakeven,
           'max_gap': a.max_gap}
    if not a.json:
        show(rows, a.day, meta, cal)
    if a.publish:
        d = os.path.join(os.path.expanduser(a.publish), 'data')
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, 'game_card.json')
        json.dump(out, open(fn, 'w'), indent=1)
        npick = sum(1 for r in rows if r.get('pick'))
        print(f'\nwrote {fn}: {len(rows)} rows, {npick} picks')
    if a.json:
        print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
