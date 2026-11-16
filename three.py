#!/usr/bin/env python3
"""THREE -- a separate matchup ranking for points, rebounds and assists,
and the players who appear on all three.

NO PRICE IS CONSULTED ANYWHERE IN THIS FILE. That is not an oversight,
it is the design. This ranks matchups. Whether a matchup is worth
betting is a later question that needs a line; this is the question
before it, and mixing the two is how a board turns into a card that
fades everything.

WHY THIS EXISTS: ONE MATCHUP MODEL WAS DOING THREE JOBS
  defense.py computes FIT -- how soft a defence is in the zones a
  player's shots actually come from, weighted rim / 3-10 / midrange /
  three by how much each persists year to year. It is a good model and
  it is a SCORING model. It knows nothing about rebounds or assists.

  But FIT was the only matchup number in the system, so card.py used
  the same figure to rank all four markets. That is why a rebounds row
  on 29 Oct carried the reason "NO is soft from rim (+0.52)" -- the
  points model, talking about rebounds. A defence that concedes at the
  rim is being used to argue about boards, and the two are not even
  the same sign: a defence that lets everything drop through generates
  FEWER defensive rebounds for the team playing it.

  So each category gets the model its own causal chain deserves.

THE THREE MODELS

  POINTS -- reuse FIT. Zone-weighted defensive softness against the
    player's own shot diet. Already the right model; nothing to add.

  REBOUNDS -- rebound opportunity. The chain is: shots go up, some
    miss, misses become available boards, the player's rate and
    minutes decide his share, the opponent's rebounders contest it.
    boards.py's environment() already measures the first half per
    team (`misses`, and `forced` as the defence's own half). The
    second half is the opponent's rebounding strength, which suppresses
    your share of what is available. Both are in the cache.

  ASSISTS -- assist opportunity. The chain is longer and it is the
    reason this category had nothing: an assist needs a possession, a
    pass, AND A MADE SHOT. So it depends on how well the opponent's
    defence lets the OTHER team shoot, which is a different property
    from how many points it concedes. A defence that forces bad shots
    that still go in suppresses nothing; one that forces misses kills
    assists even at a decent points total.

    The empirical shortcut is the honest one here: assists allowed per
    100 possessions, measured directly. It captures scheme, switching,
    shot quality conceded and rim protection at once, without my
    having to decompose a chain I cannot observe. It needed team-level
    assists, which minutes.py did not aggregate until today -- that
    absence, not the difficulty, is why no assist model existed.

RANKING: LIFT, NOT LEVEL
  A star projects high in every matchup, so ranking by projected total
  returns the same names nightly and says nothing about tonight. Each
  board therefore ranks by LIFT: how many points / rebounds / assists
  this environment is worth to him ABOVE his own baseline. The
  multiplier says how soft the spot is; the lift says whether the spot
  is soft enough to matter to someone who gets enough of the stat for
  it to show up.

PRA
  Not a separately modelled market. A PRA candidate is a player whose
  matchup is favourable in all three categories at once, so the same
  opponent is helping him three ways -- which is exactly when a
  combined line is easier to clear than any of its parts. The
  intersection is the model.

usage
  python3 three.py 20261030              # all three boards + the overlap
  python3 three.py 20261030 --top 12
  python3 three.py 20261030 --json
"""
import os, re, sys, json, math, argparse, subprocess, statistics
import collections, datetime, io

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
try:
    from defense import DATA, code, norm_player, slate, injury_map
    from minutes import games, game_lines, ApiError
    from boards import environment, prior_rates, blend_rate, PRIOR_GAMES, bref
except Exception as e:                                    # pragma: no cover
    print(f'three.py needs defense.py, minutes.py and boards.py beside '
          f'it: {e}', file=sys.stderr)
    raise SystemExit(2)

BUILD = '2026-11-16f'
LOGS = os.path.join(DATA, 'season_logs.json')
BOARDLOG = os.path.join(DATA, 'boards.jsonl')
PRICES = os.path.join(DATA, 'external_picks.jsonl')
SLATE = os.path.join(DATA, 'slate.jsonl')
MIN_MIN = 8.0
MIN_GAMES = 2          # of this season, before a player is rankable
MIN_BASE = {'pts': 6.0, 'threes': 1.0, 'reb': 2.5, 'ast': 1.5}

# ONE DECAY FOR FOUR STATS WAS A SIMPLIFICATION WE HAD ALREADY
# MEASURED OUR WAY OUT OF.
#
# card.py --fit-shrink found the RMSE-optimal prior weight per
# market -- pts 12, reb 8, ast 6, threes 16 -- and the board went on
# using 8 for everything because that is what blend_rate defaults
# to. The differences are real and in the direction the literature
# expects: efficiency-driven stats (threes) need a longer memory,
# role-driven ones (assists) a shorter one.
PRIOR_K = {'pts': 12.0, 'reb': 8.0, 'ast': 6.0, 'threes': 16.0}

# A three-point percentage over four or five games is close to
# meaningless -- twenty-odd attempts, and the binomial SE on 20
# attempts at 36% is 11 points. So it is shrunk hard toward the
# league. LEAGUE_3P is where it shrinks to; PCT_PRIOR_ATT is how many
# league-average attempts the prior is worth.
#
# PCT_PRIOR_ATT = 75 is a standard empirical-Bayes weight for this
# statistic and is NOT measured here, so it carries the same warning
# as LIFT_K: a defensible order of magnitude, not a finding. At 25
# actual attempts it leaves a player a quarter of the way from the
# league mean to his own rate, which is about right for a number this
# noisy this early.
LEAGUE_3P = 0.360
PCT_PRIOR_ATT = 75.0

# THREE KINDS OF FILTER, AND ONLY ONE OF THEM SHOULD BE OFF
#
# defense.py's flags got treated as one switch and flipped together,
# first all on (the points pool came back 11 deep against hundreds for
# the other two, so the PRA intersection was really just asking who
# survived that filter) and then all off (Gary Payton II at 13.6
# minutes topping a points board). Both were wrong because the flags
# are not one kind of thing:
#
#   BETTING filters -- the |FIT| threshold, the under-possession
#     bands. These say "this matchup is not strong enough to act on".
#     They must be OFF here. The ranking is the thing that decides
#     strength, so gating the input on it is circular, and it also
#     truncates the distribution the slate z-score is computed over.
#
#   VOLUME floors -- minutes, and possessions the player personally
#     finishes. These say "he does not do enough of this for the
#     number to mean anything". They must stay ON. A six-minute
#     specialist walking into a perfect matchup is noise, not a lean,
#     and no amount of matchup quality fixes a sample of one shot.
#
#   DATA-QUALITY filters -- KEEP, how much of last season's rotation
#     the defence still has. This says "the matchup read itself
#     describes a roster that has gone". It belongs ON for points,
#     where the zone profile comes from last season, and is
#     irrelevant for rebounds and assists, whose environments are
#     measured from this season's games directly.
#
# So the volume floors live HERE, applied identically to all three
# boards, rather than being delegated to defense.py -- where they
# would reach the points board and silently miss the other two.
MIN_MPG = 18.0          # rotation floor, all three boards
MIN_POSS = 9.0          # possessions he finishes -- points board only
MIN_KEEP = 5            # of the defence's top 8 -- points board only

# WHICH SEASON IS EACH BOARD ACTUALLY LOOKING AT?
#
#   POINTS and THREES: the zone profile -- rim, 3-10, midrange, three
#     -- is entirely LAST season. Verified in defense.py: every zone
#     component is read off `base[t]`, and the only thing this season
#     contributes to the defensive table is drtg_raw.
#
#   REBOUNDS and ASSISTS: entirely THIS season, four or five games,
#     which is why they measured 32% and 15% real and get shrunk.
#
# Neither blends. So the two boards with the big sample are also the
# two that cannot see a defence that has changed since April, and the
# two that can see this season have almost no sample to see it with.
# That is the honest shape of the thing today and it is worth having
# in front of you rather than buried.
#
# DRIFT is the bridge. defense.py already computes it -- this
# season's raw defensive rating minus last season's -- and now sends
# it along on every row. A positive drift means the defence is giving
# up MORE than last year, which supports an over; negative means they
# have tightened and last season's softness is stale.
#
# It is REPORTED, not folded in. Four games of defensive rating is
# the same thin sample that reduced the rebound board to +0.58, and
# correcting a full-season zone profile with it would be importing
# the noise we just spent the morning shrinking out. MAX_DRIFT only
# withholds a row when this season flatly contradicts last season's
# read; everything else is shown with the number attached.
MAX_DRIFT = 99.0        # points/threes only; 0 disables the check

# A FLOOR UNDER THE SHRINKAGE, FOR A REASON THAT IS NOT OPTIMISM
#
# The reliability estimate carries an error bar wider than itself at
# this sample size (see reliability()). Letting it drive the
# shrinkage unfiltered meant the rebound board was scaled by 0.32 one
# night and 0.16 the next -- a halving caused by estimator noise, not
# by anything in the league -- which makes two consecutive nights
# incomparable and makes the logged rows harder to calibrate later,
# since the same matchup logs a different z depending on when it was
# run.
#
# n/(n+k) with k = 20 is the same stable form the zone blend uses. It
# has no sampling noise in it at all: it depends only on games
# played, so it can only rise. The shrinkage is the LARGER of that
# and the measured figure, so a measurement that collapses to 4%
# overnight cannot drag the board with it, while a measurement that
# genuinely clears the floor is still free to.
REL_FLOOR_PRIOR = 20.0


def rel_floor(n_games):
    """The least shrinkage a sample of this size can justify."""
    if not n_games:
        return 0.0
    return n_games / (n_games + REL_FLOOR_PRIOR)
CATS = ('pts', 'threes', 'reb', 'ast')   # the boards; anything else
                                         # in the dict is metadata

# PRA IS P + R + A. IT IS NOT "ALL THE BOARDS".
#
# overlap() used CATS for both meanings -- the set of boards to draw
# from, and the set of legs a PRA candidate must appear on. Adding
# threes to CATS silently changed the second one too, so the
# intersection was still computed over points/rebounds/assists while
# the lookup that followed demanded a row on all FOUR boards. A player
# in the PRA intersection who was not also a three-point shooter hit
# next() with nothing to return and the whole run died on
# StopIteration.
#
# Two different ideas had been sharing one name. They now have two.
PRA_CATS = ('pts', 'reb', 'ast')

# WHY THREES SITS NEXT TO POINTS AND NOT NEXT TO REBOUNDS
#
# The reliability measurement split these four boards in half, and
# the line is not where the categories are -- it is where the DATA
# comes from.
#
#   Points and threes ride on FIT, whose zone profile is built from
#   the last COMPLETED season. A full season of shot location against
#   every defence. The current season contributes only a drift figure.
#   So FIT never had the three-to-five-game problem; it is not a
#   small-sample statistic at all, which is why it survived the
#   shrinkage untouched while the other two collapsed.
#
#   Rebounds and assists ride on team environments measured from THIS
#   season -- four games -- and came back 21% and 15% real.
#
# So: points and threes are the two categories where the matchup read
# has a real sample behind it today. That is the whole answer to "why
# just points".
#
# THREES IS MODELLED ON ATTEMPTS, NOT MAKES
#   A three-point line is crossed by taking more of them, not by
#   shooting a better percentage. Attempts respond to pace and to a
#   defence that does not run you off the line; the percentage is
#   close to a coin from game to game, and chasing it is how a model
#   turns noise into confidence. So the matchup moves 3PA, and the
#   makes follow from his own rate shrunk toward the league.

# ONE CLAIM, PRINTED FIFTEEN TIMES, IS STILL ONE CLAIM
#
# On 30 Oct the points board returned five Pacers and two Heat: seven
# of fifteen rows from a single game, every one of them saying "MIA is
# soft from three and IND shoots threes". That is a real read. It is
# ALSO one read, and a list that repeats it seven times looks like
# seven independent findings and is not.
#
# The rebound and assist boards are worse, because their environments
# are team-level by construction -- every Knick facing PHI carries an
# identical z, so a board with no cap is a ranking of GAMES with each
# roster's volume leaders stapled underneath.
#
# Capping rows per game turns the board back into a survey of the
# slate: the best matchup in each game, then the next best, rather
# than one game's roster in batting order. It also unclogs the PRA
# intersection, which was otherwise dominated by whichever game
# happened to flood all three boards at once.
PER_GAME = 2

# LIFT = base * LIFT_K * z.
#
# LIFT_K IS A PLACEHOLDER AND IS NOT MEASURED. It says a one-sigma
# environment is worth about 6% of a player's baseline, which is a
# guess of the right order and nothing more. It is here because a
# ranking needs to separate players inside the same team -- they all
# face the same defence, so they all carry the same z, and without a
# volume term a twelve-rebound centre and a three-rebound guard tie.
#
# Calibrating it is the same job as calibrating FIT: log the boards,
# settle them against the box score, regress actual-over-baseline on
# z. Until that is done, read LIFT as an ordering within a team and
# not as a number of rebounds. The z is the measured part.
LIFT_K = 0.06

PRIOR36 = {'pts': 'pts_per_minute_36', 'ast': 'ast_per_minute_36',
           'threes': 'fg3_per_minute_36'}


def _stamp():
    print(f'[three.py build {BUILD}]', file=sys.stderr)


# --- the per-team environments ---------------------------------------

# --- the market's own view of tonight's environment ------------------
#
# WHY THIS REPLACES THE ZONE READ AS THE PRIMARY TERM
#
#   FIT measured null across five markets at 959 player-games. The
#   research puts a realistic opponent effect at 1-3% of a player's
#   mean per SD, against single-game noise with a CV near 0.35-0.6,
#   and says 959 games could only have detected 3-4%. So the null
#   rules out a large effect and is silent on a small one -- which
#   means building the board around it was always going to produce a
#   ranking nobody could verify.
#
#   Meanwhile the implied team total does in one number what the zone
#   read attempts from six games of shooting percentages: it is the
#   deepest market's estimate of how much this team scores tonight,
#   and it already contains the opponent's defence, the pace, the
#   injuries and the rest. It is not a six-game sample of anything.
#
#   THIS IS NOT AN EDGE AND MUST NOT BE PRESENTED AS ONE. Using the
#   market's environment estimate is using the best available
#   estimate. The claim a board row makes is "this is a good spot for
#   production tonight", which is a different claim from "this is
#   mispriced" -- and the board has never made the second one.
#
#   POINTS, ASSISTS and THREES scale with the team's own implied
#   total: an offence the market expects to score 12% more than usual
#   is an offence whose shots, assists and threes all rise.
#   REBOUNDS scale with PACE instead, proxied by the game total,
#   because boards come from possessions rather than from points.
# Teams on the slate below which the opponent-only boards
# (rebounds, assists) stop meaning anything. z is standardised across
# the slate, and those boards have exactly one value per DEFENCE, so
# the sample standard deviation is taken over this many points.
# Simulated with identical teams: 67% of 4-team slates throw a top z
# above +1.00, and the arithmetic maximum at n=4 is +1.50.
THIN_SLATE_TEAMS = 6
THIN_SLATE_WARN = 10

LEAGUE_TOTAL = 230.0      # fallback if the slate's own mean is unusable

# --- the minutes term -------------------------------------------------
#
# What counts as a blowout, in final margin. 20 is the common
# convention and the rotation effect is continuous rather than
# switching on at a threshold, so --blowout reports several cuts and
# this is the one the model uses.
BLOWOUT_MARGIN = 20.0

# SD of final margin around the closing spread. ASSUMED, not measured:
# this cache holds final margins but not the spreads they were priced
# at, so it cannot be checked here yet. Every board row now logs its
# spread, so --blowout will measure it once a few weeks have accrued.
# Treat it like LIFT_K and ZONE_PRIOR_GAMES -- a defensible figure,
# not a finding.
MARGIN_SD = 13.0

# Minutes lost to a blowout, by minutes tier. DELIBERATELY EMPTY.
# The model reads data/blowout.json; with no file the minutes term is
# OFF and every run says so. The one public figure for this (six
# minutes) is illustrative in its own source, and a borrowed
# placeholder sitting inside every projection is exactly the failure
# this file has twice shipped -- a term that looks like a measurement.
BLOWOUT = os.path.join(DATA, 'blowout.json')
SCHED = os.path.join(DATA, 'sched_cache.json')

# Minutes floors for the SLIP, which are not the board's floors.
# Two independent reasons to sit well above 18:
#   - the CV of per-minute production correlates with minutes per game
#     at r = -0.75 (Columbia, 92 players): low-minute players are far
#     noisier per minute, not just fewer minutes of the same noise.
#   - hold. Star and main-stat props run 4-6%; secondary and bench
#     props run 6-10%, so a secondary prop needs roughly 53-55% rather
#     than 52.4% to break even. Our 18-minute floor was surfacing
#     19-to-24-minute players, which is the worst of both.
PICK_MIN_MPG = 28.0

# GO-TO scoring. The hand discipline from 04-06 Nov, written down so
# the list can be regenerated instead of living in a conversation.
# THESE WEIGHTS ARE A HEURISTIC, calibrated to nothing -- LIFT_K and
# ZONE_PRIOR_GAMES again. They are named, in one place, and printed
# with the output, which is the only claim being made for them.
GOTO_MAX = 5
GOTO_W_BLOWOUT = 0.50   # per unit of P(|final margin| >= 20)

# Used INSTEAD of GOTO_W_BLOWOUT once the minutes haircut is live, so
# the same risk is not charged twice -- once in the projection and
# again in the score. It stands for what the haircut cannot see: shot
# diet collapsing in a rout (Miller, 0-for-3 while CHA shot 11-for-50)
# and boards redistributing to reserves (Towns, 30 to four reserves).
# THAT RESIDUAL IS REAL AND ITS SIZE IS NOT MEASURED. Smaller than the
# full dock only because the minutes part has been carved out, not
# because anything was calibrated.
GOTO_W_BLOWOUT_RESIDUAL = 0.15
GOTO_W_SHARE = 0.10     # a rebounds row standing on share, not volume
GOTO_W_THIN_SD = 0.05   # his own sd rests on fewer than GOTO_SD_N games
GOTO_SD_N = 8
TIER_MPG = ((32.0, 'star'), (26.0, 'main'), (0.0, 'secondary'))

# --- the probability layer --------------------------------------------
#
# Above this projected mean, a Normal with a continuity correction
# replaces the count distribution: discreteness stops mattering and
# the negative-binomial fit gets unstable on a handful of games.
NORMAL_MIN = 8.0

# Games of a player's own variance needed before it outweighs the
# league mean-to-SD curve. Same shrinkage form as every other rate
# here, w = n/(n+k). Four games of a variance is a worse estimate
# than four games of a mean, so this sits above PRIOR_K.
SD_PRIOR_GAMES = 10.0

# Hold by prop tier, from the published ranges: major/star props run
# 4-6%, secondary and bench 6-10%. Converted to the win rate a bet
# must clear. These are MARKET STRUCTURE, not our estimate -- the
# point of carrying them is that the same probability is a bet on a
# star and not a bet on a 20-minute reserve.
TIER_HOLD = {'star': 0.052, 'main': 0.055, 'secondary': 0.075}

# How far past break-even a side must sit before the slip takes it.
# Not a confidence level -- a buffer against our own projection error,
# which on 01 Nov ran 11.6% low and on 02 Nov 47% high.
EDGE_BUFFER = 0.030


def inj_age_hours():
    """How old is the injury cache? A stale file is worse than none.

    injury_map() serves from disk inside a TTL and refetches after
    it, and nothing printed the age -- so a board built from an
    eight-hour-old file looked exactly like one built from a fresh
    fetch. The entire point of checking status before a slate is
    that it changed this morning.
    """
    import time as _t
    p = os.path.join(DATA, 'injuries.json')
    if not os.path.exists(p):
        return None
    try:
        blob = json.load(open(p))
        fetched = blob.get('fetched')
        return None if not fetched else (_t.time() - float(fetched)) / 3600.0
    except Exception:
        return None


def stat_spread(logs_days, before=None):
    """{(player, team, market): (mean, sd, n)} from the cache.

    The player's own game-to-game spread, which is the input every
    public treatment of this assumes instead of measuring. Volatility
    is not close to constant across players -- the published range of
    the CV of per-minute production is 0.196 to 1.611 -- so one
    assumed variance would price the steadiest and the streakiest
    identically.
    """
    field = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
    vals = collections.defaultdict(list)
    for d in sorted(logs_days or {}):
        if before and d >= before:
            continue
        for r in logs_days[d]:
            key = (norm_player(r.get('player')), code(r.get('team')))
            for mk, f in field.items():
                v = r.get(f)
                if v is not None:
                    vals[key + (mk,)].append(float(v))
    out = {}
    for k, vs in vals.items():
        if len(vs) < 2:
            continue
        out[k] = (statistics.fmean(vs), statistics.pstdev(vs), len(vs))
    return out


def sd_curve(spread):
    """League mean -> SD, per market, as the shrinkage prior.

    Fitted as sd = c * sqrt(mean), which is the Poisson shape, with c
    measured per market. c near 1 means Poisson-like; c above 1 is
    overdispersion, which is what counting stats with variable minutes
    actually look like.
    """
    bym = collections.defaultdict(list)
    for (p, t, mk), (mu, sd, n) in spread.items():
        if mu > 0.5 and n >= 3:
            bym[mk].append(sd / math.sqrt(mu))
    return {mk: statistics.median(v) for mk, v in bym.items() if v}


def blended_sd(mu, mk, spread, curve, key=None):
    """His own SD shrunk toward the league curve. (sd, weight, n)."""
    c = curve.get(mk) or 1.0
    prior = c * math.sqrt(max(mu, 0.1))
    got = spread.get((key or ('', '')) + (mk,)) if key else None
    if not got:
        # SAME FACTOR ON THE PRIOR-ONLY PATH. Missing it here would
        # leave every player with no own history priced on an
        # uninflated SD -- a silent split in the pricing that would
        # show up as a calibration gap nobody could place.
        return prior * sd_inflation().get(mk, 1.0), 0.0, 0
    _mu_own, sd_own, n = got
    w = n / (n + SD_PRIOR_GAMES)
    sd = (1 - w) * prior + w * sd_own
    # MEASURED INFLATION, applied at the one point the SD is made so
    # nothing downstream can miss it. 1.0 until --fit-sd --apply.
    return sd * sd_inflation().get(mk, 1.0), round(w, 3), n


def _nb_over(mu, var, line):
    """P(X > line) for a negative binomial matched on mean and var."""
    if var <= mu:
        return None
    p = mu / var                      # 0 < p < 1
    rr = mu * p / (1.0 - p)
    k = int(math.floor(line))
    # P(X = i) recursively, stable for the small i we need
    lg = math.lgamma
    cdf = 0.0
    for i in range(k + 1):
        logp = (lg(rr + i) - lg(rr) - lg(i + 1)
                + rr * math.log(p) + i * math.log1p(-p))
        cdf += math.exp(logp)
    return max(0.0, min(1.0, 1.0 - cdf))


def _pois_over(mu, line):
    k = int(math.floor(line))
    cdf = 0.0
    term = math.exp(-mu)
    for i in range(k + 1):
        if i:
            term *= mu / i
        cdf += term
    return max(0.0, min(1.0, 1.0 - cdf))


def p_over(mu, line, sd=None, mk=None):
    """P(stat > line), and which distribution said so.

    The distribution is chosen by the data, not by preference:
      mean >= NORMAL_MIN   -> Normal, continuity-corrected. Points is
                              a sum of 1s, 2s and 3s and is not
                              Poisson in the stat itself.
      var  >  mean         -> Negative Binomial on (mean, var).
      otherwise            -> Poisson.
    """
    if mu is None or line is None:
        return None, None
    var = None if sd is None else sd * sd
    if mu >= NORMAL_MIN:
        s_ = sd if sd and sd > 0 else math.sqrt(max(mu, 0.1))
        z = (line + 0.5 - mu) / s_
        return (0.5 * math.erfc(z / math.sqrt(2.0))), 'normal'
    if var is not None and var > mu:
        v = _nb_over(mu, var, line)
        if v is not None:
            return v, 'negbin'
    return _pois_over(mu, line), 'poisson'


def side_call(mu, line, sd, mk, tier, buffer=None):
    """Which side, how likely, and whether it clears that tier's hold.

    Returns a dict, or None without a line. Deliberately NOT a side
    preference: both sides are computed and the better one is offered,
    so the 46-48% over skew shows up in which side keeps winning
    rather than being assumed.
    """
    if line is None:
        return None
    buffer = EDGE_BUFFER if buffer is None else buffer
    po, dist = p_over(mu, line, sd, mk)
    if po is None:
        return None
    pu = 1.0 - po
    side, p = ('over', po) if po >= pu else ('under', pu)
    need = 0.5 + (TIER_HOLD.get(tier, 0.075) / 2.0)
    return {'side': side, 'p': round(p, 4), 'p_over': round(po, 4),
            'dist': dist, 'tier': tier,
            'need': round(need, 4), 'buffer': buffer,
            'clears': bool(p >= need + buffer),
            'margin': round(p - need, 4)}


def mpg_tier(mpg):
    """star / main / secondary, for hold and for per-minute noise."""
    for lo, name in TIER_MPG:
        if (mpg or 0) >= lo:
            return name
    return 'secondary'


def blowout_risk(spread, sd=None, margin=None):
    """P(the game finishes outside +/- margin), given the spread.

    The team's expected margin is -spread (a -16 favourite expects to
    win by 16). Final margin is taken as normal around it. Either
    direction counts: the winner rests his stars and so does the
    loser, so a blowout costs minutes on both benches.

    Returns None when the spread is unknown, NOT 0.0 -- an unknown
    spread must not read as a safe game. Every caller treats None as
    "no haircut and say so" rather than as zero risk.
    """
    if spread is None:
        return None
    sd = MARGIN_SD if sd is None else sd
    margin = BLOWOUT_MARGIN if margin is None else margin
    if sd <= 0:
        return None
    mu = -float(spread)
    # P(X >= margin) + P(X <= -margin) for X ~ N(mu, sd^2)
    hi = 0.5 * math.erfc((margin - mu) / (sd * math.sqrt(2.0)))
    lo = 0.5 * math.erfc((margin + mu) / (sd * math.sqrt(2.0)))
    return round(min(1.0, hi + lo), 4)


def load_blowout(path=None):
    """Measured minute loss by tier, or None when never measured.

    None is the honest state and the callers announce it. A default
    here would be a guess wearing a measurement's clothes.
    """
    path = path or BLOWOUT
    if not os.path.exists(path):
        return None
    try:
        d = json.load(io.open(path, encoding='utf-8'))
    except Exception as e:
        print(f'  !! blowout.json unreadable ({e}) -- minutes term OFF')
        return None
    loss = d.get('loss') or {}
    if not loss:
        return None
    return {'loss': {k: float(v) for k, v in loss.items()},
            'n': d.get('n'), 'measured_on': d.get('measured_on'),
            'margin': d.get('margin'), 'source': d.get('source')}


