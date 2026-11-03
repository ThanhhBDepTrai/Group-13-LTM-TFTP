"""Hồ sơ nhân sự, gắn với tài khoản, phòng ban, chức vụ và quản lý trực tiếp."""

from __future__ import annotations

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError


class EmployeeManager:
    def __init__(self, db: Database):
        self.db = db

    def create(self, full_name, *, username=None, email=None, department_id=None,
               position_id=None, manager_id=None) -> dict:
        full_name = str(full_name).strip()
        if not full_name:
            raise EnterpriseError("Họ tên không được trống")
        with self.db.connect() as c:
            try:
                cur = c.execute(
                    "INSERT INTO employees(username, full_name, email, department_id, "
                    "position_id, manager_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    (username, full_name, email, department_id, position_id, manager_id,
                     now_iso()))
            except Exception as exc:
                raise EnterpriseError("Dữ liệu nhân sự không hợp lệ hoặc trùng tài khoản",
                                      409) from exc
            return self._get(c, cur.lastrowid)

    @staticmethod
    def _get(c, emp_id) -> dict:
        r = c.execute(
            "SELECT e.*, d.name AS department, p.name AS position FROM employees e "
            "LEFT JOIN departments d ON d.id=e.department_id "
            "LEFT JOIN positions p ON p.id=e.position_id WHERE e.id=?", (emp_id,)).fetchone()
        if r is None:
            raise EnterpriseError("Không tìm thấy nhân viên", 404)
        return dict(r)

    def get(self, emp_id: int) -> dict:
        with self.db.connect() as c:
            return self._get(c, emp_id)

    def by_username(self, username: str) -> dict | None:
        with self.db.connect() as c:
            r = c.execute("SELECT id FROM employees WHERE username=?", (username,)).fetchone()
            return self._get(c, r["id"]) if r else None

    def list(self, *, department_id=None) -> list[dict]:
        sql, args = ("SELECT e.*, d.name AS department, p.name AS position FROM employees e "
                     "LEFT JOIN departments d ON d.id=e.department_id "
                     "LEFT JOIN positions p ON p.id=e.position_id"), []
        if department_id is not None:
            sql += " WHERE e.department_id=?"
            args.append(department_id)
        with self.db.connect() as c:
            return [dict(r) for r in c.execute(sql + " ORDER BY e.full_name", args)]

    def update(self, emp_id: int, **fields) -> dict:
        allowed = {"full_name", "email", "department_id", "position_id", "manager_id"}
        bad = set(fields) - allowed
        if bad:
            raise EnterpriseError(f"Trường không hợp lệ: {', '.join(sorted(bad))}")
        if fields.get("manager_id") == emp_id:
            raise EnterpriseError("Nhân viên không thể tự quản lý chính mình")
        with self.db.connect() as c:
            if fields:
                sets = ", ".join(f"{k}=?" for k in fields)
                c.execute(f"UPDATE employees SET {sets} WHERE id=?", (*fields.values(), emp_id))
            return self._get(c, emp_id)

    def delete(self, emp_id: int) -> None:
        with self.db.connect() as c:
            if c.execute("DELETE FROM employees WHERE id=?", (emp_id,)).rowcount == 0:
                raise EnterpriseError("Không tìm thấy nhân viên", 404)
