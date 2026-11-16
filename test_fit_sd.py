"""Tests for --fit-sd.

The claim that matters is NO COMPOUNDING: a refit on rows that were
already priced with a factor must return the same factor, not its
square. Everything else is guard rails.
"""
import importlib.util, io, json, math, os, random, statistics, sys
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


def nm(prefix, i):
    """Digit-free unique name -- norm_player strips digits."""
    out, i = '', i + 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(97 + r) + out
    return f'{prefix} {out}'


FIELD = {'pts': 'pts', 'threes': 'tpm', 'reb': 'reb', 'ast': 'ast'}
TRUE_FACTOR = 1.30


def build(tmp, sd_inf_in_rows):
    """Rows whose error is TRUE_FACTOR wider than the sd they carry.

    sd_inf_in_rows is the factor already applied to the stored sd, so
    the raw blended sd is stored_sd / sd_inf_in_rows. The planted
    truth is the same either way: the error is TRUE_FACTOR x raw.
    """
    random.seed(5)
    logs_days, brows = {}, []
    raw_sd = 3.0
    stored_sd = raw_sd * sd_inf_in_rows
    for i in range(400):
        base = 20.0
        # error is TRUE_FACTOR wider than the RAW sd
        act = max(0, int(round(base + random.gauss(
            0, raw_sd * TRUE_FACTOR))))
        day = f'202611{(i % 10) + 1:02d}'
        who = nm('sdman', i)
        box = {'player': who, 'team': 'BOS', 'min': 30,
               'pts': None, 'tpm': None, 'reb': None, 'ast': None}
        box['pts'] = act
        logs_days.setdefault(day, []).append(box)
        brows.append({'day': day, 'market': 'pts',
                      'player': T.norm_player(who), 'team': T.code('BOS'),
                      'opp': 'XXX', 'base': base, 'sd': stored_sd,
                      'sd_inf': sd_inf_in_rows, 'mins': 30,
                      'market_on': True, 'goto_rank': None})
    keys = {(r['day'], r['player']) for r in brows}
    assert len(keys) == len(brows), 'planted names collapse'
    bp = os.path.join(tmp, f'boards_{sd_inf_in_rows}.jsonl')
    with io.open(bp, 'w', encoding='utf-8') as fh:
        for r in brows:
            fh.write(json.dumps(r) + '\n')
    return logs_days, bp


tmp = tempfile.mkdtemp()

print('\n--- fit recovers the planted factor ---')
logs, bp = build(tmp, 1.0)
T._SDINF_CACHE = {}
res = T.fit_sd_props(logs, boardlog=bp)
ck('pts measured', 'pts' in res)
ck('n = 400', res['pts']['n'] == 400, res['pts']['n'])
ck(f'sd_z ~ {TRUE_FACTOR} (planted)',
   abs(res['pts']['sd_z'] - TRUE_FACTOR) < 0.12,
   f"{res['pts']['sd_z']:.4f}")
ck('mean_z ~ 0 (unbiased, as planted)',
   abs(res['pts']['mean_z']) < 0.12, f"{res['pts']['mean_z']:.4f}")
ck('enough rows', res['pts']['enough'])

print('\n--- NO COMPOUNDING: refit on corrected rows ---')
# identical truth, but the rows now carry sd already widened by 1.30
logs2, bp2 = build(tmp, TRUE_FACTOR)
T._SDINF_CACHE = {'pts': TRUE_FACTOR}
res2 = T.fit_sd_props(logs2, boardlog=bp2)
ck('refit returns the SAME factor, not its square',
   abs(res2['pts']['sd_z'] - TRUE_FACTOR) < 0.12,
   f"{res2['pts']['sd_z']:.4f} (square would be "
   f"{TRUE_FACTOR ** 2:.2f})")
ck('  ...and is nowhere near the square',
   abs(res2['pts']['sd_z'] - TRUE_FACTOR ** 2) > 0.3,
   f"{res2['pts']['sd_z']:.4f}")
ck('current factor is reported', res2['pts']['current'] == TRUE_FACTOR,
   res2['pts']['current'])

print('\n--- rows with no sd_inf are treated as factor 1.0 ---')
logs3, _ = build(tmp, 1.0)
bp3 = os.path.join(tmp, 'boards_nokey.jsonl')
rows = [json.loads(l) for l in io.open(bp, encoding='utf-8')]
for r in rows:
    r.pop('sd_inf', None)
with io.open(bp3, 'w', encoding='utf-8') as fh:
    for r in rows:
        fh.write(json.dumps(r) + '\n')
T._SDINF_CACHE = {}
res3 = T.fit_sd_props(logs3, boardlog=bp3)
ck('legacy rows still fit', abs(res3['pts']['sd_z'] - TRUE_FACTOR) < 0.12,
   f"{res3['pts']['sd_z']:.4f}")

