"""Conservative job reconstruction. No raw journal MESSAGE is returned."""
from datetime import datetime, timezone
import re

STAGES = ('Pre-sync','Maintenance','Final sync','Database dump','Commit','Recovering')
EVENTS = {'BACKUP START':'Backup bắt đầu','BACKUP PASS':'Engine báo hoàn tất','BACKUP FAILED':'Engine báo thất bại',
          **{stage:stage for stage in STAGES}}
HEX_ID = re.compile(r'[0-9a-f]{32}')


def parse_events(text, max_records=200):
    import json
    events = []
    for line in text.splitlines()[:max_records]:
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                continue
            unit = raw.get('_SYSTEMD_UNIT') or raw.get('UNIT')
            if unit is not None and unit != 'nextcloud-auto-backup.service':
                continue
            message = raw.get('MESSAGE')
            if not isinstance(message, str) or len(message) > 8192:
                continue
            engine = re.search(r'\bNC-BACKUP-RC(1\.[23])\b', message)
            if not engine:
                continue
            event = next((key for key in EVENTS if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', message)), None)
            if event is None:
                continue
            timestamp = datetime.fromtimestamp(int(raw['__REALTIME_TIMESTAMP']) / 1_000_000, timezone.utc)
            row = {'source':'backup','timestamp':timestamp.isoformat(),'event':event,'message':EVENTS[event],
                   'engine':'RC' + engine[1], 'invocation_id':None,'boot_id':None,'monotonic_usec':None}
            for source, target in (('_SYSTEMD_INVOCATION_ID','invocation_id'),('_BOOT_ID','boot_id')):
                value = raw.get(source)
                if isinstance(value, str) and HEX_ID.fullmatch(value):
                    row[target] = value
            monotonic = raw.get('__MONOTONIC_TIMESTAMP')
            if isinstance(monotonic, str) and monotonic.isdigit() and len(monotonic) <= 18:
                row['monotonic_usec'] = int(monotonic)
            events.append(row)
        except (ValueError, KeyError, TypeError, OverflowError, OSError):
            continue
    return sorted(events, key=lambda row:row['timestamp'])


def reconstruct_jobs(events):
    groups = {}
    jobs = []
    for sequence, row in enumerate(events):
        invocation = row.get('invocation_id')
        boot = row.get('boot_id')
        key = (boot, invocation) if boot and invocation else None
        if key is None:
            if row['event'] in ('BACKUP PASS','BACKUP FAILED'):
                jobs.append({'id':f'event-{sequence}','start_at':None,'finish_at':row['timestamp'],
                             'result':'failed' if row['event']=='BACKUP FAILED' else 'reported_success',
                             'stage':'Unknown','duration_sec':None,'source':'journal','confidence':'partial',
                             'engine':row['engine'],'log_available':True,'evidence_code':'JOB_START_OR_ID_UNKNOWN'})
            continue
        groups.setdefault(key, []).append(row)
    for (boot, invocation), rows in groups.items():
        starts = [row for row in rows if row['event']=='BACKUP START']
        finishes = [row for row in rows if row['event'] in ('BACKUP PASS','BACKUP FAILED')]
        # Ambiguous/repeated lifecycle events never produce a verified duration or complete confidence.
        start = starts[0] if len(starts)==1 else None
        finish = finishes[0] if len(finishes)==1 else None
        duration = None
        if start and finish and start['engine']==finish['engine']:
            a, b = start.get('monotonic_usec'), finish.get('monotonic_usec')
            if isinstance(a,int) and isinstance(b,int) and b >= a and start['timestamp'] <= finish['timestamp']:
                duration = round((b-a)/1_000_000, 3)
        complete = duration is not None
        result = 'unknown'
        if finishes:
            result = 'failed' if finishes[-1]['event']=='BACKUP FAILED' else 'success' if complete else 'reported_success'
        stage = next((row['event'] for row in reversed(rows) if row['event'] in STAGES), 'Unknown')
        jobs.append({'id':invocation + '-' + boot,'start_at':start['timestamp'] if start else None,
                     'finish_at':finishes[-1]['timestamp'] if finishes else None,'result':result,'stage':stage,
                     'duration_sec':duration,'source':'journal','confidence':'complete' if complete else 'partial',
                     'engine':rows[-1]['engine'],'log_available':True,
                     'evidence_code':'JOB_LIFECYCLE_OBSERVED' if complete else 'JOB_LIFECYCLE_INCOMPLETE'})
    jobs.sort(key=lambda row:row.get('finish_at') or row.get('start_at') or '', reverse=True)
    return jobs[:500]


def current_execution(service, events, monotonic_usec, boot_id=None):
    invocation = service.get('invocation_id')
    start = service.get('exec_start_monotonic_usec')
    finish = service.get('exec_exit_monotonic_usec')
    result = {'stage':'Unknown','elapsed_sec':None,'last_execution_duration_sec':None,
              'duration_source':'systemd_process_execution'}
    if service.get('active_state') == 'active':
        if isinstance(start,int) and 0 < start <= monotonic_usec:
            result['elapsed_sec'] = round((monotonic_usec-start)/1_000_000, 1)
        relevant = [row for row in events if invocation and row.get('invocation_id')==invocation
                    and boot_id and row.get('boot_id')==boot_id
                    and isinstance(start,int) and isinstance(row.get('monotonic_usec'),int)
                    and start <= row['monotonic_usec'] <= monotonic_usec]
        result['stage'] = next((row['event'] for row in reversed(relevant) if row['event'] in STAGES), 'Unknown')
    elif isinstance(start,int) and isinstance(finish,int) and 0 < start <= finish <= monotonic_usec:
        result['last_execution_duration_sec'] = round((finish-start)/1_000_000, 3)
    return result


def grammar_summary(text, events):
    import json
    tagged=0
    for line in text.splitlines()[:1000]:
        try:
            message=json.loads(line).get('MESSAGE','')
            if isinstance(message,str) and re.search(r'\bNC-BACKUP-RC1\.[23]\b',message):tagged+=1
        except (ValueError,AttributeError):pass
    counts={name:sum(row['event']==name for row in events) for name in EVENTS}
    return {'source':'bounded_journal_30_days','tagged_lines':tagged,'recognized_events':len(events),
            'unrecognized_tagged_lines':max(0,tagged-len(events)),'events':counts,
            'with_invocation_id':sum(row.get('invocation_id') is not None for row in events),
            'with_boot_id':sum(row.get('boot_id') is not None for row in events),
            'start_observed':counts['BACKUP START']>0,'note':'Không xuất MESSAGE thô; thiếu START/ID thì thời lượng chưa xác minh.'}
