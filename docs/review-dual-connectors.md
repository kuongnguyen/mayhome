# Hồ sơ review: MHC 0.1.0 — dual connector monitoring

## Phạm vi bàn giao

- Collector hai nguồn độc lập: cloudflared.service và cloudflared-tunnel.
- IP Docker được tìm và kiểm tra trong từng vòng, không cố định 172.18.0.2.
- Dashboard/API phân biệt runtime và kết nối Edge; một nguồn khỏe không che nguồn lỗi.
- Tất cả action flags vẫn false. Không có run backup, delete, restore, thay timer hoặc restart container.
- API additive: giữ GET /api/v1/host/post-boot, thêm connectors.systemd/docker; JSON collector cũ không có map hiển thị yêu cầu nâng collector.

Các file chính: src/mayhome_center/post_boot.py, collector.py, preflight.py, app.py, templates/dashboard.html, static/app.js/app.css; tests/test_dual_tunnels.py, test_post_boot.py, browser_post_boot.py. Cấu hình mẫu: packaging/systemd/mayhome-center-collector.service.d/tunnel-monitor.conf.example.

## Trước khi cài lên mayhome

1. Review source trên nhánh review/mhc-dual-connectors và checksum bundle nếu dùng gói source. Không gọi đây là RC1 freeze; chưa tạo tag release production. Hướng dẫn clone đúng nhánh trong README.
2. Hoàn thành audit Stage 0: backup inactive/success, UUID/RW đúng, guard/timers/cron an toàn, marker absent và maintenance OFF từ nguồn được review. Operator đã cung cấp bằng chứng hai tunnel kết nối và backup service inactive/dead/Result=success. Operator tiếp tục xác nhận hai mount đúng UUID/RW, ba timer active/enabled, ba container chạy, maintenance=false, needsDbUpgrade=false, marker absent trong parent root:root 0700. Chưa có audit hoàn chỉnh: baseline/checkpoint, script hashes/review, lịch timer/retention và backup integrity vẫn cần evidence.
3. CIDR được operator xác minh ngày 2026-10-16: 172.18.0.0/16, gateway 172.18.0.1. Mapping loopback đã được đối chiếu PID service trong hội thoại. Kiểm tra lại trước activation nếu trạng thái thay đổi. Không sửa Compose, network, DNS hoặc tunnel để phục vụ monitor nếu chưa có review riêng.
4. Giữ kênh quản trị LAN/SSH độc lập; dashboard qua tunnel sẽ không truy cập được khi tunnel ngắt.

## Chuẩn bị development/review (không root)

Tại thư mục source review trên mayhome (Python 3.12+):

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -e . --no-deps
.venv/bin/pytest -q
.venv/bin/python -m mayhome_center.preflight --probe-tunnel > mhc-preflight.json
```

Preflight chạy read-only và kiểm tra cả hai connector khi --probe-tunnel; exit 2 là dự kiến vì manual gates chưa tự cấp phép. Thiếu quyền đọc được báo BLOCKED/UNKNOWN, không tự cấp Docker group/sudo. Không gửi token/config/inspect thô.

## Cài file MHC — chỉ sau phê duyệt deployment

```sh
sudo sh packaging/install.sh --install-reviewed
```

Installer chỉ cài vào release mới `/opt/mayhome-center` và ba unit MHC; từ chối overwrite. Code/template/static được stage 0644, thư mục 0755, root-owned để web account đọc được dù source checkout ban đầu 0600/0700. Không copy .env/SQL/cache/symlink. Không chmod/chown backup paths. Venv do root cài với umask 022. Source code/venv không được writable bởi web account. Installer chưa kiểm chứng thực thi trên host.

Tạo env root:root 0600 với hai secret độc lập MHC_AUTH_TOKEN/MHC_SESSION_KEY theo ops-runbook; web Secure cookies bật. Xem service identity không shell và web sandbox trong packaging/systemd. Không đưa secret vào đây hoặc Git.

Drop-in collector: review cấu hình đã điền CIDR 172.18.0.0/16 và cài thành `/etc/systemd/system/mayhome-center-collector.service.d/tunnel-monitor.conf`. File .example đã có CIDR được operator cung cấp nhưng vẫn chỉ dành cho review; installer không copy/kích hoạt nó. Giữ deny-any và chỉ cho loopback + CIDR này. Verify systemd units/drop-in, cgroup IP filtering và quyền runtime trên host.

Activation (daemon-reload/enable/start chỉ MHC) chỉ thực hiện theo ops-runbook sau explicit authorization. Không restart service cloudflared, container hoặc Nextcloud trong quá trình cài monitor. Không mở domain mới trong bước này.

## Nghiệm thu và rollback

- JSON fresh, hai source correct, status độc lập, API auth, listener chỉ loopback; read-only backups/container/timers không đổi trước/sau.
- Kiểm tra lỗi bằng fixture; không dừng connector production. Chưa claim reboot recovery từ log unregister/register không có timestamp đầy đủ.
- Quan sát >=15 phút trên host, CPU/RSS/I/O; verify collector không bị unit timeout, TTL/stale; xem backup 02:00 khi thực sự chạy. RC1 operational acceptance vẫn PENDING.
- Rollback dùng packaging/rollback.sh với --disable-mhc-reviewed chỉ sau authorization; chỉ dừng MHC và giữ log/file. Giữ cloudflared và Nextcloud nguyên trạng.

## Trạng thái bàn giao

Source review nằm trong checkout cloud; bundle reproducible được tạo bằng scripts/build_review_bundle.py (không venv, .git, cache hay env secrets). Nhánh review: review/mhc-dual-connectors. Chưa merge, chưa tạo freeze tag và chưa triển khai production. Kết quả kiểm thử: 99 pytest passed; 24 Chromium checks passed, không JS errors. Chi tiết và ảnh fixture trong docs/evidence. Tệp checklist này là hướng dẫn review, không phải bằng chứng production PASS.

Bằng chứng host bổ sung: docs/evidence/operator-host-safety-observation.json. Chỉ phản ánh thời điểm operator chạy lệnh; cần kiểm tra lại ngay trước activation. Không có thay đổi production từ cloud.
