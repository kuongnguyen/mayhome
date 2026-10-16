"""Isolated synthetic UI acceptance; never connects to mayhome or writes runtime status."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[1]
SERVER = '''import sys
from pathlib import Path
from mayhome_center import create_app
from werkzeug.serving import make_server
app=create_app({'STATUS_PATH':sys.argv[1], 'SESSION_COOKIE_SECURE':False})
server=make_server('127.0.0.1',0,app)
Path(sys.argv[2]).write_text(str(server.server_port))
server.serve_forever()
'''


def write_fixture(path, state):
    now = datetime.now(timezone.utc)
    effective = 'UNKNOWN' if state == 'PARTIAL' else state
    docker_status = 'HEALTHY' if state == 'STALE' else effective
    systemd = {'status':'HEALTHY','active_state':'active','edge_connections':4,'ready_http_status':200,
               'uptime_sec':900,'code':'TUNNEL_EDGE_CONNECTED'}
    docker = {'status':docker_status,'running':True,'address':'172.18.0.2',
              'edge_connections':4 if docker_status=='HEALTHY' else 0,
              'ready_http_status':200 if docker_status=='HEALTHY' else 503,
              'uptime_sec':30 if state=='WARNING' else 900,'code':'TUNNEL_EDGE_CONNECTED' if docker_status=='HEALTHY' else 'TUNNEL_EDGE_DISCONNECTED'}
    if state == 'PARTIAL':
        docker.update(edge_connections=None,ready_http_status=None,code='TUNNEL_CONTAINER_UNVERIFIED')
    monitor = {'status':'HEALTHY' if state=='STALE' else effective,
               'connectors':{'systemd':systemd,'docker':docker},
               'scope':'cloudflared_to_edge','public_route_verified':False}
    payload = {'schema_version':1,'generated_at':(now-timedelta(seconds=120) if state=='STALE' else now).isoformat(),
               'data':{'post_boot':monitor,'host':{'name':'SIMULATED-TEST-DATA'},
                       'containers':[{'name':'cloudflared','running':True}]},'errors':[]}
    temporary=path.with_suffix('.new');temporary.write_text(json.dumps(payload));temporary.replace(path)


def main():
    results=[]
    failures=[]
    with tempfile.TemporaryDirectory(prefix='mhc-browser-fixture-') as directory:
        status=Path(directory)/'status.json';portfile=Path(directory)/'port'
        write_fixture(status,'CRITICAL')
        token=secrets.token_urlsafe(32)
        environment=os.environ.copy()
        environment.update(MHC_AUTH_TOKEN=token,MHC_SESSION_KEY=secrets.token_hex(32))
        process=subprocess.Popen([str(REPO/'.venv/bin/python'),'-c',SERVER,str(status),str(portfile)],
                                 cwd=REPO,env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while not portfile.exists():
                if process.poll() is not None or time.monotonic()>deadline:
                    raise RuntimeError('Isolated fixture server did not start')
                time.sleep(.05)
            base='http://127.0.0.1:'+portfile.read_text()
            with sync_playwright() as runner:
                browser=runner.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                page=browser.new_page()
                page.on('pageerror',lambda error:failures.append(str(error)))
                page.goto(base+'/login');page.locator('#token').fill(token)
                page.get_by_role('button',name='Đăng nhập').click();page.wait_for_url(base+'/')
                for state in ('WARNING','CRITICAL','HEALTHY','STALE','PARTIAL'):
                    write_fixture(status,state)
                    for width in (360,390,768,1440):
                        page.set_viewport_size({'width':width,'height':900})
                        page.goto(base+'/');page.wait_for_load_state('networkidle')
                        panel=page.locator('section.panel').filter(has=page.get_by_role('heading',name='Post-Boot Health Monitor'))
                        expected = 'UNKNOWN' if state=='PARTIAL' else state
                        assert page.locator('#tunnel-status').inner_text()==expected
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        api=page.request.get(base+'/api/v1/host/post-boot').json()
                        assert api['data']['status']==expected
                        if state=='CRITICAL':
                            assert api['data']['connectors']['systemd']['status']=='HEALTHY'
                            assert api['data']['connectors']['docker']['running'] is True
                            assert api['data']['connectors']['docker']['edge_connections']==0
                            assert page.locator('[data-connector="docker"] [data-tunnel-status]').inner_text()=='CRITICAL'
                            assert page.locator('[data-connector="systemd"] [data-tunnel-status]').inner_text()=='HEALTHY'
                        if state=='STALE':
                            assert all(row['status']=='STALE' for row in api['data']['connectors'].values())
                        page.locator('h1').evaluate("node => node.textContent = 'DỮ LIỆU GIẢ LẬP · Post-Boot Monitor'")
                        page.screenshot(path=str(REPO/f'docs/evidence/fixture-post-boot-{state.lower()}-{width}.png'),full_page=True)
                        results.append({'scenario':state,'width':width,'api_status':expected,'overflow':False})
                # Browser loses network while displaying a previously healthy sample.
                write_fixture(status,'HEALTHY')
                page.clock.install();page.goto(base+'/')
                page.route('**/api/v1/overview',lambda route:route.abort())
                page.clock.fast_forward(31000)
                expect(page.locator('.freshness .status')).to_have_text('UNKNOWN')
                assert 'Không thể cập nhật' in page.locator('.freshness').inner_text()
                assert page.locator('#tunnel-status').inner_text()=='UNKNOWN'
                assert page.locator('[data-tunnel-status]').all_text_contents()==['UNKNOWN','UNKNOWN']
                results.append({'scenario':'network_failure','display':'UNKNOWN','tunnel_display':'UNKNOWN'})
                page.unroute('**/api/v1/overview')
                page.clock.fast_forward(61000)
                expect(page.locator('#tunnel-status')).to_have_text('HEALTHY')
                results.append({'scenario':'network_recovered','tunnel_display':'HEALTHY'})
                page.context.set_offline(True)
                expect(page.locator('#tunnel-status')).to_have_text('UNKNOWN')
                results.append({'scenario':'browser_offline','tunnel_display':'UNKNOWN'})
                page.context.set_offline(False)
                page.clock.fast_forward(31000)
                expect(page.locator('#tunnel-status')).to_have_text('HEALTHY')
                results.append({'scenario':'browser_online','tunnel_display':'HEALTHY'})
                assert not failures,failures
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    print(json.dumps({'data_source':'SYNTHETIC FIXTURES, NOT PRODUCTION','checks':results,'javascript_errors':failures},indent=2))

if __name__=='__main__':main()
