#!/usr/bin/env python3
"""CARD -- one ranked table: best matchups, best side of the distribution.

WHAT THIS REPLACES, AND WHY
  Until now the morning ran three screens and a price pull, and I
  combined them by hand in a chat window. That hand-combination is
  where the errors lived. It compared a model total to a league
  baseline instead of the posted line and called an over an under. It
  read a ranking built on a three-game average as though it were a
  projection. Worst of all it kept comparing a MEAN to a LINE, then
  comparing that to a PROBABILITY, and calling the result an edge --
  three different objects, one conclusion, no arithmetic connecting
  them.

  The frame is also wrong when it hunts mispricings. Every player prop
  de-vigs to somewhere near 46-48% on the over, because points and
  rebounds are right-skewed: the line sits near the mean, the mean
  sits above the median, so the under wins slightly more than half the
  time. That is the BASELINE, not a verdict. Reading it as a verdict
  fades every bet on every slate forever, which is both wrong and
  useless.

  What this does instead:
    - rank by MATCHUP, which is the thing the screen is actually good
      at, and let FIT choose the side
    - project a mean honestly: last season regressed against this one,
      times the minutes the spread implies
    - put a REAL distribution around it, right-skewed like the stat,
      with a variance measured league-wide rather than a player's own
      three-game spread
    - report P(side) and the market's, side by side, in the same units

  The market number is CONTEXT, not a veto. A spot where the matchup
  is excellent and the price is ordinary is exactly the spot worth
  having, because a market that prices every prop at 47% is barely
  distinguishing a great matchup from a neutral one.

usage
  python3 card.py --day 20261028            # the card
  python3 card.py --day 20261028 --var      # show the variance fit
  python3 card.py --day 20261028 --market reb
"""
import os, re, sys, json, math, argparse, subprocess, statistics
import collections, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from defense import (DATA, BASE_SEASON, code, norm_player,
                     injury_map, inj_tag)
    from minutes import games, margin_curve, minutes_risk, game_lines, ApiError
    from boards import (bref, prior_rates, blend_rate, PRIOR_GAMES,
                        positions, pos_group)
except Exception as e:                                    # pragma: no cover
    print(f'card.py needs defense.py, minutes.py and boards.py beside it: {e}',
          file=sys.stderr)
    raise SystemExit(2)

BUILD = '2026-10-30g'
LOGS = os.path.join(DATA, 'season_logs.json')
PRICES = os.path.join(DATA, 'external_picks.jsonl')

# our short key -> (box-score field, the label props_lines.py logs under)
MARKETS = {
    'pts': ('pts', 'Points Scored'),
    'reb': ('reb', 'Total Rebounds'),
    'ast': ('ast', 'Total Assists'),
    'threes': ('tpm', '3-Pointers Made'),
    # pra is DERIVED. derive_pra() writes the column into every log row
    # at load, so variance_fit, player_means, blend_rate, p_over,
    # calibrate and bias_by_population all work on it unchanged -- and
    # the variance they measure is the variance of the sum, which
    # already contains every covariance between the three parts.
    # 'PRA' is not a label of my choosing: it is the exact string
    # props_lines.py already writes into external_picks.jsonl, and
    # load_prices() joins on it. A prettier label here would simply
    # never match a stored price, and the card would report no PRA
    # candidates on a night when the lines were sitting in the file.
    'pra': ('pra', 'PRA'),
}
# per-36 field in the Basketball-Reference cache, for the prior
PRIOR36 = {'pts': 'pts_per_minute_36', 'reb': 'trb_per_minute_36',
           'ast': 'ast_per_minute_36', 'threes': 'fg3_per_minute_36',
           'pra': None}            # built from the other three
MIN_MIN = 8.0


def _stamp():
    print(f'[card.py build {BUILD}]', file=sys.stderr)


# --- the distribution -------------------------------------------------
#
# A symmetric normal is the wrong shape and it is wrong in a direction
# that matters. Points and rebounds are counts: bounded below at zero,
# unbounded above, mean above median. P(over) computed on a normal is
# therefore too high near the line, which is precisely where every bet
# lives. A gamma has the right shape, takes a mean and a variance, and
# needs nothing invented.

def _gammap(a, x):
    """Regularised lower incomplete gamma P(a, x). Series + continued
    fraction, the standard split at x < a+1. Written out because scipy
    is not reliably present on the machine this has to run on, and a
    silent import failure at 9am is worse than thirty lines."""
    if x <= 0:
        return 0.0
    if a <= 0:
        return 1.0
    gln = math.lgamma(a)
    if x < a + 1.0:
        ap, s, d = a, 1.0 / a, 1.0 / a
        for _ in range(500):
            ap += 1.0
            d *= x / ap
            s += d
            if abs(d) < abs(s) * 1e-12:
                break
        return s * math.exp(-x + a * math.log(x) - gln)
    # continued fraction for Q(a,x), then P = 1 - Q
    tiny = 1e-300
    b, c, d = x + 1.0 - a, 1.0 / tiny, 1.0 / (x + 1.0 - a)
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        dl = d * c
        h *= dl
        if abs(dl - 1.0) < 1e-12:
            break
    q = math.exp(-x + a * math.log(x) - gln) * h
    return 1.0 - q


def p_over(mean, var, line):
    """P(X > line) for a gamma with this mean and variance.

    The line is a half-number (13.5), so there is no tie to handle and
    no continuity correction to argue about.
    """
    if mean <= 0 or var <= 0:
        return None
    k = mean * mean / var
    theta = var / mean
    return max(0.0, min(1.0, 1.0 - _gammap(k, line / theta)))


# --- the variance model -----------------------------------------------

def variance_fit(logs_days, field, min_games=4):
    """var = a * mean^b, fitted league-wide in logs.

    A player's own three-game variance is worthless -- it is an
    estimate from two degrees of freedom, and it was about to be used
    to size bets. But the RELATIONSHIP between a man's average and his
    spread is stable across the league and measurable from the same
    cache: bigger scorers vary more, in a regular way. Fitting that
    once and reading each player's variance off his own mean is far
    steadier than trusting his four games, and it costs nothing.

    Returns (a, b, r2, n). b near 1 would be Poisson; counting stats in
    basketball are overdispersed, so b above 1 is expected.
    """
    by = collections.defaultdict(list)
    for day in sorted(logs_days):
        for r in logs_days[day]:
            if (r.get('min') or 0) >= MIN_MIN:
                by[(norm_player(r.get('player')), code(r.get('team')))].append(
                    r.get(field) or 0)
    pts = []
    for k, v in by.items():
        if len(v) < min_games:
            continue
        m = statistics.fmean(v)
        var = statistics.variance(v)
        if m > 0.5 and var > 0:
            pts.append((math.log(m), math.log(var)))
    if len(pts) < 12:
        return None
    n = len(pts)
    mx = statistics.fmean([p[0] for p in pts])
    my = statistics.fmean([p[1] for p in pts])
    sxx = sum((p[0] - mx) ** 2 for p in pts)
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts)
    if sxx <= 0:
        return None
    b = sxy / sxx
    loga = my - b * mx
    ss_res = sum((p[1] - (loga + b * p[0])) ** 2 for p in pts)
    ss_tot = sum((p[1] - my) ** 2 for p in pts)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return math.exp(loga), b, r2, n


def modelled_var(fit, mean):
    if not fit or mean <= 0:
        return None
    a, b, _r2, _n = fit
    return a * (mean ** b)


# --- inputs -----------------------------------------------------------

def _json_from(text):
    """Parse the JSON document out of mixed output.

    defense.py --json prints its held-out notices -- the KEEP cut and
    the ruled-OUT names -- to STDOUT before the JSON, so the stream is
    two things at once and json.loads sees a stray 'held out on KEEP'
    and gives up. Those notices are worth keeping, so rather than
    silencing them this finds where the document actually starts.

    Only line-initial braces are tried: json.dumps(indent=1) puts its
    opening brace in column zero, and scanning every '{' in the file
    would try to parse from the middle of a nested object dozens of
    times over.
    """
    for m in re.finditer(r'(?m)^[{\[]', text):
        try:
            return json.loads(text[m.start():])
        except Exception:
            continue
    return None


def load_board(day, extra=()):
    """defense.py --board DAY --json, run here so the card is one command.

    THE DAY HAS TO BE PASSED, and for a while it was not.
    `--board` with no value defaults to today, so every card boarded
    tonight's slate whatever --day said, then joined it against the
    requested day's prices. Today that is accidentally correct and
    invisible. Any backfill silently produced an empty card: asked for
    the 28th it boarded the 29th's three games, found no overlap with
    the 28th's twelve games of prices, and reported "no board
    candidate has a posted line in this market" -- which reads like
    missing prices rather than a wrong slate.
    """
    cmd = [sys.executable, os.path.join(HERE, 'defense.py'),
           '--board', day, '--json', *extra]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        print(p.stderr[-800:], file=sys.stderr)
        raise SystemExit('defense.py --board failed')
    # the notices belong on screen; pass them through where they belong
    head = p.stdout[:p.stdout.find('{')] if '{' in p.stdout else ''
    for ln in head.splitlines():
        if ln.strip():
            print(ln, file=sys.stderr)
    got = _json_from(p.stdout)
    if got is None:
        print(p.stdout[:400], file=sys.stderr)
        raise SystemExit('defense.py --board did not return JSON')
    return got


def load_prices(day):
    """{(player_key, market_key): row} for one day, latest wins."""
    if not os.path.exists(PRICES):
        return {}
    label_to_key = {lbl: k for k, (_f, lbl) in MARKETS.items()}
    out = {}
    for ln in open(PRICES):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') != day:
            continue
        mk = label_to_key.get(r.get('market'))
        if not mk or r.get('line') is None:
            continue
        out[(norm_player(r.get('player')), mk)] = r
    return out


