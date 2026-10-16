import json
import os
from datetime import timedelta
from pathlib import Path
import pytest
from mayhome_center import collector, create_app
from mayhome_center.backup_history import parse_events, reconstruct_jobs, current_execution
from mayhome_center.backup_diagnostics import diagnostics
from mayhome_center.domain import utcnow, protection
from mayhome_center.snapshot_metadata import cached_metadata, summary

BOOT='b'*32
INV='a'*32

def event(message, seconds, invocation=INV, boot=BOOT, engine='RC1.3'):
    return json.dumps({'MESSAGE':f'NC-BACKUP-{engine} {message}',
        '__REALTIME_TIMESTAMP':str(1792108800000000 + seconds*1000000),
        '__MONOTONIC_TIMESTAMP':str(seconds*1000000), '_BOOT_ID':boot,
        '_SYSTEMD_INVOCATION_ID':invocation,'_SYSTEMD_UNIT':'nextcloud-auto-backup.service'})

def test_full_lifecycle_and_redaction():
    text='\n'.join([event('BACKUP START token=DO_NOT_LEAK',10),event('Database dump /private/person.jpg',20),event('BACKUP PASS',30)])
    rows=parse_events(text)
    jobs=reconstruct_jobs(rows)
    assert len(jobs)==1 and jobs[0]['result']=='success'
    assert jobs[0]['confidence']=='complete' and jobs[0]['duration_sec']==20
    assert jobs[0]['stage']=='Database dump'
    assert 'DO_NOT_LEAK' not in str(rows) and 'person.jpg' not in str(rows)

@pytest.mark.parametrize('text',[
    event('BACKUP PASS',30),
    '\n'.join([event('BACKUP START',10),event('BACKUP PASS',30,boot='c'*32)]),
    '\n'.join([event('BACKUP START',10),event('BACKUP PASS',30,invocation='c'*32)]),
    '\n'.join([event('BACKUP START',10),event('BACKUP PASS',30,engine='RC1.2')]),
    '\n'.join([event('BACKUP START',10),event('BACKUP START',11),event('BACKUP PASS',30)]),
])
def test_partial_reboot_mismatched_or_ambiguous_history(text):
    assert all(job['confidence']=='partial' and job['duration_sec'] is None for job in reconstruct_jobs(parse_events(text)))

def test_truncated_running_and_failed_history():
    rows=parse_events(event('BACKUP START',10)+'\n{truncated')
    assert reconstruct_jobs(rows)[0]['result']=='unknown'
    rows=parse_events(event('BACKUP FAILED',30,invocation='bad'))
    assert reconstruct_jobs(rows)[0]['result']=='failed'
    assert reconstruct_jobs(rows)[0]['duration_sec'] is None

def test_monotonic_not_wall_clock_duration():
    start=json.loads(event('BACKUP START',10));finish=json.loads(event('BACKUP PASS',30))
    finish['__MONOTONIC_TIMESTAMP']='1'
    jobs=reconstruct_jobs(parse_events(json.dumps(start)+'\n'+json.dumps(finish)))
    assert jobs[0]['confidence']=='partial' and jobs[0]['duration_sec'] is None

def test_unrelated_unit_and_large_message_ignored():
    raw=json.loads(event('BACKUP PASS',30));raw['_SYSTEMD_UNIT']='ssh.service'
    assert not parse_events(json.dumps(raw))
    assert not parse_events(event('BACKUP PASS '+'x'*9000,30))
    assert len(parse_events('\n'.join([event('BACKUP PASS',30)]*250)))==200

def test_current_stage_only_from_current_boot_and_invocation():
    rows=parse_events('\n'.join([event('Pre-sync',12),event('Database dump',15,boot='c'*32),event('Commit',16,invocation='d'*32)]))
    service={'active_state':'active','invocation_id':INV,'exec_start_monotonic_usec':10000000}
    state=current_execution(service,rows,20000000,BOOT)
    assert state['elapsed_sec']==10 and state['stage']=='Pre-sync'
    assert current_execution(service,rows,20000000,'d'*32)['stage']=='Unknown'
    assert current_execution(service,rows,1,BOOT)['elapsed_sec'] is None

def checkpoint(root,name='20261015-210345',checksum=True):
    path=root/'snapshots'/name;path.mkdir(parents=True)
    for directory in ['data','application','database','config','manifests']:(path/directory).mkdir()
    (path/'database/nextcloud.sql.gz').write_bytes(b'NOT READ OR HASHED')
    (path/'COMPLETE').touch();(path/'manifests/checkpoint.txt').touch()
    if checksum:(path/'manifests/database.sha256').write_text('a'*64+'  database/nextcloud.sql.gz\n')
    return path

def test_complete_structure_not_checksum_verification(tmp_path):
    checkpoint(tmp_path)
    row=collector.inventory(tmp_path)[0]
    assert row['integrity_level']=='structure_valid'
    assert row['db_checksum_manifest_format_valid'] is True
    assert row['db_checksum_verified'] is False and row['restoration_verified'] is False
    assert row['protected'] is True

@pytest.mark.parametrize('bad_path',['database/nextcloud.sql.gz','manifests/database.sha256','config','COMPLETE'])
def test_snapshot_symlinks_never_validate_structure(tmp_path,bad_path):
    path=checkpoint(tmp_path);victim=tmp_path/'private';victim.mkdir();(victim/'secret').write_text('DO_NOT_READ')
    target=path/bad_path
    if target.is_dir():target.rmdir()
    else:target.unlink()
    target.symlink_to(victim if bad_path=='config' else victim/'secret')
    row=collector.inventory(tmp_path)[0]
    assert row['integrity_level']!='structure_valid'
    assert 'DO_NOT_READ' not in str(row)

