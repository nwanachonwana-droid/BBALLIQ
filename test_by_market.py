"""Synthetic test for --by-market.

Builds boards.jsonl / slate.jsonl / season_logs rows with KNOWN
properties, then checks market_breakdown recovers them and that every
verdict and flag path fires. Statistics are hand-checked against
values computed by hand, so a wrong formula cannot pass by agreeing
with itself.
"""
import importlib.util, contextlib, io, json, math, os, random, sys
import tempfile

spec = importlib.util.spec_from_file_location('three', 'three.py')
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

FAIL = []


def ck(label, cond, detail=''):
    print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
          + (f'   [{detail}]' if detail and not cond else ''))
    if not cond:
        FAIL.append(label)


# ---------------------------------------------------------------
print('\n--- hand-checked statistics ---')
lo, hi = T._wilson(6, 10)
ck('wilson(6,10) lo=0.3127', abs(lo - 0.3127) < 5e-4, f'{lo:.4f}')
ck('wilson(6,10) hi=0.8318', abs(hi - 0.8318) < 5e-4, f'{hi:.4f}')
ck('breakeven(-110)=110/210',
   abs(T._breakeven(-110) - 110 / 210) < 1e-12)
ck('breakeven(+120)=100/220',
   abs(T._breakeven(120) - 100 / 220) < 1e-12)
ck('binom_p(0,10,.5)=2*0.5^10',
   abs(T._binom_p(0, 10, 0.5) - 2 * 0.5 ** 10) < 1e-12)
ck('binom_p(5,10,.5)=1.0', abs(T._binom_p(5, 10, 0.5) - 1.0) < 1e-12)
ck('wilson(0,0) -> (None,None)', T._wilson(0, 0) == (None, None))
ck('binom_p(n=0) -> None', T._binom_p(0, 0, 0.5) is None)
ck('breakeven(None) -> None', T._breakeven(None) is None)
nn = T._n_needed(2, 8, T._breakeven(-110))
ck('n_needed(2,8) is an int > 8', isinstance(nn, int) and nn > 8, repr(nn))
ck('n_needed None at/above breakeven',
   T._n_needed(6, 10, T._breakeven(-110)) is None)

# p_over is the null generator, so check it means what the code assumes
pr, dist = T.p_over(20.0, 19, 3.0, 'pts')
ck('p_over(20,19,sd3) == P(X>=20) ~0.566', abs(pr - 0.5662) < 2e-3,
   f'{pr:.4f} via {dist}')
ck('  ...and that is NOT 0.500 (why the old null was wrong)',
   abs(pr - 0.5) > 0.05, f'{pr:.4f}')

# ---------------------------------------------------------------
random.seed(11)
tmp = tempfile.mkdtemp()
BOARD = os.path.join(tmp, 'boards.jsonl')
SLATE = os.path.join(tmp, 'slate.jsonl')

FIELD = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
logs_days = {}
brows = []


def nm(prefix, i):
    """A unique, DIGIT-FREE name.

    norm_player STRIPS DIGITS, so 'pts man 17' and 'pts man 3' both
    normalise to 'pts man': 200 planted players collapse to one key
    and every board row resolves to the same box score. That is what
    made this test's first run report level 0.906 on data planted at
    1.000, and the identical mistake gave selftest.py a false pass
    earlier in this project. Letters only.
    """
    out, i = '', i + 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(97 + r) + out
    return f'{prefix} {out}'


def add(day, market, who, team, base, actual, sd, goto=None):
    """One board row plus its matching box-score row."""
    box = {'player': who, 'team': team, 'min': 30,
           'pts': None, 'tpm': None, 'reb': None, 'ast': None}
    box[FIELD[market]] = actual
    logs_days.setdefault(day, []).append(box)
    brows.append({
        'day': day, 'market': market,
        'player': T.norm_player(who), 'team': T.code(team),
        'opp': 'XXX', 'base': base, 'sd': sd, 'mins': 30,
        'market_on': True, 'goto_rank': goto})


def plant(market, prefix, team, n, base, sd_reported, true_mean,
          true_sd, goto_every=None):
    for i in range(n):
        act = max(0, int(round(random.gauss(true_mean, true_sd))))
        add(f'202611{(i % 8) + 10:02d}', market, nm(prefix, i), team,
            base, act, sd_reported,
            goto=(1 if goto_every and i % goto_every == 0 else None))


