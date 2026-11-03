"""Mỗi Server có một Workspace (một công ty)."""

from __future__ import annotations

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError
from enterprise.workspace.workspace_models import Workspace


class WorkspaceManager:
    def __init__(self, db: Database):
        self.db = db

    def create(self, name: str) -> Workspace:
        name = str(name).strip()
        if not name:
            raise EnterpriseError("Tên Workspace không được trống")
        with self.db.connect() as c:
            try:
                cur = c.execute("INSERT INTO workspaces(name, created_at) VALUES (?,?)",
                                (name, now_iso()))
            except Exception as exc:
                raise EnterpriseError("Workspace đã tồn tại", 409) from exc
            row = c.execute("SELECT * FROM workspaces WHERE id=?", (cur.lastrowid,)).fetchone()
        return Workspace(row["id"], row["name"], row["created_at"])

    def get(self) -> Workspace | None:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM workspaces ORDER BY id LIMIT 1").fetchone()
        return Workspace(row["id"], row["name"], row["created_at"]) if row else None