print('\n--- guards ---')
T._SDINF_CACHE = {}
small = {k: v for k, v in list(logs.items())[:1]}
rs = T.fit_sd_props(small, boardlog=bp)
ck('below the floor it is not "enough"',
   rs['pts']['n'] < T.SD_INF_MIN_N and not rs['pts']['enough'],
   rs['pts']['n'])
ck('show_fit_sd writes nothing below the floor',
   T.show_fit_sd(rs, apply=True,
                 path=os.path.join(tmp, 'never.json')) == 1)
ck('  ...and no file appeared',
   not os.path.exists(os.path.join(tmp, 'never.json')))

print('\n--- clamping ---')
T._SDINF_CACHE = {}
wild = {'pts': []}
bpw = os.path.join(tmp, 'wild.jsonl')
lw, rw = {}, []
random.seed(9)
for i in range(200):
    who = nm('wild', i)
    day = '20261101'
    lw.setdefault(day, []).append(
        {'player': who, 'team': 'BOS', 'min': 30,
         'pts': max(0, int(round(20 + random.gauss(0, 30.0))))})
    rw.append({'day': day, 'market': 'pts', 'player': T.norm_player(who),
               'team': T.code('BOS'), 'base': 20.0, 'sd': 3.0,
               'sd_inf': 1.0, 'market_on': True, 'goto_rank': None})
with io.open(bpw, 'w', encoding='utf-8') as fh:
    for r in rw:
        fh.write(json.dumps(r) + '\n')
rwild = T.fit_sd_props(lw, boardlog=bpw)
ck('a wild sd_z is clamped to the cap',
   rwild['pts']['want'] == T.SD_INF_CAP, rwild['pts']['want'])
ck('and the raw value is kept for the printout',
   rwild['pts']['clamped_from'] > T.SD_INF_CAP,
   rwild['pts']['clamped_from'])

print('\n--- apply writes, and the loader reads it back ---')
T._SDINF_CACHE = {}
sp = os.path.join(tmp, 'sd_inflation.json')
rc = T.show_fit_sd(T.fit_sd_props(logs, boardlog=bp), apply=True,
                   path=sp)
ck('apply returns 0', rc == 0, rc)
ck('file written', os.path.exists(sp))
T._SDINF_CACHE = None
got = T.sd_inflation(path=sp)
ck('loader reads the factor back',
   abs(got.get('pts', 0) - TRUE_FACTOR) < 0.12, got)
ck('an absent market defaults to 1.0', got.get('reb', 1.0) == 1.0)

print('\n--- blended_sd actually applies it ---')
T._SDINF_CACHE = {'pts': 1.5}
curve = {'pts': 1.0}
sd_a, _, _ = T.blended_sd(16.0, 'pts', {}, curve)   # prior-only path
T._SDINF_CACHE = {}
sd_b, _, _ = T.blended_sd(16.0, 'pts', {}, curve)
ck('prior-only path is inflated',
   abs(sd_a / sd_b - 1.5) < 1e-9, f'{sd_a:.4f} vs {sd_b:.4f}')
spread = {('x', 'BOS', 'pts'): (16.0, 4.0, 20)}
T._SDINF_CACHE = {'pts': 1.5}
sd_c, _, _ = T.blended_sd(16.0, 'pts', spread, curve, key=('x', 'BOS'))
T._SDINF_CACHE = {}
sd_d, _, _ = T.blended_sd(16.0, 'pts', spread, curve, key=('x', 'BOS'))
ck('blended path is inflated',
   abs(sd_c / sd_d - 1.5) < 1e-9, f'{sd_c:.4f} vs {sd_d:.4f}')
T._SDINF_CACHE = {}
ck('a market with no factor is untouched',
   T.blended_sd(16.0, 'reb', {}, {'reb': 1.0})[0] == 4.0,
   T.blended_sd(16.0, 'reb', {}, {'reb': 1.0})[0])

print('\n--- a bad factor file cannot poison pricing ---')
bad = os.path.join(tmp, 'bad.json')
io.open(bad, 'w').write('{"factors": {"pts": 99.0, "reb": "x"}}')
T._SDINF_CACHE = None
gb = T.sd_inflation(path=bad)
ck('out-of-range factor rejected', 'pts' not in gb, gb)
ck('non-numeric factor rejected', 'reb' not in gb, gb)
io.open(bad, 'w').write('not json at all')
T._SDINF_CACHE = None
ck('unparseable file -> no factors', T.sd_inflation(path=bad) == {})

print('\n=============== REAL OUTPUT ===============')
T._SDINF_CACHE = {}
T.show_fit_sd(T.fit_sd_props(logs, boardlog=bp), apply=False)

print(f"\n{'ALL PASS' if not FAIL else 'FAILURES: ' + '; '.join(FAIL)}")
sys.exit(1 if FAIL else 0)