def derive_pra(logs_days):
    """Write a pra column into every log row that has all three parts.

    A row missing any one of them is left WITHOUT the column rather
    than summed from what is there: a player with points and rebounds
    recorded but assists absent would otherwise get a PRA that is
    quietly too low, and nothing downstream could tell that apart from
    a quiet night. Returns how many rows got the column, so a caller
    can notice if the answer is nearly none.
    """
    n = 0
    for day in logs_days:
        for r in logs_days[day]:
            p, rb, a = r.get('pts'), r.get('reb'), r.get('ast')
            if p is None or rb is None or a is None:
                r.pop('pra', None)
                continue
            r['pra'] = p + rb + a
            n += 1
    return n


def player_means(logs_days, field, before=None):
    """{(key, team): (mean per game, mpg, games, per-36 rate)}"""
    by = collections.defaultdict(lambda: {'v': [], 'm': []})
    for day in sorted(logs_days):
        if before and day >= before:
            continue
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            d = by[(norm_player(r.get('player')), code(r.get('team')))]
            d['v'].append(r.get(field) or 0)
            d['m'].append(m)
    out = {}
    for k, d in by.items():
        if not d['v']:
            continue
        tot_m = sum(d['m'])
        out[k] = (statistics.fmean(d['v']), statistics.fmean(d['m']),
                  len(d['v']), (sum(d['v']) / tot_m * 36.0) if tot_m else 0.0)
    return out


def priors_for(market):
    """{key: per-36 rate} from last season, for this market."""
    if market == 'pra':
        # Summed from the three component priors, and only for players
        # who have all three. Rebounds comes from prior_rates() rather
        # than the bref column for the same reason it does on its own:
        # that is the measured prior, and mixing the two sources would
        # make the PRA prior disagree with the reb prior for the same
        # player.
        pts = priors_for('pts')
        ast = priors_for('ast')
        reb = prior_rates()
        out = {}
        for k in pts.keys() & ast.keys() & reb.keys():
            out[k] = pts[k] + ast[k] + reb[k]
        return out
    fld = PRIOR36.get(market)
    if not fld:
        return {}
    if market == 'reb':
        return prior_rates()
    out = {}
    for name, entry in (bref() or {}).items():
        k = norm_player(name)
        best = None
        for t, row in (entry.get('teams') or {}).items():
            if t.endswith('TM'):
                continue
            v, mp = row.get(fld), row.get('mp') or 0
            if v and (best is None or mp > best[1]):
                best = (float(v), mp)
        if best:
            out[k] = best[0]
    return out


# --- depletion: the bias, made mechanical ----------------------------
#
# WHY THIS IS A COLUMN AND NOT A PARAGRAPH
#   The measured 7% projection bias is not the only signed error in
#   this card. The bigger one is that a player's rate is blended from
#   games he played in a DIFFERENT lineup. When two of his team-mates
#   are out, his role grows and the projection does not, so the number
#   runs low -- which quietly turns every under on a short-handed team
#   into a trap and every over into an understated edge.
#
#   I worked that rule out from the top of this very card, wrote it
#   down, and then broke it two paragraphs later by dropping a player
#   whose team was short-handed. A rule I have to remember is a rule
#   that fails. So it is computed here, printed on every row, and
#   allowed to move the ranking.
#
#   Note the asymmetry is in the ERROR, not the bet: a short-handed
#   team does not make an over good. It makes my over number too low
#   and my under number too low, so the under is wrong in the
#   direction that loses money and the over in the direction that
#   costs me a bet I should have taken.

SHORT_THRESHOLD = 0.08          # share of a team's minutes ruled out


def depletion(logs_days, inj, pos=None, before=None, lookback=6):
    """{team: {overall share out, and the share out BY POSITION GROUP}}.

    Minutes-weighted rather than a headcount, because losing a
    33-minute starter and losing a third-string centre are not the
    same fact and a count cannot tell them apart.

    AND SLICED BY POSITION, because the team-wide number answers the
    wrong question. Chicago being short Patrick Williams and Nic
    Claxton -- a forward and a centre -- says little about the backup
    point guard, whose minutes go to other guards. I flagged Tre Jones
    off the team figure and described it as his role expanding, which
    did not follow. A centre's minutes go to bigs.

    No cross-position weights are invented here: each group is simply
    counted on its own, and the row is flagged on the player's own
    group. Usage does bleed across positions in a way minutes do not,
    but by how much is unmeasured, so it is not modelled.
    """
    mins = collections.defaultdict(lambda: collections.defaultdict(list))
    days = sorted(logs_days)
    if before:
        days = [d for d in days if d < before]
    for day in days[-lookback:]:
        for r in logs_days[day]:
            t = code(r.get('team'))
            mins[t][norm_player(r.get('player'))].append(r.get('min') or 0)
    pos = pos or {}
    out = {}
    for t, players in mins.items():
        avg = {k: statistics.fmean(v) for k, v in players.items() if v}
        total = sum(avg.values())
        if total <= 0:
            continue
        gone, who = 0.0, []
        by_pos = collections.defaultdict(lambda: [0.0, 0.0])   # [out, all]
        for k, m in avg.items():
            g = pos_group(pos.get(k))
            by_pos[g][1] += m
            info = (inj or {}).get(k)
            if info and inj_tag(info) == 'OUT' and code(info.get('team')) == t:
                gone += m
                by_pos[g][0] += m
                who.append((m, f"{info.get('name') or k} ({g})"))
        who.sort(reverse=True)
        out[t] = {
            'share': gone / total,
            'by_pos': {g: (o / a if a > 0 else 0.0)
                       for g, (o, a) in by_pos.items()},
            'who': [n for _m, n in who[:4]],
            'mins_out': round(gone, 1),
        }
    return out



# --- the card ---------------------------------------------------------

def load_calibration(market):
    """(bias scale, FIT coefficient or None) from the saved measurement.

    TWO SEPARATE THINGS, and conflating them would waste the better of
    the two. The calibration measures both:

      mean_ratio -- actual over projected, averaged across every
        player-game. If the projection were unbiased this is 1.000.
        For points it came back at 0.93 across all five FIT buckets,
        about 3.5 SE below one: the projection runs 7% hot regardless
        of matchup. That is a bug in the projection and it is
        correctable today.

      beta -- how much FIT moves the residual. This is the matchup
        hypothesis, and it is applied ONLY when it clears 2 SE.
        Letting an insignificant coefficient become a multiplier is
        how a ranking turns into a projection without anyone deciding
        it should.

    The bias correction applies whether or not beta does, because a
    systematically high projection is wrong in every row, including
    the ones where the matchup tells you nothing.
    """
    if not os.path.exists(FITCAL):
        return None, None
    try:
        c = (json.load(open(FITCAL)) or {}).get(market)
    except Exception:
        return None, None
    if not c or not c.get('ok'):
        return None, None
    bias = c.get('mean_ratio')
    if bias is not None and not (0.5 <= bias <= 1.5):
        bias = None            # implausible: do not silently rescale
    beta = c if (c.get('z') is not None and abs(c['z']) >= 2.0) else None
    return bias, beta


def build(day, market, board, logs_days, lines, curve, vfit, before=None,
          cal=None, bias=None, dep=None, allpos=None):
    """One row per (board candidate x posted line), scored by P(side)."""
    field, _label = MARKETS[market]
    means = player_means(logs_days, field, before=before)
    priors = priors_for(market)
    prices = load_prices(day)

    rows = []
    for side, bucket in (('over', board.get('overs') or []),
                         ('under', board.get('unders') or [])):
        for b in bucket:
            k = norm_player(b.get('player'))
            t = code(b.get('team'))
            pr = prices.get((k, market))
            if not pr:
                continue
            got = means.get((k, t))
            if not got:
                continue
            cur_pg, mpg_log, g, cur36 = got
            rate36, w = blend_rate(cur36, priors.get(k), g)

            # minutes: this season's if he has played, else last
            # season's, then the spread's effect on top. now_mpg comes
            # from the board, which already shrinks it toward the
            # baseline -- use it rather than recomputing differently.
            mins = b.get('now_mpg') or b.get('mpg') or mpg_log
            ln = (lines or {}).get(t) or {}
            # Bound here because several things below need it. It used
            # to exist only as an inline expression inside the
            # rows.append() dict, so the depletion block referencing
            # `opp` raised NameError on the first real run -- three
            # builds after I introduced it, because every test I wrote
            # exercised the helpers and none of them called build().
            opp = b.get('opp') or ln.get('opp') or '-'
            adj = 0.0
            if ln.get('spread') is not None and curve:
                _p, _e, excess = minutes_risk(ln['spread'], curve)
                if excess is not None:
                    adj = excess
            mean = rate36 / 36.0 * max(0.0, mins + adj)
            # the matchup, in the same units as everything else -- but
            # only when it has been shown to move outcomes
            if bias:
                mean *= bias
            fit_mult = 1.0
            if cal and b.get('fit') is not None:
                fit_mult = max(0.5, min(1.5, 1.0 + cal['beta'] * b['fit']))
                mean *= fit_mult
            var = modelled_var(vfit, mean)
            po = p_over(mean, var, pr['line']) if var else None
            if po is None:
                continue
            mine = po if side == 'over' else 1.0 - po
            mkt = pr.get('prob_over')
            mkt_side = None if mkt is None else (
                mkt if side == 'over' else 1.0 - mkt)
            # the signed bias, attached to the row rather than
            # remembered: my rate is blended from a lineup that no
            # longer exists, so a short-handed team means this number
            # is LOW -- which argues for the over and against the
            # under, every time, without my having to notice.
            grp = pos_group((allpos or {}).get(k))
            dteam = (dep or {}).get(t, {})
            # his own position group first; the team figure is kept for
            # display but does not decide the flag
            mine_short = (dteam.get('by_pos') or {}).get(grp,
                                                         dteam.get('share', 0.0))
            team_short = dteam.get('share', 0.0)
            opp_short = (dep or {}).get(opp or '', {}).get('share', 0.0)
            if mine_short >= SHORT_THRESHOLD:
                flag = 'bias-FOR' if side == 'over' else 'bias-AGAINST'
            elif opp_short >= SHORT_THRESHOLD and side == 'over':
                flag = 'opp-short'
            else:
                flag = '-'
            rows.append({
                'pos': grp,
                'short': round(mine_short, 3),
                'team_short': round(team_short, 3),
                'opp_short': round(opp_short, 3),
                'short_who': (dep or {}).get(t, {}).get('who') or [],
                'flag': flag,
                'player': b.get('player'), 'team': t,
                'opp': opp,
                'market': market, 'side': side, 'line': pr['line'],
                'fit': b.get('fit'), 'via': b.get('via') or '-',
                'via_z': b.get('via_z'), 'keep': b.get('keep'),
                'poss': b.get('poss'), 'drift': b.get('drift'),
                'g': g, 'cur36': round(cur36, 2),
                'prior36': (None if priors.get(k) is None
                            else round(priors[k], 2)),
                'rate36': round(rate36, 2), 'w_new': round(w, 2),
                'mins': round(mins + adj, 1), 'min_adj': round(adj, 2),
                'mean': round(mean, 2), 'sd': round(math.sqrt(var), 2),
                'fit_mult': round(fit_mult, 3),
                'p_mine': round(mine, 4),
                'p_mkt': None if mkt_side is None else round(mkt_side, 4),
                'gap': (None if mkt_side is None
                        else round(mine - mkt_side, 4)),
                'books': pr.get('n_books'),
            })
    # RANKED BY P(SIDE), not by the gap to the market.
    #
    # Every bet here pays about the same price, so the chance it wins
    # IS the expected value, and "the side of the distribution with the
    # best chance" is the thing being asked for. Ranking by the gap
    # instead would quietly reinstate the market as the arbiter --
    # which is the frame that fades a whole slate every day.
    # RANKED BY P(SIDE), with one override: a row whose known bias
    # runs against it is sorted to the bottom whatever its probability
    # says. Those were the four best-looking bets on this card
    # yesterday and all four were wrong in a direction I could name
    # before tip. Leaving them on top and relying on prose to warn
    # about them is what failed.
    rows.sort(key=lambda r: (r['flag'] == 'bias-AGAINST', -r['p_mine']))
    return rows


