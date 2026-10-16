from datetime import datetime,timedelta,timezone
import json,sqlite3
import pytest
from mayhome_center.alerts import evaluate,present
from mayhome_center.collector import parse_systemd_time
from mayhome_center import history_store as hs
from mayhome_center.hardware import series
from mayhome_center.backup_history import grammar_summary,parse_events

NOW=datetime(2026,10,16,20,tzinfo=timezone.utc)


def test_due_observed_then_missed_and_not_guessed():
    due=NOW.timestamp()+3600
    initial=evaluate({'backup':{'next_run_epoch':due}},NOW)
    late=evaluate({'backup':{'active_state':'inactive','next_run_epoch':due+86400}},NOW+timedelta(hours=2),initial)
    assert late['items'][0]['code']=='BACKUP_LATE'
    assert not evaluate({'backup':{'active_state':'inactive'}},NOW)['items']
    running=evaluate({'backup':{'active_state':'active','next_run_epoch':due+86400}},NOW+timedelta(hours=2),late)
    assert not running['items'] and running['resolved']


def test_late_grace_and_journal_gap_are_not_backup_failure():
    old={'schedule':{'due_at':NOW.timestamp()-600}}
    assert not evaluate({'backup':{'active_state':'inactive'}},NOW,old)['items']
    old['schedule']['due_at']=NOW.timestamp()-1900
    result=evaluate({'backup':{'active_state':'inactive'}},NOW,old)
    assert result['items'][0]['severity']=='WARNING'
    assert result['items'][0]['code']!='BACKUP_FAILED'


def test_failure_tunnel_and_disk_levels_and_dedup():
    data={'backup':{'result':'failed'},'post_boot':{'connectors':{'docker':{'status':'CRITICAL','running':True,'edge_connections':0}}},
          'storage':[{'mountpoint':'fixture','status':'HEALTHY','total_bytes':100*1024**3,'available_bytes':4*1024**3}]}
    first=evaluate(data,NOW);second=evaluate(data,NOW+timedelta(seconds=30),first)
    assert {item['code'] for item in first['items']}=={'BACKUP_FAILED','TUNNEL_EDGE_DISCONNECTED','DISK_NEAR_FULL'}
    assert second['items'][0]['first_seen']==first['items'][0]['first_seen']
    assert all(item['severity']=='STALE' for item in present(second,'stale')['items'])


@pytest.mark.parametrize('text,valid',[('Sat 2026-10-17 02:00:00 +07',True),('2026-10-17 02:00:00 UTC',True),('Sat 2026-10-17 02:00:00 ICT',True),('n/a',False),('Sat 2026-10-17 02:00:00 UNKNOWN',False)])
def test_systemd_time_does_not_guess_unknown_zone(text,valid):
    assert (parse_systemd_time(text) is not None)==valid


@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(hs,'trusted',lambda *args:None)
    monkeypatch.setattr(hs,'MIN_FREE',0)
    return tmp_path/'hardware.sqlite3'


def sample(stamp,boot='fixture-boot',value=20):
    return {'observed_at':stamp.isoformat(),'boot_id':boot,'current':{'cpu_percent':value,'memory_percent':50}}


def test_persistence_survives_reopen_reboot_and_no_duplicate_minute(db):
    for index in range(4):assert hs.append(sample(NOW+timedelta(seconds=index*30)),db)['state']=='available'
    hs.append(sample(NOW+timedelta(seconds=90)),db)
    hs.append(sample(NOW+timedelta(seconds=150),'new-boot'),db)
    result=hs.read(1,30,db,now=(NOW+timedelta(seconds=160)).timestamp())
    assert result['state']=='available' and result['items']
    assert any(row['discontinuous'] for row in result['items'])
    with sqlite3.connect(db) as connection:assert connection.execute('SELECT COUNT(*) FROM raw').fetchone()[0]==5


def test_minute_averages_nulls_and_history_retention(db):
    hs.append(sample(NOW,value=10),db);hs.append(sample(NOW+timedelta(seconds=30),value=30),db)
    future=NOW+timedelta(days=2)
    hs.append(sample(future),db)
    result=hs.read(168,240,db,now=(future+timedelta(seconds=1)).timestamp())
    assert any(row['cpu_percent']==20 for row in result['items'])
    with sqlite3.connect(db) as c:assert c.execute('SELECT COUNT(*) FROM raw').fetchone()[0]==1
    hs.append(sample(NOW+timedelta(days=31)),db)
    with sqlite3.connect(db) as c:assert c.execute('SELECT MIN(stamp) FROM minutes').fetchone()[0]>NOW.timestamp()


def test_clock_reverse_pressure_and_missing_db_do_not_break_collection(db,monkeypatch):
    hs.append(sample(NOW),db)
    assert hs.append(sample(NOW-timedelta(hours=1)),db)['code']=='HISTORY_CLOCK_REVERSED'
    assert hs.read(path=db.parent/'missing')['state']=='unavailable'
    assert not (db.parent/'missing').exists()
    monkeypatch.setattr(hs,'MIN_FREE',10**30)
    assert hs.append(sample(NOW+timedelta(minutes=1)),db)['code']=='HISTORY_DISK_PRESSURE'


def test_read_is_readonly_and_range_bounded(db):
    hs.append(sample(NOW),db)
    before=db.read_bytes();hs.read(1,30,db,now=NOW.timestamp()+1)
    assert db.read_bytes()==before
    for hours,points in [(2,240),(720,241),(1,0)]:
        with pytest.raises(ValueError):hs.read(hours,points,db)


