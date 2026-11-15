
// Data, lifted verbatim from the design so nothing is retyped.
const PRA = [['Nikola Jokic', 'DEN', '50', 'locked', 'Same team, coach and role'],
      ['Shai Gilgeous-Alexander', 'OKC', '42.5', 'locked', 'Core intact; role unchanged'],
      ['Cade Cunningham', 'DET', '38', 'moved', 'Healthy after a collapsed lung cut 2025-26'],
      ['Jalen Brunson', 'NYK', '37', 'locked', 'Cleared, no restrictions, Sep 24'],
      ['Kevin Durant', 'HOU', '36.5', 'moved', 'VanVleet back from ACL; usage should fall'],
      ['Donovan Mitchell', 'CLE', '35.5', 'locked', 'Four-year extension; same role'],
      ['Alperen Sengun', 'HOU', '35', 'moved', 'Creator role shrinks with VanVleet back'],
      ['Lauri Markkanen', 'UTA', '32', 'moved', 'Neck day-to-day; hip ended 2025-26'],
      ['RJ Barrett', 'TOR', '30', 'moved', 'Ingram out, Kawhi Leonard in'],
      ['Amen Thompson', 'HOU', '26', 'locked', 'Five-year extension; same team']];
const PR  = [['Nikola Jokic', 'DEN', '40', 'locked', 'Same team, coach and role'],
      ['Shai Gilgeous-Alexander', 'OKC', '36', 'locked', 'Core intact; role unchanged'],
      ['Kevin Durant', 'HOU', '32', 'moved', 'VanVleet back from ACL; usage should fall'],
      ['Lauri Markkanen', 'UTA', '30', 'moved', 'Jackson Jr. and Peterson take shots'],
      ['Jalen Brunson', 'NYK', '30', 'locked', 'Cleared, no restrictions, Sep 24'],
      ['Donovan Mitchell', 'CLE', '30', 'locked', 'Four-year extension; same role'],
      ['Cade Cunningham', 'DET', '29.5', 'moved', 'Healthy after a shortened 2025-26'],
      ['RJ Barrett', 'TOR', '26', 'moved', 'Ingram out, Kawhi Leonard in'],
      ['Jalen Duren', 'DET', '25.5', 'locked', 'Re-signed Oct 1; Stewart traded'],
      ['Amen Thompson', 'HOU', '22', 'locked', 'Five-year extension; same team']];
const COL = [['Duke', '120.5', '91.0', '+29.5', '70.1', 'average'],
      ['Florida', '120.6', '93.5', '+27.1', '74.1', 'very fast'],
      ['Arizona', '118.8', '91.8', '+27.0', '74.2', 'very fast'],
      ['Virginia', '120.7', '94.0', '+26.7', '70.4', 'average'],
      ['Illinois', '122.8', '96.5', '+26.3', '72.1', 'fast'],
      ['Houston', '118.8', '93.0', '+25.8', '67.3', 'slow'],
      ['Kansas', '119.2', '93.7', '+25.5', '72.2', 'fast'],
      ['Louisville', '119.2', '94.6', '+24.6', '72.6', 'fast']];
const MID = [['44', 'Grand Canyon', 'MWC', '+16.8', '73.1', 'top 50'],
      ['57', 'VCU', 'A10', '+15.3', '73.3', 'top 75'],
      ['65', 'High Point', 'BSth', '+13.0', '73.4', 'top 75'],
      ['66', 'New Mexico', 'MWC', '+12.9', '72.4', 'top 75'],
      ['67', "Saint Mary's", 'WCC', '+12.9', '70.8', 'top 75'],
      ['68', 'Saint Louis', 'A10', '+12.8', '72.7', 'top 75'],
      ['69', 'Wichita St.', 'Amer', '+12.4', '71.8', 'top 75'],
      ['72', 'Dayton', 'A10', '+11.8', '70.2', 'top 75'],
      ['77', 'George Washington', 'A10', '+10.9', '72.7', ''],
      ['86', 'Nevada', 'MWC', '+9.6', '69.7', ''],
      ['89', 'Santa Clara', 'WCC', '+9.3', '73.1', ''],
      ['90', 'South Florida', 'Amer', '+9.2', '73.1', '']];
