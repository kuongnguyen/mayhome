import json
import re
import sys
from datetime import timedelta
import pytest
from mayhome_center import create_app
from mayhome_center.collector import inventory, parse_journal, unit_status, run, ProbeError
from mayhome_center.domain import protection, utcnow
from mayhome_center.store import load_status

@pytest.fixture
def status_file(tmp_path):
    path = tmp_path / 'status.json'
    path.write_text(json.dumps({'schema_version':1,'generated_at':utcnow().isoformat(),'data':{
        'snapshots':[{'id':'20261015-210345','type':'checkpoint','completeness':'complete','integrity_level':'present',
        'protected':True,'protection_reasons':['fixed_checkpoint']}], 'jobs':[], 'logs':[], 'storage':[], 'backup':{}},'errors':[]}))
    return path

@pytest.fixture
def client(status_file):
    app = create_app({'TESTING':True,'SECRET_KEY':'testing-only','AUTH_TOKEN':'test-token',
                      'SESSION_COOKIE_SECURE':False,'STATUS_PATH':str(status_file)})
    return app.test_client()

def login(client):
    client.get('/login')
    with client.session_transaction() as session:
        csrf = session['csrf']
    return client.post('/login',data={'csrf':csrf,'token':'test-token'})

def test_auth_and_headers(client):
    assert client.get('/healthz').json == {'alive':True}
    assert client.get('/api/v1/overview').status_code == 401
    assert client.get('/api/v1/overview',headers={'Cf-Access-Authenticated-User-Email':'admin@example.com','Cf-Access-Jwt-Assertion':'forged'}).status_code == 401
    assert client.post('/login',data={'token':'test-token'}).status_code == 401
    assert login(client).status_code == 302
    response = client.get('/api/v1/overview')
    assert response.status_code == 200
    assert response.json['data']['status'] == 'UNKNOWN'
    assert response.headers['Cache-Control'] == 'no-store'
    assert "frame-ancestors 'none'" in response.headers['Content-Security-Policy']
    assert client.get('/',headers={'Host':'evil.example'}).status_code == 400

@pytest.mark.parametrize('method',['post','put','delete','patch'])
def test_no_mutations(client,method):
    login(client)
    assert getattr(client,method)('/api/v1/backups/status').status_code == 405
    assert getattr(client,method)('/api/v1/backups/run').status_code == 405

@pytest.mark.parametrize('page',['/','/backups','/logs','/about'])
def test_pages(client,page):
    login(client)
    response=client.get(page)
    assert response.status_code == 200
    assert 'MAYHOME' in response.text
    assert 'progress' not in response.text

def test_pagination_and_ids(client):
    login(client)
    assert client.get('/api/v1/backups/snapshots?limit=0').status_code == 400
    assert client.get('/api/v1/backups/snapshots?cursor=-1').status_code == 400
    assert client.get('/api/v1/backups/snapshots?limit=201').status_code == 400
    assert client.get('/api/v1/logs?source=secret').status_code == 400
    assert client.get('/api/v1/backups/snapshots/20261015-210345').json['data']['integrity_level'] == 'present'
    assert client.get('/api/v1/backups/snapshots/../../config.php').status_code == 404
    assert client.get('/api/v1/capabilities').json['data']['delete'] is False

def test_store_failures(status_file,tmp_path):
    assert load_status(str(status_file)).freshness == 'fresh'
    payload=json.loads(status_file.read_text())
    payload['generated_at']=(utcnow()-timedelta(seconds=100)).isoformat()
    status_file.write_text(json.dumps(payload))
    assert load_status(str(status_file)).freshness == 'stale'
    payload['generated_at']=(utcnow()+timedelta(hours=1)).isoformat()
    status_file.write_text(json.dumps(payload))
    assert load_status(str(status_file)).freshness == 'unavailable'
    alias=tmp_path/'alias';alias.symlink_to(status_file)
    assert load_status(str(alias)).freshness == 'unavailable'
    status_file.write_text('{')
    assert load_status(str(status_file)).freshness == 'unavailable'
    status_file.write_text('x'*1048577)
    assert load_status(str(status_file)).freshness == 'unavailable'

def test_missing_collector(client,status_file):
    login(client);status_file.unlink()
    assert client.get('/readyz').status_code == 503
    assert client.get('/api/v1/overview').status_code == 503
    assert client.get('/').status_code == 200

def test_semantics():
    assert protection({'storage':[{'status':'CRITICAL'}]},'fresh') == 'CRITICAL'
    assert protection({'containers':[{'running':False}]},'fresh') == 'CRITICAL'
    assert protection({'backup':{'marker_present':True}},'fresh') == 'CRITICAL'
    assert protection({'backup':{'active_state':'active'}},'fresh') == 'RUNNING'
    assert protection({'backup':{'active_state':'active'}},'stale') == 'STALE'
    assert protection({},'fresh') == 'UNKNOWN'

