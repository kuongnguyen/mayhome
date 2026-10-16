"""Bounded Linux counters. No process enumeration, shell, device writes or extra daemon."""
from datetime import datetime, timezone
import math
from pathlib import Path
import re
import time

POINTS = 120
MAX_GAP = 120
METRICS = ('cpu_percent','memory_percent','temperature_c','disk_read_bps','disk_write_bps',
           'network_down_bps','network_up_bps','uptime_seconds')


def read(path):
    with Path(path).open() as stream:
        value = stream.read(1048577)
    if len(value)>1048576:
        raise ValueError('counter too large')
    return value


def finite(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def cpu(text):
    lines=text.splitlines()
    fields=next(line.split()[1:9] for line in lines if line.startswith('cpu '))
    numbers=[int(value) for value in fields]
    if len(numbers)<5 or any(value<0 for value in numbers):raise ValueError('cpu')
    return {'total':sum(numbers),'idle':numbers[3]+numbers[4],
            'cores':sum(bool(re.match(r'^cpu[0-9]+\s',line)) for line in lines)}


def memory(text):
    values={}
    for line in text.splitlines():
        match=re.fullmatch(r'(MemTotal|MemAvailable|SwapTotal|SwapFree):\s+([0-9]+) kB',line)
        if match:values[match[1]]=int(match[2])*1024
    total,available=values['MemTotal'],values['MemAvailable']
    if total<=0 or not 0<=available<=total:raise ValueError('memory')
    swap_total,swap_free=values.get('SwapTotal'),values.get('SwapFree')
    return {'memory_total':total,'memory_available':available,'memory_used':total-available,
            'memory_percent':round(100*(total-available)/total,2),
            'swap_used':swap_total-swap_free if swap_total is not None and swap_free is not None and 0<=swap_free<=swap_total else None}


def disk_counters(text, names):
    counters={}
    for line in text.splitlines():
        fields=line.split()
        if len(fields)>=10 and fields[2] in names:
            a,b=int(fields[5])*512,int(fields[9])*512
            if a<0 or b<0:raise ValueError('disk')
            counters[fields[2]]=[a,b]
    return counters or None


def network_counters(text, names):
    counters={}
    for line in text.splitlines():
        if ':' not in line:continue
        name,body=line.split(':',1);name=name.strip();fields=body.split()
        if name in names and len(fields)>=16:
            a,b=int(fields[0]),int(fields[8])
            if a<0 or b<0:raise ValueError('network')
            counters[name]=[a,b]
    return counters or None


def pair_rate(current, previous, seconds):
    if not isinstance(current,dict) or not isinstance(previous,dict) or not current or current.keys()!=previous.keys():return None,None
    changes=[0,0]
    for name,values in current.items():
        old=previous[name]
        if not isinstance(old,list) or len(old)!=2:return None,None
        for index in (0,1):
            if not finite(old[index]) or old[index]<0 or values[index]<old[index]:return None,None
            changes[index]+=values[index]-old[index]
    return tuple(round(value/seconds,2) for value in changes)


def temperature():
    # Only CPU/package/SoC sensors, never infer room/disk/GPU temperatures.
    values=[]
    for zone in sorted(Path('/sys/class/thermal').glob('thermal_zone*'))[:32]:
        try:
            kind=read(zone/'type').strip().lower()
            if kind in ('x86_pkg_temp','cpu-thermal','cpu_thermal','soc_thermal'):
                values.append(float(read(zone/'temp'))/1000)
        except (OSError,ValueError):pass
    for hw in sorted(Path('/sys/class/hwmon').glob('hwmon*'))[:32]:
        try:
            if read(hw/'name').strip() in ('coretemp','k10temp','cpu_thermal'):
                values.append(float(read(hw/'temp1_input'))/1000)
        except (OSError,ValueError):pass
    values=[value for value in values if math.isfinite(value) and -20<=value<=150]
    return round(max(values),1) if values else None


def collect(previous=None):
    now=datetime.now(timezone.utc).isoformat();mono=time.monotonic()
    current={key:None for key in METRICS}
    current.update(memory_total=None,memory_used=None,memory_available=None,swap_used=None,cores=None,frequency_mhz=None)
    raw={};errors=[]
    def probe(name,call):
        try:return call()
        except (OSError,ValueError,KeyError,IndexError,StopIteration,OverflowError):
            errors.append(name+'_UNAVAILABLE');return None
    boot=probe('BOOT',lambda:read('/proc/sys/kernel/random/boot_id').strip())
    if not isinstance(boot,str) or not re.fullmatch('[0-9a-f-]{36}',boot):boot=None
    raw['cpu']=probe('CPU',lambda:cpu(read('/proc/stat')))
    if raw['cpu']:current['cores']=raw['cpu']['cores']
    mem=probe('MEMORY',lambda:memory(read('/proc/meminfo')))
    if mem:current.update(mem)
    uptime=probe('UPTIME',lambda:float(read('/proc/uptime').split()[0]))
    current['uptime_seconds']=int(uptime) if finite(uptime) and uptime>=0 else None
    current['temperature_c']=probe('TEMPERATURE',temperature)
    def frequency():
        value=float(read('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq'))/1000
        return round(value,1) if math.isfinite(value) and 0<value<=20000 else None
    current['frequency_mhz']=probe('FREQUENCY',frequency)
    def disks():
        names={p.name for p in sorted(Path('/sys/block').iterdir())[:256] if (p/'device').exists() and re.fullmatch(r'[A-Za-z0-9_-]{1,32}',p.name)}
        return disk_counters(read('/proc/diskstats'),names)
    raw['disk']=probe('DISK',disks)
    def network():
        # Track active non-loopback interfaces individually to detect interface changes/resets.
        names={p.name for p in sorted(Path('/sys/class/net').iterdir())[:256] if p.name!='lo' and read(p/'operstate').strip()=='up'}
        return network_counters(read('/proc/net/dev'),names)
    raw['network']=probe('NETWORK',network)
    old=previous if isinstance(previous,dict) else {}
    seconds=mono-old.get('monotonic',mono) if finite(old.get('monotonic')) else 0
    continuous=boot is not None and old.get('boot_id')==boot and 0<seconds<=MAX_GAP
    if continuous:
        oldraw=old.get('_counters',{})
        if not isinstance(oldraw,dict):oldraw={}
        a,b=raw['cpu'],oldraw.get('cpu')
        if a and isinstance(b,dict) and a['cores']==b.get('cores') and finite(b.get('total')) and finite(b.get('idle')):
            total,idle=a['total']-b['total'],a['idle']-b['idle']
            if total>0 and 0<=idle<=total:current['cpu_percent']=round(100*(total-idle)/total,2)
        current['disk_read_bps'],current['disk_write_bps']=pair_rate(raw['disk'],oldraw.get('disk'),seconds)
        current['network_down_bps'],current['network_up_bps']=pair_rate(raw['network'],oldraw.get('network'),seconds)
    history=[]
    if boot and old.get('boot_id')==boot and isinstance(old.get('history'),list):
        for row in old['history'][-POINTS:]:
            if not isinstance(row,dict):continue
            try:age=(datetime.fromisoformat(now)-datetime.fromisoformat(row['timestamp'])).total_seconds()
            except (ValueError,KeyError,TypeError):continue
            if not 0<age<=3600:continue
            history.append({'timestamp':row['timestamp'],**{key:row.get(key) if finite(row.get(key)) else None for key in METRICS}})
    history.append({'timestamp':now,**{key:current[key] for key in METRICS}})
    return {'version':1,'observed_at':now,'boot_id':boot,'monotonic':mono,'current':current,'history':history[-POINTS:],
            '_counters':raw,'sample_interval_seconds':round(seconds,2) if continuous else None,
            'errors':errors,'network_scope':'active_non_loopback_interfaces','disk_scope':'physical_whole_devices',
            'history_scope':'up_to_1_hour_in_run_tmpfs','rate_state':'sampled' if continuous else 'waiting_for_second_sample'}


def public(hardware, freshness):
    data=hardware if isinstance(hardware,dict) else {}
    return {key:value for key,value in data.items() if key not in ('_counters','boot_id','monotonic')} | {'freshness':freshness}


def series(history, key):
    """SVG segments with a real time axis; nulls and sample gaps break the line."""
    if key not in METRICS or not isinstance(history,list):return []
    rows=[]
    for row in history[-POINTS:]:
        try:stamp=datetime.fromisoformat(row['timestamp']).timestamp()
        except (ValueError,KeyError,TypeError):continue
        rows.append((stamp,row.get(key)))
    if len(rows)<2:return []
    start,end=rows[0][0],rows[-1][0]
    values=[value for _,value in rows if finite(value)]
    if not values or end<=start:return []
    low,high=min(values),max(values);span=max(high-low,1)
    segments=[];points=[];last=None
    for stamp,value in rows:
        if not finite(value) or (last is not None and stamp-last>MAX_GAP):
            if len(points)>1:segments.append(' '.join(points))
            points=[]
        if finite(value):points.append(f'{(stamp-start)/(end-start)*300:.2f},{36-(value-low)/span*28:.2f}')
        last=stamp
    if len(points)>1:segments.append(' '.join(points))
    return segments
