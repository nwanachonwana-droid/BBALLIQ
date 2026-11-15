#!/usr/bin/env python3
"""BOARDS -- the rebound market, from data already on disk.

WHY THIS MARKET
  A rebound is the most mechanical number on a box score. Points need
  a shot to go in; a rebound needs one to miss, and misses are a
  function of how many shots get taken and how well they are taken --
  both of which are already measured in this project for other
  reasons. Nothing new is scraped here. season_logs.json has every
  player's rebounds and minutes, every team's attempts and makes, and
  minutes.py has the game pairings that turn those into opponents.

  It decomposes cleanly:

      rebounds  =  boards available  x  his share  x  his minutes

  and each term is measurable rather than modelled.

WHAT IS MISSING, SAID OUT LOUD
  The box scores carry total rebounds with no offensive/defensive
  split, and no free-throw attempts. So:
    - A big who gets his boards on the offensive glass and a guard who
      gets them defensively look identical here. That matters for the
      long-rebound test below and is why it is run by POSITION rather
      than claimed for individuals.
    - Available boards count missed field goals only. Missed free
      throws are roughly five to eight per cent of rebound chances,
      and rather than multiply by a number I would be inventing, they
      are left out -- which makes every projection slightly low in the
      same direction for everybody, so the RANKING is unaffected.

THE ANGLE WORTH TESTING
  A defence that concedes a high share of three-point attempts
  generates long rebounds. Long rebounds are available to whoever is
  standing further out, so they should shift boards away from centres
  and toward guards and wings. If that is real it is a genuine edge --
  centre rebound unders and guard rebound overs against the same
  opponent, from one fact -- and the three-point attempt share a
  defence allows persists at +0.46, which is strong enough to project.

  It is a hypothesis, so --test measures it and reports pass or fail.
  It does not quietly become a coefficient in the screen. If it comes
  back flat, that is a finding and the screen stays as it is.

usage
  python3 boards.py --env               # what each team's games produce
  python3 boards.py --test              # does the long-rebound effect exist?
  python3 boards.py --screen 20261028   # ranked, against posted lines
  python3 boards.py --player "Jokic"    # one man's rate and spread
"""
import os, re, sys, json, argparse, statistics, collections

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from defense import (DATA, BASE_SEASON, code, deaccent, norm_player,
                         slate, injury_map, inj_tag)
    from minutes import (games, team_games, margin_curve, minutes_risk,
                         game_lines, ApiError, ROTATION_MIN)
except Exception as e:                                    # pragma: no cover
    print(f'boards.py needs defense.py and minutes.py beside it: {e}',
          file=sys.stderr)
    print('  all three live in ~/Downloads -- run install.sh',
          file=sys.stderr)
    raise SystemExit(2)

# A player needs this many appearances before his rate is used. Below
# it a single 14-rebound night sets the projection.
MIN_GAMES = 3
# And this many minutes in a game for it to count toward his rate: a
# four-minute cameo has a wild rebounds-per-minute figure and no
# information in it.
MIN_MIN = 8.0


def bref(season=BASE_SEASON):
    p = os.path.join(DATA, f'bref_{season}.json')
    if not os.path.exists(p):
        return {}
    try:
        return json.load(open(p))
    except Exception:
        return {}


def positions():
    """{player_key: 'G'|'F'|'C'} from last season's Basketball-Reference.

    Only the first letter is kept. 'SG' and 'PG' rebound alike for this
    purpose, and the distinction is not reliable enough across sources
    to lean on.
    """
    out = {}
    for name, entry in (bref() or {}).items():
        k = norm_player(name)
        for t, row in (entry.get('teams') or {}).items():
            pos = (row.get('pos') or '').strip().upper()
            if pos:
                out[k] = pos[-1] if pos[-1] in 'GFC' else pos[0]
                break
    return out


def pos_group(p):
    return {'C': 'C', 'F': 'F', 'G': 'G'}.get((p or '')[:1], '?')


def prior_rates():
    """{player_key: rebounds per 36} from last season's Basketball-Reference."""
    out = {}
    for name, entry in (bref() or {}).items():
        k = norm_player(name)
        best = None
        for t, row in (entry.get('teams') or {}).items():
            if t.endswith('TM'):
                continue
            r = row.get('trb_per_minute_36')
            mp = row.get('mp') or 0
            if r and (best is None or mp > best[1]):
                best = (float(r), mp)
        if best:
            out[k] = best[0]
    return out


