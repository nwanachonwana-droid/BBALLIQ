#!/usr/bin/env python3
"""
KALSHI_FIND -- are tonight's props listed on Kalshi, and at what cost?

THE QUESTION THIS ANSWERS
  A sportsbook quote of -132 against a +107 fair price is about nine
  points of vig, paid on every bet, forever, at a venue that will
  limit you if the bets win. An exchange charges a half-spread plus a
  fee instead -- and lets you post rather than cross, which is the
  only way to stop paying the spread altogether.

  Whether that is actually cheaper is an empirical question about
  tonight's order book, not a matter of opinion. So: find the same
  market on Kalshi, read the quote, and compare.

WHAT IT REPORTS
  For each market: the bid, the ask, the absolute spread and the
  spread as a share of mid. Relative spread is the one that matters --
  3c wide on a 50c contract is 6%, and the same 3c on an 8c contract
  is 37%. Absolute cents is the wrong test and was the mistake that
  made full-game totals look tradeable when they were not.

  Round-trip cost assumes crossing: half the spread in, half out, plus
  Kalshi's fee both ways. The POST column is what the same trade costs
  if you post inside the spread and get filled -- fee only. The gap
  between those two columns is the whole argument for an exchange.

usage
  python3 kalshi_find.py --raw                 # show the API's fields
  python3 kalshi_find.py                       # tonight's logged picks
  python3 kalshi_find.py --player "Tyrese Maxey"
  python3 kalshi_find.py --all                 # every open NBA prop
"""
import os, re, sys, json, time, argparse, datetime

DATA = os.path.expanduser('~/kalshi_data')
DL = os.path.expanduser('~/Downloads')
SLATE = os.path.join(DATA, 'slate.jsonl')
SERIES_FILE = os.path.join(DATA, 'series_live.json')

# Kalshi's fee, same model the grader uses: 7% of p*(1-p) per side.
FEE = lambda p: 0.07 * p * (1 - p)

# prop suffixes we care about, mapped to our market names
SUFFIX = {
    'PTS': 'points', 'REB': 'rebounds', 'AST': 'assists',
    '3PT': 'threes', 'ONE3PT': 'threes', 'PRA': 'pra', 'PR': 'pr',
}


def load_kget():
    """Borrow the signed client out of late_bot_v3.py.

    Same approach grade.py uses: exec everything above main() so the
    module-level auth runs, then lift the one function we need. Avoids
    a second copy of the request signing, which would drift.
    """
    path = os.path.join(DL, 'late_bot_v3.py')
    if not os.path.exists(path):
        raise RuntimeError(f'need the Kalshi client at {path}')
    src = open(path).read().split('def main()')[0]
    g = {'__name__': 'botlib'}
    argv, sys.argv = sys.argv, ['bot']
    try:
        exec(src, g)
    finally:
        sys.argv = argv
    if 'kget' not in g:
        raise RuntimeError('kget not found in late_bot_v3.py')
    return g['kget']


def prop_series():
    """NBA prop series from the discovery file, else a sane default."""
    out = []
    if os.path.exists(SERIES_FILE):
        try:
            blob = json.load(open(SERIES_FILE))
            names = blob if isinstance(blob, list) else list(blob)
            for t in names:
                t = t if isinstance(t, str) else t.get('ticker', '')
                if not t.startswith('KXNBA'):
                    continue
                tail = t[len('KXNBA'):]
                if tail in SUFFIX:
                    out.append((t, SUFFIX[tail]))
        except Exception as e:
            print(f'  ! could not read {SERIES_FILE}: {e}', file=sys.stderr)
    if not out:
        out = [(f'KXNBA{s}', m) for s, m in SUFFIX.items()]
    return sorted(set(out))


def today_local():
    return datetime.datetime.now().astimezone().strftime('%Y-%m-%d')


