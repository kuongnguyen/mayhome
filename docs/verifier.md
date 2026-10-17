# MHC Verifier 0.1.0 — review trước khi chạy trên mayhome

Công cụ CLI độc lập, không thay ứng dụng0.5.0, không cài service/timer, không thêm quyền/API/nút web. Chỉ đọc backup; không gọi script retention/guard đã cài, không restore hoặc tạo SQL tạm. Mọi kiểm thử ở cloud dùng gzip/directory giả lập.

## Ba chế độ

| Chế độ | Đọc SQL gzip? | Kết quả |
|---|---|---|
| plan --snapshot ID | Không; chỉ mở/stat file, đọc COMPLETE/manifest nhỏ | PLAN_ONLY: kích thước, fingerprint, thời gian đọc tối thiểu và budget |
| verify --snapshot ID --run | Có, đúng một file cố định | PASS/FAILED/INCONCLUSIVE với SHA256 và gzip riêng |
| retention-projection | Không; chỉ inventory directory và marker COMPLETE | PROJECTION_ONLY hoặc INCONCLUSIVE, không phải dry-run producer |

Chỉ nhận timestamp checkpoint hợp lệ. Chưa hỗ trợ baseline vì chưa có manifest độc lập theo schema cần thiết; staging không được chọn để verify. ID không phải path tùy ý.

## Giới hạn và kiểm tra an toàn

- Native root trên hostname mayhome; hai mount đúng UUID/RW; backup service loaded/inactive/success, marker recovery vắng mặt, marker dữ liệu nguồn là file thường.
- Lịch chạy backup kế tiếp phải đọc được và còn cách trên35phút. Đây là khoảng tránh lịch tự nhiên, không giữ chỗ hoặc cấm operator chạy backup thủ công.
- Đường dẫn mở từng component bằng dirfd/O_NOFOLLOW; thư mục và artifact đích phải root-owned, không group/world-writable. Marker/manifest/SQL phải regular file, không FIFO/device. Nếu quyền thực khác, trả INCONCLUSIVE; không chmod/chown backup để ép PASS.
- O_NOATIME bắt buộc cho đọc/scan nguồn, không tự fallback. Root native cần kernel/filesystem/capability hỗ trợ; không chạy trong Docker hoặc qua web.
- Manifest <=4096byte, COMPLETE <=256byte. Chỉ chấp nhận SHA256 lowercase và tên DB cố định được allowlist; không thực thi tên/path trong manifest.
- Mặc định tối đa4MiB/s compressed (cho giảm tới0.25MiB/s), compressed<=4GiB, expanded<=64GiB, wall<=1800s, CPU<=120s, address space256MiB, nice19. Limits áp dụng riêng verifier, không renice tiến trình khác. Một byte sentinel được phép để phát hiện vượt byte budget.
- Đọc compressed đúng một lượt theo khối<=64KiB; hash đồng thời kiểm gzip bằng zlib, output mỗi lần<=64KiB và bị bỏ ngay. Không tạo SQL giải nén, không đọc config.php/user files. Hỗ trợ nhiều gzip member, cap1024member; chế độ strict từ chối trailing garbage/zero padding sau member.
- CRC/truncation/EOF được kiểm; gzip hỏng dù manifest khớp vẫn không PASS.
- MHC lock riêng `/run/mayhome-center-verifier/verify.lock` ngăn hai verifier, không dùng lock của engine. Chỉ tạo thư mục/file riêng này; stdout report không tự ghi vào backup hoặc DB của MHC.
- Kiểm tra idle/mount/recovery khoảng mỗi2giây và trước/sau công việc. Không có atomic exclusion với engine độc lập: có thể có khoảng chồng lấn ngắn; phát hiện thì dừng. Không hứa dừng được I/O ở kernel uninterruptible sleep ngay lập tức.
- So fingerprint device/inode/size/mtime/ctime các descriptor trước/sau và mở lại đường dẫn; nguồn/marker/manifest thay đổi thì INCONCLUSIVE. PASS chỉ là bằng chứng tại thời điểm kiểm tra, không bảo đảm snapshot bất biến mãi mãi.

## Chính xác ý nghĩa kết quả

PASS chỉ khi SHA khớp, gzip đầy đủ/CRC đạt và kiểm tra ổn định/guard đều đạt. `restoration_verified` luôn false. FAILED là mismatch/gzip lỗi khi post-read source ổn định. INCONCLUSIVE gồm thiếu quyền/nguồn, backup đang chạy, quá gần lịch, nguồn đổi, budget hoặc lock; không biến các tình huống đó thành “file hỏng”. Trường code giữ enum lỗi, không dump exception/SQL.

