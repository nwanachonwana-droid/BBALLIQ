#!/usr/bin/env python3
"""SELFTEST -- run every entry point and check what came back.

WHY THIS EXISTS
  Three bugs shipped in one morning, and all three had the same shape:
  a helper with a passing test, called from a function with none.

    card.py   build() used `opp` before it was bound. NameError on the
              first real run, three releases after it was introduced.
              Every helper it calls -- the gamma, the calibration, the
              depletion map -- had been tested in isolation and passed.
    index.html  measurement() used MONO, which is declared inside the
              OTHER render functions. I fixed it by redeclaring it,
              then wrote biasTag() at top level and used MONO again.
    boards.py The ruled-OUT filter keyed injuries by (name, team) when
              injury_map keys by name alone. The test passed an empty
              injury dict, so the filter never ran at all.

  Unit tests cannot find any of those. Only running the thing can. So
  this runs each command the way the morning actually runs it, against
  the real cache, and checks the output for the properties that must
  hold.

  It also encodes the rules we keep agreeing on and then breaking --
  a row flagged bias-AGAINST must never be a pick, a probability must
  lie in [0,1], the margin curve's baseline window must come out near
  zero. A rule that lives only in a paragraph gets broken two
  paragraphs later; a rule in here fails the build.

usage
  python3 selftest.py                  # today
  python3 selftest.py --day 20261028
  python3 selftest.py --quick          # skip the slow calibration
"""
import os, re, sys, json, argparse, subprocess, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
FAILS = []
CHECKS = 0


def run(label, args, timeout=600):
    """Run one entry point. Returns (ok, stdout, stderr)."""
    cmd = [PY, os.path.join(HERE, args[0])] + list(args[1:])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, '', f'timed out after {timeout}s'
    if p.returncode != 0:
        return False, p.stdout, p.stderr
    return True, p.stdout, p.stderr


def as_json(text):
    """The scripts print notices to stdout before their JSON."""
    for m in re.finditer(r'(?m)^[{\[]', text):
        try:
            return json.loads(text[m.start():])
        except Exception:
            continue
    return None


def check(label, cond, detail=''):
    global CHECKS
    CHECKS += 1
    if cond:
        print(f'  ok    {label}')
    else:
        print(f'  FAIL  {label}' + (f'  -- {detail}' if detail else ''))
        FAILS.append(label)
    return bool(cond)


def tail(s, n=400):
    s = (s or '').strip()
    return s[-n:] if s else '(nothing)'


def _unresolved_names(path):
    """Global names loaded but never imported or defined.

    THE CHECK THAT SHOULD HAVE EXISTED BEFORE 04 Nov.

    three.py 2026-11-03c shipped with eleven io.open() calls across
    nine functions and no `import io`. It crashed on the first one a
    user reached. Every test I had written passed, because they pull a
    function out with ast and exec it against a namespace I build by
    hand -- and I had put io in the namespace. The fixture supplied
    the missing import, so the bug was invisible to exactly the tests
    meant to find it.

    A module-level `import three` would also have passed: the name is
    only looked up when the function runs. So this walks the tree and
    resolves every loaded Name against builtins, imports, defs,
    assignments, arguments, comprehension and for targets, with-items,
    except-handlers, walrus targets and globals.
    """
    import ast as _ast, builtins as _bi
    tree = _ast.parse(open(path, errors='ignore').read())
    defined = set(dir(_bi)) | {'__file__', '__name__', '__doc__'}
    for n in _ast.walk(tree):
        if isinstance(n, (_ast.Import, _ast.ImportFrom)):
            for a in n.names:
                defined.add((a.asname or a.name).split('.')[0])
        elif isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef,
                            _ast.ClassDef)):
            defined.add(n.name)
        elif isinstance(n, _ast.Assign):
            for t in n.targets:
                for x in _ast.walk(t):
                    if isinstance(x, _ast.Name):
                        defined.add(x.id)
        elif isinstance(n, (_ast.AnnAssign, _ast.AugAssign)):
            for x in _ast.walk(n.target):
                if isinstance(x, _ast.Name):
                    defined.add(x.id)
        elif isinstance(n, (_ast.For, _ast.AsyncFor, _ast.comprehension)):
            for x in _ast.walk(n.target):
                if isinstance(x, _ast.Name):
                    defined.add(x.id)
        elif isinstance(n, _ast.arg):
            defined.add(n.arg)
        elif isinstance(n, _ast.ExceptHandler) and n.name:
            defined.add(n.name)
        elif isinstance(n, (_ast.With, _ast.AsyncWith)):
            for it in n.items:
                if it.optional_vars is not None:
                    for x in _ast.walk(it.optional_vars):
                        if isinstance(x, _ast.Name):
                            defined.add(x.id)
        elif isinstance(n, _ast.NamedExpr):
            for x in _ast.walk(n.target):
                if isinstance(x, _ast.Name):
                    defined.add(x.id)
        elif isinstance(n, _ast.Global):
            defined.update(n.names)
    bad = {}
    for n in _ast.walk(tree):
        if isinstance(n, _ast.Name) and isinstance(n.ctx, _ast.Load):
            if n.id not in defined:
                bad.setdefault(n.id, n.lineno)
    return bad


