"""Cấu hình mặc định tập trung cho toàn bộ TFTP Pro.

Mọi module (client, server, GUI, admin API, benchmark) đọc giá trị mặc định
từ đây để tránh tình trạng CLI dùng port 69 còn server/GUI dùng 6969.

Có thể ghi đè bằng tham số dòng lệnh hoặc biến môi trường. Các hằng số dưới
đây chỉ là giá trị mặc định, không phải giới hạn cứng của giao thức.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

APP_NAME = "TFTP Pro"
VERSION = "1.1.0"

# --- TFTP core -------------------------------------------------------------
DEFAULT_HOST = "0.0.0.0"  # server bind address
DEFAULT_CLIENT_HOST = "127.0.0.1"
DEFAULT_PORT = 6969
DEFAULT_ROOT = "server_storage"

DEFAULT_BLKSIZE = 512
DEFAULT_TIMEOUT = 3.0  # giây
DEFAULT_RETRIES = 5
DEFAULT_MAX_CLIENTS = 20

# RFC 7440: windowsize = 1 tương đương hành vi RFC 1350 (an toàn nhất).
DEFAULT_WINDOWSIZE = 1
# Server chấp nhận tối đa 16 block/cửa sổ ở cấu hình mặc định. Client có thể
# yêu cầu nhỏ hơn; giá trị OACK luôn <= giá trị client yêu cầu.
DEFAULT_MAX_WINDOWSIZE = 16

# --- Dịch vụ quản trị (HTTP/TCP, tách khỏi TFTP) ---------------------------
# Mặc định chỉ bind localhost. Muốn mở trong LAN phải cấu hình host/port và
# bảo vệ bằng token (xem docs/admin_api.md).
DEFAULT_ADMIN_HOST = "127.0.0.1"
DEFAULT_ADMIN_PORT = 6971
DEFAULT_ADMIN_DB = "tftp_admin.db"
DEFAULT_TOKEN_TTL = 8 * 60 * 60  # 8 giờ
DEFAULT_MAX_ADMIN_BODY = 64 * 1024 * 1024  # 64 MiB cho một lần upload API

# --- PXE lab ---------------------------------------------------------------
DEFAULT_PXE_PROFILES = "pxe_profiles.json"
PXE_BOOT_DIRS = ("pxe/boot", "pxe/efi", "pxe/config")

# --- Dịch vụ liệt kê file kiểu cũ (TCP port + 1) ---------------------------
# Vẫn giữ để tương thích GUI/CLI cũ; chức năng quản trị mới đi qua Admin API.
LEGACY_LISTING_OFFSET = 1


@dataclass(frozen=True)
class Defaults:
    """Bản sao bất biến của cấu hình, tiện cho test và hiển thị."""

    host: str = DEFAULT_HOST
    client_host: str = DEFAULT_CLIENT_HOST
    port: int = DEFAULT_PORT
    root: Path = Path(DEFAULT_ROOT)
    blksize: int = DEFAULT_BLKSIZE
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    max_clients: int = DEFAULT_MAX_CLIENTS
    windowsize: int = DEFAULT_WINDOWSIZE
    max_windowsize: int = DEFAULT_MAX_WINDOWSIZE
    admin_host: str = DEFAULT_ADMIN_HOST
    admin_port: int = DEFAULT_ADMIN_PORT
    admin_db: str = DEFAULT_ADMIN_DB
    pxe_profiles: str = DEFAULT_PXE_PROFILES


DEFAULTS = Defaults()

__all__ = [
    "APP_NAME",
    "DEFAULTS",
    "DEFAULT_ADMIN_DB",
    "DEFAULT_ADMIN_HOST",
    "DEFAULT_ADMIN_PORT",
    "DEFAULT_BLKSIZE",
    "DEFAULT_CLIENT_HOST",
    "DEFAULT_HOST",
    "DEFAULT_MAX_ADMIN_BODY",
    "DEFAULT_MAX_CLIENTS",
    "DEFAULT_MAX_WINDOWSIZE",
    "DEFAULT_PORT",
    "DEFAULT_PXE_PROFILES",
    "DEFAULT_RETRIES",
    "DEFAULT_ROOT",
    "DEFAULT_TIMEOUT",
    "DEFAULT_TOKEN_TTL",
    "DEFAULT_WINDOWSIZE",
    "LEGACY_LISTING_OFFSET",
    "PXE_BOOT_DIRS",
    "VERSION",
    "Defaults",
]