# pts    well calibrated: truth matches the reported base and sd
plant('pts', 'ptsman', 'BOS', 240, 20.0, 3.0, 20.0, 3.0, goto_every=40)
# threes OVER-PROJECTING: base 2.5 but truth centres on 2.1
plant('threes', 'thrman', 'DAL', 160, 2.5, 1.2, 2.1, 1.2, goto_every=40)
# reb    unbiased but sd TOO TIGHT: reports 2.0, truth spreads 4.0
plant('reb', 'rebman', 'ATL', 160, 8.0, 2.0, 8.0, 4.0)
# ast    well calibrated, smaller n
plant('ast', 'astman', 'NYK', 80, 6.0, 2.0, 6.0, 2.0)

# planted names must not collapse, or every number below is noise
_k = {(r['day'], r['player'], r['team'], r['market']) for r in brows}
assert len(_k) == len(brows), f'{len(brows)} rows -> {len(_k)} keys'

with io.open(BOARD, 'w', encoding='utf-8') as fh:
    for r in brows:
        fh.write(json.dumps(r) + '\n')

slip_rows = []


def bet(market, w, l, odds=-110):
    for i in range(w + l):
        slip_rows.append({
            'day': '20261110', 'tier': 'board',
            'subject': nm(f'{market}bettor', i), 'team': 'BOS',
            'market': T.SLATE_MARKET[market], 'side': 'over',
            'line': 1.5, 'odds': odds, 'unpriced': False,
            'result': 'win' if i < w else 'loss',
            'units': (0.909 if i < w else -1.0)})


bet('pts', 60, 30)      # .667 on n=90 -> KEEP
bet('threes', 2, 6)     # n=8          -> UNDERPOWERED
bet('reb', 12, 28)      # .300 on n=40 -> DROP
# ast deliberately gets no slip rows at all

with io.open(SLATE, 'w', encoding='utf-8') as fh:
    for r in slip_rows:
        fh.write(json.dumps(r) + '\n')

res = T.market_breakdown(logs_days, boardlog=BOARD, slate=SLATE,
                         tier='board')
b, s = res['board'], res['slip']

print('\n--- board recovers planted truth ---')
ck('pts n=240', b['pts']['n'] == 240, b['pts']['n'])
ck('every row got a model probability',
   all(v['graded_p'] == v['n'] for v in b.values()),
   {k: (v['graded_p'], v['n']) for k, v in b.items()})
ck('pts level ~1.00', abs(b['pts']['level'] - 1.0) < 0.03,
   f"{b['pts']['level']:.4f}")
ck('pts well calibrated: |z cal| < 2', abs(b['pts']['z_cal']) < 2.0,
   f"{b['pts']['z_cal']}")
ck('pts zsd ~1.0', abs(b['pts']['zsd'] - 1.0) < 0.15,
   f"{b['pts']['zsd']:.3f}")
ck('threes level ~0.84 (planted over-projection)',
   b['threes']['level'] < 0.95, f"{b['threes']['level']:.4f}")
ck('threes caught as OVERCONFIDENT: z cal < -2',
   b['threes']['z_cal'] < -2.0, f"{b['threes']['z_cal']}")
ck('threes obs < exp', b['threes']['rate'] < b['threes']['exp_rate'],
   f"{b['threes']['rate']:.3f} vs {b['threes']['exp_rate']:.3f}")
ck('reb sd too tight caught: zsd > 1.5', b['reb']['zsd'] > 1.5,
   f"{b['reb']['zsd']:.3f}")
ck('reb level still ~1.00 (the bias is NOT the problem there)',
   abs(b['reb']['level'] - 1.0) < 0.05, f"{b['reb']['level']:.4f}")
ck('brier in [0,1] everywhere',
   all(0 <= v['brier'] <= 1 for v in b.values()))
ck('rate == hit/n everywhere',
   all(abs(v['rate'] - v['hit'] / v['n']) < 1e-12 for v in b.values()))
ck('goto counted', b['pts']['goto_n'] == 6, b['pts']['goto_n'])

