# Kiến trúc TFTP Pro

Tài liệu mô tả cách các module trong TFTP Pro 1.1.0 phối hợp, ranh giới tin cậy
và hợp đồng dữ liệu nội bộ. Mục tiêu là giữ TFTP Core ổn định, tách quản trị
khỏi giao thức và cho phép GUI/CLI dùng chung một nguồn dữ liệu.

## 1. Nguyên tắc thiết kế

1. **TFTP Core không chứa chức năng quản trị.** Xóa/đổi tên/tạo thư mục/phân
   quyền không trở thành opcode TFTP mới; tất cả đi qua Admin API.
2. **StorageManager là cửa ngõ duy nhất tới filesystem.** Server, Admin API,
   PXE và benchmark đều dùng nó để chuẩn hóa/kiểm tra đường dẫn.
3. **Admin API là dịch vụ riêng.** Mặc định bind `127.0.0.1`; TFTP và API có
   thể chạy chung tiến trình nhưng vẫn tách cổng/giao thức.
4. **Tương thích trước.** Client mặc định `windowsize = 1`; nếu option không
   được chấp nhận, hai bên quay về stop-and-wait RFC 1350.
5. **Không tự viết DHCP.** PXE chỉ quản lý boot profile/file; DHCP là dịch vụ
   độc lập trong mạng lab.
6. **Trace nội bộ trước.** Packet Analyzer dùng sự kiện do chính ứng dụng phát
   ra, không yêu cầu quyền bắt gói hoặc driver đặc biệt.
7. **Lỗi không làm hỏng phiên truyền.** Ghi metrics/trace được bọc để lỗi phụ
   không làm sập upload/download.

## 2. Sơ đồ lớp

```text
┌────────────────────────────── GUI (tftp_gui.py) ──────────────────────────────┐
│ Truyền file │ File Manager │ Dashboard │ Packet Analyzer │ Users │ PXE       │
└───────┬───────────────────────────────┬───────────────────────────────────────┘
        │ TFTP UDP                      │ HTTP + Bearer token
        ▼                               ▼
┌───────────────────┐          ┌──────────────────────────┐
│ TFTP Client       │          │ Admin API (admin_api.py) │
│ (tftp_client.py)  │          │ auth / routes / JSON     │
└───────┬───────────┘          └────┬───────────┬─────────┘
        │ UDP                       │           │
        ▼                           ▼           ▼
┌───────────────────┐      ┌────────────┐  ┌──────────────┐
│ TFTP Server       │      │ AuthManager│  │ Metrics      │
│ (tftp_server.py)  │      │ (SQLite)   │  │ Collector    │
└───┬───────────┬───┘      └────────────┘  └──────────────┘
    │           │
    ▼           ▼
┌─────────┐  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ Storage │  │ PacketTracer │   │ PxeManager   │   │ benchmark    │
│ Manager │  │ (ring buffer)│   │ + JSON       │   │ script       │
└─────────┘  └──────────────┘   └──────────────┘   └──────────────┘
```

`tftp_pro.py` là launcher khuyến nghị: tạo một `StorageManager`,
`MetricsCollector`, `PacketTracer` và `PxeManager`, chia sẻ cho TFTP Server và
Admin API. Nhờ vậy Dashboard/Packet Analyzer thấy đúng các phiên TFTP. Chạy
`tftp_server.py` và `admin_api.py` riêng vẫn được hỗ trợ, nhưng khi đó metrics và
trace không được chia sẻ giữa hai tiến trình.

## 3. Thành phần

