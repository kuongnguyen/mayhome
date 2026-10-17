from datetime import datetime,timedelta,timezone
import gzip
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import pytest
from mayhome_center import verifier as v

ID='20261015-210345'


@pytest.fixture
def root(tmp_path,monkeypatch):
    # Production keeps O_NOATIME + root-only checks. Temp fixtures belong to the test user.
    monkeypatch.setattr(v,'flags',lambda directory=False:os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK|(os.O_DIRECTORY if directory else 0))
    original=v.protected
    def check(info,directory=False):
        mode=info.st_mode
        if directory and not stat.S_ISDIR(mode):raise v.Inconclusive('NOT_DIRECTORY')
        if not directory and not stat.S_ISREG(mode):raise v.Inconclusive('NOT_REGULAR_FILE')
    monkeypatch.setattr(v,'protected',check)
    root=tmp_path/'backup';(root/'snapshots').mkdir(parents=True)
    return root


def checkpoint(root,name=ID,body=None,digest=None,complete=True):
    path=root/'snapshots'/name;(path/'database').mkdir(parents=True);(path/'manifests').mkdir()
    body=gzip.compress(b'SYNTHETIC SQL ONLY\n'*100) if body is None else body
    (path/'database/nextcloud.sql.gz').write_bytes(body)
    (path/'manifests/database.sha256').write_text((digest or hashlib.sha256(body).hexdigest())+'  nextcloud.sql.gz\n')
    if complete:(path/'COMPLETE').write_text('COMPLETE '+name+'\n')
    return path


def test_hash_and_gzip_single_pass(root):
    path=checkpoint(root);before=(path/'database/nextcloud.sql.gz').read_bytes()
    result=v.verify(ID,root,check=lambda:None)
    assert result['status']=='PASS' and result['db_checksum_verified']
    assert result['sha256_match'] and result['gzip_valid'] and not result['restoration_verified']
    assert result['compressed_bytes_read']==len(before)
    assert before==(path/'database/nextcloud.sql.gz').read_bytes()
    assert 'SYNTHETIC SQL' not in str(result)


def test_sha_mismatch_is_failed_only_after_stability_check(root):
    checkpoint(root,digest='0'*64)
    result=v.verify(ID,root,check=lambda:None)
    assert result['status']=='FAILED' and result['code']=='SHA256_MISMATCH' and result['gzip_valid']


@pytest.mark.parametrize('body',[b'',b'not gzip',gzip.compress(b'fake')[:-3],gzip.compress(b'fake')+b'junk'])
def test_invalid_gzip_never_passes_even_when_hash_matches(root,body):
    checkpoint(root,body=body)
    result=v.verify(ID,root,check=lambda:None)
    assert result['status']=='FAILED' and result['sha256_match'] is True and result['gzip_valid'] is False


def test_multimember_and_expansion_limit(root):
    checkpoint(root,body=gzip.compress(b'a'*1000)+gzip.compress(b'b'*1000))
    passed=v.verify(ID,root,check=lambda:None)
    assert passed['status']=='PASS' and passed['gzip_members']==2 and passed['expanded_bytes_processed']==2000
    limited=v.verify(ID,root,check=lambda:None,expanded_limit=100)
    assert limited['status']=='INCONCLUSIVE' and limited['code']=='EXPANDED_BUDGET_EXCEEDED'


def test_compressed_budget_no_read_and_plan_does_not_read_sql(root,monkeypatch):
    path=checkpoint(root)
    read=os.read;pread=os.pread
    sql_inode=(path/'database/nextcloud.sql.gz').stat().st_ino
    def refuse_sql(fd,count):
        assert os.fstat(fd).st_ino!=sql_inode
        return read(fd,count)
    monkeypatch.setattr(os,'read',refuse_sql)
    def refuse_sql_pread(fd,*args):
        assert os.fstat(fd).st_ino!=sql_inode
        return pread(fd,*args)
    monkeypatch.setattr(os,'pread',refuse_sql_pread)
    report=v.inspect(ID,root,check=lambda:None)
    assert report['sql_content_read'] is False and not report['db_checksum_verified']
    assert v.verify(ID,root,check=lambda:None,compressed_limit=1)['code']=='COMPRESSED_BUDGET_EXCEEDED'


