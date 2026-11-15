#!/usr/bin/env python3
"""
SCREEN — apply our rules to what Covers published.

WHAT THIS IS, AND IS NOT
  It is not a list of Sharpz picks. Nothing here comes from our model;
  every number is Covers'. What it does is apply OUR discipline to THEIR
  output, so the result is a short list of things worth a second look
  rather than a reprint of somebody's card.

  Covers publishes two independent surfaces for the same games: named
  writers picking sides, and a projection table giving a modelled value
  for each player stat. They are not the same thing and they do not
  always agree. Where they agree, two reads concur. Where they
  contradict, one of them is wrong and that is more interesting than
  either on its own.

THE RULES, AND WHY
  CORROBORATED  a writer took a side and the projection table agrees.
                Two surfaces, one conclusion.
  CONFLICT      a writer took a side the projection contradicts. Flagged
                loudly: a house divided against itself is a signal, and
                it is the case most likely to reveal whether writer or
                model is worth following.
  UNBACKED      a writer pick with no projection to check it against.
                Kept but marked -- absence of a check is not a pass.
  CONTRADICTION the same player on both sides of the same stat. Dropped.
                Two writers disagreeing cancels out.

  Then the thresholds we already use elsewhere: an edge has to be big
  enough to clear costs, and a projection sitting a tenth of a point off
  the line is noise whatever its stated EV.

WHAT IS STILL MISSING
  Our own model. The whole point of the exercise is to compare their
  numbers with ours, and that needs run.py to have priced the same
  games. Until then this screens Covers against itself, which is worth
  doing but is not the finished article.

usage
  python3 screen.py
  python3 screen.py --day 20261020 --min-ev 10 --min-diff 0.5
"""
import os, re, json, argparse, collections, subprocess, datetime

DATA = os.path.expanduser('~/kalshi_data')
PICKS = os.path.join(DATA, 'external_picks.jsonl')


# The PRA board, as published on the site. These are the players whose
# points+rebounds+assists number is stable enough to be worth quoting --
# a priceability ranking, not a recommendation. Keeping it here lets the
# nightly screen say which of them actually play tonight, and what an
# outside model says about them.
PRA_BOARD = [
    ('Nikola Jokic', 'DEN', 50.0, 'locked'),
    ('Shai Gilgeous-Alexander', 'OKC', 42.5, 'locked'),
    ('Cade Cunningham', 'DET', 38.0, 'moved'),
    ('Jalen Brunson', 'NYK', 37.0, 'locked'),
    ('Kevin Durant', 'HOU', 36.5, 'moved'),
    ('Donovan Mitchell', 'CLE', 35.5, 'locked'),
    ('Alperen Sengun', 'HOU', 35.0, 'moved'),
    ('Lauri Markkanen', 'UTA', 32.0, 'moved'),
    ('RJ Barrett', 'TOR', 30.0, 'moved'),
    ('Amen Thompson', 'HOU', 26.0, 'locked'),
]

# Our board uses the common three-letter codes; Covers uses its own.
# Only the ones that actually differ are listed.
TEAM_ALIAS = {'NYK': 'NY', 'SAS': 'SA', 'PHX': 'PHO', 'GSW': 'GS',
              'NOP': 'NO', 'SAC': 'SAC', 'WAS': 'WSH'}


# Everyone spells the same team differently. Canonicalise before
# comparing, or a correct slate filter silently drops the Knicks.
TEAM_CANON = {
    'NYK': 'NY', 'NY': 'NY', 'SAS': 'SA', 'SA': 'SA', 'PHX': 'PHO',
    'PHO': 'PHO', 'GSW': 'GS', 'GS': 'GS', 'NOP': 'NO', 'NO': 'NO',
    'WAS': 'WSH', 'WSH': 'WSH', 'UTAH': 'UTA', 'UTA': 'UTA',
    'BRK': 'BKN', 'BKN': 'BKN', 'CHO': 'CHA', 'CHA': 'CHA',
}


def canon(code):
    c = (code or '').strip().upper()
    return TEAM_CANON.get(c, c)


def espn_slate(day):
    """The teams actually playing on `day`, from the schedule itself.

    WHY NOT INFER IT FROM THE SCRAPE
      covers2.py stamps each row with the date it scraped, not the date
      the game is played, and Covers' page carries last night's
      finished games alongside tonight's. Inferring the slate from
      those rows put Gilgeous-Alexander, Cunningham and Brunson on a
      board for a night none of them were playing -- the data was from
      today, the games were not.

      The schedule is the only thing that knows which games are on. If
      it cannot be reached we return None and the caller falls back to
      the old inference, because a board built on a guess is still
      better than no board -- but it is a fallback, not the plan.
    """
    url = ('https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
           f'/scoreboard?dates={day}')
    try:
        r = subprocess.run(['curl', '-s', '--compressed', '-m', '20', url],
                           capture_output=True, timeout=30)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        board = json.loads(r.stdout.decode())
    except Exception:
        return None
    teams = set()
    for e in board.get('events') or []:
        for c in ((e.get('competitions') or [{}])[0]
                  .get('competitors') or []):
            ab = (c.get('team') or {}).get('abbreviation')
            if ab:
                teams.add(canon(ab))
    return teams or None