| Module | Trách nhiệm |
|---|---|
| `tftp_config.py` | Hằng số mặc định, `Defaults`, version. |
| `tftp_packet.py` | Đóng gói/giải mã RRQ/WRQ/DATA/ACK/ERROR/OACK; `TFTPError`. |
| `tftp_options.py` | Thương lượng `blksize`, `timeout`, `tsize`, `windowsize`; `TransferOptions`. |
| `tftp_client.py` | Upload/download, retry, window ACK, listing TCP cũ, progress/cancel. |
| `tftp_server.py` | UDP server, nhiều phiên, TID, window nhận, metrics/trace. |
| `storage_manager.py` | Path validation, CRUD, metadata, SHA-256, atomic write. |
| `auth.py` | User/role/token/audit, PBKDF2-SHA256, SQLite. |
| `admin_api.py` | HTTP REST, phân quyền endpoint, JSON/bytes response. |
| `admin_client.py` | Client gọi API cho GUI/CLI. |
| `metrics.py` | `TransferEvent`, thống kê, SQLite lịch sử. |
| `packet_trace.py` | `PacketEvent`, ring buffer sự kiện gói. |
| `packet_analyzer.py` | Lọc, nhóm, tổng hợp sự kiện gói. |
| `pxe_config.py` | Profile BIOS/UEFI, kiểm tra boot file, JSON. |
| `tftp_gui.py` | Tkinter sáu tab, cấu hình, login, thao tác quản trị. |
| `tftp_pro.py` | Chạy TFTP + Admin API chung tiến trình. |
| `scripts/benchmark_tftp.py` | Đo A–E, kiểm tra SHA-256, xuất JSON. |

## 4. Luồng dữ liệu

### 4.1. Upload/download TFTP

```text
GUI/CLI -> TFTPClient -> UDP -> TFTPServer -> StorageManager -> đĩa
                                  |
                                  +-> MetricsCollector (TransferEvent)
                                  +-> PacketTracer (PacketEvent)
```

- Client gửi RRQ/WRQ kèm option; server trả OACK nếu chấp nhận.
- Với `windowsize > 1`, bên gửi phát tối đa N DATA block rồi chờ ACK tích lũy
  của block cuối cửa sổ. Timeout/ACK cũ/thiếu block được xử lý theo cửa sổ.
- Server ghi file qua file tạm và `os.replace`; lỗi/hủy sẽ dọn file tạm.
- Server ghi `TransferEvent` khi phiên kết thúc, kể cả thất bại/hủy.

### 4.2. Quản trị

```text
GUI -> AdminClient -> HTTP Admin API -> AuthManager (token/permission)
                                  +-> StorageManager (file)
                                  +-> MetricsCollector (dashboard)
                                  +-> PacketTracer (packets)
                                  +-> PxeManager (profiles)
                                  +-> SQLite (users/tokens/audit/metrics)
```

GUI không mở database, không đọc trực tiếp thư mục server. Mọi thao tác file
đều qua endpoint đã phân quyền. Token không được ghi log.

### 4.3. SHA-256

1. Client/GUI tính SHA-256 file local trước khi upload.
2. Server lưu file qua `StorageManager`, tính hash khi metadata được yêu cầu.
3. GUI lấy hash server qua `/api/files/hash` (hoặc header `X-File-SHA256` khi
   download qua Admin API).
4. So sánh và hiển thị `VERIFIED` / `MISMATCH`.
5. Khi mismatch, ghi log và đề nghị truyền lại; không tuyên bố file an toàn chỉ
   dựa vào hash.

### 4.4. PXE

```text
PXE client -> DHCP độc lập (next-server, bootfile) -> TFTP Pro :6969
                                                       |
                                                       +-> server_storage/pxe/...
PxeManager -> pxe_profiles.json; kiểm tra boot file qua StorageManager
```

TFTP Pro không cấp IP, không trả DHCP option. PxeManager chỉ chọn boot file theo
`arch` (`bios`/`uefi`) và báo file thiếu.

### 4.5. Monitoring

- `MetricsCollector` tổng hợp số liệu từ `TransferEvent`, lưu SQLite nếu có
  `db_path`; Dashboard đọc qua API.
- `PacketTracer` giữ ring buffer (mặc định 2000 sự kiện) và đếm theo opcode.
- `PacketAnalyzer` là lớp thuần dữ liệu: lọc theo opcode/chiều/source/
  destination/block, tổng hợp payload, phát hiện DATA gửi lại.

