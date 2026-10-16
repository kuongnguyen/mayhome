# Backup Manager 0.2.0 — review và nâng cấp

Bản 0.1.0 đã được operator kiểm tra trên mayhome: web/collector hoạt động, đăng nhập thành công, hai connector HEALTHY với 4 kết nối Edge; operator xác nhận Cloudflare Access trước khi vào ứng dụng. Bản 0.2.0 dưới đây mới được kiểm thử trong cloud, chưa cài trên mayhome. Không coi kiểm thử giả lập là nghiệm thu production.

## Phạm vi

- Lịch sử job chỉ ghép START và PASS/FAILED khi cùng invocation, boot, engine và thời gian hợp lệ. Thiếu bằng chứng phiên thì hiển thị kết quả engine báo, không dựng thời lượng. Stage hiện tại chỉ lấy từ invocation/boot đang chạy.
- Inventory phân biệt baseline/checkpoint/staging; kiểm tra metadata cấu trúc cố định và định dạng manifest checksum. Không mở SQL, config.php, .env hoặc cây dữ liệu cá nhân; không tính hash SQL. `structure_valid` không chứng minh checksum hay khả năng restore.
- Inventory tối đa 500 entry, ngân sách 2 giây, cache metadata tối đa 180 giây với timestamp riêng. Mount không đúng thì không quét. Không biết số snapshot thì hiển thị dấu —.
- Chẩn đoán riêng mount, container, timer, maintenance, marker, inventory, checkpoint được bảo vệ, lịch sử, checksum và retention. Dữ liệu quá hạn không giữ badge PASS.
- `MHC_NEXTCLOUD_STATUS=1` là tùy chọn: chỉ gọi `occ status --output=json` để đọc installed/maintenance/needsDbUpgrade/version. Mặc định chưa bật. Không bật/tắt maintenance, không chạy backup, retention hay restart connector.

Các cảnh báo CHECKSUM_UNKNOWN, HISTORY_INCOMPLETE, RETENTION_UNVERIFIED vẫn hợp lệ. Lịch sử chỉ từ tối đa 1.000 dòng journal trong 30 ngày; thiếu START hoặc log không đúng grammar sẽ được ghi nhận thiếu bằng chứng. Không bảo đảm lịch sử đầy đủ 30 ngày.

## Review trước khi nâng cấp

Review các module `backup_history.py`, `backup_diagnostics.py`, `snapshot_metadata.py`, thay đổi collector và `packaging/upgrade.py`. Xem kiểm thử `test_backup_phase2.py`, `test_upgrade.py`, cùng bằng chứng giả lập `docs/evidence/backup-manager-browser-results.json`. Không dùng lại installer lần đầu vì `/opt/mayhome-center` đã tồn tại.

Nâng cấp chỉ tác động ba unit của MHC; giữ nguyên `/etc/mayhome-center.env`, unit và drop-in hiện có, Nextcloud, backup engine, timer backup, ổ đĩa, Cloudflare Access/DNS/tunnel. Helper giữ nguyên toàn bộ thư mục MHC cũ để quay lui. Cần dung lượng cho thêm một release và venv.

## Chuẩn bị release trên mayhome

Trong checkout đang có, kiểm tra trước:

```sh
cd ~/mayhome-center-review
git status --short
git fetch origin review/mhc-dual-connectors
git diff HEAD..origin/review/mhc-dual-connectors --stat
```

Nếu có sửa đổi cục bộ, lưu và review chúng trước. Khi checkout sạch và đã review diff:

```sh
git merge --ff-only origin/review/mhc-dual-connectors
git rev-parse HEAD
sudo python3 packaging/upgrade.py prepare --reviewed
```

`prepare` cài dependencies theo requirements.lock vào `/opt/mayhome-center-releases/<SHA>` và smoke-test web; dịch vụ cũ vẫn chạy. Nếu chuẩn bị thất bại, chưa activate; kiểm tra lỗi thay vì xóa thư mục release cũ. Helper không ghi đè release đã tồn tại. Đối chiếu SHA đầy đủ với commit đã review.

