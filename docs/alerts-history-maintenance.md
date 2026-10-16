# MAYHOME 0.5.0 — maintenance/log evidence, local alerts and history across reboot

## Phạm vi

Maintenance chỉ xác nhận từ lệnh cố định `docker exec --user www-data nextcloud-app php /var/www/html/occ status --output=json`, timeout 5 giây. Drop-in `nextcloud-status.conf` bật nguồn này; mặc định chưa bật thì UNKNOWN. Không thực thi `maintenance:mode`, không sửa engine/timer/Nextcloud. Tool `scripts/backup_evidence.py` chỉ xuất boolean installed/maintenance/needsDbUpgrade, thống kê sự kiện/log và độ đủ bằng chứng; không xuất MESSAGE thô, token, SQL, ID phiên hay tên tệp cá nhân.

Grammar hiện chỉ nhận event cố định có tag NC-BACKUP-RC1.2/1.3. `log_grammar` thống kê số START/PASS/FAILED/stage, số có invocation/boot và dòng có tag nhưng chưa nhận diện. Các dòng chưa nhận diện có thể là log thông tin bình thường; không tự coi là lỗi. Chưa có kết quả tool trên mayhome thì không tuyên bố đã đối chiếu engine thật hoặc dựng duration cho job thiếu START.

## Cảnh báo dashboard

- BACKUP_FAILED: systemd báo failure hoặc job gần nhất báo FAILED.
- BACKUP_LATE: quá thời điểm đã quan sát từ NextElapseUSecRealtime 30 phút, engine inactive/failed và chưa thấy job sau lịch. Cảnh báo WARNING vì journal có thể thiếu; không suy ra chắc chắn backup chưa chạy.
- BACKUP_LONG_RUNNING: engine active >2 giờ; không tự dừng.
- TUNNEL_EDGE_DISCONNECTED: lấy severity từ từng connector (bao gồm grace boot); Running không thay thế readiness/Edge.
- DISK_NEAR_FULL: WARNING khi dùng >=85% hoặc còn <10GiB; CRITICAL khi >=95% hoặc <2GiB. Chỉ tính khi mount HEALTHY/UUID/RW đã xác minh.
- HISTORY_DEGRADED: lịch sử ngừng ghi/không khả dụng do quota/permission/clock/disk pressure.

Alert có ID ổn định, first_seen/last_seen và danh sách resolved của chu kỳ hiện tại. Không spam một dòng mới cho cùng vấn đề mỗi lần poll. Khi collector stale, severity hiển thị STALE kèm severity lịch sử trong API. Lịch đã quan sát và trạng thái alert được lưu cùng DB khi bật persistence để phục hồi sau reboot. Chưa từng quan sát lịch thì chưa đủ bằng chứng báo trễ; không tự đoán lịch 02:00.

Delivery hiện là **dashboard_only**, không gửi Telegram/email/webhook, chưa có ack/silence hoặc audit giao nhận. Bốn cảnh báo CHECKSUM_UNKNOWN/HISTORY_INCOMPLETE/RETENTION_UNVERIFIED/MAINTENANCE_UNKNOWN chỉ mất khi có nguồn đủ bằng chứng; các chức năng mới không tự xác minh checksum hay restore.

## History bền vững

SQLite stdlib tại `/var/lib/mayhome-center/hardware.sqlite3`, tách biệt ổ/engine Nextcloud. Root collector là writer; web chỉ mở `mode=ro`. Thư mục root:mayhome-center 0750, DB0640; từ chối symlink hoặc quyền ghi không an toàn. Không thêm quyền ghi cho web.

- Mẫu thô giữ 24 giờ, hard cap4000 row; trung bình phút giữ30 ngày, hard cap45000 row.
- DB quota64MiB bằng SQLite max_page_count; không VACUUM toàn bộ ở mỗi vòng. Delete journal có thể dùng thêm dung lượng tạm.
- Dừng ghi khi filesystem còn <512MiB hoặc DB đầy/lock/schema/permission lỗi; collector/web/live vẫn hoạt động và báo degraded. Không xóa backup để giải phóng dung lượng.
- Lịch sử bắt đầu từ lúc bật writer, không dựng lại 30 ngày quá khứ. Mỗi vòng timer ghi một transaction; schema version1, writer retry ở vòng sau. Không dựa vào browser để thu thập.
- API lịch sử vẫn đọc được DB khi collector chưa có mẫu sau boot, kèm meta freshness unavailable; API chỉ số hiện tại vẫn503. Web đọc tối đa45000 row, deadline SQLite1giây, tổng hợp tối đa240 điểm; không tạo DB khi đọc và không trả boot ID/interface/counter raw.
- Ranges1h/6h/24h/7d/30d; khoảng dài dùng minute averages và bucket averages. Không có dữ liệu thì không dựng điểm. Gaps và đổi boot được đánh dấu discontinuity, không nối biểu đồ qua reboot; từng bucket là trung bình các mẫu có mặt.

