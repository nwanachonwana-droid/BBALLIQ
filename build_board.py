#!/usr/bin/env python3
"""
Build index.html from Main.dc.html.

The board is the one page with real logic -- five data tables, a tab
bar, and a status strip -- so its template directives are rewritten as
plain JS rather than stripped. The markup itself is transformed in
place rather than retyped, so no row, colour or style is transcribed by
hand and nothing drifts from what was designed.

  <sc-for list="{{rows}}" as="r"> TPL </sc-for>
      -> the enclosing <tbody> gets an id, TPL becomes a JS template
  <sc-if value="{{isNba}}"> PANEL </sc-if>
      -> <div id="panel-isNba" hidden> PANEL </div>
  {{hole}} in the status strip
      -> an element id that JS writes into

The status numbers come from data/status.json at load, so the daily
update is one small file and a push -- no HTML rebuild.
"""
import re, json

src = open('Main.dc.html').read()
body = re.search(r'<x-dc>(.*)</x-dc>', src, re.S).group(1)
logic = re.search(r'<script type="text/x-dc".*?>(.*)</script>', src, re.S).group(1)
helmet = re.search(r'<helmet>(.*?)</helmet>', src, re.S).group(1).strip()

# ---- data arrays, verbatim -----------------------------------------
arrays = {}
for name in ('PRA', 'PR', 'COL', 'MID', 'EUR'):
    arrays[name] = re.search(rf'var {name} = \[(.*?)\n    \];', logic,
                             re.S).group(1).strip()

# ---- row templates out of each sc-for ------------------------------
tpl = {}


def grab_for(m):
    listname = m.group('list')
    var = m.group('var')
    inner = m.group('inner')
    # {{r.field}} -> ${d.field}
    t = re.sub(r'\{\{' + var + r'\.(\w+)\}\}', r'${d.\1}', inner)
    tpl[listname] = t.strip()
    return ''


FOR = re.compile(
    r'<sc-for list="\{\{(?P<list>\w+)\}\}" as="(?P<var>\w+)"[^>]*>'
    r'(?P<inner>.*?)</sc-for>', re.S)

# tables: give the enclosing tbody an id, empty it
for listname in ('rows', 'crows', 'mrows', 'erows'):
    pat = re.compile(
        r'<tbody>\s*<sc-for list="\{\{' + listname + r'\}\}" as="(\w+)"[^>]*>'
        r'(.*?)</sc-for>\s*</tbody>', re.S)
    m = pat.search(body)
    assert m, f'tbody/sc-for not found for {listname}'
    var, inner = m.group(1), m.group(2)
    tpl[listname] = re.sub(r'\{\{' + var + r'\.(\w+)\}\}', r'${d.\1}',
                           inner).strip()
    body = body[:m.start()] + f'<tbody id="tb-{listname}"></tbody>' + body[m.end():]

# tab bar: keep the container, empty it
m = re.search(r'(<div role="tablist"[^>]*>)\s*<sc-for list="\{\{tabs\}\}" '
              r'as="(\w+)"[^>]*>(.*?)</sc-for>\s*(</div>)', body, re.S)
assert m, 'tablist sc-for not found'
tpl['tabs'] = re.sub(r'\{\{' + m.group(2) + r'\.(\w+)\}\}', r'${d.\1}',
                     m.group(3)).strip()
body = (body[:m.start()] + m.group(1).replace('>', ' id="tabbar">', 1)
        + m.group(4) + body[m.end():])

# ---- sc-if -> hideable panels --------------------------------------
def panel(m):
    return (f'<div id="panel-{m.group(1)}" hidden>{m.group(2)}</div>')


body = re.sub(r'<sc-if value="\{\{(\w+)\}\}"[^>]*>(.*?)</sc-if>', panel,
              body, flags=re.S)

# ---- status line -> one element JS writes into ----------------------
body = body.replace('id="dateline">{{dateline}}</div>',
                    'id="dateline"></div>')
body = body.replace('>{{foot}}</p>', ' id="foot"></p>')
body = body.replace('{{lineHead}}', '<span id="lineHead"></span>')