@pytest.mark.parametrize('relative',['database','manifests','COMPLETE','database/nextcloud.sql.gz','manifests/database.sha256'])
def test_symlink_rejected_at_each_component(root,relative,tmp_path):
    path=checkpoint(root);target=path/relative;outside=tmp_path/'outside'
    if target.is_dir():target.rename(outside)
    else:target.rename(outside)
    target.symlink_to(outside,target_is_directory=outside.is_dir())
    result=v.verify(ID,root,check=lambda:None)
    assert result['status']=='INCONCLUSIVE' and not result['db_checksum_verified']


@pytest.mark.parametrize('name',['baseline','.staging-x','../../etc','20260230-010000'])
def test_bad_id_is_never_opened(root,name):
    assert v.verify(name,root,check=lambda:None)['status']=='INCONCLUSIVE'


def test_manifest_path_traversal_rejected(root):
    path=checkpoint(root);(path/'manifests/database.sha256').write_text('0'*64+'  ../../etc/shadow\n')
    assert v.verify(ID,root,check=lambda:None)['code']=='INVALID_SHA256_MANIFEST'


def test_metadata_change_or_engine_start_aborts(root):
    path=checkpoint(root);calls=[]
    def mutate():
        calls.append(1)
        if len(calls)==2:(path/'COMPLETE').write_text('COMPLETE '+ID+'\nchanged\n')
    assert v.verify(ID,root,check=mutate)['code']=='SOURCE_CHANGED'
    def active():raise v.Inconclusive('BACKUP_NOT_IDLE_SUCCESS')
    assert v.verify(ID,root,check=active)['code']=='BACKUP_NOT_IDLE_SUCCESS'


def test_source_changed_and_corruption_is_not_reported_as_stable_failure(root):
    path=checkpoint(root,body=b'bad gzip');calls=[]
    def mutate():
        calls.append(1)
        if len(calls)==2:(path/'database/nextcloud.sql.gz').write_bytes(b'changed')
    report=v.verify(ID,root,check=mutate)
    assert report['status']=='INCONCLUSIVE' and report['code']=='SOURCE_CHANGED'


def test_root_mode_and_noatime_requirements(monkeypatch):
    with pytest.raises(v.Inconclusive):v.protected(SimpleNamespace(st_uid=123,st_mode=stat.S_IFREG|0o600))
    with pytest.raises(v.Inconclusive):v.protected(SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o666))
    assert v.flags()&os.O_NOATIME


def test_projection_no_sql_reads_fixed_latest_staging_and_age_boundary(root,monkeypatch):
    for name in (ID,'20260101-000000','20260102-000000','20260103-000000'):checkpoint(root,name)
    (root/'snapshots/.staging-fixture').mkdir()
    read=os.read
    monkeypatch.setattr(os,'read',lambda *_:pytest.fail('projection must not read SQL'))
    result=v.projection(root,check=lambda:None,now=datetime(2026,11,1,tzinfo=timezone.utc))
    rows={r['snapshot_id']:r for r in result['items']}
    assert result['status']=='PROJECTION_ONLY' and not result['retention_verified']
    assert rows[ID]['protected'] and rows['20260103-000000']['protected']
    assert any(row['reason']=='staging_preserve' and row['protected'] for row in rows.values())
    assert rows['20260101-000000']['age_candidate_pending_integrity']
    assert result['age_candidate_count']==2


def test_projection_exact_fourteen_days_and_unknown_inventory(root):
    for name in ('20260101-000000','20260102-000000','20260103-000000'):checkpoint(root,name)
    when=v.stamp('20260101-000000')+timedelta(days=14)
    report=v.projection(root,check=lambda:None,now=when)
    assert report['age_candidate_count']==0
    (root/'snapshots/unexpected').mkdir()
    report=v.projection(root,check=lambda:None,now=when+timedelta(days=10))
    assert report['status']=='INCONCLUSIVE' and report['age_candidate_count'] is None
    assert not any(r['age_candidate_pending_integrity'] for r in report['items'])


def test_inventory_limit_and_guard_fail_closed(root):
    (root/'snapshots/unexpected').mkdir()
    calls=[]
    def guard():
        calls.append(1)
        if len(calls)==2:raise v.Inconclusive('BACKUP_NOT_IDLE_SUCCESS')
    with pytest.raises(v.Inconclusive):v.projection(root,check=guard)


