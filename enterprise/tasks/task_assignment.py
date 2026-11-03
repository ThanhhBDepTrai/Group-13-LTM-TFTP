from __future__ import annotations

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError


class TaskAssignment:
    def __init__(self, db: Database):
        self.db = db

    def assign(self, task_id: int, username: str) -> None:
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
                raise EnterpriseError("Không tìm thấy nhiệm vụ", 404)
            u = c.execute("SELECT active FROM ent_users WHERE username=?", (username,)).fetchone()
            if u is None or not u["active"]:
                raise EnterpriseError("Người nhận không tồn tại hoặc đã bị khóa", 404)
            c.execute("INSERT OR IGNORE INTO task_assignments(task_id, username, assigned_at) "
                      "VALUES (?,?,?)", (task_id, username, now_iso()))

    def unassign(self, task_id: int, username: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM task_assignments WHERE task_id=? AND username=?",
                      (task_id, username))

    def assignees(self, task_id: int) -> list[str]:
        with self.db.connect() as c:
            return [r["username"] for r in c.execute(
                "SELECT username FROM task_assignments WHERE task_id=? ORDER BY username",
                (task_id,))]