def show(rows, day, market, vfit, top):
    print(f'\nCARD -- {day}, {market}, ranked by P(side)  '
          f'[card.py {BUILD}]')
    if not rows:
        print('  no board candidate has a posted line in this market.')
        return
    if vfit:
        a, b, r2, n = vfit
        print(f'  variance model: var = {a:.3f} * mean^{b:.2f}  '
              f'(R2 {r2:.2f}, {n} players)')
    print(f"\n  {'PLAYER':20}{'TM':>4}{'OPP':>4}{'SIDE':>6}{'LINE':>6}"
          f"{'FIT':>6}{'MEAN':>6}{'SD':>5}"
          f"{'MINE':>7}{'MKT':>7}{'GAP':>7}{'OUT%':>6}  {'BIAS':<13}")
    def num(v, w, d=2):
        return (' ' * (w - 1) + '-') if v is None else f'{v:>{w}.{d}f}'

    picks = [x for x in rows if x.get('pick')]
    if GRADES_PUBLISHED and picks:
        print(f'  PICKS ({len(picks)}): '
              + ', '.join(f"{x['player']} {x['side']} {x['line']} "
                          f"[{x['grade']}]" for x in picks))
    elif picks:
        print(f'  ({len(picks)} row(s) would have graded A/B. '
              f'Logged, not published -- see GRADES_PUBLISHED.)')
    for x in rows[:top]:
        # built as plain strings first. A previous version inlined these
        # as nested f-strings with escaped quotes and a chr(39) dodge,
        # which did not parse and would have been unreadable if it had.
        mkt = '      -' if x['p_mkt'] is None else f"{x['p_mkt'] * 100:>6.1f}%"
        gap = '      -' if x['gap'] is None else f"{x['gap'] * 100:>+7.1f}"
        mine = f"{x['p_mine'] * 100:>6.1f}%"
        bias_s = '' if x['flag'] == '-' else x['flag']
        print(f"  {x['player'][:19]:20}{x['team']:>4}{x['opp']:>4}"
              f"{x['side']:>6}{x['line']:>6.1f}{num(x['fit'], 6)}"
              f"{x['mean']:>6.1f}{x['sd']:>5.1f}{mine}{mkt}{gap}"
              f"{x['short']*100:>5.0f}%  {bias_s:<13}")
    print('\n  MINE is P(this side wins) from a gamma with the projected\n'
          '  mean and a variance read off the league fit above -- not the\n'
          "  player's own three-game spread, which is two degrees of\n"
          '  freedom and was about to size bets.')
    print('  MKT is the same number de-vigged from the books. It is shown\n'
          '  for context and is NOT a filter: nearly every prop prices\n'
          '  near 47% on the over because the stat is right-skewed, so a\n'
          '  47% market price is the baseline, not a verdict.')
    print('  FIT is the matchup: how soft that defence is in the zones\n'
          '  this man shoots from. It picks the side and it sets this\n'
          '  ordering. It is NOT in the mean -- measured at 546\n'
          '  player-games walk-forward it did not predict production,\n'
          '  so it does not get a multiplier, and for the same reason\n'
          '  no row on this card carries a grade. The ordering is the\n'
          '  hypothesis under test, not a verdict to bet into.')
    print('  OUT% is the share of his team\'s recent minutes ruled out\n'
          '  tonight. bias-FOR means my rate comes from a lineup that no\n'
          '  longer exists, so this projection is LOW and the over is\n'
          '  better than it looks. bias-AGAINST is the same error\n'
          '  pointing at an under -- those rows are sorted to the bottom\n'
          '  regardless of probability, because they were the four best\n'
          '  looking bets on this card and all four were wrong.')
    ft = footing(market)
    if ft and ft.get('stale_days') is not None and ft['stale_days'] > 7:
        print(f"\n  footing: measured {ft['stale_days']} days ago "
              f"({ft['measured']}) and not refreshed.\n"
              f"  Run card.py --bias-pop --all-markets.")
    elif ft:
        word = 'THIN' if ft['thin'] else 'ok'
        print(f"\n  footing [{word}]: this market's correction is measured on "
              f"{ft['n']} player-games\n"
              f"  that actually had a posted line, giving +/-{ft['se']*100:.1f}%"
              f" on the ratio. An error\n"
              f"  up to {ft['mde']*100:.0f}% is still consistent with that"
              f" sample.")
        if ft['thin']:
            print('  That is wide enough to flip the side of a typical row, so\n'
                  '  treat this ordering as the weakest of the four until the\n'
                  '  sample grows. Re-run card.py --bias-pop weekly.')
    else:
        print('\n  footing: not measured. Run card.py --bias-pop --all-markets.')

    shorts = sorted({(x['team'], tuple(x['short_who']), x['short'])
                     for x in rows if x['short'] >= SHORT_THRESHOLD},
                    key=lambda z: -z[2])
    if shorts:
        print('\n  short-handed tonight:')
        for t, who, sh in shorts:
            print(f"    {t:4} {sh*100:>4.0f}% of minutes out"
                  + (f"  -- {', '.join(who)}" if who else ''))


# --- grading ----------------------------------------------------------
#
# A LETTER HAS TO COME FROM SOMETHING
#   The grade is a function of the row, not an opinion typed over it.
#   Every input below is either measured or counted, and every demotion
#   names itself in `why`, so a C can be argued with on its reasons
#   rather than on its letter.
#
#   The starting point is edge over break-even. At -110 a bet needs
#   52.4% to be worth making, so a 54% chance is 1.6 points of edge and
#   not much else. Everything after that is a reason to trust the
#   number less.

BREAKEVEN = 0.524          # -110. --breakeven to change it.
PICK_GRADES = ('A', 'B')   # what we actually bet

# WHY THE LETTERS ARE NO LONGER ON THE PAGE
#   A grade is a claim about confidence, and the confidence came
#   from p_mine, which comes from a projection whose matchup term
#   (FIT) was measured over 546 player-games walk-forward and did
#   not predict production: pts z +0.56, reb -0.51, ast -0.16,
#   threes +0.12, and no ordering across the buckets in any of the
#   four. Ranking on it may still be right. Publishing a letter
#   derived from it says we have established that it is, and we
#   have not.
#
#   The first night of evidence pointed the other way. On 28 Oct
#   the bands went A 0-1, B 1-5, C 2-2, D 1-1, F 9-5 -- a trend
#   running backwards at 2.20 SE. One night is not a finding
#   either; 27 rows is noise in both directions. But an untested
#   ordering and a night of contrary evidence is not something to
#   put a letter on.
#
#   So: still computed, still written to slate.jsonl, still graded
#   tomorrow by slate.py grades. Just not shown. When the bands
#   separate at two standard errors over a real sample, set this
#   back to True and the letters return with something behind
#   them. Until then the card publishes the ordering and says
#   plainly that the ordering is what is on trial.
GRADES_PUBLISHED = False

# Published alongside the card so the page explains the grade from the
# same numbers that produced it. A rubric written separately in HTML
# drifts from the code the first time a threshold moves, and then the
# site is explaining a rule nobody is applying.
RUBRIC = {
    'breakeven': BREAKEVEN,
    'bands': [
        ('A', 0.05, 'five or more points of edge'),
        ('B', 0.03, 'three or more'),
        ('C', 0.015, 'one and a half or more'),
        ('D', 0.0, 'anything above break-even'),
        ('F', None, 'at or below break-even, or a known bias against it'),
    ],
    'demotions': [
        'fewer than three games of this season to read him from',
        'more than a six-point disagreement with the books, a band where '
        'every gap we have examined turned out to be our own error',
        'a defence that has kept fewer than six of last season\'s top '
        'eight, so the matchup read describes a roster that has gone',
    ],
    'picks': ('A row graded A or B is a bet. Everything else is '
              'published, graded tomorrow against the box score, and '
              'kept out of the headline record.'),
}
MAX_GAP = 0.10             # beyond this the disagreement is not shown