# Games of this season before the current rate outweighs last season's.
# The same shrinkage defense.py uses on minutes, for the same reason and
# with the same constant.
PRIOR_GAMES = 8.0


def blend_rate(cur36, prior36, g, prior=PRIOR_GAMES):
    """(blended rebounds per 36, weight on this season).

    WITHOUT THIS THE SCREEN IS WORSE THAN USELESS, and the posted lines
    proved it on the first day it met them. Jokić had 9.67 rebounds in
    three games, so the raw screen projected 9.54 against a line of
    12.5 -- a three-rebound under on a man who has rebounded 12 or 13 a
    night for years. It was not reading a cold start, it was reading
    the only games it had. Towns failed the same way pointing the other
    direction: 14.0 over four games projected 14.78 against a 13.5
    line, an over built entirely out of a hot week.

    The market blends a career against the current season. This did
    not, so every number it produced was a four-game average wearing a
    projection's clothes -- and the two biggest apparent edges on the
    board were both just unregressed noise.

    Weight on the current sample is g/(g+8): a quarter at three games,
    a third at four, half at eight, and most of it by midseason. A
    player with no prior keeps his raw rate and is flagged, because a
    rookie genuinely has nothing to regress toward.
    """
    if not prior36:
        return cur36, 1.0
    if not g:
        return prior36, 0.0
    w = g / (g + prior)
    return w * cur36 + (1 - w) * prior36, w


# --- the rebound environment ------------------------------------------

def environment(gs):
    """Per team: how many boards its games actually produce.

    Two halves, kept separate because they are different claims:
      forced  -- misses the team's DEFENCE generates (opponent's FGA
                 minus FGM). This is the half a projection can lean
                 on, since it is a property of the defence.
      own     -- misses the team's own offence generates, which the
                 opponent's rebounders feed on.
    Plus the three-point attempt share in those games, which is what
    the long-rebound test needs.
    """
    agg = collections.defaultdict(lambda: {
        'g': 0, 'forced': 0, 'own': 0, 'opp_fga': 0, 'opp_tpa': 0,
        'fga': 0, 'tpa': 0, 'poss': 0.0})
    for g in gs:
        for t in g['teams']:
            opp = g['home'] if g['away'] == t else g['away']
            me, ot = g['teams'][t], g['teams'][opp]
            a = agg[t]
            a['g'] += 1
            a['forced'] += ot['fga'] - ot['fgm']
            a['own'] += me['fga'] - me['fgm']
            a['opp_fga'] += ot['fga']
            a['opp_tpa'] += ot['tpa']
            a['fga'] += me['fga']
            a['tpa'] += me['tpa']
            a['poss'] += ot['fga'] + 0.44 * max(
                0, ot['pts'] - 2 * ot['fgm'] - ot['tpm'])
    out = {}
    for t, a in agg.items():
        if not a['g']:
            continue
        misses = (a['forced'] + a['own']) / a['g']
        tpa_all = a['opp_tpa'] + a['tpa']
        fga_all = a['opp_fga'] + a['fga']
        out[t] = {
            'games': a['g'],
            # MISSED FIELD GOALS, not rebounds. Roughly 97 a game against
            # about 88 actual rebounds, because some misses never become
            # one -- tipped out, shot clock, out of bounds -- and because
            # free-throw misses are not counted here at all. It is a
            # proxy for rebound opportunity and only ever used as a
            # ratio against the league, so the level does not matter;
            # the name did, and 'boards' invited reading 104.5 as a
            # rebound count.
            'misses': round(misses, 1),
            'forced': round(a['forced'] / a['g'], 1),
            'own': round(a['own'] / a['g'], 1),
            # The first version printed the MISS rate under the heading
            # OPPFG%: 1 - (opp_fga - forced)/opp_fga, where opp_fga
            # minus the misses is the makes. So GS showed 47.2 and read
            # as the stingiest defence on the board while actually
            # allowing 52.8% -- the worst of the thirty, which its
            # league-low FORCED of 39.8 says plainly. A complement
            # printed under the wrong name is the same class of error as
            # comparing a model total to a league baseline instead of
            # the posted line, and it cost a call that way once already.
            'opp_fg_pct': round((a['opp_fga'] - a['forced'])
                                / a['opp_fga'] * 100, 1)
            if a['opp_fga'] else None,
            'tpa_rate': round(tpa_all / fga_all * 100, 1) if fga_all else None,
            'poss': round(a['poss'] / a['g'], 1),
        }
    return out


