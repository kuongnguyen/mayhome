# Post-Boot Health Monitor — hai connector độc lập

RC1 chỉ đọc. Không restart/recreate container, sửa Compose/tunnel/DNS, thay Nextcloud hoặc backup.

## Bằng chứng host do operator cung cấp

Ngày 2026-10-16, operator chạy lệnh trên mayhome và gửi kết quả:

- `cloudflared.service`: PID 4029 trùng listener 127.0.0.1:20241, active; readiness 200 và gauge 4 tại lần kiểm tra.
- `cloudflared-tunnel`: Running, NetworkMode `cuongnh_nextcloud-net`, IP lúc kiểm tra 172.18.0.2; readiness 200 và gauge 4.
- Log container có 4 lần unregister rồi 4 lần register; chưa có timestamp đầy đủ để quy nguyên nhân cho reboot.

Đây là bằng chứng operator cung cấp tại thời điểm đó, không phải trạng thái hiện tại do cloud trực tiếp quan sát. Operator sau đó xác minh network CIDR 172.18.0.0/16, gateway 172.18.0.1 và backup service inactive/dead/Result=success. Tunnel ID/hostname mapping, DNS/Access và khả năng khởi động tự động vẫn chưa xác minh. Không suy ra hai connector cùng tunnel từ số kết nối bằng nhau.

## Collector và hợp đồng

`data.post_boot.connectors.systemd` và `.docker` có status, code, observed_at, uptime_sec, boot_id, grace_seconds, ready_http_status, edge_connections, scope=cloudflared_to_edge và public_route_verified=false.

- Systemd: chỉ đọc ActiveState/MainPID của đúng `cloudflared.service`; probe cố định `http://127.0.0.1:20241/{ready,metrics}`. Không phát MainPID/raw properties ra API. Service unknown/inactive không thể HEALTHY dù endpoint trả 200.
- Docker: inspect đúng tên `cloudflared-tunnel`, chỉ chọn Id/Name/State.Running/HostConfig.NetworkMode và IP trên đúng network `cuongnh_nextcloud-net`. Không đọc full inspect/env/labels/tokens. Inspect IPAM.Config của network đó, xác minh một IPv4 subnet RFC1918 prefix /16.. /30 và IP thuộc subnet, không là network/broadcast. Reject loopback/link-local/public/IPv6/host networking/nhầm identity. URL chỉ được tạo từ địa chỉ IPv4 đã kiểm tra, cổng 20241 và hai path cố định.
- Không cache IP giữa các vòng. Inspect lại container sau probe; nếu Id/IP/Running/network mode thay đổi, bỏ kết quả và trả UNKNOWN/TUNNEL_CONTAINER_CHANGED. Không dùng kết quả connector khác thay cho một connector lỗi.

API GET `/api/v1/host/post-boot` và overview trả cùng hợp đồng. UI có hai hàng riêng, runtime không đồng nghĩa connectivity. Web chỉ đọc status JSON, không Docker socket. Collector errors ghi rõ tên nguồn systemd/docker.

| Evidence từng connector | Status |
|---|---|
| Readiness 200, metrics HTTP 200, gauge >0, runtime xác minh | HEALTHY cho connector → Edge |
| Readiness 503 hoặc gauge 0, uptime <300 giây | WARNING / chờ phục hồi sau boot |
| Bằng chứng mất kết nối và uptime >=300 giây hoặc không biết uptime | CRITICAL |
| Runtime inactive/stopped | WARNING trong grace, CRITICAL ngoài grace |
| Thiếu quyền/Docker/network config, timeout, metric lỗi, service unknown, chưa opt-in | UNKNOWN (không tự suy ra disconnected) |
| JSON collector >90 giây | STALE ở tổng và từng connector |

Tổng ưu tiên CRITICAL > UNKNOWN > WARNING > HEALTHY; chỉ HEALTHY khi cả hai connector HEALTHY. Một connector chưa cấu hình vẫn UNKNOWN. Trạng thái bảo vệ backup độc lập. Khi trình duyệt offline hoặc polling lỗi, tất cả badge tunnel hạ UNKNOWN; khi phục hồi, lấy lại dữ liệu server. Không có progress giả.

## Bật sau review

Mặc định cả hai opt-in tắt. Chưa kích hoạt trên production.

1. Review [gói triển khai](review-dual-connectors.md), quyền collector và mapping endpoint. Thu subnet bằng lệnh chỉ đọc:

   `docker network inspect --format '{{json .IPAM.Config}}' cuongnh_nextcloud-net`

2. Điền `packaging/systemd/mayhome-center-collector.service.d/tunnel-monitor.conf.example` thành drop-in root-owned chỉ sau review: `MHC_TUNNEL_MONITOR=1`, `MHC_TUNNEL_DOCKER_MONITOR=1` và `IPAddressAllow=<CIDR thực tế đã xác minh>`. File review đã điền 172.18.0.0/16 theo kết quả network inspect do operator cung cấp; vẫn cần kiểm tra lại trước activation. Không dùng `IPAddressAllow=any` hoặc mở toàn bộ private network.
3. Base collector unit vẫn `IPAddressDeny=any`, `IPAddressAllow=localhost`, AF_INET/AF_UNIX. Không thêm Docker CIDR vào base unit tự động. Không có drop-in đúng thì probe Docker có thể UNKNOWN vì sandbox chặn, đây là giới hạn cấu hình phải xử lý qua review.
4. Installer chỉ đặt file MHC, không copy/kích hoạt drop-in, không daemon-reload/enable/start. Activation cần audit Stage 0 và authorization riêng. Web account không vào Docker group.
5. Kiểm tra JSON/API/UI của hai connector, permissions và unit sandbox trên mayhome; không shutdown/reboot production để thử lỗi.

## Giới hạn và kiểm thử

Mỗi curl: tắt curlrc, no redirects, HTTP nội bộ không credential, max-time 2s, runner timeout 3s, output <=64KiB. Docker/systemd inspect timeout 3s, output 4KiB. Collector timer khoảng 30s cộng thời gian thu thập. Kết nối nội bộ không đi ra Internet. Chỉ network/root code đã review có thể định nguồn IP; không có URL input từ browser.

Đây chưa phải public route health, DNS, Access/MFA, origin app, thông báo external hay lịch sử incident bền vững. Khi tunnel mất cần LAN/SSH/kênh độc lập để đọc cảnh báo.

Fixture tests: từng connector lỗi, Running nhưng Edge=0, IP đổi mỗi vòng, container recreation giữa probe, Docker thiếu/quyền lỗi, IP/subnet không an toàn, JSON stale, auth và GET-only. HTTP integration dùng cloudflared giả lập trên loopback, không container/host thật. Chromium kiểm tra hai hàng độc lập, responsive, network failure/recovery/offline bằng dữ liệu gắn nhãn GIẢ LẬP. Chưa có nghiệm thu reboot/15 phút/resource budget của mã mới trên host.
