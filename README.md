# TFTP Pro 1.1.0

TFTP Pro là ứng dụng Python gồm TFTP Client/Server, giao diện Tkinter, Admin API
quản trị riêng và công cụ hỗ trợ PXE trong mạng lab. Phiên bản này giữ tương
thích CLI/GUI cũ, đồng thời bổ sung RFC 7440, quản lý file/người dùng, SHA-256,
dashboard, packet analyzer và boot profile PXE. Chế độ Enterprise bổ sung
Workspace riêng, tài khoản `SUPER_ADMIN` và các role doanh nghiệp.

> TFTP không có xác thực/mã hóa tích hợp. Không mở TFTP hoặc Admin API trực tiếp
> ra Internet. Workspace tạo từ `tftp_gui.py` hiện chỉ bind `127.0.0.1`, nghĩa là
> chỉ máy đang chạy ứng dụng truy cập được; mở firewall không làm ứng dụng tự
> truy cập được từ máy khác. Muốn dùng từ xa cần triển khai mạng/VPN và cấu hình
> server phù hợp trước.

## 1. Tính năng

- RFC 1350: RRQ, WRQ, DATA, ACK, ERROR; nhiều client, TID riêng, retry có giới hạn.
- RFC 2347/2348/2349: `blksize`, `timeout`, `tsize`.
- RFC 7440: `windowsize`, ACK tích lũy theo cửa sổ; tự fallback về `1`.
- Mặc định an toàn: client `windowsize = 1`, server chấp nhận tối đa `16`.
- Ghi file nguyên tử và kiểm tra path traversal/symlink qua `StorageManager`.
- Admin API HTTP tách khỏi TFTP, token Bearer; hỗ trợ tài khoản TFTP legacy và
  tài khoản Enterprise (`SUPER_ADMIN`, `ADMIN`, `HR`, `MANAGER`, `TEAM_LEADER`,
  `EMPLOYEE`, `INTERN`, `GUEST`) trên các API tương ứng.
- File Manager: list, search, metadata, upload, download, rename, mkdir, delete.
- Enterprise Guest chỉ xem/tải file được Admin cấp qua Admin API; quyền Guest
  không áp dụng cho giao thức TFTP thuần.
- SHA-256: hash file, upload/download có đối chiếu `VERIFIED` / `MISMATCH`.
- Dashboard metrics và Packet Analyzer từ trace nội bộ của Client/Server.
- PXE profile BIOS/UEFI, kiểm tra boot file; **không** chạy DHCP server.
- GUI có các tab Truyền file, File Manager, Dashboard, Packet Analyzer, Người dùng,
  Nhân viên và PXE (Guest chỉ thấy File Manager).

## 2. Yêu cầu

- Windows (GUI đã được kiểm tra trên Windows), Python 3.10 trở lên.
- Tkinter (thường được cài kèm Python trên Windows; chọn mục Tcl/Tk khi cài Python).
- Chạy ứng dụng không cần thư viện bên thứ ba; `pytest` và `ruff` chỉ dùng để test/lint.

## 3. Cài đặt

Clone repository, sau đó chạy trong thư mục dự án:

```powershell
git clone https://github.com/ThanhhBDepTrai/Group-13-LTM-TFTP.git
Set-Location Group-13-LTM-TFTP
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e ".[dev]"
```

