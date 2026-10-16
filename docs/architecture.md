# Kiến trúc và Stage 0

## Bằng chứng hiện có

Audit ngày 2026-10-16 trên cloud: Linux, Python 3.12.14, uid 1000, checkout `/workspace/mayhome` chưa có commit và không có tệp ứng dụng ban đầu. Đây **không phải máy production mayhome**. Chưa có SSH hoặc một kênh truy cập host được cấp. Không chạy lệnh trên production.

Chưa xác minh: phần cứng, systemd/Docker thực tế, mount UUID, file hash engine, quyền truy cập, timer retention, backup 02:00, maintenance, marker, tunnel/DNS, cổng 5010 trên mayhome. Các giá trị UUID/path trong code là expected policy từ manifest, không phải quan sát.

## Luồng dữ liệu

Browser → Cloudflare Access + tunnel (chưa cấu hình) → Gunicorn loopback → Flask/session → JSON collector giới hạn 1 MiB.

Root collector (chỉ sau audit/phê duyệt) → lệnh argv cố định, timeout/output cap → metadata không có secrets → `/run/mayhome-center/status.json` root:mayhome-center 0640 trong thư mục 0750 → web không đặc quyền.

Không có web-to-root IPC hay action endpoint. Collector không nhận argv, tên unit, container hoặc đường dẫn từ HTTP. `domain.py` định nghĩa hợp đồng/status và registry tường minh. `collector.py` chứa adapter host; `store.py` là cổng đọc JSON. Module mới cần routes/service riêng qua hợp đồng này, không tải plugin động. Bản đầu tổ chức gọn trong một package vì repo rỗng.

## ADR xác thực

Chọn xác thực ứng dụng bằng khóa ngẫu nhiên tối thiểu 32 byte, phiên ký với khóa riêng ổn định tối thiểu 32 byte. Cloudflare Access/MFA là lớp bổ sung khi triển khai; ứng dụng không sử dụng header/JWT Cloudflare để bỏ qua đăng nhập. Không cần JWKS vì không chọn JWT làm xác thực backend. Phiên 30 phút, cookie Secure/HttpOnly/SameSite Strict; đăng nhập kiểm tra CSRF. Rate limit bộ nhớ mỗi worker chỉ là lớp giảm thiểu, production cần rate limiting ở Access/edge. Không tin X-Forwarded-*; Gunicorn tắt forwarded allowlist. Cookie Secure bật độc lập với forwarded scheme.

## Chính sách trạng thái

STALE ưu tiên khi collector >90 giây; UNKNOWN khi chưa có hoặc dữ liệu không đủ. Sai mount, container dừng, marker hoặc backup failed → CRITICAL; engine active → RUNNING nhưng không suy ra thành công. Hiện chưa đủ evidence để phát HEALTHY: maintenance/guard/recent-backup policy chưa xác minh. Lịch dùng giá trị systemd host; đồng hồ giao diện UTC+07. Có thể báo lỗi một phần với 200; unavailable toàn bộ 503.

Snapshot COMPLETE chỉ `present`, không phải structure_valid/checksum/restore verified. Job `reported_success` chỉ là sự kiện PASS từ journal, confidence partial; không tuyên bố duration, khởi đầu, lần backup hoàn tất có kiểm chứng. Lịch sử giới hạn 200 record/30 ngày, có thể thiếu.
