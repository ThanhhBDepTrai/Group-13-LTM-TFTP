"""Dữ liệu khởi tạo: vai trò, quyền và ma trận role -> permission."""

from __future__ import annotations

ROLES = [  # (name, level, mô tả)
    ("SUPER_ADMIN", 100, "Toàn quyền trên Server"),
    ("ADMIN", 80, "Quản trị hệ thống"),
    ("HR", 60, "Quản lý nhân sự"),
    ("MANAGER", 60, "Quản lý phòng ban"),
    ("TEAM_LEADER", 40, "Trưởng nhóm"),
    ("EMPLOYEE", 20, "Nhân viên"),
    ("INTERN", 10, "Thực tập sinh"),
]

PERMISSIONS = {
    "server.manage": "Cấu hình Server, join code",
    "users.manage": "Tạo/sửa/khóa tài khoản",
    "employees.manage": "Quản lý nhân sự, phòng ban, chức vụ",
    "employees.read": "Xem danh sách nhân sự",
    "roles.manage": "Cấp/đổi vai trò",
    "files.list": "Liệt kê file",
    "files.read": "Đọc/tải file",
    "files.upload": "Tải file lên",
    "files.delete": "Xóa file",
    "files.manage": "Quản lý folder, tag, ACL",
    "tasks.manage": "Tạo và giao nhiệm vụ",
    "tasks.update": "Cập nhật trạng thái nhiệm vụ được giao",
    "audit.read": "Xem lịch sử hoạt động",
    # Quyền tương thích với các endpoint TFTP Pro cũ.
    "files.download": "Tải file từ TFTP Pro",
    "files.mkdir": "Tạo thư mục trên File Manager",
    "files.rename": "Đổi tên file hoặc thư mục",
    "pxe.read": "Xem cấu hình PXE",
    "pxe.manage": "Quản lý cấu hình PXE",
    "stats.read": "Xem Dashboard và Packet Analyzer",
}

_ALL = set(PERMISSIONS)
_BASIC = {"files.list", "files.read", "tasks.update"}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "SUPER_ADMIN": _ALL,
    "ADMIN": _ALL - {"roles.manage"},
    "HR": _BASIC | {"employees.manage", "employees.read", "users.manage", "files.upload"},
    "MANAGER": _BASIC | {"employees.read", "files.upload", "files.delete", "files.manage",
                         "tasks.manage", "audit.read"},
    "TEAM_LEADER": _BASIC | {"employees.read", "files.upload", "tasks.manage"},
    "EMPLOYEE": _BASIC | {"files.upload"},
    "INTERN": set(_BASIC),
}


def seed(db) -> None:
    with db.connect() as conn:
        for name, level, desc in ROLES:
            conn.execute("INSERT OR IGNORE INTO roles(name, level, description) VALUES (?,?,?)",
                         (name, level, desc))
        for code, desc in PERMISSIONS.items():
            conn.execute("INSERT OR IGNORE INTO permissions(code, description) VALUES (?,?)",
                         (code, desc))
        for role, perms in ROLE_PERMISSIONS.items():
            for p in perms:
                conn.execute("INSERT OR IGNORE INTO role_permissions(role, permission) "
                             "VALUES (?,?)", (role, p))
