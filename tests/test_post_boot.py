import json
import subprocess
import pytest
from mayhome_center import create_app
from mayhome_center import post_boot
from mayhome_center.collector import ProbeError
from mayhome_center.domain import utcnow

@pytest.mark.parametrize('ready,count,uptime,status,code',[
    (200,4,30,'HEALTHY','TUNNEL_EDGE_CONNECTED'),
    (503,0,30,'WARNING','TUNNEL_BOOT_RECOVERING'),
    (503,0,299,'WARNING','TUNNEL_BOOT_RECOVERING'),
    (503,0,300,'CRITICAL','TUNNEL_EDGE_DISCONNECTED'),
    (200,0,900,'CRITICAL','TUNNEL_EDGE_DISCONNECTED'),
    (503,4,900,'CRITICAL','TUNNEL_EDGE_DISCONNECTED'),
    (None,None,900,'UNKNOWN','TUNNEL_PROBE_UNAVAILABLE'),
    (200,None,900,'UNKNOWN','TUNNEL_PROBE_UNAVAILABLE'),
    (302,None,900,'UNKNOWN','TUNNEL_PROBE_UNAVAILABLE'),
    (503,0,None,'CRITICAL','TUNNEL_EDGE_DISCONNECTED'),
])
def test_health_not_container_running(ready,count,uptime,status,code):
    assert post_boot.assess(ready,count,uptime)==(status,code)

@pytest.mark.parametrize('metric',['','cloudflared_tunnel_ha_connections NaN',
    'cloudflared_tunnel_ha_connections +Inf','cloudflared_tunnel_ha_connections -1',
    'cloudflared_tunnel_ha_connections .5','cloudflared_tunnel_ha_connections 1e308'])
def test_invalid_metrics(metric):
    with pytest.raises(ValueError):post_boot.connections(metric)

def test_labeled_metrics():
    assert post_boot.connections('# HELP ignored\ncloudflared_tunnel_ha_connections{tunnel_id="redacted"} 4\n')==4

def test_disabled_never_runs_commands(monkeypatch):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(42,'boot-one'))
    def forbidden(*args,**kwargs):raise AssertionError('must not probe without opt-in')
    data=post_boot.collect_systemd(forbidden,enabled=False)
    assert data['code']=='TUNNEL_MONITOR_NOT_CONFIGURED'
    assert data['uptime_sec']==42

def test_probe_bounded_fixed_no_redirect_no_secrets(monkeypatch):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'boot-one'))
    calls=[]
    def fake(argv,**limits):
        if argv[0].endswith('systemctl'): return 'ActiveState=active\nMainPID=12345'
        calls.append((argv,limits))
        return '503' if argv[-1].endswith('/ready') else 'cloudflared_tunnel_ha_connections 0\n# SECRET=private\n200'
    data=post_boot.collect_systemd(fake,enabled=True)
    assert data['status']=='CRITICAL'
    assert data['public_route_verified'] is False
    assert 'SECRET' not in json.dumps(data)
    assert len(calls)==2
    for argv,limits in calls:
        assert argv[-1].startswith('http://127.0.0.1:20241/')
        assert '--location' not in argv
        assert '--max-time' in argv and '--max-filesize' in argv
        assert limits['timeout']==3
        assert limits['max_bytes']<=65536

@pytest.mark.parametrize('exception',[ProbeError('timeout'),FileNotFoundError(),subprocess.TimeoutExpired('curl',3)])
def test_probe_failure_safe(monkeypatch,exception):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'boot-one'))
    def fail(*args,**kwargs):raise exception
    assert post_boot.collect_systemd(fail,enabled=True)['status']=='UNKNOWN'

