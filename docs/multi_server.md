# Multi-Server / Workspace Enterprise

Mỗi Workspace dùng một database SQLite và một thư mục lưu file riêng. Trên
Windows, database của Workspace do `tftp_gui.py` tạo nằm ở
`%APPDATA%\TFTPPro\servers\<workspace_id>\efm.db`. Ứng dụng ghi danh sách
Workspace vào `%APPDATA%\TFTPPro\workspaces.json`; file này không chứa mật khẩu.

## Tạo và khôi phục Workspace

1. Chạy `python tftp_gui.py`, chọn **Create Server**, nhập tên Workspace/công ty
   và thư mục lưu trữ.
2. Tạo tài khoản `SUPER_ADMIN` (mật khẩu tối thiểu 8 ký tự). Ứng dụng chạy
   Server, Admin API và TFTP local.
3. Sau khi đóng ứng dụng, database không bị xóa. Lần sau chọn
   **Login to saved Workspace**, chọn Workspace, nhập username/mật khẩu. Ứng
   dụng khởi động lại runtime từ database đó rồi đăng nhập.
4. **Join Server** dùng để đăng nhập vào Server đang chạy và có thể truy cập
   từ máy hiện tại; nút này không khởi động Server.

## Tài khoản và quyền Guest

- Trong tab **Người dùng**, admin nhập username/mật khẩu, chọn role `EMPLOYEE`
  hoặc `GUEST`, rồi bấm **Tạo**. Tạo từng tài khoản qua cùng form này.
- Nhiều phiên đăng nhập bằng cùng tài khoản Guest hoạt động độc lập.
- Trong **File Manager**, chọn file/thư mục và dùng **Cấp quyền tải cho Guest** hoặc
  **Thu hồi quyền Guest**. Quyền trên thư mục áp dụng cho cây con bên trong.
- Chỉ Admin thấy cột trạng thái Guest trong danh sách File Manager; cột này
  chỉ rõ file/thư mục Guest có thể tải. Ngày sửa được hiển thị theo giờ địa
  phương, định dạng `ngày/tháng/năm giờ:phút:giây`.
- Guest chỉ dùng File Manager để xem/tải phạm vi được cấp. ACL được thực thi
  trên Admin API; TFTP thuần không xác thực người dùng, nên không dùng TFTP để
  kiểm soát quyền Guest.

## Lưu ý triển khai

Admin API Workspace mặc định bind `127.0.0.1`. Không mở TFTP hoặc Admin API
trực tiếp ra Internet. Workspace tạo từ `tftp_gui.py` hiện cố định bind localhost, nên chỉ máy
đang chạy GUI truy cập được. Cùng mạng Wi-Fi hoặc thêm firewall rule không đủ
để client khác kết nối. `tftp_gui.py` chưa có cấu hình bật chế độ LAN/VPN.

Đóng GUI sẽ dừng dịch vụ TFTP/API nhưng không xóa tài khoản, database hoặc file.
Để sao lưu/chuyển Workspace, giữ cả thư mục dữ liệu dưới `%APPDATA%\TFTPPro\servers`
và thư mục lưu trữ file mà admin đã chọn.
