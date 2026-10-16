import importlib.util
import json
from pathlib import Path
import subprocess
import pytest

spec=importlib.util.spec_from_file_location('upgrade',Path(__file__).parents[1]/'packaging/upgrade.py')
upgrade=importlib.util.module_from_spec(spec);spec.loader.exec_module(upgrade)
SHA='a'*40

@pytest.fixture
def tree(tmp_path,monkeypatch):
    base=tmp_path/'releases';base.mkdir()
    current=tmp_path/'mayhome-center';current.mkdir();(current/'previous-source').write_text('old')
    release=base/SHA;release.mkdir();(release/'READY.json').write_text(json.dumps({'sha':SHA}))
    secret=tmp_path/'mayhome-center.env';secret.write_text('DO NOT CHANGE')
    calls=[]
    monkeypatch.setattr(upgrade,'BASE',base);monkeypatch.setattr(upgrade,'CURRENT',current)
    monkeypatch.setattr(upgrade,'trusted',lambda path:None)
    monkeypatch.setattr(upgrade,'backup_idle',lambda:None)
    monkeypatch.setattr(upgrade,'service_active',lambda unit:True)
    def checked(argv,**kwargs):
        assert argv[0]=='systemctl'
        assert all(name.startswith('mayhome-center') for name in argv[2:])
        calls.append(argv)
        return subprocess.CompletedProcess(argv,0,stdout='')
    monkeypatch.setattr(upgrade,'checked',checked)
    return current,release,secret,calls

def test_activation_and_rollback_preserve_legacy_and_secret(tree):
    current,release,secret,calls=tree
    upgrade.activate(SHA)
    assert current.is_symlink() and current.resolve()==release
    record=json.loads((release/'ACTIVATION.json').read_text())
    previous=Path(record['previous_target'])
    assert (previous/'previous-source').read_text()=='old'
    assert secret.read_text()=='DO NOT CHANGE'
    upgrade.rollback(SHA)
    assert current.resolve()==previous
    assert release.exists() and previous.exists() and secret.read_text()=='DO NOT CHANGE'

@pytest.mark.parametrize('when',['stop','start'])
def test_failure_restores_previous_release(tree,monkeypatch,when):
    current,release,secret,calls=tree
    original=upgrade.checked
    failed=False
    def failing(argv,**kwargs):
        nonlocal failed
        if argv[1]==when and not failed:
            failed=True
            raise subprocess.CalledProcessError(1,argv)
        return original(argv,**kwargs)
    monkeypatch.setattr(upgrade,'checked',failing)
    with pytest.raises(subprocess.CalledProcessError):upgrade.activate(SHA)
    assert (current/'previous-source').read_text()=='old'
    assert secret.read_text()=='DO NOT CHANGE'

def test_active_backup_prevents_stopping_mhc(tree,monkeypatch):
    current,release,secret,calls=tree
    def active():raise RuntimeError('backup active')
    monkeypatch.setattr(upgrade,'backup_idle',active)
    with pytest.raises(RuntimeError):upgrade.activate(SHA)
    assert not calls and not current.is_symlink()

@pytest.mark.parametrize('sha',['../etc','HEAD','', 'A'*40, 'a'*39])
def test_invalid_release_id_rejected(tree,sha):
    with pytest.raises(ValueError):upgrade.release_path(sha)

def test_missing_or_wrong_ready_proof_rejected(tree):
    current,release,secret,calls=tree
    (release/'READY.json').write_text(json.dumps({'sha':'b'*40}))
    with pytest.raises(RuntimeError):upgrade.activate(SHA)
    assert not calls

def test_reactivation_does_not_overwrite_rollback_evidence(tree):
    current,release,secret,calls=tree
    upgrade.activate(SHA)
    record=(release/'ACTIVATION.json').read_bytes()
    with pytest.raises(RuntimeError):upgrade.activate(SHA)
    assert (release/'ACTIVATION.json').read_bytes()==record

def test_no_start_of_previously_inactive_units(tree,monkeypatch):
    current,release,secret,calls=tree
    monkeypatch.setattr(upgrade,'service_active',lambda unit:False)
    upgrade.activate(SHA)
    assert not any(argv[1]=='start' for argv in calls)

def test_unrelated_rollback_refused(tree):
    current,release,secret,calls=tree
    with pytest.raises(RuntimeError):upgrade.rollback(SHA)
    assert not calls

@pytest.mark.parametrize('values',[{}, {'LoadState':'not-found','ActiveState':'inactive','Result':'success'},
                                  {'LoadState':'loaded','ActiveState':'active','Result':'success'},
                                  {'LoadState':'loaded','ActiveState':'inactive','Result':'exit-code'}])
def test_backup_missing_running_or_failed_blocks(monkeypatch,values):
    monkeypatch.setattr(upgrade,'checked',lambda *args,**kwargs:subprocess.CompletedProcess(args,0,stdout='\n'.join(k+'='+v for k,v in values.items())))
    with pytest.raises(RuntimeError):upgrade.backup_idle()

def test_backup_loaded_inactive_success_allows(monkeypatch):
    monkeypatch.setattr(upgrade,'checked',lambda *args,**kwargs:subprocess.CompletedProcess(args,0,stdout='LoadState=loaded\nActiveState=inactive\nResult=success'))
    upgrade.backup_idle()

@pytest.mark.parametrize('dirty',['tracked','untracked'])
def test_prepare_refuses_dirty_source_before_creating_release(tmp_path,monkeypatch,dirty):
    monkeypatch.setattr(upgrade,'BASE',tmp_path/'releases')
    def checked(argv,**kwargs):
        if argv[1]=='diff' and dirty=='tracked':raise subprocess.CalledProcessError(1,argv)
        return subprocess.CompletedProcess(argv,0,stdout='src/new.py\n' if argv[1]=='ls-files' else '')
    monkeypatch.setattr(upgrade,'checked',checked)
    with pytest.raises((RuntimeError,subprocess.CalledProcessError)):upgrade.prepare(tmp_path)
    assert not (tmp_path/'releases').exists()

@pytest.mark.parametrize('pip_failure',[False,True])
def test_prepare_stages_release_without_stopping_current(tree,monkeypatch,pip_failure):
    current,release,secret,calls=tree
    # The fixture release is removed only inside this temporary test directory.
    (release/'READY.json').unlink();release.rmdir()
    source=Path(__file__).resolve().parents[1]
    commands=[]
    monkeypatch.setattr(upgrade,'trusted',lambda path:None)
    def checked(argv,**kwargs):
        commands.append(argv)
        assert argv[0]!='systemctl'
        if pip_failure and 'pip' in argv:
            raise subprocess.CalledProcessError(1,argv)
        return subprocess.CompletedProcess(argv,0,stdout=SHA+'\n' if argv[:2]==['git','rev-parse'] else '')
    monkeypatch.setattr(upgrade,'checked',checked)
    if pip_failure:
        with pytest.raises(subprocess.CalledProcessError):upgrade.prepare(source)
        assert not (release/'READY.json').exists()
    else:
        assert upgrade.prepare(source)==SHA
        assert json.loads((release/'READY.json').read_text())['sha']==SHA
        assert (release/'src/mayhome_center/snapshot_metadata.py').is_file()
        assert any('-I' in command for command in commands)
    assert not current.is_symlink() and (current/'previous-source').read_text()=='old'
    assert secret.read_text()=='DO NOT CHANGE' and not calls
