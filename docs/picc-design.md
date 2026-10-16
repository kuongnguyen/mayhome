# MAYHOME 0.3.0 — giao diện theo PiCC

Mẫu đối chiếu là [kuongnguyen/picc](https://github.com/kuongnguyen/picc/tree/535af154cab4b5425b72537decd65cd1dc27d1a4), chủ yếu `frontend/src/style.css` và `frontend/src/App.vue`. PiCC dùng thiết kế monitoring sáng: sidebar 224 px, nền xám nhạt, điểm nhấn xanh dương, biểu tượng nét mảnh và thẻ bo góc.

MAYHOME áp dụng thiết kế này vào bốn trang Tổng quan, Quản lý backup, Nhật ký, Cấu hình và trang đăng nhập:

- Sidebar sáng có biểu tượng và trạng thái trang đang chọn; trên mobile chuyển thành thanh điều hướng ngang.
- Thanh đầu trang có breadcrumb, nhãn chỉ đọc và đồng hồ Việt Nam.
- Thẻ tóm tắt có điểm nhấn xanh lá/xanh dương/tím/vàng; thẻ xuất hiện trước chi tiết tunnel.
- Nhóm lịch sử, snapshot và bảng bằng chứng giữ đầy đủ nội dung. Từng mức HEALTHY/PASS/WARNING/CRITICAL/UNKNOWN/STALE vẫn có chữ riêng.
- Giao diện tối theo lựa chọn hệ điều hành; hỗ trợ focus bàn phím, skip link và reduced motion.
- Biểu tượng SVG nội bộ, font hệ thống, không tải CDN, không thêm Node/Vue vào MAYHOME.

Chỉ thay trình bày, nhãn kết quả và version 0.3.0. Không thay API, collector probes, xác thực, cấu hình production hoặc backup engine. Không thêm dữ liệu giả vào production, không tạo đồ thị không có nguồn dữ liệu. PiCC được đọc làm mẫu; không sửa repository PiCC hoặc nối quyền điều khiển giữa hai máy.

## Kiểm chứng

150 pytest; 15 trường hợp Backup Manager; 24 trường hợp connector; 38 trường hợp thiết kế (login, bốn trang ở 360/390/768/1440 px, light/dark và keyboard skip). Kết quả ở `docs/evidence/picc-*-results.*`. Ảnh có chữ GIẢ LẬP và không phải trạng thái mayhome. Kiểm thử này không thay cho audit accessibility đầy đủ hoặc nghiệm thu runtime trên host.

Xem ảnh [desktop sáng](evidence/fixture-picc-overview-light-1440.png), [mobile sáng](evidence/fixture-picc-overview-light-360.png), [desktop tối](evidence/fixture-picc-overview-dark-1440.png), [đăng nhập](evidence/fixture-picc-login-light-1440.png).

## Review và nâng cấp

Dùng quy trình đã có trong [hướng dẫn nâng cấp/quay lui](backup-manager-phase2.md). Không chạy lại installer lần đầu. Trong checkout `~/mayhome-center-review`, kiểm tra local changes trước khi cập nhật:

```sh
git status --short
git fetch origin review/mhc-dual-connectors
git diff HEAD..origin/review/mhc-dual-connectors --stat
```

Sau review và khi tracked source không có sửa đổi cục bộ:

```sh
git merge --ff-only origin/review/mhc-dual-connectors
git rev-parse HEAD
sudo python3 packaging/upgrade.py prepare --reviewed
```

Chỉ sau `Prepared release:` thành công, dùng SHA đầy đủ vừa review (không dùng SHA ví dụ của bản 0.2.0):

```sh
MHC_REVIEW_SHA=$(git rev-parse HEAD)
sudo python3 packaging/upgrade.py activate --sha "$MHC_REVIEW_SHA" --reviewed
sudo systemctl start mayhome-center-collector.service
```

Dừng nếu lệnh báo lỗi. Helper chỉ chuyển MHC khi backup inactive/success, giữ release trước để rollback. Sau đó kiểm tra web/collector/timer, `/healthz`, đăng nhập và `/readyz`, số snapshot, hai connector, điều hướng và phiên bản 0.3.0. Nếu lỗi dùng `rollback --sha "$MHC_REVIEW_SHA" --reviewed` như tài liệu nâng cấp. Bản 0.3.0 chưa được triển khai hay nghiệm thu trên mayhome bởi cloud.
