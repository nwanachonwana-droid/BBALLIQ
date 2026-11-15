#!/usr/bin/env python3
"""
SITE_DATA — turn the daily logs into the few numbers the site shows.

THE PROBLEM THIS SOLVES
  Before there is a track record there is nothing honest to publish
  about results, and publishing a six-pick record is worse than
  publishing none: it is noise presented as evidence. But that does not
  mean there is nothing to show. Every day the system looks at a slate
  and makes decisions, and those decisions are falsifiable, dated, and
  far more informative than an early win rate.

  So the site shows the FUNNEL -- games seen, markets priced, how many
  cleared the gates, how many were taken, and which gate did the
  rejecting -- plus how mature the ratings are. A reader can check it
  against the next day's slate. Almost nobody publishes this, because
  it makes declining visible, which is exactly why it is worth showing.

WHAT IT READS
  ~/kalshi_data/runs.jsonl    every run, pick or no pick   (run.py)
  ~/kalshi_data/picks.jsonl   every pick with entry price  (run.py)
  ~/kalshi_data/euro/euro_sigma.json  fitted sigmas        (euro_fit.py)
  ~/kalshi_data/euro/team_adv_E*.json rating maturity      (euro_data.py)

WHAT IT WILL NOT DO
  Report a win rate or CLV below the sample where the number means
  something. CLV converges in roughly 30 observations and win rate
  needs on the order of 750. Below those, this prints the count and
  says the panel stays shut -- with the number needed, so the promise
  has a date attached rather than being a hedge.

usage
  python3 site_data.py
  python3 site_data.py --days 14
"""
import os, sys, json, glob, time, argparse, collections, statistics

DATA = os.path.expanduser('~/kalshi_data')
CLV_MIN = 30          # CLV is near-deterministic; converges fast
WIN_MIN = 200         # a win rate below this is not evidence either way

GATE_LABEL = {
    'relspread': 'bid-ask too wide for the price',
    'spread':    'bid-ask too wide in cents',
    'ev':        'edge too small to clear costs',
    'disagree':  'our two sources disagreed',
    'tail':      'price too close to 0 or 1',
    'noise':     'edge inside the noise',
}


def load_jsonl(path, days=None):
    if not os.path.exists(path):
        return []
    cutoff = time.time() - days * 86400 if days else 0
    out = []
    for ln in open(path):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if cutoff and (r.get('ts') or 0) < cutoff:
            continue
        out.append(r)
    return out


def funnel(runs):
    """The headline strip: what happened on the most recent run."""
    if not runs:
        return None
    last = max(runs, key=lambda r: r.get('ts') or 0)
    gates = last.get('gates') or {}
    top = sorted(gates.items(), key=lambda kv: -kv[1])
    return {
        'when': time.strftime('%d %b %H:%M',
                              time.localtime(last.get('ts') or 0)),
        'games': last.get('games', 0),
        'priced': last.get('rungs', 0),
        'cleared': last.get('eligible', 0),
        'taken': last.get('picks', 0),
        'top_gate': ({'gate': top[0][0],
                      'label': GATE_LABEL.get(top[0][0], top[0][0]),
                      'killed': top[0][1]} if top else None),
        'gates': [{'gate': g, 'label': GATE_LABEL.get(g, g), 'killed': n}
                  for g, n in top],
    }


def history(runs):
    """One row per day, for the pricing log page."""
    by_day = collections.defaultdict(list)
    for r in runs:
        by_day[r.get('day') or '?'].append(r)
    rows = []
    for day in sorted(by_day, reverse=True):
        rs = by_day[day]
        gates = collections.Counter()
        for r in rs:
            for g, n in (r.get('gates') or {}).items():
                gates[g] += n
        rows.append({
            'day': day,
            'runs': len(rs),
            'games': max(r.get('games', 0) for r in rs),
            'priced': sum(r.get('rungs', 0) for r in rs),
            'cleared': sum(r.get('eligible', 0) for r in rs),
            'taken': sum(r.get('picks', 0) for r in rs),
            'top_gate': (max(gates.items(), key=lambda kv: kv[1])[0]
                         if gates else None),
        })
    return rows


