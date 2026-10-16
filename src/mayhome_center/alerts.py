"""Deterministic local alerts. No external notification or management actions."""
from datetime import datetime, timezone


def evaluate(data, now=None, previous=None):
    now=now or datetime.now(timezone.utc);items=[]
    def add(code,severity,message,resource):
        items.append({'id':code+':'+resource,'code':code,'severity':severity,'message':message,'resource':resource})
    backup=data.get('backup',{});active=backup.get('active_state')=='active'
    jobs=data.get('jobs',[])
    if backup.get('result')=='failed' or (jobs and jobs[0].get('result')=='failed'):
        add('BACKUP_FAILED','CRITICAL','Engine hoặc phiên gần nhất báo thất bại.','backup')
    if active and isinstance(backup.get('elapsed_sec'),(int,float)) and backup['elapsed_sec']>7200:
        add('BACKUP_LONG_RUNNING','WARNING','Backup chạy quá 2 giờ; cần kiểm tra tiến trình, không tự dừng.','backup')
    # Remember a due time actually observed from systemd. Never invent a schedule from wall clock.
    old=previous if isinstance(previous,dict) else {}
    state=old.get('schedule',{}) if isinstance(old.get('schedule'),dict) else {}
    due=state.get('due_at');new_due=backup.get('next_run_epoch')
    if not isinstance(due,(int,float)) or due>now.timestamp():
        due=new_due if isinstance(new_due,(int,float)) and new_due>0 else due
    if isinstance(due,(int,float)) and now.timestamp()>due+1800:
        observed_after=False
        for job in jobs:
            try:
                value=job.get('start_at') or job.get('finish_at')
                if datetime.fromisoformat(value).timestamp()>=due:observed_after=True
            except (ValueError,TypeError):pass
        if active or observed_after:
            due=new_due if isinstance(new_due,(int,float)) and new_due>due else None
        elif backup.get('active_state') in ('inactive','failed'):
            add('BACKUP_LATE','WARNING','Quá lịch đã quan sát 30 phút nhưng chưa thấy phiên chạy; journal có thể thiếu.','backup')
    if due is None:
        due=new_due if isinstance(new_due,(int,float)) and new_due>0 else None
    for name,item in data.get('post_boot',{}).get('connectors',{}).items():
        status=item.get('status')
        if status in ('CRITICAL','WARNING'):
            add('TUNNEL_EDGE_DISCONNECTED',status,'Connector mất kết nối Edge; Running không chứng minh đã kết nối.',name)
        elif status=='UNKNOWN':add('TUNNEL_UNVERIFIED','UNKNOWN','Chưa đủ dữ liệu để xác minh connector.',name)
    for mount in data.get('storage',[]):
        total,available=mount.get('total_bytes'),mount.get('available_bytes');name=mount.get('mountpoint','unknown')
        if mount.get('status')!='HEALTHY':
            add('MOUNT_UNVERIFIED','CRITICAL','Mount thiếu, sai UUID hoặc chưa xác minh được RW.',name);continue
        if isinstance(total,(int,float)) and isinstance(available,(int,float)) and total>0 and 0<=available<=total:
            percent=100*(total-available)/total
            if percent>=95 or available<2*1024**3:add('DISK_NEAR_FULL','CRITICAL','Ổ lưu trữ dùng >=95% hoặc còn dưới 2 GiB.',name)
            elif percent>=85 or available<10*1024**3:add('DISK_NEAR_FULL','WARNING','Ổ lưu trữ dùng >=85% hoặc còn dưới 10 GiB.',name)
    if data.get('history_persistence',{}).get('state')=='degraded':add('HISTORY_DEGRADED','WARNING','Lịch sử dài hạn ngừng ghi hoặc không khả dụng; xem trạng thái lưu trữ MHC.','history')
    olditems={item.get('id'):item for item in old.get('items',[]) if isinstance(item,dict)}
    for item in items:
        before=olditems.get(item['id'],{})
        item['first_seen']=before.get('first_seen',now.isoformat());item['last_seen']=now.isoformat()
    resolved=[{'id':item['id'],'code':item.get('code'),'resolved_at':now.isoformat()} for item in olditems.values() if item.get('id') not in {x['id'] for x in items}]
    return {'items':items,'resolved':resolved[:100],'schedule':{'due_at':due},'observed_at':now.isoformat(),
            'delivery':'dashboard_only','late_grace_seconds':1800}


def present(alerts,freshness):
    result=dict(alerts) if isinstance(alerts,dict) else {'items':[]}
    if freshness!='fresh':
        result['items']=[{**item,'severity':'STALE' if freshness=='stale' else 'UNKNOWN','historical_severity':item.get('severity')} for item in result.get('items',[])]
    result['freshness']=freshness
    return result
