"""Local UI/API scenarios: synthetic alerts and a temporary persistent database."""
from datetime import datetime,timedelta,timezone
import json,os,secrets,sqlite3,subprocess,tempfile,time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
from browser_hardware import fixture
from browser_post_boot import REPO

SERVER='''import sys
from pathlib import Path
from mayhome_center import create_app
from mayhome_center import history_store
from werkzeug.serving import make_server
# Only this isolated test bypasses production root-ownership checks for its temp directory.
history_store.trusted=lambda *args:None
app=create_app({'STATUS_PATH':sys.argv[1],'HISTORY_PATH':sys.argv[3],'SESSION_COOKIE_SECURE':False})
s=make_server('127.0.0.1',0,app);Path(sys.argv[2]).write_text(str(s.server_port));s.serve_forever()
'''


def main():
    checks=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='mhc-alert-history-fixture-') as directory:
        status=Path(directory)/'status.json';port=Path(directory)/'port';db=Path(directory)/'history.sqlite3'
        fixture(status,'normal');data=json.loads(status.read_text());now=datetime.now(timezone.utc)
        data['data']['alerts']={'items':[{'id':code,'code':code,'severity':level,'message':message,'resource':'GIẢ LẬP','first_seen':now.isoformat()} for code,level,message in [('BACKUP_LATE','WARNING','Quá lịch đã quan sát 30 phút.'),('TUNNEL_EDGE_DISCONNECTED','CRITICAL','Container Running nhưng mất Edge.'),('DISK_NEAR_FULL','WARNING','Dung lượng dưới ngưỡng cảnh báo.')]]}
        data['data']['post_boot']['status']='CRITICAL'
        data['data']['post_boot']['connectors']['docker'].update(status='CRITICAL',edge_connections=0,ready_http_status=503)
        status.write_text(json.dumps(data))
        with sqlite3.connect(db) as c:
            c.execute('PRAGMA user_version=1');c.execute('CREATE TABLE raw(stamp REAL PRIMARY KEY,boot TEXT,values_json TEXT)');c.execute('CREATE TABLE minutes(stamp INTEGER PRIMARY KEY,boot TEXT,values_json TEXT)')
            for index in range(60):
                stamp=now.timestamp()-(60-index)*60;values={'cpu_percent':10+index%10,'memory_percent':50}
                c.execute('INSERT INTO raw VALUES(?,?,?)',(stamp,'first-boot' if index<30 else 'second-boot',json.dumps(values)))
            for index in range(360):
                stamp=now.timestamp()-(360-index)*60
                c.execute('INSERT INTO minutes VALUES(?,?,?)',(int(stamp)//60*60,'fixture-boot',json.dumps({'cpu_percent':[10+index%10,1],'memory_percent':[50,1]})))
        token=secrets.token_urlsafe(32);env=os.environ.copy();env.update(MHC_AUTH_TOKEN=token,MHC_SESSION_KEY=secrets.token_hex(32))
        process=subprocess.Popen([str(REPO/'.venv/bin/python'),'-c',SERVER,str(status),str(port),str(db)],cwd=REPO,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not port.exists():
                if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('Fixture server unavailable')
                time.sleep(.05)
            base='http://127.0.0.1:'+port.read_text()
            with sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                page=browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base+'/login');page.locator('#token').fill(token);page.get_by_role('button',name='Đăng nhập').click();page.wait_for_url(base+'/')
                for width in (360,768,1440):
                    page.set_viewport_size({'width':width,'height':1000})
                    for hours in (1,6,24,168,720):
                        page.goto(base+f'/?hours={hours}');page.wait_for_load_state('networkidle')
                        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                        expect(page.locator('.local-alerts .evidence-row')).to_have_count(3)
                        expect(page.locator('.history-ranges a[aria-current]')).to_have_attribute('href',f'/?hours={hours}')
                        assert 'bền vững' in page.locator('.history-note').inner_text()
                        assert page.locator('[data-hardware="cpu_percent"] polyline').count()>0
                        checks.append({'width':width,'hours':hours,'alerts':3,'overflow':False})
                    if width in (360,1440):
                        page.locator('h1').evaluate("node=>node.textContent='GIẢ LẬP · Cảnh báo và lịch sử'")
                        page.screenshot(path=str(REPO/f'docs/evidence/fixture-alerts-history-{width}.png'),full_page=True)
                assert not errors,errors
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    print(json.dumps({'data_source':'SYNTHETIC FIXTURES, NOT PRODUCTION','checks':checks,'javascript_errors':errors},indent=2))
if __name__=='__main__':main()
