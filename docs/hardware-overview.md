# MAYHOME 0.4.0 — Tổng quan phần cứng và Backup Manager riêng

Theo yêu cầu, Tổng quan `/` là dashboard phần cứng theo PiCC: CPU, Memory, Temperature, Disk activity, Network và Uptime; desktop ba cột, tablet hai cột, mobile hẹp một cột. Thẻ đầu trang có biểu đồ nhỏ từ mẫu thật. `/backups` giữ lịch sử job, snapshot, hai ổ lưu trữ và các bằng chứng bảo vệ. Hai connector tunnel được giữ ở phía dưới Tổng quan. Không thêm biểu đồ/trị số giả vào production.

## Nguồn và cách tính

Collector MHC hiện có đọc thêm các đường dẫn Linux cố định, không thêm daemon, API ghi, lệnh shell hoặc dependency:

| Chỉ số | Nguồn | Giới hạn |
|---|---|---|
| CPU | `/proc/stat` | Delta tổng CPU; không cộng guest hai lần; idle gồm iowait; số lõi online từ cpuN |
| Memory | `/proc/meminfo` | Used = MemTotal − MemAvailable, không dùng MemFree thay MemAvailable |
| Temperature | CPU/SoC thermal zone, hwmon coretemp/k10temp | Cảm biến không hỗ trợ thì null/—; không suy ra từ nhiệt độ đĩa hoặc ngưỡng Raspberry Pi |
| Frequency | policy0 scaling_cur_freq | Tần số đại diện, không phải tần số riêng từng lõi; có thể không khả dụng |
| Disk | `/proc/diskstats` + `/sys/block/*/device` | Chỉ whole device có device; không cộng partition/loop; sector kernel = 512 byte |
| Network | `/proc/net/dev` + operstate | Tổng interface up ngoài lo; có thể đếm trùng bridge/veth trên máy Docker, không phải lưu lượng WAN được xác minh |
| Uptime | `/proc/uptime` | Thời gian từ boot; không suy thành tình trạng backup |

CPU/rates cần hai lần thu thập cùng boot, cùng bộ disk/interface và khoảng cách <=120 giây. Mẫu đầu, reboot, reset counter, thay interface, CPU hotplug, khoảng gián đoạn dài: giá trị chưa xác định thay vì số 0 hoặc spike. Sensor bị thiếu không làm mất các số liệu khác. Counter raw/tên interface và boot ID không xuất qua API phần cứng.

Lấy mẫu theo timer MHC hiện có: OnUnitInactiveSec=30s, cộng thời gian collector và độ chính xác timer; không phải realtime 5 giây. Poll trình duyệt 30 giây. Không thay timer backup hoặc timer collector trong release này.

Lưu tối đa 120 mẫu và một giờ trong chính JSON `/run/mayhome-center/status.json`, không có DB hoặc ghi vào ổ Nextcloud. `/run` là runtime tmpfs trên máy chuẩn systemd: lịch sử mất khi reboot, không hứa giữ lịch sử 24 giờ/30 ngày. Biểu đồ dùng timestamp thật, trục dọc tự động; null/khoảng mất mẫu >120s ngắt đường, không nối dữ liệu qua reboot. Khi offline/lỗi fetch, giá trị chính thành — và chart mờ để báo lịch sử. Dữ liệu stale có nhãn STALE và thời điểm mẫu.

Freshness của phần cứng nói về tuổi mẫu, không đồng nghĩa Nextcloud backup đã HEALTHY. Các cảnh báo checksum/retention vẫn thuộc bằng chứng backup và không được xóa để làm dashboard xanh.

## API

`GET /api/v1/host/hardware`: cùng session/GET-only/Host allowlist như API hiện có; trả current, history, observed_at, sample_interval_seconds, rate_state, scope, freshness, errors. Missing collector: 503. Collector cũ chưa có hardware: object trống có freshness và UI hiện —. Overview API cũng có hardware đã loại counter nội bộ.

## Kiểm chứng và nghiệm thu

Các test `test_hardware.py` kiểm tra cách tính CPU/RAM, filter disk/network, reset/reboot/gap, giới hạn lịch sử, API/auth và tách trang. `browser_hardware.py` dùng fixture GIẢ LẬP cho sáu thẻ, chart, sensor thiếu, mẫu đầu, stale/offline, breakpoint và điều hướng. Chạy lại suite backup/tunnel/design. Bằng chứng ở `docs/evidence/hardware-*-results.*`; ảnh `fixture-hardware-1440.png` và `fixture-hardware-360.png` là giả lập.

Cloud có kiểm tra đọc counter Linux thật hai mẫu và serialize, nhưng đó không phải phần cứng mayhome. Chưa nghiệm thu sensor, permission systemd, network scope, chi phí I/O/CPU hoặc hoạt động 15 phút trên mayhome.

## Nâng cấp có rollback

Theo [quy trình MHC hiện có](backup-manager-phase2.md), review SHA 0.4.0, prepare release mới khi bản cũ đang chạy, rồi activate khi backup inactive/success. Không chạy lại installer lần đầu; không cài PiCC lên mayhome, không sao chép khóa PiCC. Env/unit/drop-in cũ được giữ nguyên.

```sh
cd ~/mayhome-center-review
git status --short
git fetch origin review/mhc-dual-connectors
git diff HEAD..origin/review/mhc-dual-connectors --stat
```

Khi source sạch và diff đã review:

```sh
git merge --ff-only origin/review/mhc-dual-connectors
git rev-parse HEAD
sudo python3 packaging/upgrade.py prepare --reviewed
```

Chỉ khi `Prepared release:` thành công, kích hoạt đúng SHA đã review:

```sh
MHC_REVIEW_SHA=$(git rev-parse HEAD)
sudo python3 packaging/upgrade.py activate --sha "$MHC_REVIEW_SHA" --reviewed
sudo systemctl start mayhome-center-collector.service
```

Mẫu CPU/rates đầu tiên có thể là —; chờ thêm hai chu kỳ timer khoảng 60–90 giây. Kiểm tra version0.4.0, các thẻ có mẫu mới, sensor nào không hỗ trợ, hai connector, trang backup và readiness có xác thực. Không cần restart Nextcloud/cloudflared. Rollback dùng SHA kích hoạt như hướng dẫn chung. Sau khi chạy, thu thập riêng memory/CPU/I/O/collector duration của host và xác nhận các timer backup không thay đổi.