def test_own_lock_prevents_concurrent_verifiers(root,monkeypatch):
    monkeypatch.setattr(v,'LOCK',root/'verifier/verify.lock')
    with v.own_lock():
        with pytest.raises(v.Inconclusive,match='VERIFIER_ALREADY_RUNNING'):
            with v.own_lock():pass


def test_time_budget_and_incomplete_marker(root):
    path=checkpoint(root)
    result=v.verify(ID,root,check=lambda:None,seconds=0)
    assert result['status']=='INCONCLUSIVE' and result['code']=='TIME_BUDGET_EXCEEDED'
    (path/'COMPLETE').write_text('not committed')
    assert v.verify(ID,root,check=lambda:None)['code']=='INVALID_COMPLETE_MARKER'


def test_guard_failure_overrides_matching_checksum(root):
    checkpoint(root);calls=[]
    def guard():
        calls.append(1)
        if len(calls)==2:raise v.Inconclusive('BACKUP_NOT_IDLE_SUCCESS')
    result=v.verify(ID,root,check=guard)
    assert result['status']=='INCONCLUSIVE' and not result['db_checksum_verified']


def test_crc_mismatch_is_detected_even_with_matching_sha(root):
    body=bytearray(gzip.compress(b'fake'));body[-8]^=1
    checkpoint(root,body=bytes(body))
    report=v.verify(ID,root,check=lambda:None)
    assert report['status']=='FAILED' and report['sha256_match'] is True and report['code']=='GZIP_INVALID'


def test_candidate_names_do_not_export_personal_or_unknown_names(root):
    (root/'snapshots/personal-name-secret').mkdir()
    report=v.projection(root,check=lambda:None)
    assert 'personal-name-secret' not in str(report)
    assert report['age_candidate_count'] is None


def test_snapshot_path_swap_never_returns_verified(root):
    path=checkpoint(root);calls=[]
    def swap():
        calls.append(1)
        if len(calls)==2:
            target=path/'database/nextcloud.sql.gz';replacement=target.with_suffix('.replacement')
            replacement.write_bytes(target.read_bytes());replacement.replace(target)
    result=v.verify(ID,root,check=swap)
    assert result['status']=='INCONCLUSIVE' and not result['db_checksum_verified']


def test_inventory_count_limit(root):
    for index in range(501):(root/'snapshots'/f'.staging-{index}').mkdir()
    with pytest.raises(v.Inconclusive,match='INVENTORY_BUDGET_EXCEEDED'):
        v.projection(root,check=lambda:None)


def test_safety_fails_closed_without_idle_loaded_engine(monkeypatch):
    monkeypatch.setattr(v,'mount_status',lambda *_:{'status':'HEALTHY'})
    monkeypatch.setattr(v,'unit_status',lambda _: {'load_state':'loaded','active_state':'active','result':'success'})
    with pytest.raises(v.Inconclusive,match='BACKUP_NOT_IDLE_SUCCESS'):v.safety()


def test_cli_refuses_verify_without_explicit_run(monkeypatch,capsys):
    monkeypatch.setattr(v.os,'geteuid',lambda:0)
    monkeypatch.setattr(v.os,'uname',lambda:SimpleNamespace(nodename='mayhome'))
    monkeypatch.setattr('sys.argv',['verifier','verify','--snapshot',ID])
    with pytest.raises(SystemExit) as error:v.main()
    assert error.value.code==2 and '--run' in capsys.readouterr().err


def test_rate_limit_is_applied_to_actual_compressed_reads(root):
    import time
    body=gzip.compress(os.urandom(65536));checkpoint(root,body=body)
    rate=256*1024;started=time.monotonic()
    report=v.verify(ID,root,check=lambda:None,rate=rate)
    assert report['status']=='PASS'
    assert time.monotonic()-started>=len(body)/rate-.02


def test_excessive_gzip_members_is_inconclusive(root):
    checkpoint(root,body=gzip.compress(b'')*1025)
    report=v.verify(ID,root,check=lambda:None)
    assert report['status']=='INCONCLUSIVE' and report['code']=='GZIP_MEMBER_LIMIT'
