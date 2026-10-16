# API v1

Mọi endpoint dữ liệu yêu cầu session từ form `/login` (CSRF + khóa truy cập); `/healthz` public chỉ trả `{alive:true}`. Không chấp nhận bearer token/header Cloudflare làm danh tính. Host chỉ localhost, 127.0.0.1, mayhome.cuong.win. Chỉ GET/HEAD, OPTIONS theo framework; mutation 404/405.

| GET | Data |
|---|---|
| /healthz | Liveness web, không phải sức khỏe backup |
| /readyz | Collector fresh, 503 khi missing/stale |
| /api/v1/host/hardware | Current + bounded history phần cứng, không counter nội bộ |
| /api/v1/alerts | Cảnh báo hiện tại, first/last seen, resolved; delivery dashboard_only |
| /api/v1/backups/log-grammar | Thống kê event/metadata có mặt, không MESSAGE thô |
| /api/v1/host/hardware/history | hours=1,6,24,168,720; points=30..240; DB mode=ro, 503 nếu chưa khả dụng |
| /api/v1/overview | Host, backup, inventory, storage, containers, jobs, logs, alerts/status |
| /api/v1/backups/status | Engine, timer, marker, maintenance, services |
| /api/v1/backups/inventory | Thời điểm inventory riêng, số complete/staging hoặc unknown |
| /api/v1/backups/diagnostics | Từng kiểm tra bằng chứng; stale override trạng thái |
| /api/v1/backups/jobs | items + next_cursor |
| /api/v1/backups/snapshots | items + next_cursor |
| /api/v1/backups/snapshots/{id} | Metadata theo ID hợp lệ, 404 nếu không có |
| /api/v1/backups/retention | Chính sách dự kiến và độ tin cậy; mode unknown khi chưa xác minh |
| /api/v1/backups/storage | UUID, mount, bytes thực tế từ statvfs |
| /api/v1/logs | items + next_cursor, source backup/watchdog/guard/retention |
| /api/v1/capabilities | Modules + mutation flags false |

Envelope: `{data, meta:{collected_at,freshness,source:"collector-v1"}, errors:[{code,message}]}`. Trạng thái không có dữ liệu: 503, một phần: 200 kèm errors, stale: 200/freshness stale; readyz 503. Auth: 401, chưa cấu hình auth: 503, sai tham số: 400, giới hạn đăng nhập: 429.

Phân trang limit 1..200 mặc định 20, cursor offset 0..10000. Offset chỉ có ý nghĩa trong inventory hiện tại, không đảm bảo ổn định khi collector refresh. Collector chỉ xuất log backup ở bản này; nguồn khác trả rỗng, không ngụ ý không có sự kiện.

## Post-Boot Health Monitor

GET /api/v1/host/post-boot và overview.data.post_boot trả {status, code, observed_at, connectors:{systemd:{...},docker:{...}}, scope, public_route_verified:false}. Mỗi connector có status/code/observed_at/boot_id/uptime_sec/grace_seconds/ready_http_status/edge_connections; systemd có active_state, Docker có running/address/network_cidr/container_name/network_name. Chỉ phát các fields đã chọn, không raw inspect/metrics/container ID/token.

Status tổng CRITICAL > UNKNOWN > WARNING > HEALTHY. STALE override tổng và từng connector khi collector quá TTL; liveness/readiness web không chứng minh public route. Auth/GET-only/pagination/error contract khác giữ nguyên. [Chi tiết probe và opt-in](post-boot-monitor.md), [hồ sơ review triển khai](review-dual-connectors.md).

Backup Manager 0.2.0: jobs có mức tin cậy phiên và thời lượng chỉ khi ghép được START/finish hợp lệ. Snapshot detail có structure_checks và bằng chứng định dạng manifest; không xác minh hash SQL/restore. Xem [giới hạn và nâng cấp](backup-manager-phase2.md).

0.4.0: hardware có freshness riêng trong envelope, observed_at, current, history tối đa120 mẫu/1h, rate_state và sample_interval_seconds. Rates null khi chưa đủ hai mẫu/counter reset/reboot/gap. Không có API ghi hoặc truy vấn đường dẫn hệ thống tùy ý. Xem [nguồn và giới hạn phần cứng](hardware-overview.md).

0.5.0: lịch sử/alerts có giới hạn và nguồn bằng chứng trong [tài liệu phase5](alerts-history-maintenance.md). Ngưỡng trễ30phút sau lịch thực đã quan sát; trạng thái stale không giữ severity như một quan sát mới. Lịch sử dài hạn được bật riêng, không ghi vào ổ Nextcloud.

API lịch sử bền vững có thể trả200 khi DB khả dụng dù collector chưa có mẫu sau boot; meta.freshness vẫn unavailable và lỗi collector vẫn được giữ. Chỉ số hiện tại vẫn503 trong tình huống đó.