def cents(v):
    """Kalshi returns prices as dollar STRINGS: "0.2400" is 24c.

    Reading those as cents would have made every market look a
    hundred times too cheap and every spread look free.
    """
    try:
        return float(v) * 100.0
    except (TypeError, ValueError):
        return None


def tip_time(m):
    """When the GAME is, not when the market expires.

    close_time sits days after the game -- a 23 Oct game carried a 25
    Oct close -- so filtering on it keeps the whole week. The tip is
    occurrence_datetime.
    """
    for k in ('occurrence_datetime', 'expected_expiration_time',
              'close_time'):
        v = m.get(k)
        if not v:
            continue
        try:
            return datetime.datetime.fromisoformat(
                str(v).replace('Z', '+00:00')).astimezone()
        except Exception:
            continue
    return None


def plays_on(m, daystr):
    t = tip_time(m)
    return True if t is None else t.strftime('%Y%m%d') == daystr


def text_of(m):
    """Every field a player's name might be hiding in."""
    return ' '.join(str(m.get(k) or '') for k in (
        'title', 'yes_sub_title', 'no_sub_title', 'rules_primary',
        'event_ticker', 'ticker'))


def strike_of(m):
    """'Jalen Suggs: 1+' -> (name, '1+'). floor_strike is the number."""
    sub = str(m.get('yes_sub_title') or m.get('no_sub_title') or '')
    name, _, tail = sub.partition(':')
    return name.strip(), tail.strip()


def norm(n):
    return re.sub(r'[^a-z]', '', (n or '').lower())


def surname(n):
    parts = [p for p in re.sub(r"[^A-Za-z' -]", ' ', n or '').split() if p]
    return parts[-1].lower() if parts else ''


def logged_players(day):
    if not os.path.exists(SLATE):
        return []
    out = []
    for ln in open(SLATE):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') == day and r.get('market') != 'ml':
            out.append((r['subject'], r['market'], r['side'], r['line']))
    return out


def fetch_markets(kget, series):
    out, cursor = [], None
    for _ in range(10):                     # page, but do not loop forever
        p = {'series_ticker': series, 'status': 'open', 'limit': 200}
        if cursor:
            p['cursor'] = cursor
        try:
            r = kget('/markets', p)
        except Exception as e:
            raise RuntimeError(f'{series}: {e}')
        ms = (r or {}).get('markets') or []
        out.extend(ms)
        cursor = (r or {}).get('cursor')
        if not cursor or not ms:
            break
        time.sleep(0.3)
    return out