def minutes_haircut(mpg, spread, bo):
    """(expected minutes, risk, minutes lost). Identity when unmeasured.

    expected = mpg - loss[tier] * P(blowout)

    Absolute minutes rather than a fraction, because that is the shape
    the loss is measured in: a blowout costs a man a number of
    minutes, not a percentage of himself.
    """
    if not bo or mpg is None:
        return mpg, None, 0.0
    r = blowout_risk(spread)
    if r is None:
        return mpg, None, 0.0
    loss = (bo['loss'].get(mpg_tier(mpg))
            or bo['loss'].get('all') or 0.0)
    cut = loss * r
    return max(MIN_MIN, mpg - cut), r, round(cut, 2)


def day_pairs(day, cache=None, fetch_missing=True):
    """[(away, home)] for one past day, from the schedule, cached.

    season_logs rows carry no opponent and no game id -- the writer
    records day, player, team and the stat columns, nothing more -- so
    a 14-game night is 28 teams in one bucket with no way to pair
    them. The schedule is the only thing that knows, and slate() takes
    an explicit date, so it answers for past days too.
    """
    cache = {} if cache is None else cache
    if day in cache:
        return [tuple(x) for x in cache[day]]
    if not fetch_missing:
        return []
    try:
        pairs = [(code(a), code(h)) for a, h in (slate(day) or [])]
    except Exception as e:
        print(f'  ! schedule for {day} unavailable: {e}')
        pairs = []
    cache[day] = [list(p) for p in pairs]
    return pairs


def load_sched(path=None):
    path = path or SCHED
    if not os.path.exists(path):
        return {}
    try:
        return json.load(io.open(path, encoding='utf-8'))
    except Exception:
        return {}


def save_sched(cache, path=None):
    path = path or SCHED
    try:
        io.open(path, 'w', encoding='utf-8').write(
            json.dumps(cache, indent=0))
    except Exception as e:
        print(f'  ! could not cache schedules ({e})')


def team_margins(logs_days, fetch_missing=True):
    """{(day, team): final margin}, paired via the schedule.

    Each team's points are summed from its own player rows. The totals
    are understated -- this cache does not hold every player who
    appeared -- but a MARGIN is a difference of two equally understated
    totals from the same game, so the shortfall largely cancels. What
    remains is compression toward zero, which biases the measured
    minute loss DOWN. Conservative in the right direction.
    """
    cache = load_sched()
    before = len(cache)
    out, missing, from_row = {}, [], 0
    for d, rows in sorted((logs_days or {}).items()):
        pts = collections.Counter()
        own = {}
        for r in rows:
            t = code(r.get('team'))
            if not t:
                continue
            pts[t] += r.get('pts') or 0
            o = code(r.get('opp')) if r.get('opp') else None
            if o:
                own[t] = o
        if not pts:
            continue
        # PREFER THE ROW. edges.py now records opp, so a rebuilt cache
        # needs no network at all. The schedule path stays for days
        # already cached without it.
        if own and len(own) >= len(pts):
            pairs = sorted({tuple(sorted((t, o)))
                            for t, o in own.items()})
            from_row += 1
        else:
            pairs = day_pairs(d, cache, fetch_missing)
        if not pairs:
            missing.append(d)
            continue
        for a, h in pairs:
            if pts.get(a) and pts.get(h):
                out[(d, a)] = pts[a] - pts[h]
                out[(d, h)] = pts[h] - pts[a]
    if len(cache) != before:
        save_sched(cache)
    if from_row:
        print(f'  {from_row} day(s) paired from the rows themselves '
              f'(no network needed)')
    if missing:
        print(f'  ! no schedule for {len(missing)} day(s): '
              f'{", ".join(missing[:6])}'
              + (' ...' if len(missing) > 6 else ''))
    return out


def measure_blowout(logs_days, margin=None, close=8.0, min_each=3,
                    write=None):
    """Minutes lost to a blowout, within player, by tier."""
    margin = BLOWOUT_MARGIN if margin is None else margin
    marg = team_margins(logs_days)
    if not marg:
        print('\n  no team margins could be built.')
        # SAY WHAT WAS ACTUALLY THERE. The first version of this
        # asserted a cause ("the rows carry no game identifier") that
        # was true but not the blocker, and printed no evidence, so
        # the next step was a guess. Show the row shape instead.
        sample = next((rows[0] for rows in (logs_days or {}).values()
                       if rows), None)
        if sample:
            print(f'  a season_logs row holds: '
                  f'{", ".join(sorted(sample.keys()))}')
        print('  Pairing comes from the schedule (slate), cached in '
              f'{SCHED}.\n  If the schedule calls failed above, that is '
              'the blocker -- check\n  the network, then re-run.')
        return None
    # per player: minutes in blowouts, minutes in close games
    blow = collections.defaultdict(list)
    tight = collections.defaultdict(list)
    allmin = collections.defaultdict(list)
    for d, rows in (logs_days or {}).items():
        for r in rows:
            t, p = code(r.get('team')), norm_player(r.get('player'))
            m = r.get('min')
            if not t or not p or m is None:
                continue
            mg = marg.get((d, t))
            if mg is None:
                continue
            allmin[p].append(m)
            if abs(mg) >= margin:
                blow[p].append(m)
            elif abs(mg) <= close:
                tight[p].append(m)

    per_tier = collections.defaultdict(lambda: {'w': 0.0, 'sw': 0.0,
                                                'n': 0})
    rows_out = []
    # WHO THE FILTER EXCLUDES IS PART OF THE ANSWER.
    #
    # The measurement needs min_each games on BOTH sides. A deep bench
    # player appears almost entirely in blowouts, so he has ~0 close
    # games and is dropped -- and he is exactly the man whose minutes
    # GO UP. The 'secondary' tier that survives is rotation players,
    # not true bench, so a positive loss there is survivorship, not
    # evidence that benches lose minutes too. Counted and printed, so
    # the number cannot be over-read.
    excl = {'too few blowouts': 0, 'too few close games': 0, 'neither': 0}
    for p in sorted(set(blow) | set(tight)):
        nb, nt = len(blow.get(p) or []), len(tight.get(p) or [])
        if nb >= min_each and nt >= min_each:
            continue
        if nb < min_each and nt < min_each:
            excl['neither'] += 1
        elif nb < min_each:
            excl['too few blowouts'] += 1
        else:
            excl['too few close games'] += 1
    for p in sorted(set(blow) & set(tight)):
        nb, nt = len(blow[p]), len(tight[p])
        if nb < min_each or nt < min_each:
            continue
        mb = statistics.fmean(blow[p])
        mt = statistics.fmean(tight[p])
        base = statistics.fmean(allmin[p])
        tier = mpg_tier(base)
        w = float(min(nb, nt))
        d_ = mt - mb                      # positive = minutes LOST
        g = per_tier[tier]
        g['w'] += w
        g['sw'] += w * d_
        g['n'] += 1
        rows_out.append((p, tier, base, mt, mb, d_, nb, nt))

    if not rows_out:
        print(f'\n  nobody has {min_each}+ games both in blowouts '
              f'(|margin| >= {margin:.0f}) and\n  in close games '
              f'(|margin| <= {close:.0f}). Too early in the season; '
              f'this needs\n  a few more weeks of logs.')
        return None

    loss = {t: round(g['sw'] / g['w'], 2)
            for t, g in per_tier.items() if g['w']}
    print(f'\nMINUTES LOST TO A BLOWOUT -- within player, by tier')
    print(f'  blowout |margin| >= {margin:.0f}, close |margin| <= '
          f'{close:.0f}, {min_each}+ games each side')
    print(f'\n  {"TIER":11}{"PLAYERS":>8}{"CLOSE":>8}{"BLOWOUT":>9}'
          f'{"LOST":>8}')
    for t in ('star', 'main', 'secondary'):
        g = per_tier.get(t)
        if not g or not g['w']:
            continue
        sub = [r for r in rows_out if r[1] == t]
        print(f'  {t:11}{g["n"]:>8}'
              f'{statistics.fmean(r[3] for r in sub):>8.1f}'
              f'{statistics.fmean(r[4] for r in sub):>9.1f}'
              f'{loss[t]:>+8.2f}')
    print('\n  LOST is positive when a blowout COSTS him minutes.')
    tot_ex = sum(excl.values())
    if tot_ex:
        print(f'\n  EXCLUDED {tot_ex} player(s) for lacking '
              f'{min_each}+ games on both sides:')
        for k, v in sorted(excl.items(), key=lambda kv: -kv[1]):
            if v:
                print(f'    {v:>4}  {k}')
        if excl['too few close games']:
            print(f"\n  READ THE SECONDARY TIER WITH THAT IN MIND. The "
                  f"{excl['too few close games']} player(s) dropped for\n"
                  f'  too few CLOSE games are the deep bench -- they '
                  f'appear almost only\n  in blowouts, and they are '
                  f'exactly the men whose minutes GO UP.\n  So the '
                  f'secondary tier measured here is rotation players, '
                  f'not true\n  bench, and a positive loss for it is '
                  f'survivorship rather than\n  evidence that benches '
                  f'lose minutes as well.')

    # the biggest individual movers, as a sanity check on the method
    rows_out.sort(key=lambda r: -abs(r[5]))
    print(f'\n  largest movers  {"":6}{"BASE":>7}{"CLOSE":>7}'
          f'{"BLOW":>7}{"LOST":>7}{"nB":>4}{"nC":>4}')
    for p, t, base, mt, mb, d_, nb, nt in rows_out[:8]:
        print(f'    {p[:20]:22}{base:>7.1f}{mt:>7.1f}{mb:>7.1f}'
              f'{d_:>+7.1f}{nb:>4}{nt:>4}')

    if write:
        out = {'loss': loss, 'n': {t: per_tier[t]['n'] for t in loss},
               'margin': margin, 'close': close,
               'measured_on': datetime.datetime.now().astimezone()
               .strftime('%Y-%m-%d'),
               'source': 'measure_blowout, within player, weighted by '
                         'min(nBlowout, nClose)'}
        io.open(write, 'w', encoding='utf-8').write(
            json.dumps(out, indent=1))
        print(f'\n  wrote {write}')
        print('  the minutes term is now ON. Re-run the board and the '
              'header will\n  report the haircut instead of warning '
              'that it is off.')
    else:
        print('\n  NOT WRITTEN. Add --write-blowout to turn the minutes '
              'term on.')
    return loss


def show_margin_sd(logs_days, boardlog=None):
    """Measure MARGIN_SD against the spreads the boards were built on.

    Every board row now logs the spread it ran under, so once a few
    weeks have accrued this replaces the assumed 13.0. Until then it
    says how far off it is from being able to.
    """
    path = boardlog or BOARDLOG
    marg = team_margins(logs_days)
    pairs = []
    if os.path.exists(path):
        seen = set()
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            sp, d, t = r.get('spread'), r.get('day'), r.get('team')
            if sp is None or (d, t) in seen:
                continue
            mg = marg.get((d, code(t)))
            if mg is None:
                continue
            seen.add((d, t))
            pairs.append((-float(sp), float(mg)))
    print('\nMARGIN_SD -- is 13.0 right for this season?')
    if len(pairs) < 30:
        print(f'  {len(pairs)} team-game(s) with both a logged spread and '
              f'a margin.\n  Needs about 30 before the SD means '
              f'anything; keep logging.')
        return None
    err = [m - e for e, m in pairs]
    sd = statistics.pstdev(err)
    print(f'  {len(pairs)} team-games.  mean error {statistics.fmean(err):+.2f}'
          f'   SD {sd:.2f}   (assumed {MARGIN_SD:.1f})')
    print('  Mean error well away from zero would mean the SPREADS are '
          'biased,\n  which is a different and much bigger claim than '
          'the SD being off.')
    return sd


def show_sd_inflation():
    """Say out loud which SD the board is pricing with.

    LOUD, ON STDOUT, BECAUSE SILENCE LOOKS LIKE SUCCESS -- the same
    rule market_env already follows. The SD sets every probability on
    the slip, and on 16 Nov the factors were applied and the question
    came straight back as "board stayed the same. Is that accurate?"
    It was accurate -- goto_picks is price-free and cannot move on an
    SD -- but nothing in the run output said whether the factors were
    even in force, so the only way to tell a working correction from
    a file that never loaded was to ask.

    A term that changes every price on the page must not be able to
    switch itself off quietly.
    """
    inf = sd_inflation()
    live = {mk: inf[mk] for mk in CATS if inf.get(mk, 1.0) != 1.0}
    if not live:
        print('  SD inflation: OFF (factor 1.0 everywhere). '
              '--fit-sd to measure it.')
        return
    bits = ', '.join(f'{mk} x{live[mk]:.2f}' for mk in CATS
                     if mk in live)
    print(f'  SD inflation: {bits}')
    print('  This widens every slip probability. It does NOT touch the '
          'go-to five,')
    print('  which scores on matchup z and consults no price.')


def market_env(day, logs_days, before=None):
    """{team: {...}} -- what the market expects of each team tonight.

    Returns the implied team total, the game total, and each as a
    ratio against that team's own baseline, so the terms are
    multipliers on a projection rather than raw points.
    """
    # LOUD, ON STDOUT, BECAUSE SILENCE LOOKED LIKE SUCCESS.
    #
    # The first run of this shipped with the market term inert -- no
    # key in the environment, game_lines raised, the fallback
    # returned {} and every multiplier became 1.0. The boards printed
    # normally and the only tell was a missing bracket in a game
    # header. A term that changes every number on the page must not
    # be able to switch itself off quietly.
    try:
        lines = game_lines(day)
    except Exception as e:
        print('\n  !! MARKET TERM IS OFF: ' + str(e))
        print('  !! Every projection below is UNSCALED -- no implied '
              'total, no pace.')
        print('  !! Run:  . ~/.sharpz_env   and try again.\n')
        return {}
    if not lines:
        print('\n  !! MARKET TERM IS OFF: no lines returned for '
              + str(day))
        print('  !! Every projection below is UNSCALED.\n')
        return {}

    # each team's own baseline scoring, from the cache
    pts, gms = collections.Counter(), collections.Counter()
    for d in sorted(logs_days):
        if before and d >= before:
            continue
        day_pts = collections.Counter()
        for r in logs_days[d]:
            day_pts[code(r.get('team'))] += r.get('pts') or 0
        for t, v in day_pts.items():
            if v > 0:
                pts[t] += v
                gms[t] += 1
    base = {t: pts[t] / gms[t] for t in pts if gms[t]}
    lg_base = statistics.fmean(base.values()) if base else 0.0

    totals = [v.get('total') for v in lines.values() if v.get('total')]
    lg_total = statistics.fmean(totals) if totals else LEAGUE_TOTAL

    # IMPLIED / BASE WOULD HAVE BEEN WRONG, AND SILENTLY.
    #
    # The implied total is a real team total -- about 113. The
    # baseline is summed from season_logs, which does not store every
    # player who appeared: the median team-game in this cache holds
    # about 157 player-minutes against a real 240. So the baseline is
    # understated by roughly a third, and implied/base would have
    # come out near 1.5 for every team on the slate, hit the clamp,
    # and silently become a constant.
    #
    # A ratio of ratios removes it. How far above its own usual is
    # this team expected to score, against how far above the league's
    # usual it normally sits -- both measured inside the same cache,
    # so any uniform shortfall cancels exactly.
    implied_all = {}
    for t, v in lines.items():
        tot, spr = v.get('total'), v.get('spread')
        if tot is not None and spr is not None:
            implied_all[t] = tot / 2.0 - spr / 2.0
    lg_implied = (statistics.fmean(implied_all.values())
                  if implied_all else 0.0)

    out = {}
    for t, implied in implied_all.items():
        v = lines[t]
        b = base.get(t) or lg_base
        rel_market = (implied / lg_implied) if lg_implied else 1.0
        rel_base = (b / lg_base) if lg_base else 1.0
        out[t] = {
            'implied': round(implied, 1),
            'total': round(v['total'], 1),
            'spread': round(v['spread'], 1),
            'base_pts': round(b, 1),
            'score_mult': round(rel_market / rel_base, 4) if rel_base else 1.0,
            'pace_mult': round(v['total'] / lg_total, 4) if lg_total else 1.0,
        }
    return out


def assist_env(gs):
    """Per team: assists its opponents record, per 100 possessions.

    The direct measurement, for the reason in the header: the chain
    from defence to assists conceded runs through shot quality, rim
    protection, switching and closeout speed, none of which this cache
    observes. What it does observe is the outcome.

    Possessions are estimated the same way environment() does it --
    FGA plus 0.44 times free-throw attempts, with FTA backed out of
    points and makes, because the cache has no FTA column. Turnovers
    would belong in both the possession estimate and the model (a
    steal ends a possession before anyone can be credited) and they
    are not in the cache at all. That is a real gap, not a rounding
    one, and it is why this is a first version.
    """
    agg = collections.defaultdict(
        lambda: {'g': 0, 'opp_ast': 0, 'opp_poss': 0.0, 'own_ast': 0})
    for g in gs:
        for t in g['teams']:
            opp = g['home'] if g['away'] == t else g['away']
            ot = g['teams'].get(opp) or {}
            me = g['teams'].get(t) or {}
            if 'ast' not in ot:
                continue
            a = agg[t]
            a['g'] += 1
            a['opp_ast'] += ot.get('ast') or 0
            a['own_ast'] += me.get('ast') or 0
            fta = 0.44 * max(0, (ot.get('pts') or 0)
                             - 2 * (ot.get('fgm') or 0) - (ot.get('tpm') or 0))
            a['opp_poss'] += (ot.get('fga') or 0) + fta
    out = {}
    for t, a in agg.items():
        if not a['g'] or a['opp_poss'] <= 0:
            continue
        out[t] = {
            'games': a['g'],
            'ast_allowed': round(a['opp_ast'] / a['g'], 1),
            'ast_per100': round(a['opp_ast'] / a['opp_poss'] * 100, 2),
            'poss': round(a['opp_poss'] / a['g'], 1),
        }
    return out


def rebound_strength(gs):
    """Per team: the SHARE of available boards it collects, not the count.

    THE BUG THIS REPLACES
      The first version returned rebounds per game. That number is
      contaminated by exactly the thing it was supposed to be
      independent of: a team playing fast, miss-heavy games collects
      more rebounds without being any better at rebounding. So
      `contest` was partly re-measuring `avail` with the opposite
      sign, and the two quietly cancelled a share of each other --
      which is why Scottie Barnes's rebound case came out at +1.29
      while the volume half of it was +0.23.

      Dividing by the misses actually available in those games removes
      the volume term and leaves the thing we meant: of the boards
      that came up, what fraction did this team take? That is a
      property of the team, and it is comparable across paces.

    Returns {team: share}, where share is its rebounds over the total
    missed field goals in its games. Roughly half, by construction,
    since both teams are collecting from the same pool.
    """
    agg = collections.defaultdict(lambda: {'reb': 0, 'miss': 0})
    for g in gs:
        teams = g.get('teams') or {}
        tot_miss = sum((v.get('fga') or 0) - (v.get('fgm') or 0)
                       for v in teams.values())
        if tot_miss <= 0:
            continue
        for t, v in teams.items():
            agg[t]['reb'] += v.get('reb') or 0
            agg[t]['miss'] += tot_miss
    return {t: a['reb'] / a['miss']
            for t, a in agg.items() if a['miss'] > 0}


def per_game_metrics(gs):
    """{metric: {team: [one value per game]}} -- the raw material for
    asking how much of the between-team spread is real."""
    out = {'misses': collections.defaultdict(list),
           'share': collections.defaultdict(list),
           'ast100': collections.defaultdict(list)}
    for g in gs:
        teams = g.get('teams') or {}
        if len(teams) != 2:
            continue
        tot_miss = sum((v.get('fga') or 0) - (v.get('fgm') or 0)
                       for v in teams.values())
        for t, v in teams.items():
            opp = g['home'] if g['away'] == t else g['away']
            ot = teams.get(opp) or {}
            if tot_miss > 0:
                out['misses'][t].append(tot_miss)
                out['share'][t].append((v.get('reb') or 0) / tot_miss)
            if 'ast' in ot:
                fta = 0.44 * max(0, (ot.get('pts') or 0)
                                 - 2 * (ot.get('fgm') or 0)
                                 - (ot.get('tpm') or 0))
                poss = (ot.get('fga') or 0) + fta
                if poss > 0:
                    out['ast100'][t].append((ot.get('ast') or 0) / poss * 100)
    return out


def reliability(gs):
    """How much of each environment's between-team spread is REAL?

    THE PROBLEM THE LEAGUE TABLE EXPOSED
      Every team in the cache has played three to five games. The
      board z-scores a team against the league, which silently assumes
      the team's figure IS its ability. Over four games it is not: it
      is ability plus a large helping of which four games.

      Observed spread between teams = true spread + sampling spread.
      Both are measurable here -- the sampling half from how much a
      team's own number bounces game to game, the observed half from
      how teams differ. Subtract and you get the true spread, and the
      ratio is the share of the z that means anything.

      Where that ratio is small the z is mostly noise, and a +1.8
      should be read as a +0.6. Shrinking by the ratio is the standard
      fix and it is the same move as blend_rate: trust a short sample
      proportionally to how much it has earned. We already do it for
      players. We were not doing it for teams.

    Returns {metric: {'rel': r, 'within': sd, 'between': sd, 'true': sd}}.
    """
    pg = per_game_metrics(gs)
    out = {}
    for mk, by_team in pg.items():
        teams = {t: v for t, v in by_team.items() if len(v) >= 2}
        if len(teams) < 5:
            out[mk] = {'ok': False}
            continue
        # within: pooled variance of a team's own game-to-game values
        num = den = 0.0
        for t, v in teams.items():
            m = statistics.fmean(v)
            num += sum((x - m) ** 2 for x in v)
            den += len(v) - 1
        within_var = num / den if den else 0.0
        means = [statistics.fmean(v) for v in teams.values()]
        between_var = statistics.pvariance(means) if len(means) > 1 else 0.0
        n_bar = statistics.fmean([len(v) for v in teams.values()])
        true_var = max(0.0, between_var - within_var / n_bar)
        rel = (true_var / between_var) if between_var > 0 else 0.0
            # HOW PRECISE IS THIS RELIABILITY FIGURE ITSELF?
        #
        # On 30 Oct this reported 21/32/15 percent. One day later, on
        # one extra night of games, it reported 5/16/4. Reliability
        # should RISE with sample, not fall by a factor of three, and
        # the swing is not the league changing -- it is a difference
        # of two noisy variance estimates being read as a measurement.
        #
        # A variance has a standard error of roughly v*sqrt(2/df).
        # Here between_var rests on ~30 team means (df 29) and
        # within_var on a few hundred observations, and the quantity
        # we want is their DIFFERENCE, which inherits both errors
        # while being much smaller than either. At these sample sizes
        # that lands at about +/-28 percentage points -- wider than
        # the estimate.
        #
        # So the number is reported WITH its error bar from now on,
        # and the shrinkage it drives is floored, because a factor
        # that halves overnight makes tonight's board incomparable
        # with last night's for no real reason.
        se_b = between_var * math.sqrt(2.0 / max(1, len(teams) - 1))
        se_w = within_var * math.sqrt(2.0 / max(1, den))
        se_t = math.sqrt(se_b ** 2 + (se_w / n_bar) ** 2)
        se_rel = (math.sqrt((se_t / between_var) ** 2
                            + (true_var * se_b / between_var ** 2) ** 2)
                  if between_var > 0 else None)

        # per-team, because a team with three games has earned less
        # trust than one with five and the board should say so.
        per_team = {}
        for t, v in teams.items():
            d = true_var + within_var / len(v)
            per_team[t] = (true_var / d) if d > 0 else 0.0
        out[mk] = {'ok': True, 'rel': rel, 'n_bar': n_bar,
                   'se_rel': se_rel, 'per_team': per_team,
                   'within': math.sqrt(within_var),
                   'between': math.sqrt(between_var),
                   'true': math.sqrt(true_var),
                   'teams': len(teams)}
    return out


def _z(vals):
    """(mean, sd) with a guard, so a flat league does not divide by 0."""
    v = [x for x in vals if x is not None]
    if len(v) < 2:
        return (statistics.fmean(v) if v else 0.0), 0.0
    return statistics.fmean(v), statistics.pstdev(v)


# --- player baselines -------------------------------------------------

def player_base(logs_days, field, before=None):
    """{(key, team): (per-36 rate, mpg, games)} for one stat."""
    by = collections.defaultdict(lambda: {'v': 0.0, 'm': 0.0, 'g': 0})
    for day in sorted(logs_days):
        if before and day >= before:
            continue
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            d = by[(norm_player(r.get('player')), code(r.get('team')))]
            d['v'] += r.get(field) or 0
            d['m'] += m
            d['g'] += 1
    out = {}
    for k, d in by.items():
        if d['m'] <= 0:
            continue
        out[k] = (d['v'] / d['m'] * 36.0, d['m'] / d['g'], d['g'])
    return out


def priors_for(market):
    if market == 'reb':
        return prior_rates()
    fld = PRIOR36.get(market)
    if not fld:
        return {}
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


# --- FIT, for the points board ---------------------------------------

# defense.py's board applies KEEP, |FIT| and possession floors before
# it returns anything. Those are CARD filters -- they decide what is
# worth betting -- and running them here made the points pool 11 deep
# on a nine-game slate while the rebound and assist pools ran to
# hundreds, because those two have no equivalent gate.
#
# That broke the intersection, which is the entire PRA model. "On all
# three boards" was really asking "did he survive defense.py's
# filter", and the other two categories were along for the ride.
#
# card.py already had the answer: PERMISSIVE, the flag set it uses to
# calibrate FIT against every player rather than the chosen few. Same
# need, same flags, copied here rather than reinvented -- and OUT
# players are re-filtered below by injury_map, so --allow-add is safe.
PERMISSIVE = ['--json', '--top', '3000', '--min-keep', '0', '--min-fit', '0',
              '--min-poss', '0', '--under-poss-lo', '0',
              '--under-poss-hi', '999', '--max-role', '1.1',
              '--drift-lo', '0', '--drift-hi', '99', '--allow-out']


