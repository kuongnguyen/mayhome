"""Backup Manager E2E: synthetic metadata only; no production filesystem/SQL access."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from playwright.sync_api import expect, sync_playwright
from browser_post_boot import SERVER, REPO


def fixture(path, scenario):
    now=datetime.now(timezone.utc)
    rows=[{'id':'20261015-210345','type':'checkpoint','completeness':'complete','protected':True,
           'protection_reasons':['fixed_checkpoint','latest_two_completed'],'integrity_level':'structure_valid',
           'structure_checks':{'COMPLETE':True,'database/nextcloud.sql.gz':True,'manifests/database.sha256':True},
           'db_checksum_manifest_format_valid':True,'db_checksum_verified':False,'metadata_errors':[]},
          {'id':'.staging-fixture','type':'staging','completeness':'incomplete','protected':False,
           'protection_reasons':[],'integrity_level':'unknown','metadata_errors':[]},
          {'id':'baseline','type':'baseline','completeness':'unknown','protected':True,
           'protection_reasons':['baseline'],'integrity_level':'present','metadata_errors':[]}]
    jobs=[{'id':'fixture-one','start_at':'2026-10-16T00:00:00+00:00','finish_at':'2026-10-16T00:01:05.600000+00:00',
           'result':'success','duration_sec':65.6,'stage':'Commit','confidence':'complete','engine':'RC1.3','evidence_code':'JOB_LIFECYCLE_OBSERVED'},
          {'id':'fixture-two','start_at':None,'finish_at':'2026-10-15T00:01:00+00:00','result':'reported_success',
           'duration_sec':None,'stage':'Unknown','confidence':'partial','engine':'RC1.2','evidence_code':'JOB_START_OR_ID_UNKNOWN'},
          {'id':'fixture-three','start_at':now.isoformat(),'finish_at':None,'result':'unknown','duration_sec':None,
           'stage':'Pre-sync','confidence':'partial','engine':'RC1.3','evidence_code':'JOB_LIFECYCLE_INCOMPLETE'}]
    data={'jobs':jobs,'snapshots':rows,'backup':{'stage':'Unknown','maintenance':False,'marker_present':False},
          'inventory':{'known':True,'count':3,'complete_count':1,'staging_count':1,'protected_count':2,'generated_at':now.isoformat(),'cached':True},
          'diagnostics':{'note':'DỮ LIỆU GIẢ LẬP — chưa phải nghiệm thu production.','checks':[
              {'code':'SOURCE_MOUNT','status':'PASS','message':'Mount UUID/RW fixture.'},
              {'code':'CHECKSUM','status':'UNKNOWN','message':'Chưa có bằng chứng hash/gzip.'}]}}
    if scenario=='missing':
        data.update(jobs=[],snapshots=[],inventory={'known':False,'count':None})
    generated=(now-timedelta(seconds=120) if scenario=='stale' else now).isoformat()
    path.write_text(json.dumps({'schema_version':1,'generated_at':generated,'data':data,'errors':[]}))


def main():
    results=[];errors=[]
    with tempfile.TemporaryDirectory(prefix='mhc-backup-fixture-') as directory:
        status=Path(directory)/'status.json';port=Path(directory)/'port'
        fixture(status,'normal')
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
                page=browser.new_page();page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto(base+'/login');page.locator('#token').fill(token);page.get_by_role('button',name='Đăng nhập').click()
                page.wait_for_url(base+'/')
                for scenario in ('normal','missing','stale'):
                    fixture(status,scenario)
                    for width in (360,390,768,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        page.goto(base+'/backups');page.wait_for_load_state('networkidle')
                        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                        expect(page.get_by_role('heading',name='Lịch sử chạy backup')).to_be_visible()
                        expect(page.locator('h1')).to_have_text('Quản lý backup')
                        expect(page.locator('#evidence-checks')).to_have_count(1)
                        assert page.locator('#evidence-checks').evaluate("node => !node.closest('.page-heading') && node.getBoundingClientRect().top > document.querySelector('#snapshot-inventory').getBoundingClientRect().bottom")
                        if scenario=='normal':
                            assert '65.6 giây' in page.locator('#job-history').inner_text()
                            assert 'Engine báo PASS · thiếu bằng chứng phiên' in page.locator('#job-history').inner_text()
                            page.locator('.snapshot details').first.locator('summary').click()
                            assert 'không đồng nghĩa đã tính hash SQL' in page.locator('.snapshot details').first.inner_text()
                        if scenario=='stale':
                            assert set(page.locator('[data-live-status]').all_text_contents())=={'STALE'}
                        page.locator('h1').evaluate("node => node.textContent = 'DỮ LIỆU GIẢ LẬP · Backup Manager'")
                        if width in (360,1440):
                            page.screenshot(path=str(REPO/f'docs/evidence/fixture-backups-{scenario}-{width}.png'),full_page=True)
                        results.append({'scenario':scenario,'width':width,'overflow':False})
                fixture(status,'normal');page.clock.install();page.goto(base+'/backups')
                page.context.set_offline(True)
                expect(page.locator('[data-live-status]').first).to_have_text('UNKNOWN')
                page.context.set_offline(False);page.clock.fast_forward(31000)
                expect(page.locator('[data-live-status]').first).to_have_text('PASS')
                results.append({'scenario':'offline_recovery','status':'PASS'})
                # When reading a detail drawer, do not retain green evidence once the API says STALE.
                page.locator('.snapshot details').first.locator('summary').click()
                fixture(status,'stale');page.clock.fast_forward(31000)
                expect(page.locator('[data-live-status]').first).to_have_text('STALE')
                results.append({'scenario':'stale_while_reading_details','status':'STALE'})
                fixture(status,'normal');page.clock.fast_forward(31000)
                expect(page.locator('[data-live-status]').first).to_have_text('UNKNOWN')
                assert page.locator('.snapshot details').first.get_attribute('open') is not None
                page.locator('.snapshot details').first.locator('summary').click()
                page.locator('body').click(position={'x':5,'y':5})
                page.clock.fast_forward(31000)
                expect(page.locator('[data-live-status]').first).to_have_text('PASS')
                results.append({'scenario':'deferred_refresh_after_details_close','status':'PASS'})
                assert not errors, errors
                browser.close()
        finally:
            process.terminate()
            try:process.wait(timeout=3)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    print(json.dumps({'data_source':'SYNTHETIC FIXTURES, NOT PRODUCTION','checks':results,'javascript_errors':errors},indent=2))

if __name__=='__main__':main()
