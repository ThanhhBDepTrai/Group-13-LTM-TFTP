"""Kiểm tra quyền thực tế qua bảng role_permissions (không kế thừa tự động)."""

from __future__ import annotations

from database.database import Database
from enterprise.errors import EnterpriseError


class PermissionManager:
    def __init__(self, db: Database):
        self.db = db

    def permissions_of(self, role: str) -> set[str]:
        with self.db.connect() as c:
            rows = c.execute("SELECT permission FROM role_permissions WHERE role=?",
                             (role,)).fetchall()
        return {r["permission"] for r in rows}

    def has(self, role: str, permission: str) -> bool:
        return permission in self.permissions_of(role)

    def require(self, user: dict, permission: str) -> None:
        if not self.has(user.get("role", ""), permission):
            raise EnterpriseError("Không có quyền thực hiện thao tác này", 403)

    def grant(self, role: str, permission: str) -> None:
        with self.db.connect() as c:
            try:
                c.execute("INSERT OR IGNORE INTO role_permissions(role, permission) "
                          "VALUES (?,?)", (role, permission))
            except Exception as exc:
                raise EnterpriseError("Role hoặc permission không tồn tại") from exc

    def revoke(self, role: str, permission: str) -> None:
        if role == "SUPER_ADMIN":
            raise EnterpriseError("Không thể thu hồi quyền của SUPER_ADMIN", 403)
        with self.db.connect() as c:
            c.execute("DELETE FROM role_permissions WHERE role=? AND permission=?",
                      (role, permission))