def points_fits(day, extra=()):
    """{(key, team): fit} from defense.py --board, which owns the model."""
    cmd = [sys.executable, os.path.join(HERE, 'defense.py'),
           '--board', day, *PERMISSIVE, *extra]
    p = subprocess.run(cmd, capture_output=True, text=True)
    m = re.search(r'(?m)^\{', p.stdout or '')
    if not m:
        print('  ! defense.py --board produced no JSON; the points board '
              'will be empty', file=sys.stderr)
        return {}
    try:
        b = json.loads(p.stdout[m.start():])
    except Exception:
        return {}
    out = {}
    for side in ('overs', 'unders'):
        for r in b.get(side) or []:
            if r.get('fit') is None:
                continue
            out[(norm_player(r.get('player')), code(r.get('team')))] = {
                'fit': r['fit'], 'via': r.get('via'),
                # pulled through so the floors can be applied here
                # rather than handed to defense.py, where they would
                # gate the points board and miss the other two.
                'poss': r.get('poss'), 'keep': r.get('opp_keep'),
                'zone_z': r.get('zone_z') or {},
                'three_share': r.get('three_share'),
                # ALL FOUR SHARES, not just three. A row whose via is
                # 'rim' needs rim_share to report any volume at all,
                # and this reader was dropping it -- the same
                # field-not-copied-at-a-boundary bug as `via`.
                'shares': {z: r.get(f'{z}_share')
                           for z in ('rim', 'short', 'mid', 'three')},
                # how much of FIT the via zone accounts for.
                # Concentration, NOT magnitude -- see defense.py.
                'via_w': r.get('via_w'),
                # REST, WHICH WAS BEING COMPUTED AND THROWN AWAY.
                # defense.py has set x['rest'] and x['opp_rest'] all
                # along and this reader never pulled them, so no board
                # has ever known that a man played last night. On 05
                # Nov both PHX and MIA are on back-to-backs and it is
                # the single largest factor on a two-game slate.
                # Reported, not scored -- there is no measured minute
                # cost for a back-to-back in this cache yet, and
                # inventing one would be the borrowed-constant mistake
                # the minutes term exists to avoid.
                'rest': r.get('rest'),
                'opp_rest': r.get('opp_rest'),
                'drift': r.get('opp_drift'),
                'now_games': r.get('opp_now_games'),
                'w_now': r.get('opp_w_now'),
                'now_mpg': r.get('now_mpg') or r.get('mpg'),
            }
    return out


# --- the three boards -------------------------------------------------

def show_env(gs):
    """Every team's environment, ranked, so the z-scores are checkable.

    WHY THIS EXISTS
      On 30 Oct seven of the eight games on the board showed a
      POSITIVE share term -- that is, almost every opponent rebounding
      below average. A z-score centres on zero by construction, so
      seven of eight on one side is either a genuinely soft slate
      (possible: eight teams out of thirty, and the binomial puts
      7-of-8 at about 3.5%) or a miscentred distribution.

      Guessing between those from the board output is impossible, and
      I have guessed wrong enough this week. The whole league printed
      in rank order settles it in one look: if tonight's opponents
      really do cluster at the bottom, you will see them there.
    """
    env = environment(gs)
    aenv = assist_env(gs)
    rstr = rebound_strength(gs)
    mu_m, sd_m = _z([v['misses'] for v in env.values()])
    mu_a, sd_a = _z([v['ast_per100'] for v in aenv.values()])
    mu_r, sd_r = _z(list(rstr.values()))
    print(f'\nLEAGUE ENVIRONMENTS -- {len(env)} teams in the cache'
          f'  [three.py {BUILD}]')
    rel = reliability(gs)
    print('\n  HOW MUCH OF THIS SPREAD IS REAL')
    print(f"    {'METRIC':10}{'BETWEEN':>9}{'WITHIN':>9}{'TRUE':>9}"
          f"{'RELIABLE':>10}")
    for mk, lbl in (('misses', 'misses/g'), ('share', 'reb share'),
                    ('ast100', 'ast/100')):
        v = rel.get(mk) or {}
        if not v.get('ok'):
            print(f'    {lbl:10}  not enough games to decompose')
            continue
        print(f"    {lbl:10}{v['between']:>9.3f}{v['within']:>9.3f}"
              f"{v['true']:>9.3f}{v['rel']*100:>9.0f}%")
    print('    BETWEEN is how much teams differ. WITHIN is how much one '
          'team\n    bounces game to game. Subtract the sampling part '
          'and TRUE is what\n    is left. RELIABLE is the fraction of '
          'every z that means anything --\n    the boards scale by it, '
          'per team, so a club with three games is\n    trusted less '
          'than one with five.')

    # WHEN DOES EACH BOARD START MEANING SOMETHING?
    #
    # Reliability is not a verdict on the metric, it is a verdict on
    # the sample. T and W are properties of the league and do not
    # change; only n does, and it changes every night. So the same two
    # numbers that say "15% today" also say exactly when today's
    # answer stops applying -- which turns "wait for more games" from
    # a shrug into a date.
    ns = (4, 8, 12, 20, 30, 41, 82)
    have = {mk: (rel.get(mk) or {}) for mk in ('misses', 'share', 'ast100')}
    if any(v.get('ok') for v in have.values()):
        print('\n  WHEN EACH OF THESE STARTS MEANING SOMETHING')
        print('    reliability at N games played, from the same T and W:')
        print(f"    {'METRIC':11}" + ''.join(f'{n:>7}g' for n in ns))
        for mk, lbl in (('misses', 'misses/g'), ('share', 'reb share'),
                        ('ast100', 'ast/100')):
            v = have.get(mk) or {}
            if not v.get('ok'):
                continue
            t2 = v['true'] ** 2
            w2 = v['within'] ** 2
            row = ''.join(f'{(t2/(t2 + w2/n))*100:>7.0f}%'
                          if (t2 + w2 / n) > 0 else f'{0:>7.0f}%'
                          for n in ns)
            print(f'    {lbl:11}{row}')
        print('    41 games is roughly the All-Star break, 82 the full '
              'season. The\n    metrics do not improve -- the sample '
              'does. Nothing about these\n    boards is wrong today; '
              'they are simply being asked a question\n    four games '
              'cannot answer yet.')
    print(f'  misses/g   mean {mu_m:6.1f}  sd {sd_m:5.2f}')
    print(f'  reb share  mean {mu_r:6.3f}  sd {sd_r:5.3f}')
    print(f'  ast/100    mean {mu_a:6.2f}  sd {sd_a:5.2f}')
    print(f"\n  {'TEAM':6}{'G':>3}{'MISS':>8}{'z':>7}{'SHARE':>8}{'z':>7}"
          f"{'AST/100':>9}{'z':>7}")
    rows = []
    for t, v in env.items():
        sh = rstr.get(t)
        a = (aenv.get(t) or {}).get('ast_per100')
        rows.append((t, v['games'], v['misses'],
                     (v['misses'] - mu_m) / sd_m if sd_m else 0.0,
                     sh, ((sh - mu_r) / sd_r) if (sh and sd_r) else 0.0,
                     a, ((a - mu_a) / sd_a) if (a and sd_a) else 0.0))
    rows.sort(key=lambda r: -r[3])
    for t, g, m, zm, sh, zs_, a, za in rows:
        print(f"  {t:6}{g:>3}{m:>8.1f}{zm:>+7.2f}"
              f"{(sh or 0):>8.3f}{zs_:>+7.2f}{(a or 0):>9.2f}{za:>+7.2f}")
    print('\n  A board\'s SHARE term is this z NEGATED: positive on the '
          'board means\n  the opponent rebounds below average, so more '
          'is left on the floor.')


def _blow_banner(blow):
    """Say on every run whether the minutes term is on. Both ways.

    A term that is silently inert has cost this file two separate
    debugging sessions (the market term with no key, the injury
    filter comparing a dict to a string). So this prints when it is
    OFF and prints when it is ON, because only one of those being
    visible is how you end up trusting a number that was never
    applied.
    """
    if not blow:
        print('\n  !! MINUTES TERM IS OFF -- blowout.json has never been '
              'written.')
        print('  !! Every projection below treats minutes as KNOWN, and '
              'the market')
        print('  !! term rewards big favourites, whose stars sit in '
              'garbage time.')
        print('  !! Measure it:  python3 three.py --blowout '
              '--write-blowout\n')
        return
    ls = ', '.join(f'{k} {v:+.1f}m' for k, v in sorted(blow['loss'].items()))
    print(f"\n  minutes  blowout haircut ON -- measured loss {ls}"
          f" (margin {blow.get('margin')}, "
          f"{blow.get('measured_on')})")
    print(f'           applied as mpg - loss x P(|final margin| >= '
          f'{BLOWOUT_MARGIN:.0f} | spread),')
    print(f'           with the margin SD assumed at {MARGIN_SD:.1f} -- '
          f'check it with --margin-sd')


def drift_conflict(mult, dr, max_drift):
    """Does this season's defensive rating contradict the zone read?

    Returns a reason string, or None.

    SYMMETRIC, which it was not. The old test fired only when the
    board said SOFT and the defence had tightened -- correct while the
    slip only took overs, because a stale TOUGH read could never be
    backed. Once negative-z rows became live under candidates, a stale
    tough read inflates an under exactly as a stale soft read inflates
    an over.

    drift sign: dr is this season's defensive rating minus last
    season's, and a HIGHER defensive rating is a WORSE defence. So
    dr > 0 means the defence has got softer, dr < 0 means tighter.
    """
    if not max_drift or dr is None or mult is None:
        return None
    if mult > 0 and dr < -max_drift:
        return 'we read it soft, this season says it tightened'
    if mult < 0 and dr > max_drift:
        return 'we read it tough, this season says it collapsed'
    return None


def build_boards(day, logs_days, gs, top=15, min_mpg=MIN_MPG,
                 min_poss=MIN_POSS, per_game=PER_GAME,
                 max_drift=MAX_DRIFT, healthy_only=False):
    blow = load_blowout()
    _blow_banner(blow)
    spread_tab = stat_spread(logs_days, before=day)
    sdc = sd_curve(spread_tab)
    show_sd_inflation()
    sched = slate(day)
    if not sched:
        return None
    opp_of = {}
    for away, home in sched:
        opp_of[away] = home
        opp_of[home] = away

    menv = market_env(day, logs_days, before=day)
    env = environment(gs)                 # rebound opportunity, per team
    aenv = assist_env(gs)                 # assists allowed, per team
    rstr = rebound_strength(gs)           # how hard a team rebounds
    # INJURY STATUS, AND THE SHAPE OF IT
    #
    # This read `str(v).upper().startswith('OUT')` against v, which
    # is the whole dict -- so it was comparing "{'status': 'Out',
    # ..." to 'OUT' and matching almost nothing. Players who were
    # ruled out have been reaching these boards all along.
    #
    # And OUT was never the only thing that matters. A
    # day-to-day or questionable player can top a board and then not
    # dress, which is the same bad outcome arriving more quietly. He
    # is kept, because he usually plays, and FLAGGED, because
    # "usually" is the reader's decision and not the model's.
    inj = injury_map() or {}
    status = {}
    for n, v in inj.items():
        st = str((v or {}).get('status') or '').strip()
        if st:
            status[norm_player(n)] = st
    out_now = {k for k, st in status.items()
               if st.upper().startswith('OUT')}
    doubt = {k for k, st in status.items()
             if not st.upper().startswith('OUT')
             and st.upper().startswith(('DAY', 'GTD', 'QUES', 'DOUBT'))}

    # league baselines, so every multiplier is a ratio to normal
    mu_miss, sd_miss = _z([v['misses'] for v in env.values()])
    mu_a100, sd_a100 = _z([v['ast_per100'] for v in aenv.values()])
    mu_rstr, sd_rstr = _z(list(rstr.values()))   # now a SHARE, not a count

    # SHRINK EVERY TEAM ENVIRONMENT BY HOW MUCH OF IT IS REAL.
    #
    # Three to five games per team. The z-score treats a team's figure
    # as its ability; over four games it is ability plus which four
    # games, and the league table showed the two are comparable in
    # size. Shrinking by the measured reliability is the same move
    # blend_rate already makes for players -- trust a short sample in
    # proportion to what it has earned. It was never applied to teams.
    rel = reliability(gs)

    def shrink_factor(metric, team):
        r = rel.get(metric) or {}
        if not r.get('ok'):
            return 1.0          # unmeasurable: do not pretend to shrink
        measured = (r.get('per_team') or {}).get(team, r.get('rel', 0.0))
        # the floor depends only on games played, so it cannot swing
        floor = rel_floor((r.get('n_bar') or 0))
        return max(measured, floor)

    bases = {m: player_base(logs_days, f, before=day)
             for m, f in (('pts', 'pts'), ('threes', 'tpm'),
                          ('reb', 'reb'), ('ast', 'ast'))}
    # attempts, which is the part of a three that a matchup moves
    atts = player_base(logs_days, 'tpa', before=day)
    priors = {m: priors_for(m) for m in CATS}
    fits = points_fits(day)

    cut = collections.Counter()
    boards = {mk: [] for mk in CATS}
    # THE UNIVERSE IS THE UNION, NOT WHOEVER HAS A POINTS ROW.
    #
    # This iterated bases['pts'] -- so every board's candidate list
    # was silently gated on a points baseline existing, and renaming
    # or removing that one category would have emptied all four
    # boards without raising. Nothing about a rebound ranking should
    # depend on the points table being present.
    universe = set()
    for mk in CATS:
        universe |= set(bases[mk].keys())
    for (key, team) in sorted(universe):
        opp = opp_of.get(team)
        if not opp:
            continue
        if key in out_now:
            cut['ruled OUT tonight'] += 1
            continue
        if healthy_only and key in doubt:
            cut['not confirmed healthy'] += 1
            continue
        got_f = fits.get((key, team)) or {}
        for mk in CATS:
            got = bases[mk].get((key, team))
            if not got:
                continue
            rate36, mpg, g = got
            if g < MIN_GAMES:
                continue
            if mpg < min_mpg:
                cut['under the minutes floor'] += 1
                continue
            rate36, _w = blend_rate(rate36, priors[mk].get(key), g,
                                    prior=PRIOR_K.get(mk, PRIOR_GAMES))
            # MINUTES COME FROM THE LOGS, NOT FROM THE BOARD.
            #
            # This used to read the board's now_mpg first. Switching
            # the board from its default filters to PERMISSIVE then
            # moved Ivica Zubac from 29.0 projected minutes to 21.7 --
            # a 25% swing in a PROJECTION caused by changing a FILTER,
            # which also moved his rebound lift from +0.64 to +0.48
            # even though the rebound model had not changed a line.
            #
            # A filter decides who is shown. It must not decide what
            # the numbers are. The logged minutes per game are
            # computed here, from the cache, and do not care which
            # flags defense.py was handed -- so two runs with
            # different filters now give the same player the same
            # baseline, which is the minimum a ranking has to offer.
            #
            # The cost is real and worth stating: now_mpg carries
            # defense.py's role-growth adjustment for injuries and
            # rotation changes, and this does not. A man whose role
            # just grew will be understated here until his own log
            # catches up. That is a known, one-directional error --
            # the same one depletion() exists to flag on the card --
            # and it is the price of a ranking that reproduces.
            # THE MINUTES HAIRCUT, BEFORE THE RATE IS APPLIED.
            #
            # mins = mpg treated minutes as known, and minutes are
            # about 65% of production variance. Worse, score_mult
            # below is built on the implied team total, which across
            # a slate correlates with the SPREAD at about -0.85 -- so
            # the market term was rewarding big favourites while big
            # favourites are exactly where star minutes die. Durant
            # took 6 shots in a 16-point win on a 21.5 projection.
            #
            # score_mult is left alone: it is right about team points.
            # The correction belongs on the player's minutes.
            #
            # Identity when blowout.json has never been written, and
            # the header says so -- see load_blowout.
            me = menv.get(team) or {}
            mins, brisk, bcut = minutes_haircut(mpg, me.get('spread'),
                                                blow)
            base = rate36 / 36.0 * mins

            # THE MARKET'S ENVIRONMENT, APPLIED TO THE BASELINE.
            #
            # Points, assists and threes ride the team's own implied
            # total; rebounds ride the game total, because boards come
            # from possessions rather than points. Applied to the
            # BASELINE rather than to the ranking on purpose: it
            # changes what he is expected to do tonight, which is a
            # statement about the world, not about how much we like
            # the spot.
            mmult = (me.get('pace_mult') if mk == 'reb'
                     else me.get('score_mult'))
            if mmult:
                mmult = max(0.80, min(1.25, mmult))
                base *= mmult
            if base < MIN_BASE[mk]:
                continue

            # AFTER the market multiplier, not before. His own
            # measured SD is a fixed property of his game log, but the
            # league prior it shrinks toward is c*sqrt(mean), so the
            # mean it is evaluated at has to be the one we are
            # actually going to price -- the post-market projection.
            _sd, _sdw, _sdn = blended_sd(base, mk, spread_tab, sdc,
                                         key=(key, team))

            # the matchup multiplier, one model per category
            mult, why, parts, rfac = None, None, {}, 1.0
            if mk == 'pts':
                f = got_f.get('fit')
                if f is None:
                    continue
                # FIT is a z-like softness score. It is NOT calibrated
                # to a production multiplier -- measured at 546
                # player-games it did not move production -- so it is
                # carried as the ordering it is, and the "lift" below
                # is expressed in FIT units, not points.
                po = got_f.get('poss')
                if po is not None and po < min_poss:
                    cut['under the possessions floor'] += 1
                    continue
                kp = got_f.get('keep')
                if kp is not None and kp < MIN_KEEP:
                    cut['defence too changed to read'] += 1
                    continue
                mult = f
                # FIT is NOT shrunk here. Its reliability was measured
                # a different way -- 546 player-games, walk-forward --
                # and the finding was that it does not predict
                # production at all, which is a stronger statement
                # than a reliability fraction and is already why no
                # grade is published. Applying a second, differently
                # derived discount on top would be double-counting.
                rfac = 1.0
                dr = got_f.get('drift')
                _dc = drift_conflict(mult, dr, max_drift)
                if _dc:
                    cut[f'drift: {_dc}'] += 1
                    continue
                why = (f"soft from {got_f.get('via')}"
                       if got_f.get('via') else 'zone fit')
                if dr is not None:
                    why += f"; {opp} drtg {dr:+.1f} vs last season"
                # HIS OWN VOLUME IN THE ZONE THE LABEL NAMES.
                #
                # Computed the way the threes board computes it, so
                # the two are comparable: blended attempt rate per 36
                # scaled to his minutes. Only meaningful when via is
                # 'three' -- that is the one zone another board
                # measures, and the one the 02 Nov failure turned on.
                _via = got_f.get('via')
                _tatt = None
                if _via == 'three':
                    _ga = atts.get((key, team))
                    if _ga:
                        _a36r = _ga[0]
                        _a36, _ = blend_rate(
                            _a36r, None, g,
                            prior=PRIOR_K.get('threes', PRIOR_GAMES))
                        _tatt = round(_a36 / 36.0 * mpg, 1)
                _vw = got_f.get('via_w')
                _vsh = (got_f.get('shares') or {}).get(_via)
                _thin = (_via == 'three' and _tatt is not None
                         and _tatt < THIN_ZONE_ATT)
                parts = {
                    'drift': dr, 'w_now': got_f.get('w_now'),
                    'via_w': _vw,
                    'via_share': (None if _vsh is None else round(_vsh, 3)),
                    'three_att': _tatt, 'thin_zone': _thin,
                }
                if _thin:
                    why += (f'; THIN -- labelled three, he takes '
                            f'{_tatt:.1f} a game')
                elif _vw is not None and _vw < 0.5:
                    why += f'; that zone is {_vw * 100:.0f}% of his fit'
            elif mk == 'threes':
                zz = (got_f.get('zone_z') or {}).get('three')
                if zz is None:
                    continue
                po = got_f.get('poss')
                if po is not None and po < min_poss:
                    cut['under the possessions floor'] += 1
                    continue
                # Not shrunk, for the same reason points is not: this
                # is last season's zone profile, not four games.
                rfac = 1.0
                mult = zz
                # ATTEMPTS x PERCENTAGE, both estimated the same way.
                #
                # This used to divide the BLENDED makes by the RAW
                # attempt rate -- two different estimators in a ratio
                # -- and printed Draymond Green at 91% from three and
                # Nolan Traore at 64%. The percentage was not a
                # percentage, it was the blending showing through.
                #
                # Now: attempts are blended like any other rate, the
                # percentage comes from raw makes over raw attempts
                # and is shrunk toward the league, and the projection
                # is their product. That is also the model this board
                # is supposed to be -- a matchup moves attempts, not
                # the percentage.
                got_a = atts.get((key, team))
                if not got_a:
                    continue
                a36_raw, _amp, _ag = got_a
                a36, _w2 = blend_rate(a36_raw, None, g,
                                      prior=PRIOR_K.get('threes',
                                                        PRIOR_GAMES))
                a_pg = a36 / 36.0 * mpg
                if a_pg <= 0:
                    continue
                m_raw = (bases['threes'].get((key, team)) or (0, 0, 0))[0]
                att_tot = a36_raw / 36.0 * mpg * g
                mk_tot = m_raw / 36.0 * mpg * g
                pct = ((mk_tot + PCT_PRIOR_ATT * LEAGUE_3P)
                       / (att_tot + PCT_PRIOR_ATT)) if att_tot >= 0 else 0.0
                base = a_pg * pct
                if base < MIN_BASE[mk]:
                    cut['under the baseline'] += 1
                    continue
                dr = got_f.get('drift')
                _dc = drift_conflict(mult, dr, max_drift)
                if _dc:
                    cut[f'drift: {_dc}'] += 1
                    continue
                why = (f"{opp} is {zz:+.2f} from three; he takes "
                       f"{a_pg:.1f} a game at {pct*100:.0f}%")
                if dr is not None:
                    why += f"; {opp} drtg {dr:+.1f} vs last season"
                parts = {'att': round(a_pg, 1), 'pct': round(pct, 3),
                         'att_seen': round(att_tot, 0), 'drift': dr,
                         'w_now': got_f.get('w_now')}
            elif mk == 'reb':
                e = env.get(opp)
                if not e or sd_miss <= 0:
                    continue
                # VOLUME and SHARE, kept apart because they are not
                # equally trustworthy. Volume is a property of the
                # games -- more misses, more boards, and that holds
                # whoever is on the floor. Share says the opponent
                # will not contest, which depends on who they rest,
                # who is hurt and how the rotation runs, and
                # evaporates the night their bigs are healthy.
                #
                # Summed with equal weight, which is a CHOICE and not
                # a measurement. Both halves are printed so a row
                # standing entirely on the fragile one is visible
                # rather than hidden inside a total.
                vol = (e['misses'] - mu_miss) / sd_miss
                shr = (-(rstr.get(opp, mu_rstr) - mu_rstr) / sd_rstr
                       if sd_rstr > 0 else 0.0)
                mult = vol + shr
                # the two halves carry different reliabilities, so the
                # row's factor is weighted by how much each contributes
                _fv = shrink_factor('misses', opp)
                _fs = shrink_factor('share', opp)
                den = abs(vol) + abs(shr)
                rfac = ((abs(vol) * _fv + abs(shr) * _fs) / den
                        if den > 0 else min(_fv, _fs))
                # shr is NEGATED above, so positive means the
                # opponent rebounds BELOW average. The sentence said
                # "takes +1.31 of its usual share", which reads as
                # taking MORE and means the opposite of the number
                # printed beside it. A reader checking the model
                # against the words would have concluded the sign was
                # backwards -- and been right about the words.
                leaves = 'leaves' if shr >= 0 else 'takes'
                why = (f"{e['misses']:.0f} misses in {opp} games "
                       f"({vol:+.2f}); {opp} {leaves} {abs(shr):.2f} "
                       f"{'more' if shr >= 0 else 'less'} on the floor "
                       f"than average")
                parts = {'vol': round(vol, 2), 'shr': round(shr, 2)}
            else:
                a = aenv.get(opp)
                if not a or sd_a100 <= 0:
                    continue
                mult = (a['ast_per100'] - mu_a100) / sd_a100
                fa = rfac = shrink_factor('ast100', opp)
                why = (f"{opp} allows {a['ast_per100']:.1f} ast/100 "
                       f"({mult:+.2f} after shrinking to "
                       f"{(fa or 0)*100:.0f}% reliability)")

            boards[mk].append({
                'player': key, 'team': team, 'opp': opp, 'market': mk,
                'base': round(base, 1), 'mins': round(mins, 1), 'g': g,
                'min_src': 'log', 'parts': parts, 'rfac': round(rfac, 3),
                # WHICH TERMS WERE LIVE. base is not comparable
                # across these: with the market term off it is an
                # unscaled rate, with it on it is scaled by the
                # implied total. Pooling them measures neither.
                'rest': got_f.get('rest'),
                'opp_rest': got_f.get('opp_rest'),
                'market_on': bool(me),
                'minutes_on': bool(blow),
                'mpg_raw': round(mpg, 1), 'tier': mpg_tier(mpg),
                'blowout_risk': brisk, 'min_cut': bcut,
                # his OWN game-to-game spread, shrunk toward the
                # league mean-to-SD curve. The probability layer
                # cannot run without it and must not assume it.
                'sd': round(_sd, 2), 'sd_w': _sdw, 'sd_n': _sdn,
                'mkt': (round(mmult, 3) if mmult else None),
                'status': status.get(key),
                'doubt': key in doubt,
                'implied': me.get('implied'), 'gtotal': me.get('total'),
                'spread': me.get('spread'),
                # the zone that carried a points row, kept as data so
                # nothing downstream has to parse the sentence
                'via': (got_f.get('via') if mk == 'pts' else None),
                'z': round(mult, 3),
                # lift in the stat's own units for reb/ast, where the
                # multiplier is a measured environment; for pts the
                # environment is FIT, which is not calibrated to
                # production, so no unit lift is claimed.
                'lift': (None if mk == 'pts'
                         else round(base * LIFT_K * mult, 2)),
                'why': why,
            })

    # STANDARDISE EACH BOARD AGAINST TONIGHT'S OWN SPREAD.
    #
    # The three metrics were never on the same scale and I summed them
    # anyway. FIT came off the 30 Oct slate between +0.08 and -0.17;
    # the rebound and assist numbers are league z-scores and ran to
    # +2.30 and +2.15. z_sum added them, so the points matchup
    # contributed about 4% of a figure that was supposed to find
    # players helped in all three categories -- the PRA ranking was
    # really a reb+ast ranking with a rounding error attached.
    #
    # Each board's metric is now re-expressed against the spread of
    # that same metric across tonight's rankable players. That makes
    # the three comparable by construction, and it also makes them
    # mean the right thing: "best matchup on the slate" is a claim
    # about tonight's options, not about a league-wide constant. On a
    # three-game slate a +2.0 z against the league might be the worst
    # spot available, and the slate z says so.
    for mk in CATS:
        mu, sd = _z([r['z'] for r in boards[mk]])
        for r in boards[mk]:
            raw_zs = (r['z'] - mu) / sd if sd > 0 else 0.0
            r['zs_raw'] = round(raw_zs, 3)
            # reliability applied HERE, after standardising, or the
            # standardisation divides it back out (see build stamp l).
            r['zs'] = round(raw_zs * r.get('rfac', 1.0), 3)
            if r['lift'] is not None:
                r['lift'] = round(r['base'] * LIFT_K * r['zs'], 2)

    # pts ranks on zs, because FIT is player-specific already -- the
    # zone diet differs man to man, so the metric does the separating.
    #
    # reb and ast rank on lift, because their environments are
    # TEAM-level: every player facing the same defence carries an
    # identical z, and ordering on it alone would tie a whole roster
    # and break the tie on dictionary order. That asymmetry is real
    # and it is the honest limit of this version -- the points model
    # knows who the player is, the other two only know who he is
    # playing.
    for mk in CATS:
        if mk == 'pts':
            boards[mk].sort(key=lambda r: -r['zs'])
        else:
            boards[mk].sort(key=lambda r: (-(r['lift'] or 0), -r['zs']))
        if per_game and per_game > 0:
            seen = collections.Counter()
            kept = []
            for r in boards[mk]:
                g = tuple(sorted((r['team'], r['opp'])))
                if seen[g] >= per_game:
                    continue
                seen[g] += 1
                kept.append(r)
            n_cap = len(boards[mk]) - len(kept)
            if n_cap:
                cut[f'held back by the per-game cap ({mk})'] = n_cap
            boards[mk] = kept
        boards[mk] = boards[mk][:top]
    boards['_cut'] = dict(cut)
    boards['_floors'] = {'mpg': min_mpg, 'poss': min_poss,
                         'keep': MIN_KEEP, 'per_game': per_game}
    boards['_inj'] = {'out': len(out_now), 'doubt': len(doubt),
                      'age_h': inj_age_hours(), 'n': len(status)}
    boards['_rel'] = {k: round(v.get('rel', 0.0), 3)
                      for k, v in rel.items() if v.get('ok')}
    boards['_rel_se'] = {k: (round(v['se_rel'], 3)
                             if v.get('se_rel') is not None else None)
                         for k, v in rel.items() if v.get('ok')}
    # WHAT WAS ACTUALLY APPLIED, not what was measured. Adding the
    # floor without changing this label left the header saying "4%
    # reliable ... and shrunk to match" above rows that had been
    # scaled by 21%. A label that describes a different number from
    # the one in the arithmetic is worse than no label.
    boards['_rel_applied'] = {
        k: round(max(v.get('rel', 0.0), rel_floor(v.get('n_bar') or 0)), 3)
        for k, v in rel.items() if v.get('ok')}
    return boards


