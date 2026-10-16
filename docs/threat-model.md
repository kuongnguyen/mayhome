# Mô hình đe dọa và ma trận quyền

| Identity | Cho phép | Không được cấp |
|---|---|---|
| Web mayhome-center | Đọc code, status JSON, cookie signing key và auth token; bind loopback | root, docker group/socket, journal, backup/data/config/SQL, sudo |
| Collector root | Đọc unit allowlist, Docker State.Running, mount metadata, marker stat, COMPLETE/file metadata và sự kiện backup | API từ web, shell tùy ý, đọc SQL/config/user files, backup/restore/delete/retention apply |
| Operator | Review release, audit và cài file MHC sau phê duyệt | Thay đổi engine/timer/Nextcloud trong RC1 |

Collector root-owned cùng toàn bộ code/venv/ancestor dưới `/opt`; account web không có quyền ghi. Installer không copy venv dev hoặc env secret. Unit sandbox root collector có ProtectSystem=strict, chỉ ghi /run/mayhome-center; Docker daemon không chịu ràng buộc filesystem này nên allowlist argv và code ownership là ranh giới bắt buộc. Đọc socket Docker từ collector vẫn có mức tin cậy root: cần review độc lập trước deploy. Web không được đọc socket.

Nguy cơ: forged identity header (bị bỏ qua), path traversal (ID regex + dirfd/O_NOFOLLOW), symlink parent (open từng component), stale telemetry (TTL), output/log injection (Jinja escaping và journal chỉ xuất thông báo enum), hung process (timeout + output cap + kill process group), brute force (rate limit từng worker + yêu cầu edge). Không trả raw docker inspect, MESSAGE, exception hoặc file nội dung.

Giới hạn: JSON tin cậy chỉ khi parent root-owned, 0750 và file root-owned 0640; web reader kiểm tra regular/no-symlink/size/schema nhưng không thay thế quyền host. Collector metadata có thể bị một writer production độc lập thay đổi giữa các stat; inventory không đọc file nội dung nhưng collector kiểm tra root ownership/non-writable ancestors của backup root, nhưng vẫn cần kiểm tra ownership/ACL các directory con trong Stage 0. Chưa kiểm chứng sandbox unit trên host. Chưa có security audit đầy đủ hoặc coverage 95%.

Post-Boot Monitor: root collector HTTP GET loopback và IPv4 Docker được xác minh qua inspect của đúng cloudflared-tunnel/network cuongnh_nextcloud-net. Opt-in độc lập, no redirects/curlrc/tokens/raw metrics. Base unit deny-any + localhost; Docker cần drop-in chỉ cho CIDR thật đã review. Không tự mở cả private network. Prefix/IP RFC1918 kiểm tra ở code và identity kiểm tra lại sau probe; web không truy cập socket. Xem post-boot-monitor.md.