def grade_row(x, breakeven=BREAKEVEN):
    """(letter, [reasons]). Demotions are additive and all are named."""
    why = []
    edge = x['p_mine'] - breakeven

    if x['flag'] == 'bias-AGAINST':
        return 'F', [f"his own position group is {x['short']*100:.0f}% out "
                     f"tonight, so this projection runs low and the under "
                     f"is the wrong side of a known error"]

    if edge >= 0.05:
        base = 4
    elif edge >= 0.03:
        base = 3
    elif edge >= 0.015:
        base = 2
    elif edge > 0:
        base = 1
    else:
        return 'F', [f'{x["p_mine"]*100:.1f}% is below the '
                     f'{breakeven*100:.1f}% a bet needs at -110']
    why.append(f'{edge*100:+.1f} points of edge over break-even')

    # Each of these has cost money or nearly has, so each costs a grade.
    if x['g'] is not None and x['g'] < 3:
        base -= 1
        why.append(f'only {x["g"]} game(s) of this season to read him from')
    gap = abs(x['gap']) if x['gap'] is not None else 0.0
    if gap > 0.06:
        base -= 1
        why.append(f'{gap*100:.0f}-point disagreement with {x["books"] or "?"} '
                   f'books -- every large gap we have examined was our error')
    if x.get('keep') is not None and x['keep'] < 6:
        base -= 1
        why.append(f'that defence has kept only {x["keep"]}/8 of last '
                   f'season\'s rotation, so the matchup read is thin')
    if x['flag'] == 'bias-FOR':
        why.append(f'his position group is {x["short"]*100:.0f}% out, so this '
                   f'projection is if anything low -- the error favours '
                   f'this side')
    if x['flag'] == 'opp-short':
        why.append(f'opponent is {x["opp_short"]*100:.0f}% of minutes down')

    return 'ABCDF'[max(0, min(4, 4 - base))], why


def context(x):
    """One line a reader can check, rather than a number to trust."""
    bits = []
    if x.get('via') and x.get('via_z') is not None:
        soft = 'soft' if x['via_z'] > 0 else 'strong'
        bits.append(f"{x['opp']} is {soft} from {x['via']} "
                    f"({x['via_z']:+.2f})")
    bits.append(f"projects {x['mean']:.1f} against {x['line']:.1f}")
    if x.get('min_adj'):
        bits.append(f"{x['min_adj']:+.1f} min for the spread")
    if x['flag'] != '-':
        bits.append({'bias-FOR': 'his group short-handed (error favours this)',
                     'bias-AGAINST': 'his group short-handed (error opposes)',
                     'opp-short': 'opponent short-handed'}[x['flag']])
    return '; '.join(bits)


# card market key -> the name slate.py grades under
# slate.py already settles pra -- MARKETS there has carried
# lambda r: r['PTS'] + r['REB'] + r['AST'] since the Covers scrapes,
# with the aliases to match. Nothing to add on that side.
SLATE_MARKET = {'pts': 'points', 'reb': 'rebounds',
                'ast': 'assists', 'threes': 'threes', 'pra': 'pra'}


def log_rows(day, cards, odds=-110, slate_path=None):
    """Write every scored row into slate.jsonl so tomorrow settles it.

    WHY EVERY ROW AND NOT JUST THE PICKS
      The calibration learns from outcomes. Logging only the bets
      would feed it two or three rows a night, which is how we got to
      546 player-games taking six days. Logging the whole card feeds
      it everything the model had an opinion about, including the
      opinions it was least sure of -- and those are the ones that say
      whether the grading bands mean anything.

      Nothing here touches the published record. slate.py's summarise()
      counts featured tiers only, and these go in as 'log', which it
      already excludes. The log page shows them and says why the two
      numbers differ.

    THE PRICE IS A CONVENTION, AND IS MARKED AS ONE
      A measurement row was never bet, so there is no price it was
      taken at. -110 is recorded so units compute, and 'measurement':
      true marks it as not a real position. A row you actually back
      should be logged by hand at the price you got -- the add lines
      for the A/B picks are printed for exactly that.
    """
    import importlib.util
    sp = importlib.util.spec_from_file_location(
        'slatemod', os.path.join(HERE, 'slate.py'))
    sl = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(sl)
    if slate_path:                      # so this path can be tested
        sl.SLATE = slate_path

    rows = sl.load()
    have = {(r['day'], norm_player(r['subject']), r['market'], r['side'],
             float(r['line'])) for r in rows}
    added, skipped, picks = 0, 0, []
    for mk, rws in (cards or {}).items():
        smk = SLATE_MARKET.get(mk)
        if not smk:
            continue
        for r in rws:
            key = (day, norm_player(r['player']), smk, r['side'],
                   float(r['line']))
            if key in have:
                skipped += 1
                continue
            if r.get('pick'):
                picks.append(
                    f"python3 slate.py add \"{r['player']}|{smk}|"
                    f"{r['side']}|{r['line']}|ODDS|board\" --day {day}")
            rows.append({
                'day': day, 'subject': r['player'], 'market': smk,
                'side': r['side'], 'line': float(r['line']),
                'odds': int(odds), 'tier': 'log',
                'measurement': True, 'grade': r.get('grade'),
                'p_mine': r.get('p_mine'), 'p_mkt': r.get('p_mkt'),
                'fit': r.get('fit'), 'flag': r.get('flag'),
                'logged_at': datetime.datetime.now().astimezone().isoformat(),
                'result': None, 'actual': None, 'units': None,
                'graded_at': None,
            })
            have.add(key)
            added += 1
    sl.save(rows)
    print(f'\nlogged {added} row(s) to {sl.SLATE} as tier "log"'
          + (f', {skipped} already there' if skipped else ''))
    print('  these grade tomorrow and feed the calibration; the headline\n'
          f'  record excludes them. Price recorded as {odds:+d} and marked\n'
          '  measurement -- they were not bet.')
    if picks:
        print(f'\n{len(picks)} pick(s) graded A/B. Log these yourself at the '
              f'price you actually get:')
        for c in picks:
            print(f'  {c}')
    return added


# --- how hard should the blend shrink? -------------------------------
#
# PRIOR_GAMES = 8 was never measured. It was copied from the minutes
# blend in defense.py, which is a different quantity entirely, and it
# sets how much of a projection comes from last season: at three games
# the weight on the prior is 8/11, so seventy per cent of a man's
# projected rate is what he did a year ago.
#
# That is invisible in a league-wide bias check, because it cancels.
# A player whose role shrank regresses correctly, one whose role grew
# gets dragged down, and the average comes out at 0.994. But the drag
# is proportional to production, so it falls hardest on exactly the
# players whose lines are worth betting -- and on 28 October every
# star cleared his rebound number while the model had him under it:
# Wembanyama 14 against a 10.9 projection, Mobley 12 against 9.6,
# Embiid 11 against 8.6.
#
# So measure it. Walk-forward, project every player-game's RATE at a
# range of constants, and see which one predicts best. k=0 is this
# season only; a large k is last season only. The answer is whatever
# the cache says, not whatever defense.py uses for minutes.

SHRINK_KS = (0.0, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 24.0, 1e6)


def fit_shrinkage(market, logs_days, min_prior=2, ks=SHRINK_KS):
    """Walk-forward error for each shrinkage constant."""
    field, _ = MARKETS[market]
    priors = priors_for(market)
    days = sorted(logs_days)
    obs = []
    for i, day in enumerate(days):
        if i < min_prior:
            continue
        hist = player_means(logs_days, field, before=day)
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            k = norm_player(r.get('player'))
            t = code(r.get('team'))
            prev = hist.get((k, t))
            pr36 = priors.get(k)
            if not prev or pr36 is None:
                continue
            _pg, _mp, g, cur36 = prev
            act36 = (r.get(field) or 0) / m * 36.0
            obs.append((cur36, pr36, g, act36))
    if len(obs) < 60:
        return {'market': market, 'ok': False, 'n': len(obs)}

    out = []
    for kk in ks:
        errs, ratios = [], []
        for cur36, pr36, g, act36 in obs:
            w = g / (g + kk) if kk < 1e5 else 0.0
            pred = w * cur36 + (1 - w) * pr36
            if pred <= 0.2:
                continue
            errs.append((act36 - pred) ** 2)
            ratios.append(act36 / pred)
        if not errs:
            continue
        out.append({'k': None if kk >= 1e5 else kk,
                    'rmse': round(math.sqrt(statistics.fmean(errs)), 3),
                    'ratio': round(statistics.fmean(ratios), 3),
                    'n': len(errs)})
    best = min(out, key=lambda d: d['rmse']) if out else None
    cur = next((d for d in out if d['k'] == PRIOR_GAMES), None)
    return {'market': market, 'ok': True, 'n': len(obs), 'curve': out,
            'best': best, 'current': cur}


def show_shrinkage(f):
    print(f"\nSHRINKAGE -- {f['market']}")
    if not f.get('ok'):
        print(f"  only {f['n']} player-games. Not measurable.")
        return
    print(f"  {f['n']} player-games, walk-forward")
    print(f"\n  {'PRIOR k':>9}{'RMSE':>9}{'ACT/PRED':>10}")
    for d in f['curve']:
        tag = ''
        if f['best'] and d['k'] == f['best']['k']:
            tag = '   <- best'
        if d['k'] == PRIOR_GAMES:
            tag += '   (in use)'
        kk = 'prior only' if d['k'] is None else f"{d['k']:.0f}"
        print(f"  {kk:>9}{d['rmse']:>9.3f}{d['ratio']:>10.3f}{tag}")
    b, c = f['best'], f['current']
    if b and c:
        gain = (c['rmse'] - b['rmse']) / c['rmse'] * 100 if c['rmse'] else 0
        kb = 'prior only' if b['k'] is None else f"{b['k']:.0f}"
        print(f"\n  best k = {kb} against {PRIOR_GAMES:.0f} in use"
              + (f"  ({gain:+.1f}% RMSE)" if abs(gain) > 0.05 else
                 '  -- no better'))
        if b['k'] is not None and b['k'] < PRIOR_GAMES:
            print('  The blend is shrinking too hard: this season deserves\n'
                  '  more weight than it gets, which drags every projection\n'
                  '  down in proportion to production and quietly pushes\n'
                  '  probability onto the unders.')
        elif b['k'] is not None and b['k'] > PRIOR_GAMES:
            print('  The blend is not shrinking enough: a few games are\n'
                  '  being trusted more than they deserve.')
    print('\n  ACT/PRED at the right k should sit near 1.000. A value above\n'
          '  one at every k means the prior itself is low, not the blend.')


