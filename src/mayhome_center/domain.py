from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

Status = Literal["HEALTHY", "RUNNING", "WARNING", "CRITICAL", "UNKNOWN", "STALE"]
MODULES = (
    {"id": "overview", "title": "Tổng quan", "route": "/", "nav_order": 0},
    {"id": "backup_manager", "title": "Quản lý backup", "route": "/backups", "nav_order": 1},
    {"id": "logs", "title": "Nhật ký", "route": "/logs", "nav_order": 2},
    {"id": "about", "title": "Cấu hình", "route": "/about", "nav_order": 3},
)
SNAPSHOT_PATTERN = r"(?:baseline|[0-9]{8}-[0-9]{6}|\.staging-[A-Za-z0-9_-]{1,64})"
MOUNTS = {
    "/mnt/nextcloud_storage": "aa274c8e-844a-4482-aac2-9313ad6f0108",
    "/mnt/nextcloud_backup": "5740eea6-8bf1-4f3e-ac9d-2c66bda6147c",
}
UNITS = (
    "nextcloud-auto-backup.service", "nextcloud-auto-backup.timer",
    "nextcloud-backup-watchdog.timer", "nextcloud-mount-guard.timer",
    "nextcloud-backup-retention.timer",
)
CONTAINERS = ("nextcloud-app", "nextcloud-cron", "nextcloud-db")

def utcnow():
    return datetime.now(timezone.utc)

@dataclass(frozen=True)
class Observation:
    data: dict
    freshness: str
    collected_at: str | None
    errors: list

def protection(data: dict, freshness: str) -> Status:
    if freshness == "stale":
        return "STALE"
    if freshness != "fresh":
        return "UNKNOWN"
    mounts = data.get("storage", [])
    if any(m.get("status") == "CRITICAL" for m in mounts):
        return "CRITICAL"
    backup = data.get("backup", {})
    if backup.get("result") == "failed" or backup.get("marker_present") is True:
        return "CRITICAL"
    containers = data.get("containers", [])
    if any(c.get("running") is False for c in containers):
        return "CRITICAL"
    if backup.get("active_state") == "active":
        return "RUNNING"
    # RC1 must not infer health without maintenance, history, policy and guard evidence.
    return "UNKNOWN"