def overlap(boards, min_leg=0.0):
    """Players on all three boards -- the PRA candidates."""
    sets = {mk: {(r['player'], r['team']) for r in boards[mk]}
            for mk in PRA_CATS}
    both = set.intersection(*(sets[mk] for mk in PRA_CATS))
    # "on all three boards" is sensitive to how many games there are:
    # a board holds ~15 rows whether the slate is three games or
    # twelve, so on a short night a nearly neutral leg can ride in on
    # scarcity. Tatum made it on 30 Oct with +0.30 assists. A floor
    # per leg is the stable version of the same test.
    rows = []
    for key in both:
        # next() WITH A DEFAULT. The bare form raises StopIteration
        # and takes the process with it; a miss here should drop one
        # candidate, not the run.
        parts = {mk: next((r for r in boards[mk]
                           if (r['player'], r['team']) == key), None)
                 for mk in PRA_CATS}
        if any(v is None for v in parts.values()):
            continue
        if min_leg and any(parts[m]['zs'] < min_leg for m in PRA_CATS):
            continue
        rows.append({
            'player': key[0], 'team': key[1], 'opp': parts['pts']['opp'],
            'pts_z': parts['pts']['zs'], 'reb_z': parts['reb']['zs'],
            'ast_z': parts['ast']['zs'],
            'base_pra': round(sum(parts[m]['base'] for m in parts), 1),
            # summing SLATE z-scores, which are comparable. Summing the
            # raw metrics was not, and that was the whole PRA ranking.
            'z_sum': round(sum(parts[m]['zs'] for m in parts), 3),
            'why': [parts[m]['why'] for m in PRA_CATS],
        })
    rows.sort(key=lambda r: -r['z_sum'])
    return rows


# --- output -----------------------------------------------------------

TITLE = {'pts': 'POINTS', 'threes': 'THREES', 'reb': 'REBOUNDS',
         'ast': 'ASSISTS'}
MODEL = {
    'pts': 'how soft that defence is in the zones he shoots from',
    'threes': 'how soft that defence is from three, against his attempts',
    'reb': 'misses the opponent generates, plus the share it fails to take',
    'ast': 'assists that defence allows per 100 possessions',
}
# which boards rest on last season (full sample) and which on this
# one (three to five games). Printed, because it is the single thing
# that decides how hard to read a number.
SOURCE = {'pts': 'both seasons', 'threes': 'both seasons',
          'reb': 'this season', 'ast': 'this season'}


def _by_game(rows):
    """[(matchup, [rows])] ordered by each game's best row.

    GROUPED, BECAUSE THE CLAIM IS ABOUT THE GAME
      The rebound and assist environments are team-level: every Knick
      facing Philadelphia carries the same number. Printed flat, that
      reads as four findings and is one, and the reason string repeats
      verbatim down the column. Grouping says the matchup once and
      lists who it reaches -- which is both shorter and the actual
      shape of the claim.
    """
    g = collections.OrderedDict()
    for r in rows:
        k = f"{r['team']} vs {r['opp']}"
        g.setdefault(k, []).append(r)
    return list(g.items())


# --- the slip, as a rule rather than a judgement ---------------------
#
# Every night so far the shortlist has been argued out by hand from
# the same four tests. Written down they are:
#
#   1. LAST-SEASON BOARDS ONLY. Points and threes rest on a full
#      prior season. Rebounds and assists measured 10-14% reliable
#      and were floored UP to 23% -- the number on those rows is
#      mostly floor, and a floor is not a reason to bet.
#   2. REAL SAMPLE UNDERNEATH. A threes row needs attempts actually
#      observed, not a percentage inferred from twelve shots. A
#      points row needs enough games to have a baseline at all.
#   3. NOT INJURY-FLAGGED. Day-to-day men are kept on the board and
#      excluded from the slip: the board is a measurement and the
#      slip is a recommendation, and they deserve different bars.
#   4. NO DOUBLE-COUNTING. When a player's points edge IS his threes
#      edge, that is one claim; it appears once, as threes, because
#      that is where the model has attempts and a shrunk percentage
#      rather than a zone read alone.
#
# Writing them down is the point. A shortlist argued fresh each
# night is a shortlist that drifts, and nothing about tonight's
# reasoning should depend on who is doing the reading.
PICK_BOARDS = ('threes', 'pts')
# THE z GATE IS GONE, AND IT WAS THE CAUSE OF THE OVERS-ONLY BIAS.
#
# z is a standardised view of the matchup multiplier that is ALREADY
# inside the projection. Requiring z >= 1.00 meant "only consider
# spots where the matchup is soft", and a soft matchup inflates the
# projection relative to the line -- so the filter could only ever
# surface overs. Every over then ran into the 46-48% baseline. The
# structural bias was not in the side field being hardcoded; it was
# here.
#
# Kept as a reporting threshold only. Selection is by probability.
PICK_MIN_Z = 1.00
PICK_GATE_Z = False
PICK_MIN_ATT = 25        # threes: attempts actually seen
PICK_MIN_GAMES = 4       # points: games of this season
PICK_MAX = 4

# A points edge labelled "soft from three" belonging to a man who does
# not take threes. Durant's row on 02 Nov was +2.25 via three and he
# attempted none. The threes board's own floor is 25 attempts SEEN,
# which at four to six games is roughly 4-6 a game, so this sits
# deliberately below it: it is the line under which the LABEL is
# misleading, not the line for backing a threes bet.
THIN_ZONE_ATT = 2.5

# Which board measures a zone directly rather than inferring it from
# an argmax over his shot diet. Used to choose between two rows that
# share an (opponent, zone) finding.
ZONE_BOARD = {'three': 'threes'}


def claim_key(r):
    """What this row is actually CLAIMING, as a hashable key.

    Two rows with the same key are one finding wearing two names, and
    backing both is backing one read twice at double the stake.

      pts     -> (opp, via)   the zone FIT says is carrying him. A
                              points row that comes through the three
                              zone is a three-zone read.
      threes  -> (opp, 'three')  a three-zone read by construction, so
                              it collides with a three-via points row
                              on the same defence -- which is the
                              whole case for keying it this way.
      reb/ast -> (opp, mkt)   these score the OPPONENT only. Everyone
                              facing that defence shares one number
                              and only his own volume separates them,
                              so two of them are unambiguously one
                              claim.

    Returned as a tuple so it can be counted; None via (which should
    not happen on a pts row, but has) falls back to the market, which
    is conservative: it collides with other unknowns rather than
    pretending to be distinct.
    """
    mk = r.get('market')
    opp = r.get('opp')
    if mk == 'pts':
        return (opp, (r.get('via') or 'pts'))
    if mk == 'threes':
        return (opp, 'three')
    return (opp, mk)


def zone_vol(r):
    """Shots per game in the zone this row's claim is about.

    The number the 02 Nov tiebreak needed and did not have. Both
    boards can report it in the same unit:

      threes row -> parts['att'], his blended three-point attempt rate
                    scaled to his minutes. Exact; it is the board's
                    own input.
      points row -> parts['three_att'] when via is 'three', computed
                    the same way. For rim / short / mid there is no
                    second board to compare against, so the share of
                    his diet is returned instead and is only ever
                    compared with another points row.

    None when it cannot be determined, and callers must treat None as
    unknown rather than as zero -- a missing field that reads as zero
    volume would silently demote every row it touched, which is how
    the `via` bug behaved last night.
    """
    p = r.get('parts') or {}
    mk = r.get('market')
    if mk == 'threes':
        return p.get('att')
    if mk == 'pts':
        if p.get('three_att') is not None:
            return p.get('three_att')
        return p.get('via_share')
    return None


def zone_vol_str(r):
    """zone_vol in words, WITH ITS UNIT.

    Ranking and display are separate functions on purpose. zone_vol
    returns a bare float that mixes two units -- attempts per game for
    a three claim, a fraction of his shot diet for rim/short/mid --
    which is fine for ordering rows inside one finding and wrong to
    print in a single column. The first draft of this printed
    Carter's 0.3 rim SHARE next to Jabari's 6.7 attempts a game as
    though they were the same quantity, which is the drift-sentence
    mistake again: a number whose meaning lives in a variable name
    rather than on the page.
    """
    p = r.get('parts') or {}
    if r.get('market') == 'threes':
        a = p.get('att')
        return '--' if a is None else f'{a:.1f} 3PA/g'
    if r.get('market') == 'pts':
        if p.get('three_att') is not None:
            return f"{p['three_att']:.1f} 3PA/g"
        v = p.get('via_share')
        return '--' if v is None else f'{v * 100:.0f}% of his shots'
    return '--'


def claim_rank(r):
    """Sort key within one (opponent, zone) finding. Lower is kept.

    1. MEASURED BEFORE INFERRED. For a 'three' finding the threes
       board models his own attempt volume against that defence's
       softness from three. A points row labelled "via three" is an
       argmax over his shot diet -- it says which zone carries FIT,
       not how much he shoots there. Durant's row cleared +2.25 on
       that label and he took no threes; Jabari Smith's threes row
       carried 6.7 attempts a game and went 4 of 5. Keep the measured
       one.
    2. THEN VOLUME, not z. z is driven mostly by the defensive term,
       which is exactly what the two rows share, so it cannot
       separate them.
    3. THEN z, only as a last resort.

    Unknown volume sorts after known volume rather than before: a row
    that cannot show its volume does not get to win on it.
    """
    mk = r.get('market')
    _, zone = claim_key(r)
    inferred = 0 if ZONE_BOARD.get(zone, mk) == mk else 1
    v = zone_vol(r)
    return (inferred, 0 if v is not None else 1,
            -(v or 0.0), -(r.get('zs') or 0.0))


def _fair_price(p):
    """American odds at which probability p breaks even."""
    if not p or p <= 0 or p >= 1:
        return '--'
    dec = 1.0 / p
    return (f'{-100 / (dec - 1):+.0f}' if dec < 2
            else f'{100 * (dec - 1):+.0f}')


def top_picks(boards, min_z=PICK_MIN_Z, dropped=None, day=None,
              require_edge=True):
    """The rows that clear every test above, best first."""
    pt = {(r['player'], r['team']): r for r in points_threes(boards)}
    out, seen = [], set()
    for mk in PICK_BOARDS:
        for r in boards.get(mk) or []:
            key = (r['player'], r['team'])
            if key in seen:
                continue
            # See PICK_GATE_Z. Gating on a positive z is what made this
            # an overs-only model: it only ever looked at spots whose
            # matchup inflated the projection above the line. The whole
            # board is now priced, both tails included.
            if PICK_GATE_Z and r.get('zs', 0) < min_z:
                continue
            if r.get('status'):
                continue
            p = r.get('parts') or {}
            if mk == 'threes' and (p.get('att_seen') or 0) < PICK_MIN_ATT:
                continue
            if mk == 'pts' and (r.get('g') or 0) < PICK_MIN_GAMES:
                continue
            # thin_zone is NOT a gate any more, and making it one was
            # an interaction bug. It means "the matchup LABEL oversells
            # this row", which argued against backing the over. Once
            # both sides are priced, a thin matchup label makes the
            # UNDER more attractive, not less -- and gating on it threw
            # away a 72.6% Durant under on 02 Nov. It now only loses
            # him a claim-key contest, via claim_rank.
            # one claim, not two: a three-zone points edge is already
            # the threes row, which is listed first and wins the key
            if mk == 'pts':
                flag = pt.get(key)
                if flag and flag.get('same_claim'):
                    continue
            if (r.get('mpg_raw') or r.get('mins') or 0) < PICK_MIN_MPG:
                continue
            seen.add(key)
            out.append({
                'player': r['player'], 'team': r['team'], 'opp': r['opp'],
                'market': mk, 'side': 'over', 'zs': r['zs'],
                'base': r['base'], 'mins': r['mins'], 'why': r.get('why'),
                'parts': p,
                # CARRIED, NOT RECOVERED. claim_key() needs the zone,
                # and this dict is rebuilt from scratch rather than
                # being the board row -- so a field that is not copied
                # here is simply absent downstream. Leaving `via` out
                # made every points row key as (opp, 'pts'), which
                # collides with nothing, and the dedupe silently did
                # nothing at all on the one slip it was written for.
                'via': r.get('via'),
                'mpg_raw': r.get('mpg_raw'), 'min_cut': r.get('min_cut'),
                'blowout_risk': r.get('blowout_risk'),
                'tier': r.get('tier'), 'sd': r.get('sd'),
                'sd_n': r.get('sd_n'), 'g': r.get('g'),
                'corroborated': bool(pt.get(key)
                                     and not pt[key].get('same_claim')),
            })
    # WITHIN A FINDING, claim_rank decides; ACROSS findings, z still
    # orders the slip. Sorting by claim_rank first means the row kept
    # for each finding is the best-evidenced one rather than merely
    # the loudest, and the surviving rows are then ordered by z as
    # before.
    # DAY IS REQUIRED TO PRICE ANYTHING. Without it posted_line is
    # never called, no row clears, and the slip comes back EMPTY --
    # which reads exactly like "nothing qualifies tonight". That is
    # the field-not-passed-at-a-boundary failure this file has now
    # shipped twice, so it announces itself instead.
    if require_edge and not day:
        print('\n  !! top_picks called with no day -- no line can be '
              'looked up, so')
        print('  !! NOTHING can clear and the slip will be empty. This '
              'is a bug,')
        print('  !! not a quiet slate.')

    def _drop(r, why, **kw):
        if dropped is None:
            return
        d = {'player': r['player'], 'market': r['market'],
             'team': r['team'], 'opp': r['opp'], 'zs': r['zs'],
             'claim': list(claim_key(r)), 'dup_of': None,
             'vol': zone_vol_str(r), 'kept_vol': '', 'kept_market': '',
             'why_out': why}
        d.update(kw)
        dropped.append(d)

    # --- 1. price every candidate ------------------------------------
    for r in out:
        pr = posted_line(day, r['player'], r['market']) if day else None
        r['line'] = (pr or {}).get('line')
        r['call'] = side_call(r['base'], r['line'], r.get('sd'),
                              r['market'], r.get('tier') or 'secondary')
        if r['call']:
            r['side'] = r['call']['side']
            r['p'] = r['call']['p']
            r['edge'] = r['call']['margin']
        else:
            r['side'] = None
            r['p'] = r['edge'] = None

    # --- 2. drop what cannot be a bet, BEFORE anything competes ------
    # This has to come first. When it came after the dedupe, a row
    # that won its finding on evidence and then failed to clear took
    # the whole finding down with it -- including a 72.6% Durant
    # under that was never looked at.
    live = []
    for r in out:
        if not r.get('call'):
            _drop(r, 'no posted line, so no bet to price')
            continue
        if require_edge and not r['call']['clears']:
            c = r['call']
            _drop(r, (f"{c['side']} is {c['p'] * 100:.1f}%, needs "
                      f"{(c['need'] + c['buffer']) * 100:.1f}% as a "
                      f"{c['tier']} prop"))
            continue
        live.append(r)

    # --- 3. dedupe by (finding, SIDE) --------------------------------
    # SIDE belongs in the key. Durant UNDER points and Jabari OVER
    # threes are both (BOS, three) but they are opposite bets -- one
    # read at double stake is the thing being prevented, and opposite
    # sides of one finding are nearer a hedge than a duplicate.
    live.sort(key=lambda r: (claim_key(r), r['side'], claim_rank(r)))
    best = {}
    for r in live:
        best.setdefault((claim_key(r), r['side']), r)

    # --- 4. order by edge, not by z ----------------------------------
    # z orders SPOTS, which is a different question. On 02 Nov the two
    # disagreed: Durant was the #1 points row by z and, at his line,
    # the best UNDER on the board.
    live.sort(key=lambda r: (-r['edge'], -r['zs']))

    # --- 5. per-game cap, then PICK_MAX ------------------------------
    # Two legs in one game share the pace estimate, the blowout risk
    # and the chance the lineup is not what the report said, so they
    # are not two independent trials whichever sides they are on.
    kept, per, taken = [], {}, set()
    for r in live:
        k = (claim_key(r), r['side'])
        if best.get(k) is not r:
            keep = best.get(k) or {}
            _drop(r, None, dup_of=keep.get('player'),
                  kept_vol=zone_vol_str(keep),
                  kept_market=keep.get('market'))
            continue
        if k in taken:
            continue
        gk = tuple(sorted((r['team'], r['opp'])))
        if per.get(gk, 0) >= PER_GAME:
            _drop(r, f'{PER_GAME} legs already in {gk[0]}/{gk[1]}')
            continue
        per[gk] = per.get(gk, 0) + 1
        taken.add(k)
        r['claim'] = list(claim_key(r))
        kept.append(r)
        if len(kept) >= PICK_MAX:
            break
    return kept


def points_threes(boards):
    """Players on BOTH last-season boards, with the double-count named.

    He asked whether points and threes are a good pairing. They are --
    both rest on a full prior season rather than four games -- with one
    trap that the data can see and a reader cannot.

    For a high-volume three-point shooter, his points edge IS his
    threes edge: FIT says which zone is carrying him, and when that
    zone is 'three' the two boards are reporting one claim in two
    units. Jayson Tatum on 30 Oct was points +0.75 "soft from three"
    and threes +1.31 against the same Chicago defence -- not two
    findings. Treating that as confirmation is how a single read gets
    bet twice.

    FIT already records which zone it came through, so the check is
    free: via == 'three' means the same claim.
    """
    pts = {(r['player'], r['team']): r for r in boards.get('pts', [])}
    thr = {(r['player'], r['team']): r for r in boards.get('threes', [])}
    out = []
    for key in pts.keys() & thr.keys():
        p, t = pts[key], thr[key]
        # THE ZONE AS A FIELD, NOT AS A SUBSTRING OF THE DISPLAY TEXT.
        #
        # This used to recover the zone by stripping 'soft from ' off
        # the why string. Adding the drift sentence to that string
        # turned via into 'three; MIA drtg +1.6 vs last season', so
        # the == 'three' test failed for every player and the board
        # told you Nesmith and Tatum were two independent reads when
        # both were the same claim -- the exact error this function
        # exists to prevent, produced by the function itself.
        #
        # A displayed string is for a reader. Logic that depends on
        # it breaks the next time the sentence changes, and it will
        # break silently, because prose does not raise.
        via = (p.get('via') or '').strip()
        out.append({'player': key[0], 'team': key[1], 'opp': p['opp'],
                    'pts_z': p['zs'], 'thr_z': t['zs'],
                    'pts_why': p.get('why') or '', 'thr_why': t.get('why') or '',
                    'via': via,
                    'same_claim': via.strip() == 'three'})
    out.sort(key=lambda r: -(r['pts_z'] + r['thr_z']))
    return out


def slate_warning(boards, games):
    """Announce it when there is not enough slate to standardise against.

    Not corrected, because there is nothing to correct: a four-team
    slate has no slate. Announced, the way the market and minutes
    terms announce themselves, and left to the reader.
    """
    teams = set()
    for mk in CATS:
        for r in boards.get(mk) or []:
            teams.add(r.get('team'))
            teams.add(r.get('opp'))
    teams.discard(None)
    n = len(teams)
    if n == 0 or n > THIN_SLATE_WARN:
        return n
    # false-signal rate for a top z above +1.00 with n identical teams,
    # from the simulation in the patch note. Interpolated, and labelled
    # as a simulation rather than a measurement.
    rate = {2: 100, 4: 67, 6: 47, 8: 36, 10: 30}.get(
        n - (n % 2), 30)
    hard = n < THIN_SLATE_TEAMS
    tag = 'TOO SMALL TO READ' if hard else 'THIN'
    print(f'\n  !! SLATE IS {tag} -- {games} game(s), {n} team(s).')
    print(f'  !! z is standardised ACROSS THE SLATE, and REBOUNDS and '
          f'ASSISTS score')
    print(f'  !! the opponent only -- one value per defence -- so their '
          f'z comes from')
    print(f'  !! the spread of just {n} numbers.')
    print(f'  !! Simulated with IDENTICAL teams, about {rate}% of '
          f'{n}-team slates still')
    print(f'  !! throw a top z above +1.00. The arithmetic maximum at '
          f'n={n} is '
          f'{((n - 1) / (n ** 0.5)):.2f}.')
    if hard:
        print('  !! Treat the rebounds and assists boards as having NO '
              'information\n  !! tonight. Points and threes degrade '
              'less -- FIT and the attempt\n  !! model are '
              'player-specific -- but their opponent term comes from '
              'the\n  !! same few defences.')
    return n


