
class Component extends DCLogic {
  constructor(props) {
    super(props);
    this.state = { tab: 'pra' };
  }

  renderVals() {
    var tab = this.state.tab;
    var self = this;

    var PRA = [
      ['Nikola Jokic', 'DEN', '50', 'locked', 'Same team, coach and role'],
      ['Shai Gilgeous-Alexander', 'OKC', '42.5', 'locked', 'Core intact; role unchanged'],
      ['Cade Cunningham', 'DET', '38', 'moved', 'Healthy after a collapsed lung cut 2025-26'],
      ['Jalen Brunson', 'NYK', '37', 'locked', 'Cleared, no restrictions, Sep 24'],
      ['Kevin Durant', 'HOU', '36.5', 'moved', 'VanVleet back from ACL; usage should fall'],
      ['Donovan Mitchell', 'CLE', '35.5', 'locked', 'Four-year extension; same role'],
      ['Alperen Sengun', 'HOU', '35', 'moved', 'Creator role shrinks with VanVleet back'],
      ['Lauri Markkanen', 'UTA', '32', 'moved', 'Neck day-to-day; hip ended 2025-26'],
      ['RJ Barrett', 'TOR', '30', 'moved', 'Ingram out, Kawhi Leonard in'],
      ['Amen Thompson', 'HOU', '26', 'locked', 'Five-year extension; same team']
    ];

    var PR = [
      ['Nikola Jokic', 'DEN', '40', 'locked', 'Same team, coach and role'],
      ['Shai Gilgeous-Alexander', 'OKC', '36', 'locked', 'Core intact; role unchanged'],
      ['Kevin Durant', 'HOU', '32', 'moved', 'VanVleet back from ACL; usage should fall'],
      ['Lauri Markkanen', 'UTA', '30', 'moved', 'Jackson Jr. and Peterson take shots'],
      ['Jalen Brunson', 'NYK', '30', 'locked', 'Cleared, no restrictions, Sep 24'],
      ['Donovan Mitchell', 'CLE', '30', 'locked', 'Four-year extension; same role'],
      ['Cade Cunningham', 'DET', '29.5', 'moved', 'Healthy after a shortened 2025-26'],
      ['RJ Barrett', 'TOR', '26', 'moved', 'Ingram out, Kawhi Leonard in'],
      ['Jalen Duren', 'DET', '25.5', 'locked', 'Re-signed Oct 1; Stewart traded'],
      ['Amen Thompson', 'HOU', '22', 'locked', 'Five-year extension; same team']
    ];

    var COL = [
      ['Duke', '120.5', '91.0', '+29.5', '70.1', 'average'],
      ['Florida', '120.6', '93.5', '+27.1', '74.1', 'very fast'],
      ['Arizona', '118.8', '91.8', '+27.0', '74.2', 'very fast'],
      ['Virginia', '120.7', '94.0', '+26.7', '70.4', 'average'],
      ['Illinois', '122.8', '96.5', '+26.3', '72.1', 'fast'],
      ['Houston', '118.8', '93.0', '+25.8', '67.3', 'slow'],
      ['Kansas', '119.2', '93.7', '+25.5', '72.2', 'fast'],
      ['Louisville', '119.2', '94.6', '+24.6', '72.6', 'fast']
    ];

    var MID = [
      ['44', 'Grand Canyon', 'MWC', '+16.8', '73.1', 'top 50'],
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
      ['90', 'South Florida', 'Amer', '+9.2', '73.1', '']
    ];

    // EuroLeague 2026-27 after six rounds. NET is six games of this
    // season blended with each club's finish last season; RAW is the
    // six games alone. The pair is on the page deliberately -- six
    // games overstate how far apart teams are, and a reader should be
    // able to see where the restraint is being applied.
    // [team, offence, defence, net, raw net, pace, tempo]
    var EUR = [
      ['Panathinaikos', '117.4', '105.2', '+12.2', '+18.7', '74.6', 'average'],
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
      ['Baskonia', '110.3', '120.9', '−10.5', '−14.1', '77.8', 'fast']
    ];

    // ----------------------------------------------------------------
    // STATUS. Paste the output of site_data.py here each day; nothing
    // else on the page needs touching.
    //
    // Why this is on the board at all: before there is a track record,
    // results cannot be shown honestly, and a six-pick record is noise
    // presented as evidence. What CAN be shown is the work -- what was
    // looked at, what was rejected and why, and how much the ratings
    // actually know yet. All of it is dated and checkable against the
    // next slate, which an early win rate is not.
    //
    // `run: null` means no slate has been priced yet. That state is
    // deliberately shown rather than hidden: a reader who arrives
    // before the first game should see that nothing has happened,
    // not an empty panel that implies something did.
    var STATUS = {
      run: null,
      // run: { when: '20 Oct 18:40', games: 11, priced: 184,
      //        cleared: 0, taken: 0, killed: 142,
      //        gate: 'bid-ask too wide for the price' },
      maturity: { league: 'EuroLeague', own: 59, gp: 6, teams: 20,
                  reaches: '~82% by December' },
      record: { graded: 0, clvAt: 30 }
    };

    var S = STATUS;
    var runHead, runSub, runColor;
    if (S.run) {
      runHead = S.run.taken + ' of ' + S.run.priced;
      runSub = S.run.when + ' · ' + S.run.games + ' games, '
        + S.run.priced + ' markets priced, ' + S.run.cleared
        + ' cleared the gates. Most rejections: ' + S.run.gate
        + ' (' + S.run.killed + ').';
      runColor = S.run.taken > 0 ? '#FF6B2C' : '#7A8798';
    } else {
      runHead = 'Not yet';
      runSub = 'First slate prices on opening night. Every run is '
        + 'recorded here, including the ones where nothing clears — '
        + 'a quiet night is a decision, not a gap.';
      runColor = '#7A8798';
    }

    var matHead = S.maturity.own + '% this season';
    var matBar = S.maturity.own + '%';
    var matSub = S.maturity.league + ' ratings, ' + S.maturity.gp
      + ' games in across ' + S.maturity.teams + ' teams. The other '
      + (100 - S.maturity.own) + '% is last season, because six games on '
      + 'their own overstate how far apart teams are. Reaches '
      + S.maturity.reaches + '.';

    var recHead = S.record.graded + ' / ' + S.record.clvAt;
    var recSub = 'graded picks. We judge ourselves on the price we got '
      + 'versus the closing line, which settles in about '
      + S.record.clvAt + ' picks. A win rate needs nearer 200 before it '
      + 'means anything, so neither number shows until it does.';
    var recColor = S.record.graded >= S.record.clvAt ? '#FF6B2C' : '#7A8798';

    var pill = function (st) {
      var on = st === 'locked';
      return 'font-family: \'IBM Plex Mono\', monospace; font-size: 10px; letter-spacing: 1.4px; '
        + 'padding: 5px 10px; border-radius: 4px; text-transform: uppercase; '
        + (on
          ? 'color: #FF8A52; border: 1px solid #5C2D12; background: #2A1408;'
          : 'color: #96A3B4; border: 1px solid #28323F; background: #1A212B;');
    };

    var src = (tab === 'pr') ? PR : PRA;
    var rows = src.map(function (r) {
      return { name: r[0], team: r[1], line: r[2], status: r[3], note: r[4], pill: pill(r[3]) };
    });
    var crows = COL.map(function (c) {
      return { team: c[0], oe: c[1], de: c[2], net: c[3], tempo: c[4], pace: c[5] };
    });
    var mrows = MID.map(function (m) {
      return { rank: m[0], team: m[1], conf: m[2], net: m[3], tempo: m[4], tag: m[5] };
    });
    var num = function (s) { return parseFloat(String(s).replace('−', '-')); };
    var erows = EUR.map(function (e) {
      // how far last season is pulling this club off its six-game form
      var d = num(e[3]) - num(e[4]);
      var big = Math.abs(d) >= 2.5;
      return {
        team: e[0], oe: e[1], de: e[2], net: e[3], raw: e[4],
        tempo: e[5], pace: e[6],
        gap: big ? (d > 0 ? ' ▲' : ' ▼') : '',
        gapStyle: 'font-size: 11px; margin-left: 5px; color: '
          + (big ? (d > 0 ? '#3FB68B' : '#C2693C') : 'transparent')
      };
    });

    var defs = [
      { id: 'pra', kicker: 'NBA', label: 'Points + rebounds + assists' },
      { id: 'pr', kicker: 'NBA', label: 'Points + rebounds' },
      { id: 'college', kicker: 'COLLEGE', label: 'Team efficiency' },
      { id: 'mid', kicker: 'COLLEGE', label: 'Best outside the big leagues' },
      { id: 'euro', kicker: 'EUROLEAGUE', label: 'Team efficiency, in season' }
    ];
    var tabs = defs.map(function (d) {
      var on = d.id === tab;
      return {
        label: d.label,
        kicker: d.kicker,
        sel: on ? 'true' : 'false',
        pick: function () { self.setState({ tab: d.id }); },
        style: 'text-align: left; font-family: \'IBM Plex Sans\', sans-serif; font-size: 15px; '
          + 'font-weight: 600; padding: 11px 18px; border-radius: 8px; cursor: pointer; min-height: 44px; '
          + (on
            ? 'background: #FF6B2C; color: #0A0D12; border: 1px solid #FF6B2C;'
            : 'background: #11161E; color: #96A3B4; border: 1px solid #28323F;')
      };
    });

    var foots = {
      pra: 'Each player’s own typical points+rebounds+assists across his last three seasons — roughly where a book would set him. Not a projection for any given night. LOCKED: situation unchanged. MOVED: something a three-year average would miss.',
      pr: 'Each player’s own typical points+rebounds across his last three seasons — roughly where a book would set him. Not a projection for any given night. LOCKED: situation unchanged. MOVED: something a three-year average would miss.',
      college: 'Adjusted efficiency per 100 possessions, opponent-adjusted. Preseason projections — these move once games are played. A high rating means a team is good, not that the market has it wrong.',
      mid: 'Teams outside the ACC, Big Ten, Big 12, SEC, Big East and Pac-12, ranked by where they sit NATIONALLY — that column is the point. None crack the top 43 this preseason. Ratings ignore conference; prices don’t, and non-conference season is where that gap would show.',
      euro: 'Points scored and allowed per 100 possessions, 2026-27, after six rounds. NET is what we price: these six games blended 58/42 with each club’s finish last season, because six games on their own overstate how far apart teams are. 6 GAMES SAY is that unblended figure, with an arrow where the two disagree by more than 2.5 points — Panathinaikos and Fenerbahçe are being held back off a hot start, Žalgiris and Baskonia lifted off a cold one. Beşiktaş is newly promoted and has no last season to blend, so it is pulled toward the league average instead. Margins in this league carry a standard error of 11.7 points, measured out-of-sample across 310 games.'
    };

    return {
      accent: this.props.accent || '#FF6B2C',
      tabs: tabs,
      rows: rows,
      crows: crows,
      mrows: mrows,
      erows: erows,
      lineHead: (tab === 'pr') ? '3-SEASON PR' : '3-SEASON PRA',
      isNba: tab === 'pra' || tab === 'pr',
      isCollege: tab === 'college',
      isMid: tab === 'mid',
      isEuro: tab === 'euro',
      runHead: runHead, runSub: runSub, runColor: runColor,
      matHead: matHead, matSub: matSub, matBar: matBar,
      recHead: recHead, recSub: recSub, recColor: recColor,
      foot: foots[tab]
    };
  }
}
