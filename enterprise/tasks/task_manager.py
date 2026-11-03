"""Giao nhiệm vụ và theo dõi trạng thái."""

from __future__ import annotations

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError
from enterprise.tasks.task_assignment import TaskAssignment
from enterprise.tasks.task_status import can_transition


class TaskManager:
    def __init__(self, db: Database):
        self.db = db
        self.assignments = TaskAssignment(db)

    def create(self, title, created_by, *, description="", due_date=None, assignees=()) -> dict:
        title = str(title).strip()
        if not title:
            raise EnterpriseError("Tiêu đề nhiệm vụ không được trống")
        now = now_iso()
        with self.db.connect() as c:
            cur = c.execute(
                "INSERT INTO tasks(title, description, created_by, due_date, created_at, "
                "updated_at) VALUES (?,?,?,?,?,?)",
                (title, description, created_by, due_date, now, now))
            task_id = cur.lastrowid
        for name in assignees:
            self.assignments.assign(task_id, name)
        return self.get(task_id)

    def get(self, task_id: int) -> dict:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if r is None:
            raise EnterpriseError("Không tìm thấy nhiệm vụ", 404)
        d = dict(r)
        d["assignees"] = self.assignments.assignees(task_id)
        return d

    def list_for(self, username: str) -> list[dict]:
        with self.db.connect() as c:
            ids = [r["task_id"] for r in c.execute(
                "SELECT task_id FROM task_assignments WHERE username=?", (username,))]
        return [self.get(i) for i in sorted(ids)]

    def list_all(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT id FROM tasks", []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        with self.db.connect() as c:
            ids = [r["id"] for r in c.execute(sql + " ORDER BY id", args)]
        return [self.get(i) for i in ids]

    def set_status(self, task_id: int, new_status: str) -> dict:
        task = self.get(task_id)
        if not can_transition(task["status"], new_status):
            raise EnterpriseError(
                f"Không thể chuyển trạng thái từ {task['status']} sang {new_status}", 409)
        with self.db.connect() as c:
            c.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?",
                      (new_status, now_iso(), task_id))
        return self.get(task_id)
