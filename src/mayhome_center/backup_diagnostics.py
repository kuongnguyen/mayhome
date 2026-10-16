"""Explain missing evidence without turning a partial observation into HEALTHY."""
from .domain import MOUNTS, CONTAINERS


def diagnostics(data):
    checks = []
    def add(code, state, message):
        checks.append({'code':code,'status':state,'message':message})
    mounts = {row.get('mountpoint'):row for row in data.get('storage', [])}
    for mountpoint in MOUNTS:
        row = mounts.get(mountpoint, {})
        add('SOURCE_MOUNT' if mountpoint.endswith('_storage') else 'BACKUP_MOUNT',
            'PASS' if row.get('status')=='HEALTHY' else 'CRITICAL' if row.get('status')=='CRITICAL' else 'UNKNOWN',
            'Đối chiếu UUID và quyền RW của mount.')
    containers = {row.get('name'):row for row in data.get('containers', [])}
    for name in CONTAINERS:
        running = containers.get(name, {}).get('running')
        add(name.upper().replace('-','_'), 'PASS' if running is True else 'CRITICAL' if running is False else 'UNKNOWN',
            'Trạng thái container được đọc qua Docker State.Running.')
    backup = data.get('backup', {})
    marker = backup.get('marker_present')
    active = backup.get('active_state') == 'active'
    add('RECOVERY_MARKER','PASS' if marker is False else 'WARNING' if marker is True and active else 'CRITICAL' if marker is True else 'UNKNOWN',
        'Marker trong lúc engine chạy chưa được coi là sự cố recovery; marker còn sau khi dừng cần kiểm tra.')
    maintenance = backup.get('maintenance')
    add('MAINTENANCE','PASS' if maintenance is False else 'WARNING' if maintenance is True and active else 'CRITICAL' if maintenance is True else 'UNKNOWN',
        'Chỉ xác nhận từ nguồn occ status được bật riêng; không thực thi maintenance action.')
    nc_status = backup.get('nextcloud_status', {})
    installed = nc_status.get('installed')
    upgrade = nc_status.get('needs_db_upgrade')
    add('NEXTCLOUD_INSTALLATION','PASS' if installed is True else 'CRITICAL' if installed is False else 'UNKNOWN',
        'Trạng thái cài đặt từ occ status, không thực hiện nâng cấp.')
    add('NEXTCLOUD_DB_UPGRADE','PASS' if upgrade is False else 'CRITICAL' if upgrade is True else 'UNKNOWN',
        'Cần nâng cấp DB nếu occ báo needsDbUpgrade; MHC không chạy nâng cấp.')
    services = {row.get('unit_name'):row for row in backup.get('services', [])}
    for name in ('nextcloud-auto-backup.timer','nextcloud-mount-guard.timer','nextcloud-backup-watchdog.timer'):
        row = services.get(name, {})
        okay = row.get('active_state')=='active' and row.get('enabled_state')=='enabled'
        known_bad = row.get('active_state') in ('inactive','failed') or row.get('enabled_state') in ('disabled','masked')
        add(name.upper().replace('-','_').replace('.','_'),'PASS' if okay else 'WARNING' if known_bad else 'UNKNOWN',
            'Timer được quan sát chỉ đọc, không tự kích hoạt hoặc sửa lịch.')
    inventory = data.get('inventory', {})
    add('SNAPSHOT_INVENTORY','PASS' if inventory.get('known') is True else 'UNKNOWN',
        'Kiểm tra metadata giới hạn, không duyệt dữ liệu người dùng.')
    for key, code in (('baseline_observed','BASELINE_METADATA'),('fixed_checkpoint_observed','PROTECTED_CHECKPOINT_METADATA')):
        value = inventory.get(key)
        add(code,'PASS' if value is True else 'WARNING' if value is False else 'UNKNOWN',
            'Chỉ xác nhận metadata quan sát được; không thay đổi baseline hoặc checkpoint được bảo vệ.')
    jobs = data.get('jobs', [])
    add('JOB_HISTORY','PASS' if any(row.get('confidence')=='complete' for row in jobs) else 'UNKNOWN',
        'Cần start/finish cùng invocation và boot để xác nhận thời lượng; journal có thể thiếu.')
    add('CHECKSUM','UNKNOWN','Cấu trúc và file SHA256 hiện diện không phải bằng chứng đã tính hash/gzip hoặc restore.')
    add('RETENTION','UNKNOWN','Chính sách 14 ngày dự kiến; chưa có kết quả audit retention thực được xác minh.')
    return {'checks':checks,'unknown_count':sum(row['status']=='UNKNOWN' for row in checks),
            'critical_count':sum(row['status']=='CRITICAL' for row in checks),
            'note':'PASS của từng kiểm tra không đồng nghĩa backup đã đủ điều kiện HEALTHY.'}


def present_diagnostics(data, freshness):
    result = dict(data)
    if freshness != 'fresh':
        result['checks'] = [{**row,'status':'STALE' if freshness=='stale' else 'UNKNOWN'} for row in data.get('checks', [])]
    return result