## Review và kiểm chứng

Test parser/threshold/timestamp/late grace/recovery, DB reopen/reboot/clock reversal/quota/pressure/lock/retention/month query và auth/GET-only. Các browser fixture là GIẢ LẬP, không phải bằng chứng production. Xem `docs/evidence/phase5-*`. Phần đối chiếu maintenance/log, quyền native systemd, 30ngày storage overhead và reboot thật vẫn chờ mayhome.

Hồ sơ operator chứa thông tin vận hành riêng được giữ ngoài Git; bản review này chỉ chứa code, tài liệu và fixture giả lập mới.

## Lấy bằng chứng trước khi bật nguồn mới

Cập nhật checkout sạch theo quy trình review, xem diff trước khi merge. Để chạy tool bằng source mới nhưng dùng venv hiện tại (chưa cần nâng cấp dịch vụ):

```sh
cd ~/mayhome-center-review
git status --short
git fetch origin review/mhc-dual-connectors
git diff HEAD..origin/review/mhc-dual-connectors --stat
# Khi tracked source sạch và diff đã review:
git merge --ff-only origin/review/mhc-dual-connectors
sudo env PYTHONPATH="$PWD/src" /opt/mayhome-center/.venv/bin/python scripts/backup_evidence.py
```

Gửi JSON đã lọc; không gửi rawjournal/script engine, env hoặc khóa. Nếu có customtracked edits thì dừng và review, không reset. Tool không bật flag, restart hoặc chạy backup.

## Nâng cấp 0.5.0 và bật observers

Sau source review, dùng [prepare/activate/rollback](backup-manager-phase2.md), pin đúng SHA0.5.0. `prepare` kiểm tra SQLite trong Python native và không dừng dịch vụ; `activate` yêu cầu backup inactive/success. Không dùng installer lần đầu.

```sh
sudo python3 packaging/upgrade.py prepare --reviewed
# Chỉ khi Prepared release thành công, ghi lại SHA đã review:
MHC_REVIEW_SHA=$(git rev-parse HEAD)
sudo python3 packaging/upgrade.py activate --sha "$MHC_REVIEW_SHA" --reviewed
```

Chỉ sau khi activate thành công, review hai example drop-in và helper `packaging/enable_observers.py`; bật nguồn đọc maintenance và ghi lịch sử riêng MHC:

```sh
sudo python3 packaging/enable_observers.py --reviewed
sudo systemctl daemon-reload
sudo systemctl start mayhome-center-collector.service
```

Helper từ chối overwrite drop-in khác nội dung; nếu bị từ chối thì giữ cấu hình hiện tại và review cục bộ. Không gửi nội dung cấu hình tùy chỉnh có secret lên chat. StateDirectory chỉ do MHC collector quản lý; helper không thực thi hành động với unit khác hoặc thay env auth.

Đăng nhập, kiểm tra `/readyz`, `/api/v1/alerts`, `/api/v1/backups/log-grammar`, `/api/v1/host/hardware/history?hours=24`, maintenance trong Backup Manager, selector lịch sử và hai connector. Sau 60–90 giây phải có thêm mẫu DB. Chưa cần reboot production để thử; kiểm tra reopen DB trong cloud trước, reboot thực vào cửa sổ bảo trì.

Rollback release cũ bằng SHA đã activate như hướng dẫn chung; DB vẫn được giữ. Bản0.4.0 bỏ qua DB/env history nên rollback code không xóa lịch sử. Nếu muốn tắt observers, review rồi di chuyển đúng hai drop-in do helper tạo sang thư mục bảo quản ngoài unit directory, daemon-reload; không xóa secret hoặc dữ liệu DB. Không đụng các drop-in tunnel hoặc cấu hình tùy chỉnh khác.
