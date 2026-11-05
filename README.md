# TFTP Pro 1.1.0

TFTP Pro là ứng dụng Python gồm TFTP Client/Server, giao diện Tkinter, Admin API
quản trị riêng và công cụ hỗ trợ PXE trong mạng lab. Phiên bản này giữ tương
thích CLI/GUI cũ, đồng thời bổ sung RFC 7440, quản lý file/người dùng, SHA-256,
dashboard, packet analyzer và boot profile PXE. Chế độ Enterprise bổ sung
Workspace riêng, tài khoản `SUPER_ADMIN` và các role doanh nghiệp.

> TFTP không có xác thực/mã hóa tích hợp. Không mở TFTP hoặc Admin API ra
> Internet. Admin API mặc định chỉ bind `127.0.0.1`; chỉ mở trong LAN tin cậy
> khi thực sự cần và luôn dùng mật khẩu mạnh.

## 1. Tính năng

- RFC 1350: RRQ, WRQ, DATA, ACK, ERROR; nhiều client, TID riêng, retry có giới hạn.
- RFC 2347/2348/2349: `blksize`, `timeout`, `tsize`.
- RFC 7440: `windowsize`, ACK tích lũy theo cửa sổ; tự fallback về `1`.
- Mặc định an toàn: client `windowsize = 1`, server chấp nhận tối đa `16`.
- Ghi file nguyên tử và kiểm tra path traversal/symlink qua `StorageManager`.
- Admin API HTTP tách khỏi TFTP, token Bearer; hỗ trợ tài khoản TFTP legacy và
  tài khoản Enterprise (`SUPER_ADMIN`, `ADMIN`, `HR`, `MANAGER`, `TEAM_LEADER`,
  `EMPLOYEE`, `INTERN`) trên các API tương ứng.
- File Manager: list, search, metadata, upload, download, rename, mkdir, delete.
- SHA-256: hash file, upload/download có đối chiếu `VERIFIED` / `MISMATCH`.
- Dashboard metrics và Packet Analyzer từ trace nội bộ của Client/Server.
- PXE profile BIOS/UEFI, kiểm tra boot file; **không** chạy DHCP server.
- GUI sáu tab: Truyền file, File Manager, Dashboard, Packet Analyzer, Người dùng, PXE.

## 2. Yêu cầu

- Windows (GUI đã được kiểm tra trên Windows), Python 3.10 trở lên.
- Không cần thư viện bên thứ ba để chạy ứng dụng.
- Kiểm thử/dev: `pytest`, `ruff`.

## 3. Cài đặt

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

## 4. Chạy nhanh

### Chạy giao diện Enterprise (khuyến nghị để tạo tài khoản quản trị)

```powershell
python tftp_gui.py
```

Ở màn hình đầu tiên:

1. Chọn **Create Server** để tạo Workspace mới.
2. Nhập thông tin Workspace và thư mục lưu file.
3. Tạo tài khoản `SUPER_ADMIN` (mật khẩu tối thiểu 8 ký tự).
4. Sau khi vào giao diện, mở tab **Người dùng** để tạo tài khoản và chọn role
   phù hợp.

Server/API chạy trên máy cục bộ với mặc định API `127.0.0.1:6971`, TFTP
`127.0.0.1:6969`. Để kết nối Workspace đã có, chọn **Join Server** và nhập địa
chỉ API cùng tài khoản được cấp. Chỉ mở server tới máy khác khi cấu hình host,
firewall và mạng phù hợp.

### Chạy TFTP Pro độc lập

### Cách khuyến nghị: một tiến trình cho TFTP + Admin API

```powershell
# Đặt mật khẩu admin trước lần chạy đầu tiên (khuyến nghị)
$env:TFTP_ADMIN_PASSWORD = "MatKhauManh123!"
python tftp_pro.py
```

Mặc định:

- TFTP UDP: `0.0.0.0:6969`, thư mục chia sẻ `server_storage`.
- Admin API: `http://127.0.0.1:6971`, SQLite `tftp_admin.db`.
- Metrics SQLite: `tftp_admin.metrics.db` (cùng thư mục với DB admin).
- PXE profile: `pxe_profiles.json`.

Chạy tách dịch vụ (vẫn được hỗ trợ):

```powershell
python tftp_server.py
python admin_api.py --root server_storage --db tftp_admin.db
```

Chạy GUI:

```powershell
python tftp_gui.py
```

`tftp_pro.py` và `admin_api.py` dùng hệ thống tài khoản legacy `admin/user/guest`;
chúng độc lập với database tài khoản Enterprise được tạo trong Workspace. Lần
đầu chạy chế độ legacy, nếu database chưa có tài khoản, chương trình tạo
`admin` với mật khẩu lấy từ biến môi trường `TFTP_ADMIN_PASSWORD`, hoặc fallback
`admin`. Log sẽ cảnh báo khi dùng mật khẩu mặc định; hãy đổi ngay trong tab
**Người dùng** hoặc qua API.