## Kích hoạt sau review

Chọn lúc backup không chạy; helper yêu cầu service backup loaded/inactive và Result=success. Không dừng backup để đáp ứng điều kiện này. Lệnh sau chỉ nâng cấp MHC, có gián đoạn ngắn ở giao diện:

```sh
MHC_REVIEW_SHA=$(git rev-parse HEAD)
sudo python3 packaging/upgrade.py activate --sha "$MHC_REVIEW_SHA" --reviewed
sudo systemctl start mayhome-center-collector.service
systemctl show mayhome-center.service mayhome-center-collector.timer -p Id -p ActiveState -p SubState
systemctl show mayhome-center-collector.service -p Result -p ExecMainStatus
curl --disable --noproxy '*' --max-time 5 -fsS http://127.0.0.1:5010/healthz
systemctl show nextcloud-auto-backup.service -p ActiveState -p SubState -p Result
```

Qua `https://mayhome.cuong.win`, đăng nhập như trước. Kiểm tra `/readyz`, trang Quản lý backup, số snapshot/lịch chạy, hai connector và từng lý do chưa xác minh. Không gửi khóa đăng nhập vào chat. Thu thập kết quả và so với trước nâng cấp; còn cần theo dõi host 15 phút và một lượt backup tự nhiên lúc 02:00. Helper tự quay lại đường dẫn cũ nếu thao tác activate báo lỗi, nhưng systemctl start thành công chưa chứng minh ứng dụng đã sẵn sàng.

Nếu kiểm tra không đạt, quay lui đúng SHA vừa kích hoạt:

```sh
sudo python3 packaging/upgrade.py rollback --sha "$MHC_REVIEW_SHA" --reviewed
sudo systemctl start mayhome-center-collector.service
```

Không xóa release/preserved directory trước khi nghiệm thu. Rollback cũng yêu cầu backup inactive/success. Khóa và drop-in không bị thay đổi.

## Tùy chọn đọc maintenance

Chỉ sau khi review lệnh `occ status` và muốn bật nguồn dữ liệu này, cài drop-in riêng:

```sh
sudo install -o root -g root -m 0644 packaging/systemd/mayhome-center-collector.service.d/nextcloud-status.conf.example /etc/systemd/system/mayhome-center-collector.service.d/nextcloud-status.conf
sudo systemctl daemon-reload
sudo systemctl start mayhome-center-collector.service
```

Nếu đích đã có nội dung, review và giữ bản cũ trước khi thay. Lệnh này không cần restart Nextcloud hay cloudflared. Nếu không lấy được status, maintenance vẫn UNKNOWN. Chưa bật tùy chọn thì MAINTENANCE_UNKNOWN là dự kiến.

## Nghiệm thu còn lại

Đối chiếu grammar journal của backup engine thật, metadata checkpoint, chính sách retention và bằng chứng checksum/restore độc lập. Đo thời gian collector/I/O, theo dõi backup tự nhiên và reboot thật vào cửa sổ bảo trì. Không tạo sự cố mất Edge trên connector production chỉ để thử UI; kiểm thử container Running nhưng Edge=0 được thực hiện bằng fixture cô lập.

## Bản vá 0.2.1

Operator phát hiện trên 0.2.0 bảng evidence bị chèn lặp trong h1, làm chữ chồng và đẩy nội dung xuống. Bản vá bỏ bản chèn nhầm, giữ đúng một bảng sau inventory; bổ sung kiểm tra h1, ID duy nhất và vị trí bảng trong Chromium ở bốn kích thước. Không thay chính sách thu thập hoặc xác thực. Dùng cùng quy trình prepare/activate với SHA của bản vá; rollback sẽ về release 0.2.0 đang chạy.
