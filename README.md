# HƯỚNG DẪN CÀI ĐẶT VÀ CHẠY TFTP PRO

## 1. Yêu cầu môi trường

- Hệ điều hành: Windows.
- Đã cài đặt Python 3.

## 2. Khởi động TFTP Server

**Bước 1:** Mở Terminal hoặc PowerShell tại thư mục chứa mã nguồn dự án.

**Bước 2:** Chạy lệnh:

```bash
python tftp_server.py
```

**Bước 3:** Kiểm tra thông báo trên Terminal để xác nhận Server đã khởi động thành công.

Server sử dụng cấu hình mặc định:

| Thông số | Giá trị |
|---|---|
| Host | `0.0.0.0` |
| Port | `6969` |
| Thư mục lưu file | `server_storage` |
| Block size | `512 bytes` |
| Timeout | `3 giây` |
| Số lần thử lại | `5` |

Các file được Upload lên Server sẽ được lưu trong thư mục `server_storage`.

## 3. Khởi động TFTP Client

**Bước 1:** Mở một cửa sổ Terminal hoặc PowerShell khác tại thư mục dự án.

**Bước 2:** Chạy lệnh:

```bash
python tftp_gui.py
```

**Bước 3:** Giao diện TFTP Pro sẽ xuất hiện.

**Bước 4:** Nhập thông tin kết nối:

- Host: `127.0.0.1` nếu Client và Server chạy trên cùng một máy.
- Port: `6969`.

Nếu Client kết nối đến Server trên máy tính khác, nhập địa chỉ IP của máy chạy Server thay cho `127.0.0.1`.

## 4. Thực hiện Upload

Upload là quá trình gửi file từ máy Client lên Server.

1. Chọn chức năng Upload trên giao diện.
2. Chọn file cần gửi từ máy tính.
3. Nhấn nút Upload để bắt đầu truyền file.
4. Theo dõi thanh tiến trình và nhật ký hoạt động.
5. Khi hoàn tất, file được lưu trong thư mục `server_storage` trên máy Server.

Tên file trên Server được lấy từ tên file đã chọn trên máy Client.

## 5. Thực hiện Download

Download là quá trình tải file từ Server về máy Client.

1. Chọn chức năng Download trên giao diện.
2. Nhập tên file cần tải hoặc chọn tên file từ phần gợi ý lịch sử nếu có.
3. Nhấn Download để mở hộp thoại Save As.
4. Chọn ổ đĩa và thư mục muốn lưu file trên máy tính.
5. Nhập tên file nếu muốn thay đổi tên lưu.
6. Nhấn Save để xác nhận vị trí lưu và bắt đầu tải file.
7. Theo dõi thanh tiến trình cho đến khi hoàn tất.

## 6. Theo dõi quá trình truyền file

Trong quá trình Upload hoặc Download, giao diện hiển thị:

- Tiến trình truyền file.
- Phần trăm dữ liệu đã truyền.
- Dung lượng dữ liệu đã truyền.
- Tốc độ truyền dữ liệu.
- Thời gian dự kiến còn lại (ETA).
- Nhật ký hoạt động và thông báo lỗi.

Người dùng có thể nhấn Cancel để hủy quá trình truyền file.

## 7. Kiểm tra kết quả

Sau khi Upload:
- Kiểm tra file trong thư mục `server_storage` trên máy Server.
- Đối chiếu tên và dung lượng file.

Sau khi Download:
- Kiểm tra file tại thư mục đã chọn trong hộp thoại Save As.
- Đối chiếu tên và dung lượng file với file trên Server.

## 8. Lưu ý

- Cần khởi động Server trước khi chạy Client.
- Client và Server phải sử dụng đúng địa chỉ IP và cổng kết nối.
- Nếu chạy trên hai máy khác nhau, cần bảo đảm kết nối mạng và cho phép lưu lượng UDP qua Firewall.
- Lịch sử tên file trên Client chỉ là những tên file đã sử dụng hoặc đã biết, không phải danh sách file thực tế trên Server.
- Server hỗ trợ RRQ (Download), WRQ (Upload) và liệt kê file qua dịch vụ TCP trên cổng `port+1` (nút "Làm mới" trong GUI). Chưa hỗ trợ xóa file trực tiếp trên Server.


---

# TFTP Pro (English)

A small, reliable TFTP client/server with GUI, implementing RFC 1350 and optional blksize/timeout/tsize (RFC 2347-2349).

## Features
- RFC 1350: RRQ, WRQ, DATA, ACK, ERROR
- Multi-client server, ephemeral TID, stop-and-wait retry
- GUI with progress, cancel and packet log
- CLI: `tftp-server`, `tftp-client get/put`
- Optional blksize/timeout/tsize
- Atomic transfer, path traversal protection
- File listing via TCP on `port+1` (GUI refresh button)

## Quick start (from source)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python tftp_server.py --root ./server_storage
python tftp_gui.py
```

## CLI
```bash
python tftp_client.py --host 127.0.0.1 --port 6969 put ./README.md README.md
python tftp_client.py --host 127.0.0.1 --port 6969 get README.md ./README.downloaded.md
```

## Server options
| Option | Default | Description |
|---|---|---|
| `--host` | 0.0.0.0 | Bind address |
| `--port` | 6969 | UDP port |
| `--root` | server_storage | Shared folder |
| `--timeout` | 3.0 | Seconds |
| `--retries` | 5 | Retransmissions |
| `--max-clients` | 20 | Concurrent sessions |
| `--overwrite` | off | Allow overwrite |
