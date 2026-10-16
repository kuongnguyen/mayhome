"""Fixed read-only host probes. No web imports, commands or paths from requests."""
import json
import os
import re
import selectors
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from .domain import CONTAINERS, MOUNTS, UNITS, SNAPSHOT_PATTERN, utcnow

ROOT = Path('/mnt/nextcloud_backup/nextcloud')
OUTPUT = Path('/run/mayhome-center/status.json')
LIMIT = 262144

class ProbeError(RuntimeError):
    pass

def run(argv, timeout=3, max_bytes=LIMIT):
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'},
                          start_new_session=True) as process:
        chunks = bytearray()
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            try:
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ProbeError('timeout')
                    if not selector.select(remaining):
                        raise ProbeError('timeout')
                    block = os.read(process.stdout.fileno(), min(8192, max_bytes + 1 - len(chunks)))
                    if not block:
                        break
                    chunks.extend(block)
                    if len(chunks) > max_bytes:
                        raise ProbeError('oversize')
                if process.wait(timeout=max(.01, deadline - time.monotonic())):
                    raise ProbeError('command failed')
                return chunks.decode('utf-8', errors='strict')
            except BaseException:
                import signal
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise

def unit_status(unit):
    if unit not in UNITS:
        raise ProbeError('unit not allowed')
    output = run(['/usr/bin/systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState,Result,UnitFileState,NextElapseUSecRealtime'])
    props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    def state(key, choices):
        value = props.get(key)
        return value if value in choices else 'unknown'
    return {'unit_name': unit, 'active_state': state('ActiveState', ('active','inactive','failed','activating','deactivating')),
            'sub_state': state('SubState', ('running','waiting','dead','exited','failed','elapsed')),
            'result': state('Result', ('success','exit-code','timeout','signal','core-dump','resources')),
            'enabled_state': state('UnitFileState', ('enabled','disabled','static','masked','indirect')),
            'next_run': props.get('NextElapseUSecRealtime', '')[:100] or None,
            'load_state': state('LoadState', ('loaded','not-found','masked'))}

def mount_status(path, expected):
    result = {'mountpoint': path, 'expected_uuid': expected, 'status': 'UNKNOWN'}
    mounted = json.loads(run(['/usr/bin/findmnt', '--json', '--task', '1', '--mountpoint', path, '--output', 'TARGET,UUID,FSTYPE,OPTIONS']))['filesystems']
    if len(mounted) != 1 or mounted[0]['target'] != path:
        result['status'] = 'CRITICAL'
        return result
    item = mounted[0]
    rw = 'rw' in item.get('options', '').split(',')
    result.update(observed_uuid=item.get('uuid'), filesystem=item.get('fstype'), rw=rw,
                  status='HEALTHY' if item.get('uuid') == expected and rw else 'CRITICAL')
    if result['status'] == 'HEALTHY':
        usage = os.statvfs(path)
        result.update(total_bytes=usage.f_blocks * usage.f_frsize,
                      used_bytes=(usage.f_blocks - usage.f_bfree) * usage.f_frsize,
                      available_bytes=usage.f_bavail * usage.f_frsize)
    return result