def show(day, boards, ovl):
    fl = boards.get('_floors') or {}
    cut = boards.get('_cut') or {}
    print(f'\nTHREE MATCHUP BOARDS -- {day}  [three.py {BUILD}]')
    # BEFORE the boards, not after. On a two-game slate the rebounds
    # and assists z-scores are artifacts of dividing by the standard
    # deviation of four numbers, and a reader who has already read the
    # rows will not go back and discount them.
    slate_warning(boards, len({
        tuple(sorted((r.get('team'), r.get('opp'))))
        for mk in CATS for r in (boards.get(mk) or [])
        if r.get('team') and r.get('opp')}))
    if fl:
        print(f"  floors  {fl['mpg']:.0f}+ mpg on every board; "
              f"{fl['poss']:.0f}+ possessions finished and "
              f"{fl['keep']}/8 defensive")
        print(f"          continuity on points only. At most "
              f"{fl.get('per_game', 0)} row(s) per game.")
        print('  off     the |FIT| and under-possession gates -- those '
              'decide what is')
        print('          worth betting, which is a different question '
              'from this one.')
    ij = boards.get('_inj') or {}
    if ij:
        age = ij.get('age_h')
        when = ('age unknown' if age is None
                else f'{age:.1f}h old' if age >= 1
                else f'{age*60:.0f}m old')
        warn = '  <-- STALE, refresh before trusting it' if (
            age is not None and age > 6) else ''
        print(f"  injuries {ij['n']} players carry a status "
              f"({when}){warn}")
        print(f"           {ij['out']} ruled OUT and removed; "
              f"{ij['doubt']} day-to-day or questionable, KEPT and")
        print('           tagged on their row. --healthy-only drops '
              'those too.')
    rl = boards.get('_rel') or {}
    rse = boards.get('_rel_se') or {}
    if rl:
        bits = []
        for k, v in sorted(rl.items()):
            e = rse.get(k)
            bits.append(f'{k} {v*100:.0f}%'
                        + (f'+/-{e*100:.0f}' if e else ''))
        print(f"  shrunk  team environments are {', '.join(bits)} "
              f"reliable, and every z")
        print('          below is scaled by that. READ THE ERROR BARS: '
              'they are wider')
        print('          than the estimates, so these figures move a lot '
              'night to night')
        print('          and a change in them is not news. They will '
              'tighten as the')
        print('          season runs.')
        print('          POINTS is not shrunk: FIT was measured a '
              'different way (546')
        print('          player-games, and it did not predict '
              'production), and stacking')
        print('          a second discount on that would double-count.')
    if cut:
        items = [(k, v) for k, v in sorted(cut.items()) if v]
        for i, (k, v) in enumerate(items):
            print(f"  {'cut' if i == 0 else '':<8}{v:>4}  {k}")

    for mk in CATS:
        rows = boards[mk]
        print(f'\n{"=" * 68}\n{TITLE[mk]}')
        print(f'  {MODEL[mk]}')
        src = SOURCE.get(mk)
        if src == 'both seasons':
            ws = [r['parts'].get('w_now') for r in rows
                  if (r.get('parts') or {}).get('w_now') is not None]
            if ws:
                print(f'  zone profile blends both seasons -- about '
                      f'{statistics.fmean(ws)*100:.0f}% this season at '
                      f'tonight\'s game counts,')
                print('  rising as the season runs. Each row also carries '
                      'the drtg drift.')
            else:
                print('  zone profile blends last season with this one; '
                      'each row carries')
                print('  the drtg drift beside it')
        else:
            key = {'reb': 'share', 'ast': 'ast100'}.get(mk)
            v = (boards.get('_rel') or {}).get(key)
            ap = (boards.get('_rel_applied') or {}).get(key)
            se = (boards.get('_rel_se') or {}).get(key)
            if v is not None and ap is not None:
                tail = (f' +/-{se*100:.0f}' if se else '')
                if ap > v + 0.005:
                    print(f'  built on this season -- measured '
                          f'{v*100:.0f}%{tail} reliable, but scaled by '
                          f'{ap*100:.0f}%:')
                    print(f'  the measurement is noisier than the floor '
                          f'that games played alone')
                    print(f'  justifies, so the floor is what was applied')
                else:
                    print(f'  built on this season -- {v*100:.0f}%{tail} '
                          f'reliable at these game')
                    print(f'  counts, and scaled by {ap*100:.0f}% to match')
        if not rows:
            print('\n  nobody clears the floors in this category tonight.')
            continue
        for matchup, rs in _by_game(rows):
            head = f'\n  {matchup}'
            if mk == 'reb' and rs[0].get('parts'):
                p = rs[0]['parts']
                head += (f"{'':<{max(0, 14 - len(matchup))}}"
                         f"volume {p['vol']:+.2f}   "
                         f"share {p['shr']:+.2f}   ->  {rs[0]['zs']:+.2f}"
                         f"  top lift {(rs[0]['lift'] or 0):+.2f}")
            elif mk == 'ast':
                # the header carries the matchup z, but these boards
                # are ORDERED BY LIFT -- z times his own volume -- so
                # a game with a smaller z can sit higher when the
                # players in it get more assists. Printing the z
                # alone made that read as a sorting bug, so the lift
                # that actually did the sorting is printed too.
                head += (f"{'':<{max(0, 14 - len(matchup))}}"
                         f"{rs[0]['zs']:+.2f}  top lift "
                         f"{(rs[0]['lift'] or 0):+.2f}")
            # APPEND BEFORE PRINTING. This block sat AFTER print(head),
            # so the bracket was built and thrown away -- and because
            # its absence was the one tell I was using to decide
            # whether the market term had loaded, two runs were
            # diagnosed as "market term is off" when the data was
            # there and only the printing was wrong.
            if rs[0].get('implied') is not None:
                head += (f"   [{rs[0]['team']} implied "
                         f"{rs[0]['implied']:.1f}, total "
                         f"{rs[0]['gtotal']:.0f}]")
            print(head)
            unit = {'pts': 'pts', 'threes': '3pm', 'reb': 'reb',
                    'ast': 'ast'}[mk]
            for r in rs:
                val = ('' if r['lift'] is None
                       else f"{r['lift']:>+7.2f}")
                extra = (f"  {r['why']}" if mk in ('pts', 'threes') else '')
                # THE SD, PRINTED. It is computed per player from
                # his own game log, shrunk toward the league curve, and
                # it has never appeared on the page -- so on 05 Nov I
                # screened six legs using SDs I GUESSED, called all of
                # them unplayable, and they went 5-1. The number that
                # decides every probability has to be visible.
                if r.get('sd') is not None:
                    extra += (f"  [sd {r['sd']:.1f}"
                              + (f" n{r['sd_n']}" if r.get('sd_n')
                                 else ' league')
                              + ']')
                if r.get('rest'):
                    extra += f"  [{r['rest']}]"
                if mk == 'threes' and (r.get('parts') or {}).get('att_seen'):
                    extra += f" [{r['parts']['att_seen']:.0f} att seen]"
                tag = (f"  [{str(r['status']).upper()[:12]}]"
                       if r.get('status') else '')
                print(f"    {r['player'][:24]:26}{r['base']:>6.1f} {unit}"
                      f" in {r['mins']:>4.0f} min{r['zs']:>+8.2f}{val}"
                      f"{tag}{extra}")

    print(f'\n{"=" * 68}\nPRA CANDIDATES -- on the points, rebounds '
          f'and assists boards')
    if not ovl:
        print('\n  Nobody tonight. That is the usual answer: a defence '
              'soft in his\n  zones, soft on the glass AND soft in the '
              'passing lanes is rare,\n  and an empty list is the test '
              'working rather than failing.\n  --per-game 0 --top 40 to '
              'see who was closest.')
    else:
        for r in ovl:
            print(f"\n  {r['player']} -- {r['team']} vs {r['opp']}, "
                  f"{r['base_pra']:.1f} P+R+A a game")
            print(f"    points   {r['pts_z']:>+6.2f}   {r['why'][0]}")
            print(f"    rebounds {r['reb_z']:>+6.2f}   {r['why'][1]}")
            print(f"    assists  {r['ast_z']:>+6.2f}   {r['why'][2]}")
            print(f"    {'':9}{r['z_sum']:>+6.2f}   combined")

    # GO-TO FIRST. It needs no line, so it is the panel that has
    # something to say on the four nights out of four when
    # props_lines.py has not run and the slip has been empty.
    show_goto(goto_picks(boards))

    drops = []
    picks = top_picks(boards, dropped=drops, day=day)
    print(f'\n{"=" * 68}\nTONIGHT\'S SLIP')
    if not picks:
        # SAY WHICH EMPTY THIS IS. On 04 Nov the slip came back empty
        # because props_lines.py had never run, so not one row had a
        # line and nothing could be priced -- and the message said
        # "some slates have nobody", which is a different claim and a
        # wrong one. An empty slip has three causes and they need
        # three different actions.
        _no_line = sum(1 for d in (drops or [])
                       if 'no posted line' in (d.get('why_out') or ''))
        _n_drop = len(drops or [])
        if _n_drop and _no_line == _n_drop:
            print('\n  NO LINES, so nothing could be priced. This is '
                  'NOT a quiet slate --\n  every candidate was dropped '
                  'for want of a posted line.')
            print(f'  {_no_line} row(s) had none. Run:  python3 '
                  f'props_lines.py   then re-run.')
        elif _no_line:
            print(f'\n  Nothing clears the bar. {_no_line} of '
                  f'{_n_drop} candidate(s) had NO posted\n  line at all '
                  f'-- run props_lines.py and some of those may price.')
        else:
            print('\n  Nothing clears the bar tonight, and every '
                  'candidate WAS priced.\n  That is a result rather '
                  'than a gap: the side has to beat its\n  tier\'s '
                  'break-even by the buffer, and on a lot of slates '
                  'nobody\n  does. The held-back list below says what '
                  'each one came to.')
    else:
        for i, p in enumerate(picks, 1):
            unit = {'threes': 'threes', 'pts': 'points'}[p['market']]
            c = p.get('call') or {}
            side = (c.get('side') or 'over').upper()
            print(f"\n  {i}. {p['player']} -- {side} {p['line']} "
                  f"{unit}, {p['team']} vs {p['opp']}")
            # THE PROBABILITY AND THE PRICE IT NEEDS, TOGETHER.
            # A hit rate without the price it was bought at is not a
            # result. 70% at -250 loses; 48% at +130 wins.
            print(f"     {c.get('p', 0) * 100:.1f}% by the model "
                  f"({c.get('dist')}, sd {p.get('sd')}, "
                  f"{p.get('sd_n')} games) -- needs "
                  f"{(c.get('need', 0) + c.get('buffer', 0)) * 100:.1f}% "
                  f"as a {c.get('tier')} prop")
            print(f"     worth taking at {_fair_price(c.get('p'))} or "
                  f"better; projects {p['base']:.1f} in "
                  f"{p['mins']:.0f} min")
            if p.get('min_cut'):
                print(f"     minutes cut {p['min_cut']:+.2f} from "
                      f"{p.get('mpg_raw')} -- "
                      f"{(p.get('blowout_risk') or 0) * 100:.0f}% blowout "
                      f"risk on this spread")
            print(f"     {p['zs']:+.2f} on the slate; {p['why']}")
            if p['corroborated']:
                print('     his points and threes reads are SEPARATE, '
                      'so they corroborate')
        if drops:
            print(f'\n  HELD BACK -- {len(drops)} row(s) that repeat a '
                  f'finding already on the slip:')
            for d in drops:
                if d.get('why_out'):
                    print(f"    {d['player']} ({d['market']}, "
                          f"{d['zs']:+.2f}) -- {d['why_out']}")
                else:
                    print(f"    {d['player']} ({d['market']}, "
                          f"{d['zs']:+.2f}, {d['vol']}) -- same "
                          f"{d['claim'][0]} {d['claim'][1]} read as "
                          f"{d['dup_of']} ({d['kept_market']}, "
                          f"{d['kept_vol']})")
            print('    Two men expressing one defensive finding is one '
                  'read at double\n    the stake, not two reads. The '
                  'kept row is the best-EVIDENCED\n    one, not the '
                  'highest z: z is driven by the defensive term, which\n'
                  '    is exactly what these rows share, so it cannot '
                  'separate them.')
        print('\n  THESE ARE PRICED. Each row is the side the '
              'distribution favours\n  at the posted line, not the '
              'softest matchup -- those are different\n  questions and '
              'on 02 Nov they pointed opposite ways (Durant was the\n'
              '  #1 points row and, at his line, an UNDER).')
        print('\n  HIT RATE IS HALF A RESULT. The percentage is only '
              'worth having at\n  the price beside it: 70% at -250 '
              'loses, 48% at +130 wins. If the\n  book is worse than '
              'the price shown, it is not a bet.')
        print('\n  And the matchup term behind the ranking has NOT been '
              'shown to\n  predict production at 959 player-games. The '
              'probability comes from\n  the projection and his own '
              'measured spread; the matchup only orders\n  which spots '
              'got looked at.')

    pt = points_threes(boards)
    print(f'\n{"=" * 68}\nPOINTS + THREES -- the two player-specific boards')
    if not pt:
        print('\n  Nobody on both tonight.')
    else:
        print('\n  Both read the defence by zone, blending last season '
              'with this\n  one, and both know which man is shooting. '
              'Read the flag on each\n  row before treating agreement '
              'as confirmation.')
        for r in pt:
            print(f"\n  {r['player']} -- {r['team']} vs {r['opp']}")
            print(f"    points  {r['pts_z']:>+6.2f}   {r['pts_why']}")
            print(f"    threes  {r['thr_z']:>+6.2f}   {r['thr_why']}")
            if r['same_claim']:
                print('    ONE CLAIM, NOT TWO. His points edge comes from '
                      'the three zone,')
                print('    which is the threes edge with a different name. '
                      'Agreement here')
                print('    is not confirmation -- it is the same number '
                      'counted twice.')
            else:
                print(f"    Two separate reads: his points edge is from "
                      f"{r['via']}, not three,")
                print('    so these corroborate rather than repeat.')

    print(f'\n{"=" * 68}\nREADING THESE')
    print('  The number in the middle column is this matchup against '
          'the spread\n  of tonight\'s other matchups, so +1.5 means '
          'top of the slate, not\n  top of the league. LIFT is what the '
          'spot is worth to him given his\n  own volume -- an ordering, '
          'not a count: the constant behind it is a\n  placeholder until '
          'it is calibrated the way FIT was.')
    print('\n  BASE is what he does anyway, now scaled by the market\'s '
          'own view of\n  tonight: the team\'s implied total for points, '
          'assists and threes,\n  the game total for rebounds. That one '
          'number already contains the\n  opponent\'s defence, the pace, '
          'the injuries and the rest, measured by\n  the deepest market '
          'rather than from six games of our own.')
    print('\n  IT IS NOT AN EDGE. Using the market\'s environment estimate '
          'is using\n  the best estimate available. These rows say "good '
          'spot for production\n  tonight", which has never been the same '
          'claim as "mispriced".')
    print('\n  On REBOUNDS, volume and share are different claims. '
          'Volume holds\n  whoever is on the floor. Share says the '
          'opponent will not contest,\n  which depends on who they rest '
          'and who is hurt, and it goes away\n  the night their bigs are '
          'healthy. A row standing on share alone is\n  the weaker half '
          'of the model.')
    print('\n  Points and threes are the player-specific reads -- FIT '
          'knows his\n  shot diet, and the threes board scores his own '
          'attempts against\n  that defence\'s softness from three. '
          'Rebounds and assists score the\n  OPPONENT only, so everyone '
          'facing that defence shares a number and\n  only volume '
          'separates them.')
    print('\n  NO PRICE WAS CONSULTED. Whether any of these is worth '
          'betting is\n  the next question and it needs a line.')


# --- whose team is that, actually? -----------------------------------
#
# The 30 Oct boards put lebron james on PHI, ivica zubac on IND, naz
# reid on CHA and trae young on WSH. Some of those may be real --
# rosters move, and I have twice this week asserted a player's team
# from memory and been wrong. So this does not ask me. It asks the
# cache.
#
# A player who appears under two team codes this season is either
# traded (fine, and the recent team is the right one) or
# mis-attributed by the scrape (a bug, and every board row built on
# him is wrong, including the opponent, which decides his entire
# matchup). The two look identical in the board output and completely
# different underneath, so they have to be separated by the minutes:
# a real trade leaves a block of games at each team, a bad scrape
# leaves a handful of stray rows against a wall of correct ones.
def audit_teams(logs_days, min_share=0.80):
    by = collections.defaultdict(lambda: collections.defaultdict(
        lambda: {'g': 0, 'min': 0.0, 'days': []}))
    for day in sorted(logs_days):
        for r in logs_days[day]:
            m = r.get('min') or 0
            if m < MIN_MIN:
                continue
            d = by[norm_player(r.get('player'))][code(r.get('team'))]
            d['g'] += 1
            d['min'] += m
            d['days'].append(day)
    multi = []
    for name, teams in by.items():
        if len(teams) < 2:
            continue
        tot = sum(t['min'] for t in teams.values())
        rows = sorted(teams.items(), key=lambda kv: -kv[1]['min'])
        top_share = (rows[0][1]['min'] / tot) if tot else 0.0
        multi.append({'player': name, 'total_min': round(tot, 1),
                      'share': round(top_share, 3),
                      'suspect': top_share >= min_share,
                      'teams': [{'team': t, 'g': d['g'],
                                 'min': round(d['min'], 1),
                                 'first': d['days'][0], 'last': d['days'][-1]}
                                for t, d in rows]})
    multi.sort(key=lambda r: (not r['suspect'], -r['total_min']))
    return multi


