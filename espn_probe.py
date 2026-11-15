#!/usr/bin/env python3
"""ESPN PROBE -- find out which injury endpoints actually exist.

WHY A PROBE AND NOT A GUESS
  ESPN's JSON API has no official documentation, so the endpoint list
  is folklore. Every time this project has guessed at a structure it
  has cost a morning: 'AVG_SEC_PER_TOUCH' passed a stale file,
  'd. mitchell' settled the wrong player, and defense.py's first
  column map resolved opponent points off a standings table. So this
  asks the server and reports what came back, exactly like
  defense.py --discover, and decides nothing.

  It is also not reachable from where this was written -- ESPN is
  off the allowlist there and the container's proxy refuses it -- so
  it has to run on the machine that already pulls box scores.

WHAT IT IS LOOKING FOR
  An injury feed would be the most valuable input left. Three days
  running, the deciding fact has been one the screen could not see:
  Duren out and Thompson a game-time call, Alvarado out freeing
  McBride's minutes, Mark Williams' shoulder making Maluach a
  starter. The role column catches those a few games late; an injury
  report catches them the morning they happen.

  The cheapest possible answer is candidate 5. edges.py already
  fetches the game summary for every completed game, and if that
  response carries an injuries block then the feed is free and
  already in the cache.

usage
  python3 espn_probe.py            # probe, print, change nothing
  python3 espn_probe.py --dump DIR # also save each response for reading
"""
import os, re, sys, json, argparse, subprocess, datetime

UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
      'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
SITE = 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
CORE = 'https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba'
WEB = 'https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba'


def get(url, timeout=25):
    """(status-ish, bytes, parsed-or-None, error). Never raises."""
    p = subprocess.run(
        ['curl', '-sS', '--compressed', '-m', str(timeout), '-A', UA,
         '-w', '\n%{http_code}', url],
        capture_output=True, text=True)
    if p.returncode != 0:
        return None, 0, None, (p.stderr or '').strip()[:120]
    body, _, code = p.stdout.rpartition('\n')
    try:
        return code.strip(), len(body), json.loads(body), None
    except Exception as e:
        return code.strip(), len(body), None, f'not json: {e}'[:120]


def looks_like_injuries(obj):
    """Walk the response for anything injury-shaped, and say where.

    Reports the PATH as well as the hit, because 'there is injury
    data somewhere in here' is not actionable -- the next script has
    to know which key to read.
    """
    hits = []

    def walk(node, path, depth=0):
        if depth > 6 or len(hits) > 40:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in ('injuries', 'injury') and v:
                    hits.append((path + '.' + k, v))
                walk(v, path + '.' + k, depth + 1)
        elif isinstance(node, list):
            for i, v in enumerate(node[:4]):
                walk(v, f'{path}[{i}]', depth + 1)

    walk(obj, '')
    return hits


def describe(sample, indent='      '):
    """One injury entry, flattened to the fields that matter."""
    if isinstance(sample, list):
        sample = sample[0] if sample else None
    if not isinstance(sample, dict):
        print(f'{indent}{json.dumps(sample)[:200]}')
        return
    keys = sorted(sample.keys())
    print(f'{indent}keys: {", ".join(keys)}')
    for k in ('status', 'type', 'date', 'longComment', 'shortComment',
              'details', 'athlete'):
        if k not in sample:
            continue
        v = sample[k]
        if isinstance(v, dict):
            inner = {kk: v[kk] for kk in list(v)[:6]}
            print(f'{indent}{k}: {json.dumps(inner)[:220]}')
        else:
            print(f'{indent}{k}: {json.dumps(v)[:220]}')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dump', help='directory to save raw responses into')
    a = ap.parse_args()
    if a.dump:
        os.makedirs(a.dump, exist_ok=True)

    today = datetime.datetime.now().astimezone().strftime('%Y%m%d')

    # One event id from today's scoreboard, so the summary probe has
    # something real to ask about. If there are no games, fall back to
    # a recent day rather than probing a URL with an empty id.
    event = None
    for d in (today, '20261026', '20261025'):
        code, n, js, err = get(f'{SITE}/scoreboard?dates={d}&limit=100')
        evs = (js or {}).get('events') or []
        if evs:
            event = evs[0].get('id')
            print(f'using event {event} from {d} '
                  f'({evs[0].get("shortName", "?")})\n')
            break

    cands = [
        ('1  site/injuries', f'{SITE}/injuries'),
        ('2  core/injuries', f'{CORE}/injuries'),
        ('3  web/injuries', f'{WEB}/injuries'),
        ('4  team 18 (NYK)', f'{SITE}/teams/18'),
        ('5  game summary', f'{SITE}/summary?event={event}' if event else None),
        ('6  scoreboard', f'{SITE}/scoreboard?dates={today}&limit=100'),
    ]

    found = []
    for label, url in cands:
        if not url:
            print(f'{label}: skipped, no event id')
            continue
        code, n, js, err = get(url)
        head = f'{label}: HTTP {code}  {n} bytes'
        if err:
            print(f'{head}  -- {err}')
            continue
        print(head)
        if a.dump and js is not None:
            fn = os.path.join(a.dump, re.sub(r'\W+', '_', label) + '.json')
            json.dump(js, open(fn, 'w'), indent=1)
        if js is None:
            continue
        if isinstance(js, dict):
            print(f'      top-level keys: {", ".join(sorted(js)[:14])}')
        hits = looks_like_injuries(js)
        if not hits:
            print('      no injury-shaped key found')
            continue
        found.append(label)
        for path, val in hits[:3]:
            cnt = len(val) if isinstance(val, list) else 1
            print(f'      >> {path}  ({cnt} entr{"y" if cnt == 1 else "ies"})')
            describe(val)
        print()

    print('\n' + '=' * 62)
    if found:
        print('injury data present in: ' + ', '.join(found))
        print('\nPaste this output back. The path and field names above are\n'
              'what a fetcher would read -- no guessing from here.')
    else:
        print('no injury data in any candidate.')
        print('\nThat is a real answer, not a failure: it means the feed has\n'
              'to come from somewhere else, and we stop spending time on\n'
              'ESPN for it.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