def test_history_discontinuity_breaks_charts():
    rows=[{'timestamp':(NOW+timedelta(seconds=i*60)).isoformat(),'cpu_percent':20,'discontinuous':i==2} for i in range(4)]
    assert len(series(rows,'cpu_percent'))==2


def test_grammar_export_never_contains_raw_message():
    raw=json.dumps({'MESSAGE':'NC-BACKUP-RC1.3 unexpected SECRET /personal/file','__REALTIME_TIMESTAMP':'1792108800000000'})
    report=grammar_summary(raw,parse_events(raw))
    assert report['unrecognized_tagged_lines']==1
    assert 'SECRET' not in str(report) and 'personal' not in str(report)


def test_api_history_and_alert_auth_and_invalid_inputs(db,tmp_path):
    from mayhome_center import create_app
    hs.append(sample(NOW),db)
    status=tmp_path/'status.json';status.write_text(json.dumps({'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
       'data':{'alerts':evaluate({'backup':{'result':'failed'}},NOW)},'errors':[]}))
    client=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test','STATUS_PATH':str(status),'HISTORY_PATH':str(db)}).test_client()
    assert client.get('/api/v1/host/hardware/history').status_code==401
    with client.session_transaction() as session:session['authenticated']=True
    assert client.get('/api/v1/alerts').json['data']['items'][0]['code']=='BACKUP_FAILED'
    assert client.get('/api/v1/host/hardware/history?hours=720').status_code==200
    assert client.get('/api/v1/host/hardware/history?hours=2').status_code==400
    assert client.get('/?hours=2').status_code==400
    assert client.post('/api/v1/alerts').status_code==405


def test_writer_lock_degrades_reader_without_create(db):
    hs.append(sample(NOW),db)
    writer=sqlite3.connect(db);writer.execute('BEGIN EXCLUSIVE')
    try:assert hs.read(path=db)['state']=='unavailable'
    finally:writer.rollback();writer.close()


def test_history_quota_failure_is_reported(db,monkeypatch):
    monkeypatch.setattr(hs,'QUOTA',4096)
    monkeypatch.setattr(hs,'PATH',db)
    # Explicit path exercises SQLite's hard page limit; persist catches it for collector safety.
    with pytest.raises(sqlite3.Error):hs.append(sample(NOW),db)


def test_symlink_history_is_rejected(tmp_path):
    target=tmp_path/'real';target.write_text('test');link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(ValueError):hs.trusted(link)


def test_alert_due_time_survives_reboot_via_own_database(db):
    due=NOW.timestamp()+3600
    state=evaluate({'backup':{'next_run_epoch':due}},NOW)
    hs.append(sample(NOW),db,alerts=state)
    retained=hs.load_alerts(db)
    late=evaluate({'backup':{'active_state':'inactive','next_run_epoch':due+86400}},NOW+timedelta(hours=2),retained)
    assert late['items'][0]['code']=='BACKUP_LATE'


def test_full_month_query_is_bounded(db):
    hs.append(sample(NOW),db)
    with sqlite3.connect(db) as c:
        text=json.dumps({'cpu_percent':[20,1]})
        c.executemany('INSERT OR REPLACE INTO minutes VALUES (?,?,?)',((int(NOW.timestamp())-i*60,'fixture',text) for i in range(43200)))
    result=hs.read(720,240,db,now=NOW.timestamp()+1)
    assert result['state']=='available' and 1<=len(result['items'])<=240


def test_observer_enable_preserves_existing_and_is_repeatable(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('enable_observers',Path(__file__).parents[1]/'packaging/enable_observers.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'TARGET',tmp_path/'dropins');monkeypatch.setattr(module,'trusted',lambda path:None)
    module.TARGET.mkdir();existing=module.TARGET/'nextcloud-status.conf';existing.write_text('custom existing content')
    with pytest.raises(RuntimeError):module.configure()
    assert not (module.TARGET/'persistent-history.conf').exists() and existing.read_text()=='custom existing content'
    existing.write_bytes((module.SOURCE/'nextcloud-status.conf.example').read_bytes())
    module.configure();first=(module.TARGET/'persistent-history.conf').read_bytes();module.configure()
    assert (module.TARGET/'persistent-history.conf').read_bytes()==first


def test_operator_evidence_tool_outputs_only_sanitized_fields(monkeypatch,capsys):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('backup_evidence',Path(__file__).parents[1]/'scripts/backup_evidence.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def command(argv,limit):
        if argv[0]=='/usr/bin/docker':return json.dumps({'installed':True,'maintenance':False,'needsDbUpgrade':False,'secret':'PRIVATE'})
        return json.dumps({'MESSAGE':'NC-BACKUP-RC1.3 BACKUP PASS PRIVATE /personal/file','__REALTIME_TIMESTAMP':'1792108800000000'})
    monkeypatch.setattr(module,'command',command);module.main();text=capsys.readouterr().out;report=json.loads(text)
    assert report['nextcloud']['maintenance'] is False
    assert report['log_grammar']['events']['BACKUP PASS']==1
    assert 'PRIVATE' not in text and '/personal/' not in text


def test_persistent_history_readable_before_first_collector_sample(db,tmp_path):
    from mayhome_center import create_app
    hs.append(sample(NOW),db)
    client=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test','STATUS_PATH':str(tmp_path/'not-yet-collected'),'HISTORY_PATH':str(db)}).test_client()
    with client.session_transaction() as session:session['authenticated']=True
    result=client.get('/api/v1/host/hardware/history')
    assert result.status_code==200 and result.json['meta']['freshness']=='unavailable'
    assert client.get('/api/v1/host/hardware').status_code==503