## 5. Đăng nhập Admin API

Ví dụ PowerShell:

```powershell
$login = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:6971/api/auth/login `
  -ContentType "application/json" `
  -Body '{"username":"admin","password":"MatKhauManh123!"}'

$headers = @{ Authorization = "Bearer $($login.token)" }
Invoke-RestMethod -Uri http://127.0.0.1:6971/api/dashboard -Headers $headers
```

Hoặc dùng curl:

```bash
curl -s -X POST http://127.0.0.1:6971/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"MatKhauManh123!"}'
```

Token có hiệu lực 8 giờ. Gọi `POST /api/auth/logout` để thu hồi token hiện tại.
Chi tiết endpoint và quyền: [docs/admin_api.md](docs/admin_api.md).

## 6. Giao diện GUI

Thanh trên cùng cấu hình TFTP IP, Port, Window và Admin API URL/tài khoản.

| Tab | Chức năng |
|---|---|
| **Truyền file** | Upload/download TFTP, tiến trình, tốc độ, ETA, log, cancel. Danh sách file lấy từ dịch vụ listing cũ (TCP `port+1`). |
| **File Manager** | Duyệt/tìm file qua Admin API; upload, download, rename, tạo thư mục, xóa, xem metadata và SHA-256. |
| **Dashboard** | Số client hoạt động, tổng phiên, dung lượng, tốc độ trung bình, timeout/retry, phiên gần đây. |
| **Packet Analyzer** | Lọc trace theo opcode/chiều, xem block, payload, source/destination và thống kê gói. |
| **Người dùng** | Quản lý tài khoản Enterprise theo quyền; các role hiện có gồm `ADMIN`, `HR`, `MANAGER`, `TEAM_LEADER`, `EMPLOYEE`, `INTERN`. |
| **PXE** | Tạo/sửa/xóa profile BIOS/UEFI, chọn boot file mặc định, kiểm tra file tồn tại. |

Upload/download TFTP vẫn hoạt động khi chưa đăng nhập Admin API. Đăng nhập để
dùng File Manager, Dashboard, Packet Analyzer, quản lý người dùng/PXE và để
đối chiếu SHA-256 với Server sau khi truyền.

## 7. CLI

```powershell
# Download
python tftp_client.py --host 127.0.0.1 --port 6969 get README.md .\README.downloaded.md

# Upload
python tftp_client.py --host 127.0.0.1 --port 6969 put .\README.md README.md

# Tăng cửa sổ khi cả hai phía hỗ trợ RFC 7440
python tftp_client.py --host 127.0.0.1 --port 6969 --blksize 1456 --windowsize 8 get big.bin .\big.bin

# Server độc lập
python tftp_server.py --host 0.0.0.0 --port 6969 --root .\server_storage --max-clients 20
```

`windowsize = 1` là mặc định của client và tương đương stop-and-wait cũ. Nếu
server không trả `windowsize` trong OACK, client tự dùng `1`.

## 8. SHA-256 và tính toàn vẹn

- Server tính hash khi lưu file và cung cấp qua `GET /api/files/hash` hoặc header
  `X-File-SHA256` khi download qua Admin API.
- GUI tính hash file local, lấy hash Server qua Admin API và hiển thị
  `VERIFIED` hoặc `MISMATCH`.
- `POST /api/files/verify` kiểm tra một hash mong đợi.
- Hash chỉ xác nhận nội dung file trùng khớp; nó không thay thế xác thực, mã hóa
  hay bảo đảm an toàn đường truyền.

## 9. Benchmark

```powershell
python scripts/benchmark_tftp.py --size 2MiB --runs 3
python scripts/benchmark_tftp.py --size 4MiB --runs 3 --json-out benchmark.json
```

Script đo download + upload cho các cấu hình A–E, kiểm tra SHA-256 sau mỗi lần
và ghi nhận thời gian, tốc độ, timeout/retry. Chỉ so sánh các lần chạy trên cùng
máy, cùng mạng và cùng kích thước file. Xem [docs/benchmark.md](docs/benchmark.md).

## 10. PXE trong lab

TFTP Pro chỉ quản lý boot profile và cung cấp boot file qua TFTP; DHCP là dịch
vụ độc lập trong mạng lab. Thư mục mặc định:

```text
server_storage/
  pxe/
    boot/     # boot file BIOS, ví dụ undionly.kpxe
    efi/      # boot file UEFI, ví dụ ipxe.efi
    config/   # cấu hình iPXE/script
```

