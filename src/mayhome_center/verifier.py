"""Independent, operator-run verification. Never imports into the web or periodic collector."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import stat
import time
import zlib
from .collector import mount_status, unit_status, run, ProbeError
from .domain import MOUNTS

ROOT=Path('/mnt/nextcloud_backup/nextcloud')
LOCK=Path('/run/mayhome-center-verifier/verify.lock')
STAMP=re.compile(r'[0-9]{8}-[0-9]{6}')
FIXED='20261015-210345'
ZONE=timezone(timedelta(hours=7))
VERSION='0.1.0'


class Inconclusive(Exception):pass
class InvalidData(Exception):pass


def stamp(value):
    if not STAMP.fullmatch(value):raise Inconclusive('INVALID_SNAPSHOT_ID')
    try:return datetime.strptime(value,'%Y%m%d-%H%M%S').replace(tzinfo=ZONE)
    except ValueError:raise Inconclusive('INVALID_SNAPSHOT_DATE')


def fingerprint(info):
    return {'device':info.st_dev,'inode':info.st_ino,'size':info.st_size,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns}


def protected(info,directory=False):
    if info.st_uid!=0 or info.st_mode&0o022:raise Inconclusive('UNTRUSTED_BACKUP_PERMISSIONS')
    if directory and not stat.S_ISDIR(info.st_mode):raise Inconclusive('NOT_DIRECTORY')
    if not directory and not stat.S_ISREG(info.st_mode):raise Inconclusive('NOT_REGULAR_FILE')


def flags(directory=False):
    if not hasattr(os,'O_NOATIME'):raise Inconclusive('NOATIME_UNSUPPORTED')
    return os.O_RDONLY|os.O_NOFOLLOW|os.O_NOATIME|os.O_NONBLOCK|(os.O_DIRECTORY if directory else 0)


def directory(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts:raise Inconclusive('INVALID_ROOT')
    fd=os.open('/',flags(True))
    try:
        protected(os.fstat(fd),True)
        for part in path.parts[1:]:
            child=os.open(part,flags(True),dir_fd=fd)
            try:protected(os.fstat(child),True)
            except BaseException:os.close(child);raise
            os.close(fd);fd=child
        return fd
    except BaseException:os.close(fd);raise


def artifact(parent,relative):
    parts=relative.split('/');fd=os.dup(parent)
    try:
        for part in parts[:-1]:
            child=os.open(part,flags(True),dir_fd=fd)
            try:protected(os.fstat(child),True)
            except BaseException:os.close(child);raise
            os.close(fd);fd=child
        result=os.open(parts[-1],flags(),dir_fd=fd)
        try:protected(os.fstat(result))
        except BaseException:os.close(result);raise
        return result
    finally:os.close(fd)


def small(fd,limit):
    if os.fstat(fd).st_size>limit:raise Inconclusive('METADATA_TOO_LARGE')
    body=os.pread(fd,limit+1,0)
    if len(body)>limit:raise Inconclusive('METADATA_TOO_LARGE')
    return body


@contextmanager
def selected(root,snapshot):
    stamp(snapshot);fds=[]
    try:
        base=directory(root/'snapshots'/snapshot);fds.append(base)
        initial={'directory':fingerprint(os.fstat(base))}
        complete=artifact(base,'COMPLETE');fds.append(complete)
        initial['complete']=fingerprint(os.fstat(complete))
        if small(complete,256).strip()!=('COMPLETE '+snapshot).encode():raise Inconclusive('INVALID_COMPLETE_MARKER')
        manifest=artifact(base,'manifests/database.sha256');fds.append(manifest)
        initial['manifest']=fingerprint(os.fstat(manifest))
        body=small(manifest,4096)
        match=re.fullmatch(rb'([0-9a-f]{64})[ \t]+\*?(?:database/)?nextcloud\.sql\.gz(?:\r?\n)?',body)
        if not match:raise Inconclusive('INVALID_SHA256_MANIFEST')
        sql=artifact(base,'database/nextcloud.sql.gz');fds.append(sql)
        initial['sql']=fingerprint(os.fstat(sql))
        for name,fd in [('directory',base),('complete',complete),('manifest',manifest),('sql',sql)]:
            if fingerprint(os.fstat(fd))!=initial[name]:raise Inconclusive('METADATA_CHANGED')
        yield {'fds':{'directory':base,'complete':complete,'manifest':manifest,'sql':sql},
               'fingerprints':initial,
               'expected_sha256':match[1].decode(),'manifest_sha256':hashlib.sha256(body).hexdigest()}
    finally:
        for fd in reversed(fds):os.close(fd)


def safety():
    # Reproduce checks directly; do not execute the existing guard or retention producer.
    for path,uuid in MOUNTS.items():
        if mount_status(path,uuid).get('status')!='HEALTHY':raise Inconclusive('MOUNT_NOT_VERIFIED')
    engine=unit_status('nextcloud-auto-backup.service')
    if engine.get('load_state')!='loaded' or engine.get('active_state')!='inactive' or engine.get('result')!='success':raise Inconclusive('BACKUP_NOT_IDLE_SUCCESS')
    marker=Path('/var/lib/nextcloud-auto-backup/maintenance-owned')
    try:marker.lstat()
    except FileNotFoundError:pass
    else:raise Inconclusive('RECOVERY_MARKER_PRESENT')
    timer=unit_status('nextcloud-auto-backup.timer');due=timer.get('next_run_epoch')
    if timer.get('load_state')!='loaded' or not isinstance(due,(int,float)) or not math.isfinite(due):raise Inconclusive('BACKUP_SCHEDULE_UNKNOWN')
    if due<=time.time()+2100:raise Inconclusive('BACKUP_WINDOW_TOO_CLOSE')
    source=directory(Path('/mnt/nextcloud_storage'))
    try:
        data=os.open('nextcloud-data',flags(True),dir_fd=source)
        try:
            if not stat.S_ISREG(os.stat('.ncdata',dir_fd=data,follow_symlinks=False).st_mode):raise Inconclusive('SOURCE_MARKER_INVALID')
        finally:os.close(data)
    finally:os.close(source)


@contextmanager
def own_lock():
    parent=LOCK.parent
    parent.mkdir(mode=0o750,exist_ok=True)
    directory_fd=directory(parent)
    try:
        fd=os.open(LOCK.name,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=directory_fd)
        try:
            protected(os.fstat(fd))
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Inconclusive('VERIFIER_ALREADY_RUNNING')
            yield
        finally:os.close(fd)
    finally:os.close(directory_fd)


def inspect(snapshot,root=ROOT,check=safety,compressed_limit=4*1024**3,rate=4*1024**2):
    check()
    with selected(root,snapshot) as item:
        size=item['fingerprints']['sql']['size']
        check()
        for name,fd in item['fds'].items():
            if fingerprint(os.fstat(fd))!=item['fingerprints'][name]:raise Inconclusive('SOURCE_CHANGED')
        return {'snapshot_id':snapshot,'compressed_bytes':size,'manifest_format_valid':True,'complete_marker_valid':True,
                'within_compressed_budget':size<=compressed_limit,'minimum_read_seconds':round(size/rate,2),
                'fingerprints':item['fingerprints'],'manifest_sha256':item['manifest_sha256'],
                'status':'PLAN_ONLY','sql_content_read':False,'db_checksum_verified':False,'restoration_verified':False}


def verify(snapshot,root=ROOT,check=safety,rate=4*1024**2,compressed_limit=4*1024**3,expanded_limit=64*1024**3,seconds=1800):
    started=time.monotonic();last_guard=started;count=expanded=members=0
    report={'schema_version':1,'verifier_version':VERSION,'mode':'verify','snapshot_id':snapshot,'started_at':datetime.now(timezone.utc).isoformat(),
            'status':'INCONCLUSIVE','db_checksum_verified':False,'restoration_verified':False,'sha256_match':None,'gzip_valid':None,
            'limits':{'read_bytes_per_second':rate,'compressed_bytes':compressed_limit,'expanded_bytes':expanded_limit,'seconds':seconds},
            'coordination':'own_lock_only; engine idleness polled; no atomic engine exclusion'}
    def budget():
        nonlocal last_guard
        if time.monotonic()-started>seconds:raise Inconclusive('TIME_BUDGET_EXCEEDED')
        if time.monotonic()-last_guard>=2:
            check();last_guard=time.monotonic()
    try:
        check()
        with selected(root,snapshot) as item:
            report['source_fingerprints']=item['fingerprints'];report['manifest_sha256']=item['manifest_sha256']
            if item['fingerprints']['sql']['size']>compressed_limit:raise Inconclusive('COMPRESSED_BUDGET_EXCEEDED')
            digest=hashlib.sha256();decoder=None;decode_error=False;read_started=time.monotonic()
            while True:
                budget();block=os.read(item['fds']['sql'],min(65536,compressed_limit-count+1))
                if not block:break
                count+=len(block)
                if count>compressed_limit:raise Inconclusive('COMPRESSED_BUDGET_EXCEEDED')
                digest.update(block)
                pause=read_started+count/rate-time.monotonic()
                if pause>0:time.sleep(pause)
                pending=b'' if decode_error else block
                while pending:
                    budget()
                    if decoder is None:
                        members+=1
                        if members>1024:raise Inconclusive('GZIP_MEMBER_LIMIT')
                        decoder=zlib.decompressobj(31)
                    try:output=decoder.decompress(pending,min(65536,expanded_limit-expanded+1))
                    except zlib.error:
                        decode_error=True;pending=b'';break
                    expanded+=len(output)
                    if expanded>expanded_limit:raise Inconclusive('EXPANDED_BUDGET_EXCEEDED')
                    if decoder.eof:
                        pending=decoder.unused_data;decoder=None
                    else:
                        tail=decoder.unconsumed_tail
                        if tail==pending and not output:
                            decode_error=True;pending=b'';break
                        pending=tail
            gzip_ok=not decode_error and members>0 and decoder is None
            # Stability checks precede PASS *or* corruption/mismatch conclusions.
            check()
            for name,fd in item['fds'].items():
                if fingerprint(os.fstat(fd))!=item['fingerprints'][name]:raise Inconclusive('SOURCE_CHANGED')
            with selected(root,snapshot) as fresh:
                if fresh['fingerprints']!=item['fingerprints'] or fresh['manifest_sha256']!=item['manifest_sha256']:raise Inconclusive('SOURCE_PATH_CHANGED')
            if count!=item['fingerprints']['sql']['size']:raise Inconclusive('SQL_SIZE_CHANGED')
            report.update(sha256_match=digest.hexdigest()==item['expected_sha256'],gzip_valid=gzip_ok,computed_sha256=digest.hexdigest())
            budget()
            if not gzip_ok:raise InvalidData('GZIP_INVALID' if decode_error else 'GZIP_TRUNCATED_OR_EMPTY')
            if not report['sha256_match']:raise InvalidData('SHA256_MISMATCH')
            report.update(status='PASS',code='SHA256_AND_GZIP_VERIFIED',db_checksum_verified=True)
    except zlib.error:
        # Without a stable post-read identity, a decoder error is inconclusive, not proof of corruption.
        report.update(status='INCONCLUSIVE',code='GZIP_DECODE_ERROR_REVIEW_SOURCE',gzip_valid=False)
    except InvalidData as error:report.update(status='FAILED',code=str(error))
    except Inconclusive as error:report.update(status='INCONCLUSIVE',code=str(error))
    except MemoryError:report.update(status='INCONCLUSIVE',code='MEMORY_BUDGET_EXCEEDED')
    except (OSError,ValueError,ProbeError):report.update(status='INCONCLUSIVE',code='SOURCE_OR_GUARD_UNAVAILABLE')
    report.update(compressed_bytes_read=count,expanded_bytes_processed=expanded,gzip_members=members,
                  elapsed_seconds=round(time.monotonic()-started,3),finished_at=datetime.now(timezone.utc).isoformat())
    return report


def projection(root=ROOT,check=safety,now=None,days=14):
    if not isinstance(days,int) or not 14<=days<=365:raise Inconclusive('INVALID_RETENTION_DAYS')
    check();now=now or datetime.now(timezone.utc);rows=[];complete=True;fd=directory(root/'snapshots');started=time.monotonic()
    try:
        with os.scandir(fd) as entries:
            for index,entry in enumerate(entries):
                if index>=500 or time.monotonic()-started>2:raise Inconclusive('INVENTORY_BUDGET_EXCEEDED')
                safe_name=entry.name if STAMP.fullmatch(entry.name) else ('staging-entry-' if entry.name.startswith('.staging-') else 'unrecognized-entry-')+str(index)
                row={'snapshot_id':safe_name,'protected':True,'reason':'unknown_preserve','age_candidate_pending_integrity':False}
                rows.append(row)
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):complete=False;continue
                if entry.name.startswith('.staging-'):
                    row['reason']='staging_preserve';continue
                try:
                    when=stamp(entry.name)
                    if when>now:raise Inconclusive('FUTURE_DATE')
                    child=os.open(entry.name,flags(True),dir_fd=fd)
                    try:
                        protected(os.fstat(child),True)
                        marker=artifact(child,'COMPLETE')
                        try:valid=small(marker,256).strip()==('COMPLETE '+entry.name).encode()
                        finally:os.close(marker)
                    finally:os.close(child)
                    if not valid:raise Inconclusive('INVALID_COMPLETE')
                    row.update(complete_marker_valid=True,age_seconds=(now-when).total_seconds(),protected=False,reason='within_retention')
                except (OSError,ValueError,Inconclusive):complete=False
        confirmed=sorted((r for r in rows if r.get('complete_marker_valid')),key=lambda r:r['snapshot_id'],reverse=True)
        latest={r['snapshot_id'] for r in confirmed[:2]}
        for row in rows:
            if row['snapshot_id']==FIXED:row.update(protected=True,reason='fixed_checkpoint')
            elif row['snapshot_id'] in latest:row.update(protected=True,reason='latest_two_complete_markers')
            elif not complete:row.update(protected=True,reason='inventory_incomplete_preserve')
            elif complete and row.get('complete_marker_valid') and row['age_seconds']>days*86400:
                row.update(reason='old_enough_pending_integrity',age_candidate_pending_integrity=True)
        check()
        return {'schema_version':1,'verifier_version':VERSION,'mode':'retention_projection','status':'PROJECTION_ONLY' if complete else 'INCONCLUSIVE',
                'observed_at':now.isoformat(),'days':days,'age_rule':'directory_name_UTC+07; age > days*86400',
                'baseline':'outside snapshots, always protected','inventory_complete':complete,'items':rows,
                'age_candidate_count':sum(r['age_candidate_pending_integrity'] for r in rows) if complete else None,
                'producer_dry_run_executed':False,'sql_content_read':False,'retention_verified':False,'deletion_supported':False}
    finally:os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('plan','verify','retention-projection'))
    parser.add_argument('--snapshot');parser.add_argument('--run',action='store_true',help='Explicitly read the selected SQL gzip, never delete')
    parser.add_argument('--read-mib-per-second',type=float,default=4)
    parser.add_argument('--max-seconds',type=int,default=1800)
    parser.add_argument('--max-compressed-mib',type=int,default=4096)
    parser.add_argument('--max-expanded-mib',type=int,default=65536)
    parser.add_argument('--days',type=int,default=14)
    args=parser.parse_args()
    if os.geteuid()!=0 or os.uname().nodename.split('.')[0]!='mayhome':parser.error('Requires administrator on mayhome')
    if not math.isfinite(args.read_mib_per_second) or not .25<=args.read_mib_per_second<=4:parser.error('Rate must be 0.25..4 MiB/s')
    if not 10<=args.max_seconds<=1800 or not 1<=args.max_compressed_mib<=4096 or not 1<=args.max_expanded_mib<=65536:parser.error('Budget outside supported bounds')
    if args.mode in ('plan','verify') and not args.snapshot:parser.error('--snapshot required')
    if args.mode=='verify' and not args.run:parser.error('verify requires --run after reviewing plan')
    if args.run and args.mode!='verify':parser.error('--run only applies to verify')
    if not 14<=args.days<=365:parser.error('--days must be 14..365')
    signal.signal(signal.SIGALRM,lambda *_:(_ for _ in ()).throw(Inconclusive('WALL_TIME_LIMIT')))
    signal.setitimer(signal.ITIMER_REAL,args.max_seconds)
    result=None
    try:
        if args.mode=='verify':
            import resource
            resource.setrlimit(resource.RLIMIT_AS,(256*1024**2,256*1024**2))
            signal.signal(signal.SIGXCPU,lambda *_:(_ for _ in ()).throw(Inconclusive('CPU_BUDGET_EXCEEDED')))
            resource.setrlimit(resource.RLIMIT_CPU,(120,121));os.nice(19)
        with own_lock():
            if args.mode=='plan':result=inspect(args.snapshot,compressed_limit=args.max_compressed_mib*1024**2,rate=args.read_mib_per_second*1024**2)
            elif args.mode=='verify':result=verify(args.snapshot,rate=args.read_mib_per_second*1024**2,compressed_limit=args.max_compressed_mib*1024**2,expanded_limit=args.max_expanded_mib*1024**2,seconds=args.max_seconds)
            else:result=projection(days=args.days)
    except Inconclusive as error:result={'status':'INCONCLUSIVE','code':str(error),'db_checksum_verified':False,'retention_verified':False}
    except MemoryError:result={'status':'INCONCLUSIVE','code':'MEMORY_BUDGET_EXCEEDED','db_checksum_verified':False}
    except (OSError,ValueError,ProbeError):result={'status':'INCONCLUSIVE','code':'SOURCE_OR_GUARD_UNAVAILABLE','db_checksum_verified':False,'retention_verified':False}
    finally:signal.setitimer(signal.ITIMER_REAL,0)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result.get('status') in ('PASS','PROJECTION_ONLY','PLAN_ONLY') else 2

if __name__=='__main__':raise SystemExit(main())