def open_dir(path, trusted=False):
    """Open each path component without following symlinks (including parents)."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ProbeError('invalid path')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
            if trusted:
                info = os.fstat(fd)
                if info.st_uid != 0 or info.st_mode & 0o022:
                    raise ProbeError('untrusted ancestor')
        return fd
    except BaseException:
        os.close(fd)
        raise

def inventory(root=ROOT, max_entries=1000):
    rows = []
    base = open_dir(root, trusted=(root == ROOT))
    try:
        # Baseline is observed by fixed metadata filenames; no user data traversal.
        baseline = False
        try:
            db = os.open('database', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
            try:
                info = os.stat('nextcloud-20261015-174303.sql.gz', dir_fd=db, follow_symlinks=False)
                baseline = stat.S_ISREG(info.st_mode)
            finally:
                os.close(db)
        except OSError:
            pass
        if baseline:
            rows.append({'id':'baseline','type':'baseline','completeness':'unknown','integrity_level':'present',
                         'protected':True,'protection_reasons':['baseline'],'metadata_errors':[]})
        snapshots = os.open('snapshots', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base)
        try:
            deadline = time.monotonic() + 2
            with os.scandir(snapshots) as entries:
                for count, entry in enumerate(entries):
                    if count >= max_entries or time.monotonic() > deadline:
                        raise ProbeError('inventory limit')
                    if not re.fullmatch(SNAPSHOT_PATTERN, entry.name) or entry.name == 'baseline':
                        continue
                    if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                        continue
                    child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=snapshots)
                    try:
                        staging = entry.name.startswith('.staging-')
                        try:
                            complete = stat.S_ISREG(os.stat('COMPLETE', dir_fd=child, follow_symlinks=False).st_mode) and not staging
                        except OSError:
                            complete = False
                        fixed = entry.name == '20261015-210345'
                        rows.append({'id':entry.name,'type':'staging' if staging else 'checkpoint',
                            'completeness':'complete' if complete else 'incomplete',
                            'integrity_level':'present' if complete else 'unknown','protected':fixed,
                            'protection_reasons':['fixed_checkpoint'] if fixed else [],'metadata_errors':[]})
                    finally:
                        os.close(child)
        finally:
            os.close(snapshots)
    finally:
        os.close(base)
    rows.sort(key=lambda row: row['id'], reverse=True)
    completed = [r for r in rows if r['type'] == 'checkpoint' and r['completeness'] == 'complete']
    for row in completed[:2]:
        row['protected'] = True
        row['protection_reasons'].append('latest_two_completed')
    return rows

EVENTS = {'BACKUP PASS':'Backup hoàn tất (theo journal)', 'BACKUP FAILED':'Backup thất bại',
          'Pre-sync':'Pre-sync','Final sync':'Final sync','Database dump':'Database dump',
          'Maintenance':'Maintenance','Commit':'Commit','Recovering':'Recovering'}

def parse_journal(text):
    logs = []
    for line in text.splitlines()[:200]:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            message = row.get('MESSAGE', '')
            if not isinstance(message, str) or len(message) > 8192:
                continue
            if 'NC-BACKUP-RC1.3' not in message and 'NC-BACKUP-RC1.2' not in message:
                continue
            event = next((key for key in EVENTS if key in message), None)
            if event:
                stamp = int(row['__REALTIME_TIMESTAMP']) / 1000000
                from datetime import datetime, timezone
                logs.append({'source':'backup','timestamp':datetime.fromtimestamp(stamp, timezone.utc).isoformat(),
                             'event':event,'message':EVENTS[event],
                             'engine':'RC1.3' if 'NC-BACKUP-RC1.3' in message else 'RC1.2'})
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            continue
    return logs

def collect():
    errors = []
    def probe(code, operation, fallback):
        try:
            return operation()
        except (OSError, ValueError, KeyError, TypeError, ProbeError, subprocess.SubprocessError):
            errors.append({'code':code,'message':'Nguồn dữ liệu không khả dụng hoặc bị giới hạn.'})
            return fallback
    services = [probe('SYSTEMD_UNAVAILABLE', lambda u=u:unit_status(u), {'unit_name':u,'active_state':'unknown'}) for u in UNITS]
    storage = [probe('MOUNT_UNAVAILABLE',lambda p=p,u=u:mount_status(p,u), {'mountpoint':p,'expected_uuid':u,'status':'CRITICAL'}) for p,u in MOUNTS.items()]
    for mount in storage:
        if mount['status'] == 'CRITICAL':
            errors.append({'code':'MOUNT_UUID_MISMATCH','message':'Mount thiếu, sai UUID, chỉ đọc hoặc chưa thể xác minh.'})
    containers = []
    for name in CONTAINERS:
        raw = probe('DOCKER_UNAVAILABLE',lambda n=name:run(['/usr/bin/docker','inspect','--type','container','--format','{{json .State.Running}}',n]), '')
        containers.append({'name':name,'running':True if raw.strip() == 'true' else False if raw.strip() == 'false' else None})
    snapshots = probe('INVENTORY_UNAVAILABLE', inventory, []) if all(m['status']=='HEALTHY' for m in storage) else []
    journal = probe('JOURNAL_UNAVAILABLE',lambda:run(['/usr/bin/journalctl','--unit=nextcloud-auto-backup.service','--since=-30days','--lines=200','--output=json','--no-pager']), '')
    logs = parse_journal(journal)
    jobs = [{'id':str(i), 'finish_at':e['timestamp'], 'result':'failed' if e['event']=='BACKUP FAILED' else 'reported_success',
             'stage':'Unknown','source':'journal','duration_sec':None,'confidence':'partial','engine':e['engine']} for i,e in enumerate(logs) if e['event'] in ('BACKUP PASS','BACKUP FAILED')]
    def marker_status():
        parent = open_dir('/var/lib/nextcloud-auto-backup', trusted=True)
        try:
            try:
                os.stat('maintenance-owned', dir_fd=parent, follow_symlinks=False)
                return True
            except FileNotFoundError:
                return False
        finally:
            os.close(parent)
    marker = probe('MARKER_UNREADABLE', marker_status, None)
    # A missing marker is distinct from unreadable parent; do not claim absent from access failure.
    engine = services[0]
    backup = {'active_state':engine['active_state'], 'result':'failed' if engine.get('active_state')=='failed' or engine.get('result') in ('exit-code','timeout','signal','core-dump','resources') else 'unknown',
              'next_run':services[1].get('next_run'), 'stage':'Unknown', 'elapsed_sec':None,
              'maintenance':None, 'marker_present':marker, 'services':services}
    from .post_boot import collect_post_boot
    post_boot = collect_post_boot(run)
    for source, connector in post_boot['connectors'].items():
        if connector['status'] != 'HEALTHY':
            errors.append({'code':connector['code'], 'message':'Cloudflare Tunnel (' + source + '): ' + {
                'WARNING':'đang chờ phục hồi trong 5 phút đầu sau boot.',
                'CRITICAL':'connector không hoạt động hoặc không có kết nối Edge, kể cả khi container vẫn Running.',
                'UNKNOWN':'chưa cấu hình hoặc chưa xác minh được kết nối Edge.',
            }[connector['status']]})
    errors.extend([
        {'code':'CHECKSUM_UNKNOWN','message':'Chưa có bằng chứng gzip/SHA256 từ bộ kiểm chứng được phê duyệt.'},
        {'code':'MAINTENANCE_UNKNOWN','message':'Collector không chạy occ; trạng thái maintenance chưa xác minh.'},
        {'code':'HISTORY_INCOMPLETE','message':'Journal giới hạn 200 dòng; chưa xác nhận thời lượng và độ đầy đủ lịch sử.'},
        {'code':'RETENTION_UNVERIFIED','message':'Chưa có nguồn xuất trạng thái retention được xác minh.'},
    ])
    return {'schema_version':1,'generated_at':utcnow().isoformat(),'data':{
        'host':{'name':'mayhome','observed_name':os.uname().nodename,'timezone':time.tzname[0]},
        'post_boot':post_boot,'backup':backup,'storage':storage,'containers':containers,'snapshots':snapshots,
        'logs':list(reversed(logs)), 'jobs':list(reversed(jobs)),
        'retention':{'days':14,'mode':'unknown','eligible_count':None,'protected_count':sum(r['protected'] for r in snapshots),
                     'note':'Chính sách 14 ngày từ manifest; chưa xác minh cấu hình thực tế.'}},'errors':errors}

def publish(payload):
    parent = OUTPUT.parent
    fd = open_dir(parent, trusted=True)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ProbeError('unsafe output directory')
        temporary = '.status-' + os.urandom(12).hex()
        out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640, dir_fd=fd)
        try:
            os.fchown(out, 0, info.st_gid)
            with os.fdopen(out, 'w') as stream:
                json.dump(payload, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, OUTPUT.name, src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
    finally:
        os.close(fd)

if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Collector requires separately reviewed root-owned service installation.')
    publish(collect())
