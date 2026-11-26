from __future__ import annotations

from database.database import Database
from enterprise.errors import EnterpriseError


class _NamedTable:
    table = ""
    label = ""

    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str, description: str = "") -> dict:
        name = str(name).strip()
        if not name:
            raise EnterpriseError(f"Tên {self.label} không được trống")
        with self.db.connect() as c:
            try:
                cur = c.execute(f"INSERT INTO {self.table}(name, description) VALUES (?,?)",
                                (name, description))
            except Exception as exc:
                raise EnterpriseError(f"{self.label} đã tồn tại", 409) from exc
            return dict(c.execute(f"SELECT * FROM {self.table} WHERE id=?",
                                  (cur.lastrowid,)).fetchone())

    def list(self) -> list[dict]:
        with self.db.connect() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM {self.table} ORDER BY name")]

    def delete(self, item_id: int) -> None:
        with self.db.connect() as c:
            if c.execute(f"DELETE FROM {self.table} WHERE id=?", (item_id,)).rowcount == 0:
                raise EnterpriseError(f"Không tìm thấy {self.label}", 404)


class DepartmentManager(_NamedTable):
    table, label = "departments", "Phòng ban"

    def update(
        self,
        item_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        quota_bytes: int | None = None,
    ) -> dict:
        fields: list[str] = []
        args: list = []
        if name is not None:
            name = str(name).strip()
            if not name:
                raise EnterpriseError("Tên phòng ban không được trống")
            fields.append("name=?")
            args.append(name)
        if description is not None:
            fields.append("description=?")
            args.append(str(description))
        if quota_bytes is not None:
            try:
                quota = int(quota_bytes)
            except (TypeError, ValueError) as exc:
                raise EnterpriseError("quota_bytes phải là số nguyên") from exc
            if quota <= 0:
                raise EnterpriseError("quota_bytes phải lớn hơn 0")
            fields.append("quota_bytes=?")
            args.append(quota)
        if not fields:
            raise EnterpriseError("Không có trường nào để cập nhật")
        args.append(int(item_id))
        with self.db.connect() as c:
            changed = c.execute(
                f"UPDATE {self.table} SET {', '.join(fields)} WHERE id=?", args
            ).rowcount
            if changed == 0:
                raise EnterpriseError(f"Không tìm thấy {self.label}", 404)
            return dict(
                c.execute(f"SELECT * FROM {self.table} WHERE id=?", (int(item_id),)).fetchone()
            )
