#!/usr/bin/env python3
"""
PROPS_LINES -- tonight's player prop LINES from the books, via The Odds API.

WHY THIS EXISTS
  The PRA board holds a three-season figure for each player: roughly
  where a book would set him, and a measure of how forecastable his
  minutes are. It has never had anything to be compared against.

  Covers was the first attempt and it does not work. Their model
  publishes only the props it chooses to flag -- on 20 Oct it had one
  prop for Gilgeous-Alexander and nothing at all for his rebounds or
  assists -- so reconstructing PRA from three projected legs leaves
  almost every row blank.

  The books quote the combined line directly. The Odds API carries it
  as `player_points_rebounds_assists`, which is the number our board
  should actually be measured against: our figure is what he typically
  does, theirs is what he is expected to do tonight. The gap between
  them is the only interesting quantity on that row.

WHAT A LINE IS AND IS NOT
  This is not a projection and must never be presented as one. It is a
  market price, which means it already contains everything the market
  knows -- injuries, rest, matchup -- and beating it is the whole
  difficulty of the business. Our three-season number disagreeing with
  it is not evidence we are right. It is a question worth logging.

COST
  The events endpoint is free. Odds are billed per event at
  (markets x regions), so one market in one region across a ten-game
  slate costs ten credits. --dry prints the bill without paying it.

usage
  export ODDS_API_KEY=...
  python3 props_lines.py --dry
  python3 props_lines.py
  python3 props_lines.py --markets pra,pts,reb,ast
"""
import os, sys, json, argparse, datetime, statistics, collections
import urllib.request, urllib.error

API = 'https://api.the-odds-api.com/v4'
SPORT = 'basketball_nba'
DATA = os.path.expanduser('~/kalshi_data')
PICKS = os.path.join(DATA, 'external_picks.jsonl')

# Our short name -> the API's market key, and the label we log under.
MARKETS = {
    'pra': ('player_points_rebounds_assists', 'PRA'),
    'pts': ('player_points', 'Points Scored'),
    'reb': ('player_rebounds', 'Total Rebounds'),
    'ast': ('player_assists', 'Total Assists'),
    'threes': ('player_threes', '3-Pointers Made'),
    'pr': ('player_points_rebounds', 'Points + Rebounds'),
    'pa': ('player_points_assists', 'Points + Assists'),
    'ra': ('player_rebounds_assists', 'Rebounds + Assists'),
}

# our pick markets -> the key above, so --for-picks can ask for only
# what was actually bet
PICK_MARKET = {'points': 'pts', 'rebounds': 'reb', 'assists': 'ast',
               'threes': 'threes', 'pra': 'pra', 'pr': 'pr', 'pa': 'pa',
               'ra': 'ra'}
SLATE = os.path.join(DATA, 'slate.jsonl')


def markets_for_picks(day):
    """Only the markets today's logged picks need.

    A closing capture exists to price what was bet, not to re-read the
    whole board. Eight markets across a ten-game slate is 80 credits a
    run; the three or four a card actually uses is a quarter of that,
    and the budget has to survive a full season.
    """
    if not os.path.exists(SLATE):
        return []
    want = set()
    for ln in open(SLATE):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r.get('day') != day:
            continue
        k = PICK_MARKET.get(r.get('market'))
        if k:
            want.add(k)
    return sorted(want)

# The API uses full team names; our board and Covers use codes.
TEAM_CODE = {
    'Atlanta Hawks': 'ATL', 'Boston Celtics': 'BOS', 'Brooklyn Nets': 'BKN',
    'Charlotte Hornets': 'CHA', 'Chicago Bulls': 'CHI',
    'Cleveland Cavaliers': 'CLE', 'Dallas Mavericks': 'DAL',
    'Denver Nuggets': 'DEN', 'Detroit Pistons': 'DET',
    'Golden State Warriors': 'GS', 'Houston Rockets': 'HOU',
    'Indiana Pacers': 'IND', 'Los Angeles Clippers': 'LAC',
    'Los Angeles Lakers': 'LAL', 'Memphis Grizzlies': 'MEM',
    'Miami Heat': 'MIA', 'Milwaukee Bucks': 'MIL',
    'Minnesota Timberwolves': 'MIN', 'New Orleans Pelicans': 'NO',
    'New York Knicks': 'NY', 'Oklahoma City Thunder': 'OKC',
    'Orlando Magic': 'ORL', 'Philadelphia 76ers': 'PHI',
    'Phoenix Suns': 'PHO', 'Portland Trail Blazers': 'POR',
    'Sacramento Kings': 'SAC', 'San Antonio Spurs': 'SA',
    'Toronto Raptors': 'TOR', 'Utah Jazz': 'UTA',
    'Washington Wizards': 'WSH',
}