## 5. Hợp đồng dữ liệu

### TransferEvent

```python
TransferEvent(
    timestamp: str,          # ISO-8601 UTC
    client_ip: str,
    filename: str,
    direction: str,          # "upload" | "download"
    bytes_total: int,
    duration: float,
    retries: int,
    timeouts: int,
    status: str,             # "success" | "failed" | "cancelled"
    error: str | None = None,
)
```

### PacketEvent

```python
PacketEvent(
    timestamp: str,          # HH:MM:SS.mmm
    source: str,             # "ip:port"
    destination: str,        # "ip:port"
    opcode: str,             # RRQ/WRQ/DATA/ACK/ERROR/OACK
    block_number: int | None,
    payload_length: int,
    direction: str,          # "Client -> Server" | "Server -> Client"
)
```

### FileMetadata

```python
FileMetadata(
    name: str,
    relative_path: str,
    size: int,
    modified_at: str,        # ISO-8601 UTC
    is_dir: bool,
    sha256: str | None,
)
```

Các dataclass đều có `to_dict()` để dùng thống nhất giữa API và GUI.

## 6. Ranh giới bảo mật

| Lớp | Kiểm soát |
|---|---|
| TFTP UDP | Không xác thực; chỉ dùng LAN/lab tin cậy. |
| Admin API | Bearer token, role/permission từng endpoint, mặc định localhost. |
| Mật khẩu | PBKDF2-HMAC-SHA256, salt ngẫu nhiên 16 byte, 200.000 vòng. |
| Token | Chuỗi ngẫu nhiên URL-safe; DB chỉ lưu SHA-256. |
| Filesystem | `StorageManager` chặn `..`, absolute path, ổ đĩa, symlink/junction. |
| Upload API | Giới hạn 64 MiB/request; ghi nguyên tử. |
| Log | Không ghi mật khẩu/token; lỗi API trả thông báo ngắn. |
| PXE | Chỉ profile/file; DHCP nằm ngoài phạm vi và phải chạy trong lab cô lập. |

## 7. Cấu trúc thư mục

```text
.
├── admin_api.py / admin_client.py
├── auth.py
├── metrics.py
├── packet_trace.py / packet_analyzer.py
├── pxe_config.py / pxe_profiles.json
├── storage_manager.py
├── tftp_config.py / tftp_options.py / tftp_packet.py
├── tftp_client.py / tftp_server.py / tftp_gui.py / tftp_pro.py
├── docs/
│   ├── admin_api.md
│   ├── architecture.md
│   ├── benchmark.md
│   ├── interface.md
│   ├── protocol.md
│   └── pxe_lab.md
├── scripts/benchmark_tftp.py
├── server_storage/
│   └── pxe/{boot,efi,config}/
└── tests/
```

## 8. Điểm mở rộng

- Thêm role/quyền: cập nhật `ROLE_PERMISSIONS` và kiểm tra endpoint tương ứng.
- Thêm loại metrics: mở rộng `TransferEvent` + schema SQLite + Dashboard.
- Thêm nguồn packet: gọi `PacketTracer.record_raw()` hoặc tạo `PacketEvent`.
- Thêm backend lưu trữ: giữ nguyên interface `StorageManager`.
- Thêm DHCP integration: dùng dịch vụ ngoài; PxeManager chỉ xuất boot filename.

## 9. Kiểm thử theo lớp

| Lớp | Test |
|---|---|
| Config/option/packet | `test_config.py`, `test_packet.py`, `test_windowsize.py` |
| Storage/path | `test_storage.py` |
| Auth/API/integrity | `test_auth.py`, `test_admin_api.py`, `test_integrity.py` |
| Metrics/analyzer | `test_metrics.py`, `test_packet_analyzer.py` |
| PXE | `test_pxe.py` |
| Client/server cũ | Các test hiện có trong `tests/` |

Chạy toàn bộ:

```powershell
python -m pytest -q
python -m ruff check .
```
