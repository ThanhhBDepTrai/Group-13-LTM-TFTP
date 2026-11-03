# Admin API

Admin API là dịch vụ HTTP tách hoàn toàn khỏi TFTP. Mọi thao tác quản trị file,
người dùng, thống kê và PXE đều đi qua API này; không thêm opcode TFTP mới và
GUI không đọc trực tiếp database/thư mục của Server.

- Mặc định: `http://127.0.0.1:6971`
- Giao thức: HTTP/1.1, JSON UTF-8
- Xác thực: `Authorization: Bearer <token>`
- Token TTL: 8 giờ
- Body tối đa: 64 MiB (`DEFAULT_MAX_ADMIN_BODY`)
- Database: SQLite, mặc định `tftp_admin.db`

Không mở Admin API ra Internet. Nếu cần dùng trong LAN, chỉ bind trên interface
tin cậy, đặt firewall và dùng mật khẩu mạnh. Khi bind khác localhost, chương
trình ghi cảnh báo.

## 1. Khởi động

Cách khuyến nghị (TFTP + API chung tiến trình, chung metrics/trace):

```powershell
$env:TFTP_ADMIN_PASSWORD = "MatKhauManh123!"
python tftp_pro.py
```

Chạy riêng Admin API:

```powershell
python admin_api.py --host 127.0.0.1 --port 6971 --root server_storage --db tftp_admin.db
```

Tùy chọn:

| Option | Mặc định | Mô tả |
|---|---|---|
| `--host` | `127.0.0.1` | Địa chỉ bind |
| `--port` | `6971` | Cổng HTTP |
| `--root` | `server_storage` | Thư mục chia sẻ |
| `--db` | `tftp_admin.db` | SQLite user/token/audit |
| `--metrics-db` | `<db>.metrics.db` | SQLite metrics |
| `--pxe-profiles` | `pxe_profiles.json` | Cấu hình PXE |
| `--log-level` | `INFO` | Mức log |

Lần chạy đầu, nếu database chưa có user, `admin` được tạo với mật khẩu từ
`TFTP_ADMIN_PASSWORD` hoặc fallback `admin`. Log cảnh báo khi dùng fallback.
Đổi mật khẩu ngay sau lần đăng nhập đầu.

## 2. Đăng nhập và token

```http
POST /api/auth/login
Content-Type: application/json

{"username": "admin", "password": "MatKhauManh123!"}
```

Phản hồi:

```json
{
  "token": "…",
  "username": "admin",
  "role": "admin",
  "expires_at": 1790000000.0
}
```

Gửi token cho các request sau:

```http
Authorization: Bearer <token>
```

| Endpoint | Quyền | Mô tả |
|---|---|---|
| `POST /api/auth/login` | công khai | Tạo token |
| `POST /api/auth/logout` | đã đăng nhập | Thu hồi token hiện tại |
| `GET /api/auth/me` | đã đăng nhập | Thông tin user hiện tại |
| `GET /api/health` | công khai | `{"status":"ok","version":"1.1.0"}` |

Token hết hạn hoặc user bị tắt sẽ bị từ chối với HTTP 401. Đổi role khỏi
`admin`, đổi mật khẩu hoặc tắt tài khoản sẽ thu hồi toàn bộ token của user đó.

## 3. Vai trò và quyền

| Quyền | admin | user | guest |
|---|:---:|:---:|:---:|
| `files.list` | ✓ | ✓ | ✓ |
| `files.read` | ✓ | ✓ | ✓ |
| `files.download` | ✓ | ✓ | ✓ |
| `files.upload` | ✓ | ✓ | |
| `files.delete` | ✓ | | |
| `files.rename` | ✓ | | |
| `files.mkdir` | ✓ | | |
| `users.manage` | ✓ | | |
| `stats.read` | ✓ | ✓ | |
| `pxe.read` | ✓ | ✓ | ✓ |
| `pxe.manage` | ✓ | | |

Quyền được kiểm tra ở từng endpoint. Yêu cầu trái quyền trả HTTP 403. Không thể
xóa, hạ quyền hoặc tắt admin hoạt động cuối cùng.

## 4. File

Các đường dẫn là tương đối so với `--root`; dùng `/` làm dấu phân cách. API từ
chối `..`, đường dẫn tuyệt đối, đường dẫn ổ đĩa Windows và symlink/junction
thoát khỏi root.

| Method | Endpoint | Quyền | Tham số |
|---|---|---|---|
| `GET` | `/api/files` | `files.list` | `path` (mặc định root) |
| `GET` | `/api/files/search` | `files.list` | `q`, `limit` (mặc định 200) |
| `GET` | `/api/files/metadata` | `files.read` | `path`, `hash=1` để tính SHA-256 |
| `GET` | `/api/files/hash` | `files.read` | `path` |
| `GET` | `/api/files/download` | `files.download` | `path` |
| `POST` | `/api/files/upload` | `files.upload` | `path`, body nhị phân |
| `POST` | `/api/files/verify` | `files.read` | JSON `path`, `sha256` |
| `POST` | `/api/files/rename` | `files.rename` | JSON `path`, `new_path`, `overwrite` |
| `POST` | `/api/files/mkdir` | `files.mkdir` | JSON `path`, `exist_ok` |
| `DELETE` | `/api/files` | `files.delete` | `path`, `recursive=1` nếu xóa thư mục |

Ví dụ upload (curl):

