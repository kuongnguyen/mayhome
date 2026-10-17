"""MHC-only reviewed upgrade. Prepare dependencies BEFORE stopping any MHC process."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys

BASE = Path('/opt/mayhome-center-releases')
CURRENT = Path('/opt/mayhome-center')
WEB = 'mayhome-center.service'
TIMER = 'mayhome-center-collector.timer'
COLLECTOR = 'mayhome-center-collector.service'
SHA = re.compile(r'[0-9a-f]{40}')


def checked(argv, **kwargs):
    return subprocess.run(argv, check=True, **kwargs)


def trusted(path):
    path = Path(path)
    for node in (path, *path.parents):
        info = node.lstat()
        if node.is_symlink() or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Release path is not root-owned and protected')


def release_path(sha):
    if not SHA.fullmatch(sha):
        raise ValueError('Expected full lowercase 40-character commit SHA')
    return BASE / sha


def clean_source(source):
    checked(['git','diff','--quiet'],cwd=source)
    checked(['git','diff','--cached','--quiet'],cwd=source)
    output = checked(['git','ls-files','--others','--exclude-standard','--','src','packaging','scripts','pyproject.toml','requirements.lock'],
                     cwd=source,capture_output=True,text=True).stdout
    if output.strip():
        raise RuntimeError('Untracked installation/source files require review')


def prepare(source):
    source = Path(source).resolve()
    clean_source(source)
    sha = checked(['git','rev-parse','--verify','HEAD^{commit}'],cwd=source,capture_output=True,text=True).stdout.strip()
    destination = release_path(sha)
    BASE.mkdir(mode=0o755,exist_ok=True)
    trusted(BASE)
    destination.mkdir(mode=0o755)
    spec = importlib.util.spec_from_file_location('mhc_stage',source/'packaging/stage_source.py')
    stage = importlib.util.module_from_spec(spec);spec.loader.exec_module(stage)
    stage.stage_sources(source/'src',destination/'src')
    for name in ('pyproject.toml','requirements.lock'):
        origin = source/name
        if origin.is_symlink():
            raise RuntimeError('Symlinked project manifest requires review')
        shutil.copyfile(origin,destination/name);(destination/name).chmod(0o644)
    checked(['/usr/bin/python3','-m','venv',str(destination/'.venv')])
    python = str(destination/'.venv/bin/python')
    checked([python,'-m','pip','install','-r',str(destination/'requirements.lock')])
    checked([python,'-m','pip','install','--no-deps',str(destination)])
    checked([python,'-m','pip','check'])
    checked([python,'-I','-c',
             "import sqlite3; c=sqlite3.connect(':memory:'); assert c.execute('select 1').fetchone() == (1,); c.close(); from mayhome_center import create_app; a=create_app({'AUTH_TOKEN':''}); assert a.test_client().get('/healthz').json == {'alive':True}"])
    clean_source(source)
    if checked(['git','rev-parse','HEAD'],cwd=source,capture_output=True,text=True).stdout.strip()!=sha:
        raise RuntimeError('Source HEAD changed during preparation')
    (destination/'READY.json').write_text(json.dumps({'sha':sha,'prepared_at':datetime.now(timezone.utc).isoformat()})+'\n')
    print('Prepared release:',destination)
    print('No running service, unit, secret or collector drop-in changed.')
    return sha


def service_active(unit):
    return subprocess.run(['systemctl','is-active','--quiet',unit]).returncode==0


def backup_idle():
    response = checked(['systemctl','show','nextcloud-auto-backup.service','--property=LoadState,ActiveState,Result'],capture_output=True,text=True)
    values = dict(line.split('=',1) for line in response.stdout.splitlines() if '=' in line)
    if values.get('LoadState')!='loaded' or values.get('ActiveState')!='inactive' or values.get('Result')!='success':
        raise RuntimeError('Backup service is not confirmed loaded/inactive/success; do not activate this upgrade')


def switch(target):
    temporary = CURRENT.parent / ('.mayhome-center-next-' + secrets.token_hex(8))
    try:
        temporary.symlink_to(target,target_is_directory=True)
        os.replace(temporary,CURRENT)
    finally:
        temporary.unlink(missing_ok=True)


def stop_mhc():
    checked(['systemctl','stop',TIMER,WEB])
    checked(['systemctl','stop',COLLECTOR])


def start_mhc(states):
    if states.get('web'):
        checked(['systemctl','start',WEB])
    if states.get('timer'):
        checked(['systemctl','start',TIMER])


def activate(sha):
    destination = release_path(sha)
    trusted(destination)
    ready = json.loads((destination/'READY.json').read_text())
    if ready.get('sha')!=sha:
        raise RuntimeError('Prepared release SHA mismatch')
    if (destination/'ACTIVATION.json').exists():
        raise RuntimeError('Already activated before; inspect state instead of overwriting rollback evidence')
    previous = CURRENT.resolve(strict=True)
    trusted(previous)
    if previous==destination:
        raise RuntimeError('Release is already current')
    backup_idle()
    states = {'web':service_active(WEB),'timer':service_active(TIMER)}
    try:
        stop_mhc()
        # A legacy directory is preserved whole, including its venv. No deletion.
        if not CURRENT.is_symlink():
            preserved = CURRENT.parent / ('mayhome-center-preserved-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4))
            CURRENT.rename(preserved)
            previous = preserved
        record = {'previous_target':str(previous),'states':states,'activated_sha':sha}
        (destination/'ACTIVATION.json').write_text(json.dumps(record)+'\n')
        switch(destination)
        start_mhc(states)
    except BaseException:
        try:
            stop_mhc()
        except Exception:
            pass
        if previous != CURRENT:
            switch(previous)
        start_mhc(states)
        raise
    print('Activated MHC release:',sha)
    print('Verify authenticated readiness, collector, snapshots, UI and unchanged backup state. No production acceptance claimed.')


def rollback(sha):
    destination = release_path(sha)
    trusted(destination)
    if CURRENT.resolve(strict=True)!=destination:
        raise RuntimeError('Requested release is not current; refusing unrelated rollback')
    record = json.loads((destination/'ACTIVATION.json').read_text())
    if record.get('activated_sha')!=sha:
        raise RuntimeError('Rollback evidence mismatch')
    previous = Path(record['previous_target'])
    if previous.parent not in (BASE,CURRENT.parent) or (previous.parent!=BASE and not previous.name.startswith('mayhome-center-preserved-')):
        raise RuntimeError('Rollback path is outside known MHC release locations')
    trusted(previous)
    backup_idle()
    stop_mhc()
    switch(previous)
    start_mhc(record['states'])
    print('MHC rollback completed. Both releases and secret/drop-in files preserved.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','activate','rollback'))
    parser.add_argument('--sha')
    parser.add_argument('--reviewed',action='store_true')
    args = parser.parse_args()
    if os.geteuid()!=0 or os.uname().nodename.split('.')[0]!='mayhome' or not args.reviewed:
        parser.error('Requires administrator on mayhome and --reviewed after source/safety review')
    os.umask(0o022)
    if args.action=='prepare':
        if args.sha:
            parser.error('prepare uses the clean checkout HEAD, not an arbitrary SHA argument')
        prepare(Path(__file__).resolve().parents[1])
    else:
        if not args.sha:
            parser.error('--sha is required')
        {'activate':activate,'rollback':rollback}[args.action](args.sha)

if __name__=='__main__':main()