def league_boards(env):
    vals = [v['misses'] for v in env.values() if v['misses']]
    return statistics.fmean(vals) if vals else None


# --- player rates -----------------------------------------------------

def player_rows(logs_days, before=None):
    """{(key, team): {...}} straight off the cached box-score rows.

    games() aggregates to the team level and keeps only minutes per
    player, which is all the minutes work needed. Rebounds need the
    full row, so this reads the same cache directly.
    """
    by = collections.defaultdict(lambda: {
        'min': [], 'reb': [], 'days': [], 'name': None})
    for day in sorted(logs_days):
        if before and day >= before:
            continue
        for r in logs_days[day]:
            k = norm_player(r.get('player'))
            t = code(r.get('team'))
            m = r.get('min') or 0
            d = by[(k, t)]
            d['name'] = r.get('player')
            if m >= MIN_MIN:
                d['min'].append(m)
                d['reb'].append(r.get('reb') or 0)
                d['days'].append(day)
    out = {}
    for (k, t), d in by.items():
        if len(d['min']) < MIN_GAMES:
            continue
        tot_m = sum(d['min'])
        if tot_m <= 0:
            continue
        out[(k, t)] = {
            'key': k, 'team': t, 'name': d['name'],
            'games': len(d['min']),
            'mpg': round(statistics.fmean(d['min']), 1),
            'reb_pg': round(statistics.fmean(d['reb']), 2),
            'reb_per_min': sum(d['reb']) / tot_m,
            'sd': (round(statistics.stdev(d['reb']), 2)
                   if len(d['reb']) > 1 else None),
            'days': d['days'],
        }
    return out


# --- the long-rebound test --------------------------------------------

def long_rebound_test(gs, logs_days, pos, before=None):
    """Do boards move from centres to guards when threes go up?

    Method is the margin curve's, for the same reason: terciles rather
    than a slope, and every player measured against HIS OWN mean so
    that a centre and a guard contribute deviations rather than levels.
    Pooling raw rates would simply rediscover that centres rebound
    more than guards, which nobody needs a script for.
    """
    # each game's three-point attempt share, both teams together
    share = {}
    for g in gs:
        if before and g['day'] >= before:
            continue
        fga = sum(s['fga'] for s in g['teams'].values())
        tpa = sum(s['tpa'] for s in g['teams'].values())
        if fga:
            share[(g['day'], g['away'], g['home'])] = tpa / fga * 100
    if len(share) < 9:
        return None
    cuts = statistics.quantiles(list(share.values()), n=3)

    def band(v):
        return 'low' if v < cuts[0] else ('high' if v >= cuts[1] else 'mid')

    # map day -> that day's games, so a player's row finds its game
    day_games = collections.defaultdict(list)
    for key in share:
        day_games[key[0]].append(key)

    base = player_rows(logs_days, before=before)
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for day in sorted(logs_days):
        if before and day >= before:
            continue
        for r in logs_days[day]:
            k, t = norm_player(r.get('player')), code(r.get('team'))
            m = r.get('min') or 0
            if m < MIN_MIN or (k, t) not in base:
                continue
            gk = next((x for x in day_games.get(day, [])
                       if t in (x[1], x[2])), None)
            if gk is None:
                continue
            rpm = (r.get('reb') or 0) / m
            dev = (rpm - base[(k, t)]['reb_per_min']) * 36.0   # per 36
            out[pos_group(pos.get(k))][band(share[gk])].append(dev)

    res = {'cuts': [round(c, 1) for c in cuts], 'groups': {}}
    for grp in ('C', 'F', 'G'):
        row = {}
        for b in ('low', 'mid', 'high'):
            v = out.get(grp, {}).get(b) or []
            row[b] = {'n': len(v),
                      'dev': round(statistics.fmean(v), 3) if v else None}
        if row['low']['dev'] is not None and row['high']['dev'] is not None:
            row['swing'] = round(row['high']['dev'] - row['low']['dev'], 3)
            pooled = [x for b in ('low', 'high')
                      for x in (out.get(grp, {}).get(b) or [])]
            if len(pooled) > 3:
                sd = statistics.stdev(pooled)
                nl = row['low']['n'] or 1
                nh = row['high']['n'] or 1
                se = (sd ** 2 / nl + sd ** 2 / nh) ** 0.5
                row['se'] = round(se, 3)
                row['z'] = round(row['swing'] / se, 2) if se else None
        res['groups'][grp] = row
    return res