def tonight_teams(rows, day=None):
    """Teams playing tonight: the schedule if we can reach it."""
    if day:
        sched = espn_slate(day)
        if sched:
            return sched
    t = set()
    for r in rows:
        for k in ('away', 'home'):
            v = r.get(k)
            if v and len(v) <= 4 and v.isupper():
                t.add(canon(v))
    return t


# PRA is points + rebounds + assists, and Covers never quotes the
# combined line -- only the three pieces, on separate cards. So the
# only honest comparison is to collect all three and add them up, which
# also makes the gaps visible: a player with two of three projected
# cannot be compared to our line at all, and saying so is the point.
COMPONENT = {'pointsscored': 'pts', 'points': 'pts',
             'totalrebounds': 'reb', 'rebounds': 'reb',
             'totalassists': 'ast', 'assists': 'ast'}
COMPONENT_ORDER = ('pts', 'reb', 'ast')


def pra_watch(rows, day=None):
    """Our board, narrowed to players whose team plays tonight.

    The join is one-way on purpose: we are not claiming Covers agrees
    with our PRA number. We hold a PRA line; they publish component
    projections. Where all three components exist they sum to something
    directly comparable, and where they don't the row says which piece
    is missing rather than quietly showing one stat as if it stood for
    the whole.

    Covers renders every prop twice -- once on the card, once in the
    modal behind it -- so the raw rows carry each projection two or
    three times. Deduping here is not tidiness: reading the raw list
    printed every player's read twice on the page.
    """
    teams = tonight_teams(rows, day)
    writers = [r for r in rows if r.get('kind') == 'prop']

    # one projection per player+market, last write wins
    projs = {}
    for r in rows:
        if r.get('kind') != 'prop_projection':
            continue
        projs[(short(r.get('player')), norm_market(r.get('market')))] = r

    # The books quote PRA as a single number. Where that exists it is
    # the comparison -- a market price beats three projected legs added
    # together, and unlike the legs it is quoted for everyone who has a
    # prop at all. The summed components stay as the fallback.
    books = {}
    book_legs = {}
    for r in rows:
        if r.get('kind') != 'book_prop':
            continue
        mk = norm_market(r.get('market'))
        if mk == 'pra':
            books[short(r.get('player'))] = r
        elif COMPONENT.get(mk):
            book_legs[(short(r.get('player')), COMPONENT[mk])] = r

    out = []
    for name, team, line, status in PRA_BOARD:
        code = canon(TEAM_ALIAS.get(team, team))
        if code not in teams and canon(team) not in teams:
            continue
        key = short(name)

        # Each leg can come from two places and they answer different
        # questions. The book quotes a LINE for nearly every starter;
        # Covers publishes a PROJECTION for the handful its model
        # flags. The line is the spine because it is nearly always
        # there -- the projection is laid over it where it exists.
        cov = {}
        for (pkey, mkey), r in projs.items():
            if pkey != key:
                continue
            slot = COMPONENT.get(mkey)
            if slot and slot not in cov:
                cov[slot] = r

        comp = {c: None for c in COMPONENT_ORDER}
        for slot in COMPONENT_ORDER:
            bl = book_legs.get((key, slot))
            cv = cov.get(slot)
            if bl is None and cv is None:
                continue
            comp[slot] = {
                'line': (bl or cv).get('line'),
                'side': (cv or {}).get('side'),
                'projection': (cv or {}).get('projection'),
                'ev_pct': (cv or {}).get('ev_pct'),
                'src': 'books' if bl else 'covers',
                'n_books': (bl or {}).get('n_books'),
                'prob_over': (bl or {}).get('prob_over'),
            }

        have = [c for c in COMPONENT_ORDER if comp[c]]
        # The summed fallback is a sum of PROJECTIONS. Book lines are
        # not projections and must never be added into it -- that would
        # silently turn a market price into a model output.
        covers_pra = None
        vals = [(comp[c] or {}).get('projection') for c in COMPONENT_ORDER]
        if all(v is not None for v in vals):
            covers_pra = round(sum(vals), 2)

        b = books.get(key)
        # A market price outranks a sum of projections, so the book line
        # wins when both exist. `market_src` carries which one it was,
        # because a reader comparing our number against a traded price
        # and against somebody's model should know which they are
        # looking at -- they are not the same kind of claim.
        if b is not None:
            market_pra, market_src = b.get('line'), 'books'
        elif covers_pra is not None:
            market_pra, market_src = covers_pra, 'covers'
        else:
            market_pra, market_src = None, None
        delta = (round(market_pra - line, 2)
                 if market_pra is not None else None)

        # With nothing from the books and no projected component, a
        # writer pick is still a read -- weaker, and labelled so.
        note = None
        if b is None and not have:
            sides = {w['side'] for w in writers if short(w.get('player')) == key}
            note = ('writers split, no projection' if len(sides) > 1
                    else 'no outside read tonight')

        out.append({'player': name, 'team': team, 'pra_line': line,
                    'minutes': status, 'components': comp,
                    'covered': len(have), 'covers_pra': covers_pra,
                    'market_pra': market_pra, 'market_src': market_src,
                    'book_prob_over': (b or {}).get('prob_over'),
                    'book_n': (b or {}).get('n_books'),
                    'book_spread': (b or {}).get('line_spread'),
                    'delta': delta, 'note': note})

    # A row we can actually compare outranks one we cannot, and a book
    # price outranks a summed projection; our own PRA breaks ties.
    rank = {'books': 0, 'covers': 1, None: 2}
    out.sort(key=lambda p: (rank[p['market_src']], -p['covered'],
                            -p['pra_line']))
    return out


