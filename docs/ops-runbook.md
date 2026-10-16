# Vận hành và deployment gates

**Chưa được cài lên mayhome.** Không chạy các lệnh dưới đây trong production trước audit và phê duyệt riêng. Không tạo DNS/tunnel hay enable service trong cloud task này.

## Stage 0 bắt buộc trên mayhome

Administrator thu thập read-only: OS/hardware cần thiết; SHA256 các script engine/watchdog/retention; `systemctl show` các unit trong manifest; timer schedule và timezone; mount UUID/options của hai ổ; trạng thái container chỉ fields cần thiết; port 5010; metadata baseline/fixed checkpoint; sự tồn tại/validity marker; maintenance qua nguồn chỉ đọc đã review; tunnel hostname hiện có (không xuất token/config secrets). Review ACL, root ownership, symlink/ancestor và directory write permissions cho backup root, release và runtime output.

Dừng deployment khi backup đang chạy, maintenance ON, marker còn, UUID sai hoặc guard chưa xác nhận. Không tự sửa production. Xác minh retention timer và backup tự động 02:00 riêng; candidate package không chứng minh timer enabled.

## Cài đặt sau phê duyệt

Từ release checkout đã review sạch trên host mayhome, chạy `sudo sh packaging/install.sh --install-reviewed`. Script tạo account không shell, cài source/venv root-owned và ba unit MHC; **không reload/enable/start**. Nó từ chối overwrite release/unit hiện có. Lỗi cài có thể để lại file MHC cần operator kiểm tra, không rollback tự động.

Tạo `/etc/mayhome-center.env` root:root 0600 qua trình quản lý bí mật với MHC_AUTH_TOKEN và MHC_SESSION_KEY là hai secret độc lập ngẫu nhiên tối thiểu 32 byte. Không dùng ví dụ cố định, không đưa secret vào command history/Git/chat. MHC_COOKIE_SECURE=1 cho production. Không đặt secret trong collector output.

Kiểm tra `systemd-analyze verify` ba unit thật, quyền release không writable bởi web, collector dir `/run/mayhome-center` root:mayhome-center 0750. Unit collector tạo thư mục khi chạy. ProtectSystem=strict làm mount namespace collector read-only; probe findmnt đọc mount namespace PID 1 để đối chiếu RW production. Phải xác minh findmnt --task 1 được hỗ trợ và PID 1 là host systemd, không phải container.

Sau phê duyệt activation riêng: `systemctl daemon-reload`, `systemctl enable --now mayhome-center-collector.timer mayhome-center.service`. Chỉ unit MHC. Kiểm tra collector tạo JSON 0640 root:mayhome-center và `journalctl -u mayhome-center-collector.service` không có secret; curl loopback health, đăng nhập, readyz/overview. MHC web không có supplementary docker group. Kiểm tra listener chỉ 127.0.0.1. Không gọi guard script trước khi review nội dung.

## Cloudflare gate

Chỉ sau review cấu hình tunnel hiện tại, hostname collision và explicit authorization: thêm route mayhome.cuong.win → loopback 5010, Access identity policy + MFA + rate limiting, edge TLS/HSTS. Giữ cloud.cuong.win nguyên trạng. Test unauthorized Access, direct origin không public, forged headers không vượt app login, cookie Secure và functional APIs. Không cần thêm token Cloudflare vào ứng dụng web.

## Sự cố

- STALE: xem collector MHC unit/timer, JSON mtime/permissions, exit status, timeout; không restart Nextcloud/backup để sửa dashboard.
- Disk full/mount critical: đối chiếu UUID và RW, báo operator; không xóa snapshots hay mount tự động.
- Backup fail/marker: giữ nguyên evidence, kiểm tra log engine/guard theo quy trình backup riêng; MHC không clear marker/maintenance.
- Rotation: cập nhật hai secret trong env secure, restart chỉ web MHC sau phê duyệt; mọi session cũ hết hiệu lực.
- Rollback: `sudo sh packaging/rollback.sh --disable-mhc-reviewed` dừng chỉ MHC, giữ release/log. Không wildcard unit, không xóa data. Upgrade chưa tự động hóa: lưu release cũ, review diff và units trước thay thế.

## Nghiệm thu production

Đo RSS, CPU, I/O và latency trên host ít nhất 15 phút; điều chỉnh cadence inventory 2–5 phút sau profiling. Xác minh backup 02:00 thực sự hoàn tất và xuất hiện trong dashboard, trạng thái mount/guard/cron/schedule không thay đổi trước/sau. Restore isolated cần quy trình riêng. Không tag RC1 freeze trước các gates. Tắt MHC không ảnh hưởng timer/engine backup.

## Cloudflare mất kết nối sau boot

Xem [Post-Boot Health Monitor](post-boot-monitor.md) để bật probe read-only có kiểm soát. Chỉ container Running không chứng minh cloudflared còn kết nối Edge. Cảnh báo sau boot dựa readiness/gauge, không tự restart bất kỳ container nào. Khi CRITICAL, operator kiểm tra mạng/DNS/clock và logs cloudflared qua kênh quản trị riêng, không thay đổi Nextcloud/backup để sửa tunnel. UNKNOWN là thiếu bằng chứng, không phải xác nhận tunnel disconnected hoặc healthy.
