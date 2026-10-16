import json
from copy import deepcopy
from datetime import timedelta
import pytest
from mayhome_center import post_boot, create_app
from mayhome_center.collector import ProbeError
from mayhome_center.domain import utcnow

class Host:
    def __init__(self):
        self.container={'id':'a'*64,'name':'/cloudflared-tunnel','running':True,
                        'network_mode':'cuongnh_nextcloud-net','ip':'172.18.0.2'}
        self.subnet='172.18.0.0/16'
        self.connections={'127.0.0.1':4,'172.18.0.2':4,'172.18.0.3':4}
        self.active='active'
        self.calls=[]
        self.inspect_count=0
        self.after=None
    def run(self,argv,**limits):
        self.calls.append(argv)
        if argv[0].endswith('systemctl'):
            assert argv[1:]==['show','cloudflared.service','--property=ActiveState,MainPID']
            return 'ActiveState='+self.active+'\nMainPID=4029'
        if argv[0].endswith('docker'):
            if argv[1]=='network':
                assert argv[-1]=='cuongnh_nextcloud-net'
                return json.dumps([{'Subnet':self.subnet}])
            assert argv[-1]=='cloudflared-tunnel'
            assert '.Config.Env' not in argv[-2]
            self.inspect_count+=1
            return json.dumps(self.after if self.after and self.inspect_count%2==0 else self.container)
        assert argv[0]=='/usr/bin/curl'
        assert argv[1]=='--disable'
        assert '--location' not in argv
        address=argv[-1].split('/')[2].split(':')[0]
        count=self.connections[address]
        return ('200' if count else '503') if argv[-1].endswith('/ready') else f'cloudflared_tunnel_ha_connections {count}\n200'

@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(900,'fixture-boot'))
    return Host()

def test_both_healthy(host):
    data=post_boot.collect_post_boot(host.run,True,True)
    assert data['status']=='HEALTHY'
    assert set(data['connectors'])=={'systemd','docker'}
    assert data['connectors']['docker']['running'] is True
    assert data['connectors']['docker']['edge_connections']==4
    assert data['connectors']['systemd']['active_state']=='active'

@pytest.mark.parametrize('broken',['127.0.0.1','172.18.0.2'])
def test_one_healthy_does_not_hide_other_disconnected(host,broken):
    host.connections[broken]=0
    data=post_boot.collect_post_boot(host.run,True,True)
    assert data['status']=='CRITICAL'
    failed='systemd' if broken=='127.0.0.1' else 'docker'
    other='docker' if failed=='systemd' else 'systemd'
    assert data['connectors'][failed]['status']=='CRITICAL'
    assert data['connectors'][other]['status']=='HEALTHY'
    assert data['connectors']['docker']['running'] is True

def test_ip_resolved_again_each_collection(host):
    assert post_boot.collect_docker(host.run,True)['address']=='172.18.0.2'
    host.container.update(ip='172.18.0.3',id='b'*64)
    host.calls=[]
    assert post_boot.collect_docker(host.run,True)['address']=='172.18.0.3'
    urls=[argv[-1] for argv in host.calls if argv[0].endswith('curl')]
    assert len(urls)==2
    assert all('172.18.0.3:20241' in url for url in urls)

def test_recreated_during_probe_cannot_reuse_health(host):
    host.after={**host.container,'id':'b'*64,'ip':'172.18.0.3'}
    data=post_boot.collect_docker(host.run,True)
    assert data['status']=='UNKNOWN'
    assert data['code']=='TUNNEL_CONTAINER_CHANGED'
    assert data['edge_connections'] is None

@pytest.mark.parametrize('address',['127.0.0.1','169.254.169.254','8.8.8.8','172.19.0.2','172.18.0.0','172.18.255.255','::1','localhost','172.18.0.2/path',''])
def test_unsafe_or_outside_network_ip_never_probed(host,address):
    host.container['ip']=address
    assert post_boot.collect_docker(host.run,True)['status']=='UNKNOWN'
    assert not any(argv[0].endswith('curl') for argv in host.calls)

@pytest.mark.parametrize('subnet',['0.0.0.0/0','8.8.0.0/16','172.18.0.0/8','172.18.0.0/31','not-a-network'])
def test_unsafe_network_never_probed(host,subnet):
    host.subnet=subnet
    assert post_boot.collect_docker(host.run,True)['status']=='UNKNOWN'
    assert not any(argv[0].endswith('curl') for argv in host.calls)

def test_stopped_container_does_not_probe_reassigned_ip(host):
    host.container['running']=False
    data=post_boot.collect_docker(host.run,True)
    assert data['status']=='CRITICAL'
    assert data['code']=='TUNNEL_CONTAINER_NOT_RUNNING'
    assert not any(argv[0].endswith('curl') for argv in host.calls)

@pytest.mark.parametrize('field,value',[('name','/different'),('network_mode','host'),('id','invalid'),('running','true')])
def test_identity_and_network_contract(host,field,value):
    host.container[field]=value
    assert post_boot.collect_docker(host.run,True)['status']=='UNKNOWN'
    assert not any(argv[0].endswith('curl') for argv in host.calls)

def test_missing_docker_is_partial_telemetry(host):
    original=host.run
    def limited(argv,**kwargs):
        if argv[0].endswith('docker'):raise FileNotFoundError()
        return original(argv,**kwargs)
    data=post_boot.collect_post_boot(limited,True,True)
    assert data['status']=='UNKNOWN'
    assert data['connectors']['systemd']['status']=='HEALTHY'
    assert data['connectors']['docker']['status']=='UNKNOWN'

def test_opt_in_independent(host):
    data=post_boot.collect_post_boot(host.run,True,False)
    assert data['status']=='UNKNOWN'
    assert data['connectors']['docker']['code']=='TUNNEL_MONITOR_NOT_CONFIGURED'
    assert not any(argv[0].endswith('docker') for argv in host.calls)

def test_boot_grace_separate_from_runtime(host,monkeypatch):
    monkeypatch.setattr(post_boot,'boot_context',lambda:(299,'new-boot'))
    host.connections['172.18.0.2']=0
    data=post_boot.collect_post_boot(host.run,True,True)
    assert data['status']=='WARNING'
    assert data['connectors']['docker']['status']=='WARNING'
    assert data['connectors']['systemd']['status']=='HEALTHY'

@pytest.mark.parametrize('active',['inactive','failed','unknown'])
def test_systemd_inactive_or_unknown_never_green(host,active):
    host.active=active
    assert post_boot.collect_systemd(host.run,True)['status']!='HEALTHY'

def test_stale_and_unauthenticated_nested_api(host,tmp_path):
    path=tmp_path/'status.json'
    monitor=post_boot.collect_post_boot(host.run,True,True)
    payload={'schema_version':1,'generated_at':(utcnow()-timedelta(seconds=100)).isoformat(),
             'data':{'post_boot':monitor},'errors':[]}
    path.write_text(json.dumps(payload))
    client=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test','STATUS_PATH':str(path)}).test_client()
    assert client.get('/api/v1/host/post-boot').status_code==401
    with client.session_transaction() as session:session['authenticated']=True
    for route in ('/api/v1/host/post-boot','/api/v1/overview'):
        data=client.get(route).json['data']
        if route.endswith('overview'):data=data['post_boot']
        assert data['status']=='STALE'
        assert all(row['status']=='STALE' for row in data['connectors'].values())
    html=client.get('/').text
    assert 'data-connector="docker"' in html and 'data-connector="systemd"' in html
    assert 'status HEALTHY' not in html
