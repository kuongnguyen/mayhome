# Bằng chứng nghiệm thu — bản phát triển 0.1.0

Ngày kiểm tra: 2026-10-16 (UTC+07). Checkout cloud chưa có commit gốc; **chưa freeze/tag RC1**. PASS dưới đây chỉ áp dụng cloud/fixture, không thay cho nghiệm thu production. Không có thao tác production trong phiên này.

## Lệnh và kết quả thật

- `.venv/bin/pytest -q`: **99 passed**, không skip/xfail, xem `evidence/pytest-results.txt`.
- `MHC_TEST_TOKEN_FILE=<ephemeral file> .venv/bin/python tests/runtime_check.py`: 30 request trang có đăng nhập qua Gunicorn 2 worker/2 thread, uid 1000; thành công. API thiếu collector trả 503 đúng kỳ vọng. Xem `evidence/runtime-results.json` cho latency (cloud, chưa chứng minh cache/host budget).
- `MHC_TEST_TOKEN_FILE=<ephemeral file> /tmp/mhc-browser/bin/python tests/browser_check.py`: Chromium/Playwright 1.56.0, 16 cặp trang/viewport 360/390/768/1440; không global horizontal overflow, không JS error. Ảnh và JSON trong `evidence/`.
- `.venv/bin/pip check`: No broken requirements found.
- `sh -n packaging/install.sh packaging/rollback.sh`: exit 0, **không chạy installer/rollback**.
- `systemd-analyze verify packaging/systemd/...`: chưa pass nguyên bản trong cloud vì `/opt/mayhome-center/.venv` chưa cài. Bản copy trong `/tmp/mhc-verify` chỉ thay prefix `/opt/mayhome-center` thành checkout cloud: verify exit 0. Unit target, identity, sandbox và startup production vẫn PENDING.

## Theo manifest

| ID | Kết quả | Evidence / giới hạn |
|---|---|---|
| 001 | PASS cloud / PENDING host | Gunicorn uid 1000 loopback, 30 request; dedicated service chưa cài |
| 002 | PASS fixture / PENDING host | Test inventory nhận baseline/fixed checkpoint; không đọc production |
| 003 | PASS cloud | 4 HTTP mutation methods đều 405 |
| 004 | PARTIAL | Web chỉ đọc JSON, không Docker code path; sandbox host chưa xác minh |
| 005 | PASS fixture / PENDING ACL audit | ID traversal, symlink parent/entry bị chặn |
| 006 | PASS fixture | Wrong UUID/RO và unavailable probe không xanh |
| 007 | PASS fixture | Staging có COMPLETE vẫn incomplete |
| 008 | PASS fixture | COMPLETE chỉ present; không db_checksum_verified |
| 009 | PARTIAL | RUNNING semantics có test; stage/elapsed hiện Unknown, chờ log format thật |
| 010 | PASS fixture / PENDING host | Failure severity tested; chưa có run fail production |
| 011 | PARTIAL | Marker present CRITICAL; validity/content không đọc, chưa xác minh host |
| 012 | PASS fixture | Container false critical, missing tools unknown fields |
| 013 | PASS fixture | Stale precedence, invalid/oversize JSON rejected |
| 014 | PASS app / PENDING edge | Unauthenticated API 401; Access chưa cấu hình |
| 015 | PASS app | Forged Cloudflare identity/JWT không xác thực |
| 016 | PARTIAL | No raw log emission, redaction test; ACL audit production pending |
| 017 | PENDING host | Read-only code/unit policy, không thực thi root collector ở cloud |
| 018 | PENDING host | Rollback script syntax only; không chạy systemctl |
| 019 | PENDING host | Đọc property NextElapseUSecRealtime, không scrape cột; timer thật chưa xác minh |
| 020 | PENDING | Retention mode unknown; counts eligibility chưa có exporter evidence |
| 021 | PASS fixture / PARTIAL | Journal output/time/count caps, API pagination; nguồn ngoài backup chưa thu thập |
| 022 | PASS cloud | Chromium 360px cả bốn trang không global overflow |
| 023 | PARTIAL | Semantic nav/status/form, focus/skip link; chưa audit keyboard/contrast AA toàn diện |
| 024 | PARTIAL | Confidence partial, không infer full run from log PASS; chưa test reboot thật |
| 025 | PASS design | Chỉ statvfs bytes thực tế, không du/logical size giả |
| 026 | PARTIAL | Bounded poll/backoff, offline branch; chưa browser fault injection đầy đủ |
| 027 | PASS fixture / PENDING host | Expected UUID + observed/RW/statvfs contract |
| 028 | PASS fixture | Fixed + baseline + latest two complete bảo vệ, staging không eligible |
| 029 | NOT RUN | Chưa đo 15+ phút trên mayhome; chỉ short cloud smoke |
| 030 | NOT RUN | Chưa quan sát backup 02:00 thực tế |
| 031 | NOT RUN | Không truy cập/thay tunnel hoặc cloud.cuong.win |
| 032 | PENDING host | Không có IPC gọi engine; chưa kiểm chứng backup khi MHC dừng |
| 033 | PARTIAL | Vietnamese UI, status code độc lập; English catalog chưa hoàn thiện |
| 034 | PENDING host | Retention timer chỉ đọc property; chưa biết actual enabled |

Coverage parsing/security 95%: **chưa đo**. Profile RSS/CPU/I/O trên host, source script hashes, actual backup evidence, secure release ownership, systemd Restart=on-failure, Access/MFA và isolated restore đều chưa nghiệm thu. RC1 operational acceptance **PENDING**; không triển khai/merge/tag qua các gates còn thiếu.