def cost_rows(m):
    """(bid, ask, mid, spread, rel, cross_cost, post_cost) in cents."""
    bid = cents(m.get('yes_bid_dollars'))
    ask = cents(m.get('yes_ask_dollars'))
    if bid is None or ask is None or ask <= 0:
        return None
    if ask <= bid:
        return None
    mid = (bid + ask) / 2.0
    spread = ask - bid
    rel = spread / mid if mid else None
    p = mid / 100.0
    fee = FEE(p) * 100.0                     # cents per side
    cross = spread / 2.0 * 2 + fee * 2       # half-spread each way + fees
    post = fee * 2                           # filled at your own price
    return bid, ask, mid, spread, rel, cross, post


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--day', default=datetime.datetime.now()
                    .astimezone().strftime('%Y%m%d'))
    ap.add_argument('--player', action='append',
                    help='look one player up instead of the logged slate')
    ap.add_argument('--all', action='store_true',
                    help='every open NBA prop market, no player filter')
    ap.add_argument('--raw', action='store_true',
                    help='dump one market so we can see the real fields')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    try:
        kget = load_kget()
    except Exception as e:
        print(f'{e}', file=sys.stderr)
        return 1

    series = prop_series()
    print(f'checking {len(series)} prop series: '
          f'{", ".join(s for s, _ in series)}\n')

    if a.raw:
        for s, _mk in series:
            try:
                ms = fetch_markets(kget, s)
            except Exception as e:
                print(f'{s}: {e}')
                continue
            print(f'{s}: {len(ms)} open markets')
            if ms:
                print('  fields: ' + ', '.join(sorted(ms[0])))
                print('  sample: ' + json.dumps(ms[0], indent=2)[:900])
                return 0
            time.sleep(0.3)
        print('no open markets in any prop series')
        return 0

    if a.player:
        wanted = [(p, None, None, None) for p in a.player]
    elif a.all:
        wanted = []
    else:
        wanted = logged_players(a.day)
        if not wanted:
            print(f'nothing logged for {a.day}; use --player or --all')
            return 1
        print('looking for: ' + ', '.join(w[0] for w in wanted) + '\n')

    rows = []
    for s, mk in series:
        try:
            ms = fetch_markets(kget, s)
        except Exception as e:
            print(f'  ! {e}', file=sys.stderr)
            continue
        ms = [m for m in ms if plays_on(m, a.day)]
        for m in ms:
            blob = text_of(m)
            pname, strike = strike_of(m)
            who = None
            if wanted:
                for name, _mk, _side, _line in wanted:
                    if surname(name) and surname(name) in blob.lower():
                        who = name
                        break
                if not who:
                    continue
            c = cost_rows(m)
            rows.append({
                'series': s, 'market': mk, 'player': who,
                'ticker': m.get('ticker'),
                'title': (m.get('yes_sub_title') or m.get('title') or '')[:46],
                'strike': strike, 'kname': pname,
                'event': m.get('event_ticker'),
                'bid': c[0] if c else None, 'ask': c[1] if c else None,
                'spread': c[3] if c else None, 'rel': c[4] if c else None,
                'cross': c[5] if c else None, 'post': c[6] if c else None,
                'vol': cents(m.get('volume_fp')) and
                       float(m.get('volume_fp') or 0),
                'oi': float(m.get('open_interest_fp') or 0),
            })
        time.sleep(0.3)

    if a.json:
        print(json.dumps(rows, indent=2))
        return 0

    if not rows:
        print('No matching open Kalshi markets closing today.')
        print('\nThat is itself the answer for tonight: if Kalshi has not')
        print('listed these props, the sportsbook is the only venue and')
        print('the vig is the price of doing business. Worth re-checking')
        print('on a fuller slate before concluding anything general.')
        return 0

    hdr = (f"{'PLAYER':20}{'MKT':8}{'STRIKE':8}{'EVENT':22}{'BID':>5}"
           f"{'ASK':>5}{'SPR':>5}{'REL':>7}{'CROSS':>7}{'POST':>6}{'VOL':>8}")
    print(hdr)
    print('-' * len(hdr))
    rows.sort(key=lambda r: (r['rel'] is None, r['rel'] or 0))
    for r in rows:
        f = lambda v, d=1: '-' if v is None else f'{v:.{d}f}'
        rel = '-' if r['rel'] is None else f"{r['rel']*100:.1f}%"
        who = r['player'] or r.get('kname') or ''
        ev = (r.get('event') or '').replace('KXNBA', '')
        print(f"{who[:19]:20}{r['market']:8}{(r.get('strike') or '')[:7]:8}"
              f"{ev[:21]:22}{f(r['bid'],0):>5}"
              f"{f(r['ask'],0):>5}{f(r['spread'],0):>5}{rel:>7}"
              f"{f(r['cross']):>7}{f(r['post']):>6}{(r['vol'] or 0):>8.0f}")

    print('\nREL    spread as a share of mid -- the test that matters.')
    print('CROSS  round-trip cents if you take both sides of the spread.')
    print('POST   round-trip cents if you post and get filled: fee only.')
    print('\nCompare CROSS against the sportsbook vig on the same bet.')
    print('A -132 quote where fair is +107 costs about 9 points. If CROSS')
    print('on a 50c contract is under about 4.5c, the exchange is cheaper')
    print('even before you try posting -- and POST is the real prize.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