def test_reboot_grace_resets_from_host_uptime(monkeypatch):
    def disconnected(argv,**kwargs):
        if argv[0].endswith('systemctl'): return 'ActiveState=active\nMainPID=4029'
        return '503' if argv[-1].endswith('/ready') else 'cloudflared_tunnel_ha_connections 0\n200'
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'old-boot'))
    assert post_boot.collect_systemd(disconnected,True)['status']=='CRITICAL'
    monkeypatch.setattr(post_boot,'boot_context',lambda:(10,'new-boot'))
    data=post_boot.collect_systemd(disconnected,True)
    assert data['boot_id']=='new-boot'
    assert data['status']=='WARNING'

def test_post_boot_authenticated_api_and_stale_ui(tmp_path):
    path=tmp_path/'status.json'
    payload={'schema_version':1,'generated_at':utcnow().isoformat(),'errors':[], 'data':{
        'post_boot':{'status':'CRITICAL','edge_connections':0,'ready_http_status':503,'uptime_sec':900},
        'containers':[{'name':'cloudflared','running':True}]}}
    path.write_text(json.dumps(payload))
    client=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test',
        'SESSION_COOKIE_SECURE':False,'STATUS_PATH':str(path)}).test_client()
    assert client.get('/api/v1/host/post-boot').status_code==401
    with client.session_transaction() as session:session['authenticated']=True
    assert client.get('/api/v1/host/post-boot').json['data']['status']=='CRITICAL'
    assert 'Container Running không chứng minh' in client.get('/').text
    assert client.post('/api/v1/host/post-boot').status_code==405
    from datetime import timedelta
    payload['generated_at']=(utcnow()-timedelta(seconds=100)).isoformat()
    payload['data']['post_boot']['status']='HEALTHY'
    path.write_text(json.dumps(payload))
    assert client.get('/api/v1/host/post-boot').json['data']['status']=='STALE'
    assert 'status STALE' in client.get('/').text

def test_real_http_probe_with_simulated_cloudflared(monkeypatch):
    """Exercise real curl/timeouts/HTTP interpretation, not production cloudflared."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from threading import Thread
    from mayhome_center.collector import run
    class Handler(BaseHTTPRequestHandler):
        connected=False
        def do_GET(self):
            if self.path=='/ready':
                self.send_response(200 if self.connected else 503)
                self.end_headers()
            elif self.path=='/metrics':
                self.send_response(200)
                self.end_headers()
                self.wfile.write(('cloudflared_tunnel_ha_connections '+('4' if self.connected else '0')).encode())
            else:
                self.send_response(404);self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    probe_url=f'http://127.0.0.1:{server.server_port}'
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    original_probe_endpoint=post_boot.probe_endpoint
    def probe_test_endpoint(run_command, endpoint, result):
        if endpoint == 'http://127.0.0.1:20241':
            endpoint = probe_url
        return original_probe_endpoint(run_command, endpoint, result)
    monkeypatch.setattr(post_boot,'probe_endpoint',probe_test_endpoint)
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'test-boot'))
    def fixture_run(argv, **kwargs):
        if argv[0].endswith('systemctl'): return 'ActiveState=active\nMainPID=4029'
        return run(argv,**kwargs)
    try:
        assert post_boot.collect_systemd(fixture_run,True)['status']=='CRITICAL'
        Handler.connected=True
        assert post_boot.collect_systemd(fixture_run,True)['status']=='HEALTHY'
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)

@pytest.mark.parametrize('http_status',['302','403','500'])
def test_http_error_metrics_never_reports_healthy(monkeypatch,http_status):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'test-boot'))
    def fake(argv,**kwargs):
        if argv[0].endswith('systemctl'): return 'ActiveState=active\nMainPID=4029'
        assert argv[1]=='--disable'  # curlrc cannot add redirects/credentials/extra URLs
        return '200' if argv[-1].endswith('/ready') else 'cloudflared_tunnel_ha_connections 4\n'+http_status
    data=post_boot.collect_systemd(fake,True)
    assert data['status']=='UNKNOWN'
    assert data['edge_connections'] is None
