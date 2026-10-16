"""Optional cloud UI smoke: requires Playwright, Chromium and an isolated test server.
MHC_TEST_TOKEN_FILE must refer to an ephemeral test credential, never production.
"""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

base = os.environ.get('MHC_TEST_BASE', 'http://127.0.0.1:5010')
with sync_playwright() as runner:
    browser = runner.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    context = browser.new_context()
    page = context.new_page()
    failures=[]
    page.on('pageerror', lambda error: failures.append(str(error)))
    page.goto(base+'/login')
    page.locator('#token').fill(Path(os.environ['MHC_TEST_TOKEN_FILE']).read_text())
    page.get_by_role('button',name='Đăng nhập').click()
    page.wait_for_url(base+'/')
    results=[]
    for width,height in [(360,800),(390,844),(768,1024),(1440,1000)]:
        page.set_viewport_size({'width':width,'height':height})
        for route in ['/', '/backups', '/logs', '/about']:
            page.goto(base+route)
            page.wait_for_load_state('networkidle')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), (width,route)
            assert page.locator('h1').is_visible()
            assert page.locator('.freshness .status').inner_text()=='UNKNOWN'
            results.append({'viewport':width,'route':route,'overflow':False})
        page.goto(base+'/')
        page.screenshot(path=f'docs/evidence/dashboard-{width}.png',full_page=True)
    assert not failures, failures
    print(json.dumps({'checks':results,'javascript_errors':failures},indent=2))
    browser.close()
