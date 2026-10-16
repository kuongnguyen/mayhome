"""PiCC-inspired navigation/theme QA against isolated synthetic MAYHOME data."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from playwright.sync_api import sync_playwright, expect
from browser_post_boot import SERVER, REPO, write_fixture


def main():
    results=[]; errors=[]
    with tempfile.TemporaryDirectory(prefix='mhc-design-fixture-') as directory:
        status=Path(directory)/'status.json'; port=Path(directory)/'port'
        write_fixture(status,'HEALTHY')
        token=secrets.token_urlsafe(32)
        environment=os.environ.copy();environment.update(MHC_AUTH_TOKEN=token,MHC_SESSION_KEY=secrets.token_hex(32))
        process=subprocess.Popen([str(REPO/'.venv/bin/python'),'-c',SERVER,str(status),str(port)],cwd=REPO,env=environment,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not port.exists():
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Fixture server unavailable')
                time.sleep(.05)
            base='http://127.0.0.1:'+port.read_text()
            with sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                for scheme in ('light','dark'):
                    context=browser.new_context(color_scheme=scheme)
                    page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                    for width in (360,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        page.goto(base+'/login')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        page.locator('h1').evaluate("node => node.textContent = 'GIẢ LẬP · Đăng nhập MAYHOME'")
                        page.screenshot(path=str(REPO/f'docs/evidence/fixture-picc-login-{scheme}-{width}.png'),full_page=True)
                        results.append({'page':'/login','scheme':scheme,'width':width,'overflow':False})
                    page.locator('#token').fill(token);page.get_by_role('button',name='Đăng nhập').click()
                    page.wait_for_url(base+'/')
                    for width in (360,390,768,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        for route in ('/','/backups','/logs','/about'):
                            page.goto(base+route);page.wait_for_load_state('networkidle')
                            expect(page.locator('.sidebar nav a[aria-current]')).to_have_attribute('href',route)
                            assert page.locator('h1').count()==1
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                            assert page.locator('.brand-mark svg').count()==1
                            # Every supported page must remain reachable using the real navigation.
                            for target in ('/','/backups','/logs','/about'):
                                assert page.locator(f'.sidebar nav a[href="{target}"]').count()==1
                            results.append({'page':route,'scheme':scheme,'width':width,'overflow':False})
                            if route=='/' and width in (360,1440):
                                page.locator('h1').evaluate("node => node.textContent = 'DỮ LIỆU GIẢ LẬP · MAYHOME'")
                                page.screenshot(path=str(REPO/f'docs/evidence/fixture-picc-overview-{scheme}-{width}.png'),full_page=True)
                    # Keyboard users can skip the navigation to the content region.
                    page.goto(base+'/');page.keyboard.press('Tab')
                    expect(page.locator('.skip')).to_be_focused()
                    page.keyboard.press('Enter')
                    page.wait_for_url(base+'/#content')
                    assert page.url.endswith('#content')
                    results.append({'scenario':'keyboard_skip','scheme':scheme,'status':'PASS'})
                    context.close()
                assert not errors,errors
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    print(json.dumps({'data_source':'SYNTHETIC FIXTURES, NOT PRODUCTION','checks':results,'javascript_errors':errors},indent=2))

if __name__=='__main__':main()