Nếu PowerShell chặn lệnh activate, có thể gọi Python trong virtual environment
trực tiếp mà không activate:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe tftp_gui.py
```

## 4. Chạy ứng dụng

### Workspace Enterprise (khuyến nghị)

```powershell
python tftp_gui.py
```

Ở màn hình đầu tiên:

1. Chọn **Create Server** để tạo Workspace mới.
2. Nhập thông tin Workspace và thư mục lưu file.
3. Tạo tài khoản `SUPER_ADMIN` (mật khẩu tối thiểu 8 ký tự).
4. Để tạo nhân viên/Guest, mở tab **Người dùng**, nhập username/mật khẩu, chọn
   role rồi bấm **Tạo**. Tạo hồ sơ cá nhân ở tab **Nhân viên** là bước riêng,
   không bắt buộc để tài khoản đăng nhập được.
5. Để cho Guest tải file, chọn file/thư mục trong **File Manager**, bấm
   **Cấp quyền tải cho Guest**. Cột trạng thái chỉ Admin thấy; quyền trên thư
   mục áp dụng cho nội dung bên trong.
6. Lần sau mở ứng dụng, chọn **LOGIN TO SAVED WORKSPACE**, chọn Workspace và
   đăng nhập bằng tài khoản đã tạo. Database tài khoản nằm ở
   `%APPDATA%\TFTPPro\servers\<workspace_id>\efm.db`; registry lưu cấu hình,
   không lưu mật khẩu. Thư mục file là vị trí bạn chọn khi tạo Workspace.
   Hãy sao lưu cả thư mục dữ liệu và thư mục file nếu muốn chuyển Workspace
   sang máy khác; GitHub không lưu các dữ liệu này.

**Lưu ý:** Workspace này mặc định dùng API `127.0.0.1:6971` và TFTP
`127.0.0.1:6969`. Chỉ máy đang chạy `tftp_gui.py` mới truy cập được. Khi đóng
ứng dụng, server dừng nhưng database và tài khoản vẫn được giữ lại.

Để một client khác **kết nối đến server đang chạy**, chọn **JOIN SERVER** và
nhập URL cùng tài khoản đã được cấp. Tuy nhiên, với cấu hình Workspace hiện tại,
client khác không thể kết nối qua LAN/Internet vì server chỉ bind localhost.
Firewall đơn thuần không khắc phục được giới hạn bind này.

> Dùng `python tftp_gui.py` cho luồng Workspace, File Manager, quyền Guest và
> tài khoản Enterprise. `efm_app.py` là giao diện/luồng khác, không thay thế đầy
> đủ các màn hình File Manager của `tftp_gui.py`.

#### Khắc phục nhanh khi không đăng nhập được

- Nếu thấy `WinError 10061` / “actively refused”, API chưa chạy hoặc URL/port
  không đúng. Trên máy đã tạo Workspace, chọn **LOGIN TO SAVED WORKSPACE** để
  khởi động lại server. **JOIN SERVER** chỉ kết nối tới server đang chạy, không
  khởi động server giúp bạn.
- Nếu màn hình đăng nhập báo sai tài khoản/mật khẩu, kiểm tra bạn đang chọn
  đúng Workspace và dùng đúng role/tài khoản đã tạo trong tab **Người dùng**.
- Máy khác hiện không kết nối được Workspace tạo bằng GUI vì API/TFTP chỉ bind
  localhost. Cùng Wi-Fi hoặc mở firewall không thay đổi giới hạn này.

### TFTP Pro legacy (TFTP + Admin API)

```powershell
# Chạy tại thư mục dự án. Đổi mật khẩu mặc định trước khi dùng.
$env:TFTP_ADMIN_PASSWORD = "MatKhauManh123!"
python tftp_pro.py
```

Mặc định:

- TFTP UDP: `0.0.0.0:6969`, thư mục chia sẻ `server_storage`.
- Admin API: `http://127.0.0.1:6971`, SQLite `tftp_admin.db`.
- Metrics SQLite: `tftp_admin.metrics.db` (cùng thư mục với DB admin).
- PXE profile: `pxe_profiles.json`.

Legacy TFTP mặc định lắng nghe TFTP trên mọi network interface, nhưng Admin API
chỉ lắng nghe localhost. Đây là chế độ TFTP cũ, **không** phải cách bật truy cập
LAN cho Workspace Enterprise.

Chế độ này dùng hệ tài khoản legacy riêng trong `tftp_admin.db` (`admin/user/guest`),
không dùng tài khoản Enterprise trong Workspace và không hỗ trợ ACL Guest của
Workspace. Nếu database legacy chưa có tài khoản, chương trình tạo `admin` bằng
giá trị `TFTP_ADMIN_PASSWORD`; nếu không đặt biến này, mật khẩu mặc định là
`admin` và cần đổi ngay. File/database được lưu tương đối với thư mục chạy lệnh.

Chạy dịch vụ legacy tách riêng (tùy chọn):

```powershell
python tftp_server.py
python admin_api.py --root server_storage --db tftp_admin.db
```

Không chạy `tftp_pro.py` cùng lúc với `tftp_gui.py` trên cùng cổng mặc định
`6969`/`6971`; một trong hai tiến trình sẽ không thể bind cổng.

## 5. Đăng nhập Admin API

Ví dụ PowerShell cho tài khoản Enterprise trong Workspace:

```powershell
$login = Invoke-RestMethod -Method Post `
  -Uri http://127.0.0.1:6971/api/ent/login `
  -ContentType "application/json" `
  -Body '{"username":"superadmin","password":"MatKhauWorkspace!"}'

$headers = @{ Authorization = "Bearer $($login.token)" }
Invoke-RestMethod -Uri http://127.0.0.1:6971/api/ent/me -Headers $headers
```