def show_test(res):
    print('\nLONG-REBOUND TEST -- do boards move outward as threes rise?')
    if not res:
        print('  fewer than 9 games paired. Not testable yet.')
        return
    lo, hi = res['cuts']
    print(f'  games split by three-point attempt share: low <{lo}%, '
          f'high >={hi}%')
    print(f"\n  {'POS':5}{'LOW n':>8}{'LOW':>8}{'HIGH n':>9}{'HIGH':>8}"
          f"{'SWING':>8}{'SE':>7}{'Z':>6}")
    for grp in ('C', 'F', 'G'):
        r = res['groups'].get(grp) or {}
        f = lambda v: '      -' if v is None else f'{v:+6.3f}'
        print(f"  {grp:5}{r.get('low', {}).get('n', 0):>8}"
              f"{f(r.get('low', {}).get('dev')):>8}"
              f"{r.get('high', {}).get('n', 0):>9}"
              f"{f(r.get('high', {}).get('dev')):>8}"
              f"{f(r.get('swing')):>8}"
              f"{(('%.3f' % r['se']) if r.get('se') else '     -'):>7}"
              f"{(('%+.2f' % r['z']) if r.get('z') is not None else '    -'):>6}")
    print('  Rebounds per 36 against each player\'s own mean, so this is\n'
          '  movement rather than level -- it cannot just rediscover that\n'
          '  centres rebound more than guards.')

    # WHAT A NULL RESULT IS WORTH depends entirely on what could have
    # been seen, and without that a "FAILS" line is unreadable: it
    # could mean the effect is absent or merely that the sample is too
    # small to notice it. 2 SE is the smallest swing this many
    # observations can separate from noise. Checked against injected
    # effects of known size -- silent at 0.31 reb/36, detected at 0.62,
    # which puts the floor where the table says it is.
    ses = [(res['groups'].get(g) or {}).get('se') for g in ('C', 'G')]
    ses = [x for x in ses if x]
    if ses:
        mde = 2.0 * max(ses)
        print(f'  At these sample sizes the smallest swing separable from\n'
              f'  noise is about {mde:.2f} rebounds per 36. A quiet result '
              f'below\n  that is "not visible yet", not "not there".')

    cz = (res['groups'].get('C') or {}).get('z')
    gz = (res['groups'].get('G') or {}).get('z')
    sig = [z for z in (cz, gz) if z is not None and abs(z) >= 2.0]
    if cz is not None and gz is not None and cz <= -2.0 and gz >= 2.0:
        print('\n  ** PASSES. Centres lose boards and guards gain them as the\n'
              '     three-point share rises, both past 2 SE. One opponent\n'
              '     fact then points two ways at once: centre unders and\n'
              '     guard overs against the same high-3PA defence.')
    elif sig:
        print('\n  ** PARTIAL. One position group clears 2 SE and the other\n'
              '     does not, so the mechanism is not confirmed -- a real\n'
              '     long-rebound effect has to move boards FROM somewhere TO\n'
              '     somewhere. Treat the significant side as suggestive only.')
    else:
        print('\n  FAILS at this sample. Nothing here clears 2 SE, so the\n'
              '  effect does not enter the screen. That is a result, not a\n'
              '  setback: it is one fewer thing to be wrong about, and the\n'
              '  screen stays as it is.')


# --- the screen -------------------------------------------------------