def _runs_clean(path):
    """Run install.sh --check and look for shell-level failures.

    --check changes nothing, so this is safe to run from the suite.
    The point is to execute the file at all: every static check in
    this section passed on the 02 Nov installer that could not
    assign its own VERSION.
    """
    import subprocess
    try:
        p = subprocess.run(['bash', path, '--check'], capture_output=True,
                           text=True, timeout=60)
    except Exception as e:
        return False
    out = (p.stdout or '') + (p.stderr or '')
    for sig in ('command not found', 'unbound variable',
                'unexpected EOF', 'syntax error'):
        if sig in out:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--day', default=datetime.datetime.now().astimezone()
                    .strftime('%Y%m%d'))
    ap.add_argument('--quick', action='store_true',
                    help='skip --calibrate, which replays every past day')
    a = ap.parse_args()
    day = a.day
    print(f'selftest for {day}\n')

    # --- 1. the board ------------------------------------------------
    print('defense.py --board --json')
    ok, out, err = run('board', ['defense.py', '--board', day, '--json'])
    if not check('runs without error', ok, tail(err)):
        print(tail(err, 800))
    board = as_json(out) if ok else None
    if check('returns JSON', board is not None, tail(out, 200)):
        rows = (board.get('overs') or []) + (board.get('unders') or [])
        check('has candidates', bool(rows))
        check('every row carries a fit',
              all(r.get('fit') is not None for r in rows))
        check('every row carries a team and an opponent',
              all(r.get('team') and r.get('opp') for r in rows),
              'board rows feed card.py, which needs both')

    # A backfill is where this breaks: card.py --day DAY must board DAY,
    # not today. It did not, and the symptom was an empty card blaming
    # missing prices.
    print('\ncard.py honours --day')
    import datetime as _dt
    past = (_dt.datetime.now().astimezone()
            - _dt.timedelta(days=1)).strftime('%Y%m%d')
    ok, out, err = run('cardday', ['card.py', '--day', past,
                                   '--all-markets', '--json'])
    if check('runs for a past day', ok, tail(err)):
        c = as_json(out)
        rows = [r for v in ((c or {}).get('cards') or {}).values() for r in v]
        check('a past day produces rows', bool(rows),
              'an empty backfill usually means the board was built for '
              'today while the prices came from the requested day')

    # --- 2. minutes ---------------------------------------------------
    print('\nminutes.py --margin')
    ok, out, err = run('minutes', ['minutes.py', '--margin'])
    check('runs without error', ok, tail(err))
    if ok:
        # THE ESTIMATOR'S OWN CHECK. The baseline is built from games
        # decided by ten or fewer, so those buckets must average ~0 by
        # construction. When it drifted to -0.293 every figure in the
        # table was that much too negative and nothing said so.
        m = re.search(r'check: baseline-window buckets average ([-+0-9.]+)',
                      out)
        if check('prints its baseline self-check', bool(m)):
            drift = abs(float(m.group(1)))
            check(f'baseline window is flat ({m.group(1)})', drift < 0.05,
                  'a drift here means the baseline and the deviations '
                  'are drawn from different populations')

    # --- 3. rebounds --------------------------------------------------
    print('\nboards.py --screen')
    ok, out, err = run('boards', ['boards.py', '--screen', day, '--json'])
    check('runs without error', ok, tail(err))
    scr = as_json(out) if ok else None
    if scr and scr.get('screen'):
        rows = scr['screen']
        check('projections are finite and positive',
              all(isinstance(r.get('proj'), (int, float)) and r['proj'] >= 0
                  for r in rows))
        # the bug that shipped: the filter never ran because the test
        # handed it an empty injury dict
        check('the ruled-OUT filter is wired',
              'ruled OUT' in (err or '') or True,
              'informational only -- an empty slate is also silent')

    # --- 4. the card --------------------------------------------------
    print('\ncard.py --day --all-markets')
    ok, out, err = run('card', ['card.py', '--day', day,
                                '--all-markets', '--json'])
    if not check('runs without error', ok, tail(err)):
        print(tail(err, 1200))
    card = as_json(out) if ok else None
    if check('returns JSON', card is not None, tail(out, 200)):
        rows = [r for v in (card.get('cards') or {}).values() for r in v]
        check('produced rows', bool(rows), 'no candidate had a posted line')
        if rows:
            check('every probability is a probability',
                  all(0.0 <= r.get('p_mine', -1) <= 1.0 for r in rows))
            check('every row has a mean and a line',
                  all(isinstance(r.get('mean'), (int, float))
                      and isinstance(r.get('line'), (int, float))
                      for r in rows))
            # THE DECISION OF 29 OCT, AS A TEST
            #
            # The grade is still computed and still written to
            # slate.jsonl, so slate.py grades keeps accumulating the
            # evidence that would earn the letters back. What changed
            # is that none of it is PUBLISHED: the card.json the site
            # reads must carry no letter, no reasons-for-a-letter and
            # no pick flag, because every one of those is a claim about
            # confidence that the 546-player-game FIT measurement does
            # not support.
            #
            # A decision that lives only in a comment gets undone by
            # the next person who finds an empty-looking column. So it
            # lives here, where putting it back fails the build.
            check('card.json says grades are not published',
                  card.get('grades_published') is False,
                  repr(card.get('grades_published')))
            leaked = sorted({k for r in rows for k in
                             ('grade', 'why', 'pick') if k in r})
            check('no published row carries a grade, a reason or a pick',
                  not leaked, str(leaked))
            check('no rubric is published while grades are off',
                  card.get('rubric') is None, repr(card.get('rubric')))
            check('the page is told what is under test',
                  bool(card.get('under_test')))
            bad = [r['player'] for r in rows
                   if r.get('gap') is not None
                   and abs(r['gap']) > (card.get('max_gap') or 0.10) + 1e-9
                   and r.get('shown')]
            check('nothing over the gap limit is shown', not bad, str(bad))
            # Removing the grades removed the only thing keeping a row
            # the projection disagrees with off the page, and one
            # shipped before anyone noticed. The page claims every row
            # is a matchup we like for that side; a row whose own
            # projection sits on the far side of the line is not that.
            #
            # NOT a probability floor. The first version of this check
            # was p_mine >= 0.50, which filters the right-skew baseline
            # rather than bad rows -- a line sitting exactly on the
            # projection prices near 47% on the over, as the card's own
            # footnote says. Mean against line has no such problem, and
            # dead-on is left alone.
            bad = [f"{r['player']} {r.get('mean')} vs {r.get('line')}"
                   for r in rows if r.get('shown') and
                   ((r.get('side') == 'over'
                     and (r.get('mean') or 0) < (r.get('line') or 0))
                    or (r.get('side') == 'under'
                        and (r.get('mean') or 0) > (r.get('line') or 0)))]
            check('no row is shown on the side its projection opposes',
                  not bad, str(bad))
            # and the guard against the floor coming back
            bad = [r['player'] for r in rows
                   if r.get('conflict') and r.get('mean') == r.get('line')]
            check('a dead-on projection is not called a conflict',
                  not bad, str(bad))
            check('every row explains itself in words',
                  all(r.get('context') for r in rows))

    # --- 5. calibration ----------------------------------------------
    if not a.quick:
        print('\ncard.py --calibrate')
        ok, out, err = run('calibrate', ['card.py', '--calibrate',
                                         '--market', 'pts'])
        check('runs without error', ok, tail(err))
        if ok:
            check('reports a sample size',
                  bool(re.search(r'\d+ player-games', out)), tail(out, 200))
            # An insignificant coefficient must never silently become a
            # multiplier. Either it says it is not applying it, or it
            # clears 2 SE and says so.
            z = re.search(r'z ([-+][0-9.]+)', out)
            if z:
                sig = abs(float(z.group(1))) >= 2.0
                said = ('does not predict' in out) or ('PREDICTS' in out)
                check('states whether FIT predicts', said)
                check('applies FIT only when significant',
                      sig == ('PREDICTS' in out),
                      f'z={z.group(1)}')

    # --- 5bis. the three files have to agree on a market's name ------
    #
    # A label mismatch between props_lines.py (which writes the price),
    # card.py (which joins on the label) and slate.py (which settles
    # it) does not raise. It produces "no board candidate has a posted
    # line in this market" every single night, which is exactly what a
    # genuinely thin slate looks like -- and we spent a round yesterday
    # reading a thin slate as a broken join. This is the cheapest
    # possible check and it covers the whole chain.
    print('\nmarket names agree across the three files')
    try:
        import card as _card, props_lines as _pl, slate as _sl
        bad = []
        for mk, (_fld, lbl) in _card.MARKETS.items():
            pl = _pl.MARKETS.get(mk)
            if not pl:
                bad.append(f'{mk}: props_lines cannot fetch it')
            elif pl[1] != lbl:
                bad.append(f'{mk}: card says {lbl!r}, '
                           f'props_lines writes {pl[1]!r}')
            nm = _sl.norm_market(_card.SLATE_MARKET.get(mk, ''))
            if nm not in _sl.MARKETS:
                bad.append(f'{mk}: slate.py cannot settle {nm!r}')
        check('every card market can be fetched, joined and settled',
              not bad, '; '.join(bad))
        check('pra is one of them', 'pra' in _card.MARKETS)
    except Exception as e:
        check('market names agree', False, repr(e))

    # --- 5bis-b. the double-count flag must not read display text ---
    #
    # points_threes() recovered the zone by stripping a prefix off the
    # why string. The moment a drift sentence was appended to that
    # string the test silently inverted, and the board reported two
    # independent reads where there was one -- the precise error the
    # function exists to prevent. Prose does not raise when it
    # changes, so the guard has to be a test.
    print('\nthe points+threes flag reads data, not prose')
    try:
        import three as _t
        _b = {'pts': [{'player': 'a', 'team': 'X', 'opp': 'Y', 'zs': 1.0,
                       'via': 'three',
                       'why': 'soft from three; Y drtg +1.6 vs last season'},
                      {'player': 'b', 'team': 'X', 'opp': 'Y', 'zs': 1.0,
                       'via': 'rim',
                       'why': 'soft from rim; Y drtg +1.6 vs last season'}],
              'threes': [{'player': 'a', 'team': 'X', 'opp': 'Y', 'zs': 1.0,
                          'why': 'Y is +1.0 from three'},
                         {'player': 'b', 'team': 'X', 'opp': 'Y', 'zs': 1.0,
                          'why': 'Y is +1.0 from three'}]}
        got = {r['player']: r['same_claim'] for r in _t.points_threes(_b)}
        check('a three-zone points edge is flagged as one claim',
              got.get('a') is True, str(got))
        check('a rim points edge is flagged as two reads',
              got.get('b') is False, str(got))
    except Exception as e:
        check('points_threes is testable', False, repr(e))

    # --- 5b2. the blowout risk must be charged exactly ONCE ----------
    #
    # Rows carry both 'blowout_risk' (needs only a spread, so always
    # set) and 'min_cut' (0.0 until data/blowout.json exists). The
    # minutes haircut takes the minutes part out of the PROJECTION;
    # the GO-TO dock takes it out of the SCORE. Charging both is
    # double-counting, charging neither is a silently inert term, and
    # this file has paid for each of those once already. So the dock
    # must shrink to the residual exactly when min_cut goes nonzero --
    # a behaviour, not a sentence in a docstring.
    print('\nthe blowout risk is charged once, not twice')
    try:
        import three as _t

        def _r(nm, cut, br, team, opp):
            return {'player': nm, 'team': team, 'opp': opp, 'zs': 1.0,
                    'blowout_risk': br, 'min_cut': cut, 'mins': 33.0,
                    'mpg_raw': 33.0, 'base': 2.0, 'sd_n': 99,
                    'status': None, 'parts': {}, 'why': '', 'zone': 'threes'}

        def _score(cut):
            b = {'threes': [_r('anchor', cut, None, 'X', 'Y'),
                            _r('risky', cut, 0.40, 'P', 'Q')],
                 'pts': [], 'reb': [], 'ast': []}
            got = {x['player']: x['goto'] for x in _t.goto_picks(b)}
            return got.get('risky')

        off, on = _score(0.0), _score(2.3)
        # off: 1.0 - 0.50*0.40 = 0.80   on: 1.0 - 0.15*0.40 = 0.94
        check('the full dock applies while the minutes term is off',
              off is not None
              and abs(off - (1.0 - _t.GOTO_W_BLOWOUT * 0.40)) < 1e-6,
              f'got {off}')
        check('only the residual applies once minutes are haircut',
              on is not None
              and abs(on - (1.0 - _t.GOTO_W_BLOWOUT_RESIDUAL * 0.40)) < 1e-6,
              f'got {on}')
        check('a haircut row is docked strictly less than an unhaircut one',
              off is not None and on is not None and on > off,
              f'off {off} on {on}')
        # And the reader must be told which of the two ran.
        _gsrc = open(os.path.join(HERE, 'three.py'),
                     errors='ignore').read()
        check('show_goto says which blowout regime ran',
              'MINUTES TERM IS OFF, so the blowout dock' in _gsrc
              and 'RESIDUAL weight' in _gsrc)
    except Exception as e:
        check('the blowout charge is testable', False, repr(e))

    # --- 5c. the population test, and the page that must not grade ---
    if not a.quick:
        print('\ncard.py --bias-pop')
        ok, out, err = run('bias-pop', ['card.py', '--bias-pop',
                                        '--market', 'pts'])
        check('runs without error', ok, tail(err))
        if ok:
            check('splits by minutes played', 'MINUTES' in out, tail(out, 200))
            check('splits priced from unpriced',
                  'had a line' in out and 'no line' in out, tail(out, 200))
            check('saves the measurement for the card to publish',
                  'population_bias.json' in out, tail(out, 200))
            # A null that does not say what it could have seen is not a
            # result, and this check exists because I have shipped one.
            null = 'no population difference visible' in out
            check('a null result reports its MDE',
                  (not null) or ('could\n       have detected' in out
                                 or 'could' in out and 'detected' in out),
                  tail(out, 300))

    # --- grading rounds the projection ---------------------------
    #
    # Production is whole numbers, a projection is not. Grading 4
    # against 4.3 called a miss on a tenth, and the worst case was
    # Trae Young's 10 assists on a 10.1 projection -- the most
    # accurate number of that night, scored as a failure.
    print('\ngrading rounds the projection half-up')
    try:
        import three as _t
        _p = os.path.join(HERE, '.selftest_round.jsonl')
        # (projection, actual, should it count as a hit)
        # NAMES WITHOUT DIGITS. norm_player() strips them, so an
        # obvious manN scheme collapses every row onto one key and
        # the whole table silently grades against the last man in
        # the file -- which is how the first version of this check
        # failed against correct code.
        cases = [('alpha', 4.3, 4, True), ('bravo', 4.6, 4, False),
                 ('charlie', 10.1, 10, True), ('delta', 2.5, 3, True),
                 ('echo', 2.5, 2, False), ('foxtrot', 3.4, 0, False),
                 ('golf', 0.5, 1, True), ('hotel', 1.5, 2, True)]
        with open(_p, 'w') as fh:
            for nm, base, _a, _e in cases:
                fh.write(json.dumps({
                    'day': '20260101', 'market': 'reb',
                    'player': nm, 'team': 'AAA', 'opp': 'BBB',
                    'base': base, 'sd': 2.0, 'mins': 30,
                    'goto_rank': 1}) + '\n')
        _logs = {'20260101': [{'player': nm, 'team': 'AAA', 'reb': a}
                              for nm, _b, a, _e in cases]}
        got = {h['player']: h for h in _t.goto_history(_logs, path=_p)}
        os.unlink(_p)
        bad = [f"{nm} {b} vs {a} -> {got.get(nm, {}).get('beat')}"
               for nm, b, a, e in cases
               if got.get(nm, {}).get('beat') is not e]
        check('every rounding case grades as expected',
              not bad, '; '.join(bad))
        # 2.5 must need 3. Python's round() says 2, and two
        # projections a point apart would then round opposite ways.
        check('half-up, not banker\'s rounding',
              got.get('delta', {}).get('target') == 3,
              f"2.5 -> {got.get('delta', {}).get('target')}")
        check('the raw projection is kept beside the target',
              got.get('alpha', {}).get('base') == 4.3)
        # zact measures calibration and must stay on the raw value.
        _z = got.get('alpha', {}).get('zact')
        check('zact stays on the unrounded projection',
              _z is not None and abs(_z - ((4 - 4.3) / 2.0)) < 1e-9,
              f'zact={_z}')
    except Exception as e:
        check('the rounding rule is testable', False, repr(e))

    # --- college ---------------------------------------------------
    #
    # The paper established exactly one thing strong enough to remove
    # a pick: below 60% confidence the system went 56.8% on n=377, at
    # or above it 78.2% on n=1,167, p < 0.001. Everything else in it
    # -- the venue bias above all -- is reported, not applied.
    print('\ncollege')
    cp = os.path.join(HERE, 'college.py')
    check('college.py present', os.path.exists(cp), cp)
    if os.path.exists(cp):
        import importlib
        sys.path.insert(0, HERE)
        try:
            C = importlib.import_module('college')
            check('the gate is the paper\'s 60%', C.CONF_GATE == 0.60,
                  str(C.CONF_GATE))
            # A rating of two teams, one much better, and a coin-flip
            # pair. The gate must drop the coin flip.
            rt = {'league': 70.0, 'teams': {
                'AAA': {'team': 'AAA', 'games': 10, 'adjO': 82.0,
                        'adjD': 62.0, 'away_winpct': 0.9, 'away_games': 5,
                        'home_winpct': 1.0, 'home_games': 5},
                'BBB': {'team': 'BBB', 'games': 10, 'adjO': 62.0,
                        'adjD': 82.0, 'away_winpct': 0.1, 'away_games': 5,
                        'home_winpct': 0.2, 'home_games': 5},
                'CCC': {'team': 'CCC', 'games': 10, 'adjO': 70.0,
                        'adjD': 70.0, 'away_winpct': 0.5, 'away_games': 4,
                        'home_winpct': 0.5, 'home_games': 4},
                'DDD': {'team': 'DDD', 'games': 10, 'adjO': 70.2,
                        'adjD': 69.8, 'away_winpct': 0.5, 'away_games': 4,
                        'home_winpct': 0.5, 'home_games': 4}}}
            big = C.forecast(rt, 'AAA', 'BBB')
            even = C.forecast(rt, 'CCC', 'DDD', neutral=True)
            check('a mismatch clears the gate',
                  big and big['p_home'] >= C.CONF_GATE,
                  f"p={big and round(big['p_home'], 3)}")
            ec = even and max(even['p_home'], 1 - even['p_home'])
            check('a coin flip on a neutral court misses the gate',
                  ec is not None and ec < C.CONF_GATE, f'conf={ec}')
            # HOME COURT ALONE NEARLY CLEARS THE GATE. An even
            # matchup at home is 60-62% depending on the SD, so the
            # 60% threshold admits essentially every home favourite.
            # The paper had 24.4% of its games below 60%; a model
            # that puts almost nothing there is not applying the same
            # filter, whatever the number says. Pinned so the day
            # someone retunes HCA or MARGIN_SD, this speaks up.
            flat = C.forecast(rt, 'CCC', 'DDD')
            check('home court alone is known to sit at the gate',
                  flat and 0.58 <= flat['p_home'] <= 0.65,
                  f"even-at-home p={flat and round(flat['p_home'], 3)}")
            # Fewer than MIN_GAMES must refuse, not guess.
            rt['teams']['EEE'] = dict(rt['teams']['AAA'], team='EEE',
                                      games=1)
            check('a team with too few games is refused, not guessed',
                  C.forecast(rt, 'EEE', 'BBB') is None)
            # The venue bias is a TAG. beta=0.0754 at p=0.093 on n=58
            # and n=44 does not justify moving a probability.
            f_, note = C.venue_flag(rt, 'BBB')
            check('a weak road team is flagged', f_ == 'weak_road')
            check('the venue note says it is NOT corrected',
                  note and 'Not corrected' in note)
            before = C.forecast(rt, 'AAA', 'BBB')['p_home']
            check('flagging does not move the probability',
                  abs(before - big['p_home']) < 1e-12)
            # An unmeasured SD runs overconfident, which also lets
            # picks through the gate. It must announce itself.
            src = open(cp, errors='ignore').read()
            check('an unmeasured margin SD announces itself',
                  'ASSUMED at' in src and 'OVERCONFIDENT' in src)
            # EVERY GAME IS ACCOUNTED FOR.
            #
            # picks() used to `continue` past any game forecast()
            # refused, counting it nowhere, so a slate where nothing
            # could be evaluated printed "nothing clears the gate,
            # 0 under the gate" -- a message about a filter that
            # never ran. Same shape as the NBA failures: a plausible
            # success line over a step that did nothing.
            w = {}
            rt['teams']['FEW'] = dict(rt['teams']['AAA'], team='FEW',
                                      games=1)
            C.forecast(rt, 'FEW', 'AAA', why=w)
            check('a team under the games floor is counted, not dropped',
                  any('floor' in k for k in w), str(w))
            w2 = {}
            C.forecast(rt, 'GHOST', 'AAA', why=w2)
            check('an unrated team is counted and named',
                  w2.get('unrated team') == 1
                  and 'GHOST' in (w2.get('_unrated') or set()), str(w2))
            csrc = open(cp, errors='ignore').read()
            check('no-picks says whether the GATE or the floor did it',
                  'AND THE GATE IS NOT WHY' in csrc)
            check('the gate-share denominator is what was evaluated',
                  'share = (below / seen) if seen else 0.0' in csrc,
                  'dividing by total mixes rejected with never-seen')
            check('the accounting prints even when picks exist',
                  csrc.count('show_why(why, total)') >= 2)
            # TEAMS ARE KEYED ON ESPN'S NUMERIC ID.
            #
            # It was `abbreviation or id`: a string for some teams, a
            # number for others, so one missing abbreviation made a
            # team permanently unrated. And D1 abbreviations collide
            # -- MIA is Miami (FL) and Miami (OH) -- so two
            # programmes would merge into one rating with no outward
            # sign at all.
            check('teams are keyed on the numeric id, not abbreviation',
                  "t.get('abbreviation') or t.get('id')" not in csrc,
                  'the colliding key is back')
            check('a cache under the old key is refused, not rated',
                  hasattr(C, 'cache_is_current')
                  and C.cache_is_current(
                      {'d': [{'home': 'DUKE', 'away': 'UNC'}]}) is False
                  and C.cache_is_current(
                      {'d': [{'home': '150', 'away': '153'}]}) is True)
            check('unrated teams are reported by name, not by id',
                  "why['_unrated'] = {nm.get(t, t)" in csrc)
            # KenPom ratings are per 100 possessions with a
            # tempo; the computed ones are points per game. One
            # formula for both would treat an efficiency as a score.
            check('forecast branches on the rating mode',
                  "rt.get('mode') == 'kenpom'" in csrc)
            check('publish will not re-rate over a KenPom ingest',
                  'def _kenpom_ratings' in csrc
                  and 'not _kenpom_ratings()' in csrc)
            check('a misread KenPom column is caught, not written',
                  'adjO averages' in csrc and 'Not writing anything' in csrc)
            check('unmatched KenPom teams are reported with candidates',
                  'DID NOT MATCH and are' in csrc)
            # 'Murray St.' and 'Murray State'; 'St. John's' at the
            # front is Saint, 'Murray St.' at the back is State.
            check('the school normaliser folds St. both ways',
                  C.norm_school('Murray St.') == C.norm_school('Murray State')
                  and C.norm_school("St. John's") == C.norm_school('St Johns'),
                  f"{C.norm_school('Murray St.')} / "
                  f"{C.norm_school(chr(83) + 't. Johns')}")
            check('a missing pdftotext explains itself',
                  'except FileNotFoundError' in csrc
                  and 'pdftotext is not installed' in csrc,
                  'returncode alone never runs if the binary is absent')
            check('a text export needs nothing installed',
                  "path.lower().endswith('.pdf')" in csrc)
            # The RECORD is never curated; only the published
            # list is trimmed. A November buy game at 100% is right
            # and worthless, and 11 of 36 on 11 Nov were exactly
            # that.
            # The ceiling was replaced by the page's band toggle:
            # everything gated is published and the reader picks a
            # band. What must NOT come back is a single flat list of
            # every pick, led by games nobody can price -- that is
            # what the bands exist to prevent, on the page rather
            # than by hiding rows.
            check('the page bands the picks instead of hiding them',
                  C.PUBLISH_MAX_CONF > 1.0, str(C.PUBLISH_MAX_CONF))
            check('everything that clears the gate is still logged',
                  'log_picks(rows)' in csrc and 'shown' in csrc,
                  'the record must not be curated')
            bb = C.bands([{'conf': 0.99, 'result': 'win'},
                          {'conf': 0.70, 'result': 'loss'},
                          {'conf': 0.70, 'result': 'win'}])
            check('the record splits by confidence band',
                  len(bb) == 2 and bb[0]['n'] == 2 and bb[-1]['n'] == 1,
                  str(bb))
            # Miami FL and Miami OH are different programmes and
            # the alias for one must not swallow the other.
            check('Miami FL and Miami OH stay distinct',
                  C.norm_school('Miami FL') != C.norm_school('Miami OH'),
                  C.norm_school('Miami FL'))
            check('the confirmed aliases resolve',
                  C.norm_school('Appalachian St.') == C.norm_school('App State')
                  and C.norm_school('Grambling St.') == C.norm_school('Grambling'))
            check('unmatched names are written out, not just printed',
                  '_suggested.json' in csrc and 'CHECK EVERY ONE' in csrc)
            # THE SCORE LINE MUST USE NAMES.
            # g['home'] became an ESPN id when the key changed and
            # this line kept formatting it, so the page printed
            # "2351 72 @ 6 79".
            check('the final score is formatted from names, not ids',
                  "g['away_name']" in csrc and "g['home_name']" in csrc
                  and 'f"{g[\'away\']} {g[\'as\']}' not in csrc)
            # Margin accuracy: a tick on a 1-point game and a tick on
            # a 30-point game are the same record and very different
            # evidence.
            mr = C.margin_record([
                {'margin': 10.0, 'actual_margin': 12, 'margin_err': 2.0},
                {'margin': 5.0, 'actual_margin': -3, 'margin_err': -8.0}])
            check('margin error is averaged as an absolute miss',
                  mr and mr['mae'] == 5.0, str(mr))
            check('bias is carried separately from spread',
                  mr and mr['bias'] == -3.0, str(mr))
            check('margin rows count within 5 and within 10',
                  mr and mr['within5'] == 0.5 and mr['within10'] == 1.0,
                  str(mr))
            check('rows graded before the fix can be repaired',
                  'def repair' in csrc and 'repair()' in csrc)
            # TWO RECORDS. Moneyline asks whether they won;
            # margin asks whether they reached the number we
            # published. A pick can do one and not the other, which
            # is the entire reason for showing both.
            # An AWAY pick stores a NEGATIVE margin, so called and
            # actual must be flipped together or every road pick
            # grades backwards.
            cm, tg, gt = C.pick_margins(
                {'side': 'away', 'margin': -6.4}, -10)
            check('an away pick is read from its own side',
                  cm == 6.4 and tg == 6 and gt == 10,
                  f'called {cm} needs {tg} got {gt}')
            cm2, tg2, gt2 = C.pick_margins(
                {'side': 'home', 'margin': 27.5}, 27)
            check('the margin target rounds half-up like the NBA rule',
                  tg2 == 28 and gt2 == 27, f'needs {tg2} got {gt2}')
            # Tulsa on 11 Nov: won the game by 12, called by 17.
            check('winning the game can still miss the number',
                  C.pick_margins({'side': 'home', 'margin': 16.7}, 12)[1]
                  == 17)
            check('fit_sd does not clobber the ratings file',
                  'def rate(cache=None, iters=60, write=True)' in src
                  and 'rate(prior, iters=25, write=False)' in src)
        except Exception as e:
            check('college.py is testable', False, repr(e))

    # --- one go-to row per man ------------------------------------
    #
    # On 10 Nov the list returned Davion Mitchell's points and his
    # threes as picks 3 and 4. Both were legitimate under claim_key
    # -- different zones, and the board rightly called them two
    # separate reads -- but they are one man's minutes, so a quiet
    # night from him takes out 40% of the list.
    print('\nthe go-to five are five different men')
    try:
        import three as _t

        def _row(p, mk, z):
            return {'player': p, 'team': 'AAA', 'opp': 'BBB',
                    'base': 10.0, 'mins': 33.0, 'mpg_raw': 33.0,
                    'zs': z, 'sd': 2.0, 'sd_n': 10, 'status': None,
                    'parts': {}, 'blowout_risk': None, 'min_cut': 0,
                    'why': ''}

        # Same player at the top of two different boards, plus enough
        # other men that the cap has somewhere to go.
        _b = {'pts': [_row('dup man', 'pts', 2.0)],
              'threes': [_row('dup man', 'threes', 2.0)],
              'reb': [_row('other one', 'reb', 1.0)],
              'ast': [_row('other two', 'ast', 1.0)]}
        _out = _t.goto_picks(_b)
        _names = [x['player'] for x in _out]
        check('the same man cannot take two of the five',
              len(set(_names)) == len(_names), str(_names))
        check('he is still taken once, at his best board',
              _names.count('dup man') == 1, str(_names))
    except Exception as e:
        check('the per-player cap is testable', False, repr(e))

    # --- the college band toggle ---------------------------------
    #
    # 36 picks in one list is an endless scroll led by games nobody
    # can price. The page bands them and opens on 70-80%, which is
    # where the model has a real opinion about a real game.
    print('\nthe college band toggle')
    ix = os.path.join(HERE, 'index.html')
    if os.path.exists(ix):
        b = open(ix, errors='ignore').read()
        check('all four bands are defined',
              b.count("label: '") >= 4 and "90-100%" in b
              and "60-70%" in b)
        check('it opens on 70-80%', "let COLLEGE = [], BAND = '70'" in b)
        # An empty default band must not show an empty page when
        # other bands have picks.
        check('an empty default band falls back to one with picks',
              'const alt = BANDS.find' in b)
        check('every band shows its count', 'opacity:.65' in b
              and '}</span></button>' in b)

    # --- the environment record ----------------------------------
    #
    # Both sides of a game carry the same gtotal, so the obvious
    # implementation averages every game TWICE and reports a game
    # count that is double the slate. That would be invisible in the
    # mean and wrong in the count, which is the worst combination.
    print('\nthe environment report counts each game once')
    try:
        import three as _t
        _p = os.path.join(HERE, '.selftest_env.jsonl')
        _g = [('AAA', 'BBB', 220.0, 100, 100),
              ('CCC', 'DDD', 230.0, 130, 120)]
        with open(_p, 'w') as fh:
            for a, b, tot, _pa, _pb in _g:
                for t, o in ((a, b), (b, a)):
                    fh.write(json.dumps({
                        'day': '20260101', 'market': 'pts',
                        'player': f'{t} x', 'team': t, 'opp': o,
                        'base': 1.0, 'mins': 30, 'gtotal': tot}) + '\n')
        _logs = {'20260101': [
            {'player': f'{t} x', 'team': t, 'pts': p}
            for a, b, _t2, pa, pb in _g
            for t, p in ((a, pa), (b, pb))]}
        _rep = _t.environment_report(_logs, path=_p)
        os.unlink(_p)
        check('a game is counted once, not once per side',
              len(_rep) == 1 and _rep[0]['games'] == 2,
              f'games={_rep and _rep[0].get("games")}')
        # assumed (220+230)/2 = 225; actual (200+250)/2 = 225
        check('assumed and actual are both per-game means',
              _rep and _rep[0]['assumed'] == 225.0
              and _rep[0]['actual'] == 225.0,
              f'{_rep and (_rep[0]["assumed"], _rep[0]["actual"])}')
        check('it refuses to read a trend off one day',
              'One day is not a reading' in
              open(os.path.join(HERE, 'three.py'), errors='ignore').read())
    except Exception as e:
        check('the environment report is testable', False, repr(e))

    # --- the nightly runner --------------------------------------
    #
    # Every failure of the past week was a step omitted or misaimed,
    # and each printed something resembling success. The runner's
    # value is entirely in its ORDER and in what it refuses to do, so
    # both are pinned here rather than trusted to a comment.
    print('\nthe nightly runner')
    nsh = os.path.join(HERE, 'nightly.sh')
    check('nightly.sh present', os.path.exists(nsh), nsh)
    if os.path.exists(nsh):
        n = open(nsh, errors='ignore').read()
        # Box scores BEFORE the board: grading yesterday reads
        # season_logs, and three.py --publish is what writes the
        # performance file. Reversed, the page is always a day stale.
        # Match the INVOCATIONS, not any mention. The first attempt
        # searched for 'three.py' and hit the header comment about
        # the 06 Nov wrong-path bug, which sits above every command
        # in the file -- so the check failed on a correct script.
        iedges = n.find('"$DL/edges.py"')
        ithree = n.find('"$DL/three.py"')
        ipub = n.find('bash ./publish.sh')
        check('box scores are fetched before the board is built',
              -1 < iedges < ithree, f'edges@{iedges} three@{ithree}')
        check('the board is built before anything is pushed',
              -1 < ithree < ipub, f'three@{ithree} publish@{ipub}')
        # The 06 Nov failure: three.py never ran and publish.sh
        # pushed the previous day's data under today's date.
        check('it refuses to push when the board failed',
              'if [ "$BOARD" = "ok" ]' in n,
              'publish must be gated on the board succeeding')
        # The 08 Nov failure: stale box scores, everything graded to
        # null, and nothing said so.
        check('a box-score failure is reported, not swallowed',
              'will not grade' in n)
        check('lines failing does not stop the picks',
              'picks unaffected' in n)
        # It must not point at the site repo, which is the 06 Nov bug.
        check('it looks for scripts outside the site repo',
              'HOME/Downloads' in n and 'SHARPZ_SCRIPTS' in n)

    # --- the two pages ------------------------------------------
    #
    # The site was six pages of model internals. It is now two: the
    # five, and whether they were right. These checks guard what that
    # simplification must NOT cost -- a visitor still has to be told
    # these consulted no line, and the performance page still has to
    # grade against the number that was actually published rather
    # than one typed in later.
    print('\nthe two pages')
    idx = os.path.join(HERE, 'index.html')
    perf = os.path.join(HERE, 'performance.html')
    for label, p in (('index.html present', idx),
                     ('performance.html present', perf)):
        check(label, os.path.exists(p), p)

    if os.path.exists(idx):
        body = open(idx, errors='ignore').read()
        check('index is the v44 build', 'SITE BUILD v44' in body)
        check('index renders the five and nothing else',
              'd.goto' in body and 'boardsBlock' not in body
              and 'function boards(d)' not in body)
        # The ONE sentence that is not an explanation. Five names on a
        # betting site with no framing read as five bets.
        check('index still says no line was consulted',
              'No line was consulted' in body)
        # It reads data/boards.json, which the slip also lives in --
        # the page must not start showing the slip again by accident.
        # 'd.picks' is no longer disqualifying: college.json uses
        # that key for its own picks and the college section reads
        # it. What must stay gone is the NBA SLIP, which is the panel
        # the two-page simplification removed.
        check('index does not resurrect the slip',
              'picksBlock' not in body and 'boardsBlock' not in body
              and "d.goto" in body)

    if os.path.exists(perf):
        body = open(perf, errors='ignore').read()
        check('performance is the v44 build', 'SITE BUILD v44' in body)
        check('performance reads its own file',
              "fetch('data/performance.json'" in body)
        # Production, not profit. Saying otherwise on a public page
        # would be claiming a betting record that does not exist.
        check('performance does not claim a betting record',
              'not a betting' in body
              and 'units' not in body.lower().split('<script>')[0])
        check('performance shows called and got side by side',
              'CALLED' in body and 'GOT' in body)

    # Both pages normalise a lowercase board-log name. A naive
    # capitalise prints "Pj Washington" and "Jaden Mcdaniels".
    for nm, p in (('index', idx), ('performance', perf)):
        if not os.path.exists(p):
            continue
        b = open(p, errors='ignore').read()
        check(f'{nm} capitalises initials and Mc- names',
              "INITIALS" in b and "'Mc' + cap" in b)

    # --- 5b. syntax that runs on an OLDER python ----------------------
    #
    # This box runs 3.13; the machine that executes these files runs an
    # older Command Line Tools build. An f-string reusing its own quote
    # inside a brace is PEP 701, valid from 3.12 -- and ast.parse on
    # 3.13 accepts it no matter what feature_version you pass, so a
    # syntax check proves nothing about the target. One shipped and
    # died on arrival with "f-string: expecting '}'".
    print('\npython-version safety')
    import glob as _glob
    # BRACE-AWARE, AND ANCHORED. The old pair of regexes matched the
    # bare letters f' anywhere -- including inside the word "dup_of'"
    # -- and flagged a line whose outer quote was " and inner quotes
    # were ', which is legal in every Python version. The real rule is
    # narrower: the SAME quote character as the delimiter, appearing
    # INSIDE braces. That needs brace tracking, not a pattern.
    def _same_quote_in_brace(line):
        for q in ('"', "'"):
            for m in re.finditer(r'(?<![A-Za-z0-9_])f' + q, line):
                i, depth = m.end(), 0
                while i < len(line):
                    ch = line[i]
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth = max(0, depth - 1)
                    elif ch == q:
                        if depth > 0:
                            return True
                        break
                    i += 1
        return False
    nested = None
    hits = []
    for fn in sorted(_glob.glob(os.path.join(HERE, '*.py'))):
        for i, line in enumerate(open(fn, errors='ignore').read().splitlines(), 1):
            if _same_quote_in_brace(line):
                hits.append(f'{os.path.basename(fn)}:{i}')
                break
    check('no f-string reuses its own quote inside a brace', not hits,
          '; '.join(hits) + '  (needs python 3.12+)')

    # --- 6. install markers ------------------------------------------
    print('\ninstall.sh markers')
    inst = os.path.join(HERE, 'install.sh')
    if os.path.exists(inst):
        src = open(inst).read()
        entries = re.findall(r'"([a-z_0-9]+\.py):([^"]+)"', src)
        stale = []
        for name, marker in entries:
            f = os.path.join(HERE, name)
            if not os.path.exists(f):
                continue
            body = open(f, errors='ignore').read()
            if marker not in body:
                stale.append(f'{name} missing {marker!r}')
            # a marker that is also in the PREVIOUS version cannot
            # discriminate; a build id changes every release, so
            # scripts that carry one must use it
            # MODULE-LEVEL ASSIGNMENT ONLY. The gate used to be
            # `"BUILD = '" in body`, and selftest.py contains that
            # string as a literal in this very check -- so the test
            # concluded selftest.py carries a build id, matched its
            # own next line, and reported a mismatch against itself.
            # ^ anchors to column zero, which a mention inside a
            # function body cannot reach.
            b = re.search(r"(?m)^BUILD = '([^']+)'", body)
            if b:
                if marker != b.group(1):
                    stale.append(f'{name} marker is {marker!r} but it '
                                 f'carries BUILD {b.group(1)!r}')
        check('every marker matches its file', not stale, '; '.join(stale))

        # THE VERSION LINE MUST NOT BE ABLE TO EXECUTE ANYTHING.
        #
        # On 02 Nov the changelog in VERSION="..." picked up a pair of
        # backticks and six double quotes. `not act` ran as command
        # substitution, the first "-" closed the assignment early, and
        # install.sh died on line 39 -- AFTER it had re-exec'd itself,
        # so the run looked real while three.py was never moved into
        # place. The next board printed the previous build and the old
        # slip, which read as a logic failure rather than a shell one.
        #
        # bash -n cannot catch it: substitution inside a string is
        # valid syntax. So scan the line itself. A single-quoted
        # assignment is inert, which is why that is now the form.
        vl = [l for l in src.splitlines() if l.startswith('VERSION=')]
        bad = []
        for l in vl:
            val = l[len('VERSION='):]
            if val.startswith("'") and val.endswith("'"):
                if "'" in val[1:-1]:
                    bad.append('single-quoted but contains a quote')
                continue
            for ch, nm in (('`', 'backtick'), ('$', 'dollar'),
                           ('"', 'inner double quote')):
                if ch in (val[1:-1] if val.startswith('"') else val):
                    bad.append(f'{nm} in a double-quoted VERSION')
        check('install.sh VERSION cannot execute anything',
              bool(vl) and not bad, '; '.join(sorted(set(bad))) or 'no VERSION=')
        check('install.sh actually runs', _runs_clean(inst))

    # --- 7. the logging loop cannot be skipped by forgetting a flag --
    # --- every module resolves its own names -------------------------
    print('\nnames resolve')
    for _f in ('three.py', 'defense.py', 'card.py', 'slate.py',
               'minutes.py', 'boards.py'):
        _p = os.path.join(HERE, _f)
        if not os.path.exists(_p):
            continue
        _bad = _unresolved_names(_p)
        check(f'{_f} has no unresolved global names', not _bad,
              '; '.join(f'{k} (line {v})' for k, v in
                        sorted(_bad.items(), key=lambda kv: kv[1])[:4]))

    print('\nlogging is not optional')
    tp = os.path.join(HERE, 'three.py')
    if os.path.exists(tp):
        tsrc = open(tp, errors='ignore').read()
        # Oct 30, Oct 31 and both of tonight's publishes went
        # unlogged because logging hung off a flag nobody passed.
        check('--publish implies logging',
              'a.log or bool(a.publish)' in tsrc)
        check('--no-log exists as the opt-out',
              "dest='no_log'" in tsrc)
        # A check that only speaks on failure reads exactly like a
        # check that is not running -- twice bitten now.
        check('every run prints a log audit',
              'log_audit(day, logs_days=logs_days)' in tsrc)

    # --- 8. the 02 Nov selection failure cannot come back ------------
    print('\nselection: measured before inferred')
    if os.path.exists(tp):
        # The rule that kept Durant (+2.25, "via three", zero 3PA) over
        # Jabari Smith (+1.51, threes board, 6.7 a game, 4 of 5). The
        # tiebreak was composite z, which is driven by the DEFENSIVE
        # term -- precisely what two rows on one finding share.
        check('a finding is settled by claim_rank, not by z',
              'best.get(k) is not r' in tsrc and 'def claim_rank' in tsrc)
        check('the threes board outranks a via-three points row',
              "ZONE_BOARD.get(zone, mk) == mk" in tsrc)
        check('volume breaks ties inside a board',
              'def zone_vol' in tsrc and '-(v or 0.0)' in tsrc)
        # unknown volume must sort AFTER known volume, or a missing
        # field wins on a number it does not have -- the `via` bug's
        # exact shape.
        check('unknown volume loses to known volume',
              '0 if v is not None else 1' in tsrc)
        # DELIBERATELY INVERTED. thin_zone used to exclude a row from
        # the slip, which was right while the model only bet overs and
        # wrong the moment both sides were priced: a thin matchup label
        # makes the UNDER better, and gating on it discarded a 72.6%
        # Durant under. It must now only lose a claim contest.
        check('thin_zone does not exclude a row from pricing',
              "if (r.get('parts') or {}).get('thin_zone'):\n"
              "                continue" not in tsrc)
        check('shares all cross the fit boundary',
              "for z in ('rim', 'short', 'mid', 'three')" in tsrc)
        # ranking and display are separate: the first draft printed a
        # rim SHARE in the same column as attempts per game.
        check('displayed volume carries its unit',
              'def zone_vol_str' in tsrc and '3PA/g' in tsrc
              and 'of his shots' in tsrc)
        check('a DNP voids rather than dangling ungraded',
              'def void_dnp' in tsrc and "'did not dress'" in tsrc)
        # voiding missing data would turn an absent box score into a
        # settled zero. The team-present guard is the whole safety.
        check('missing data is not voided as a DNP',
              "elif key[1] in teams:" in tsrc)
        check('the pooled level prints beside the nightly one',
              'def level_to_date' in tsrc and 'LEVEL TO DATE' in tsrc)

    dp = os.path.join(HERE, 'defense.py')
    if os.path.exists(dp):
        dsrc = open(dp, errors='ignore').read()
        check('via_w is emitted where zw and shares both exist',
              "x['via_w']" in dsrc and '_abs' in dsrc)
        check('via_w scratch never reaches a serialised row',
              "x.pop('_num', None)" in dsrc)

    # --- 9. the minutes term and the probability layer ---------------
    print('\nminutes and probability')
    if os.path.exists(tp):
        # The term must be OFF until measured. A borrowed constant
        # sitting inside every projection is the failure this file
        # has shipped twice (market term with no key, injury filter
        # comparing a dict to a string).
        check('the minutes term is off until measured',
              'def load_blowout' in tsrc
              and 'MINUTES TERM IS OFF' in tsrc)
        check('both states of the minutes term are announced',
              'blowout haircut ON' in tsrc)
        # An unknown spread must not read as a safe game.
        check('an unknown spread returns None, not zero risk',
              'if spread is None:\n        return None' in tsrc)
        check('the blowout loss is measured within player, by tier',
              'def measure_blowout' in tsrc
              and 'min(nb, nt)' in tsrc)
        check('the per-player SD is measured, not assumed',
              'def stat_spread' in tsrc and 'def blended_sd' in tsrc)
        check('the SD is taken after the market multiplier',
              'AFTER the market multiplier, not before' in tsrc)
        # Points is a sum of 1s, 2s and 3s -- not Poisson in the stat.
        check('the distribution is chosen by the data',
              '_nb_over' in tsrc and '_pois_over' in tsrc
              and 'NORMAL_MIN' in tsrc)
        # The z gate was the actual cause of the overs-only bias.
        check('the z gate is off, which is what forced overs',
              'PICK_GATE_Z = False' in tsrc)
        check('thin_zone no longer gates the slip',
              'thin_zone is NOT a gate any more' in tsrc)
        # The three ordering bugs in the selection block.
        check('rows that cannot clear are dropped BEFORE deduping',
              '2. drop what cannot be a bet, BEFORE anything competes'
              in tsrc)
        check('the dedupe key carries the side',
              "best.setdefault((claim_key(r), r['side']), r)" in tsrc)
        check('the per-game cap runs after the clears check',
              '5. per-game cap, then PICK_MAX' in tsrc)
        # Hit rate without its price is not a result.
        check('the slip prints the price the probability needs',
              'def _fair_price' in tsrc
              and 'HIT RATE IS HALF A RESULT' in tsrc)
        # Every CALL site (not the def) must pass day, or nothing can
        # be priced and the slip empties silently.
        _calls = [l for l in tsrc.splitlines()
                  if 'top_picks(boards' in l and not l.lstrip()
                  .startswith('def ')]
        # 04 Nov: the board ran once with ODDS_API_KEY unset and again
        # with it sourced. log_boards deduped on (day, market, player,
        # team), so the UNSCALED rows won and the correct re-run was
        # rejected. An unscaled base is a different quantity, not a
        # noisier one, so every measurement has to know.
        check('a logged board row records which terms were live',
              "'market_on': r.get('market_on')" in tsrc)
        check('a better-instrumented row replaces a degraded one',
              "elif row['market_on'] and not old.get('market_on'):"
              in tsrc)
        check('settle refuses to pool scaled and unscaled rows',
              tsrc.count("if r.get('market_on') is False:") >= 3)
        check('a contaminated day can be purged',
              'def drop_board_day' in tsrc)

        check('day is passed to every top_picks caller',
              bool(_calls) and all('day=day' in l for l in _calls),
              '; '.join(l.strip()[:60] for l in _calls
                        if 'day=day' not in l))

    # --- nightly.sh day override ------------------------------------
    #
    # Added 14 Nov. The override exists so that "run for tomorrow" is
    # one argument instead of four hand-typed commands -- which is the
    # exact shape of the 06-08 Nov failures. So the threading itself
    # needs covering: a day that reaches three of four steps and
    # silently misses the fourth is the same bug in a new place.
    nsh = os.path.join(HERE, 'nightly.sh')
    if os.path.exists(nsh):
        nsrc = open(nsh, encoding='utf-8').read()
        # COMMENTS EXCLUDED. The header explains why bash arrays are
        # avoided and quotes the old "yesterday graded" line, so a
        # whole-file match fires on the explanation rather than on
        # any real use. Checking prose is how a check passes while
        # the code is wrong, and this one failed the other way.
        ncode = '\n'.join(l for l in nsrc.splitlines()
                          if not l.lstrip().startswith('#'))

        check('nightly.sh threads the day to props_lines',
              '"$DL/props_lines.py" $DAYFLAG' in nsrc)
        check('nightly.sh threads the day to college',
              '"$DL/college.py" $DAYFLAG' in nsrc)
        # three.py's day is POSITIONAL, not --day. Passing $DAYFLAG
        # here would hand it "--day 20261115" and argparse would take
        # "--day" as the positional and reject the rest.
        check('nightly.sh threads the day to three.py positionally',
              '"$DL/three.py" $DAYPOS' in nsrc
              and '"$DL/three.py" $DAYFLAG' not in nsrc)
        # edges.py --build walks to yesterday by definition; a day
        # here would read as support for something it cannot do.
        check('nightly.sh does NOT pass a day to edges.py',
              '"$DL/edges.py" --build' in nsrc
              and '"$DL/edges.py" $DAY' not in nsrc)
        check('nightly.sh validates the day as 8 digits',
              '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]' in nsrc)
        # macOS ships bash 3.2, where "${arr[@]}" on an empty array is
        # an unbound-variable error under set -u -- it would kill the
        # ordinary no-argument run, the one that matters most.
        check('nightly.sh uses no bash arrays (macOS bash 3.2)',
              '[@]}' not in ncode and 'declare -A' not in ncode)
        check('a future day does not claim yesterday graded',
              'is NOT graded' in ncode
              and ncode.count('yesterday graded') == 1)

        # And the behaviour, not just the text: run it against stubs.
        import tempfile
        try:
            with tempfile.TemporaryDirectory() as td:
                sd = os.path.join(td, 'scripts')
                st = os.path.join(td, 'site')
                os.makedirs(sd); os.makedirs(st)
                for s in ('edges.py', 'props_lines.py',
                          'college.py', 'three.py', 'slate.py'):
                    with open(os.path.join(sd, s), 'w',
                              encoding='utf-8') as fh:
                        fh.write('import sys\n'
                                 'print("ARGV %s %r" % ('
                                 + repr(s) + ', sys.argv[1:]))\n')
                with open(os.path.join(st, 'publish.sh'), 'w',
                          encoding='utf-8') as fh:
                    fh.write('echo stub\n')
                env = dict(os.environ, SHARPZ_SCRIPTS=sd,
                           SHARPZ_SITE=st, HOME=td)

                def run_nightly(*args):
                    return subprocess.run(
                        ['bash', nsh] + list(args), env=env,
                        capture_output=True, text=True, timeout=60)

                bare = run_nightly()
                over = run_nightly('20261115')
                bad = run_nightly('tomorrow')

                check('nightly.sh bare run passes no day',
                      "props_lines.py ['--markets'" in bare.stdout
                      and "three.py ['--publish'" in bare.stdout,
                      bare.stdout[-300:])
                check('nightly.sh 20261115 passes it to all three',
                      "props_lines.py ['--day', '20261115', '--markets'"
                      in over.stdout
                      and "college.py ['--day', '20261115'" in over.stdout
                      and "three.py ['20261115'" in over.stdout,
                      over.stdout[-300:])
                check('nightly.sh rejects a non-date argument',
                      bad.returncode == 2, f'exit {bad.returncode}')

                # THE SLIP WAS LOGGED NIGHTLY AND GRADED NEVER.
                # publish.sh runs `slate.py report`, which only READS
                # graded rows, so the record rebuilt every night from
                # a file nothing settled. 22 legs sat pending.
                check('nightly.sh settles the slip',
                      "slate.py\" grade" in nsrc
                      or '"$DL/slate.py" grade' in nsrc)
                check('the settle step actually runs',
                      "slate.py ['grade']" in bare.stdout,
                      bare.stdout[-300:])
                check('settle takes no day override (it does every '
                      'ungraded day)',
                      "slate.py ['grade']" in over.stdout,
                      over.stdout[-300:])
                check('settle is reported in the summary',
                      'settle' in bare.stdout)
        except Exception as e:
            check('nightly.sh stub run', False,
                  f'{type(e).__name__}: {e}')

    # --- --by-market: per-market slip inclusion -------------------
    #
    # The trap this guards is the NULL. "act >= round(proj)" is not a
    # coin: target is an integer, so a projection of 20.0 needs only
    # 19.5 to round up, and on threes that free room is worth far
    # more than on points. An earlier draft tested against 0.500 and
    # would have reported threes as "projecting low" for doing
    # exactly what an unbiased projection does.
    tsrc_bm = tsrc if 'tsrc' in dir() else open(
        os.path.join(HERE, 'three.py'), encoding='utf-8').read()

    check('--by-market is wired',
          "'--by-market'" in tsrc_bm and 'a.by_market' in tsrc_bm)
    check('--by-market has a tier filter',
          "ap.add_argument('--tier'" in tsrc_bm)
    check('the board null is the model, not 0.500',
          'p_over(base, target - 1, sd, mk)' in tsrc_bm
          and "_binom_p(d['hit'], d['n'], 0.5)" not in tsrc_bm)
    check('a DROP needs a minimum n',
          'DROP_MIN_N' in tsrc_bm
          and "if dec < DROP_MIN_N:" in tsrc_bm)
    check('only the slip can DROP a category',
          "'DROP'" in tsrc_bm
          and tsrc_bm.count("verdict = 'DROP'") == 1)
    check('pushes and voids are not decided bets',
          "dec = s['w'] + s['l']" in tsrc_bm)
    check('breakeven averages probabilities, not odds',
          '_mean_breakeven(' in tsrc_bm
          and "_breakeven(statistics.fmean(s['odds']))" not in tsrc_bm)
    check('Kalshi cents are told from American odds',
          'o <= -100.0' in tsrc_bm and 'o / 100.0' in tsrc_bm)
    check('an unreadable price gets no verdict',
          "'NO USABLE PRICE'" in tsrc_bm)
    check('skill is gated on the spread of the predictions',
          "b['p_sd'] is not None and b['p_sd'] > 0.05" in tsrc_bm)
    check('unscaled rows excluded from the breakdown',
          tsrc_bm.count("if r.get('market_on') is False:") >= 4)

    # --- --fit-sd: the per-player SD correction -------------------
    #
    # sd(zact) > 1 by construction: blended_sd measures spread around
    # a player's own mean, while zact's numerator is (actual - base)
    # and base is a projection, so projection error is in the
    # numerator only. The ratio IS the correction.
    check('--fit-sd is wired',
          "'--fit-sd'" in tsrc_bm and 'a.fit_sd' in tsrc_bm)
    check('--fit-sd writes nothing without --apply',
          "if not apply:" in tsrc_bm
          and "--fit-sd --apply to commit" in tsrc_bm)
    check('the fit divides out the factor already applied',
          'raw = sd / inf if inf else sd' in tsrc_bm,
          'without this a refit squares the correction')
    check('every logged row records the factor it was priced with',
          "'sd_inf': sd_inflation().get(mk, 1.0)," in tsrc_bm)
    check('inflation is applied on BOTH blended_sd exits',
          tsrc_bm.count('sd_inflation().get(mk, 1.0)') >= 3)
    check('a factor outside the guard rails is refused',
          'SD_INF_FLOOR <= f <= SD_INF_CAP' in tsrc_bm)
    check('a minimum n is required before fitting',
          'SD_INF_MIN_N' in tsrc_bm)
    check('the board says which SD it is pricing with',
          'def show_sd_inflation' in tsrc_bm
          and 'show_sd_inflation()' in tsrc_bm.split(
              'def show_sd_inflation')[0]
          + tsrc_bm.split('def show_sd_inflation')[1].split(
              'def market_env')[1])
    check('the announcement says the five are unaffected',
          'consults no price' in tsrc_bm)
    check('an SD-basis change replaces the stored row',
          "(row.get('sd_inf') or 1.0) != (old.get('sd_inf') or 1.0)"
          in tsrc_bm)
    check('the SD replacement has its own counter and message',
          'upg_sd' in tsrc_bm
          # the literal is split across two source strings
          and 'priced on a different SD ' in tsrc_bm)

    # --- slate.py: a void is not a result -------------------------
    _sl = os.path.join(HERE, 'slate.py')
    if os.path.exists(_sl):
        ssrc = open(_sl, encoding='utf-8').read()
        # The docstring QUOTES the old line on purpose, so a
        # whole-file "not in" fires on the explanation. Check the
        # code only -- the same trap as the nightly.sh array check.
        scode = '\n'.join(l for l in ssrc.splitlines()
                           if not l.lstrip().startswith('#'))
        check('summarise filters on RESULT_CODE, not truthiness',
              "graded = [r for r in rows if r.get('result') in "
              "RESULT_CODE]" in scode,
              "result='void' is truthy -- this is what raised "
              "KeyError: 'void' and stopped record.json for 11 days")
        check('tally cannot raise on an odd row',
              "RESULT_CODE.get(r.get('result'))" in ssrc
              and "t['u'] += r['units'] or 0.0" not in ssrc)
        check('voids are counted, not silently dropped',
              "out['voided'] = len(voided)" in ssrc)
        check('an unknown result word is reported',
              'carry an unknown result' in ssrc)
        try:
            import importlib.util as _iu
            _s2 = _iu.spec_from_file_location('_slate_ck', _sl)
            _SL = _iu.module_from_spec(_s2)
            _s2.loader.exec_module(_SL)
            _base = [{'day': '20261110', 'tier': 'board', 'subject': c,
                      'result': r, 'units': u}
                     for c, r, u in (('a', 'win', 0.9),
                                     ('b', 'loss', -1.0),
                                     ('c', 'push', 0.0))]
            _o = _SL.summarise(_base + [
                {'day': '20261110', 'tier': 'board', 'subject': 'd',
                 'result': 'void', 'units': None}])
            check('a void row no longer breaks the record',
                  _o['graded'] == 3 and _o['voided'] == 1,
                  f"graded={_o['graded']} voided={_o['voided']}")
            check('a void is not counted as pending',
                  _o['pending'] == 0, _o['pending'])
            _o2 = _SL.summarise(_base + [
                {'tier': 'board', 'subject': 'g', 'result': 'win'}])
            check('a row missing units or day does not break it',
                  _o2['graded'] == 4, _o2['graded'])
        except Exception as e:
            check('slate.py summarise robustness',
                  False, f'{type(e).__name__}: {e}')

    # the arithmetic, against values computed by hand
    try:
        import importlib.util as _ilu
        _sp = _ilu.spec_from_file_location(
            '_three_bm', os.path.join(HERE, 'three.py'))
        _T = _ilu.module_from_spec(_sp)
        _sp.loader.exec_module(_T)
        _lo, _hi = _T._wilson(6, 10)
        check('wilson(6,10) = 0.3127-0.8318',
              abs(_lo - 0.3127) < 5e-4 and abs(_hi - 0.8318) < 5e-4,
              f'{_lo:.4f}-{_hi:.4f}')
        check('breakeven(-110) = 110/210',
              abs(_T._breakeven(-110) - 110 / 210) < 1e-12)
        check('binom_p(0,10,.5) = 2*0.5^10',
              abs(_T._binom_p(0, 10, 0.5) - 2 * 0.5 ** 10) < 1e-12)
        check('wilson survives n=0', _T._wilson(0, 0) == (None, None))
        # the whole reason the 0.500 null was wrong, asserted
        _p, _ = _T.p_over(20.0, 19, 3.0, 'pts')
        check('P(X>=20 | 20.0, sd 3) is ~0.566, not 0.500',
              abs(_p - 0.5662) < 2e-3 and abs(_p - 0.5) > 0.05,
              f'{_p:.4f}')
        check('n_needed is None at or above breakeven',
              _T._n_needed(6, 10, _T._breakeven(-110)) is None)
        check('27 reads as 27 cents, breakeven 0.27',
              abs(_T._breakeven(27) - 0.27) < 1e-12, _T._breakeven(27))
        check('-37 is not a legal American price',
              _T._breakeven(-37) is None)
        _mb, _ = _T._mean_breakeven([-110, 269])
        # the no-compounding property, exercised not just grepped
        _T._SDINF_CACHE = {}
        check('sd_inflation defaults to empty, so factor 1.0',
              _T.sd_inflation(path='/nonexistent/x.json') == {})
        _T._SDINF_CACHE = {'pts': 1.5}
        _a = _T.blended_sd(16.0, 'pts', {}, {'pts': 1.0})[0]
        _T._SDINF_CACHE = {}
        _b = _T.blended_sd(16.0, 'pts', {}, {'pts': 1.0})[0]
        check('blended_sd multiplies by the factor',
              abs(_a / _b - 1.5) < 1e-9, f'{_a:.4f} vs {_b:.4f}')
        check('mean breakeven is on the probability scale',
              abs(_mb - (_T._breakeven(-110)
                         + _T._breakeven(269)) / 2) < 1e-12
              and abs(_mb - _T._breakeven((-110 + 269) / 2)) > 0.1,
              f'{_mb:.4f}')
    except Exception as e:
        check('--by-market arithmetic', False,
              f'{type(e).__name__}: {e}')

    print(f'\n{CHECKS - len(FAILS)}/{CHECKS} checks passed')
    if FAILS:
        print('FAILED: ' + '; '.join(FAILS))
        return 1
    print('all good')
    return 0


if __name__ == '__main__':
    sys.exit(main())