Ví dụ tiếp theo dùng tài khoản legacy của `tftp_pro.py`:

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
| **File Manager** | Duyệt/tìm file qua Admin API; upload, download (tự kiểm tra SHA-256), tạo thư mục, đổi tên, xóa và xem SHA-256. Admin có cột riêng cho biết Guest có thể tải file/thư mục nào và có thể cấp/thu hồi quyền. Ngày sửa hiển thị ngày giờ địa phương. |
| **Dashboard** | Thống kê TFTP và upload/download qua File Manager: client đang hoạt động, tổng phiên, dung lượng, tốc độ, timeout/retry và lịch sử. |
| **Packet Analyzer** | Lọc trace theo opcode/chiều, xem block, payload, source/destination và thống kê gói. |
| **Người dùng** | Quản lý tài khoản Enterprise; giao diện ưu tiên `ADMIN`, `EMPLOYEE` và `GUEST`. Các role Enterprise khác vẫn được giữ để tương thích. Admin tạo nhân viên hoặc Guest từng tài khoản bằng form tạo tài khoản. |
| **PXE** | Tạo/sửa/xóa profile BIOS/UEFI, chọn boot file mặc định, kiểm tra file tồn tại. |
| **Nhân viên** | Danh sách hồ sơ nhân sự và tài khoản có role `EMPLOYEE`; tài khoản chưa có hồ sơ vẫn hiển thị username. |

Upload/download TFTP vẫn hoạt động khi chưa đăng nhập Admin API. Đăng nhập để
dùng File Manager, Dashboard, Packet Analyzer, quản lý người dùng/PXE và để
đối chiếu SHA-256 với Server sau khi truyền.

Guest đăng nhập bằng tài khoản Enterprise `GUEST`, chỉ thấy và tải các file/thư
mục được admin cấp trong File Manager. Nhiều phiên đăng nhập Guest có thể hoạt
động đồng thời. Giới hạn này được thực thi trên Admin API; giao thức TFTP thuần
không có xác thực, vì vậy không dùng TFTP để phục vụ Guest hoặc mở các cổng dịch
vụ ra Internet.

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
| TFTP host/port (legacy CLI) | `0.0.0.0:6969` | `--host`, `--port` |
| Client host/port | `127.0.0.1:6969` | `--host`, `--port` |
| Thư mục chia sẻ | `server_storage` | `--root` |
| Block size | `512` | `--blksize` |
| Timeout / retries | `3.0 s` / `5` | `--timeout`, `--retries` |
| Max clients | `20` | `--max-clients` |
| Client windowsize | `1` | `--windowsize` |
| Server windowsize tối đa | `16` | `--windowsize` |
| Admin API (legacy CLI) | `127.0.0.1:6971` | `--admin-host`, `--admin-port` |
| Admin DB | `tftp_admin.db` | `--admin-db`, `--db` |
| Admin body tối đa | `64 MiB` | hằng số `DEFAULT_MAX_ADMIN_BODY` |
| Token TTL | `8 giờ` | `AuthManager(token_ttl=...)` |
| PXE profiles | `pxe_profiles.json` | `--pxe-profiles` |

Các giá trị nằm trong `tftp_config.py`; CLI/GUI/benchmark dùng chung để tránh
lệch cổng hoặc block size.

## 12. Bảo mật và giới hạn

- Không expose TFTP/Admin API ra Internet; ưu tiên LAN/lab hoặc VPN.
- Workspace tạo từ `tftp_gui.py` hiện chỉ bind localhost. Không có tùy chọn GUI
  để bật LAN; firewall đơn thuần không cho máy khác kết nối.
- `tftp_pro.py` cho phép cấu hình bind Admin API bằng `--admin-host`, nhưng đổi
  bind/firewall không mã hóa HTTP và không làm TFTP an toàn. Chỉ thử nghiệm
  trong mạng cô lập, không mở trực tiếp ra Internet.
- Mật khẩu lưu bằng PBKDF2-HMAC-SHA256 + salt; token lưu dạng SHA-256, không lưu
  token gốc. Log không ghi mật khẩu/token.
- Mọi đường dẫn file đi qua `StorageManager`; `..`, đường dẫn tuyệt đối, ổ đĩa
  Windows và symlink/junction thoát root đều bị từ chối.
- PXE chỉ là cấu hình/profile + hướng dẫn lab; dự án không triển khai DHCP server.
- SHA-256 không biến TFTP thành giao thức an toàn.

## 13. Kiểm thử

```powershell
python -m pytest -q
```

Bộ test bao phủ packet/option, windowsize/fallback, storage/path traversal,
Enterprise và legacy auth/API, metrics, packet analyzer, SHA-256, PXE và cấu hình
mặc định. `ruff` có thể dùng riêng để kiểm tra style; lệnh này không cần để chạy
ứng dụng.

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