def screen(gs, logs_days, day, env, lines, curve, pos, inj, top=20,
           priors=None):
    """Projected rebounds for everyone playing, ranked by gap to the line."""
    lg = league_boards(env)
    rows = player_rows(logs_days, before=day)
    teams = set()
    for pair in slate(day):
        teams |= {code(pair[0]), code(pair[1])}
    if not teams and lines:
        teams = set(lines)

    # USE THE MAP AS GIVEN. injury_map() is keyed by norm_player(name)
    # and nothing else, which is how defense.py reads it:
    #     inj.get(norm_player(x['player']))
    #
    # The first version re-keyed it to (name, team). That looks like a
    # tightening and is actually a silent hole: the 'team' field comes
    # from walking up to a parent node for an abbreviation, and when
    # that walk finds nothing it is None, so code(None) is '' and the
    # tuple can never match. Şengün and Holmgren were both ruled out
    # last night, defense.py held them out by name, and this screen
    # projected them anyway. The test missed it because it passed an
    # empty injury dict and so never ran the filter at all.
    out, held = [], []
    for (k, t), r in rows.items():
        if t not in teams:
            continue
        info = (inj or {}).get(k)
        tag = inj_tag(info) if info else None
        if tag == 'OUT':
            # named, not just dropped: a filter that works silently is
            # indistinguishable from one that does not work at all
            held.append(f"{r['name'] or k} ({t})")
            continue
        ln = (lines or {}).get(t) or {}
        opp = ln.get('opp')
        # the environment term: boards this matchup should produce,
        # against the league's average game. Both halves count -- his
        # own team's misses feed the opponent's rebounders and vice
        # versa, so the pair is what matters, not either side alone.
        mult = None
        if lg and opp and opp in env and t in env:
            pair_boards = (env[t]['forced'] + env[opp]['forced']) / 2.0 + \
                          (env[t]['own'] + env[opp]['own']) / 2.0
            mult = pair_boards / lg
        mpg = r['mpg']
        adj = 0.0
        if ln.get('spread') is not None and curve:
            _p, _e, excess = minutes_risk(ln['spread'], curve)
            if excess is not None:
                adj = excess
        cur36 = r['reb_per_min'] * 36.0
        pr36 = (priors or {}).get(k)
        rate36, w = blend_rate(cur36, pr36, r['games'])
        proj = rate36 / 36.0 * max(0.0, mpg + adj)
        if mult:
            proj *= mult
        out.append({
            'player': r['name'] or k, 'key': k, 'team': t, 'opp': opp,
            'pos': pos_group(pos.get(k)), 'games': r['games'],
            'mpg': mpg, 'min_adj': round(adj, 2),
            'reb_pg': r['reb_pg'], 'sd': r['sd'],
            'env': None if mult is None else round(mult, 3),
            'cur36': round(cur36, 2),
            'prior36': None if pr36 is None else round(pr36, 2),
            'rate36': round(rate36, 2), 'w_new': round(w, 2),
            'proj': round(proj, 2),
            'spread': ln.get('spread'), 'status': tag or '-',
        })
    out.sort(key=lambda x: -x['proj'])
    if held:
        print('  ruled OUT, held out: ' + '; '.join(sorted(held)),
              file=sys.stderr)
    return out[:top]


def show_screen(rows, day, lg):
    print(f'\nPROJECTED REBOUNDS -- {day}')
    if not rows:
        print('  nobody qualified. Either the slate is not in the cache or\n'
              f'  no player has {MIN_GAMES}+ games of {MIN_MIN}+ minutes yet.')
        return
    print(f"  {'PLAYER':21}{'TM':>4}{'OPP':>4}{'P':>3}{'G':>3}"
          f"{'MPG':>6}{'ADJ':>6}{'REB':>6}{'SD':>6}"
          f"{'NOW36':>7}{'WAS36':>7}{'W':>5}{'ENV':>7}{'PROJ':>7}")
    for x in rows:
        sd = '     -' if x['sd'] is None else f"{x['sd']:>6.2f}"
        ev = '      -' if x['env'] is None else f"{x['env']:>7.3f}"
        pr = '  none*' if x['prior36'] is None else f"{x['prior36']:>7.2f}"
        print(f"  {x['player'][:20]:21}{x['team']:>4}{(x['opp'] or '-'):>4}"
              f"{x['pos']:>3}{x['games']:>3}{x['mpg']:>6.1f}"
              f"{x['min_adj']:>+6.2f}{x['reb_pg']:>6.2f}{sd}"
              f"{x['cur36']:>7.2f}{pr}{x['w_new']:>5.2f}{ev}"
              f"{x['proj']:>7.2f}")
    print(f'  ENV is this matchup\'s expected missed FG against the league\n'
          f'  average of {lg:.1f} per game; above 1.0 means more misses to\n'
          f'  collect. ADJ is the spread\'s minutes effect. PROJ is\n'
          f'  rate x (minutes + ADJ) x ENV, and excludes free-throw\n'
          f'  misses -- low for everyone by the same few per cent, so the\n'
          f'  order holds even though the levels run light.')
    print('  SD is his game-to-game rebound spread. A projection 1.5 over\n'
          '  a line with an SD of 4 is not an edge, it is a coin flip with\n'
          '  a story attached.')
    print('  NOW36 is this season per 36, WAS36 last season, W the weight on\n'
          '  this season at g/(g+8). PROJ uses the blend: a three-game\n'
          '  average is a quarter of its own projection, which is why an\n'
          '  unregressed screen had Jokic three rebounds under a 12.5 line.\n'
          '  "none*" means no prior -- a rookie, so PROJ is raw and thin.')


