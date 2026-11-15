from playwright.sync_api import sync_playwright
import os, json, sys

HERE = os.path.abspath('.')
fails = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={'width':1280,'height':1000})
    errs = []
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))

    # ---- the board ----
    pg.goto(f'file://{HERE}/index.html')
    pg.wait_for_timeout(900)
    tabs = pg.query_selector_all('#tabbar button')
    print(f'tabs rendered: {len(tabs)}')
    if len(tabs) != 5: fails.append(f'expected 5 tabs, got {len(tabs)}')

    def rowcount(sel):
        return len(pg.query_selector_all(sel + ' tr'))

    # default tab = PRA
    n = rowcount('#tb-rows')
    print(f'PRA rows: {n}')
    if n != 10: fails.append(f'PRA rows {n} != 10')
    vis = pg.eval_on_selector('#panel-isNba', 'e => !e.hidden')
    print(f'NBA panel visible on load: {vis}')
    if not vis: fails.append('NBA panel hidden on load')

    # click through every tab
    for i, label in enumerate(['pra','pr','college','mid','euro']):
        pg.query_selector_all('#tabbar button')[i].click()
        pg.wait_for_timeout(250)
        shown = pg.evaluate("""() => {
            const ids=['panel-isNba','panel-isCollege','panel-isMid','panel-isEuro'];
            return ids.filter(id => { const e=document.getElementById(id);
                                      return e && !e.hidden; });
        }""")
        foot = pg.inner_text('#foot')
        print(f'  tab {label:8} -> visible {shown}  foot {len(foot)} chars')
        if len(shown) != 1: fails.append(f'tab {label}: {len(shown)} panels visible')
        if len(foot) < 40: fails.append(f'tab {label}: footnote too short')

    # euro table content
    pg.query_selector_all('#tabbar button')[4].click()
    pg.wait_for_timeout(250)
    n = rowcount('#tb-erows')
    first = pg.inner_text('#tb-erows tr:first-child')
    print(f'EuroLeague rows: {n}')
    print(f'  first row: {first.split(chr(10))[:6]}')
    if n != 20: fails.append(f'euro rows {n} != 20')
    if 'Panathinaikos' not in first: fails.append('euro first row wrong')

    # status strip fed from data/status.json
    print(f'status: runHead="{pg.inner_text("#runHead")}" '
          f'matHead="{pg.inner_text("#matHead")}" '
          f'recHead="{pg.inner_text("#recHead")}"')
    if not pg.inner_text('#matHead').strip(): fails.append('matHead empty')
    if not pg.inner_text('#runHead').strip(): fails.append('runHead empty')
    bar = pg.eval_on_selector('#matBar', 'e => e.style.width')
    print(f'  maturity bar width: {bar}')
    if bar in ('', '0%'): fails.append('maturity bar not set')

    pg.screenshot(path='shot-board.png', full_page=True)

    # ---- the three article pages ----
    for f in ('research.html','minutes.html','misses.html'):
        pg.goto(f'file://{HERE}/{f}')
        pg.wait_for_timeout(400)
        h = pg.evaluate('document.body.scrollHeight')
        txt = len(pg.inner_text('body'))
        leftover = pg.evaluate("() => document.body.innerHTML.includes('{{')")
        print(f'{f:16} height {h}px  text {txt} chars  holes-left {leftover}')
        if h < 600: fails.append(f'{f} too short ({h}px)')
        if leftover: fails.append(f'{f} has unconverted holes')
        pg.screenshot(path=f'shot-{f.replace(".html","")}.png', full_page=True)

    # mobile check on the board
    pg.set_viewport_size({'width':390,'height':844})
    pg.goto(f'file://{HERE}/index.html'); pg.wait_for_timeout(700)
    sw = pg.evaluate('document.documentElement.scrollWidth')
    print(f'\nphone 390px -> scrollWidth {sw}')
    if sw > 400: fails.append(f'horizontal overflow on phone: {sw}px')

    print(f'\nconsole errors: {errs if errs else "none"}')
    if errs: fails.append(f'{len(errs)} console errors')
    b.close()

print()
if fails:
    print('FAILURES:'); [print('  -', f) for f in fails]; sys.exit(1)
print('ALL CHECKS PASS')
