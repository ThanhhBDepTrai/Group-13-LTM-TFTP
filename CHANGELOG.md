# Changelog

## [1.0.0] - 2026-10-28
### Added
- Multi-client UDP server with ephemeral TID and thread pool
- Tkinter GUI with progress, cancel and packet log
- CLI client (get/put) and server
- Atomic upload/download, path traversal protection
- Optional blksize, timeout, tsize negotiation
### Fixed
- Correct retry when ACK/DATA is lost
- Duplicate DATA handling
- GUI thread safety
### Known limitations
- No encryption or authentication; use in trusted LAN only
- No resume support
- IPv4 only

## Unreleased
- Thêm Enterprise File Manager: multi-Server (Server ID, join code, registry), tài khoản/phiên
  7 vai trò, nhân sự/phòng ban/chức vụ, ACL file, cấp quyền truyền TFTP, task, audit.
- Admin API: route `/api/ent/*` bật bằng `--enterprise-db`; `/api/health` trả thêm `server_id`.