# inter-page links
for old, new in (('Main.dc.html', 'index.html'),
                 ('Research.dc.html', 'research.html'),
                 ('Minutes.dc.html', 'minutes.html'),
                 ('Misses.dc.html', 'misses.html')):
    body = body.replace(f'href="{old}"', f'href="{new}"')

# footnotes, lifted from the artboard so the copy stays identical
fm = re.search(r'var foots = \{(.*?)\n    \};', logic, re.S)
raw = fm.group(1)
# Each footnote is one long single-line string, so parse line by line.
# A DOTALL match overruns into the next key, and the last entry has no
# trailing comma or newline to anchor on.
assert "\\'" not in raw, 'a footnote contains an escaped quote; parser needs work'
foots = dict(re.findall(r"^\s+(\w+): '(.*?)',?$", raw, re.M))
for key in ('pra', 'pr', 'college', 'mid', 'euro'):
    assert key in foots, f'footnote {key} not found'

leftover = re.findall(r'\{\{[^}]*\}\}', body)
assert not leftover, f'unconverted holes: {set(leftover)}'

APP = """
// Data, lifted verbatim from the design so nothing is retyped.
const PRA = [@@PRA@@];
const PR  = [@@PR@@];
const COL = [@@COL@@];
const MID = [@@MID@@];
const EUR = [@@EUR@@];

const FOOTS = @@FOOTS@@;

const DEFS = [
  { id:'pra',     kicker:'NBA',        label:'Points + rebounds + assists' },
  { id:'pr',      kicker:'NBA',        label:'Points + rebounds' },
  { id:'college', kicker:'COLLEGE',    label:'Team efficiency' },
  { id:'mid',     kicker:'COLLEGE',    label:'Best outside the big leagues' },
  { id:'euro',    kicker:'EUROLEAGUE', label:'Team efficiency, in season' }
];

const pill = st => {
  const on = st === 'locked';
  return "font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 1.4px; "
    + 'padding: 5px 10px; border-radius: 4px; text-transform: uppercase; '
    + (on ? 'color: #FF8A52; border: 1px solid #5C2D12; background: #2A1408;'
          : 'color: #96A3B4; border: 1px solid #28323F; background: #1A212B;');
};
const tabStyle = on =>
  "text-align: left; font-family: 'IBM Plex Sans', sans-serif; font-size: 15px; "
  + 'font-weight: 600; padding: 11px 18px; border-radius: 8px; cursor: pointer; min-height: 44px; '
  + (on ? 'background: #FF6B2C; color: #0A0D12; border: 1px solid #FF6B2C;'
        : 'background: #11161E; color: #96A3B4; border: 1px solid #28323F;');

const num = s => parseFloat(String(s).replace('\\u2212', '-'));

const T = {
  rows:  d => `@@TPL_rows@@`,
  crows: d => `@@TPL_crows@@`,
  mrows: d => `@@TPL_mrows@@`,
  erows: d => `@@TPL_erows@@`,
  tabs:  d => `@@TPL_tabs@@`
};

let tab = 'pra';

function erow(e) {
  const dgap = num(e[3]) - num(e[4]);
  const big = Math.abs(dgap) >= 2.5;
  return { team:e[0], oe:e[1], de:e[2], net:e[3], raw:e[4],
           tempo:e[5], pace:e[6],
           gap: big ? (dgap > 0 ? ' \\u25B2' : ' \\u25BC') : '',
           gapStyle: 'font-size: 11px; margin-left: 5px; color: '
             + (big ? (dgap > 0 ? '#3FB68B' : '#C2693C') : 'transparent') };
}

function render() {
  const src = tab === 'pr' ? PR : PRA;
  document.getElementById('tb-rows').innerHTML = src.map(r =>
    T.rows({ name:r[0], team:r[1], line:r[2], status:r[3], note:r[4],
             pill: pill(r[3]) })).join('');
  document.getElementById('tb-crows').innerHTML = COL.map(c =>
    T.crows({ team:c[0], oe:c[1], de:c[2], net:c[3], tempo:c[4],
              pace:c[5] })).join('');
  document.getElementById('tb-mrows').innerHTML = MID.map(m =>
    T.mrows({ rank:m[0], team:m[1], conf:m[2], net:m[3], tempo:m[4],
              tag:m[5] })).join('');
  document.getElementById('tb-erows').innerHTML =
    EUR.map(e => T.erows(erow(e))).join('');

  document.getElementById('tabbar').innerHTML = DEFS.map(d =>
    T.tabs({ label:d.label, kicker:d.kicker, sel: d.id === tab,
             style: tabStyle(d.id === tab), pick:'' })).join('');
  [...document.querySelectorAll('#tabbar button')].forEach((b, i) => {
    b.onclick = () => { tab = DEFS[i].id; render(); };
  });

  const on = {
    'panel-isNba':     tab === 'pra' || tab === 'pr',
    'panel-isCollege': tab === 'college',
    'panel-isMid':     tab === 'mid',
    'panel-isEuro':    tab === 'euro'
  };
  for (const [id, show] of Object.entries(on)) {
    const el = document.getElementById(id);
    if (el) el.hidden = !show;
  }
  document.getElementById('lineHead').textContent =
    tab === 'pr' ? '3-SEASON PR' : '3-SEASON PRA';
  document.getElementById('foot').textContent = FOOTS[tab];
}

// One line, from data/status.json. The board leads with names; this
// says only whether anything is on tonight. If the fetch fails the
// line still reads sensibly rather than going blank.
// Two things feed this line, and they arrive at different times: our
// own priced run, which some nights produces nothing, and the outside
// reads, which land every morning. Leading with "NO PRICES YET" while
// seventeen screened reads sat directly below it described the half of
// the page that was empty and ignored the half that wasn't.
let _S = null, _T = null;
function dateline() {
  const el = document.getElementById('dateline');
  if (!el) return;
  const r = _S && _S.latest;
  const fresh = _T && !staleDay(_T);
  const n = (fresh && _T.rows && _T.rows.length) || 0;
  const SEP = '  \u00b7  ';
  const when = r ? r.when
    : (fresh ? _T.day.slice(4, 6) + '/' + _T.day.slice(6, 8) : '');
  const outside = n ? n + ' OUTSIDE READS SCREENED'
                    : (_T && staleDay(_T) ? 'NO READS YET TODAY' : '');
  const ours = r
    ? (r.games + ' GAMES PRICED' + SEP
       + (r.taken > 0 ? r.taken + ' ON THE BOARD' : 'NOTHING CLEARED'))
    : 'OUR PRICES NOT IN YET';
  const txt = [when, outside, ours].filter(Boolean).join(SEP);
  el.textContent = txt || 'NOTHING IN YET TONIGHT';
}

render();
// Tonight's independent reads. Covers' numbers, our ordering.
// ---------------------------------------------------------------
// Every number on this page carries its own label. An unlabelled
// 42.5 beside an unlabelled 26.81 is two facts the reader has to
// guess at; a stacked caption survives flex wrapping, which a table
// header row does not once a phone forces the columns to stack.
function cell(label, value, color, min) {
  return `<div style="min-width: ${min || 64}px">
    <div style="font-family: 'IBM Plex Mono', monospace; font-size: 9px;
      letter-spacing: 1.3px; color: #5D6B7C; margin-bottom: 3px;
      white-space: nowrap">${label}</div>
    <div style="font-family: 'IBM Plex Mono', monospace; font-size: 15px;
      color: ${color || '#C3CDDA'}; white-space: nowrap">${value}</div>
  </div>`;
}

const DASH = '<span style="color: #3C4654">&mdash;</span>';
function fmt(v, dp) {
  return (v === undefined || v === null) ? DASH
    : (dp === undefined ? String(v) : Number(v).toFixed(dp));
}
function sgn(v, dp) {
  if (v === undefined || v === null) return DASH;
  return (v > 0 ? '+' : '') + Number(v).toFixed(dp === undefined ? 2 : dp);
}

// Data from a previous day is not "tonight". The board showed
// yesterday's players as tonight's slate because nothing compared the
// file's date to the calendar -- the morning scrape had not run yet,
// so the newest rows in the log were still last night's. A board that
// silently presents stale reads as live ones is worse than an empty
// one, so this refuses to render rows and says which day they are from.
function todayStr() {
  const d = new Date();
  return d.getFullYear() + String(d.getMonth() + 1).padStart(2, '0')
       + String(d.getDate()).padStart(2, '0');
}
function staleDay(t) {
  return (t && t.day && t.day < todayStr()) ? t.day : null;
}
function prettyDay(d) {
  if (!d || d.length !== 8) return d || '';
  const m = ['Jan','Feb','Mar','Apr','May','Jun',
             'Jul','Aug','Sep','Oct','Nov','Dec'][+d.slice(4, 6) - 1];
  return +d.slice(6, 8) + ' ' + m;
}
function staleNotice(what, day) {
  return `<div style="padding: 20px 24px; background: #11161E;
      font-size: 14px; line-height: 1.7; color: #8B98A9">
    No ${what} for today yet. The most recent are from
    <strong style="color: #C3CDDA">${prettyDay(day)}</strong> and are not
    shown here, because last night's players are not tonight's slate.
  </div>`;
}

// --- PRA WATCH ---------------------------------------------------
// Leads the page: these are the players we hold a number on. The
// three components are shown whether or not Covers quoted them, so a
// gap reads as a gap instead of vanishing.
function praWatch(t) {
  const block = document.getElementById('praBlock');
  const body = document.getElementById('praRows');
  const meta = document.getElementById('praMeta');
  if (!block || !body) return;
  const stale = staleDay(t);
  if (stale) {
    block.hidden = false;
    if (meta) meta.textContent = '';
    body.innerHTML = staleNotice('board players on', stale);
    return;
  }
  const w = (t && t.pra_watch) || [];
  if (!w.length) { block.hidden = true; return; }
  block.hidden = false;
  const priced = w.filter(p => p.market_pra !== null
                            && p.market_pra !== undefined).length;
  const booked = w.filter(p => p.market_src === 'books').length;
  if (meta) {
    let tail;
    if (!priced) tail = 'no market line on any of them';
    else if (booked === priced) tail = booked + ' with a book line';
    else tail = priced + ' comparable, ' + booked + ' from the books';
    meta.textContent = w.length + ' of 10 board players on tonight'
      + '  ·  ' + tail;
  }
  const MONO = "font-family: 'IBM Plex Mono', monospace;";
  const ARCH = "font-family: 'Archivo', sans-serif;";

  body.innerHTML = w.map((p, i) => {
    const top = i ? 'border-top: 1px solid #1A212B;' : '';
    const lock = p.minutes === 'locked';
    const pill = lock
      ? 'color: #FF8A52; border: 1px solid #5C2D12; background: #2A1408;'
      : 'color: #96A3B4; border: 1px solid #28323F; background: #1A212B;';

    // A leg may carry a book line, a Covers projection, or both. The
    // label says which, because "9.5" from a book and "9.5 -> 8.2"
    // from a model are different claims and a reader who cannot tell
    // them apart is worse off than one shown nothing.
    const comp = p.components || {};
    const part = (key, name) => {
      const c = comp[key];
      if (!c) return cell(name + '  LINE', DASH, null, 92);
      const hasProj = c.projection !== null && c.projection !== undefined;
      const label = name + (hasProj ? '  LINE → PROJ' : '  LINE');
      const v = hasProj
        ? `${fmt(c.line)} → ${fmt(c.projection)}`
        : fmt(c.line);
      return cell(label, v, '#C3CDDA', 92);
    };

    // Name the source in the column label itself. A traded price and a
    // sum of somebody's projections are not the same kind of claim and
    // must not share an unmarked column.
    const mlabel = p.market_src === 'books'
      ? 'BOOK PRA · ' + (p.book_n || 0) + ' BOOKS'
      : (p.market_src === 'covers' ? 'COVERS PRA · SUMMED'
                                   : 'MARKET PRA');

    // Green when the market sits under our PRA, orange when over: the
    // direction is the whole content of the comparison.
    const d = p.delta;
    const dcol = (d === null || d === undefined) ? '#5D6B7C'
               : (d < 0 ? '#3FB68B' : '#FF6B2C');

    const note = p.note
      ? `<div style="flex: 1 1 100%; font-size: 13px; color: #6B7A8D;
           padding-top: 2px">${p.note}</div>`
      : '';

    return `<div style="padding: 16px 24px; background: #11161E; ${top}">
      <div style="display: flex; flex-wrap: wrap; gap: 10px 14px;
        align-items: center; margin-bottom: 12px">
        <span style="${ARCH} font-weight: 600; font-size: 17px;
          letter-spacing: -0.2px">${p.player}</span>
        <span style="${MONO} font-size: 12px; letter-spacing: 0.6px;
          color: #8B98A9">${p.team}</span>
        <span style="${MONO} font-size: 9px; letter-spacing: 1.4px;
          padding: 4px 8px; border-radius: 4px; text-transform: uppercase;
          ${pill}">${p.minutes}</span>
      </div>
      <div style="display: flex; flex-wrap: wrap; gap: 14px 22px;
        align-items: flex-start">
        ${cell('OUR PRA', fmt(p.pra_line), '#FF6B2C', 70)}
        ${cell(mlabel, fmt(p.market_pra), '#C3CDDA', 112)}
        ${cell('VS OURS', sgn(p.delta), dcol, 70)}
        ${p.book_prob_over != null
            ? cell('P(OVER)', (p.book_prob_over * 100).toFixed(1) + '%',
                   '#8B98A9', 72)
            : ''}
        ${part('pts', 'PTS')}
        ${part('reb', 'REB')}
        ${part('ast', 'AST')}
        ${note}
      </div>
    </div>`;
  }).join('');
}

// --- CORROBORATED PROPS ------------------------------------------
// Second block. Only props where a writer and the projection model
// independently took the same side; the rest are counted, not shown.
function ticker(t) {
  const meta = document.getElementById('tickMeta');
  const body = document.getElementById('tickRows');
  const when = document.getElementById('tickWhen');
  if (!body) return;
  const stale = staleDay(t);
  if (stale) {
    if (meta) meta.textContent = '';
    body.innerHTML = staleNotice('outside reads', stale);
    return;
  }
  if (!t || !t.rows || !t.rows.length) {
    body.innerHTML = '<div style="padding: 20px 24px; color: #6B7A8D; '
      + 'font-size: 14px">Nothing corroborated tonight.</div>';
    return;
  }
  if (meta) {
    const c = t.counts || {};
    const held = (c.held_conflict || 0) + (c.held_unbacked || 0);
    meta.textContent = t.rows.length + ' of ' + (c.writer_picks || 0)
      + ' picks corroborated'
      + (held ? '  ·  ' + held + ' read but not shown' : '');
  }
  if (when && t.generated) {
    when.textContent = new Date(t.generated)
      .toLocaleString(undefined, { day: 'numeric', month: 'short',
                                   hour: '2-digit', minute: '2-digit' });
  }
  const MONO = "font-family: 'IBM Plex Mono', monospace;";
  const ARCH = "font-family: 'Archivo', sans-serif;";

  body.innerHTML = t.rows.map((r, i) => {
    const top = i ? 'border-top: 1px solid #1A212B;' : '';
    const side = r.side.charAt(0).toUpperCase() + r.side.slice(1);
    return `<div style="padding: 15px 24px; background: #11161E; ${top}">
      <div style="display: flex; flex-wrap: wrap; gap: 8px 14px;
        align-items: baseline; margin-bottom: 11px">
        <span style="${ARCH} font-weight: 600; font-size: 16px;
          letter-spacing: -0.2px">${r.player}</span>
        <span style="font-size: 14px; color: #8B98A9">${r.market}</span>
        ${r.game ? `<span style="${MONO} font-size: 11px;
          color: #6B7A8D">${r.game}</span>` : ''}
      </div>
      <div style="display: flex; flex-wrap: wrap; gap: 14px 22px;
        align-items: flex-start">
        ${cell('PICK', side, '#C3CDDA', 62)}
        ${cell('LINE', fmt(r.line), '#C3CDDA', 56)}
        ${cell('PROJ', fmt(r.projection), '#C3CDDA', 62)}
        ${cell('EDGE', sgn(r.difference, 1), '#8B98A9', 62)}
        ${cell('EV', r.ev_pct != null ? r.ev_pct.toFixed(1) + '%' : DASH,
               '#3FB68B', 62)}
      </div>
    </div>`;
  }).join('');
}

// --- TONIGHT'S PICKS ----------------------------------------------
// Pending picks for today only. A settled pick moves to the record
// and the log; leaving winners on the board after they land turns a
// results page into a sales page.
const TIER_STYLE = {
  core:        ['#FF8A52', '#5C2D12', '#2A1408'],
  speculative: ['#8B98A9', '#28323F', '#1A212B']
};

function editorPicks(d) {
  const block = document.getElementById('editorBlock');
  const body = document.getElementById('editorRows');
  const meta = document.getElementById('editorMeta');
  if (!block || !body) return;
  const today = todayStr();
  const picks = ((d && d.picks) || []).filter(
    p => p.day === today && !p.result);
  if (!picks.length) { block.hidden = true; return; }
  block.hidden = false;

  const order = { core: 0, speculative: 1 };
  picks.sort((a, b) => (order[a.tier] ?? 9) - (order[b.tier] ?? 9)
                    || String(a.subject).localeCompare(String(b.subject)));
  if (meta) {
    const n = picks.filter(p => p.tier === 'core').length;
    meta.textContent = picks.length + ' published'
      + (n && n !== picks.length ? '  ·  ' + n + ' core' : '');
  }
  const MONO = "font-family: 'IBM Plex Mono', monospace;";
  const ARCH = "font-family: 'Archivo', sans-serif;";

  body.innerHTML = picks.map((p, i) => {
    const top = i ? 'border-top: 1px solid #1A212B;' : '';
    const st = TIER_STYLE[p.tier] || TIER_STYLE.speculative;
    const odds = p.odds > 0 ? '+' + p.odds : String(p.odds);
    // "over 2.5 threes" is how a 3+ bet is logged; print it the way it
    // was sold rather than the way it is stored.
    const call = p.market === 'ml'
      ? 'Moneyline'
      : (p.market === 'threes' && p.side === 'over'
          ? (p.line + 0.5) + '+ threes'
          : p.side.charAt(0).toUpperCase() + p.side.slice(1) + ' ' + p.line);
    const mk = p.market === 'pa' ? 'PTS + AST'
             : p.market === 'pr' ? 'PTS + REB'
             : p.market === 'pra' ? 'PTS + REB + AST'
             : p.market.toUpperCase();
    return `<div style="display: flex; flex-wrap: wrap; gap: 10px 18px;
        align-items: baseline; padding: 15px 24px; background: #11161E; ${top}">
      <span style="${MONO} font-size: 9px; letter-spacing: 1.4px;
        padding: 4px 8px; border-radius: 4px; color: ${st[0]};
        border: 1px solid ${st[1]}; background: ${st[2]};
        text-transform: uppercase">${p.tier || 'core'}</span>
      <span style="${ARCH} font-weight: 600; font-size: 17px;
        letter-spacing: -0.2px; min-width: 190px">${p.subject}</span>
      <span style="font-size: 15px; color: #C3CDDA;
        min-width: 130px">${call}</span>
      <span style="${MONO} font-size: 12px; letter-spacing: 0.6px;
        color: #6B7A8D; min-width: 130px">${mk}</span>
      <span style="${MONO} font-size: 15px; color: #8B98A9">${odds}</span>
    </div>`;
  }).join('');
}

// --- THE RECORD -------------------------------------------------
// Counts from pick one, win rate only at 30. The threshold is the
// point of the panel: a number shown early gets read as a result.
function record(d) {
  const block = document.getElementById('recBlock');
  const body = document.getElementById('recRows');
  const meta = document.getElementById('recMeta');
  if (!block || !body) return;
  if (!d || !d.logged) { block.hidden = true; return; }
  block.hidden = false;
  if (meta) {
    meta.textContent = d.days && d.days.length
      ? d.days.length + (d.days.length === 1 ? ' day' : ' days') + ' published'
      : '';
  }
  const open = d.graded >= (d.unlocks_at || 30);
  const cells = [
    cell('LOGGED', String(d.logged), '#C3CDDA', 70),
    cell('GRADED', String(d.graded), '#C3CDDA', 70),
    cell('PENDING', String(d.pending), '#8B98A9', 70)
  ];
  if (open) {
    const u = d.units || 0;
    const ucol = u > 0 ? '#3FB68B' : (u < 0 ? '#FF6B2C' : '#8B98A9');
    cells.push(cell('RECORD', d.record || '—', '#C3CDDA', 86));
    cells.push(cell('UNITS', sgn(u, 2), ucol, 70));
    if (d.roi_pct !== null && d.roi_pct !== undefined) {
      cells.push(cell('ROI', sgn(d.roi_pct, 1) + '%', ucol, 70));
    }
    if (d.win_pct !== null && d.win_pct !== undefined) {
      cells.push(cell('WIN RATE', d.win_pct.toFixed(1) + '%', '#C3CDDA', 80));
    }
  } else {
    cells.push(cell('WIN RATE', 'AT ' + (d.unlocks_at || 30), '#5D6B7C', 86));
  }

  const note = open
    ? ''
    : `<div style="flex: 1 1 100%; font-size: 13px; color: #6B7A8D;
         padding-top: 4px">${d.to_go} more graded before the win rate
         goes up. Everything logged so far is in the CSV below.</div>`;

  let tiers = '';
  if (open && d.by_tier) {
    tiers = Object.entries(d.by_tier).map(([k, v]) => {
      const u = v.units || 0;
      const c = u > 0 ? '#3FB68B' : (u < 0 ? '#FF6B2C' : '#8B98A9');
      return `<div style="display: flex; flex-wrap: wrap; gap: 10px 18px;
          align-items: baseline; padding: 11px 24px; background: #11161E;
          border-top: 1px solid #1A212B">
        <span style="font-family: 'IBM Plex Mono', monospace; font-size: 10px;
          letter-spacing: 1.3px; color: #5D6B7C; min-width: 96px;
          text-transform: uppercase">${k}</span>
        <span style="font-family: 'IBM Plex Mono', monospace; font-size: 14px;
          color: #C3CDDA; min-width: 70px">${v.record}</span>
        <span style="font-family: 'IBM Plex Mono', monospace; font-size: 14px;
          color: ${c}">${sgn(u, 2)}u</span>
      </div>`;
    }).join('');
  }

  body.innerHTML = `<div style="display: flex; flex-wrap: wrap;
      gap: 14px 22px; align-items: flex-start; padding: 16px 24px;
      background: #11161E">${cells.join('')}${note}</div>` + tiers;
}

dateline();
ticker(null);
praWatch(null);
record(null);
editorPicks(null);
// Either fetch may fail or arrive first, so each stores its half and
// re-renders the line. Neither waits on the other: the reads show
// whether or not we have priced anything, which is the point.
fetch('data/tonight.json', { cache: 'no-store' })
  .then(r => r.ok ? r.json() : Promise.reject())
  .then(d => { _T = d; ticker(d); praWatch(d); dateline(); })
  .catch(() => {});
fetch('data/record.json', { cache: 'no-store' })
  .then(r => r.ok ? r.json() : Promise.reject())
  .then(d => { record(d); editorPicks(d); })
  .catch(() => {});
fetch('data/status.json', { cache: 'no-store' })
  .then(r => r.ok ? r.json() : Promise.reject())
  .then(d => { _S = d; dateline(); })
  .catch(() => {});
"""

