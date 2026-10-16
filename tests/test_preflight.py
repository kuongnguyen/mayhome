from types import SimpleNamespace
import pytest
from mayhome_center import preflight

@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(preflight.os,'uname',lambda:SimpleNamespace(nodename='mayhome'))
    monkeypatch.setattr(preflight,'mount_status',lambda path,uuid:{'mountpoint':path,'status':'HEALTHY'})
    def unit(name):
        return {'unit_name':name,'load_state':'loaded','active_state':'inactive' if name.endswith('.service') else 'active','enabled_state':'enabled','result':'success'}
    monkeypatch.setattr(preflight,'unit_status',unit)
    monkeypatch.setattr(preflight,'recovery_marker',lambda:False)
    monkeypatch.setattr(preflight,'run',lambda *args,**kwargs:'true')
    monkeypatch.setattr(preflight,'collect_post_boot',lambda *args,**kwargs:{'status':'HEALTHY'})

def test_cloud_refuses_production_audit(monkeypatch):
    monkeypatch.setattr(preflight.os,'uname',lambda:SimpleNamespace(nodename='cloud-worker'))
    monkeypatch.setattr(preflight,'mount_status',lambda *args:pytest.fail('No host probes on cloud'))
    report=preflight.audit(True)
    assert not report['target_host_verified']
    assert not report['deployment_allowed']
    assert report['checks'][0]['code']=='TARGET_HOST_MISMATCH'

def test_auto_pass_does_not_authorize_deployment(host):
    report=preflight.audit(True)
    assert report['automatic_checks_passed']
    assert not report['deployment_allowed']
    assert 'NEXTCLOUD_MAINTENANCE_OFF_VERIFIED' in report['manual_gates']

def test_no_opt_in_no_tunnel_probe(host,monkeypatch):
    monkeypatch.setattr(preflight,'collect_post_boot',lambda *args,**kwargs:pytest.fail('probe requires opt-in'))
    report=preflight.audit()
    assert not report['automatic_checks_passed']
    assert any(c['code']=='TUNNEL_PROBE_NOT_REQUESTED' for c in report['checks'])

@pytest.mark.parametrize('state',['active','activating','deactivating','failed','unknown'])
def test_active_or_unknown_backup_blocks(host,monkeypatch,state):
    original=preflight.unit_status
    def unit(name):
        row=original(name)
        if name=='nextcloud-auto-backup.service':row['active_state']=state
        return row
    monkeypatch.setattr(preflight,'unit_status',unit)
    assert not preflight.audit(True)['automatic_checks_passed']

def test_marker_blocks(host,monkeypatch):
    monkeypatch.setattr(preflight,'recovery_marker',lambda:True)
    report=preflight.audit(True)
    assert not report['automatic_checks_passed']
    assert any(c['code']=='RECOVERY_MARKER_PRESENT' and c['outcome']=='BLOCKED' for c in report['checks'])

def test_missing_mount_cannot_pass(host,monkeypatch):
    def denied(*args):raise PermissionError('DO NOT EXPOSE RAW ERROR')
    monkeypatch.setattr(preflight,'mount_status',denied)
    report=preflight.audit(True)
    assert not report['automatic_checks_passed']
    assert 'DO NOT EXPOSE' not in str(report)

def test_retention_disabled_never_requires_activation(host,monkeypatch):
    original=preflight.unit_status
    def unit(name):
        row=original(name)
        if name=='nextcloud-backup-retention.timer':row.update(active_state='inactive',enabled_state='disabled')
        return row
    monkeypatch.setattr(preflight,'unit_status',unit)
    assert preflight.audit(True)['automatic_checks_passed']

def test_inactive_failed_backup_result_blocks(host,monkeypatch):
    original=preflight.unit_status
    def unit(name):
        row=original(name)
        if name=='nextcloud-auto-backup.service':row['result']='exit-code'
        return row
    monkeypatch.setattr(preflight,'unit_status',unit)
    assert not preflight.audit(True)['automatic_checks_passed']


@pytest.mark.parametrize('state',['false','unrecognized'])
def test_stopped_or_unknown_container_blocks(host,monkeypatch,state):
    monkeypatch.setattr(preflight,'run',lambda *args,**kwargs:state)
    report=preflight.audit(True)
    assert not report['automatic_checks_passed']
    assert all(c['outcome']=='BLOCKED' for c in report['checks'] if c['code']=='CONTAINER_STATE')
