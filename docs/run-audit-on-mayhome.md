# Chạy audit chỉ đọc trên máy mayhome

Gói độc lập `artifacts/mayhome-readonly-audit.pyz` được build từ đúng source trong checkout bằng `python3 scripts/build_audit_bundle.py`. Không cần cài Flask, pip, MHC service hoặc thay cấu hình host. Python 3.12+ theo target của dự án; các probe cần systemctl, findmnt, Docker CLI và curl đã có trên host. Không cài công cụ thiếu tự động.

## Chạy

Tải **cả hai tệp** `mayhome-readonly-audit.pyz` và `mayhome-readonly-audit.pyz.sha256` về cùng thư mục trên mayhome. Xem source bên trong archive nếu cần review. Sau đó:

```sh
sha256sum -c mayhome-readonly-audit.pyz.sha256
python3 mayhome-readonly-audit.pyz --probe-tunnel > mhc-preflight.json
```

Chạy bằng tài khoản của bạn trước. Không tự thêm sudo hoặc cấp Docker group để làm báo cáo xanh; thiếu quyền sẽ được ghi BLOCKED/UNVERIFIED để review. Gói không đọc env secret, config Nextcloud, SQL hoặc nội dung user data, không gọi shell/lệnh quản lý service, không cài hay restart container. Probe tunnel gồm systemd loopback và Docker cloudflared-tunnel; IP container được tra cứu mỗi vòng trên network cuongnh_nextcloud-net, kiểm tra private subnet và đối chiếu lại identity. Chỉ HTTP GET cổng 20241, hai path cố định; không gửi thông tin xác thực hoặc follow redirect.

**Exit code 2 là dự kiến:** báo cáo luôn `deployment_allowed: false` vì maintenance, guard, hash script, quyền release, preservation, tunnel routes và authorization vẫn cần review độc lập. `automatic_checks_passed` không phải giấy phép triển khai.

Báo cáo kiểm tra mount UUID/RW, trạng thái engine/timer/guard, ba container Nextcloud, recovery marker và tunnel Edge connections. Retention timer disabled/inactive chỉ được ghi nhận; công cụ không yêu cầu bật timer đó. Backup active/activating/deactivating/failed hoặc inactive nhưng Result lỗi, mount chưa xác minh, container ngừng/unknown, marker hoặc mất kết nối tunnel đều chặn bước triển khai.

Nếu port metrics chưa được map đúng connector, tunnel có thể UNKNOWN; không tự thay Compose để xử lý. Báo cáo không thay thế đầy đủ Stage 0: chưa xác minh maintenance bằng occ, source hashes, backup 02:00, baseline/checkpoint hoặc Cloudflare route/access.

## Gửi kết quả

Kiểm tra nội dung `mhc-preflight.json`, rồi gửi lại báo cáo để phân tích. Chỉ cần JSON này; không gửi `.env`, private key, token, full docker inspect hoặc tunnel config. Báo cáo có UUID/mountpoint/unit/container dự kiến, boot ID và timestamp, không có credential values. Không chạy install/enable/reboot sau audit trước khi có kết luận và phạm vi triển khai được review.

## Bằng chứng build trong cloud

- Build deterministic, SHA256 được lưu cạnh artifact.
- Chạy với `python3 -S` chứng minh không cần site packages.
- Chạy trên cloud trả `TARGET_HOST_MISMATCH`, không chạy probe production và exit 2.
- Kết quả: `docs/evidence/audit-bundle-results.json`, `docs/evidence/preflight-cloud.json`.
