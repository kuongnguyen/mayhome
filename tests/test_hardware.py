from datetime import datetime, timedelta, timezone
import json
import pytest
from mayhome_center import hardware as hw, create_app


def test_cpu_does_not_double_count_guest_and_counts_online_cores():
    result=hw.cpu('cpu  100 0 50 800 50 0 0 0 25 0\ncpu0 1 0 1 3\ncpu1 1 0 1 3\n')
    assert result=={'total':1000,'idle':850,'cores':2}


def test_memory_available_is_not_free_and_ignores_unrelated_fields():
    result=hw.memory('MemTotal: 1000 kB\nMemFree: 10 kB\nMemAvailable: 400 kB\nSwapTotal: 100 kB\nSwapFree: 30 kB\n')
    assert result['memory_percent']==60 and result['memory_used']==600*1024
    assert result['swap_used']==70*1024


@pytest.mark.parametrize('text',['MemTotal: 0 kB\nMemAvailable: 0 kB','MemTotal: 100 kB\nMemAvailable: 101 kB','MemFree: 1 kB'])
def test_memory_invalid_is_unknown(text):
    with pytest.raises((ValueError,KeyError)):hw.memory(text)


def test_only_whole_selected_disks_and_active_interfaces_counted():
    assert hw.disk_counters('8 0 sda 1 2 10 4 5 6 20 8\n8 1 sda1 1 2 9999 4 5 6 9999 8',{'sda'})=={'sda':[5120,10240]}
    assert hw.network_counters('lo: 999 0 0 0 0 0 0 0 999 0 0 0 0 0 0 0\neth0: 100 0 0 0 0 0 0 0 200 0 0 0 0 0 0 0',{'eth0'})=={'eth0':[100,200]}


@pytest.mark.parametrize('old,new',[(None,{'eth0':[10,20]}),({'eth0':[100,200]},{'eth0':[99,220]}),({'eth0':[1,2]},{'eth1':[10,20]}),({'eth0':[1,None]},{'eth0':[10,20]})])
def test_counter_resets_missing_and_changed_interfaces_do_not_become_zero(old,new):
    assert hw.pair_rate(new,old,30)==(None,None)


def test_counter_delta_uses_elapsed_time():
    assert hw.pair_rate({'sda':[400,800]},{'sda':[100,200]},30)==(10,20)


@pytest.fixture
def counters(monkeypatch):
    state={'mono':100,'cpu':'cpu 100 0 0 900 0 0 0 0\ncpu0 1 0 0 1','boot':'a'*8+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*4+'-'+ 'a'*12}
    def read(path):
        path=str(path)
        if path=='/proc/sys/kernel/random/boot_id':return state['boot']
        if path=='/proc/stat':return state['cpu']
        if path=='/proc/meminfo':return 'MemTotal: 1000 kB\nMemAvailable: 400 kB'
        if path=='/proc/uptime':return '100 0'
        raise FileNotFoundError()
    monkeypatch.setattr(hw,'read',read)
    monkeypatch.setattr(hw,'temperature',lambda:None)
    monkeypatch.setattr(hw.time,'monotonic',lambda:state['mono'])
    return state


def test_first_sample_and_missing_sensor_never_fabricated(counters):
    result=hw.collect()
    assert result['current']['cpu_percent'] is None
    assert result['current']['temperature_c'] is None
    assert result['current']['memory_percent']==60
    assert result['rate_state']=='waiting_for_second_sample'


def test_two_cpu_samples_then_reboot_and_gap(counters):
    first=hw.collect();counters.update(mono=130,cpu='cpu 200 0 0 1000 0 0 0 0\ncpu0 1 0 0 1')
    second=hw.collect(first)
    assert second['current']['cpu_percent']==50
    assert second['sample_interval_seconds']==30
    counters.update(mono=160,cpu='cpu 250 0 0 1050 0 0 0 0\ncpu0 1 0 0 1\ncpu1 1 0 0 1')
    assert hw.collect(second)['current']['cpu_percent'] is None
    counters['mono']=300
    assert hw.collect(second)['current']['cpu_percent'] is None
    counters.update(mono=30,boot='b'*8+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*4+'-'+ 'b'*12)
    reboot=hw.collect(second)
    assert len(reboot['history'])==1 and reboot['current']['cpu_percent'] is None


def test_bounded_history_and_sanitized_public(counters):
    first=hw.collect();now=datetime.now(timezone.utc)
    first['history']=[{'timestamp':(now-timedelta(seconds=i+1)).isoformat(),'cpu_percent':30} for i in reversed(range(130))]
    counters['mono']=130
    result=hw.collect(first)
    assert len(result['history'])==120
    assert not {'_counters','boot_id','monotonic'} & hw.public(result,'fresh').keys()


def test_chart_breaks_missing_values_and_long_gaps():
    start=datetime.now(timezone.utc)
    history=[{'timestamp':(start+timedelta(seconds=sec)).isoformat(),'cpu_percent':value} for sec,value in [(0,20),(30,40),(60,None),(90,30),(120,50),(400,10),(430,20)]]
    lines=hw.series(history,'cpu_percent')
    assert len(lines)==3 and all(len(line.split())==2 for line in lines)
    assert hw.series(history[:1],'cpu_percent')==[]


def test_hardware_endpoint_auth_and_dashboard_separation(tmp_path,counters):
    path=tmp_path/'status.json';result=hw.collect()
    path.write_text(json.dumps({'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),'data':{'hardware':result},'errors':[]}))
    app=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test','STATUS_PATH':str(path)})
    client=app.test_client()
    assert client.get('/api/v1/host/hardware').status_code==401
    with client.session_transaction() as session:session['authenticated']=True
    public=client.get('/api/v1/host/hardware').json['data']
    assert public['freshness']=='fresh' and '_counters' not in public
    assert '_counters' not in client.get('/api/v1/overview').json['data']['hardware']
    overview=client.get('/').text;backup=client.get('/backups').text
    assert overview.count('data-hardware=')==6
    assert 'id="snapshot-inventory"' not in overview
    assert 'data-hardware=' not in backup and 'id="snapshot-inventory"' in backup
    assert client.post('/api/v1/host/hardware').status_code==405
