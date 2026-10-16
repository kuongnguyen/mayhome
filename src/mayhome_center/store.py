import json
import os
import stat
from datetime import datetime
from pathlib import Path
from .domain import Observation, utcnow

MAX_BYTES = 1024 * 1024

def load_status(path: str) -> Observation:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
                raise ValueError("invalid status file")
            payload = json.loads(stream.read(MAX_BYTES + 1))
        if payload.get("schema_version") != 1:
            raise ValueError("schema")
        generated = payload["generated_at"]
        timestamp = datetime.fromisoformat(generated)
        age = (utcnow() - timestamp).total_seconds()
        if age < -30:
            raise ValueError("future timestamp")
        if not isinstance(payload.get("data"), dict) or not isinstance(payload.get("errors"), list):
            raise ValueError("shape")
        data = payload['data']
        for key in ('snapshots', 'jobs', 'logs', 'storage', 'containers'):
            rows = data.get(key, [])
            if not isinstance(rows, list) or len(rows) > 1000 or any(not isinstance(row, dict) for row in rows):
                raise ValueError('collection shape')
        for key in ('backup', 'retention', 'host', 'post_boot', 'inventory', 'diagnostics', 'hardware'):
            if not isinstance(data.get(key, {}), dict):
                raise ValueError('object shape')
        if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in data.get('snapshots', [])):
            raise ValueError('snapshot shape')
        if any(not isinstance(error, dict) or not isinstance(error.get('code'), str) or not isinstance(error.get('message'), str) for error in payload['errors']):
            raise ValueError('error shape')
        fresh = "fresh" if age <= 90 else "stale"
        errors = payload["errors"][:100]
        if fresh == "stale":
            errors = errors + [{"code": "COLLECTOR_STALE", "message": "Dữ liệu collector đã quá 90 giây."}]
        return Observation(payload["data"], fresh, generated, errors)
    except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
        return Observation({}, "unavailable", None, [{"code": "COLLECTOR_UNAVAILABLE", "message": "Chưa có dữ liệu collector hợp lệ."}])