@pytest.mark.parametrize('content',['a'*64+'  ../../.env\n','a'*64+'  /etc/shadow\n','secret-value','x'*5000])
def test_checksum_metadata_is_bounded_and_only_expected_filename(tmp_path,content):
    path=checkpoint(tmp_path);(path/'manifests/database.sha256').write_text(content)
    row=collector.inventory(tmp_path)[0]
    assert row['db_checksum_manifest_format_valid'] is False
    assert row['db_checksum_verified'] is False
    assert content not in str(row)

def test_staging_with_complete_marker_stays_incomplete_and_unprotected(tmp_path):
    checkpoint(tmp_path,'.staging-example')
    row=collector.inventory(tmp_path)[0]
    assert row['completeness']=='incomplete' and not row['protected']
    assert row['integrity_level']=='unknown'

def test_invalid_timestamp_skipped(tmp_path):
    checkpoint(tmp_path,'20269999-250000')
    assert collector.inventory(tmp_path)==[]

def test_inventory_cache_ttl_independent_and_unknown_count():
    now=utcnow()
    state=summary([],now.isoformat())
    payload={'data':{'inventory':state,'snapshots':[]}}
    assert cached_metadata(payload,now)[1]['cached'] is True
    assert cached_metadata(payload,now+timedelta(seconds=181)) is None
    assert cached_metadata(payload,now-timedelta(seconds=1)) is None
    assert summary([],now.isoformat(),known=False)['count'] is None
    state['format_version']=1
    assert cached_metadata(payload,now) is None

def test_inventory_does_not_open_user_files_or_dump(tmp_path,monkeypatch):
    path=checkpoint(tmp_path)
    (path/'data/private.txt').write_text('PRIVATE')
    original=os.open
    def guarded(name,*args,**kwargs):
        assert not str(name).endswith(('.sql.gz','private.txt','config.php','.env'))
        return original(name,*args,**kwargs)
    monkeypatch.setattr(os,'open',guarded)
    assert collector.inventory(tmp_path)[0]['integrity_level']=='structure_valid'

def test_occ_opt_in_and_sanitization(monkeypatch):
    monkeypatch.delenv('MHC_NEXTCLOUD_STATUS',raising=False)
    def forbidden(*args,**kwargs):pytest.fail('not enabled')
    monkeypatch.setattr(collector,'run',forbidden)
    assert collector.nextcloud_status()['maintenance'] is None
    monkeypatch.setenv('MHC_NEXTCLOUD_STATUS','1')
    def status(argv,**limits):
        assert argv==['/usr/bin/docker','exec','--user','www-data','nextcloud-app','php','/var/www/html/occ','status','--output=json']
        assert limits['timeout']==5 and limits['max_bytes']==16384
        return json.dumps({'maintenance':False,'installed':True,'needsDbUpgrade':False,'version':'35.0.1.1','secret':'NEVER_EMIT'})
    monkeypatch.setattr(collector,'run',status)
    result=collector.nextcloud_status()
    assert result['maintenance'] is False and 'NEVER_EMIT' not in str(result)

@pytest.mark.parametrize('raw',[{},[],{'maintenance':'false','installed':True,'needsDbUpgrade':False}])
def test_invalid_occ_never_claims_maintenance_off(monkeypatch,raw):
    monkeypatch.setenv('MHC_NEXTCLOUD_STATUS','1')
    monkeypatch.setattr(collector,'run',lambda *args,**kwargs:json.dumps(raw))
    with pytest.raises(collector.ProbeError):collector.nextcloud_status()

def test_diagnostics_explains_unknown_and_running_marker_not_recovery_failure():
    data={'backup':{'active_state':'active','marker_present':True,'maintenance':True}}
    data['diagnostics']=diagnostics(data)
    assert protection(data,'fresh')=='RUNNING'
    assert any(check['code']=='CHECKSUM' and check['status']=='UNKNOWN' for check in data['diagnostics']['checks'])
    data['backup']['active_state']='inactive'
    assert protection(data,'fresh')=='CRITICAL'
    assert protection(data,'stale')=='STALE'

def test_backup_pages_api_empty_and_partial_job(tmp_path):
    path=tmp_path/'status.json';now=utcnow().isoformat()
    data={'backup':{},'jobs':[{'id':'fixture','result':'unknown','start_at':now,'finish_at':None,'confidence':'partial','duration_sec':None}],
          'inventory':summary([],now,known=False),'diagnostics':diagnostics({})}
    path.write_text(json.dumps({'schema_version':1,'generated_at':now,'data':data,'errors':[]}))
    client=create_app({'TESTING':True,'SECRET_KEY':'test','AUTH_TOKEN':'test','STATUS_PATH':str(path)}).test_client()
    assert client.get('/api/v1/backups/diagnostics').status_code==401
    with client.session_transaction() as session:session['authenticated']=True
    assert client.get('/backups').status_code==200
    assert 'Chưa rõ kết quả' in client.get('/backups').text
    assert client.get('/api/v1/backups/inventory').json['data']['count'] is None
    assert client.get('/api/v1/backups/diagnostics').json['data']['unknown_count']>0
    assert client.post('/api/v1/backups/diagnostics').status_code==405
