"""Quản lý vai trò người dùng, bảo vệ SUPER_ADMIN cuối cùng."""

from __future__ import annotations

from database.database import Database
from enterprise.errors import EnterpriseError


class RoleManager:
    def __init__(self, db: Database):
        self.db = db

    def list_roles(self) -> list[dict]:
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM roles ORDER BY level DESC")]

    def level(self, role: str) -> int:
        with self.db.connect() as c:
            r = c.execute("SELECT level FROM roles WHERE name=?", (role,)).fetchone()
        if r is None:
            raise EnterpriseError("Vai trò không tồn tại")
        return r["level"]

    def can_assign(self, actor_role: str, target_role: str) -> bool:
        """Chỉ được cấp vai trò có level thấp hơn level của mình (SUPER_ADMIN cấp mọi role)."""
        if actor_role == "SUPER_ADMIN":
            return True
        return self.level(target_role) < self.level(actor_role)

    def set_role(self, actor: dict, username: str, new_role: str) -> None:
        self.level(new_role)
        if not self.can_assign(actor["role"], new_role):
            raise EnterpriseError("Không được cấp vai trò ngang hoặc cao hơn mình", 403)
        with self.db.connect() as c:
            row = c.execute("SELECT role FROM ent_users WHERE username=?",
                            (username,)).fetchone()
            if row is None:
                raise EnterpriseError("Không tìm thấy người dùng", 404)
            if row["role"] == "SUPER_ADMIN" and actor["role"] != "SUPER_ADMIN":
                raise EnterpriseError("Không được sửa SUPER_ADMIN", 403)
            if row["role"] == "SUPER_ADMIN" and new_role != "SUPER_ADMIN":
                n = c.execute("SELECT COUNT(*) FROM ent_users WHERE role='SUPER_ADMIN' "
                              "AND active=1").fetchone()[0]
                if n <= 1:
                    raise EnterpriseError("Không thể bỏ SUPER_ADMIN cuối cùng", 409)
            c.execute("UPDATE ent_users SET role=? WHERE username=?", (new_role, username))
            c.execute("DELETE FROM sessions WHERE username=?", (username,))