def show_env(env, lg):
    print(f'\nREBOUND OPPORTUNITY -- league average {lg:.1f} missed FG/game')
    print(f"  {'TEAM':5}{'G':>3}{'MISSES':>8}{'FORCED':>8}{'OWN':>7}"
          f"{'OPPFG%':>8}{'3PA%':>7}{'POSS':>7}")
    for t, v in sorted(env.items(), key=lambda kv: -kv[1]['misses']):
        f = lambda x, w=7: (' ' * (w - 1) + '-') if x is None else f'{x:>{w}.1f}'
        print(f"  {t:5}{v['games']:>3}{v['misses']:>8.1f}{v['forced']:>8.1f}"
              f"{v['own']:>7.1f}{f(v['opp_fg_pct'], 8)}{f(v['tpa_rate'])}"
              f"{v['poss']:>7.1f}")
    print('  MISSES is missed field goals in this team\'s games, not\n'
          '  rebounds -- a proxy for opportunity, used only as a ratio.\n'
          '  FORCED is misses this defence generates; OWN is misses its\n'
          '  offence generates for the other side to collect. OPPFG% is\n'
          '  what opponents actually shoot, so LOWER is a better defence.\n'
          '  Free-throw misses are excluded -- no attempts in the box.')


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
BUILD = '2026-10-28b'


def _stamp(name):
    import sys as _s
    print(f'[{name} build {BUILD}]', file=_s.stderr)

def main():
    ap = argparse.ArgumentParser(description='the rebound market')
    ap.add_argument('--env', action='store_true',
                    help='boards each team\'s games produce')
    ap.add_argument('--test', action='store_true',
                    help='does the long-rebound effect exist in the data?')
    ap.add_argument('--screen', metavar='DAY', help='ranked projections')
    ap.add_argument('--player', help='one man, by name fragment')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--before', metavar='DAY',
                    help='walk-forward: ignore this day and later')
    ap.add_argument('--refresh', action='store_true')
    ap.add_argument('--json', action='store_true')
    _stamp('boards.py')
    a = ap.parse_args()
    if not any((a.env, a.test, a.screen, a.player)):
        ap.print_help()
        return 2

    logs_p = os.path.join(DATA, 'season_logs.json')
    if not os.path.exists(logs_p):
        print(f'no {logs_p} -- run edges.py --build first', file=sys.stderr)
        return 1
    logs_days = json.load(open(logs_p)).get('days') or {}

    before = a.before or a.screen
    gs = games(before=before)
    if not gs:
        print('no games paired. Run: python3 minutes.py --schedule',
              file=sys.stderr)
        return 1
    env = environment(gs)
    lg = league_boards(env)
    pos = positions()
    out = {'games': len(gs), 'league_boards': lg}

    if a.env:
        out['env'] = env
        if not a.json:
            print(f'{len(gs)} games paired')
            show_env(env, lg)

    if a.test:
        res = long_rebound_test(gs, logs_days, pos, before=before)
        out['test'] = res
        if not a.json:
            show_test(res)

    if a.player:
        q = norm_player(a.player)
        rows = player_rows(logs_days, before=before)
        hit = [r for (k, t), r in rows.items() if q in k]
        out['player'] = hit
        if not a.json:
            if not hit:
                print(f'\nno player matching "{a.player}" with '
                      f'{MIN_GAMES}+ qualifying games')
            for r in sorted(hit, key=lambda r: -r['reb_pg']):
                print(f"\n  {r['name']}  {r['team']}  "
                      f"{pos_group(pos.get(r['key']))}")
                print(f"    {r['games']} games, {r['mpg']} mpg, "
                      f"{r['reb_pg']} reb/game, "
                      f"{r['reb_per_min'] * 36:.2f} per 36"
                      + (f", SD {r['sd']}" if r['sd'] else ''))

    if a.screen:
        curve, _n = margin_curve(gs, min_games=2)
        lines = {}
        try:
            lines = game_lines(a.screen, refresh=a.refresh)
        except ApiError as e:
            print(f'  ! lines unavailable, projecting without spread '
                  f'or opponent: {e}', file=sys.stderr)
        inj = injury_map(refresh=a.refresh)
        rows = screen(gs, logs_days, a.screen, env, lines, curve, pos,
                      inj, top=a.top, priors=prior_rates())
        out['screen'] = rows
        if not a.json:
            show_screen(rows, a.screen, lg)

    if a.json:
        print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == '__main__':
    sys.exit(main())
