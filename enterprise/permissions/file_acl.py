"""ACL theo đường dẫn: user / role / department. Quy tắc: deny mặc định,
chủ file và SUPER_ADMIN luôn có quyền; ACL gần nhất (đường dẫn dài nhất) thắng."""

from __future__ import annotations

import posixpath

from database.database import Database
from enterprise.errors import EnterpriseError

ACTIONS = {"read": "can_read", "write": "can_write", "delete": "can_delete"}


def normalize(path: str) -> str:
    p = posixpath.normpath("/" + str(path).replace("\\", "/").strip("/"))
    if ".." in p.split("/"):
        raise EnterpriseError("Đường dẫn không hợp lệ")
    return p


class FileACL:
    def __init__(self, db: Database):
        self.db = db

    def set(self, path, subject_type, subject, *, read=False, write=False, delete=False):
        if subject_type not in ("user", "role", "department"):
            raise EnterpriseError("subject_type không hợp lệ")
        with self.db.connect() as c:
            c.execute(
                "INSERT INTO file_permissions(path, subject_type, subject, can_read, can_write, "
                "can_delete) VALUES (?,?,?,?,?,?) ON CONFLICT(path, subject_type, subject) "
                "DO UPDATE SET can_read=excluded.can_read, can_write=excluded.can_write, "
                "can_delete=excluded.can_delete",
                (normalize(path), subject_type, subject, int(read), int(write), int(delete)))

    def remove(self, path, subject_type, subject) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM file_permissions WHERE path=? AND subject_type=? "
                      "AND subject=?", (normalize(path), subject_type, subject))

    def check(self, user: dict, path: str, action: str, *, owner: str | None = None) -> bool:
        if action not in ACTIONS:
            raise EnterpriseError("Hành động không hợp lệ")
        if user.get("role") == "SUPER_ADMIN" or (owner and owner == user.get("username")):
            return True
        target, column = normalize(path), ACTIONS[action]
        subjects = {("user", user["username"]), ("role", user["role"])}
        if user.get("department"):
            subjects.add(("department", user["department"]))
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM file_permissions").fetchall()
        best_len, allowed = -1, False
        for r in rows:
            base = r["path"]
            covers = target == base or target.startswith(base.rstrip("/") + "/")
            if covers and (r["subject_type"], r["subject"]) in subjects and len(base) >= best_len:
                if len(base) > best_len:
                    best_len, allowed = len(base), False
                allowed = allowed or bool(r[column])
        return allowed

    def can_read_tree(self, user: dict, path: str) -> bool:
        """Return whether a directory or any descendant is readable by the user."""
        target = normalize(path)
        if self.check(user, target, "read"):
            return True
        prefix = target.rstrip("/") + "/"
        subjects = {("user", user["username"]), ("role", user["role"])}
        if user.get("department"):
            subjects.add(("department", user["department"]))
        with self.db.connect() as c:
            rows = c.execute(
                "SELECT path, subject_type, subject FROM file_permissions "
                "WHERE can_read=1"
            ).fetchall()
        return any(
            row["path"].startswith(prefix)
            and (row["subject_type"], row["subject"]) in subjects
            and self.check(user, row["path"], "read")
            for row in rows
        )

    def require(self, user, path, action, *, owner=None) -> None:
        if not self.check(user, path, action, owner=owner):
            raise EnterpriseError("Không có quyền với tệp/thư mục này", 403)