def short(name):
    """'Cade Cunningham' -> 'c. cunningham', so the two surfaces join.

    The writer list prints full names and the projection table prints an
    initial. Matching on the surname alone would collide (two Collinses
    is not hypothetical), so the initial is kept.
    """
    n = re.sub(r'[^A-Za-z\'\- ]', ' ', name or '').strip()
    parts = [p for p in n.split() if p]
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0].lower()
    first, last = parts[0], parts[-1]
    return f'{first[0].lower()}. {last.lower()}'


def norm_market(m):
    return re.sub(r'[^a-z]', '', (m or '').lower())


def load(day=None):
    if not os.path.exists(PICKS):
        return []
    out = []
    for ln in open(PICKS):
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if day and r.get('day') != day:
            continue
        out.append(r)
    return out


def dedupe(rows, kind):
    d = {}
    for r in rows:
        if r.get('kind') != kind:
            continue
        k = (short(r.get('player')), r.get('side'), r.get('line'),
             norm_market(r.get('market')))
        d[k] = r
    return d


def screen(rows, min_ev, min_diff):
    writers = dedupe(rows, 'prop')
    projs = dedupe(rows, 'prop_projection')

    # a player on both sides of one stat cancels out
    seen = collections.defaultdict(set)
    for (p, side, line, mk) in writers:
        seen[(p, mk)].add(side)
    both = {k for k, v in seen.items() if len(v) > 1}

    # project by player+market so a conflicting SIDE is still found
    by_pm = {}
    for (p, side, line, mk), r in projs.items():
        by_pm[(p, mk)] = r

    out = []
    for (p, side, line, mk), w in writers.items():
        if (p, mk) in both:
            out.append(('CONTRADICTION', w, None, None))
            continue
        pr = projs.get((p, side, line, mk))
        if pr and pr.get('proj_side') == 'push':
            # their model lands on the number: no view, so it cannot
            # corroborate a writer who took one
            out.append(('UNBACKED', w, pr, 'projection is a push'))
            continue
        if pr:
            out.append(('CORROBORATED', w, pr, None))
            continue
        alt = by_pm.get((p, mk))
        if alt:
            why = (f"projection says {alt['side']} {alt['line']} "
                   f"(proj {alt['projection']})")
            out.append(('CONFLICT', w, alt, why))
        else:
            out.append(('UNBACKED', w, None, None))

    kept, cut = [], []
    for tag, w, pr, why in out:
        if tag == 'CONTRADICTION':
            cut.append((tag, w, 'both sides taken'))
            continue
        if tag == 'CORROBORATED':
            ev = pr.get('ev_pct') or 0
            diff = abs(pr.get('difference') or 0)
            if ev < min_ev:
                cut.append((tag, w, f'EV {ev:.1f}% under {min_ev}%'))
                continue
            if diff < min_diff:
                cut.append((tag, w, f'projection {diff:.1f} off the line'))
                continue
        kept.append((tag, w, pr, why))

    order = {'CONFLICT': 0, 'CORROBORATED': 1, 'UNBACKED': 2}
    kept.sort(key=lambda x: (order[x[0]],
                             -((x[2] or {}).get('ev_pct') or 0)))
    return kept, cut, len(writers), len(projs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--day')
    ap.add_argument('--min-ev', type=float, default=10.0,
                    help='minimum stated EV%% on a corroborated pick')
    ap.add_argument('--min-diff', type=float, default=0.5,
                    help='projection must sit at least this far off the line')
    ap.add_argument('--json', action='store_true',
                    help='emit data/tonight.json for the site')
    a = ap.parse_args()

    rows = load(a.day)
    if not rows:
        print(f'nothing in {PICKS}' + (f' for {a.day}' if a.day else ''))
        return
    days = sorted({r.get('day') for r in rows if r.get('day')})
    kept, cut, nw, np_ = screen(rows, a.min_ev, a.min_diff)

    if a.json:
        import datetime
        def pack(t, w, pr, why):
            d = {'status': t, 'player': w['player'],
                 'side': w['side'], 'line': w['line'],
                 'market': w['market']}
            if pr:
                d.update({'projection': pr['projection'],
                          'ev_pct': pr.get('ev_pct'),
                          'stars': pr.get('stars'),
                          'difference': pr.get('difference'),
                          'game': (f"{pr.get('away','')}@{pr.get('home','')}"
                                   if pr.get('away') else None)})
            if why:
                d['note'] = why
            return d
        scores = {}
        for r in rows:
            if r.get('kind') == 'model_score':
                scores[(r['away'], r['home'])] = r
        # The page shows corroborated props only. A writer pick with no
        # projection behind it is one surface, not two, and a table
        # mixing the checked with the unchecked invites reading them
        # alike. The others stay in the log and in the counts -- they
        # are evidence about whose reads hold up, just not picks.
        # A row whose game is not on tonight's schedule is last
        # night's, mis-stamped by the scraper. Drop it rather than
        # show a settled game as a live pick.
        slate = tonight_teams(rows, days[-1] if days else None)

        # Rows off the props page carry no game, only the matchup
        # pages do -- so a filter that keeps anything without a game
        # keeps exactly the rows it was meant to catch. Build a
        # player -> game map from every row that DOES name one (book
        # lines always do) and use it for the rest.
        pgame = {}
        for r in rows:
            if r.get('away') and r.get('home') and r.get('player'):
                pgame.setdefault(short(r['player']),
                                 (canon(r['away']), canon(r['home'])))

        def on_tonight(w):
            a, h = canon(w.get('away')), canon(w.get('home'))
            if a or h:
                return (a in slate) or (h in slate)
            g = pgame.get(short(w.get('player')))
            if g:
                return (g[0] in slate) or (g[1] in slate)
            # nothing anywhere names this player's game: keep only if
            # we have no game data at all, since dropping everything
            # on a failed lookup is worse than showing a stale row
            return not pgame

        kept = [k for k in kept if on_tonight(k[1])]
        shown = [k for k in kept if k[0] == 'CORROBORATED']
        held = collections.Counter(k[0] for k in kept if k[0] != 'CORROBORATED')
        out = {
            'generated': datetime.datetime.now().astimezone().isoformat(),
            'day': days[-1] if days else None,
            'source': 'Covers.com',
            'counts': {'writer_picks': nw, 'projections': np_,
                       'kept': len(kept), 'dropped': len(cut),
                       'shown': len(shown),
                       'held_conflict': held.get('CONFLICT', 0),
                       'held_unbacked': held.get('UNBACKED', 0)},
            'rows': [pack(*k) for k in shown],
            'pra_watch': pra_watch(rows, days[-1] if days else None),
            'game_totals': [
                {'away': v['away'], 'home': v['home'],
                 'covers': v['proj_total']} for v in scores.values()],
        }
        print(json.dumps(out, indent=1))
        return

    print(f'day(s) {", ".join(days)}   '
          f'{nw} writer picks, {np_} projections\n')

    def line_of(w, pr):
        price = (f"{w['odds']:+d}" if 'odds' in w
                 else f"{w.get('win_pct','?')}%")
        s = (f"  {w['player']:24}{w['side'][:1]}{w['line']:<6}"
             f"{w['market']:18}{price:>6}")
        if pr:
            s += (f"   proj {pr['projection']:>6}"
                  f"  EV {pr.get('ev_pct', 0):>6.2f}%"
                  f"  {'*' * int(pr.get('stars') or 0)}")
        return s

    for tag in ('CONFLICT', 'CORROBORATED', 'UNBACKED'):
        grp = [k for k in kept if k[0] == tag]
        if not grp:
            continue
        print(f'{tag}  ({len(grp)})')
        print('-' * 96)
        for _t, w, pr, why in grp:
            print(line_of(w, pr))
            if why:
                print(f'      -> {why}')
        print()

    if cut:
        print(f'DROPPED  ({len(cut)})')
        print('-' * 96)
        for tag, w, why in cut:
            print(f'  {w["player"]:24}{w["side"][:1]}{w["line"]:<6}'
                  f'{w["market"]:18}  {why}')

    print('\nNOTE: every number above is Covers\'. This screens their two')
    print('surfaces against each other using our rules. It is not a')
    print('Sharpz pick list — that needs run.py to price the same games')
    print('so there is a second, independent projection to compare.')


if __name__ == '__main__':
    main()