def maturity():
    """How much of each league's rating is THIS season's games.

    This is the honest progress bar on a season. Early on the ratings
    are mostly last year's team, which is why projections look flat and
    why that is not a defect. The number climbs on its own.
    """
    out = []
    for path in sorted(glob.glob(os.path.join(DATA, 'euro',
                                              'team_adv_*.json'))):
        try:
            d = json.load(open(path))
        except Exception:
            continue
        if not d:
            continue
        ws = [t.get('shrink_w') for t in d.values()
              if t.get('shrink_w') is not None]
        gps = sorted(t.get('gp', 0) for t in d.values())
        if not ws:
            continue
        season = os.path.basename(path).replace('team_adv_', '').replace('.json', '')
        out.append({
            'league': season,
            'teams': len(d),
            'median_gp': gps[len(gps) // 2],
            'own_form_pct': round(100 * statistics.mean(ws)),
            'prior_pct': round(100 * (1 - statistics.mean(ws))),
        })
    return out


def record(picks):
    """Results, but only once they mean something."""
    graded = [p for p in picks if p.get('graded')]
    n = len(graded)
    out = {'picks_logged': len(picks), 'picks_graded': n,
           'clv_unlocks_at': CLV_MIN, 'win_rate_unlocks_at': WIN_MIN}
    if n >= CLV_MIN:
        clv = [p['clv'] for p in graded if p.get('clv') is not None]
        if clv:
            out['clv_cents'] = round(100 * statistics.mean(clv), 2)
            out['clv_n'] = len(clv)
    else:
        out['clv_note'] = (f'{n} of {CLV_MIN} graded. Closing-line value '
                           f'settles in about {CLV_MIN} picks, so it stays '
                           f'shut until then.')
    if n >= WIN_MIN:
        w = [1 for p in graded if p.get('won')]
        out['win_rate'] = round(100 * len(w) / n, 1)
    else:
        out['win_note'] = (f'A win rate needs roughly {WIN_MIN} settled '
                           f'picks before it separates skill from luck. '
                           f'Showing one sooner would be noise.')
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=0,
                    help='only runs from the last N days (0 = all)')
    ap.add_argument('--json', action='store_true',
                    help='bare JSON on stdout, nothing else — for the '
                         'deploy script to redirect into data/status.json')
    a = ap.parse_args()

    runs = load_jsonl(os.path.join(DATA, 'runs.jsonl'), a.days or None)
    picks = load_jsonl(os.path.join(DATA, 'picks.jsonl'), a.days or None)

    out = {
        'generated': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        'latest': funnel(runs),
        'history': history(runs)[:30],
        'maturity': maturity(),
        'record': record(picks),
    }

    if a.json:
        # Bare JSON only. The deploy script redirects this into
        # data/status.json, so a stray banner would corrupt the file.
        print(json.dumps(out, indent=1))
        return

    print('=' * 68)
    print('COPY EVERYTHING BELOW THIS LINE')
    print('=' * 68)
    print(json.dumps(out, indent=1))
    print('=' * 68)
    print('COPY EVERYTHING ABOVE THIS LINE')
    print('=' * 68)

    if not runs:
        print('\nNo runs logged yet. run.py writes runs.jsonl on every run,')
        print('pick or no pick, so this fills in from the first slate.')
    else:
        f = out['latest']
        print(f"\nlatest: {f['games']} games, {f['priced']} priced, "
              f"{f['cleared']} cleared, {f['taken']} taken")
        if f['top_gate']:
            print(f"  most rejections: {f['top_gate']['gate']} "
                  f"({f['top_gate']['killed']}) — {f['top_gate']['label']}")


if __name__ == '__main__':
    main()