SUBS = {
    'PRA': arrays['PRA'], 'PR': arrays['PR'], 'COL': arrays['COL'],
    'MID': arrays['MID'], 'EUR': arrays['EUR'],
    'FOOTS': json.dumps(foots, indent=1),
    'TPL_rows': tpl['rows'], 'TPL_crows': tpl['crows'],
    'TPL_mrows': tpl['mrows'], 'TPL_erows': tpl['erows'],
    'TPL_tabs': tpl['tabs'],
}
app = APP
for k, v in SUBS.items():
    app = app.replace('@@' + k + '@@', v)
assert '@@' not in app, 'unsubstituted marker remains'

page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sharpz Basketball — the board</title>
<meta name="description" content="Players and teams we can put a number on.">
{helmet}
</head>
<body>
{body}
<script>
{app}
</script>
</body>
</html>
"""

open('index.html', 'w').write(page)
open('app.js', 'w').write(app)
print(f'index.html  {len(page)} bytes')
print(f'  tables: {", ".join(tpl)}')
print(f'  footnotes: {", ".join(foots)}')
print(f'  rows: PRA {len(arrays["PRA"].splitlines())}, '
      f'COL {len(arrays["COL"].splitlines())}, '
      f'MID {len(arrays["MID"].splitlines())}, '
      f'EUR {len(arrays["EUR"].splitlines())}')