Exit0 = PLAN_ONLY/PASS/PROJECTION_ONLY tùy mode; exit2 = FAILED/INCONCLUSIVE hoặc sai CLI. Không coi mọi exit0 là checksum PASS: phải xem mode/status và `db_checksum_verified`. Nếu tiến trình bị kill/mất điện không có JSON hoàn chỉnh thì không có bằng chứng PASS.

Chưa có importer/UI trust chain: verifier không gỡ CHECKSUM_UNKNOWN/RETENTION_UNVERIFIED tự động. Báo cáo có fingerprint/hash/time là dữ liệu vận hành riêng; giữ ngoài checkout, không upload GitHub. Có thể gửi bản tóm tắt mode/status/code/bytes để đối chiếu trong chat.

## Projection retention

Tuổi từ tên snapshot, timezoneUTC+07, điều kiện nghiêm ngặt age > days*86400 và days>=14, cùng cách tính tuổi đã review của producer. Bảo vệ baseline ngoài snapshots, fixed20261015-210345, hai COMPLETE-marker mới nhất; staging luôn giữ. Không đọc config/data/SQL, không tính hash trong projection.

`age_candidates_pending_integrity` chỉ là bản đủ tuổi theo metadata. Producer thực còn yêu cầu cấu trúc/hash hợp lệ; tool này không thay thế bước đó hoặc cho phép xóa. Có entry lạ/symlink/incomplete/ngày tương lai/quyền lỗi thì inventory_complete=false, candidate_count=null và không đề xuất candidate. Tên entry không thuộc ID chuẩn được che. Giới hạn500entry/2giây. Kết quả PROJECTION_ONLY không xác minh policy thực hoặc cấp quyền retention apply.

## Lấy source và build — không cần nâng cấp web

Trong checkout hiện có, kiểm tra và review diff trước khi merge; giữ local edits, không reset:

```sh
cd ~/mayhome-center-review
git status --short
git fetch origin review/mhc-dual-connectors
git diff HEAD..origin/review/mhc-dual-connectors --stat
# Khi tracked source sạch và diff đã review:
git merge --ff-only origin/review/mhc-dual-connectors
python3 scripts/build_verifier_bundle.py
(cd artifacts && sha256sum -c mayhome-verifier.pyz.sha256)
python3 -I artifacts/mayhome-verifier.pyz --help
```

Bundle stdlib không cần Flask/venv mới. Build/checksum chỉ tạo artifact riêng, không đọc SQL hoặc thay service. Review commit và code trước khi cấp sudo cho bundle; checksum cùng nguồn không tự thay thế tin cậy nguồn.

## Bước1: lập kế hoạch metadata trên mayhome

```sh
sudo python3 -I artifacts/mayhome-verifier.pyz plan \
  --snapshot 20261015-210345
```

Gửi mode/status, compressed_bytes, minimum_read_seconds và within_compressed_budget. Fingerprint/hash trong report giữ riêng nếu không cần chia sẻ. Nếu INCONCLUSIVE, xem code và dừng, không sửa backup để qua guard.

## Bước2: projection retention độc lập

```sh
sudo python3 -I artifacts/mayhome-verifier.pyz retention-projection --days 14
```

Đây là metadata projection, không chạy `/usr/local/sbin/nextcloud-backup-retention`. Candidate không có nghĩa được xóa. Không có --apply trong verifier.

## Bước3: chỉ sau review plan, chọn thời điểm ngoài backup

Lệnh sau **thực sự đọc file SQL gzip** và có tải I/O; chưa được chạy trên mayhome trong cloud. Ví dụ4MiB/s, không tự chạy tiếp sau plan:

```sh
sudo python3 -I artifacts/mayhome-verifier.pyz verify \
  --snapshot 20261015-210345 --run \
  --read-mib-per-second 4 --max-seconds 1800
```

Operator có thể giữ report trong thư mục riêng dưới home với quyền0700/0600; không redirect vào backup hoặc repo. Không cần bật service/timer, tạo approval flag retention, dừng Nextcloud hoặc thay khóa. Sau chạy, kiểm tra backup vẫn inactive/success, maintenance OFF và hai tunnel, rồi đối chiếu kết quả. Checkpoint khác phải có ID được review riêng; không loop hash toàn bộ snapshots.

## Kiểm chứng cloud

Test PASS/mismatch/CRC/truncated/garbage/multi-member/expansion/member/read/time budget, rate limit, symlink tại các cấp, quyền, source thay/path swap, backup bắt đầu, CLI explicit --run, lock MHC, boundary14ngày/protected/staging/unknown/future/entry budget và không đọc SQL ở plan/projection. Syntax/bundle help được kiểm tra bằng Python stdlib. Runtime native root mayhome/permission/I/O và hash thật vẫn pending.
