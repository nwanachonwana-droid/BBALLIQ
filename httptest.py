from playwright.sync_api import sync_playwright
errs=[]
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':1280,'height':1000})
    pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    pg.on('pageerror', lambda e: errs.append('PAGEERROR: '+str(e)))
    pg.goto('http://127.0.0.1:8911/index.html'); pg.wait_for_timeout(1200)
    print('runHead :', pg.inner_text('#runHead'))
    print('runSub  :', pg.inner_text('#runSub')[:90])
    print('matHead :', pg.inner_text('#matHead'), ' bar', pg.eval_on_selector('#matBar','e=>e.style.width'))
    print('recHead :', pg.inner_text('#recHead'))
    real = [e for e in errs if 'TUNNEL' not in e and 'fonts' not in e.lower()]
    print('\nnon-font console errors:', real or 'NONE')
    pg.screenshot(path='shot-http.png', full_page=True)
    b.close()