def minute_totals(gs, tol=20.0):
    """Team-games whose player minutes are out of line with the rest.

    THE CASE --audit-teams CANNOT SEE
      A player logged under two teams shows up there. A player logged
      under ONE team that is the WRONG team does not -- he looks
      perfectly consistent, and every board row built on him carries
      the wrong opponent, which is the whole matchup.

      Five players for 48 minutes is 240 player-minutes a game. But
      comparing against 240 is a trap: if season_logs drops deep-bench
      rows, EVERY team-game sits under it, the whole list lights up
      and the check tells you nothing except that the cache is
      filtered. I wrote that version first and the fixture showed 112
      of 112 team-games "under", which is not a finding, it is a
      threshold meeting a filtered cache.

      So the reference is the cache's own median team-game. A uniform
      shortfall cancels out; a team carrying a player it should not
      stands out ABOVE its peers and the team missing him stands
      BELOW. The median against 240 is reported separately, because
      that gap is worth knowing on its own -- it says how much of each
      game this cache never stored.
    """
    tots = []
    for g in gs:
        for t, rec in (g.get('teams') or {}).items():
            m = rec.get('min') or 0
            if m > 0:
                tots.append((m, g, t))
    if len(tots) < 8:
        return {'ok': False, 'n': len(tots)}
    vals = sorted(x[0] for x in tots)
    med = vals[len(vals) // 2]
    bad = []
    for m, g, t in tots:
        off = m - med
        if abs(off) > tol:
            bad.append({'day': g.get('day'), 'team': t, 'min': round(m, 1),
                        'off': round(off, 1),
                        'opp': (g['home'] if g['away'] == t else g['away'])})
    bad.sort(key=lambda r: -abs(r['off']))
    return {'ok': True, 'n': len(tots), 'median': round(med, 1),
            'vs_240': round(med - 240, 1), 'bad': bad, 'tol': tol}


def show_minutes(res):
    print('\nMINUTE TOTALS -- is any team-game out of line with the rest?')
    if not res.get('ok'):
        print(f'  only {res.get("n", 0)} team-game(s) in the cache. '
              f'Not enough to compare.')
        return
    print(f"  {res['n']} team-games. Median total: {res['median']:.1f} "
          f"player-minutes\n  ({res['vs_240']:+.1f} against the 240 a "
          f"regulation game actually contains).")
    if abs(res['vs_240']) > 5:
        print('  That gap is the cache, not the games: season_logs is not '
              'storing\n  every player who appeared. Fine for rates, '
              'worth knowing before\n  anyone treats these totals as '
              'team totals.')
    bad = res['bad']
    if not bad:
        print(f"\n  No team-game sits more than {res['tol']:.0f} minutes "
              f"off that median.\n  Nothing here suggests a player on the "
              f"wrong roster.")
        return
    print(f"\n  {len(bad)} team-game(s) off the median by more than "
          f"{res['tol']:.0f}:\n")
    print(f"    {'DAY':10}{'TEAM':6}{'OPP':6}{'MIN':>8}{'VS MED':>9}")
    for r in bad[:25]:
        print(f"    {r['day']:10}{r['team']:6}{r['opp']:6}"
              f"{r['min']:>8.1f}{r['off']:>+9.1f}")
    over = sum(1 for r in bad if r['off'] > 0)
    print(f'\n  {over} above the median, {len(bad) - over} below. A team '
          f'ABOVE is\n  carrying minutes that belong to someone else; one '
          f'BELOW is missing a\n  player. The two halves of the same '
          f'mistake land on the same DAY --\n  look for a pair before '
          f'reading either on its own, because a blowout\n  with a short '
          f'bench also moves this number.')


def show_audit(multi):
    print('\nTEAM ATTRIBUTION -- players logged under more than one team')
    if not multi:
        print('  every player sits under exactly one team. Nothing to '
              'separate.')
        return
    susp = [r for r in multi if r['suspect']]
    print(f'  {len(multi)} player(s) under 2+ team codes; '
          f'{len(susp)} look like scrape errors rather than trades.\n')
    for r in multi:
        tag = 'SUSPECT' if r['suspect'] else 'trade-like'
        print(f"  {r['player'][:28]:30}{tag:12}"
              f"{r['total_min']:>8.0f} min total")
        for t in r['teams']:
            print(f"      {t['team']:5}{t['g']:>4} g{t['min']:>8.0f} min   "
                  f"{t['first']}-{t['last']}")
    print('\n  SUSPECT means one team holds 80%+ of his minutes and the '
          'other\n  holds a sliver. A real mid-season trade does not look '
          'like that;\n  a handful of rows scraped onto the wrong side '
          'of a box score does.\n  Any board row built on the minority '
          'team has the wrong OPPONENT,\n  which means the wrong matchup '
          'and the wrong z -- not a small error.')
    print('\n  A player under exactly one team never reaches this list, '
          'so a\n  wholly mis-attributed player is invisible here. For '
          'those, check\n  the board output against the schedule: '
          'why_thin.py already asserts\n  every candidate is on a team '
          'that is playing.')


PUBLISH_TOP = 5


# card.py's label for each market, which is also what props_lines.py
# writes and what slate.py settles under. Three files already agree
# on these strings; a fourth spelling would be a fourth bug.
PICK_LABEL = {'pts': 'Points Scored', 'threes': '3-Pointers Made',
              'reb': 'Total Rebounds', 'ast': 'Total Assists'}
SLATE_MARKET = {'pts': 'points', 'threes': 'threes',
                'reb': 'rebounds', 'ast': 'assists'}


def posted_line(day, player, market):
    """The line the books have for this man tonight, or None."""
    if not os.path.exists(PRICES):
        return None
    want = PICK_LABEL.get(market)
    best = None
    for ln in open(PRICES):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if (r.get('day') != day or r.get('market') != want
                or norm_player(r.get('player')) != player):
            continue
        if r.get('line') is not None:
            best = r             # last wins: the file is append-only
    return best


def log_picks(day, picks, path=None, odds=-110):
    """Write the slip into slate.jsonl so it grades W/L tomorrow.

    THE LOOP THAT WAS MISSING
      boards.jsonl records every scored row, but with no line and no
      side -- deliberately, because the board is price-free. That
      makes it gradeable against a baseline and NOT gradeable as a
      bet. So two nights of named picks produced no W/L at all:
      check_grades found nothing to settle because nothing had been
      written as a pick.

      A slip entry needs a line. This joins each pick to the posted
      line from external_picks.jsonl at log time and writes it in the
      shape slate.py already settles. A pick with no posted line is
      written anyway, flagged unpriced, because the matchup claim is
      still testable against the box score even when no book quoted
      him -- it just cannot be scored as a bet.
    """
    path = path or SLATE
    seen = set()
    if os.path.exists(path):
        for ln in open(path):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            seen.add((r.get('day'), r.get('subject'), r.get('market')))
    n = dup = unpriced = 0
    with open(path, 'a') as fh:
        for p in picks:
            mk = SLATE_MARKET.get(p['market'], p['market'])
            key = (day, p['player'], mk)
            if key in seen:
                dup += 1
                continue
            pr = posted_line(day, p['player'], p['market'])
            row = {
                'day': day, 'tier': 'board', 'subject': p['player'],
                'team': p['team'], 'opp': p['opp'], 'market': mk,
                'side': p.get('side') or 'over',
                'line': (pr or {}).get('line'),
                'odds': odds if pr else None,
                'proj': p['base'], 'z': p['zs'],
                'why': p.get('why'), 'build': BUILD,
                'unpriced': pr is None,
                'logged_at': (datetime.datetime.now().astimezone()
                              .isoformat()),
                # THE SETTLER'S CONTRACT, not the pick's. slate.py's
                # summarise() does `if r['result']` with a bracket, so
                # a row without the key raises KeyError and record.json
                # stops rebuilding for the whole file -- not just for
                # this row. card.py has written these four since it was
                # written; log_picks did not, and that is what broke
                # publish.sh the first night the slip was logged.
                'result': None, 'actual': None, 'units': None,
                'graded_at': None,
            }
            if pr is None:
                unpriced += 1
            fh.write(json.dumps(row) + '\n')
            seen.add(key)
            n += 1
    print(f'\nlogged {n} pick(s) to {path}'
          + (f' ({dup} already there)' if dup else ''))
    if unpriced:
        print(f'  {unpriced} had NO posted line and are flagged unpriced: '
              f'gradeable\n  against the box score, not scoreable as a bet. '
              f'Run props_lines.py\n  before the slip if you want them '
              f'priced.')
    if n - unpriced:
        print(f'  {n - unpriced} carry a line and settle W/L tomorrow '
              f'under tier "board".')
    return n


def _days(path, key='day'):
    """Every distinct day present in a jsonl, with its row count."""
    out = {}
    if not os.path.exists(path):
        return out
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        d = r.get(key)
        if d:
            out[d] = out.get(d, 0) + 1
    return out


def log_audit(today, boardlog=None, slate=None, back=10,
              logs_days=None):
    """Say out loud what is and is not recorded, every single run.

    THE POINT IS THAT IT PRINTS WHEN NOTHING IS WRONG.
    A check that only speaks up on failure cannot be distinguished
    from a check that is not running, and this codebase has now been
    bitten by that twice -- build ids written to stderr where nobody
    read them, and an installer that died after re-exec'ing itself so
    the run looked real. Two lines a night is a cheap price for
    knowing the loop is closed.
    """
    bl = _days(boardlog or BOARDLOG)
    sl_rows = {}
    sp = slate or SLATE
    if os.path.exists(sp):
        for ln in io.open(sp, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            d = r.get('day')
            if d:
                sl_rows.setdefault(d, []).append(r)

    days = sorted(set(bl) | set(sl_rows), reverse=True)[:back]
    print('\nLOG')
    if not days:
        print('  nothing logged yet. --publish or --log writes tonight.')
        return
    last = days[0]
    print(f'  last day recorded {last}'
          f'  ({bl.get(last, 0)} board row(s), '
          f'{len(sl_rows.get(last) or [])} pick(s))')

    noslip = [d for d in days
              if bl.get(d) and not sl_rows.get(d) and d != today]
    if noslip:
        print(f'  {len(noslip)} day(s) with board rows but NO slip logged -- '
              f'not gradeable as bets:')
        print('    ' + ', '.join(noslip))
    ungraded = [d for d in days if d != today
                and any(r.get('result') is None
                        and r.get('line') is not None
                        for r in (sl_rows.get(d) or []))]
    if ungraded:
        print(f'  {len(ungraded)} past day(s) with picks still UNGRADED:')
        print('    ' + ', '.join(ungraded))
        # SPLIT THE REASON, because one of them has a different fix and
        # the other will clear on its own. A row for a man who never
        # dressed can never be graded, so without this the line above
        # prints every night forever and stops being read.
        dnp_days = []
        if logs_days:
            for d in ungraded:
                try:
                    if classify_picks(logs_days, d)['dnp']:
                        dnp_days.append(d)
                except Exception:
                    pass
        if dnp_days:
            print(f'    {len(dnp_days)} of those hold picks for men who '
                  f'NEVER DRESSED and can\n    never be graded:  '
                  f'python3 three.py --void-dnp {dnp_days[0]}')
        rest = [d for d in ungraded if d not in dnp_days]
        if rest:
            print('    the rest are waiting on a box score:  '
                  'python3 slate.py grade --day ' + rest[0])
    if not noslip and not ungraded:
        print('  no gaps and nothing ungraded.')


def classify_picks(logs_days, day, path=None):
    """Every slate.jsonl pick for one day, sorted into four states.

      graded   -- already has a result
      played   -- he is in the box score, grading is slate.py's job
      dnp      -- we have his team's game and he is NOT in it
      no_game  -- we do not have that team's game at all

    dnp and no_game look identical from inside slate.py (both are
    "player not found") and must not be treated alike: voiding a
    no_game row would turn missing data into a settled zero.
    """
    path = path or SLATE
    day_rows = logs_days.get(day) or []
    teams = {code(r.get('team')) for r in day_rows if r.get('team')}
    seen = {(norm_player(r.get('player')), code(r.get('team')))
            for r in day_rows}
    out = {'graded': [], 'played': [], 'dnp': [], 'no_game': []}
    if not os.path.exists(path):
        return out
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') != day:
            continue
        if r.get('result'):
            out['graded'].append(r)
            continue
        key = (norm_player(r.get('subject')), code(r.get('team')))
        if key in seen:
            out['played'].append(r)
        elif key[1] in teams:
            out['dnp'].append(r)
        else:
            out['no_game'].append(r)
    return out


def void_dnp(logs_days, day, path=None):
    """Mark picks for men who never dressed as void, 0 units.

    Only the 'dnp' bucket -- his team's game is in our logs and he is
    not in it. A 'no_game' row is missing data, not a settled result,
    and is left alone with a note.
    """
    path = path or SLATE
    st = classify_picks(logs_days, day, path)
    # ALWAYS SHOW THE CLASSIFICATION. "no DNP picks to void" is
    # indistinguishable from "found no picks at all", and on 05 Nov
    # that is exactly the ambiguity that left the Vassell row
    # unexplained -- four empty buckets print the same message as
    # four graded rows.
    tot = sum(len(v) for v in st.values())
    print(f'\n{day}: {tot} logged pick(s) in {path}')
    for k in ('graded', 'played', 'dnp', 'no_game'):
        if st[k]:
            print(f'  {len(st[k]):>3}  {k}: '
                  + ', '.join(str(r.get('subject')) for r in st[k][:6])
                  + (' ...' if len(st[k]) > 6 else ''))
    if not tot:
        print('  NOTHING LOGGED for that day. If you expected picks, '
              'they were\n  never written -- check whether '
              'props_lines.py had run.')
        return 0
    if not st['dnp']:
        print(f'\nno DNP picks to void for {day}.')
        if st['no_game']:
            print(f"  {len(st['no_game'])} row(s) have no game in our "
                  f"logs at all -- that is missing data, not a DNP:")
            for r in st['no_game']:
                print(f"    {r.get('subject')} ({r.get('team')})")
            print('  run edges.py --build to pull the box scores first.')
        return 0
    targets = {(r.get('subject'), r.get('market')) for r in st['dnp']}
    rows, n = [], 0
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if (r.get('day') == day and not r.get('result')
                and (r.get('subject'), r.get('market')) in targets):
            r['result'] = 'void'
            r['actual'] = None
            r['units'] = 0.0
            r['void_reason'] = 'did not dress'
            r['graded_at'] = (datetime.datetime.now().astimezone()
                              .isoformat())
            n += 1
            print(f"  void  {r.get('subject')} {r.get('market')} "
                  f"-- no box score, his team's game is logged")
        rows.append(r)
    tmp = path + '.void'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    os.replace(tmp, path)
    print(f'\nvoided {n} pick(s) for {day} at 0 units.')
    print('  A pick on a man who never dressed is not a loss and not a '
          'win.\n  It is also not evidence about the matchup logic, so '
          'it stays out\n  of the calibration either way.')
    print('  THIS IS ALSO A FEED MEASUREMENT: he was KEPT by the injury '
          'filter\n  and did not play. That filter silently did nothing '
          'for weeks once\n  already, so the count is worth watching.')
    return n


def repair_log(path=None):
    """Backfill the settler keys on rows written before 2026-11-02b.

    slate.py reads r['result'] with a bracket on EVERY row, so one row
    missing the key takes down record.json for the entire file. The
    rows logged on 01 and 02 Nov were written without it.

    Everything already present is left untouched -- this only adds
    keys that are absent, never overwrites a value, so running it on a
    settled file cannot un-settle anything.
    """
    path = path or SLATE
    if not os.path.exists(path):
        print(f'no slate at {path}')
        return 0
    SHELL = ('result', 'actual', 'units', 'graded_at')
    rows, fixed, bad = [], 0, 0
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            bad += 1
            continue
        miss = [k for k in SHELL if k not in r]
        if miss:
            for k in miss:
                r[k] = None
            fixed += 1
            print(f"  + {r.get('day')} {r.get('subject')} "
                  f"{r.get('market')}: added {', '.join(miss)}")
        rows.append(r)
    if not fixed:
        print(f'{len(rows)} row(s) checked, nothing to repair.')
        return 0
    tmp = path + '.repair'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    os.replace(tmp, path)
    print(f'\nrepaired {fixed} of {len(rows)} row(s) in {path}'
          + (f' ({bad} unparseable line(s) dropped)' if bad else ''))
    print('  record.json should rebuild now. Re-run ./publish.sh')
    return fixed


def log_boards(day, boards, path=None, goto=None):
    """Write every scored row so tomorrow can ask whether it was right.

    THE WHOLE ARGUMENT WAS "WE WILL CALIBRATE FROM WHAT WE SEE HAPPEN".
    Nothing was recording what we saw, so each night's ranking was
    printed, read and lost.

    UPSERT, NOT APPEND-IF-ABSENT. On 04 Nov the board ran once with the
    market term off and again with it on; the second run's rows were
    rejected as duplicates and the day kept the unscaled ones. A later
    row now REPLACES a stored one when it is better instrumented --
    market term on where the stored row had it off. Anything else is
    still a duplicate, so re-running twice in the same state changes
    nothing.
    """
    path = path or BOARDLOG
    # (market, player, team) -> 1..5 for tonight's go-to five. The
    # five are board rows, so they are stamped in the log they are
    # already in rather than copied into a second file.
    rank = {}
    for i, g in enumerate(goto or [], 1):
        rank[(g.get('market'), g.get('player'), g.get('team'))] = i
    stored = {}
    order = []
    if os.path.exists(path):
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            k = (r.get('day'), r.get('market'), r.get('player'),
                 r.get('team'))
            if k not in stored:
                order.append(k)
            stored[k] = r

    n = dup = upg = upg_sd = stamped = 0
    for mk in CATS:
        for r in boards.get(mk) or []:
            k = (day, mk, r['player'], r['team'])
            row = {
                'day': day, 'market': mk, 'player': r['player'],
                'team': r['team'], 'opp': r['opp'],
                'base': r['base'], 'mins': r['mins'], 'g': r['g'],
                'z': r['z'], 'zs': r['zs'], 'lift': r.get('lift'),
                'rfac': r.get('rfac'), 'via': r.get('via'),
                'parts': r.get('parts') or {},
                'market_on': r.get('market_on'),
                'minutes_on': r.get('minutes_on'),
                'spread': r.get('spread'),
                # THE ENVIRONMENT THIS PROJECTION ASSUMED. Both are
                # already on the board row and both scale BASE, and
                # neither has ever been written down -- so the one
                # term that moves every pick on a slate at once was
                # the only one with no record. See --environment.
                'implied': r.get('implied'), 'gtotal': r.get('gtotal'),
                # CARRIED, NOT RECOVERED. goto_history computes
                # zact = (actual - base) / sd, and a board row has an
                # sd (it is measured per player and shrunk toward the
                # league curve) -- but this log row never carried it,
                # so zact would have read None on every row forever
                # while looking like it worked. Fifth time a field has
                # gone missing at a boundary in this file.
                'sd': r.get('sd'), 'sd_n': r.get('sd_n'),
                # THE FACTOR THIS ROW WAS PRICED WITH, so fit_sd_props
                # can divide it out. Without it a second --fit-sd on
                # corrected rows would square the correction.
                'sd_inf': sd_inflation().get(mk, 1.0),
                'goto_rank': rank.get((mk, r['player'], r['team'])),
                'build': BUILD,
            }
            old = stored.get(k)
            if old is None:
                stored[k] = row
                order.append(k)
                n += 1
            elif row['market_on'] and not old.get('market_on'):
                stored[k] = row
                upg += 1
            elif (row.get('sd_inf') or 1.0) != (old.get('sd_inf') or 1.0):
                # THE SD BASIS CHANGED, so this is a different
                # quantity and not a noisier copy -- the same
                # argument as the market term above.
                #
                # 16 Nov: --fit-sd --apply wrote x1.26/x1.30/x1.06/
                # x1.12, the board was re-run, and every row came back
                # "0 logged, 59 already there". The rows WERE new --
                # they carried the widened sd -- but the only upgrade
                # path was market_on, so they were dropped as
                # duplicates and the stored rows kept the uninflated
                # sd. That would have made the next --fit-sd measure
                # against a stale denominator while the slip was
                # priced on the new one.
                stored[k] = row
                upg_sd += 1
            else:
                # A duplicate row still gets the rank if the stored
                # one predates the stamp. Adding a field the row did
                # not have is strictly more information, which is not
                # the same as REPLACING a row -- so it is counted
                # separately from the market-term upgrade above.
                if row.get('goto_rank') and not old.get('goto_rank'):
                    old['goto_rank'] = row['goto_rank']
                    stamped += 1
                dup += 1

    if n or upg or upg_sd or stamped:
        tmp = path + '.log'
        with io.open(tmp, 'w', encoding='utf-8') as fh:
            for k in order:
                fh.write(json.dumps(stored[k]) + '\n')
        os.replace(tmp, path)

    print(f'\nlogged {n} board row(s) to {path}'
          + (f' ({dup} already there)' if dup else ''))
    if rank:
        print(f'  marked {len(rank)} of them as tonight\'s GO-TO five'
              + (f', {stamped} stamped onto rows already logged'
                 if stamped else '')
              + ' -- tomorrow can grade them.')
    if upg:
        print(f'  REPLACED {upg} row(s) that had been logged with the '
              f'market term OFF.')
        print('  Those were unscaled projections -- a different quantity, '
              'not a\n  worse estimate of the same one -- and they would '
              'have been settled\n  against tonight\'s box score as if '
              'they were the model.')
    # SEPARATE COUNTER, SEPARATE MESSAGE. These two replacements have
    # different causes, and one shared message said "market term OFF"
    # over an SD change -- a correct action with the wrong reason
    # printed over it, which is worse than no message.
    if upg_sd:
        print(f'  REPLACED {upg_sd} row(s) priced on a different SD '
              f'basis.')
        print('  --fit-sd changed the inflation factor, so the stored '
              'rows carried a\n  different denominator from the one '
              'tonight priced with. Keeping them\n  would have made '
              'the next --fit-sd measure against a stale SD.')
    print('  these settle against the box score tomorrow. No line is '
          'needed: the\n  claim is that a better matchup lifts him '
          'above his own baseline,\n  which the box score can answer '
          'on its own.')
    return n + upg + upg_sd


def drop_board_day(day, path=None):
    """Remove every board row for one day, so it can be re-logged."""
    path = path or BOARDLOG
    if not os.path.exists(path):
        print(f'no board log at {path}')
        return 0
    keep, gone = [], 0
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') == day:
            gone += 1
            continue
        keep.append(r)
    if not gone:
        print(f'nothing logged for {day}.')
        return 0
    tmp = path + '.drop'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        for r in keep:
            fh.write(json.dumps(r) + '\n')
    os.replace(tmp, path)
    print(f'dropped {gone} board row(s) for {day}; {len(keep)} remain.')
    print('  Re-run the board to log it again.')
    return gone


GOTO_FIELD = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb',
              'ast': 'ast'}


def environment_report(logs_days, path=None):
    """What the market said the slate would score, against what it did.

    Returns a row per logged day. The assumed total is the market's
    game total as it stood when the board was built; the actual is
    summed from the box score.

    THIS IS A MEASUREMENT, NOT A CORRECTION. The whole reason it
    exists is that nothing was recorded, so there is no basis yet for
    adjusting anything -- and fitting a multiplier to a week of this
    would be LIFT_K again.
    """
    path = path or BOARDLOG
    if not os.path.exists(path):
        return []
    # day -> game key -> assumed total, where a game key is the two
    # teams sorted, so both sides of a game collapse to one entry
    # rather than counting the total twice.
    assumed = {}
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        gt, day = r.get('gtotal'), r.get('day')
        if not gt or not day or not r.get('team') or not r.get('opp'):
            continue
        assumed.setdefault(day, {})[
            tuple(sorted((code(r['team']), code(r['opp']))))] = gt

    out = []
    for day in sorted(assumed, reverse=True):
        pts = {}
        for x in logs_days.get(day) or []:
            t = code(x.get('team'))
            # `is None`, not falsy -- a 0-point line is a real line.
            p = x.get('pts')
            if t and p is not None:
                pts[t] = pts.get(t, 0) + p
        rows = []
        for (a, b), gt in sorted(assumed[day].items()):
            if a in pts and b in pts:
                rows.append((f'{a}-{b}', gt, pts[a] + pts[b]))
        if not rows:
            out.append({'day': day, 'games': 0, 'assumed': None,
                        'actual': None, 'diff': None, 'rows': []})
            continue
        am = statistics.fmean(r[1] for r in rows)
        ac = statistics.fmean(r[2] for r in rows)
        out.append({'day': day, 'games': len(rows),
                    'assumed': round(am, 1), 'actual': round(ac, 1),
                    'diff': round(ac - am, 1),
                    'pct': round(100 * (ac - am) / am, 1) if am else None,
                    'rows': rows})
    return out


def show_environment(rep):
    print('\n' + '=' * 68)
    print('ENVIRONMENT -- what the market said the slate would score, '
          'against\nwhat it did. BASE is scaled by this, so it moves '
          'every pick on a\nnight together.\n')
    if not rep:
        print('  Nothing logged yet. Rows written from build '
              '2026-11-08a carry the\n  assumed total; earlier rows '
              'do not and cannot be recovered.')
        return
    graded = [r for r in rep if r['diff'] is not None]
    print(f"  {'day':<10}{'games':>6}{'assumed':>9}{'actual':>8}"
          f"{'diff':>8}{'pct':>7}")
    for r in rep[:14]:
        if r['diff'] is None:
            print(f"  {r['day']:<10}{'-':>6}{'no box score yet':>32}")
            continue
        print(f"  {r['day']:<10}{r['games']:>6}{r['assumed']:>9.1f}"
              f"{r['actual']:>8.1f}{r['diff']:>+8.1f}{r['pct']:>+6.1f}%")
    if len(graded) >= 2:
        d = statistics.fmean(x['diff'] for x in graded)
        n = sum(x['games'] for x in graded)
        print(f'\n  Over {len(graded)} day(s), {n} game(s): the slate '
              f'played {d:+.1f} points\n  against what the market '
              f'assumed.')
        print('  NOT A CORRECTION FACTOR. The market total is still '
              'the best single\n  estimate available, and a '
              'multiplier fitted to a week of this is\n  LIFT_K for '
              'the third time. Read it again near 15 days.')
    else:
        print('\n  One day is not a reading. This needs ~15 before it '
              'says anything.')


def goto_history(logs_days, path=None):
    """Every go-to pick ever published, against what happened.

    Same resolution settle_day uses -- (norm_player, code(team)) into
    that day's box scores, with the market -> box-field map -- because
    a second way of answering "what did he actually get" is a second
    way to get it wrong.

    `beat` is actual >= base: did he clear the number the model
    projected. There is no line and so no W/L; see the patch note for
    why this measure punishes an accurate projection, and why zact is
    carried alongside it.
    """
    path = path or BOARDLOG
    if not os.path.exists(path):
        return []
    idx = {}
    out = []
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if not r.get('goto_rank'):
            continue
        day = r.get('day')
        if day not in idx:
            idx[day] = {
                (norm_player(x.get('player')), code(x.get('team'))): x
                for x in (logs_days.get(day) or [])}
        box = idx[day].get((norm_player(r.get('player')),
                            code(r.get('team'))))
        f = GOTO_FIELD.get(r.get('market'))
        act = None if (not box or not f) else box.get(f)
        base, sd = r.get('base'), r.get('sd')
        # math.floor(x + 0.5) is half-up: 4.3 -> 4, 4.5 -> 5, 4.6 -> 5.
        target = None if base is None else int(math.floor(base + 0.5))
        # `act is None`, never `not act`. A 0-three night is the row
        # where the projection was most wrong, and dropping it is how
        # settle_day used to make every printed average too kind.
        out.append({
            'day': day, 'rank': r.get('goto_rank'),
            # The box score spells him properly ("PJ Washington",
            # "Jaden McDaniels"); the board log is normalised to
            # lowercase. Prefer the real spelling over title-casing
            # the normalised one, which gets Mc- and initials wrong.
            'player': ((box or {}).get('player') or r.get('player')),
            'team': r.get('team'),
            'opp': r.get('opp'), 'market': r.get('market'),
            'base': base, 'mins': r.get('mins'), 'zs': r.get('zs'),
            'actual': act, 'min': (box or {}).get('min'),
            # GRADED AGAINST THE ROUNDED PROJECTION.
            #
            # Production comes in whole numbers; a projection does
            # not. Grading 4 against 4.3 called a miss on a tenth of
            # a rebound, and the clearest case was Trae Young on
            # 06 Nov: 10 assists against a projection of 10.1, the
            # most accurate number the model produced that night,
            # scored as a failure.
            #
            # HALF-UP, not Python's round(). round() is banker's
            # rounding, so round(2.5) is 2 while round(3.5) is 4 --
            # two projections a whole point apart would round in
            # opposite directions, which is not what "round up and
            # down" means to anyone reading the page.
            'target': target,
            'beat': (None if act is None or target is None
                     else bool(act >= target)),
            # zact stays on the RAW projection. Rounding exists to
            # grade fairly in whole numbers; this is the calibration
            # measure, and rounding it would throw away the very
            # precision it is there to test.
            'zact': (None if act is None or base is None or not sd
                     else round((act - base) / sd, 2)),
        })
    # Newest day first, but rank 1 first WITHIN a day -- negating rank
    # under reverse=True gives ascending rank inside descending day.
    out.sort(key=lambda r: (r['day'] or '', -(r['rank'] or 0)),
             reverse=True)
    return out


def goto_record(hist):
    """(beat, graded, mean zact) over graded go-to picks."""
    g = [r for r in hist if r.get('beat') is not None]
    zs = [r['zact'] for r in g if r.get('zact') is not None]
    return (sum(1 for r in g if r['beat']), len(g),
            (round(sum(zs) / len(zs), 2) if zs else None))


# The five published on 06 Nov, in the order they were starred.
# See the patch note: a one-time backfill of a list that predates
# goto_rank, named rather than recomputed, because recomputing it
# tonight would reproduce tonight's inputs and not what was published.
GOTO_SEED_DAY = '20261106'
GOTO_SEED = [
    ('threes', 'pj washington', 'DAL'),
    ('ast', 'trae young', 'WSH'),
    ('reb', 'onyeka okongwu', 'ATL'),
    ('threes', 'jaden mcdaniels', 'MIN'),
    ('threes', 'brandon miller', 'CHA'),
]


def seed_goto(day=None, picks=None, path=None):
    """Stamp goto_rank on a day whose rows predate the field."""
    day = day or GOTO_SEED_DAY
    picks = picks or GOTO_SEED
    path = path or BOARDLOG
    if not os.path.exists(path):
        print(f'  !! no {path} -- nothing to seed')
        return 0
    rows = []
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if ln:
            try:
                rows.append(json.loads(ln))
            except Exception:
                pass
    have = [r for r in rows if r.get('day') == day]
    if not have:
        print(f'  !! {path} has no rows for {day}. Nothing stamped.')
        return 0
    already = [r for r in have if r.get('goto_rank')]
    if already:
        print(f'  !! {day} already carries {len(already)} goto_rank(s). '
              f'Refusing to\n  !! restamp -- delete them by hand if '
              f'this is really what you want.')
        return 0
    want = {(mk, norm_player(p), code(t)): i
            for i, (mk, p, t) in enumerate(picks, 1)}
    hit = 0
    for r in have:
        k = (r.get('market'), norm_player(r.get('player')),
             code(r.get('team')))
        if k in want:
            r['goto_rank'] = want[k]
            hit += 1
    missed = [f'{p} ({mk}, {t})' for (mk, p, t) in picks
              if (mk, norm_player(p), code(t)) not in
              {(x.get('market'), norm_player(x.get('player')),
                code(x.get('team'))) for x in have}]
    tmp = path + '.seed'
    with io.open(tmp, 'w', encoding='utf-8') as fh:
        for r in rows:
            fh.write(json.dumps(r) + '\n')
    os.replace(tmp, path)
    print(f'\n  stamped {hit} of {len(picks)} go-to rows on {day}.')
    if missed:
        print('  !! NOT FOUND, so NOT stamped -- these are missing from '
              'the board log\n  !! for that day and the record will be '
              'short by that many:')
        for m in missed:
            print(f'  !!   {m}')
    return hit


SDINF = os.path.join(DATA, 'sd_inflation.json')

# PER-MARKET SD INFLATION.
#
# WHY THE SD IS TOO TIGHT, STRUCTURALLY.
#
# blended_sd measures a player's game-to-game spread around HIS OWN
# SEASON MEAN. zact then divides by that SD while the numerator is
# (actual - base), and base is a matchup-adjusted, minutes-adjusted
# PROJECTION. So the numerator carries projection error on top of game
# noise and the denominator carries only game noise. sd(zact) is
# therefore above 1.0 by construction, and on 16 Nov it measured
# 1.26 / 1.30 / 1.06 / 1.12 across the four markets.
#
# That is the same error as the college MARGIN_SD: pure game noise
# gives about 11, but a forecast margin also carries rating error, and
# assuming 11.0 gave a calibration MAD of 3.10pp against 1.20pp
# measured.
#
# The consequence is not academic. Every probability the slip prints
# comes from this SD, so a 19% tight SD makes a leg priced at 70%
# really about 66%, and the legs that flip from +EV to -EV are exactly
# the marginal ones the slip is made of. It also explains why the slip
# concentrated in threes, the market with the WORST z sd: overconfident
# probabilities look like edges, so they get selected.
#
# THE FIX IS MEASURED, NOT ASSUMED, AND IT IS OPT-IN. --fit-sd reports
# what the factors would be and writes nothing; --fit-sd --apply
# writes them. Nothing silently reprices a board.

SD_INF_MIN_N = 100      # graded rows per market before a factor is fit
SD_INF_CAP = 2.0        # never widen by more than this
SD_INF_FLOOR = 0.80     # nor tighten below it

_SDINF_CACHE = None


def sd_inflation(path=None):
    """{market: factor}, default 1.0. Cached; missing file means off."""
    global _SDINF_CACHE
    if _SDINF_CACHE is not None:
        return _SDINF_CACHE
    path = path or SDINF
    out = {}
    try:
        if os.path.exists(path):
            d = json.load(io.open(path, encoding='utf-8'))
            for mk, v in (d.get('factors') or {}).items():
                f = float(v)
                if SD_INF_FLOOR <= f <= SD_INF_CAP:
                    out[mk] = f
    except Exception:
        out = {}
    _SDINF_CACHE = out
    return out


def fit_sd_props(logs_days, boardlog=None):
    """Per-market sd of zact, which IS the factor the SD needs.

    Each logged row carries the sd it was priced with and the factor
    already applied, so the raw blended sd is recoverable and the fit
    does not compound across runs:

        raw      = sd_stored / sd_inf_stored
        z_raw    = (actual - base) / raw
        factor   = sd(z_raw)

    Reading sd(zact) off an already-inflated row and multiplying again
    is how a correction like this runs away. Dividing out first means
    a second --fit-sd on corrected rows returns the same factor rather
    than its square.
    """
    path = boardlog or BOARDLOG
    actual = {}
    for day, rows in (logs_days or {}).items():
        for r in rows:
            actual[(day, norm_player(r.get('player')),
                    code(r.get('team')))] = r
    acc = {}
    if os.path.exists(path):
        for ln in io.open(path, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get('market_on') is False:
                continue
            mk = r.get('market')
            f = GOTO_FIELD.get(mk)
            box = actual.get((r.get('day'), r.get('player'),
                              r.get('team')))
            act = None if (not box or not f) else box.get(f)
            base, sd = r.get('base'), r.get('sd')
            if act is None or base is None or not sd:
                continue
            # rows logged before inflation existed carry no factor,
            # which means a factor of 1.0 was in force
            inf = r.get('sd_inf') or 1.0
            raw = sd / inf if inf else sd
            if not raw:
                continue
            acc.setdefault(mk, []).append((act - base) / raw)
    cur = sd_inflation()
    out = {}
    for mk, zs in acc.items():
        n = len(zs)
        sd_z = statistics.stdev(zs) if n > 1 else None
        mean_z = statistics.fmean(zs) if zs else None
        want = sd_z
        clamped = None
        if want is not None:
            if want > SD_INF_CAP:
                clamped, want = want, SD_INF_CAP
            elif want < SD_INF_FLOOR:
                clamped, want = want, SD_INF_FLOOR
        # HOW WELL IS THE FACTOR ITSELF MEASURED?
        #
        # The SE of a sample SD is about sd/sqrt(2(n-1)). At n=110
        # that is 0.08, so a measured 1.06 has a 95% interval of
        # 0.92-1.20 -- it does not exclude 1.0 and is not evidence of
        # a correction on its own, while 1.30 (1.13-1.47) clearly is.
        # Printing both the same way is how a noise factor gets
        # applied with the same confidence as a real one.
        se = (sd_z / math.sqrt(2.0 * (n - 1))) if (sd_z and n > 1) else None
        lo = hi = None
        if se is not None:
            lo, hi = sd_z - 1.96 * se, sd_z + 1.96 * se
        out[mk] = {'n': n, 'sd_z': sd_z, 'mean_z': mean_z,
                   'current': cur.get(mk, 1.0), 'want': want,
                   'clamped_from': clamped, 'se': se, 'lo': lo, 'hi': hi,
                   'solid': bool(lo is not None and lo > 1.0),
                   'enough': n >= SD_INF_MIN_N}
    return out


def show_fit_sd(res, apply=False, path=None):
    print('\nPER-PLAYER SD -- is it wide enough?')
    if not res:
        print('  nothing gradeable in the board log yet.')
        return 1
    print('\n  blended_sd measures spread around a player\'s OWN MEAN,')
    print('  but zact divides (actual - base) by it, and base is a')
    print('  PROJECTION. So the numerator carries projection error and')
    print('  the denominator does not: sd(zact) is above 1.0 by')
    print('  construction, and that ratio IS the correction.')
    print(f"\n  {'market':8s} {'n':>6s} {'mean z':>7s} {'sd z':>6s} "
          f"{'95% CI':>13s} {'now':>5s} {'->':>6s}  status")
    ok = []
    for mk in CATS:
        d = res.get(mk)
        if not d:
            print(f'  {mk:8s} {"--":>6s}   nothing logged')
            continue
        status = 'ok' if d['enough'] else (
            f"needs {SD_INF_MIN_N - d['n']} more rows")
        if d['clamped_from'] is not None:
            status += f" (clamped from {d['clamped_from']:.2f})"
        if d['sd_z'] is not None and d['sd_z'] < 1.0 and d['enough']:
            status += '  <-- BELOW 1.0, which the structure does not predict'
        if d['enough'] and not d['solid']:
            status += '  <-- CI spans 1.0, not evidence on its own'
        ci = ('n/a' if d['lo'] is None
              else f"{d['lo']:.2f}-{d['hi']:.2f}")
        print(f'  {mk:8s} {d["n"]:>6d} '
              f"{_f(d['mean_z'], '+.2f'):>7s} "
              f"{_f(d['sd_z'], '.2f'):>6s} "
              f"{ci:>13s} "
              f"{d['current']:>5.2f} "
              f"{_f(d['want'], '.2f'):>6s}  {status}")
        if d['enough'] and d['want'] is not None:
            ok.append(mk)
    solid = [mk for mk in CATS
             if res.get(mk) and res[mk].get('solid')]
    soft = [mk for mk in CATS
            if res.get(mk) and res[mk]['enough']
            and not res[mk].get('solid')]
    if soft:
        print(f"\n  {', '.join(soft)}: the interval includes 1.0, so "
              f"the measurement is\n  not by itself evidence of a "
              f"correction. The STRUCTURE still says the\n  factor "
              f"cannot be below 1.0 -- variance adds -- so applying it "
              f"errs\n  conservative (fewer marginal legs) rather "
              f"than wrong. It is also\n  refittable: the factor is "
              f"divided out next time, so an overshoot\n  corrects "
              f"rather than compounds.")
    if solid:
        print(f"\n  {', '.join(solid)}: interval clears 1.0. These are "
              f"real.")
    print('\n  mean z near 0 means the projection is UNBIASED; this')
    print('  correction is about width, not level, and will not fix a')
    print('  biased projection. Check level in --by-market for that.')
    if not ok:
        print(f'\n  Nothing has {SD_INF_MIN_N} graded rows yet. '
              f'Writing nothing.')
        return 1
    if not apply:
        print(f"\n  WOULD WRITE: {', '.join(ok)}.  Re-run with "
              f"--fit-sd --apply to commit.")
        print('  Nothing has been changed. Every probability the slip')
        print('  prints comes from this SD, so it does not move on its')
        print('  own.')
        return 0
    path = path or SDINF
    factors = dict(sd_inflation())
    for mk in ok:
        factors[mk] = round(res[mk]['want'], 4)
    payload = {'build': BUILD, 'factors': factors,
               'fitted_on': {mk: res[mk]['n'] for mk in ok},
               'written_at': (datetime.datetime.now().astimezone()
                              .isoformat())}
    with io.open(path, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    global _SDINF_CACHE
    _SDINF_CACHE = None
    print(f'\n  wrote {path}')
    for mk in CATS:
        if mk in factors:
            print(f'    {mk:8s} x{factors[mk]:.4f}')
    print('\n  Every board from here prices with the wider SD, so fewer')
    print('  legs will clear and the ones that do are honestly priced.')
    print('  Expect the slip to shrink, most of all in whichever market')
    print('  had the worst sd z.')
    return 0


# ------------------------------------------------------------------
# PER-MARKET: should this category be on the slip?
# ------------------------------------------------------------------
#
# TWO LOGS, TWO QUESTIONS, AND MIXING THEM IS THE WHOLE TRAP.
#
# boards.jsonl is price-free BY DESIGN -- no line, no side -- so it
# can say whether the PROJECTION for a market is any good and it
# cannot say whether BETTING that market wins. slate.jsonl carries
# the line and the side and is the only thing that can.
#
# A market can project beautifully and still carry no edge, because
# the book's number already contains the same information. So a
# category is dropped on the SLIP evidence, never on the board
# evidence -- but the board evidence is what exists until the slip
# has n, so both print, each with its own n and its own verdict.
#
# AND EACH NULL IS STATED, because a hit rate with no null attached
# is a number that always looks like something:
#
#   board hit rate    ~0.50.  act >= round(proj) is a coin when the
#                     projection is unbiased, so a market at .62 is
#                     projecting LOW, not picking well.
#   board level       ~1.00.  sum(actual)/sum(projected).
#   sd(zact)          ~1.00.  at 1.4 the per-player sd is too tight
#                     and every probability the slip prints is
#                     overconfident -- which shows up as losing bets
#                     in a market whose projections look fine.
#   slip win rate     the BREAKEVEN the logged odds imply, not an
#                     assumed -110.
#
# AND IT WILL NOT LET YOU DROP A CATEGORY ON TEN BETS. The 60% gate
# in the paper came off n=1,544 with p<0.001; venue bias was left as
# a tag at p=0.093 on n=58. Same bar here: below DROP_MIN_N decided
# bets the verdict is UNDERPOWERED and the printout says how many
# more bets the current rate would need before it could decide.

DROP_MIN_N = 25          # decided bets before a DROP is allowed


def _wilson(k, n, z=1.96):
    """Wilson score interval for a proportion.

    Wilson rather than the normal approximation because the slip has
    single-digit n per market right now, where normal CIs run outside
    [0, 1] and report a width that is simply wrong.
    """
    if not n:
        return None, None
    p = k / n
    d = 1.0 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = (z / d) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, c - h), min(1.0, c + h)


def _binom_p(k, n, p0):
    """Exact two-sided binomial p-value against p0."""
    if not n or p0 is None or not (0.0 < p0 < 1.0):
        return None

    def pmf(i):
        return math.comb(n, i) * (p0 ** i) * ((1.0 - p0) ** (n - i))

    obs = pmf(k)
    return min(1.0, sum(pmf(i) for i in range(n + 1)
                        if pmf(i) <= obs * (1.0 + 1e-9)))


def _breakeven(odds):
    """Win rate this price needs to break even, or None.

    TWO PRICE CONVENTIONS LIVE IN slate.jsonl AND THEY OVERLAP IN
    NOTHING, so magnitude tells them apart:

      |o| >= 100      American odds. -110 stakes 110 to win 100, so
                      breakeven is 110/210 = 0.524.
      1 <= o <= 99    a KALSHI price in cents. Paying 27c to win a
                      dollar is a breakeven of exactly 0.27.

    American odds can never fall strictly inside (-100, 100): the
    minimum magnitude is 100, because -100 and +100 are both even
    money. So a 27 in this field is not "odds of +27", it is 27
    cents -- and reading it as American odds returns 0.787 where the
    truth is 0.270, which makes a profitable market look like a
    losing one. The first run of --by-market printed a breakeven of
    0.271 for points, which is not a legal American price, and that
    is how this was found.
    """
    if odds is None:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o >= 100.0:
        return 100.0 / (o + 100.0)
    if o <= -100.0:
        return (-o) / ((-o) + 100.0)
    if 0.0 < o < 100.0:
        return o / 100.0          # Kalshi cents
    return None                   # 0, or a negative under 100: unusable


def _price_kind(odds):
    """Which convention a stored price is in, for the printout."""
    if odds is None:
        return None
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return 'bad'
    if o >= 100.0 or o <= -100.0:
        return 'american'
    if 0.0 < o < 100.0:
        return 'cents'
    return 'bad'


def _mean_breakeven(odds_list):
    """Mean breakeven over prices -- NOT the breakeven of the mean.

    Averaging American odds is not a defined operation: -110 and
    +269 average to +79.5, whose breakeven is 0.557, while the two
    bets actually need 0.397 between them. The mean belongs on the
    probabilities, which live on a common scale.
    """
    bes = [b for b in (_breakeven(o) for o in odds_list)
           if b is not None]
    return (statistics.fmean(bes) if bes else None), len(bes)


def _n_needed(k, n, p0, cap=4000):
    """Decided bets needed, AT THE OBSERVED RATE, to call a DROP.

    The answer to "so how long until I know". Holds the observed win
    rate fixed and grows n until the Wilson upper bound clears below
    breakeven. None when the observed rate is already at or above
    breakeven, because then no amount of n produces a DROP.
    """
    if not n or p0 is None:
        return None
    p = k / n
    if p >= p0:
        return None
    m = n
    while m < cap:
        m += 1
        _, hi = _wilson(int(round(p * m)), m)
        if hi is not None and hi < p0:
            return m
    return None


def market_breakdown(logs_days, boardlog=None, slate=None, tier='board'):
    """Per-market board accuracy and slip W/L, with verdicts.

    Returns {'board': {mk: {...}}, 'slip': {mk: {...}}, 'tier': tier}.
    """
    boardlog = boardlog or BOARDLOG
    slate = slate or SLATE

    # --- board side: is the projection for this market any good? ---
    #
    # Resolution is (norm_player, code(team)) into that day's box
    # scores via GOTO_FIELD -- the same path settle_day and
    # goto_history use. A second way of answering "what did he get"
    # is a second way to get it wrong.
    actual = {}
    for day, rows in (logs_days or {}).items():
        for r in rows:
            actual[(day, norm_player(r.get('player')),
                    code(r.get('team')))] = r

    bd = {}
    if os.path.exists(boardlog):
        for ln in io.open(boardlog, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            # an unscaled base is a different quantity, not a noisier
            # one -- see settle_day
            if r.get('market_on') is False:
                continue
            mk = r.get('market')
            f = GOTO_FIELD.get(mk)
            box = actual.get((r.get('day'), r.get('player'),
                              r.get('team')))
            act = None if (not box or not f) else box.get(f)
            base, sd = r.get('base'), r.get('sd')
            # `act is None`, never `not act`: a 0-three night is the
            # row where the projection was most wrong.
            if act is None or not base:
                continue
            d = bd.setdefault(mk, {
                'n': 0, 'hit': 0, 'proj': 0.0, 'act': 0.0, 'z': [],
                'days': set(), 'goto_n': 0, 'goto_hit': 0, 'p': []})
            target = int(math.floor(base + 0.5))   # half-up
            d['n'] += 1
            hit = 1 if act >= target else 0
            d['hit'] += hit
            # THE MODEL'S OWN PROBABILITY FOR THIS ROW.
            #
            # The null for "act >= round(proj)" is NOT 0.500. target
            # is an integer and act is an integer, so when base is
            # 20.0 the actual only has to clear 19.5 to round to 20 --
            # half a point of free room, and the atom at the centre
            # pushes the true rate to 0.566 on sd 3. On threes, where
            # sd is near 1 and the atoms are fat, that same effect is
            # worth far more. A 0.500 null would therefore have called
            # threes "projecting low" for doing exactly what an
            # unbiased projection does, which is the kind of confident
            # wrong finding that gets a category dropped.
            #
            # So the null is the model's own number: P(X >= target) is
            # P(X > target - 1), from p_over, which picks normal,
            # negative binomial or Poisson by the data.
            pr, _dist = p_over(base, target - 1, sd, mk)
            if pr is not None:
                d['p'].append((pr, hit))
            d['proj'] += base
            d['act'] += act
            d['days'].add(r.get('day'))
            if sd:
                d['z'].append((act - base) / sd)
            if r.get('goto_rank'):
                d['goto_n'] += 1
                d['goto_hit'] += 1 if act >= target else 0

    board = {}
    for mk, d in bd.items():
        lo, hi = _wilson(d['hit'], d['n'])
        # CALIBRATION, against the model's own probabilities.
        #
        # Observed hits vs the sum of predicted probabilities. Under
        # independence the count of hits is Poisson-binomial, whose
        # variance is sum(p(1-p)) exactly -- no approximation needed,
        # and it does not assume a common p the way a binomial test
        # would.
        ps = [x[0] for x in d['p']]
        hs = [x[1] for x in d['p']]
        exp = sum(ps)
        var = sum(q * (1 - q) for q in ps)
        z_cal = ((sum(hs) - exp) / math.sqrt(var)
                 if var > 0 else None)
        # Brier is a PROPER score: it punishes confident and wrong
        # harder than unsure and wrong, which is the failure that
        # actually costs money on a slip.
        brier = (statistics.fmean([(q - h) ** 2
                                   for q, h in zip(ps, hs)])
                 if ps else None)
        # reference: predicting this market's own base rate every
        # time. Brier alone is unreadable without it.
        base_rate = statistics.fmean(hs) if hs else None
        brier_ref = (statistics.fmean([(base_rate - h) ** 2
                                       for h in hs])
                     if hs else None)
        skill = (None if not brier_ref
                 else 1.0 - brier / brier_ref)
        # SPREAD OF THE PREDICTIONS THEMSELVES.
        #
        # Skill is measured against predicting this market's base rate
        # every time, so it only means anything when the per-row
        # probabilities actually vary. Where they are near-constant,
        # skill is ~0 BY CONSTRUCTION and a flag on it fires every
        # night while saying nothing -- which is the kind of printout
        # that trains you to ignore printouts.
        p_sd = statistics.stdev(ps) if len(ps) > 1 else None
        board[mk] = {
            'n': d['n'], 'days': len(d['days']),
            'hit': d['hit'], 'rate': d['hit'] / d['n'] if d['n'] else None,
            'lo': lo, 'hi': hi,
            'graded_p': len(ps),
            'exp_rate': (exp / len(ps)) if ps else None,
            'z_cal': None if z_cal is None else round(z_cal, 2),
            'brier': brier, 'brier_ref': brier_ref, 'skill': skill,
            'p_sd': p_sd,
            'level': (d['act'] / d['proj']) if d['proj'] else None,
            'zmean': (statistics.fmean(d['z']) if d['z'] else None),
            'zsd': (statistics.stdev(d['z']) if len(d['z']) > 1 else None),
            'goto_n': d['goto_n'], 'goto_hit': d['goto_hit'],
        }

    # --- slip side: does betting this market win? ------------------
    rev = {v: k for k, v in SLATE_MARKET.items()}
    sl = {}
    if os.path.exists(slate):
        for ln in io.open(slate, encoding='utf-8'):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if tier and r.get('tier') != tier:
                continue
            mk = rev.get(r.get('market'), r.get('market'))
            s = sl.setdefault(mk, {
                'w': 0, 'l': 0, 'push': 0, 'void': 0, 'pending': 0,
                'unpriced': 0, 'units': 0.0, 'odds': [], 'days': set()})
            res = r.get('result')
            if r.get('unpriced'):
                s['unpriced'] += 1
            if res == 'win':
                s['w'] += 1
            elif res == 'loss':
                s['l'] += 1
            elif res == 'push':
                s['push'] += 1
            elif res == 'void':
                s['void'] += 1
            else:
                s['pending'] += 1
            if r.get('units') is not None:
                try:
                    s['units'] += float(r['units'])
                except (TypeError, ValueError):
                    pass
            if r.get('odds') is not None:
                s['odds'].append(r['odds'])
            if r.get('day'):
                s['days'].add(r['day'])

    slip = {}
    for mk, s in sl.items():
        dec = s['w'] + s['l']            # pushes and voids are not bets
        be, n_be = _mean_breakeven(s['odds'])
        kinds = sorted({k for k in (_price_kind(o) for o in s['odds'])
                        if k})
        lo, hi = _wilson(s['w'], dec)
        p = _binom_p(s['w'], dec, be)
        if be is None:
            # NO PRICE, NO VERDICT. A bet with an unreadable price has
            # no breakeven, and inventing -110 for it is how a market
            # gets kept or dropped on a number nobody wrote down.
            verdict = 'NO USABLE PRICE'
        elif dec < DROP_MIN_N:
            verdict = 'UNDERPOWERED'
        elif hi is not None and be is not None and hi < be:
            verdict = 'DROP'
        elif lo is not None and be is not None and lo > be:
            verdict = 'KEEP'
        else:
            verdict = 'inconclusive'
        slip[mk] = {
            'w': s['w'], 'l': s['l'], 'push': s['push'],
            'void': s['void'], 'pending': s['pending'],
            'unpriced': s['unpriced'], 'decided': dec,
            'days': len(s['days']),
            'rate': (s['w'] / dec) if dec else None,
            'lo': lo, 'hi': hi, 'breakeven': be, 'p': p,
            'units': round(s['units'], 2),
            'price_kinds': kinds, 'n_priced': n_be,
            'bad_price': len(s['odds']) - n_be,
            'roi': (round(s['units'] / dec, 3) if dec else None),
            'verdict': verdict,
            'n_needed': _n_needed(s['w'], dec, be),
        }

    return {'board': board, 'slip': slip, 'tier': tier}


def _f(v, spec, na='n/a'):
    """Format a value, or 'n/a' when it is None.

    Exists so the table lines below contain no nested quotes. The
    first version of this printer inlined conditionals inside the
    f-strings and ended up with chr() calls in it to dodge the
    quoting -- unreadable, and it did not even parse.
    """
    if v is None:
        return na
    return format(v, spec)


def _ci(lo, hi):
    return 'n/a' if lo is None else f'{lo:.3f}-{hi:.3f}'


def show_market_breakdown(res):
    board = res.get('board') or {}
    slip = res.get('slip') or {}
    tier = res.get('tier')
    print('\nPER-MARKET: SHOULD THIS CATEGORY BE ON THE SLIP?')

    # --- the slip ---------------------------------------------------
    print(f'\nTHE SLIP  (slate.jsonl, tier={tier})')
    print('  the only evidence that can drop a category')
    if not slip:
        # EMPTY AND MISSING MUST NOT LOOK THE SAME. The performance
        # page rendered a 404 and an empty list identically, which
        # cost an afternoon.
        if not os.path.exists(SLATE):
            print(f'  !! {SLATE} does not exist -- nothing has been '
                  'logged as a bet,')
            print('  !! so no category can be judged for the slip yet.')
        else:
            print(f'  !! {SLATE} exists but holds no tier={tier} rows.')
            print('  !! try --by-market --tier core, or --tier all.')
    else:
        print(f"\n  {'market':8s} {'W-L':>8s} {'rate':>6s} {'95% CI':>13s} "
              f"{'b/e':>6s} {'units':>7s} {'ROI':>7s}  verdict")
        for mk in CATS:
            s = slip.get(mk)
            if not s:
                print(f'  {mk:8s} {"--":>8s}   never on a logged slip')
                continue
            wl = f"{s['w']}-{s['l']}"
            print(f'  {mk:8s} {wl:>8s} '
                  f"{_f(s['rate'], '.3f'):>6s} "
                  f'{_ci(s["lo"], s["hi"]):>13s} '
                  f"{_f(s['breakeven'], '.3f'):>6s} "
                  f"{_f(s['units'], '+.2f'):>7s} "
                  f"{_f(s['roi'], '+.3f'):>7s}  "
                  f"{s['verdict']}")
            extra = []
            for n, lbl in ((s['pending'], 'pending'), (s['push'], 'push'),
                           (s['void'], 'void'),
                           (s['unpriced'], 'unpriced')):
                if n:
                    extra.append(f'{n} {lbl}')
            if extra:
                print(f"           ({', '.join(extra)})")
            # WHICH PRICE CONVENTION THESE WERE READ IN.
            #
            # American odds and Kalshi cents cannot both be assumed,
            # and the first run of this printed a breakeven of 0.271
            # for points -- not a legal American price -- because the
            # odds were being averaged on the odds scale and read in
            # the wrong convention. So the convention is now printed
            # rather than trusted.
            kinds = s.get('price_kinds') or []
            if kinds and kinds != ['american']:
                print(f"           prices read as: "
                      f"{', '.join(kinds)}"
                      + ('  <-- MIXED, so the breakeven above is an '
                         'average across conventions'
                         if len(kinds) > 1 else ''))
            if s.get('bad_price'):
                print(f"           !! {s['bad_price']} row(s) have a "
                      f"price that is neither American odds nor cents")
            # PENDING IS A GRADING GAP, not a neutral count. A market
            # with more pending than decided is not being measured.
            if s['pending'] > max(2, s['decided']):
                print(f"           !! {s['pending']} pending vs "
                      f"{s['decided']} decided -- this market is "
                      f"mostly ungraded,")
                print(f"           !! so its rate is built on the "
                      f"minority that settled")

        under = [mk for mk in CATS
                 if slip.get(mk) and slip[mk]['verdict'] == 'UNDERPOWERED']
        if under:
            print(f'\n  UNDERPOWERED means the interval still spans '
                  f'breakeven, so the honest\n  answer is "not yet", '
                  f'not "keep". A DROP needs {DROP_MIN_N} decided '
                  f'bets minimum:')
            for mk in under:
                s = slip[mk]
                need, rate = s['n_needed'], s['rate']
                head = f"    {mk:8s} {s['decided']:>3d} decided"
                if need:
                    print(f'{head}; at {rate:.3f} it would take ~{need} '
                          f'to justify a DROP')
                elif rate is not None and s['breakeven']:
                    print(f'{head}; at {rate:.3f} it is at or above '
                          f'breakeven, so no n drops it')
                else:
                    print(head)
        nop = [mk for mk in CATS
               if slip.get(mk)
               and slip[mk]['verdict'] == 'NO USABLE PRICE']
        if nop:
            print(f"\n  NO USABLE PRICE: {', '.join(nop)} -- no "
                  f"breakeven can be computed, so these get no\n"
                  f"  verdict at all. Inventing -110 for them is how "
                  f"a category gets kept\n  or dropped on a number "
                  f"nobody wrote down.")
        drops = [mk for mk in CATS
                 if slip.get(mk) and slip[mk]['verdict'] == 'DROP']
        if drops:
            print(f"\n  DROP: {', '.join(drops)} -- 95% upper bound is "
                  f"below breakeven.")

    # --- the board --------------------------------------------------
    print('\nTHE BOARD  (boards.jsonl, price-free)')
    print('  projection quality. CANNOT drop a category: a market can')
    print('  project well and still carry no edge against the number.')
    if not board:
        if not os.path.exists(BOARDLOG):
            print(f'  !! {BOARDLOG} does not exist.')
        else:
            print('  !! nothing gradeable -- no logged row joined a box '
                  'score.')
            print('  !! usually season_logs.json is behind: run '
                  'edges.py --build.')
        return

    print(f"\n  {'market':8s} {'n':>5s} {'days':>4s} {'obs':>6s} "
          f"{'exp':>6s} {'z cal':>6s} {'brier':>6s} {'skill':>6s} "
          f"{'p sd':>5s} {'level':>6s} {'z sd':>5s}")
    for mk in CATS:
        b = board.get(mk)
        if not b:
            print(f'  {mk:8s} {"--":>5s}   nothing logged')
            continue
        print(f'  {mk:8s} {b["n"]:>5d} {b["days"]:>4d} '
              f"{_f(b['rate'], '.3f'):>6s} "
              f"{_f(b['exp_rate'], '.3f'):>6s} "
              f"{_f(b['z_cal'], '+.2f'):>6s} "
              f"{_f(b['brier'], '.3f'):>6s} "
              f"{_f(b['skill'], '+.3f'):>6s} "
              f"{_f(b['p_sd'], '.3f'):>5s} "
              f"{_f(b['level'], '.3f'):>6s} "
              f"{_f(b['zsd'], '.2f'):>5s}")

    print('\n  HOW TO READ IT. Each column has a null, because a hit '
          'rate with')
    print('  no null attached is a number that always looks like '
          'something:')
    print('    obs vs exp     observed hit rate against THE MODEL\'S '
          'OWN')
    print('                   probability for those same rows. The '
          'null is exp,')
    print('                   never 0.500: target is an integer, so a '
          'projection')
    print('                   of 20.0 only needs 19.5 to round up, and '
          'on threes')
    print('                   that free room is worth a lot more than '
          'on points.')
    print('    z cal   ~0.00  obs vs exp in SDs (Poisson-binomial, '
          'exact).')
    print('                   |z| > 2 means this market\'s '
          'probabilities are')
    print('                   wrong, and NEGATIVE means overconfident '
          '-- it wins')
    print('                   less than it claims, which is what loses '
          'money.')
    print('    brier    lower  proper score. Punishes confident-and-'
          'wrong hardest.')
    print('    skill   >0     Brier against predicting this market\'s '
          'own base')
    print('                   rate every time. Only meaningful when '
          'p sd is')
    print('                   non-trivial: with near-constant '
          'probabilities')
    print('                   skill is ~0 by construction, not as a '
          'finding.')
    print('    p sd           spread of the per-row probabilities. '
          'Near 0 means')
    print('                   this market prints nearly the same '
          'number every')
    print('                   time, so skill below it says nothing.')
    print('    level  ~1.000  sum(actual) / sum(projected).')
    print('    z sd    ~1.00  at 1.40 the per-player sd is too tight, '
          'so every')
    print('                   probability the slip prints is '
          'overconfident.')

    flags = []
    for mk in CATS:
        b = board.get(mk)
        if not b:
            continue
        if b['zsd'] is not None and b['zsd'] > 1.25:
            flags.append(f"{mk}: z sd {b['zsd']:.2f} -- slip "
                         f"probabilities overconfident here")
        if b['level'] is not None and abs(b['level'] - 1.0) > 0.06:
            way = 'over' if b['level'] < 1.0 else 'under'
            flags.append(f"{mk}: level {b['level']:.3f} -- "
                         f"{way}-projecting by "
                         f"{abs(1 - b['level']) * 100:.1f}%")
        if (b['z_cal'] is not None and abs(b['z_cal']) > 2
                and b['n'] >= 40):
            way = ('OVERCONFIDENT -- wins less than it claims'
                   if b['z_cal'] < 0 else
                   'underconfident -- wins more than it claims')
            flags.append(f"{mk}: obs {b['rate']:.3f} vs exp "
                         f"{b['exp_rate']:.3f}, z={b['z_cal']:+.2f} "
                         f"-- {way}")
        if (b['skill'] is not None and b['skill'] < -0.02
                and b['n'] >= 40
                and b['p_sd'] is not None and b['p_sd'] > 0.05):
            flags.append(f"{mk}: skill {b['skill']:+.3f} on p spread "
                         f"{b['p_sd']:.3f} -- the per-row "
                         f"probabilities are worse than this market's "
                         f"own base rate")
    if flags:
        print('\n  FLAGGED:')
        for f in flags:
            print(f'    ! {f}')

    goto = [f"{mk} {board[mk]['goto_hit']}/{board[mk]['goto_n']}"
            for mk in CATS if board.get(mk) and board[mk]['goto_n']]
    if goto:
        print(f"\n  of which go-to five: {', '.join(goto)}")


def settle_day(logs_days, day, path=None):
    """Every logged board row for one day against what happened.

    --calibrate answers "is the ordering working" in aggregate. This
    answers "what happened last night", which is a different question
    and the one you ask first: an aggregate slope cannot tell you
    that the top row missed by nine or that a man never dressed.
    """
    path = path or BOARDLOG
    if not os.path.exists(path):
        return None
    field = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
    actual = {}
    for r in logs_days.get(day) or []:
        actual[(norm_player(r.get('player')), code(r.get('team')))] = r
    rows = []
    unscaled = 0
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') != day:
            continue
        # A row logged with the market term OFF carries an UNSCALED
        # base. That is a different quantity from a scaled one, not a
        # noisier version of it, so settling both against the same box
        # score measures neither.
        if r.get('market_on') is False:
            unscaled += 1
            continue
        box = actual.get((r.get('player'), r.get('team')))
        f = field.get(r.get('market'))
        act = None if (not box or not f) else box.get(f)
        rows.append({
            'market': r.get('market'), 'player': r.get('player'),
            'team': r.get('team'), 'opp': r.get('opp'),
            'base': r.get('base'), 'zs': r.get('zs'),
            'actual': act, 'min': (box or {}).get('min'),
            # `act is None`, NOT `not act`. A 0-rebound game is a
            # ratio of 0.00, not a missing value, and the old test
            # threw away the rows where the projection was most
            # wrong -- which made every printed average too kind.
            'ratio': (None if act is None or not r.get('base')
                      else act / r['base']),
        })
    rows.sort(key=lambda r: (r['market'], -(r['zs'] or 0)))
    if unscaled:
        print(f'\n  NOTE: {unscaled} row(s) for {day} were logged with '
              f'the market term OFF\n  and are excluded -- their base '
              f'is unscaled, so it is not the\n  projection this model '
              f'makes. --drop-board-day {day} and re-run the\n  board '
              f'to replace them.')
    return rows


def level_to_date(logs_days, path=None):
    """Per-market sum(actual)/sum(projected) over every settled day.

    sum/sum rather than a mean of ratios, for the reason the nightly
    block already states: the mean weights a 1.2-projection three as
    heavily as a 33-point projection, and in expectation that biases
    it upward.

    Returns {market: {'proj','actual','n','days'}}.
    """
    path = path or BOARDLOG
    field = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
    actual = {}
    for day, rows in (logs_days or {}).items():
        for r in rows:
            actual[(day, norm_player(r.get('player')),
                    code(r.get('team')))] = r
    out = {}
    if not os.path.exists(path):
        return out
    for ln in io.open(path, encoding='utf-8'):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('market_on') is False:
            continue
        box = actual.get((r.get('day'), r.get('player'), r.get('team')))
        f = field.get(r.get('market'))
        if not box or not f or not r.get('base'):
            continue
        a = box.get(f)
        if a is None:
            continue
        g = out.setdefault(r['market'],
                           {'proj': 0.0, 'actual': 0.0, 'n': 0,
                            'days': set()})
        g['proj'] += r['base']
        g['actual'] += a
        g['n'] += 1
        g['days'].add(r['day'])
    return out


def show_level(lv):
    """The to-date level, printed under the night's own."""
    if not lv:
        return
    print('\n  LEVEL TO DATE -- every settled board row, pooled')
    print(f'    {"":7}{"PROJ":>9}{"ACTUAL":>9}{"MISS":>9}{"ROWS":>6}'
          f'{"DAYS":>6}')
    tp = ta = tn = 0
    alldays = set()
    for mk in CATS:
        g = lv.get(mk)
        if not g or not g['proj']:
            continue
        tp += g['proj']; ta += g['actual']; tn += g['n']
        alldays |= g['days']
        print(f'    {mk:7}{g["proj"]:>9.1f}{g["actual"]:>9.0f}'
              f'{(g["actual"] / g["proj"] - 1) * 100:>+8.1f}%'
              f'{g["n"]:>6}{len(g["days"]):>6}')
    if tp:
        print(f'    {"ALL":7}{tp:>9.1f}{ta:>9.0f}'
              f'{(ta / tp - 1) * 100:>+8.1f}%{tn:>6}{len(alldays):>6}')
    print('    This is the figure to argue with. A single night moved a '
          'market\n    10 to 25 points on 01 Nov, and 01 and 02 Nov '
          'disagreed in SIGN.')


def goto_picks(boards, n=None):
    """A price-free top five across all four boards.

    Rows are NOT comparable across boards on z -- tonight the points
    board ran to +2.47 while rebounds topped out at +0.63, because
    rebounds and assists score the opponent only and get shrunk. So
    each row is scored against the best row ON ITS OWN BOARD, then
    docked:

        score = z / board_top_z
                - GOTO_W_BLOWOUT * P(blowout)
                - GOTO_W_SHARE   if a share-led rebounds row
                - GOTO_W_THIN_SD if his sd rests on < GOTO_SD_N games

    Hard filters, which are not heuristics: the minutes floor, no
    injury flag at all (the boards keep and tag DAY-TO-DAY; this list
    does not), no thin-zone label, one row per (opponent, zone)
    finding with measured before inferred, and PER_GAME per game.

    It consults NO price, which is the point -- this is the
    most-favourable-matchup list and has never needed one.
    """
    n = GOTO_MAX if n is None else n

    def eligible(r):
        return ((r.get('mpg_raw') or r.get('mins') or 0) >= PICK_MIN_MPG
                and not r.get('status')
                and not (r.get('parts') or {}).get('thin_zone')
                and (r.get('zs') or 0.0) > 0)

    # NORMALISE AGAINST THE TOP *SURVIVING* ROW, not the top row.
    #
    # The first version divided by the best z on the board including
    # rows the hard filters then removed. Tonight the points board's
    # top row is Max Christie at +2.47, who is DAY-TO-DAY and 24
    # minutes -- excluded -- so every eligible points row was being
    # scored against a number no eligible row could approach, and the
    # whole category was pushed out of the top five by an arithmetic
    # artifact rather than by anything about the players.
    tops = {}
    for mk in CATS:
        zs = [abs(r.get('zs') or 0.0) for r in (boards.get(mk) or [])
              if eligible(r)]
        tops[mk] = max(zs) if zs else 0.0

    cand = []
    for mk in CATS:
        for r in boards.get(mk) or []:
            if not eligible(r) or not tops[mk]:
                continue
            z = r.get('zs') or 0.0

            why = []
            sc = z / tops[mk]
            br = r.get('blowout_risk')
            if br:
                # Charge the risk ONCE. A nonzero min_cut means
                # minutes_haircut already took it out of the
                # projection, so only the residual it cannot see is
                # left to charge here. See the patch note.
                hc = (r.get('min_cut') or 0) > 0
                w = GOTO_W_BLOWOUT_RESIDUAL if hc else GOTO_W_BLOWOUT
                sc -= w * br
                why.append(
                    f'{br * 100:.0f}% blowout risk'
                    + (f', {r["min_cut"]:.1f}m already off the minutes'
                       if hc else ' (minutes term OFF)'))
            if mk == 'reb':
                p = r.get('parts') or {}
                # 'vol' and 'shr' are the names the rebounds row
                # already uses. Inventing new ones here would be the
                # field-not-copied-at-a-boundary bug for the fifth
                # time in this project.
                vol, sh = p.get('vol'), p.get('shr')
                if vol is not None and sh is not None and sh > vol:
                    sc -= GOTO_W_SHARE
                    why.append('share-led, not volume')
            sdn = r.get('sd_n') or 0
            if sdn < GOTO_SD_N:
                sc -= GOTO_W_THIN_SD
                why.append(f'sd rests on {sdn} games')
            cand.append(dict(r, market=mk, goto=round(sc, 3),
                             goto_why='; '.join(why)))

    cand.sort(key=lambda r: (claim_key(r), claim_rank(r)))
    best = {}
    for r in cand:
        best.setdefault(claim_key(r), r)
    cand.sort(key=lambda r: -r['goto'])

    out, per, who = [], {}, set()
    for r in cand:
        if best.get(claim_key(r)) is not r:
            continue
        # ONE ROW PER MAN.
        #
        # On 10 Nov this list returned Davion Mitchell's points AND
        # his threes as picks 3 and 4 -- two of five on one player in
        # one game. They survived claim_key legitimately, because the
        # zones differ and the board correctly called them "two
        # separate reads": his points edge is from short, his threes
        # edge from three.
        #
        # They are still one man's minutes. If he rests, is ejected,
        # or simply has a quiet night, 40% of the list dies at once.
        # The whole value of five picks is five roughly independent
        # reads, and the record already suffers from the five sharing
        # a slate -- doubling up inside one player makes the
        # correlation worse in the one place it is free to fix.
        #
        # This is a DIVERSIFICATION rule, not a claim about the
        # model. The second row is not wrong; it is redundant with
        # the first in exactly the way a second ticket on the same
        # game is.
        if r['player'] in who:
            continue
        gk = tuple(sorted((r['team'], r['opp'])))
        if per.get(gk, 0) >= PER_GAME:
            continue
        per[gk] = per.get(gk, 0) + 1
        who.add(r['player'])
        out.append(r)
        if len(out) >= n:
            break
    return out


def show_goto(rows):
    print('\n' + '=' * 68)
    print("GO-TO PICKS -- best spots on the slate, no price consulted")
    if not rows:
        print('\n  Nobody clears the floors tonight. Every candidate '
              'was under the\n  minutes floor, carrying an injury '
              'flag, or a duplicate of a finding\n  already taken.')
        return
    unit = {'pts': 'points', 'threes': 'threes', 'reb': 'rebounds',
            'ast': 'assists'}
    print()
    for i, r in enumerate(rows, 1):
        stars = '*' * (len(rows) - i + 1)
        print(f"  {stars:<5} {i}. {r['player']} -- "
              f"{unit[r['market']]}, {r['team']} vs {r['opp']}")
        print(f"        projects {r['base']:.1f} in {r['mins']:.0f} min"
              f"   z {r['zs']:+.2f}   score {r['goto']:+.2f}")
        if r.get('goto_why'):
            print(f"        docked for: {r['goto_why']}")
        if r.get('why'):
            print(f"        {r['why']}")
    hc = any((r.get('min_cut') or 0) > 0 for r in rows)
    if hc:
        print(f'\n  Scored as z/(best z on its own board), minus '
              f'{GOTO_W_BLOWOUT_RESIDUAL:.2f}xP(blowout) -- the '
              f'RESIDUAL weight,')
        print('  because the minutes haircut is live and has already '
              'taken the\n  minutes part out of these projections. '
              'The residual stands for\n  shot diet and board '
              'redistribution, and is NOT measured.')
    else:
        print('\n  !! MINUTES TERM IS OFF, so the blowout dock below is '
              'standing in\n  !! for the whole effect. Turn the real '
              'one on:\n  !!   python3 three.py --blowout '
              '--write-blowout')
        print(f'\n  Scored as z/(best z on its own board), minus '
              f'{GOTO_W_BLOWOUT:.2f}xP(blowout),')
    print(f'  {GOTO_W_SHARE:.2f} for a share-led rebound read, '
          f'{GOTO_W_THIN_SD:.2f} for an sd on under {GOTO_SD_N} games.')
    print('  THOSE WEIGHTS ARE A HEURISTIC, calibrated to nothing.')
    print('  NO PRICE CONSULTED. This is the favourable-matchup list, '
          'not a bet\n  list -- whether any is worth backing still '
          'needs a line.')


def show_settle(day, rows):
    print(f'\nWHAT HAPPENED -- {day}')
    if rows is None:
        print('  no board log yet.')
        return
    if not rows:
        print(f'  nothing logged for {day}.')
        return
    played = [r for r in rows if r['actual'] is not None]
    dnp = [r for r in rows if r['actual'] is None]
    print(f'  {len(rows)} logged; {len(played)} played, '
          f'{len(dnp)} never appeared in a box score')
    cur = None
    print(f"\n  {'PLAYER':22}{'MATCHUP':10}{'Z':>6}{'PROJ':>7}{'ACTUAL':>8}"
          f"{'MIN':>6}{'ACT/PROJ':>10}")
    for r in rows:
        if r['market'] != cur:
            cur = r['market']
            print(f'  -- {TITLE.get(cur, cur)}')
        act = '    dnp' if r['actual'] is None else f"{r['actual']:>8.0f}"
        mn = '     -' if r['min'] is None else f"{r['min']:>6.0f}"
        ra = '         -' if r['ratio'] is None else f"{r['ratio']:>10.2f}"
        print(f"  {r['player'][:21]:22}{r['team'] + ' v ' + r['opp']:10}"
              f"{(r['zs'] or 0):>+6.2f}{(r['base'] or 0):>7.1f}{act}{mn}{ra}")
    if played:
        rs = [r['ratio'] for r in played if r['ratio'] is not None]
        if rs:
            # TWO NUMBERS, BECAUSE THEY ANSWER DIFFERENTLY.
            #
            # The mean of ratios weights every row equally, so a
            # 1.2-projection three landing on 2 (ratio 1.67) counts as
            # hard as a 33-point projection landing on 34 (1.03). In
            # EXPECTATION that biases it up -- E[A/P] > E[A]/E[P]
            # whenever P varies -- but on any given night the small
            # projections can land low and drag it the other way, so
            # the label must not claim a direction the rows on screen
            # contradict. sum(actual)/sum(proj) is the one to read.
            tp = sum(r['base'] for r in played if r['ratio'] is not None)
            ta = sum(r['actual'] for r in played if r['ratio'] is not None)
            print(f'\n  LEVEL -- was the projection about right?')
            print(f'    sum(actual)/sum(projected)  {ta / tp:.3f}'
                  f'   ({ta:.0f} on {tp:.1f} projected)')
            print(f'    mean of per-row ratios      '
                  f'{statistics.fmean(rs):.3f}   (unweighted -- a 1.2 '
                  f'three counts\n{"":32}as hard as a 33-point '
                  f'projection)')
            # Per market, because they disagree. A blended figure hid
            # assists projecting high while threes projected low.
            print(f'\n    {"":6}{"PROJ":>8}{"ACTUAL":>8}{"MISS":>9}'
                  f'{"ROWS":>6}')
            for mk in CATS:
                g = [r for r in played
                     if r['market'] == mk and r['ratio'] is not None]
                if not g:
                    continue
                p = sum(r['base'] for r in g)
                a = sum(r['actual'] for r in g)
                if not p:
                    continue
                print(f'    {mk:6}{p:>8.1f}{a:>8.0f}'
                      f'{(a / p - 1) * 100:>+8.1f}%{len(g):>6}')
            print('\n  1.000 would mean the projections were right on '
                  'average. This is\n  ONE night, and a single player '
                  'moves a market by 10 to 25 points\n  at this size, '
                  'so read it as a direction to watch rather than a\n'
                  '  finding. It says nothing at all about the '
                  'ORDERING -- that is\n  --calibrate, and it needs '
                  'weeks.')
    if dnp:
        print(f"\n  the {len(dnp)} who never appeared:")
        for r in dnp[:12]:
            print(f"    {r['player']} ({r['team']})")
        print('  A logged row for a man who did not dress is not a miss, '
              'it is a\n  row that should never have been logged. If '
              'these were ruled out\n  after the board ran, that is the '
              'injury feed being later than us.')


def calibrate_boards(logs_days, path=None, min_rows=40):
    """Does actual/baseline actually rise with the matchup z?

    The same shape as the FIT calibration, and it has to be read the
    same way: a slope that does not clear two standard errors is not
    a finding, and the buckets matter more than the slope because a
    line through a bent relationship is tidy and wrong at both ends.
    """
    path = path or BOARDLOG
    if not os.path.exists(path):
        return {}
    field = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
    actual = {}
    for day, rows in logs_days.items():
        for r in rows:
            actual[(day, norm_player(r.get('player')),
                    code(r.get('team')))] = r
    out = {}
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        # see settle_day: an unscaled base is a different quantity
        if r.get('market_on') is False:
            continue
        box = actual.get((r.get('day'), r.get('player'), r.get('team')))
        if not box or not r.get('base'):
            continue
        f = field.get(r.get('market'))
        if not f or box.get(f) is None:
            continue
        out.setdefault(r['market'], []).append(
            (r.get('zs') or 0.0, (box.get(f) or 0) / r['base'], r['day']))

    res = {}
    for mk, obs in out.items():
        if len(obs) < min_rows:
            res[mk] = {'ok': False, 'n': len(obs)}
            continue
        xs = [o[0] for o in obs]
        ys = [o[1] for o in obs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        beta = sxy / sxx if sxx else 0.0
        resid = [y - (my + beta * (x - mx)) for x, y in zip(xs, ys)]
        s2 = sum(v * v for v in resid) / (len(obs) - 2) if len(obs) > 2 else 0
        se = math.sqrt(s2 / sxx) if sxx > 0 and s2 > 0 else None
        qs = statistics.quantiles(xs, n=4) if len(set(xs)) > 4 else []
        buckets = []
        if qs:
            edges = [-9e9] + list(qs) + [9e9]
            for lo, hi in zip(edges, edges[1:]):
                sel = [y for x, y in zip(xs, ys) if lo <= x < hi]
                if not sel:
                    continue
                sd = statistics.stdev(sel) if len(sel) > 1 else None
                buckets.append({
                    'lo': None if lo < -8e9 else round(lo, 2),
                    'hi': None if hi > 8e9 else round(hi, 2),
                    'n': len(sel), 'mean': round(statistics.fmean(sel), 3),
                    'se': None if not sd else round(sd / math.sqrt(len(sel)), 3)})
        res[mk] = {'ok': True, 'n': len(obs), 'beta': round(beta, 4),
                   'se': None if se is None else round(se, 4),
                   'z': None if not se else round(beta / se, 2),
                   'mean_ratio': round(my, 4), 'buckets': buckets,
                   'days': len({o[2] for o in obs})}
    return res


def show_board_calibration(res):
    print('\nIS THE MATCHUP LOGIC WORKING?')
    if not res:
        print('  nothing logged yet. Run with --log for a few nights '
              'first.')
        return
    for mk in CATS:
        c = res.get(mk)
        if not c:
            continue
        print(f'\n  {TITLE.get(mk, mk)}')
        if not c.get('ok'):
            print(f'    {c["n"]} settled row(s). Not enough to measure.')
            continue
        print(f"    {c['n']} rows over {c['days']} day(s); "
              f"actual/baseline averages {c['mean_ratio']:.3f}")
        z = c.get('z')
        print(f"    slope {c['beta']:+.4f} per unit of matchup z"
              + (f" ({z:+.2f} SE)" if z is not None else ''))
        if c['buckets']:
            print(f"      {'MATCHUP z':>16}{'N':>6}{'ACT/BASE':>10}{'SE':>8}")
            for b in c['buckets']:
                lo = '' if b['lo'] is None else f"{b['lo']:+.2f}"
                hi = '' if b['hi'] is None else f"{b['hi']:+.2f}"
                print(f"      {lo + '..' + hi:>16}{b['n']:>6}"
                      f"{b['mean']:>10.3f}"
                      + (f"{b['se']:>8.3f}" if b['se'] else f"{'-':>8}"))
        if z is None:
            print('    -> no standard error; treat as undecided.')
        elif abs(z) < 2:
            print('    -> NOT ESTABLISHED. The ordering has not shown '
                  'itself at two\n       standard errors, so it stays '
                  'a hypothesis and no grade\n       goes on the page.')
        elif z >= 2:
            print('    -> the ordering PREDICTS: better matchups really '
                  'are beating\n       their own baselines by more. '
                  'This is what would earn the\n       letters back.')
        else:
            print('    -> the ordering runs BACKWARDS at two standard '
                  'errors. That is\n       a finding too, and a louder '
                  'one than a null.')


def publish(path, day, boards, ovl, pt, top=PUBLISH_TOP, logs_days=None):
    """Write data/boards.json for the site.

    WHAT GOES AND WHAT STAYS
      The reliability figures travel WITH the rows. The page is where
      someone who cannot run the script reads these, so it is exactly
      the place a 32% number must not arrive looking like a 100% one.
      Same rule the card's footing follows: if the evidence behind a
      column is thin, the column says so in its own heading rather
      than in a note somewhere else that will drift.

      No price is written either, because none was consulted. This
      panel answers "which matchups are best tonight", and the card
      answers "is any of that worth betting". Running them together
      on one page is fine; letting a reader think they are the same
      question is not.
    """
    d = os.path.join(os.path.expanduser(path), 'data')
    os.makedirs(d, exist_ok=True)
    keep = ('player', 'team', 'opp', 'base', 'mins', 'zs', 'lift',
            'why', 'parts')
    out = {
        'day': day, 'build': BUILD,
        'generated': datetime.datetime.now().astimezone().isoformat(
            timespec='seconds'),
        'reliability': boards.get('_rel') or {},
        'floors': boards.get('_floors') or {},
        'source': SOURCE, 'title': TITLE, 'model': MODEL,
        'unit': {'pts': 'pts', 'threes': '3PM', 'reb': 'reb', 'ast': 'ast'},
        'boards': {mk: [{k: r[k] for k in keep if k in r}
                        for r in boards[mk][:top]] for mk in CATS},
        'pra': ovl, 'points_threes': pt,
        'picks': top_picks(boards, day=day),
        'goto': goto_picks(boards),
        'note': ('No price was consulted. These are matchup rankings -- '
                 'which spots on tonight\'s slate the model likes, before '
                 'the market gets a vote. Whether any of them is worth '
                 'backing is a separate question that needs a line.'),
    }
    hist = goto_history(logs_days) if logs_days else []
    b, g, z = goto_record(hist)
    json.dump({'build': BUILD, 'generated': out['generated'],
               'beat': b, 'graded': g, 'mean_z': z, 'picks': hist},
              open(os.path.join(d, 'performance.json'), 'w'), indent=1)
    print(f"  wrote {os.path.join(d, 'performance.json')}: "
          f'{len(hist)} go-to pick(s), {g} graded')
    # THE BOX SCORES ARE NOT REFRESHED BY THIS SCRIPT.
    #
    # season_logs.json is built by `edges.py --build`, and nothing in
    # the nightly sequence called it -- so 15 go-to picks sat
    # correctly stamped in the board log while every single one
    # graded to None, and the page showed an empty record for two
    # days. `0 graded` out of 15 is not a quiet state and must not
    # print like one.
    stale = sorted({r['day'] for r in hist
                    if r.get('beat') is None and (r['day'] or '') < day})
    if stale:
        print(f'  !! {len(stale)} PAST day(s) have go-to picks with NO '
              f'box score:\n  !!   ' + ', '.join(stale))
        print('  !! season_logs.json is behind, so these cannot grade '
              'and the\n  !! performance page will show them blank. '
              'Catch it up with:\n  !!   python3 edges.py --build')

    fn = os.path.join(d, 'boards.json')
    json.dump(out, open(fn, 'w'), indent=1)
    n = sum(len(v) for v in out['boards'].values())
    print(f'\nwrote {fn}: {n} rows across {len(CATS)} boards, '
          f'{len(ovl)} PRA, {len(pt)} points+threes  [three.py {BUILD}]')
    return fn


def main():
    ap = argparse.ArgumentParser(description='three matchup boards, no price')
    ap.add_argument('day', nargs='?', help='YYYYMMDD')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--min-mpg', type=float, default=MIN_MPG,
                    dest='min_mpg',
                    help=f'minutes floor, all three boards '
                         f'(default {MIN_MPG:.0f})')
    ap.add_argument('--min-poss', type=float, default=MIN_POSS,
                    dest='min_poss',
                    help=f'possessions he finishes, points board only '
                         f'(default {MIN_POSS:.0f})')
    ap.add_argument('--per-game', type=int, default=PER_GAME,
                    dest='per_game',
                    help=f'max rows per game per board, 0 for no cap '
                         f'(default {PER_GAME})')
    ap.add_argument('--min-leg', type=float, default=0.0, dest='min_leg',
                    help='PRA candidates must clear this on EVERY leg, '
                         'not just appear on all three (default 0)')
    ap.add_argument('--env', action='store_true',
                    help='print every team\'s environment, ranked')
    ap.add_argument('--max-drift', type=float, default=MAX_DRIFT,
                    dest='max_drift',
                    help='withhold a points/threes row when this '
                         'season\'s defensive rating contradicts last '
                         f'season by more than this (default '
                         f'{MAX_DRIFT:.0f} = off)')
    ap.add_argument('--healthy-only', action='store_true',
                    dest='healthy_only',
                    help='also drop day-to-day and questionable players, '
                         'not just the ones ruled out')
    ap.add_argument('--log', action='store_true',
                    help='append every scored row to boards.jsonl so it '
                         'can be settled tomorrow (implied by --publish)')
    ap.add_argument('--no-log', action='store_true', dest='no_log',
                    help='do NOT log, even with --publish -- a dry run')
    ap.add_argument('--settle', metavar='YYYYMMDD',
                    help='what happened to the rows logged on that day')
    ap.add_argument('--environment', action='store_true',
                    help='what the market said the slate would score, '
                         'against what it did (read-only)')
    ap.add_argument('--seed-goto', action='store_true',
                    dest='seed_goto',
                    help='one-time: stamp goto_rank on the five '
                         'published 06 Nov, which predate the field')
    ap.add_argument('--drop-board-day', metavar='YYYYMMDD',
                    dest='drop_board_day',
                    help='remove that day from boards.jsonl so it can '
                         'be logged again')
    ap.add_argument('--blowout', action='store_true',
                    help='measure minutes lost to a blowout from the '
                         'logs, within player, by tier')
    ap.add_argument('--write-blowout', action='store_true',
                    dest='write_blowout',
                    help='with --blowout, write data/blowout.json and '
                         'turn the minutes term ON')
    ap.add_argument('--margin-sd', action='store_true', dest='margin_sd',
                    help='check the assumed 13.0 against logged spreads')
    ap.add_argument('--void-dnp', metavar='YYYYMMDD', dest='void_dnp',
                    help='mark that day\'s picks for men who never '
                         'dressed as void at 0 units')
    ap.add_argument('--repair-log', action='store_true',
                    help='backfill settler keys on rows written before '
                         '2026-11-02b (fixes publish.sh KeyError)')
    ap.add_argument('--fit-sd', action='store_true', dest='fit_sd',
                    help='measure the per-player SD against what the '
                         'board actually produced, per market. Reports '
                         'only; add --apply to write it')
    ap.add_argument('--apply', action='store_true',
                    help='with --fit-sd, commit the measured factors')
    ap.add_argument('--by-market', action='store_true',
                    dest='by_market',
                    help='per-market board accuracy and slip W/L, with '
                         'a verdict on whether each category belongs on '
                         'the slip')
    ap.add_argument('--tier', default='board',
                    help="which slate.jsonl tier --by-market reads: "
                         "board (three.py's own slip, the default), "
                         "core, log, or all")
    ap.add_argument('--calibrate', action='store_true',
                    help='does actual/baseline rise with the matchup z?')
    ap.add_argument('--publish', metavar='DIR',
                    help='also write DIR/data/boards.json for the site')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--audit-teams', action='store_true', dest='audit_teams',
                    help='which players are logged under more than one '
                         'team, and which of those look like scrape errors')
    a = ap.parse_args()
    _stamp()

    day = a.day
    if not day:
        import datetime
        day = datetime.datetime.now().astimezone().strftime('%Y%m%d')

    if a.repair_log:
        repair_log()
        return 0

    if not os.path.exists(LOGS):
        print(f'no {LOGS} -- run edges.py --build', file=sys.stderr)
        return 1
    logs_days = json.load(open(LOGS)).get('days') or {}
    if a.settle:
        show_settle(a.settle, settle_day(logs_days, a.settle))
        show_level(level_to_date(logs_days))
        _st = classify_picks(logs_days, a.settle)
        if _st['dnp']:
            print(f"\n  {len(_st['dnp'])} logged pick(s) for men who "
                  f"never dressed:")
            for r in _st['dnp']:
                print(f"    {r.get('subject')} ({r.get('team')}) "
                      f"{r.get('market')}")
            print(f'    void them:  python3 three.py --void-dnp '
                  f'{a.settle}')
        return 0

    if a.void_dnp:
        void_dnp(logs_days, a.void_dnp)
        return 0

    if a.environment:
        show_environment(environment_report(logs_days))
        return 0

    if a.seed_goto:
        seed_goto()
        hist = goto_history(logs_days)
        b, g, z = goto_record(hist)
        if g:
            print(f'  {b}/{g} beat their projection'
                  + (f', mean move {z:+.2f} sd' if z is not None else ''))
        else:
            print('  not gradable yet -- season_logs has no box scores '
                  'for that day.\n  Run the board once to pull them, '
                  'then --seed-goto again.')
        return 0

    if a.drop_board_day:
        drop_board_day(a.drop_board_day)
        return 0

    if a.blowout:
        for mgn in (15.0, 20.0, 25.0):
            measure_blowout(logs_days, margin=mgn,
                            write=(BLOWOUT if (a.write_blowout
                                               and mgn == BLOWOUT_MARGIN)
                                   else None))
        show_margin_sd(logs_days)
        return 0

    if a.margin_sd:
        show_margin_sd(logs_days)
        return 0

    if a.fit_sd:
        return show_fit_sd(fit_sd_props(logs_days), apply=a.apply)

    if a.by_market:
        # 'all' means do not filter on tier at all
        _tier = None if (a.tier or '').lower() == 'all' else a.tier
        show_market_breakdown(
            market_breakdown(logs_days, tier=_tier))
        return 0

    if a.calibrate:
        show_board_calibration(calibrate_boards(logs_days))
        return 0

    if a.audit_teams:
        show_audit(audit_teams(logs_days))
        try:
            show_minutes(minute_totals(games(before=day)))
        except Exception as e:
            print(f'\n  ! could not check minute totals: {e}')
        return 0

    gs = games(before=day)
    if gs and a.env:
        show_env(gs)
        return 0
    if not gs:
        print('no completed games in the cache yet', file=sys.stderr)
        return 1

    boards = build_boards(day, logs_days, gs, top=a.top,
                          min_mpg=a.min_mpg, min_poss=a.min_poss,
                          per_game=a.per_game, max_drift=a.max_drift,
                          healthy_only=a.healthy_only)
    if boards is None:
        print(f'no slate for {day}', file=sys.stderr)
        return 1
    ovl = overlap(boards, min_leg=a.min_leg)

    if a.json:
        print(json.dumps({'day': day, 'build': BUILD,
                          'boards': boards, 'pra': ovl}, indent=1))
        return 0
    show(day, boards, ovl)
    # PUBLISHING IS TAKING A POSITION, SO IT IS LOGGED BY DEFAULT.
    # Tonight's board went to the site twice with no --log either
    # time, which is how a published slip ends up with no record
    # behind it. --no-log is the dry run, and it has to be asked for.
    want_log = (a.log or bool(a.publish)) and not a.no_log
    if want_log:
        log_boards(day, boards, goto=goto_picks(boards))
        log_picks(day, top_picks(boards, day=day))
        if a.publish and not a.log:
            print('  (logged because --publish was given; --no-log '
                  'to skip)')
    elif a.publish:
        print('\n  NOT LOGGED -- --no-log was given. The site will show '
              'a slip\n  that nothing recorded, so tomorrow cannot '
              'grade it.')
    if a.publish:
        publish(a.publish, day, boards, ovl, points_threes(boards),
                logs_days=logs_days)
    log_audit(day, logs_days=logs_days)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
