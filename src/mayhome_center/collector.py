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
    output = run(['/usr/bin/systemctl', 'show', unit, '--property=LoadState,ActiveState,SubState,Result,UnitFileState,NextElapseUSecRealtime,InvocationID,ExecMainStartTimestampMonotonic,ExecMainExitTimestampMonotonic'])
    props = dict(line.split('=', 1) for line in output.splitlines() if '=' in line)
    def state(key, choices):
        value = props.get(key)
        return value if value in choices else 'unknown'
    def usec(name):
        value = props.get(name, '')
        return int(value) if value.isdigit() and len(value)<=18 else None
    invocation = props.get('InvocationID', '')
    return {'unit_name': unit, 'invocation_id':invocation if re.fullmatch(r'[0-9a-f]{32}',invocation) else None,
            'exec_start_monotonic_usec':usec('ExecMainStartTimestampMonotonic'),
            'exec_exit_monotonic_usec':usec('ExecMainExitTimestampMonotonic'), 'active_state': state('ActiveState', ('active','inactive','failed','activating','deactivating')),
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

def inventory(root=ROOT, max_entries=500):
    from .snapshot_metadata import scan
    try:
        return scan(root, open_dir, max_entries)
    except ValueError as error:
        raise ProbeError('inventory bounded metadata scan failed') from error


def parse_journal(text, max_records=200):
    from .backup_history import parse_events
    return parse_events(text, max_records)


def inventory_cache():
    from .snapshot_metadata import cached_metadata
    try:
        parent = open_dir(OUTPUT.parent, trusted=True)
        try:
            descriptor = os.open(OUTPUT.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        finally:
            os.close(parent)
        with os.fdopen(descriptor,'rb') as stream:
            info = os.fstat(stream.fileno())
            if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode) or info.st_size>1048576:
                return None
            raw = stream.read(1048577)
            if len(raw)>1048576:
                return None
            return cached_metadata(json.loads(raw), utcnow())
    except (OSError, ValueError, ProbeError):
        return None


def nextcloud_status():
    result = {'maintenance':None,'installed':None,'needs_db_upgrade':None,'version':None,'source':'not_enabled'}
    if os.environ.get('MHC_NEXTCLOUD_STATUS') != '1':
        return result
    raw = json.loads(run(['/usr/bin/docker','exec','--user','www-data','nextcloud-app',
                          'php','/var/www/html/occ','status','--output=json'],timeout=5,max_bytes=16384))
    if not isinstance(raw,dict) or any(not isinstance(raw.get(key),bool) for key in ('maintenance','installed','needsDbUpgrade')):
        raise ProbeError('invalid Nextcloud status fields')
    version = raw.get('version')
    result.update(maintenance=raw['maintenance'],installed=raw['installed'],needs_db_upgrade=raw['needsDbUpgrade'],
                  version=version if isinstance(version,str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,4}',version) else None,
                  source='docker_exec_occ_status')
    return result


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
    from .snapshot_metadata import summary
    from .backup_history import reconstruct_jobs, current_execution
    cached = inventory_cache() if all(m['status']=='HEALTHY' for m in storage) else None
    if cached:
        snapshots, inventory_state = cached
    else:
        snapshots = probe('INVENTORY_UNAVAILABLE', inventory, None) if all(m['status']=='HEALTHY' for m in storage) else None
        inventory_state = {**summary(snapshots or [], utcnow().isoformat(), known=snapshots is not None),'cached':False}
        snapshots = snapshots or []
    journal = probe('JOURNAL_UNAVAILABLE',lambda:run(['/usr/bin/journalctl','--unit=nextcloud-auto-backup.service','--since=-30days','--lines=1000','--output=json','--no-pager'],max_bytes=524288), '')
    logs = parse_journal(journal, max_records=1000)
    jobs = reconstruct_jobs(logs)
    nc_status = probe('NEXTCLOUD_STATUS_UNAVAILABLE', nextcloud_status, {'maintenance':None,'source':'unavailable'})
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
              'maintenance':nc_status.get('maintenance'), 'marker_present':marker, 'services':services, 'nextcloud_status':nc_status}
    try:
        current_boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip().replace('-','')
        if not re.fullmatch(r'[0-9a-f]{32}',current_boot):
            current_boot = None
    except OSError:
        current_boot = None
    backup.update(current_execution(engine, logs, time.monotonic_ns()//1000, current_boot))
    from .post_boot import collect_post_boot
    post_boot = collect_post_boot(run)
    for source, connector in post_boot['connectors'].items():
        if connector['status'] != 'HEALTHY':
            errors.append({'code':connector['code'], 'message':'Cloudflare Tunnel (' + source + '): ' + {
                'WARNING':'đang chờ phục hồi trong 5 phút đầu sau boot.',
                'CRITICAL':'connector không hoạt động hoặc không có kết nối Edge, kể cả khi container vẫn Running.',
                'UNKNOWN':'chưa cấu hình hoặc chưa xác minh được kết nối Edge.',
            }[connector['status']]})
    if nc_status.get('maintenance') is None:
        errors.append({'code':'MAINTENANCE_UNKNOWN','message':'Chưa có occ status được bật và xác minh; không suy ra maintenance OFF.'})
    errors.extend([
        {'code':'CHECKSUM_UNKNOWN','message':'Chưa có bằng chứng gzip/SHA256 từ bộ kiểm chứng được phê duyệt.'},
        {'code':'HISTORY_INCOMPLETE','message':'Journal giới hạn 1000 dòng/30 ngày; mỗi job hiển thị độ tin cậy riêng, không đảm bảo lịch sử đầy đủ.'},
        {'code':'RETENTION_UNVERIFIED','message':'Chưa có nguồn xuất trạng thái retention được xác minh.'},
    ])
    data = {
        'host':{'name':'mayhome','observed_name':os.uname().nodename,'timezone':time.tzname[0]},
        'post_boot':post_boot,'backup':backup,'storage':storage,'containers':containers,'snapshots':snapshots,'inventory':inventory_state,
        'logs':list(reversed(logs))[:200], 'jobs':jobs,
        'retention':{'days':14,'mode':'unknown','eligible_count':None,'protected_count':sum(r['protected'] for r in snapshots),
                     'note':'Chính sách 14 ngày từ manifest; chưa xác minh cấu hình thực tế.'}}
    from .backup_diagnostics import diagnostics
    data['diagnostics'] = diagnostics(data)
    return {'schema_version':1,'collector_version':'0.3.0','generated_at':utcnow().isoformat(),'data':data,'errors':errors}

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
