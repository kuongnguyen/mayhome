# MAYHOME CENTER

Ứng dụng giám sát tài nguyên máy chủ, ưu tiên **Nextcloud Backup Manager** theo [manifest RC1](docs/MANIFEST.md). Python 3.12+, Flask, Jinja, Gunicorn; không cần Node hoặc database.

**Trạng thái: bản triển khai phát triển 0.1.0, chưa nghiệm thu RC1 production.** Không có kết nối tới máy `mayhome` trong phiên triển khai này. Các giá trị từ manifest là yêu cầu dự kiến, không phải trạng thái live.

## Chạy trong môi trường phát triển

```sh
cd /workspace/mayhome
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
.venv/bin/pip install -e . --no-deps
.venv/bin/pytest -q
.venv/bin/python scripts/dev_server.py
```

Server Gunicorn nghe **127.0.0.1:5010**. Script in đường dẫn tệp khóa đăng nhập tạm thời (thư mục 0700, tệp 0600); đọc khóa tại terminal riêng. Không đưa khóa vào Git/chat. Khóa và phiên thay đổi mỗi lần chạy. Cookie HTTP chỉ dùng ở chế độ dev loopback này. Trong môi trường cloud onboarding, kiểm tra bằng request nội bộ; không có public preview được cấu hình.

Nếu chưa có collector JSON hợp lệ, ứng dụng hiển thị `UNKNOWN`, API dữ liệu trả 503; đây là trạng thái đúng, không phải dữ liệu demo. `/healthz` chỉ chứng minh web còn sống. `/readyz` yêu cầu đăng nhập và collector còn mới.

## Đã triển khai

- Post-Boot Health Monitor: hai connector systemd/Docker độc lập, tra IP container mỗi vòng, readiness + số kết nối Edge, grace 5 phút sau boot; mặc định chưa cấu hình. Xem [hướng dẫn](docs/post-boot-monitor.md).
- Bốn trang tiếng Việt: Tổng quan, Backup Manager, Nhật ký, Cấu hình; giao diện thích ứng desktop/mobile và chế độ tối.
- API v1 GET-only, phân trang và giới hạn đầu vào; snapshot detail chỉ là metadata.
- Phiên đăng nhập ký, CSRF cho đăng nhập, giới hạn thử sai, Host allowlist, CSP, no-store; không tin header Cloudflare làm xác thực.
- Collector riêng: allowlist unit/container, kiểm tra mount UUID từ mount namespace của PID 1, inventory giới hạn không đi vào user data, log sự kiện được lọc, xuất JSON atomic.
- Nhận diện baseline, checkpoint được bảo vệ, hai checkpoint hoàn tất mới nhất và staging; không coi checksum tồn tại là đã kiểm chứng.
- Unit systemd, installer không tự kích hoạt, rollback chỉ dừng MHC; tài liệu audit/triển khai.

## Phần còn chờ nguồn dữ liệu thật và nghiệm thu

- Stage 0 trên máy thật, xác minh schema log/script RC1.3, quyền collector và host namespace.
- Maintenance, Backup Guard, elapsed/stage tin cậy, thời lượng/lịch sử đầy đủ sau reboot, retention audit và bằng chứng checksum còn UNKNOWN. Chưa có timeline 14–30 ngày hoặc bộ dịch Anh đầy đủ.
- Chưa profile collector/disk I/O trên host; inventory hiện quét metadata tối đa 1.000 entry mỗi lần thu thập, cần điều chỉnh cadence sau đo thực tế.
- Chưa audit quyền bảo mật độc lập, đo coverage 95%, kiểm tra AA toàn diện, runtime 15 phút trên host, backup 02:00, restore test hay kiểm chứng Cloudflare Access.
- Chưa cài/enabled service, thay DNS/tunnel, tạo tag RC1 freeze hoặc merge.

Không có nút chạy backup, xóa, restore, retention apply, thay timer hay điều khiển Docker.

Xem [kiến trúc](docs/architecture.md), [vận hành](docs/ops-runbook.md), [API](docs/api-v1.md), [ma trận nghiệm thu](docs/acceptance-matrix.md), [ảnh desktop](docs/evidence/dashboard-1440.png), [ảnh mobile](docs/evidence/dashboard-360.png).

## Bước tiếp theo: audit tại máy thật

Operator có thể chạy [audit Python độc lập](docs/run-audit-on-mayhome.md) rồi gửi báo cáo JSON. Build bằng `python3 scripts/build_audit_bundle.py`; artifacts gồm `.pyz` và checksum, không cần cài dependencies trên host. Audit không tự cấp quyền triển khai hay thay đổi production.

Hồ sơ review mới: [dual connector monitoring và hướng dẫn cài đặt](docs/review-dual-connectors.md). Bản review dùng nhánh `review/mhc-dual-connectors`; chưa merge hay deploy lên mayhome.


## Lấy bản review từ GitHub

Nhánh review: `review/mhc-dual-connectors` (không phải bản RC1 đã nghiệm thu production). Sau khi nhánh được push, lấy source vào thư mục mới:

```sh
git clone --branch review/mhc-dual-connectors --single-branch https://github.com/kuongnguyen/mayhome.git mayhome-center-review
cd mayhome-center-review
```

Nếu thư mục đã tồn tại, giữ nguyên và kiểm tra trước; không reset/overwrite. Review `docs/review-dual-connectors.md` trước khi cài. Clone source không kích hoạt dịch vụ hoặc thay đổi Nextcloud/backup/tunnel.
