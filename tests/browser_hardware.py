"""Synthetic hardware scenarios served by the real authenticated MAYHOME app."""
from datetime import datetime,timedelta,timezone
import json,os,secrets,subprocess,tempfile,time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from browser_post_boot import SERVER,REPO,write_fixture


def fixture(path,state):
    write_fixture(path,'HEALTHY')
    payload=json.loads(path.read_text());now=datetime.now(timezone.utc)
    current={'cpu_percent':23.5,'cores':8,'frequency_mhz':2400,'memory_percent':53.2,'memory_used':8_000_000_000,
             'memory_total':16_000_000_000,'memory_available':8_000_000_000,'swap_used':0,'temperature_c':44,
             'disk_read_bps':2048,'disk_write_bps':1024,'network_down_bps':370_000,'network_up_bps':1200,'uptime_seconds':262800}
    history=[]
    for index in range(30):
        history.append({'timestamp':(now-timedelta(seconds=(30-index)*30)).isoformat(),**current,
                        'cpu_percent':20+(index%5)*2,'network_down_bps':300_000+index*1000})
    if state=='missing_sensor':current['temperature_c']=None
    if state=='first_sample':
        for key in ('cpu_percent','disk_read_bps','disk_write_bps','network_down_bps','network_up_bps'):current[key]=None
        history=[]
    history.append({'timestamp':now.isoformat(),**current})
    if state=='missing_sensor':
        for row in history:row['temperature_c']=None
    payload['data']['hardware']={'version':1,'observed_at':now.isoformat(),'current':current,'history':history,
                                 'sample_interval_seconds':30,'errors':[]}
    if state=='stale':payload['generated_at']=(now-timedelta(seconds=200)).isoformat()
    path.write_text(json.dumps(payload))


def main():
    results=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='mhc-hardware-fixture-') as directory:
        status=Path(directory)/'status.json';port=Path(directory)/'port';fixture(status,'normal')
        token=secrets.token_urlsafe(32);env=os.environ.copy();env.update(MHC_AUTH_TOKEN=token,MHC_SESSION_KEY=secrets.token_hex(32))
        process=subprocess.Popen([str(REPO/'.venv/bin/python'),'-c',SERVER,str(status),str(port)],cwd=REPO,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not port.exists():
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Server unavailable')
                time.sleep(.05)
            base='http://127.0.0.1:'+port.read_text()
            with sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                page=browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base+'/login');page.locator('#token').fill(token);page.get_by_role('button',name='Đăng nhập').click();page.wait_for_url(base+'/')
                for state in ('normal','missing_sensor','first_sample','stale'):
                    fixture(status,state)
                    for width in (360,390,768,1440):
                        page.set_viewport_size({'width':width,'height':1000});page.goto(base+'/');page.wait_for_load_state('networkidle')
                        expect(page.locator('.hardware-card')).to_have_count(6)
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        assert page.locator('#snapshot-inventory').count()==0
                        if state=='normal':
                            assert page.locator('.hardware-chart polyline').count()>=5
                            assert page.locator('.hardware-grid').evaluate("node => getComputedStyle(node).gridTemplateColumns.split(' ').length")== (3 if width==1440 else 1 if width==360 else 2)
                        if state in ('missing_sensor','first_sample'):
                            key='temperature_c' if state=='missing_sensor' else 'cpu_percent'
                            expect(page.locator(f'[data-hardware="{key}"] [data-hardware-value]')).to_have_text('—')
                            assert page.locator(f'[data-hardware="{key}"] polyline').count()==0
                        if state=='stale':expect(page.locator('.section-label [data-live-status]')).to_have_text('STALE')
                        if state=='normal' and width in (360,1440):
                            page.locator('h1').evaluate("node => node.textContent='GIẢ LẬP · Tổng quan phần cứng'")
                            page.screenshot(path=str(REPO/f'docs/evidence/fixture-hardware-{width}.png'),full_page=True)
                        results.append({'scenario':state,'width':width,'overflow':False})
                fixture(status,'normal');page.clock.install();page.goto(base+'/')
                page.context.set_offline(True)
                expect(page.locator('[data-hardware-value]').first).to_have_text('—')
                assert page.locator('.hardware-chart.historical').count()==5
                page.context.set_offline(False);page.clock.fast_forward(31000)
                expect(page.locator('[data-hardware-value]').first).to_have_text('24')
                results.append({'scenario':'offline_recovery','status':'PASS'})
                page.locator('.sidebar nav a[href="/backups"]').click();page.wait_for_url(base+'/backups')
                assert page.locator('.hardware-card').count()==0 and page.locator('#snapshot-inventory').count()==1
                results.append({'scenario':'backup_separate','status':'PASS'})
                assert not errors,errors
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    print(json.dumps({'data_source':'SYNTHETIC FIXTURES, NOT PRODUCTION','checks':results,'javascript_errors':errors},indent=2))

if __name__=='__main__':main()