const EUR = [['Panathinaikos', '117.4', '105.2', '+12.2', '+18.7', '74.6', 'average'],
      ['Olympiacos', '121.7', '112.4', '+9.4', '+10.4', '78.0', 'very fast'],
      ['Fenerbahçe', '114.2', '105.8', '+8.4', '+12.6', '73.0', 'slow'],
      ['Valencia', '120.2', '113.2', '+6.9', '+8.1', '79.2', 'very fast'],
      ['Real Madrid', '120.1', '113.3', '+6.8', '+7.2', '76.0', 'average'],
      ['Hapoel Tel Aviv', '118.4', '113.3', '+5.2', '+6.2', '75.9', 'average'],
      ['Crvena Zvezda', '109.9', '106.2', '+3.7', '+5.3', '76.1', 'average'],
      ['Barcelona', '118.8', '116.5', '+2.3', '+3.1', '74.8', 'average'],
      ['Žalgiris', '114.5', '114.1', '+0.3', '−3.7', '74.1', 'slow'],
      ['Beşiktaş', '119.6', '119.5', '+0.1', '+0.2', '76.1', 'no prior'],
      ['Dubai', '115.2', '116.2', '−0.9', '−1.7', '73.2', 'slow'],
      ['Virtus Bologna', '112.3', '113.9', '−1.6', '+1.4', '76.1', 'average'],
      ['Partizan', '107.3', '109.8', '−2.4', '+0.2', '74.5', 'slow'],
      ['Anadolu Efes', '107.6', '112.4', '−4.8', '−4.2', '75.8', 'average'],
      ['Maccabi Tel Aviv', '114.3', '120.4', '−6.2', '−8.3', '76.1', 'average'],
      ['Paris', '111.0', '117.3', '−6.2', '−9.7', '77.9', 'fast'],
      ['Bayern Munich', '112.6', '119.1', '−6.5', '−8.6', '74.9', 'average'],
      ['Olimpia Milan', '114.6', '122.4', '−7.8', '−13.0', '74.3', 'slow'],
      ['ASVEL', '110.3', '120.3', '−10.0', '−9.9', '76.5', 'fast'],
      ['Baskonia', '110.3', '120.9', '−10.5', '−14.1', '77.8', 'fast']];

const FOOTS = {
 "pra": "Each player\u2019s own typical points+rebounds+assists across his last three seasons \u2014 roughly where a book would set him. Not a projection for any given night. LOCKED: situation unchanged. MOVED: something a three-year average would miss.",
 "pr": "Each player\u2019s own typical points+rebounds across his last three seasons \u2014 roughly where a book would set him. Not a projection for any given night. LOCKED: situation unchanged. MOVED: something a three-year average would miss.",
 "college": "Adjusted efficiency per 100 possessions, opponent-adjusted. Preseason projections \u2014 these move once games are played. A high rating means a team is good, not that the market has it wrong.",
 "mid": "Teams outside the ACC, Big Ten, Big 12, SEC, Big East and Pac-12, ranked by where they sit NATIONALLY \u2014 that column is the point. None crack the top 43 this preseason. Ratings ignore conference; prices don\u2019t, and non-conference season is where that gap would show.",
 "euro": "Points scored and allowed per 100 possessions, 2026-27, after six rounds. NET is what we price: these six games blended 58/42 with each club\u2019s finish last season, because six games on their own overstate how far apart teams are. 6 GAMES SAY is that unblended figure, with an arrow where the two disagree by more than 2.5 points \u2014 Panathinaikos and Fenerbah\u00e7e are being held back off a hot start, \u017dalgiris and Baskonia lifted off a cold one. Be\u015fikta\u015f is newly promoted and has no last season to blend, so it is pulled toward the league average instead. Margins in this league carry a standard error of 11.7 points, measured out-of-sample across 310 games."
};

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

const num = s => parseFloat(String(s).replace('\u2212', '-'));