def code(team):
    return TEAM_CODE.get(team, (team or '')[:3].upper())


class ApiError(Exception):
    pass


def get(url):
    """One GET. Returns (payload, headers).

    The quota headers come back on every response including errors, and
    they are the only way to know what a run cost, so they are returned
    rather than printed here.
    """
    req = urllib.request.Request(url, headers={'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()), dict(r.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        hdrs = dict(e.headers)
        if e.code == 401:
            raise ApiError('401 - the API key was rejected. Check '
                           'ODDS_API_KEY.') from None
        if e.code == 429:
            raise ApiError('429 - out of credits or rate limited. '
                           f"remaining={hdrs.get('x-requests-remaining')}") from None
        if e.code == 422:
            raise ApiError(f'422 - the API refused the request: {body}') from None
        raise ApiError(f'HTTP {e.code}: {body}') from None
    except urllib.error.URLError as e:
        raise ApiError(f'could not reach the API: {e.reason}') from None


def todays_events(key, day):
    """Events tipping off on `day`, in local time. Free endpoint."""
    payload, hdrs = get(f'{API}/sports/{SPORT}/events?apiKey={key}')
    out = []
    for e in payload:
        ct = e.get('commence_time')
        if not ct:
            continue
        # stored as UTC Z; compare in the machine's own zone, because
        # a 7pm Eastern tip is "tonight" and 23:00 UTC is not.
        when = (datetime.datetime.fromisoformat(ct.replace('Z', '+00:00'))
                .astimezone())
        if when.strftime('%Y%m%d') != day:
            continue
        out.append({'id': e['id'], 'away': e.get('away_team'),
                    'home': e.get('home_team'), 'commence': when})
    out.sort(key=lambda x: x['commence'])
    return out, hdrs


def american_to_prob(price):
    if price is None:
        return None
    p = float(price)
    return 100.0 / (p + 100.0) if p > 0 else (-p) / ((-p) + 100.0)


def consensus(outcomes):
    """Collapse every book's quote on one player into one row.

    The median line, not the mean: one book hanging a stale number
    should not drag the consensus, and with four or five books a single
    outlier moves a mean by a quarter point.

    The vig is removed per book and the no-vig probabilities averaged,
    so `prob_over` is comparable with a Kalshi price rather than being
    a raw book price that is systematically too high.
    """
    by_player = collections.defaultdict(lambda: {'over': [], 'under': []})
    for book, name, side, point, price in outcomes:
        s = side.lower()
        if s not in ('over', 'under'):
            continue
        by_player[name][s].append((book, point, price))

    rows = []
    for name, sides in by_player.items():
        lines = [p for _b, p, _pr in sides['over'] + sides['under']
                 if p is not None]
        if not lines:
            continue
        line = statistics.median(lines)

        # pair the two sides within each book to strip the vig
        over = {b: (p, pr) for b, p, pr in sides['over']}
        under = {b: (p, pr) for b, p, pr in sides['under']}
        probs = []
        for b in set(over) & set(under):
            if over[b][0] != under[b][0]:
                continue  # different lines, not a pair
            po = american_to_prob(over[b][1])
            pu = american_to_prob(under[b][1])
            if po and pu and (po + pu) > 0:
                probs.append(po / (po + pu))

        rows.append({
            'player': name,
            'line': round(float(line), 1),
            'n_books': len({b for b, _p, _pr in sides['over'] + sides['under']}),
            'prob_over': round(statistics.mean(probs), 4) if probs else None,
            'n_paired': len(probs),
            'line_spread': round(max(lines) - min(lines), 1),
        })
    rows.sort(key=lambda r: -r['line'])
    return rows


def fetch_market(key, ev, market_key, regions):
    url = (f'{API}/sports/{SPORT}/events/{ev["id"]}/odds'
           f'?apiKey={key}&regions={regions}&markets={market_key}'
           f'&oddsFormat=american')
    payload, hdrs = get(url)
    outcomes = []
    for bk in payload.get('bookmakers', []):
        for mk in bk.get('markets', []):
            if mk.get('key') != market_key:
                continue
            for o in mk.get('outcomes', []):
                # player name lives in `description`; `name` is the side
                outcomes.append((bk.get('key'), o.get('description'),
                                 o.get('name'), o.get('point'),
                                 o.get('price')))
    return outcomes, hdrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--markets', default='pra',
                    help='comma list of ' + ','.join(MARKETS))
    ap.add_argument('--for-picks', action='store_true',
                    help="only the markets today's logged picks use")
    ap.add_argument('--regions', default='us')
    ap.add_argument('--day', help='YYYYMMDD, default today (local)')
    ap.add_argument('--max-events', type=int, default=0,
                    help='cap events, 0 = no cap')
    ap.add_argument('--dry', action='store_true',
                    help='price the run without spending credits')
    ap.add_argument('--stdout', action='store_true',
                    help='print rows instead of appending to the log')
    a = ap.parse_args()

    key = os.environ.get('ODDS_API_KEY', '').strip()
    if not key:
        print('ODDS_API_KEY is not set in the environment.', file=sys.stderr)
        print('  export ODDS_API_KEY=...   then run again', file=sys.stderr)
        return 2

    day_guess = a.day or datetime.datetime.now().astimezone().strftime('%Y%m%d')
    if a.for_picks:
        want = markets_for_picks(day_guess)
        if not want:
            print(f'no picks logged for {day_guess}; nothing to re-price')
            return 0
        print(f'markets from the card: {", ".join(want)}')
    else:
        want = [m.strip() for m in a.markets.split(',') if m.strip()]
    bad = [m for m in want if m not in MARKETS]
    if bad:
        print(f'unknown market(s): {", ".join(bad)}', file=sys.stderr)
        print(f'known: {", ".join(MARKETS)}', file=sys.stderr)
        return 2

    day = a.day or datetime.datetime.now().astimezone().strftime('%Y%m%d')

    try:
        evs, hdrs = todays_events(key, day)
    except ApiError as e:
        print(f'events lookup failed: {e}', file=sys.stderr)
        return 1

    if a.max_events:
        evs = evs[:a.max_events]

    n_regions = len([r for r in a.regions.split(',') if r])
    cost = len(evs) * len(want) * n_regions
    remaining = hdrs.get('x-requests-remaining')

    print(f'{day}: {len(evs)} games, {len(want)} market(s), '
          f'{n_regions} region(s)  ->  {cost} credits'
          + (f'  (remaining {remaining})' if remaining else ''))
    for e in evs:
        print(f"  {e['commence'].strftime('%H:%M')}  "
              f"{code(e['away'])}@{code(e['home'])}")
    if a.dry:
        print('\n--dry: nothing fetched, nothing charged.')
        return 0
    if not evs:
        return 0

    os.makedirs(DATA, exist_ok=True)
    written, last_hdrs = [], hdrs
    for e in evs:
        for m in want:
            market_key, label = MARKETS[m]
            try:
                outcomes, last_hdrs = fetch_market(key, e, market_key,
                                                   a.regions)
            except ApiError as err:
                print(f"  ! {code(e['away'])}@{code(e['home'])} {m}: {err}",
                      file=sys.stderr)
                continue
            rows = consensus(outcomes)
            if not rows:
                print(f"  - {code(e['away'])}@{code(e['home'])} {m}: "
                      f'no book quoted this market')
                continue
            for r in rows:
                written.append({
                    'kind': 'book_prop', 'day': day,
                    'source': 'the-odds-api',
                    # when this reading was taken. CLV needs to tell an
                    # opening capture from a closing one, and two runs
                    # an hour apart are not an open and a close.
                    'captured': datetime.datetime.now()
                                .astimezone().isoformat(),
                    'player': r['player'], 'market': label,
                    'line': r['line'], 'prob_over': r['prob_over'],
                    'n_books': r['n_books'], 'n_paired': r['n_paired'],
                    'line_spread': r['line_spread'],
                    'away': code(e['away']), 'home': code(e['home']),
                    'commence': e['commence'].isoformat(),
                })
            print(f"  + {code(e['away'])}@{code(e['home'])} {m}: "
                  f"{len(rows)} players, {rows[0]['n_books']} books")

    if a.stdout:
        print()
        for r in written:
            print(json.dumps(r))
    else:
        with open(PICKS, 'a') as f:
            for r in written:
                f.write(json.dumps(r) + '\n')
        print(f'\nappended {len(written)} rows to {PICKS}')

    rem = last_hdrs.get('x-requests-remaining')
    used = last_hdrs.get('x-requests-used')
    if rem:
        print(f'credits: {used} used, {rem} remaining')
    return 0


if __name__ == '__main__':
    sys.exit(main())