```bash
curl -X POST "http://127.0.0.1:6971/api/files/upload?path=docs/readme.txt" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/octet-stream" \
  --data-binary @readme.txt
```

Phản hồi upload có `entry` (metadata) và `sha256`. Download trả bytes kèm header
`X-File-SHA256` và `Content-Disposition` UTF-8.

`GET /api/files` trả:

```json
{
  "path": "docs",
  "entries": [
    {
      "name": "readme.txt",
      "relative_path": "docs/readme.txt",
      "size": 1234,
      "modified_at": "2026-11-02T10:00:00+00:00",
      "is_dir": false,
      "sha256": null
    }
  ]
}
```

`POST /api/files/verify` trả `status` là `VERIFIED` hoặc `MISMATCH`, cùng
`expected`, `actual`, `relative_path`.

## 5. Người dùng và audit

| Method | Endpoint | Body / query | Mô tả |
|---|---|---|---|
| `GET` | `/api/users` | — | Danh sách user |
| `POST` | `/api/users` | `username`, `password`, `role` | Tạo user |
| `DELETE` | `/api/users` | `?username=` | Xóa user |
| `POST` | `/api/users/role` | `username`, `role` | Đổi role |
| `POST` | `/api/users/active` | `username`, `active` | Bật/tắt |
| `POST` | `/api/users/password` | `new_password`, tùy chọn `username`, `current_password` | Đổi mật khẩu |
| `GET` | `/api/audit` | `?limit=` | Sự kiện audit gần nhất |

Quy tắc mật khẩu: tối thiểu 4 ký tự (lab). Username: 1–64 ký tự chữ, số,
`_`, `.`, `-`. Role: `admin`, `user`, `guest`.

User không phải admin chỉ được đổi mật khẩu của chính mình và phải gửi
`current_password`. Admin đổi mật khẩu user khác mà không cần mật khẩu cũ.
Sau khi đổi mật khẩu, mọi token của user bị thu hồi.

Audit ghi các sự kiện như `auth.login`, `user.create`, `user.delete`,
`user.set_role`, `user.set_active`, `user.change_password`. Audit không chứa
mật khẩu/token.

## 6. Dashboard và packet trace

| Method | Endpoint | Quyền | Tham số |
|---|---|---|---|
| `GET` | `/api/dashboard` | `stats.read` | `limit` (số phiên gần đây, mặc định 50) |
| `GET` | `/api/packets` | `stats.read` | `limit` (mặc định 200), `opcode` |

Dashboard trả `{"stats": {...}, "recent": [...]}`. Stats gồm số client hoạt
động, tổng phiên, dung lượng upload/download, tốc độ trung bình, số phiên
thành công/thất bại, timeout/retry. Dữ liệu lịch sử nằm trong metrics SQLite.

`/api/packets` trả các `PacketEvent` gần nhất và `counts` theo opcode. Đây là
trace nội bộ do chính Client/Server phát ra, không cần quyền bắt gói của hệ
điều hành.

## 7. PXE profile

| Method | Endpoint | Quyền | Tham số |
|---|---|---|---|
| `GET` | `/api/pxe/profiles` | `pxe.read` | — |
| `POST`/`PUT` | `/api/pxe/profiles` | `pxe.manage` | JSON profile; `require_files=1` để bắt buộc file tồn tại |
| `DELETE` | `/api/pxe/profiles` | `pxe.manage` | `?name=` |
| `GET` | `/api/pxe/select` | `pxe.read` | `arch=bios|uefi`, tùy chọn `name` |

Profile:

```json
{
  "name": "lab-default",
  "description": "Profile PXE mẫu",
  "bios_file": "pxe/boot/undionly.kpxe",
  "uefi_file": "pxe/efi/ipxe.efi",
  "default": true
}
```

- `name`: 1–64 ký tự chữ, số, `_`, `.`, `-`.
- Phải có ít nhất một trong `bios_file`, `uefi_file`.
- Đường dẫn đi qua `StorageManager`; không được thoát root.
- Mặc định có đúng một profile `default`; nếu chưa có, profile đầu tiên được chọn.
- `GET /api/pxe/select` trả thêm `arch`, `boot_file`, `boot_file_exists`.
- `GET /api/pxe/profiles` trả cả `issues` cho boot file đang thiếu.

TFTP Pro không chạy DHCP. Xem [pxe_lab.md](pxe_lab.md).

## 8. Lỗi và giới hạn

Lỗi trả JSON:

```json
{"error": "Mô tả lỗi"}
```

| Status | Ý nghĩa |
|---|---|
| 400 | JSON/đường dẫn/tham số không hợp lệ |
| 401 | Thiếu/sai/hết hạn token |
| 403 | Không đủ quyền |
| 404 | Không tìm thấy endpoint/file/user/profile |
| 409 | Xung đột (file tồn tại, admin cuối cùng, ...) |
| 411 | Thiếu `Content-Length` |
| 413 | Body vượt giới hạn |
| 500 | Lỗi server |
| 501 | PXE manager chưa được cấu hình |

Upload qua API đọc toàn bộ body vào bộ nhớ và giới hạn 64 MiB mỗi request. Với
file lớn hơn, dùng TFTP hoặc tăng giới hạn có kiểm soát.

## 9. Kiểm thử

```powershell
python -m pytest tests/test_auth.py tests/test_admin_api.py tests/test_integrity.py -q
```

Các test kiểm tra login/token, phân quyền, path traversal, CRUD file, SHA-256,
quản lý user và endpoint PXE.