def bias_by_size(market, logs_days, k=None, min_prior=2):
    """Is the bias a constant, or does it grow with the projection?"""
    field, _ = MARKETS[market]
    priors = priors_for(market)
    kk = PRIOR_GAMES if k is None else k
    days = sorted(logs_days)
    pts = []
    for i, day in enumerate(days):
        if i < min_prior:
            continue
        hist = player_means(logs_days, field, before=day)
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            key = norm_player(r.get('player'))
            t = code(r.get('team'))
            prev, pr36 = hist.get((key, t)), priors.get(key)
            if not prev or pr36 is None:
                continue
            _pg, _mp, g, cur36 = prev
            w = g / (g + kk)
            pred = (w * cur36 + (1 - w) * pr36) / 36.0 * m
            if pred <= 0.5:
                continue
            pts.append((pred, (r.get(field) or 0) / pred))
    if len(pts) < 40:
        return None
    pts.sort()
    n = len(pts) // 3
    bands = [('small', pts[:n]), ('middle', pts[n:2 * n]), ('large', pts[2 * n:])]
    out = []
    for lbl, v in bands:
        if not v:
            continue
        rs = [r for _p, r in v]
        sd = statistics.stdev(rs) if len(rs) > 1 else 0.0
        out.append({'band': lbl, 'n': len(v),
                    'lo': round(v[0][0], 1), 'hi': round(v[-1][0], 1),
                    'ratio': round(statistics.fmean(rs), 3),
                    'se': round(sd / math.sqrt(len(rs)), 3) if rs else None})
    return out


def show_size_bias(market, bands):
    print(f"\n  bias by size of projection -- {market}")
    if not bands:
        print('    not enough player-games to split.')
        return
    print(f"    {'BAND':8}{'RANGE':>14}{'N':>6}{'ACT/PRED':>10}{'SE':>8}")
    for b in bands:
        # built first, not nested: an f-string reusing its own quote
        # character inside a brace is Python 3.12+ only, and this
        # machine runs 3.13 while the one that has to execute it runs
        # an older Command Line Tools build. ast.parse here accepts it
        # whatever feature_version it is given, so the check passed and
        # the file died on arrival.
        rng = f"{b['lo']}-{b['hi']}"
        print(f"    {b['band']:8}{rng:>14}{b['n']:>6}"
              f"{b['ratio']:>10.3f}{b['se']:>8.3f}")
    if len(bands) >= 2:
        lo, hi = bands[0], bands[-1]
        d = hi['ratio'] - lo['ratio']
        se = math.sqrt((lo['se'] or 0) ** 2 + (hi['se'] or 0) ** 2)
        z = d / se if se else 0.0
        print(f"    large minus small: {d:+.3f} ({z:+.2f} SE)")
        if z >= 2:
            print('    -> the bias GROWS with the projection. One flat\n'
                  '       correction cannot fix both ends, and the large\n'
                  '       end is where the bets are.')
        elif z <= -2:
            print('    -> the bias shrinks with the projection.')
        else:
            print('    -> no size dependence visible at this sample.')


# --- is the correction fitted on the people we actually bet? ----------
#
# THE QUESTION THIS EXISTS TO ASK
#   mean_ratio -- the flat correction every projection is multiplied by
#   -- is fitted over every player-game with MIN_MIN minutes or more.
#   The card is built only from players the books posted a line on,
#   which in practice means starters and the top of the rotation.
#
#   Those are two different populations. If ACT/PRED differs between
#   them, the correction is measured on people we never bet and applied
#   to people we always do, and it is then wrong in a FIXED DIRECTION
#   on every row of every card -- which is exactly the shape of error
#   that survives a sample size, because it never averages out.
#
#   There is a reason to think it does differ. ACT/PRED on the pts
#   small band is 0.872 +/- 0.034 -- the projection runs about 13% high
#   down there -- and the small band is full of eight-to-eighteen
#   minute players whose minutes are the thing being mis-estimated, not
#   their rate. Nobody posts a prop on them. Meanwhile the board's
#   unders kept landing on rotation players who then played thirty
#   minutes and went over.
#
#   Only this cache can answer it, because only this cache knows which
#   player-games had a price. That is the whole point of having kept
#   external_picks.jsonl.
#
# WHAT A NULL RESULT HERE WOULD MEAN
#   That the flat correction is fine and the 28 Oct result was noise,
#   which is the likelier answer and has to stay on the table. So the
#   contrast is printed with its standard error and with the minimum
#   difference this sample could have detected. A null without an MDE
#   is not a finding either.
MIN_BANDS = ((8.0, 18.0), (18.0, 26.0), (26.0, 34.0), (34.0, 999.0))


def _ratio_stats(rs):
    if not rs:
        return None
    sd = statistics.stdev(rs) if len(rs) > 1 else 0.0
    return {'n': len(rs), 'ratio': round(statistics.fmean(rs), 3),
            'se': round(sd / math.sqrt(len(rs)), 3) if rs else None}


