# Review checksum và retention chỉ đọc — 16/10/2026

Phạm vi: review mã MAYHOME 0.5.0 tại `8c3548f`, manifest và kiểm thử hiện có. Không chạy hash/gzip trên SQL, không chạy công cụ retention trên mayhome, không cài unit mới hoặc thay chính sách backup. Đây là hồ sơ review, không phải bằng chứng checksum PASS hoặc retention an toàn trên host.

## Kết luận

1. Metadata inventory hiện làm đúng vai trò: kiểm tra đường dẫn cố định, loại file, COMPLETE và định dạng manifest; không gắn db_checksum_verified=true. 14 kiểm thử liên quan inventory/checksum/symlink/staging/mount đã đạt; 38 test ngoài phạm vi được deselect.
2. Chưa có công cụ tính SHA256/gzip riêng trong repo; chưa có importer bằng chứng để gỡ CHECKSUM_UNKNOWN. Không thể xem checksum_metadata() là verifier.
3. Retention API chỉ trả chính sách dự kiến14ngày và mode unknown. Mã `/usr/local/sbin/nextcloud-backup-retention` trên mayhome chưa được cung cấp, nên chưa review logic thật, tham số hoặc chế độ DRY-RUN. Không chạy script đó dù thêm `--dry-run`/`--help` trước khi review.

## Điểm cần hoàn tất trước khi triển khai verifier

| Hạng mục | Kết quả review / yêu cầu |
|---|---|
| Hash thực | Chưa có: cần tính SHA256 của chính file gzip được chọn và so với manifest độc lập |
| Gzip | Chưa có: phải đọc đến EOF, kiểm CRC/truncation và các gzip member; hash khớp chưa chứng minh gzip hợp lệ |
| Baseline | Có DB tên cố định nhưng chưa có manifest SHA256 theo schema checkpoint; không tạo hash rồi so với chính hash mới đó để tuyên bố verified |
| Đường dẫn | Giữ allowlist và dirfd/O_NOFOLLOW; không dùng tên file tùy ý trong manifest làm input cho sha256sum -c/root shell |
| Tính nhất quán | COMPLETE không chứng minh bất biến; kiểm inode/device/size/mtime/ctime, manifest và marker trước/sau; đổi trạng thái thì INCONCLUSIVE |
| Chạy đồng thời | Kiểm guard/backup service/marker trước và trong lúc chạy. Lock riêng chỉ khóa hai verifier, không chặn engine độc lập; cần review cơ chế lock thật của engine nếu muốn bảo đảm phối hợp |
| I/O/CPU | Tách khỏi collector30s và web request; chỉ một DB gzip được chọn, không duyệt data/config/user files |
| Bằng chứng | Chỉ xuất ID, fingerprint file/manifest, thời điểm, bytes, SHA match/gzip result và mã lỗi. Không xuất SQL, nội dung config hoặc dữ liệu cá nhân |
| Web | Không thêm nút/root broker/shell; đọc báo cáo đã xác minh quyền và nguồn, có tuổi bằng chứng |

Các mục trên là điều kiện cho công cụ mới, không phải lỗ hổng được xác nhận của metadata collector hiện tại.

## Thiết kế verifier đề xuất, chưa chạy trên production

- CLI độc lập, operator chọn đúng một checkpoint committed; không tự chạy theo mỗi status refresh. Không có retention apply.
- Không ghi vào backup root; dùng O_NOATIME khi đọc file nếu cần bảo toàn atime, không âm thầm hạ điều kiện này. Báo cáo chỉ ghi vào vùng MHC riêng sau review.
- Đọc gzip một lượt, đồng thời hash compressed bytes và kiểm gzip trong bộ nhớ, bỏ dữ liệu giải nén. Không tạo SQL tạm, không restore hoặc kết nối database.
- Đề xuất ban đầu: tốc độ đọc compressed tối đa4MiB/s, timeout30phút, hard cap4GiB compressed/64GiB decompressed, bộ nhớ giới hạn. Các giá trị phải chốt sau khi biết kích thước DB thật. Vượt giới hạn là INCOMPLETE/UNKNOWN, không phải checksum mismatch.
- Ưu tiên CPU/I/O thấp chỉ áp dụng tiến trình verifier mới; không renice hoặc điều khiển tiến trình backup/Nextcloud.
- Chỉ PASS khi manifest hợp lệ, SHA khớp, gzip đạt và fingerprint/guard nhất quán. SHA mismatch/gzip lỗi là FAILED; không sửa hoặc xóa file.
- PASS chỉ xác nhận DB gzip tại thời điểm kiểm tra, không xác nhận toàn bộ user files hoặc khả năng restore.