print('\n--- every slip verdict path ---')
ck('pts KEEP', s['pts']['verdict'] == 'KEEP', s['pts']['verdict'])
ck('threes UNDERPOWERED', s['threes']['verdict'] == 'UNDERPOWERED',
   s['threes']['verdict'])
ck('reb DROP', s['reb']['verdict'] == 'DROP', s['reb']['verdict'])
ck('ast absent from slip', 'ast' not in s, list(s))
ck('threes has an n_needed', s['threes']['n_needed'] is not None)
ck('breakeven from logged odds',
   abs(s['pts']['breakeven'] - 110 / 210) < 1e-12)
ck('reb hi < breakeven', s['reb']['hi'] < s['reb']['breakeven'],
   f"{s['reb']['hi']:.3f} vs {s['reb']['breakeven']:.3f}")
ck('pts lo > breakeven', s['pts']['lo'] > s['pts']['breakeven'],
   f"{s['pts']['lo']:.3f} vs {s['pts']['breakeven']:.3f}")

print('\n--- pushes and voids are not bets ---')
for r_ in ('push', 'void'):
    slip_rows.append({'day': '20261110', 'tier': 'board',
                      'subject': f'x{r_}', 'market': 'points',
                      'result': r_, 'odds': -110, 'units': 0.0,
                      'unpriced': False})
with io.open(SLATE, 'w', encoding='utf-8') as fh:
    for r in slip_rows:
        fh.write(json.dumps(r) + '\n')
r2 = T.market_breakdown(logs_days, boardlog=BOARD, slate=SLATE,
                        tier='board')['slip']
ck('decided unchanged by push/void', r2['pts']['decided'] == 90,
   r2['pts']['decided'])
ck('push counted', r2['pts']['push'] == 1)
ck('void counted', r2['pts']['void'] == 1)

print('\n--- tier filter ---')
slip_rows.append({'day': '20261110', 'tier': 'core', 'subject': 'core a',
                  'market': 'assists', 'result': 'win', 'odds': -110,
                  'units': 0.909, 'unpriced': False})
with io.open(SLATE, 'w', encoding='utf-8') as fh:
    for r in slip_rows:
        fh.write(json.dumps(r) + '\n')
ck('tier=board excludes the core row',
   'ast' not in T.market_breakdown(logs_days, boardlog=BOARD,
                                   slate=SLATE, tier='board')['slip'])
ck('tier=None includes it',
   'ast' in T.market_breakdown(logs_days, boardlog=BOARD,
                               slate=SLATE, tier=None)['slip'])

print('\n--- market_on False excluded ---')
brows.append({'day': '20261109', 'market': 'pts', 'player': 'zz top',
              'team': 'BOS', 'base': 20.0, 'sd': 3.0,
              'market_on': False, 'goto_rank': None})
logs_days.setdefault('20261109', []).append(
    {'player': 'zz top', 'team': 'BOS', 'pts': 99, 'min': 30})
with io.open(BOARD, 'w', encoding='utf-8') as fh:
    for r in brows:
        fh.write(json.dumps(r) + '\n')
ck('unscaled row not counted',
   T.market_breakdown(logs_days, boardlog=BOARD, slate=SLATE,
                      tier='board')['board']['pts']['n'] == 240)

print('\n--- missing files do not crash ---')
mt = tempfile.mkdtemp()
miss = T.market_breakdown({}, boardlog=os.path.join(mt, 'a.jsonl'),
                          slate=os.path.join(mt, 'b.jsonl'))
ck('missing -> empty dicts', miss['board'] == {} and miss['slip'] == {})

print('\n--- printer survives every shape ---')
for label, payload in (
        ('full', res), ('both empty', miss),
        ('board only', {'board': res['board'], 'slip': {},
                        'tier': 'board'}),
        ('slip only', {'board': {}, 'slip': res['slip'],
                       'tier': 'board'})):
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            T.show_market_breakdown(payload)
        ck(f'printer: {label}', True)
    except Exception as e:
        ck(f'printer: {label}', False, f'{type(e).__name__}: {e}')

print('\n================ REAL OUTPUT ================')
T.show_market_breakdown(res)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