def bias_by_population(market, logs_days, k=None, min_prior=2):
    """ACT/PRED split by minutes played and by whether a line existed."""
    field, _ = MARKETS[market]
    priors = priors_for(market)
    kk = PRIOR_GAMES if k is None else k
    days = sorted(logs_days)
    pts = []
    for i, day in enumerate(days):
        if i < min_prior:
            continue
        hist = player_means(logs_days, field, before=day)
        priced = {key for (key, mk) in load_prices(day) if mk == market}
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            key = norm_player(r.get('player'))
            t = code(r.get('team'))
            prev, pr36 = hist.get((key, t)), priors.get(key)
            if not prev or pr36 is None:
                continue
            _pg, _mp, g, cur36 = prev
            w = g / (g + kk)
            pred = (w * cur36 + (1 - w) * pr36) / 36.0 * m
            if pred <= 0.5:
                continue
            pts.append({'pred': pred, 'ratio': (r.get(field) or 0) / pred,
                        'min': m, 'priced': key in priced})

    if len(pts) < 40:
        return {'market': market, 'ok': False, 'n': len(pts)}

    mins = []
    for lo, hi in MIN_BANDS:
        st = _ratio_stats([p['ratio'] for p in pts if lo <= p['min'] < hi])
        if st:
            st['lo'], st['hi'] = lo, (None if hi > 900 else hi)
            mins.append(st)

    yes = [p['ratio'] for p in pts if p['priced']]
    no = [p['ratio'] for p in pts if not p['priced']]
    out = {'market': market, 'ok': True, 'n': len(pts), 'minutes': mins,
           'priced': _ratio_stats(yes), 'unpriced': _ratio_stats(no)}

    # the contrast, with the effect this sample could have seen
    if out['priced'] and out['unpriced']:
        a, b = out['priced'], out['unpriced']
        d = a['ratio'] - b['ratio']
        se = math.sqrt((a['se'] or 0) ** 2 + (b['se'] or 0) ** 2)
        out['delta'] = round(d, 3)
        out['delta_se'] = round(se, 3)
        out['delta_z'] = round(d / se, 2) if se else None
        out['mde'] = round(2.8 * se, 3) if se else None

    # and the priced population's own minutes profile, because if the
    # two populations overlap heavily there is nothing here to find and
    # the contrast above is just the same people twice.
    if yes:
        pm = sorted(p['min'] for p in pts if p['priced'])
        out['priced_min_median'] = round(pm[len(pm) // 2], 1)
    if no:
        um = sorted(p['min'] for p in pts if not p['priced'])
        out['unpriced_min_median'] = round(um[len(um) // 2], 1)
    return out


def load_popcal(market):
    """The saved --bias-pop result for one market, or None."""
    if not os.path.exists(POPCAL):
        return None
    try:
        c = (json.load(open(POPCAL)) or {}).get(market)
    except Exception:
        return None
    return c if (c and c.get('ok')) else None


# A market is thinly calibrated when the sample behind its correction
# could hide an error big enough to flip the side of a typical row. A
# prop line sits close enough to the projection that a 10% error in the
# projection moves P(over) by several points, which is the whole
# distance between a lean and its opposite. So: 0.10 on the MDE.
THIN_MDE = 0.10


def footing(market):
    """What the page needs to say about how well this market is known."""
    c = load_popcal(market)
    if not c:
        return None
    pr = c.get('priced') or {}
    n, se = pr.get('n'), pr.get('se')
    if not n or se is None:
        return None
    mde = round(2.8 * se, 3)
    # How old the measurement is. A weekly step nobody is reminded of
    # is a weekly step that silently becomes a monthly one, and then
    # the page is quoting a footing from a sample that has since
    # doubled.
    stale = None
    if c.get('measured'):
        try:
            d0 = datetime.date.fromisoformat(c['measured'])
            stale = (datetime.date.today() - d0).days
        except Exception:
            stale = None
    return {'market': market, 'n': n, 'ratio': pr.get('ratio'), 'se': se,
            'mde': mde, 'thin': mde > THIN_MDE,
            'measured': c.get('measured'), 'stale_days': stale}


def show_bias_pop(c):
    m = c['market']
    print(f'\nPOPULATION BIAS -- {m}')
    if not c.get('ok'):
        print(f'  only {c["n"]} player-games. Not enough to split.')
        return
    print(f'  {c["n"]} player-games, walk-forward\n')
    print(f"    {'MINUTES':12}{'N':>6}{'ACT/PRED':>10}{'SE':>8}")
    for b in c['minutes']:
        rng = f"{b['lo']:.0f}-{b['hi']:.0f}" if b['hi'] else f"{b['lo']:.0f}+"
        print(f"    {rng:12}{b['n']:>6}{b['ratio']:>10.3f}{b['se']:>8.3f}")

    print(f"\n    {'POPULATION':12}{'N':>6}{'ACT/PRED':>10}{'SE':>8}"
          f"{'MED MIN':>9}")
    for lbl, key, mk in (('had a line', 'priced', 'priced_min_median'),
                         ('no line', 'unpriced', 'unpriced_min_median')):
        b = c.get(key)
        if not b:
            continue
        md = c.get(mk)
        print(f"    {lbl:12}{b['n']:>6}{b['ratio']:>10.3f}{b['se']:>8.3f}"
              f"{(md if md is not None else 0):>9.1f}")

    if c.get('delta_z') is None:
        print('\n    one population is empty -- nothing to contrast.')
        return
    print(f"\n    priced minus unpriced: {c['delta']:+.3f} "
          f"({c['delta_z']:+.2f} SE)")
    if abs(c['delta_z']) >= 2:
        d = 'HIGH' if c['delta'] < 0 else 'LOW'
        print(f"    -> the projection runs {d} on the players we bet,\n"
              f"       relative to the ones we do not. The flat correction\n"
              f"       is being fitted on the wrong population and is\n"
              f"       wrong in a fixed direction on every card. Refit it\n"
              f"       on the priced rows only.")
    else:
        print(f"    -> no population difference visible. This sample could\n"
              f"       have detected {c['mde']:.3f} at 2.8 SE; the observed\n"
              f"       difference is {abs(c['delta']):.3f}. So a correction\n"
              f"       error smaller than {c['mde']:.3f} is still possible\n"
              f"       and this does not clear the board of it.")

    print(f"\n    ACT/PRED below 1.000 means the projection runs HIGH and\n"
          f"    the model will keep choosing unders it should not. Above\n"
          f"    1.000 is the reverse. The number that matters is the one\n"
          f"    on the 'had a line' row, because that is the only\n"
          f"    population a card is ever built from.")


# --- what does adding the three variances actually cost? -------------
#
# The claim everywhere is that you cannot add Var(P), Var(R) and
# Var(A) to get Var(P+R+A), because minutes drive all three and the
# covariances are positive. That is algebraically certain. What nobody
# publishes is HOW BIG the error is, and the size is the only part
# that decides whether it matters here -- a 2% understatement of the
# SD moves a probability by well under a point and can be ignored; a
# 25% understatement moves every PRA row several points in the same
# direction and cannot.
#
# So measure it, on this cache.
#
# METHOD, and why it is not per-player variances
#   A player's own variance over six games is a terrible estimate --
#   five degrees of freedom, and we have already been burned once by
#   using a three-game spread as if it were a distribution. Instead
#   every player-game contributes a DEVIATION from that player's own
#   mean, all the deviations pool league-wide, and the covariance
#   matrix is estimated from the pool. Each player still only tells us
#   about his own spread; the pooling is across players, which is
#   where the degrees of freedom come from.
#
#   The second pass residualises on minutes first. If the correlation
#   collapses once minutes are removed, the three stats move together
#   only because playing time moves them together -- which is exactly
#   what the minutes model already handles, and it tells us where the
#   covariance actually lives.
PRA_MIN_GAMES = 4


def pra_variance(logs_days, min_games=PRA_MIN_GAMES):
    """Measured Var(P+R+A) against the naive sum of the three."""
    by = collections.defaultdict(list)
    for day in sorted(logs_days):
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            p, rb, a = r.get('pts'), r.get('reb'), r.get('ast')
            if p is None or rb is None or a is None:
                continue
            by[(norm_player(r.get('player')), code(r.get('team')))].append(
                (float(p), float(rb), float(a), float(m)))

    raw, resid = [], []
    players = 0
    for _k, games in by.items():
        if len(games) < min_games:
            continue
        players += 1
        mp = statistics.fmean([g[0] for g in games])
        mr = statistics.fmean([g[1] for g in games])
        ma = statistics.fmean([g[2] for g in games])
        mm = statistics.fmean([g[3] for g in games])
        # minutes slope per stat, for this player, to residualise on
        sm = sum((g[3] - mm) ** 2 for g in games)
        bp = br = ba = 0.0
        if sm > 0:
            bp = sum((g[3] - mm) * (g[0] - mp) for g in games) / sm
            br = sum((g[3] - mm) * (g[1] - mr) for g in games) / sm
            ba = sum((g[3] - mm) * (g[2] - ma) for g in games) / sm
        for p, rb, a, m in games:
            dp, dr, da = p - mp, rb - mr, a - ma
            raw.append((dp, dr, da))
            dm = m - mm
            resid.append((dp - bp * dm, dr - br * dm, da - ba * dm))

    if len(raw) < 60:
        return {'ok': False, 'n': len(raw), 'players': players}

    def stats_for(pts):
        n = len(pts)
        vp = sum(x[0] ** 2 for x in pts) / n
        vr = sum(x[1] ** 2 for x in pts) / n
        va = sum(x[2] ** 2 for x in pts) / n
        cpr = sum(x[0] * x[1] for x in pts) / n
        cpa = sum(x[0] * x[2] for x in pts) / n
        cra = sum(x[1] * x[2] for x in pts) / n
        naive = vp + vr + va
        true = naive + 2 * (cpr + cpa + cra)

        def rho(c, v1, v2):
            d = math.sqrt(v1 * v2)
            return (c / d) if d > 0 else 0.0

        # SE of the ratio, by splitting the pool in half repeatedly is
        # overkill; the jackknife over players is what we want but the
        # pool is already deviations, so a bootstrap-free approximation:
        # the ratio is a mean of per-observation (sum^2) over a mean of
        # per-observation (sum of squares), so propagate both SEs.
        sums = [(x[0] + x[1] + x[2]) ** 2 for x in pts]
        inds = [x[0] ** 2 + x[1] ** 2 + x[2] ** 2 for x in pts]
        se_s = (statistics.stdev(sums) / math.sqrt(n)) if n > 1 else 0.0
        se_i = (statistics.stdev(inds) / math.sqrt(n)) if n > 1 else 0.0
        ratio = (true / naive) if naive > 0 else 0.0
        se_ratio = (ratio * math.sqrt((se_s / true) ** 2 + (se_i / naive) ** 2)
                    if true > 0 and naive > 0 else 0.0)
        return {'n': n, 'var_naive': naive, 'var_true': true,
                'ratio': ratio, 'se': se_ratio,
                'sd_naive': math.sqrt(naive), 'sd_true': math.sqrt(true),
                'r_pr': rho(cpr, vp, vr), 'r_pa': rho(cpa, vp, va),
                'r_ra': rho(cra, vr, va)}

    return {'ok': True, 'players': players,
            'raw': stats_for(raw), 'resid': stats_for(resid)}


def show_pra_variance(c):
    print('\nPRA VARIANCE -- is adding the three variances wrong enough '
          'to matter?')
    if not c.get('ok'):
        print(f'  only {c["n"]} player-games from {c["players"]} player(s). '
              f'Not enough.')
        return
    print(f'  {c["raw"]["n"]} player-games from {c["players"]} players '
          f'with {PRA_MIN_GAMES}+ games each,')
    print('  pooled as deviations from each player\'s own mean.\n')
    print(f"    {'':22}{'RAW':>10}{'MINUTES OUT':>14}")
    r, d = c['raw'], c['resid']
    rows = [
        ('corr pts-reb', r['r_pr'], d['r_pr']),
        ('corr pts-ast', r['r_pa'], d['r_pa']),
        ('corr reb-ast', r['r_ra'], d['r_ra']),
    ]
    for lbl, a, b in rows:
        print(f'    {lbl:22}{a:>10.3f}{b:>14.3f}')
    print()
    print(f"    {'SD, added naively':22}{r['sd_naive']:>10.2f}"
          f"{d['sd_naive']:>14.2f}")
    print(f"    {'SD, measured':22}{r['sd_true']:>10.2f}"
          f"{d['sd_true']:>14.2f}")
    infl_r = (r['sd_true'] / r['sd_naive'] - 1) * 100 if r['sd_naive'] else 0
    infl_d = (d['sd_true'] / d['sd_naive'] - 1) * 100 if d['sd_naive'] else 0
    print(f"    {'understated by':22}{infl_r:>9.1f}%{infl_d:>13.1f}%")
    print(f"\n    variance ratio (measured / naive): "
          f"{r['ratio']:.3f} +/- {r['se']:.3f}")

    # What that does to a probability, which is the only thing that
    # decides whether this is worth the complexity.
    sd_n, sd_t = r['sd_naive'], r['sd_true']
    if sd_n > 0 and sd_t > 0:
        for off in (1.0, 2.0):
            pn = 0.5 * (1 + math.erf(off / (sd_n * math.sqrt(2))))
            pt = 0.5 * (1 + math.erf(off / (sd_t * math.sqrt(2))))
            print(f'    a projection {off:.0f} point(s) clear of the line '
                  f'reads {pn*100:.1f}% naively,\n'
                  f'      {pt*100:.1f}% at the measured spread '
                  f'({(pn-pt)*100:+.1f} points of false confidence)')

    print('\n  This is why pra is a MEASURED column and not three numbers\n'
          '  added together. variance_fit() reads the pra column off real\n'
          '  player-games, so the covariances above are already inside the\n'
          '  a*mean^b it fits -- no correlation matrix is estimated, and\n'
          '  none can be got wrong. The table is here to show the size of\n'
          '  the error that choice avoids, not to feed anything.')
    if abs(d['r_pr']) < 0.1 and abs(d['r_pa']) < 0.1 and abs(d['r_ra']) < 0.1:
        print('\n  With minutes removed the three are close to independent,\n'
              '  so nearly all of the covariance is playing time -- which\n'
              '  the minutes model already drives. That is the good case:\n'
              '  it means PRA carries no structure the parts do not.')


def main():
    ap = argparse.ArgumentParser(description='one ranked card')
    ap.add_argument('--day', help='YYYYMMDD (not needed for --calibrate)')
    ap.add_argument('--market', default='pts',
                    help='one of ' + ','.join(MARKETS) + ' (default pts)')
    ap.add_argument('--all-markets', action='store_true')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--breakeven', type=float, default=BREAKEVEN,
                    help='win rate a bet needs (default 0.524, i.e. -110)')
    ap.add_argument('--max-gap', type=float, default=MAX_GAP, dest='max_gap',
                    help='hide rows disagreeing with the market by more '
                         'than this (default 0.10)')
    ap.add_argument('--var', action='store_true', help='show variance fits')
    ap.add_argument('--fit-shrink', action='store_true', dest='fit_shrink',
                    help='measure the blend constant instead of assuming 8, '
                         'and report whether the bias grows with the size '
                         'of the projection')
    ap.add_argument('--pra-variance', action='store_true',
                    dest='pra_variance',
                    help='measure Var(P+R+A) against the naive sum of the three')
    ap.add_argument('--bias-pop', action='store_true',
                    dest='bias_pop',
                    help='ACT/PRED split by minutes and by whether a line\n                          existed -- is the flat correction fitted on\n                          the players we never bet?')
    ap.add_argument('--calibrate', action='store_true',
                    help='measure what FIT is worth, walk-forward, and save')
    ap.add_argument('--refresh-boards', action='store_true',
                    help='rebuild the per-day FIT cache the calibration uses')
    ap.add_argument('--board-arg', action='append', default=None,
                    help='flags passed to defense.py --board '
                         '(default: --top 40, so the card has candidates '
                         'to rank rather than only the board\'s own cut)')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--log', action='store_true',
                    help='write every scored row into slate.jsonl so it '
                         'grades tomorrow (A/B at their own tier, the rest '
                         'as log tier, which the headline record excludes)')
    ap.add_argument('--log-odds', type=int, default=-110, dest='log_odds',
                    help='price recorded for measurement rows (default -110)')
    ap.add_argument('--publish', metavar='DIR',
                    help='write the graded card to DIR/data/card.json '
                         'for the site (e.g. ~/sharpz-site)')
    a = ap.parse_args()
    _stamp()
    # --fit-shrink and --calibrate both read the cache and ignore the
    # day entirely; asking for one they will not use is friction for
    # no reason.
    if not a.day and not (a.calibrate or a.fit_shrink
                          or a.bias_pop or a.pra_variance):
        ap.error('--day is required unless you are calibrating, '
                 'fitting the shrinkage or measuring population '
                 'bias')

    if not os.path.exists(LOGS):
        print(f'no {LOGS} -- run edges.py --build', file=sys.stderr)
        return 1
    logs_days = json.load(open(LOGS)).get('days') or {}
    n_pra = derive_pra(logs_days)
    n_rows = sum(len(v) for v in logs_days.values())
    if n_rows and n_pra < n_rows * 0.9:
        print(f'  ! only {n_pra}/{n_rows} log rows have all three of '
              f'pts/reb/ast, so pra is thin', file=sys.stderr)

    # before the network calls: this one reads the cache only, and
    # game_lines() below would otherwise spend an API request that
    # nothing in this path uses.
    if a.pra_variance:
        show_pra_variance(pra_variance(logs_days))
        return 0

    if a.bias_pop:
        store = {}
        if os.path.exists(POPCAL):
            try:
                store = json.load(open(POPCAL)) or {}
            except Exception:
                store = {}
        today = datetime.date.today().isoformat()
        for mk in (list(MARKETS) if a.all_markets else [a.market]):
            c = bias_by_population(mk, logs_days)
            c['measured'] = today
            store[mk] = c
            show_bias_pop(c)
        # Saved so the card can publish the footing rather than the
        # reader having to take the ordering on trust. A measurement
        # that only ever prints to a terminal cannot reach the page,
        # and the page is where the claim is being made.
        json.dump(store, open(POPCAL, 'w'), indent=1)
        print(f'\nsaved -> {POPCAL}')
        print('  the card will now publish each market\'s footing.')
        return 0

    gs = games(before=a.day)
    curve, _n = margin_curve(gs) if gs else (None, 0)
    lines = {}
    try:
        lines = game_lines(a.day)
    except ApiError as e:
        print(f'  ! lines unavailable, no minutes adjustment: {e}',
              file=sys.stderr)

    if a.fit_shrink:
        for mk in (list(MARKETS) if a.all_markets else [a.market]):
            f = fit_shrinkage(mk, logs_days)
            show_shrinkage(f)
            k = (f.get('best') or {}).get('k') if f.get('ok') else None
            show_size_bias(mk, bias_by_size(mk, logs_days))
            if k is not None and f.get('ok') and k != PRIOR_GAMES:
                print(f'\n  at the fitted k={k:.0f} instead:')
                show_size_bias(mk, bias_by_size(mk, logs_days, k=k))
        return 0

    if a.calibrate:
        store = {}
        if os.path.exists(FITCAL):
            try:
                store = json.load(open(FITCAL)) or {}
            except Exception:
                store = {}
        for mk in (list(MARKETS) if a.all_markets else [a.market]):
            c = calibrate(mk, logs_days, refresh=a.refresh_boards)
            store[mk] = c
            show_calibration(c)
        json.dump(store, open(FITCAL, 'w'), indent=1)
        print(f'\nsaved -> {FITCAL}')
        return 0

    allpos = positions()
    dep = depletion(logs_days, injury_map(), pos=allpos, before=a.day)

    board = load_board(a.day, extra=(a.board_arg
                                     if a.board_arg is not None
                                     else ['--top', '40']))
    markets = list(MARKETS) if a.all_markets else [a.market]

    out = {'day': a.day, 'cards': {},
           'generated': __import__('datetime').datetime.now()
           .astimezone().isoformat()}
    for mk in markets:
        field, _ = MARKETS[mk]
        vfit = variance_fit(logs_days, field)
        if a.var and vfit and not a.json:
            aa, bb, r2, n = vfit
            print(f'  {mk:7} var = {aa:.3f} * mean^{bb:.2f}   '
                  f'R2 {r2:.2f}   {n} players')
        bias, cal = load_calibration(mk)
        if not a.json:
            if bias:
                print(f'  {mk}: projection bias x{bias:.3f} (measured '
                      f'actual/projected) -- applied')
            if cal:
                print(f"  {mk}: FIT x{1 + cal['beta']:.3f} per +1.00 FIT "
                      f"(z {cal['z']:+.1f}) -- applied")
            else:
                print(f'  {mk}: FIT not significant -- picks the side only, '
                      f'no multiplier')
        rows = build(a.day, mk, board, logs_days, lines, curve, vfit,
                     before=a.day, cal=cal, bias=bias, dep=dep,
                     allpos=allpos)
        for x in rows:
            x['grade'], x['why'] = grade_row(x, a.breakeven)
            x['context'] = context(x)
            # his rule: a disagreement this large has been our error
            # every time we have looked, so it is not published as a
            # pick at all rather than published with a caveat
            # A ROW THAT ARGUES WITH ITSELF DOES NOT GO ON THE PAGE
            #
            # The board picks the side from the matchup. The projection
            # is built separately. When the projection lands on the
            # OTHER SIDE OF THE LINE from the pick -- board says over,
            # our own mean is below the line -- the two halves of the
            # model are pointing in opposite directions. That row is
            # not "a matchup we like for this side", which is the only
            # claim the page makes.
            #
            # THIS IS THE SECOND VERSION OF THIS RULE. The first was
            # `p_mine < 0.50`, and it was wrong for a reason this
            # project had already established and I walked straight
            # back into: every counting stat is right-skewed, so a line
            # sitting exactly ON the projection prices near 46-48% on
            # the over. 47% is the BASELINE, not a verdict -- it is
            # printed under the card in those words. A 50% floor
            # therefore does not filter bad rows, it filters the skew,
            # and it would have thrown out Sabonis over 14.5 with a
            # projection of exactly 14.5: a dead-neutral row, not a
            # contradictory one. On a three-game slate it emptied the
            # rebounds card entirely.
            #
            # Comparing the mean to the line has no such problem. It is
            # a counted fact about two of our own numbers, the same
            # category as bias-AGAINST, and it says nothing about the
            # price -- which is the other trap on this road.
            #
            # Dead-on (mean == line) is NOT a conflict. The projection
            # is agnostic there, and treating agnostic as disqualifying
            # is the price-as-veto frame wearing a different hat.
            #
            # Still logged, still settled tomorrow, still feeds the
            # calibration. Just not presented as something we like.
            x['conflict'] = ((x['side'] == 'over' and x['mean'] < x['line'])
                             or (x['side'] == 'under'
                                 and x['mean'] > x['line']))
            x['shown'] = ((x['gap'] is None or abs(x['gap']) <= a.max_gap)
                          and not x['conflict'])
            x['pick'] = x['grade'] in PICK_GRADES and x['shown']
        out['cards'][mk] = rows
        if not a.json:
            show(rows, a.day, mk, vfit, a.top)
    if a.publish:
        # Published WITH the hidden rows, flagged rather than dropped.
        # The page shows only what it should, but the file keeps the
        # whole measurement so tomorrow's grading can settle every row
        # -- including the ones we declined. A board that only records
        # the bets it liked cannot tell you whether its dislikes were
        # right, and those are most of the information.
        d = os.path.join(os.path.expanduser(a.publish), 'data')
        os.makedirs(d, exist_ok=True)
        pub = {'day': a.day, 'generated': out.get('generated'),
               'breakeven': a.breakeven, 'max_gap': a.max_gap,
               'grades_published': GRADES_PUBLISHED,
               'rubric': RUBRIC if GRADES_PUBLISHED else None,
               'under_test': ('This card is ordered by FIT, the matchup '
                              'read. Over 546 player-games walk-forward '
                              'FIT did not predict production, so the '
                              'ordering is a hypothesis being measured '
                              'in public, not a set of rated bets. Every '
                              'row is graded against the box score the '
                              'next morning. Letters return when the '
                              'bands separate.'),
               'footing': {mk: footing(mk) for mk in out['cards']},
               'note': ('FIT is the matchup signal the board ranks on. '
                        'Measured over 546 player-games walk-forward it '
                        'did not predict production, so it selects the '
                        'side and is not multiplied into the projection.'),
               'cards': {}}
        for mk, rws in out['cards'].items():
            pub['cards'][mk] = [
                {k: r[k] for k in
                 ('player', 'team', 'opp', 'pos', 'side', 'line', 'fit',
                  'via', 'via_z', 'mean', 'sd', 'p_mine', 'p_mkt', 'gap',
                  'books', 'g', 'short', 'team_short', 'opp_short', 'flag',
                  'context', 'shown', 'conflict') if k in r}
                for r in rws]
        fn = os.path.join(d, 'card.json')
        json.dump(pub, open(fn, 'w'), indent=1)
        n_pick = sum(1 for rws in out['cards'].values()
                     for r in rws if r.get('pick'))
        n_shown = sum(1 for rws in out['cards'].values()
                      for r in rws if r.get('shown'))
        n_all = sum(len(v) for v in out['cards'].values())
        n_conf = sum(1 for rws in out['cards'].values()
                     for r in rws if r.get('conflict'))
        n_gap = sum(1 for rws in out['cards'].values() for r in rws
                    if r.get('gap') is not None
                    and abs(r['gap']) > a.max_gap and not r.get('conflict'))
        print(f'\nwrote {fn}: {n_all} rows, {n_shown} shown.  '
              f'[card.py {BUILD}]')
        if n_gap:
            print(f'  {n_gap} held back over the '
                  f'{a.max_gap*100:.0f}-point gap limit.')
        if n_conf:
            print(f'  {n_conf} held back as self-contradictory: the board '
                  f'picked a side our\n  own projection sits on the wrong '
                  f'side of. Logged and settled like the rest.')
        if GRADES_PUBLISHED:
            print(f'  {n_pick} graded A/B and published as picks.')
        else:
            print(f'  no grades published. {n_pick} row(s) would have '
                  f'graded A/B; they are logged and settled like every\n  other row, and the page shows the ordering only.')

    if a.log:
        log_rows(a.day, out['cards'], a.log_odds)

    if a.json:
        print(json.dumps(out, indent=1))
    return 0



# --- calibration: what is a point of FIT actually worth? --------------
#
# THE GAP THIS FILLS
#   Until now FIT chose the side and nothing else. The projected mean
#   was matchup-neutral, so the card could rank San Antonio's players
#   as the three best matchups on the slate and simultaneously give
#   their overs a 33% chance -- the two halves of one row disagreeing
#   because nothing connected them. "The defence is soft here" and
#   "he will score more than 25.5" were never in the same units.
#
#   So: measure it. For every player-game in the cache, take his
#   walk-forward projected RATE, the rate he actually produced, and
#   the FIT of that matchup, and ask whether FIT predicts the
#   residual. If it does, a point of FIT is worth a measurable
#   percentage and the projection can carry it. If it does not, that
#   is the single most valuable thing we could learn, because the
#   whole board is built on the assumption that it does.
#
# WHY THIS IS CLEAN RATHER THAN CIRCULAR
#   The zone profile FIT is built from comes entirely from `base` --
#   last completed season. The current season contributes only a DRTG
#   drift figure, which is reported and never enters a zone component.
#   So the matchup score for a game in October 2026 knows nothing
#   about October 2026, and a defence cannot look soft here BECAUSE
#   the player scored on it. That is what makes the measurement worth
#   making at all.
#
#   Two further precautions. Rates, not totals, so a blowout that cut
#   his minutes does not register as a bad matchup. And the projected
#   rate for day D is blended from days strictly before D, so the
#   baseline never contains the game being scored.

FITCAL = os.path.join(DATA, 'fit_calibration.json')
POPCAL = os.path.join(DATA, 'population_bias.json')
BOARDCACHE = os.path.join(DATA, 'board_fits.json')

# flags that turn --board from a filtered card into a full listing:
# every player scored, nothing cut, so the calibration sees the whole
# distribution of FIT rather than only the tails the board features.
PERMISSIVE = ['--json', '--top', '3000', '--min-keep', '0', '--min-fit', '0',
              '--min-poss', '0', '--under-poss-lo', '0',
              '--under-poss-hi', '999', '--max-role', '1.1',
              '--drift-lo', '0', '--drift-hi', '99', '--allow-out']


def board_fits(day, refresh=False):
    """{(player_key, team): fit} for one past day, cached on disk."""
    blob = {}
    if os.path.exists(BOARDCACHE) and not refresh:
        try:
            blob = json.load(open(BOARDCACHE))
        except Exception:
            blob = {}
    if day in blob and not refresh:
        return {tuple(k.split('|')): v for k, v in blob[day].items()}

    cmd = [sys.executable, os.path.join(HERE, 'defense.py'),
           '--board', day, *PERMISSIVE]
    p = subprocess.run(cmd, capture_output=True, text=True)
    got = _json_from(p.stdout) if p.stdout else None
    out = {}
    if got:
        for side in ('overs', 'unders'):
            for r in got.get(side) or []:
                if r.get('fit') is None:
                    continue
                out[f"{norm_player(r.get('player'))}|{code(r.get('team'))}"] \
                    = r['fit']
    blob[day] = out
    try:
        json.dump(blob, open(BOARDCACHE, 'w'))
    except Exception:
        pass
    return {tuple(k.split('|')): v for k, v in out.items()}


def calibrate(market, logs_days, min_prior=2, refresh=False):
    """Measure the FIT -> production relationship, walk-forward."""
    field, _ = MARKETS[market]
    priors = priors_for(market)
    days = sorted(logs_days)
    obs = []
    for i, day in enumerate(days):
        if i < min_prior:
            continue
        hist = player_means(logs_days, field, before=day)
        fits = board_fits(day, refresh=refresh)
        if not fits:
            continue
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            k, t = norm_player(r.get('player')), code(r.get('team'))
            fit = fits.get((k, t))
            if fit is None:
                continue
            prev = hist.get((k, t))
            pr36 = priors.get(k)
            if prev:
                _pg, _mp, g, cur36 = prev
                proj36, _w = blend_rate(cur36, pr36, g)
            elif pr36:
                proj36 = pr36
            else:
                continue
            if proj36 <= 0.5:
                continue
            act36 = (r.get(field) or 0) / m * 36.0
            obs.append((fit, act36 / proj36, day, r.get('player')))

    if len(obs) < 40:
        return {'n': len(obs), 'market': market, 'ok': False}

    xs = [o[0] for o in obs]
    ys = [o[1] for o in obs]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    beta = sxy / sxx if sxx else 0.0
    resid = [y - (my + beta * (x - mx)) for x, y in zip(xs, ys)]
    s2 = (sum(r * r for r in resid) / (len(obs) - 2)) if len(obs) > 2 else 0.0
    se = math.sqrt(s2 / sxx) if sxx > 0 and s2 > 0 else None

    # Buckets as well as a slope, for the reason the margin curve is
    # bucketed: a slope through a relationship that is not linear
    # reports a tidy number that is wrong at both ends, and the ends
    # are where every bet sits.
    qs = statistics.quantiles(xs, n=5) if len(set(xs)) > 5 else []
    buckets = []
    if qs:
        edges = [-9e9] + list(qs) + [9e9]
        for lo, hi in zip(edges, edges[1:]):
            sel = [y for x, y in zip(xs, ys) if lo <= x < hi]
            if not sel:
                continue
            sd = statistics.stdev(sel) if len(sel) > 1 else None
            buckets.append({
                'lo': None if lo < -8e9 else round(lo, 3),
                'hi': None if hi > 8e9 else round(hi, 3),
                'n': len(sel), 'mean': round(statistics.fmean(sel), 4),
                'se': None if not sd else round(sd / math.sqrt(len(sel)), 4),
            })
    return {'market': market, 'ok': True, 'n': len(obs),
            'beta': round(beta, 4),
            'se': None if se is None else round(se, 4),
            'z': None if not se else round(beta / se, 2),
            'mean_ratio': round(my, 4), 'fit_mean': round(mx, 4),
            'buckets': buckets, 'days': len(days) - min_prior}


def show_calibration(c):
    m = c['market']
    print(f'\nFIT CALIBRATION -- {m}')
    if not c.get('ok'):
        print(f'  only {c["n"]} player-games matched. Not enough to measure.')
        return
    print(f"  {c['n']} player-games over {c['days']} days, "
          f"walk-forward")
    print(f"  slope: a +1.00 FIT multiplies the projected rate by "
          f"{1 + c['beta']:.3f}")
    print(f"  beta {c['beta']:+.4f}"
          + (f"  SE {c['se']:.4f}  z {c['z']:+.2f}" if c.get('se') else ''))
    if c['buckets']:
        print(f"\n  {'FIT RANGE':>18}{'N':>6}{'ACT/PROJ':>10}{'SE':>8}")
        for b in c['buckets']:
            lo = '  -inf' if b['lo'] is None else f"{b['lo']:>6.2f}"
            hi = ' +inf' if b['hi'] is None else f"{b['hi']:>6.2f}"
            se = '       -' if b['se'] is None else f"{b['se']:>8.3f}"
            print(f"  {lo} to {hi}{b['n']:>6}{b['mean']:>10.3f}{se}")
    z = c.get('z')
    if z is not None and abs(z) >= 2:
        d = 'MORE' if c['beta'] > 0 else 'LESS'
        print(f"\n  ** FIT PREDICTS. Soft matchups produce {d} than the\n"
              f"     neutral projection, at {abs(z):.1f} SE. The card can "
              f"carry it.")
    else:
        print('\n  FIT does not predict production at this sample. That is\n'
              '  the most important thing on this page: the board ranks by\n'
              '  a number that has not yet been shown to move outcomes, so\n'
              '  it should pick the side and nothing more -- no multiplier\n'
              '  into the mean, however tempting the ranking looks.')
    print('  ACT/PROJ is actual rate over walk-forward projected rate, so\n'
          '  1.000 means the matchup told you nothing. Rates not totals,\n'
          '  so a blowout cutting his minutes is not scored as a bad\n'
          '  matchup.')

if __name__ == '__main__':
    sys.exit(main())