Công thức ước lượng riêng lượt đọc: thời gian tối thiểu = compressed_bytes / tốc độ đọc. Gzip/CPU và chờ I/O có thể làm lâu hơn. Cần chạy ngoài lượt backup tự nhiên và chỉ sau khi operator review kế hoạch cụ thể.

## Review retention: hai lớp bằng chứng

**Lớp1 — mô phỏng độc lập chỉ đọc:** từ inventory đầy đủ, liệt kê những checkpoint có thể thuộc diện xem xét theo chính sách đề xuất. Không gọi script engine, không xóa, không chạy find -delete/rm/rsync. Kết quả phải ghi rõ projection, không phải dry-run của engine đang cài.

**Lớp2 — đối chiếu script thật:** cần source, SHA256 và các helper/config được script sử dụng. Review giá trị mặc định DRY-RUN, option parsing, đường apply có thể đi vào từ env hay không, trap/hooks/helper có ghi file hoặc điều khiển service trước khi xét flag không. Hash script chỉ định danh phiên bản, không chứng minh an toàn.

Các ràng buộc bắt buộc:

- Bảo vệ baseline root và checkpoint cố định `20261015-210345` tuyệt đối; bảo vệ hai checkpoint COMPLETE mới nhất.
- Không đưa staging, symlink, tên lạ, ngày tương lai, incomplete hoặc inventory chưa đủ vào danh sách có thể xóa. Không dọn staging tự động.
- Chốt rõ tuổi theo tên directory hay mtime, múi giờ, 14*24h hay ngày lịch và điều kiện < hay <=. Ví dụ find -mtime +14 không tự tương đương cutoff đúng14*24h.
- Chỉ khi xác định được timestamp và toàn bộ inventory mới tính candidate; có lỗi quét/quyền/UUID thì kết quả UNKNOWN, không trả candidates=0 để giả an toàn.
- Kiểm bảo vệ trước xét tuổi; danh sách candidate không phải phê duyệt xóa. Kể cả audit không có candidate cũng không gỡ RETENTION_UNVERIFIED trước khi đối chiếu policy thật.
- Không bật retention timer hoặc chạy service retention để lấy bằng chứng.

## Dữ liệu cần lấy trên mayhome — chỉ metadata

Không dùng đường dẫn của file cá nhân. Các đường dẫn dưới đây là đường dẫn cố định từ manifest. stat không đọc SQL; sha256sum chỉ đọc script retention nhỏ, không thực thi nó:

```sh
sudo stat -c 'type=%F size_bytes=%s owner=%U:%G mode=%a %n' \
  /mnt/nextcloud_backup/nextcloud/snapshots/20261015-210345/database/nextcloud.sql.gz \
  /mnt/nextcloud_backup/nextcloud/snapshots/20261015-210345/manifests/database.sha256 \
  /usr/local/sbin/nextcloud-backup-retention

sudo sha256sum /usr/local/sbin/nextcloud-backup-retention

systemctl show nextcloud-auto-backup.service \
  -p ActiveState -p SubState -p Result

systemctl show nextcloud-backup-retention.timer \
  -p LoadState -p ActiveState -p UnitFileState
```

Nếu retention script không tồn tại, ghi nhận không có, không tự cài. Nếu stat cho thấy symlink/filetype bất thường, dừng việc chọn input. Chưa gửi rawscript/config lên chat nếu có secret; ưu tiên đối chiếu source từ repo đã xác định. Bằng chứng vận hành do operator cung cấp giữ cục bộ, không tự công bố GitHub.

## Bộ kiểm thử cần có cho công cụ mới

SHA khớp/lệch, gzip cắt cụt/CRC lỗi/multi-member/trailing data, manifest quá lớn/traversal/nhiều target, symlink ở từng cấp, file đổi trong lúc đọc, engine bắt đầu chạy, read/expanded/time budget, nguồn thiếu/permission và báo cáo stale. Retention cần test biên14ngày/múi giờ/tên sai/ngày tương lai, baseline/fixed/latest2, staging và inventory không đầy đủ. Fixtures chỉ dùng dữ liệu tổng hợp nhỏ, không dùng SQL thật.

**Trạng thái cuối review:** metadata PASS đúng phạm vi; verifier thực chưa có; retention producer chưa được review; chưa cho phép chạy hash SQL hoặc retention script trên mayhome từ hồ sơ này.

## Cập nhật: verifier độc lập đã được chuẩn bị

Hồ sơ trên mô tả mã tại commit8c3548f. Sau đó [MHC Verifier0.1.0](verifier.md) được bổ sung và kiểm thử bằng dữ liệu giả lập, tách khỏi collector/web. Việc có mã mới không đồng nghĩa đã xác minh checksum hoặc retention trên mayhome. Source/operator guard đã nhận trong chat và hồ sơ review riêng vẫn không được đưa vào Git; công cụ mới không gọi các script producer đó.
