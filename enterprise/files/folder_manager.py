"""Folder metadata + thư mục thật trong storage (an toàn path traversal)."""

from __future__ import annotations

from pathlib import Path

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError
from enterprise.permissions.file_acl import normalize


class FolderManager:
    def __init__(self, db: Database, root: str | Path):
        self.db, self.root = db, Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def real_path(self, virtual: str) -> Path:
        target = (self.root / normalize(virtual).lstrip("/")).resolve()
        if target != self.root and self.root not in target.parents:
            raise EnterpriseError("Đường dẫn vượt ra ngoài storage", 403)
        return target

    def create(self, virtual: str, *, owner=None, department_id=None) -> dict:
        path = normalize(virtual)
        self.real_path(path).mkdir(parents=True, exist_ok=True)
        with self.db.connect() as c:
            c.execute("INSERT OR IGNORE INTO folders(path, owner, department_id, created_at) "
                      "VALUES (?,?,?,?)", (path, owner, department_id, now_iso()))
            return dict(c.execute("SELECT * FROM folders WHERE path=?", (path,)).fetchone())

    def get(self, virtual: str) -> dict | None:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM folders WHERE path=?", (normalize(virtual),)).fetchone()
        return dict(r) if r else None

    def list(self) -> list[dict]:
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM folders ORDER BY path")]