def test_inventory(tmp_path):
    snapshots=tmp_path/'snapshots';snapshots.mkdir()
    database=tmp_path/'database';database.mkdir()
    (database/'nextcloud-20261015-174303.sql.gz').touch()
    for name in ['20261015-210345','20261016-020000','20261017-020000','.staging-test']:
        path=snapshots/name;path.mkdir();(path/'COMPLETE').touch()
    (snapshots/'20261018-020000').symlink_to(tmp_path,target_is_directory=True)
    result=inventory(tmp_path)
    assert len(result)==5
    assert all(r['integrity_level']!='db_checksum_verified' for r in result)
    assert next(r for r in result if r['type']=='staging')['completeness']=='incomplete'
    assert next(r for r in result if r['id']=='20261015-210345')['protected']
    assert next(r for r in result if r['id']=='baseline')['protected']
    assert sum('latest_two_completed' in r['protection_reasons'] for r in result)==2
    with pytest.raises(ProbeError):inventory(tmp_path,max_entries=1)
    alias=tmp_path.parent/(tmp_path.name+'-alias');alias.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(OSError):inventory(alias)

def test_journal_redacts_and_truncates():
    raw=json.dumps({'MESSAGE':'NC-BACKUP-RC1.3 BACKUP PASS token=SECRET /private/person.jpg', '__REALTIME_TIMESTAMP':'1792108800000000'})
    rows=parse_journal(raw+'\n{truncated')
    assert len(rows)==1
    assert 'SECRET' not in str(rows) and 'person.jpg' not in str(rows)
    assert parse_journal(json.dumps({'MESSAGE':'unrelated SECRET'}))==[]
    assert len(parse_journal('\n'.join([raw]*300)))==200

def test_command_limits():
    with pytest.raises(ProbeError):unit_status('sshd.service')
    with pytest.raises(ProbeError):run([sys.executable,'-c','print("x"*10000)'],max_bytes=100)
    with pytest.raises(ProbeError):run([sys.executable,'-c','import time;time.sleep(10)'],timeout=.05)
    assert run([sys.executable,'-c','print("ok")']).strip()=='ok'

def test_login_rate_limit(client):
    for _ in range(10):
        assert client.post('/login',data={'token':'wrong'}).status_code==401
    assert client.post('/login',data={'token':'wrong'}).status_code==429

def test_mount_uuid_and_readonly(monkeypatch):
    from mayhome_center import collector
    monkeypatch.setattr(collector,'run',lambda argv: json.dumps({'filesystems':[{
        'target':'/mnt/nextcloud_backup','uuid':'wrong','fstype':'ext4','options':'rw'}]}))
    assert collector.mount_status('/mnt/nextcloud_backup','expected')['status']=='CRITICAL'
    monkeypatch.setattr(collector,'run',lambda argv: json.dumps({'filesystems':[{
        'target':'/mnt/nextcloud_backup','uuid':'expected','fstype':'ext4','options':'ro'}]}))
    assert collector.mount_status('/mnt/nextcloud_backup','expected')['status']=='CRITICAL'

def test_collect_absent_tools(monkeypatch):
    from mayhome_center import collector
    def missing(*args,**kwargs):raise FileNotFoundError()
    monkeypatch.setattr(collector,'run',missing)
    data=collector.collect()
    assert data['data']['snapshots']==[]
    assert data['data']['backup']['maintenance'] is None
    assert all(c['running'] is None for c in data['data']['containers'])
    assert protection(data['data'],'fresh')=='CRITICAL'
    assert 'DOCKER_UNAVAILABLE' in [e['code'] for e in data['errors']]

def test_corrupt_shapes(status_file):
    for invalid in [[],None,{'schema_version':1,'generated_at':utcnow().isoformat(),'data':{'snapshots':[None]},'errors':[]}]:
        status_file.write_text(json.dumps(invalid))
        assert load_status(str(status_file)).freshness=='unavailable'

def test_publisher_refuses_user_owned_output(tmp_path,monkeypatch):
    import os
    from mayhome_center import collector
    if os.getuid()==0:pytest.skip('requires unprivileged execution')
    monkeypatch.setattr(collector,'OUTPUT',tmp_path/'status.json')
    with pytest.raises(ProbeError):collector.publish({'data':{}})
    assert not (tmp_path/'status.json').exists()

def test_configuration_requires_stable_session_key(monkeypatch):
    monkeypatch.setenv('MHC_AUTH_TOKEN','test')
    monkeypatch.delenv('MHC_SESSION_KEY',raising=False)
    with pytest.raises(RuntimeError):create_app()

def test_unicode_login_rejected_without_server_error(client):
    client.get('/login')
    assert client.post('/login',data={'csrf':'khóa','token':'bí mật'}).status_code==401
    with client.session_transaction() as session:csrf=session['csrf']
    assert client.post('/login',data={'csrf':csrf,'token':'bí mật'}).status_code==401