## Bổ sung Post-Boot Monitor — 2026-10-16

48 kiểm thử bao gồm 25 case mới về tunnel, grace/reboot, stale/auth/read-only và HTTP integration giả lập với curl thật. Kết nối host production, mapping metrics/readiness, lần reboot thật và sandbox IPAddressDeny/Allow vẫn PENDING. HEALTHY của panel mới chỉ có scope connector → Edge, không thay trạng thái bảo vệ backup hoặc public route. Tài liệu: post-boot-monitor.md. Unit syntax verify trên bản copy đổi prefix cloud; chưa cài unit production.


## Tiếp tục nghiệm thu và bàn giao audit — 2026-10-16

- Suite hiện tại: **99 passed**. Thêm kiểm tra preflight wrong-host, active/failed backup, mount/marker/container chưa an toàn, retention disabled không yêu cầu bật; auto checks không cấp quyền deploy. Thêm regression metrics HTTP redirect/error không tạo HEALTHY và tắt curlrc.
- `tests/browser_post_boot.py`: trình duyệt dùng fixture (không phải host) chạy 4 trạng thái × 2 viewport 360/1440px; ảnh có nhãn DỮ LIỆU GIẢ LẬP. Kiểm tra lỗi mạng/offline chuyển badge tunnel sang UNKNOWN và kết nối lại cập nhật trạng thái. Kết quả trong evidence/post-boot-browser-results.json.
- Artifact audit độc lập deterministic, kiểm tra chạy stdlib-only `python3 -S`, cloud TARGET_HOST_MISMATCH/exit 2 đúng kỳ vọng. Chưa có báo cáo từ mayhome; deployment và operational acceptance tiếp tục PENDING.


## Review hai connector — 2026-10-16

Suite cuối hiện tại: **99 passed** (evidence/pytest-results.txt). Chromium: **24 checks**, 5 trạng thái/scenario × 4 viewport (360/390/768/1440) + 4 network/offline transitions; không JS errors (evidence/post-boot-browser-results.json). Ảnh fixture mới thể hiện systemd HEALTHY trong lúc Docker Running/Edge=0/CRITICAL. Các ảnh này là giả lập, không phải production.

Các case mới: IP thay đổi qua từng vòng; inspect lại phát hiện recreation giữa probe; sai identity/network; host networking, public/link-local/loopback/IPv6/out-of-subnet/network/broadcast IP bị từ chối trước HTTP; Docker unavailable; opt-in độc lập; service inactive không thể xanh; API nested STALE và auth. Packaging stage source 0644/0755, loại cache/env/SQL và từ chối symlink/overwrite được kiểm thử.

`systemd-analyze verify` exit 0 trên bản copy đổi prefix sang checkout cloud; chưa chứng minh sandbox hay user service trên mayhome. `pip check` pass; shell syntax installer/rollback pass. Suite/Chromium cần chạy ngoài hạn chế socket của execution sandbox để dùng fixture loopback; không có request production/Docker container nào được tạo hoặc dừng. Chưa chạy installer/rollback production.

Bằng chứng mayhome trong hội thoại do operator cung cấp: hai connector từng HTTP 200/gauge 4, PID systemd trùng loopback listener, container thuộc network cuongnh_nextcloud-net. Chưa biết CIDR thật, tunnel/route identity, maintenance/backup safety audit, reboot causality hay 15-minute runtime của bản mới. RC1 operational acceptance tiếp tục **PENDING**. Source review bundle không phải release freeze và chưa được push/merge.


## Cập nhật bằng chứng operator — 2026-10-16

Network cuongnh_nextcloud-net được operator xác minh Subnet=172.18.0.0/16, Gateway=172.18.0.1. Backup service inactive/dead/Result=success. File review drop-in đã điền CIDR; systemd-analyze verify exit 0 trên bản sao cloud (đổi release prefix), bao gồm drop-in. Chưa activation hay nghiệm thu sandbox production; chưa xác minh thời điểm/integrity backup, mount/guard/marker/maintenance. Xem evidence/operator-network-backup-observation.json.


## Audit an toàn do operator cung cấp — 2026-10-16

Hai mount đúng UUID/RW; Mount Guard, backup và watchdog timers active/enabled; app/cron/DB Running; Nextcloud 35.0.1.1 maintenance=false, needsDbUpgrade=false; recovery marker không có trong parent tồn tại root:root 0700. Bằng chứng nằm evidence/operator-host-safety-observation.json. Không đánh dấu full Stage 0 hoặc deployment PASS: chưa có baseline/checkpoint/script review/hash/schedules/retention/integrity và runtime ứng dụng.

## Review 0.2.0 — Backup Manager

Cloud: 150 pytest cases, 15 Chromium Backup Manager scenarios and 24 existing connector scenarios passed; fixtures are synthetic, including container Running/Edge disconnected. Evidence: `evidence/pytest-phase2-results.txt`, `evidence/backup-manager-browser-results.json`, `evidence/post-boot-phase2-regression.json`. New snapshot screenshots are labeled GIẢ LẬP.

History correlation, metadata structure/manifest grammar, unknown/stale handling and MHC-only upgrade/rollback have automated coverage. Upgrade tests simulate systemctl/pip; they do not establish successful production upgrade. Operator previously verified 0.1.0 web/auth/collector and dual HEALTHY4 on mayhome; 0.2.0 host install, resource measurements, actual journal grammar, natural 02:00 backup, reboot, checksum, retention and restore acceptance remain pending. See `backup-manager-phase2.md`.
