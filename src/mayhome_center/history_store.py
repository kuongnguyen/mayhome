"""MHC-owned bounded SQLite history. Root collector writes; web opens mode=ro."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import time
from .hardware import METRICS, finite

PATH = Path('/var/lib/mayhome-center/hardware.sqlite3')
HOURS = (1,6,24,168,720)
QUOTA = 64*1024*1024
MIN_FREE = 512*1024*1024


def trusted(path, directory=False):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:
        raise ValueError('history path ownership or mode')
    if directory and not stat.S_ISDIR(info.st_mode):raise ValueError('history directory')
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_size>QUOTA):raise ValueError('history file or quota')


def validate(path, existing=True):
    path=Path(path)
    for node in (path.parent,*path.parent.parents):trusted(node,True)
    if existing or path.exists():trusted(path)
    return path


def clean(values):
    return {key:float(values[key]) if finite(values.get(key)) else None for key in METRICS}


def append(hardware, path=PATH, alerts=None):
    path=validate(path,False)
    usage=os.statvfs(path.parent)
    if usage.f_bavail*usage.f_frsize<MIN_FREE:return {'state':'degraded','code':'HISTORY_DISK_PRESSURE'}
    stamp=datetime.fromisoformat(hardware['observed_at']).timestamp()
    boot=hardware.get('boot_id')
    if not isinstance(boot,str) or len(boot)>64 or not boot:return {'state':'degraded','code':'HISTORY_BOOT_UNKNOWN'}
    values=clean(hardware.get('current',{}));bucket=int(stamp)//60*60
    # Parent is root-owned and not writable by the web user. Do not follow caller symlinks.
    descriptor=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o640);os.close(descriptor)
    connection=sqlite3.connect(path,timeout=.15)
    try:
        connection.execute('PRAGMA journal_mode=DELETE')
        connection.execute('PRAGMA synchronous=FULL')
        page_size=connection.execute('PRAGMA page_size').fetchone()[0]
        connection.execute(f'PRAGMA max_page_count={QUOTA//page_size}')
        version=connection.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):raise ValueError('history schema')
        connection.execute('CREATE TABLE IF NOT EXISTS raw (stamp REAL PRIMARY KEY, boot TEXT NOT NULL, values_json TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS minutes (stamp INTEGER PRIMARY KEY, boot TEXT NOT NULL, values_json TEXT NOT NULL)')
        connection.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, values_json TEXT NOT NULL)')
        connection.execute('PRAGMA user_version=1')
        latest=connection.execute('SELECT MAX(stamp) FROM raw').fetchone()[0]
        if latest is not None and stamp<latest-1:
            return {'state':'degraded','code':'HISTORY_CLOCK_REVERSED'}
        with connection:
            connection.execute('DELETE FROM raw WHERE stamp<?',(stamp-86400,))
            connection.execute('DELETE FROM minutes WHERE stamp<?',(stamp-30*86400,))
            inserted=connection.execute('INSERT OR IGNORE INTO raw VALUES (?,?,?)',(stamp,boot,json.dumps(values))).rowcount
            if inserted:
                old=connection.execute('SELECT boot,values_json FROM minutes WHERE stamp=?',(bucket,)).fetchone()
                aggregates=json.loads(old[1]) if old else {key:[0,0] for key in METRICS}
                for key,value in values.items():
                    if value is not None:
                        aggregates[key][0]+=value;aggregates[key][1]+=1
                stored_boot=boot if not old or old[0]==boot else 'mixed'
                connection.execute('INSERT OR REPLACE INTO minutes VALUES (?,?,?)',(bucket,stored_boot,json.dumps(aggregates)))
            if isinstance(alerts,dict):
                text=json.dumps(alerts)
                if len(text)>65536:raise ValueError('alert state bound')
                connection.execute('INSERT OR REPLACE INTO state VALUES (?,?)',('alerts',text))
            # Independent hard row caps also bound a badly configured timer.
            connection.execute('DELETE FROM raw WHERE stamp < (SELECT stamp FROM raw ORDER BY stamp DESC LIMIT 1 OFFSET 3999)')
            connection.execute('DELETE FROM minutes WHERE stamp < (SELECT stamp FROM minutes ORDER BY stamp DESC LIMIT 1 OFFSET 44999)')
        return {'state':'available','code':'HISTORY_PERSISTED','raw_hours':24,'minute_days':30,'quota_bytes':QUOTA}
    finally:connection.close()


def read(hours=1, points=240, path=PATH, now=None):
    if hours not in HOURS or not 30<=points<=240:raise ValueError('history range')
    result={'state':'unavailable','hours':hours,'items':[],'aggregation':'raw' if hours<=24 else 'minute_average',
            'max_gap_seconds':max(120,hours*3600/points*2),'code':'HISTORY_NOT_CONFIGURED'}
    try:
        path=validate(path)
        connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.15)
        deadline=time.monotonic()+1
        connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        try:
            if connection.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('history schema')
            end=now if now is not None else datetime.now(timezone.utc).timestamp()
            table='raw' if hours<=24 else 'minutes';interval=max(1,hours*3600/points)
            groups={};previous=None;previous_boot=None
            for index,(stamp,boot,text) in enumerate(connection.execute(f'SELECT stamp,boot,values_json FROM {table} WHERE stamp>=? AND stamp<=? ORDER BY stamp',(end-hours*3600,end))):
                if index>=45000:raise ValueError('history query limit')
                values=json.loads(text)
                if table=='minutes':values={key:pair[0]/pair[1] if isinstance(pair,list) and len(pair)==2 and pair[1]>0 else None for key,pair in values.items()}
                number=min(points-1,int((stamp-(end-hours*3600))/interval))
                group=groups.setdefault(number,{'stamp':stamp,'sums':{key:[0,0] for key in METRICS},'discontinuous':False})
                group['stamp']=stamp
                if previous is not None and (stamp-previous>120 or boot!=previous_boot or boot=='mixed'):group['discontinuous']=True
                for key,value in clean(values).items():
                    if value is not None:group['sums'][key][0]+=value;group['sums'][key][1]+=1
                previous,previous_boot=stamp,boot
            for group in groups.values():
                result['items'].append({'timestamp':datetime.fromtimestamp(group['stamp'],timezone.utc).isoformat(),
                  'discontinuous':group['discontinuous'],**{key:round(a/b,3) if b else None for key,(a,b) in group['sums'].items()}})
            result.update(state='available',code='HISTORY_READ',aggregation='bucket_averages_'+table)
        finally:connection.close()
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):result.update(state='unavailable',code='HISTORY_READ_UNAVAILABLE',items=[])
    return result


def persist(hardware, alerts=None):
    if os.environ.get('MHC_HISTORY_PERSIST')!='1':return {'state':'disabled','code':'HISTORY_NOT_CONFIGURED'}
    try:return append(hardware,PATH,alerts)
    except (OSError,ValueError,TypeError,KeyError,sqlite3.Error):return {'state':'degraded','code':'HISTORY_WRITE_UNAVAILABLE'}


def load_alerts(path=PATH):
    try:
        path=validate(path)
        connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.15)
        try:
            row=connection.execute("SELECT values_json FROM state WHERE key='alerts'").fetchone()
            if row and len(row[0])<=65536:
                result=json.loads(row[0])
                return result if isinstance(result,dict) else None
        finally:connection.close()
    except (OSError,ValueError,sqlite3.Error):pass
    return None