const T = {
  rows:  d => `<tr>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-family: 'Archivo', sans-serif; font-weight: 600; font-size: 16px; letter-spacing: -0.2px; white-space: nowrap">${d.name}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; font-family: 'IBM Plex Mono', monospace; font-size: 12px; letter-spacing: 0.6px; color: #8B98A9">${d.team}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 20px; font-weight: 500; color: #FF6B2C; letter-spacing: -0.5px">${d.line}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; white-space: nowrap"><span style="${d.pill}">${d.status}</span></td>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-size: 14px; line-height: 1.5; color: #96A3B4">${d.note}</td>
              </tr>`,
  crows: d => `<tr>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-family: 'Archivo', sans-serif; font-weight: 600; font-size: 16px; letter-spacing: -0.2px; white-space: nowrap">${d.team}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #C3CDDA">${d.oe}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #C3CDDA">${d.de}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 20px; font-weight: 500; color: #FF6B2C; letter-spacing: -0.5px">${d.net}</td>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-size: 14px; color: #96A3B4">
                  <span style="font-family: 'IBM Plex Mono', monospace; font-size: 14px; color: #C3CDDA">${d.tempo}</span>
                  <span style="color: #6B7A8D; margin-left: 10px">${d.pace}</span>
                </td>
              </tr>`,
  mrows: d => `<tr>
                <td style="padding: 15px 18px 15px 24px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #8B98A9">${d.rank}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; font-family: 'Archivo', sans-serif; font-weight: 600; font-size: 16px; letter-spacing: -0.2px; white-space: nowrap">${d.team}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; font-family: 'IBM Plex Mono', monospace; font-size: 12px; letter-spacing: 0.6px; color: #8B98A9">${d.conf}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 20px; font-weight: 500; color: #FF6B2C; letter-spacing: -0.5px">${d.net}</td>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-size: 14px; color: #96A3B4">
                  <span style="font-family: 'IBM Plex Mono', monospace; font-size: 14px; color: #C3CDDA">${d.tempo}</span>
                  <span style="color: #6B7A8D; margin-left: 10px">${d.tag}</span>
                </td>
              </tr>`,
  erows: d => `<tr>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-family: 'Archivo', sans-serif; font-weight: 600; font-size: 16px; letter-spacing: -0.2px; white-space: nowrap">${d.team}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #C3CDDA">${d.oe}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #C3CDDA">${d.de}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; font-family: 'IBM Plex Mono', monospace; font-size: 20px; font-weight: 500; color: #FF6B2C; letter-spacing: -0.5px">${d.net}</td>
                <td style="padding: 15px 12px; border-top: 1px solid #1A212B; text-align: right; white-space: nowrap">
                  <span style="font-family: 'IBM Plex Mono', monospace; font-size: 15px; color: #6B7A8D">${d.raw}</span>
                  <span style="${d.gapStyle}">${d.gap}</span>
                </td>
                <td style="padding: 15px 24px; border-top: 1px solid #1A212B; font-size: 14px; color: #96A3B4">
                  <span style="font-family: 'IBM Plex Mono', monospace; font-size: 14px; color: #C3CDDA">${d.tempo}</span>
                  <span style="color: #6B7A8D; margin-left: 10px">${d.pace}</span>
                </td>
              </tr>`,
  tabs:  d => `<button type="button" role="tab" aria-selected="${d.sel}" onClick="${d.pick}" style="${d.style}">
          <span style="font-family: 'IBM Plex Mono', monospace; font-size: 10px; letter-spacing: 1.8px; display: block; margin-bottom: 4px; opacity: 0.7">${d.kicker}</span>
          ${d.label}
        </button>`
};

let tab = 'pra';

function erow(e) {
  const dgap = num(e[3]) - num(e[4]);
  const big = Math.abs(dgap) >= 2.5;
  return { team:e[0], oe:e[1], de:e[2], net:e[3], raw:e[4],
           tempo:e[5], pace:e[6],
           gap: big ? (dgap > 0 ? ' \u25B2' : ' \u25BC') : '',
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
  const SEP = '  ·  ';
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