Upload boot file thật vào các thư mục trên, sau đó tạo profile trong tab **PXE**
hoặc qua `/api/pxe/profiles`. Profile mẫu `lab-default` được tạo tự động và có
thể báo thiếu file cho đến khi boot file được upload. Xem
[docs/pxe_lab.md](docs/pxe_lab.md).

> Chỉ bật DHCP thử nghiệm trong mạng Host-only/isolated hoặc môi trường được
> quản lý. Không bật DHCP trên Wi-Fi dùng chung, mạng gia đình hoặc mạng lớp học.

## 11. Cấu hình mặc định

| Thông số | Mặc định | Ghi đè |
|---|---|---|
| TFTP host/port | `0.0.0.0:6969` | `--host`, `--port` |
| Client host/port | `127.0.0.1:6969` | `--host`, `--port` |
| Thư mục chia sẻ | `server_storage` | `--root` |
| Block size | `512` | `--blksize` |
| Timeout / retries | `3.0 s` / `5` | `--timeout`, `--retries` |
| Max clients | `20` | `--max-clients` |
| Client windowsize | `1` | `--windowsize` |
| Server windowsize tối đa | `16` | `--windowsize` |
| Admin API | `127.0.0.1:6971` | `--admin-host`, `--admin-port` |
| Admin DB | `tftp_admin.db` | `--admin-db`, `--db` |
| Admin body tối đa | `64 MiB` | hằng số `DEFAULT_MAX_ADMIN_BODY` |
| Token TTL | `8 giờ` | `AuthManager(token_ttl=...)` |
| PXE profiles | `pxe_profiles.json` | `--pxe-profiles` |

Các giá trị nằm trong `tftp_config.py`; CLI/GUI/benchmark dùng chung để tránh
lệch cổng hoặc block size.

## 12. Bảo mật và giới hạn

- Không expose TFTP/Admin API ra Internet; ưu tiên LAN/lab hoặc VPN.
- Admin API mặc định chỉ localhost. Nếu bind địa chỉ khác, chương trình cảnh báo;
  phải dùng mật khẩu mạnh, firewall và mạng tin cậy.
- Mật khẩu lưu bằng PBKDF2-HMAC-SHA256 + salt; token lưu dạng SHA-256, không lưu
  token gốc. Log không ghi mật khẩu/token.
- Mọi đường dẫn file đi qua `StorageManager`; `..`, đường dẫn tuyệt đối, ổ đĩa
  Windows và symlink/junction thoát root đều bị từ chối.
- PXE chỉ là cấu hình/profile + hướng dẫn lab; dự án không triển khai DHCP server.
- SHA-256 không biến TFTP thành giao thức an toàn.

## 13. Kiểm thử

```powershell
python -m pytest -q
python -m ruff check .
```

Bộ test bao phủ packet/option, windowsize/fallback, storage/path traversal, auth,
Admin API, metrics, packet analyzer, SHA-256, PXE và cấu hình mặc định.

## 14. Tài liệu

- [docs/architecture.md](docs/architecture.md) – kiến trúc, luồng dữ liệu, module.
- [docs/admin_api.md](docs/admin_api.md) – endpoint, quyền, ví dụ gọi API.
- [docs/benchmark.md](docs/benchmark.md) – cách đo và đọc kết quả.
- [docs/pxe_lab.md](docs/pxe_lab.md) – dựng lab DHCP + TFTP + PXE client.
- [docs/multi_server.md](docs/multi_server.md) – tạo Workspace Enterprise và kết nối Server.
- [protocol.md](protocol.md) – chi tiết gói tin, option, RFC 7440.
- [docs/interface.md](docs/interface.md) – interface module và CLI.
- [docs/protocol.md](docs/protocol.md) – bản tóm tắt giao thức tiếng Anh.

---

# TFTP Pro (English summary)

TFTP Pro is a Python TFTP client/server with a Tkinter GUI, a separate authenticated
Admin API, SHA-256 integrity checks, monitoring/packet analysis, and isolated-lab
PXE profiles. It implements RFC 1350, RFC 2347–2349 and RFC 7440; the client
defaults to `windowsize = 1` for backward compatibility.

```bash
pip install -e ".[dev]"
export TFTP_ADMIN_PASSWORD='change-me'   # PowerShell: $env:TFTP_ADMIN_PASSWORD='change-me'
python tftp_pro.py                       # TFTP :6969 + Admin API 127.0.0.1:6971
python tftp_gui.py
python tftp_client.py get README.md ./README.downloaded.md
python scripts/benchmark_tftp.py --size 2MiB --runs 3
```

The Admin API binds to localhost by default. Do not expose TFTP or the Admin API
to the Internet. The project does not implement a DHCP server; PXE support is
limited to boot profiles and files for an isolated lab. See the Vietnamese
sections above and the documents in `docs/` for details.
