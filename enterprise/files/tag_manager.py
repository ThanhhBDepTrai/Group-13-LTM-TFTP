from __future__ import annotations

from database.database import Database
from enterprise.errors import EnterpriseError


class TagManager:
    def __init__(self, db: Database):
        self.db = db

    def add(self, file_id: int, tag: str) -> None:
        tag = str(tag).strip().lower()
        if not tag:
            raise EnterpriseError("Tag không được trống")
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM files WHERE id=?", (file_id,)).fetchone() is None:
                raise EnterpriseError("Không tìm thấy file", 404)
            c.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
            tid = c.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()["id"]
            c.execute("INSERT OR IGNORE INTO file_tags(file_id, tag_id) VALUES (?,?)",
                      (file_id, tid))

    def remove(self, file_id: int, tag: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM file_tags WHERE file_id=? AND tag_id="
                      "(SELECT id FROM tags WHERE name=?)", (file_id, tag.strip().lower()))

    def tags_of(self, file_id: int) -> list[str]:
        with self.db.connect() as c:
            return [r["name"] for r in c.execute(
                "SELECT t.name FROM tags t JOIN file_tags ft ON ft.tag_id=t.id "
                "WHERE ft.file_id=? ORDER BY t.name", (file_id,))]

    def files_with(self, tag: str) -> list[str]:
        with self.db.connect() as c:
            return [r["path"] for r in c.execute(
                "SELECT f.path FROM files f JOIN file_tags ft ON ft.file_id=f.id "
                "JOIN tags t ON t.id=ft.tag_id WHERE t.name=? ORDER BY f.path",
                (tag.strip().lower(),))]
