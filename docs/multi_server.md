# Multi-Server

Mỗi Server = một công ty = một database SQLite + một storage riêng, định danh bằng
`Server ID` dạng `SRV-XXXXXXXX`. Client lưu danh sách Server trong `~/.efm/servers.json`
(không lưu mật khẩu/token).

## Tạo Server (trong ứng dụng)
Chạy `python efm_app.py` -> **Create Server** -> nhập tên, IP, API Port, TFTP Port, tài khoản
Super Admin. Ứng dụng kiểm tra port, sinh Server ID, tạo Workspace + Database, tạo Super Admin
và chạy TFTP + API. Dữ liệu ở `~/.efm/servers/<tên>/`.

## Tạo Server bằng dòng lệnh (tùy chọn, cho máy chủ không có GUI)
```
set EFM_ADMIN_PASSWORD=MatKhauManh123!
python -m enterprise.cli setup --name "Cty A" --host 192.168.1.10 --api-port 6971 --tftp-port 6969 --check-bind
python admin_api.py --enterprise-db database/tftp_admin.db --enterprise-root storage/company_files --host 0.0.0.0
```
## Tham gia & đăng nhập
1. Admin tạo mã: `POST /api/ent/join-codes` → mã dùng 1 lần, hết hạn sau 24h (chỉ lưu hash).
2. Nhân viên: `POST /api/ent/join` với `code`, `username`, `password`.
3. Client gọi `/api/health`, so `server_id` với registry; lệch thì từ chối đăng nhập.
4. `POST /api/ent/login` → token + danh sách permission.

## Endpoint chính (`/api/ent/*`, Bearer token)
login, join, logout, me, users, users/role, join-codes, employees, departments,
tasks, tasks/status, audit, transfer/authorize.

## Cấp quyền truyền file TFTP
Trước RRQ/WRQ gọi `EnterpriseService.authorize_transfer(user, "RRQ"|"WRQ", path)`;
sau WRQ gọi `register_upload` để lưu owner + SHA-256. Giao thức TFTP không bị sửa.
