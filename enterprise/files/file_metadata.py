"""Metadata file (owner, size, sha256) lưu trong DB."""

from __future__ import annotations

import hashlib
from pathlib import Path

from database.database import Database, now_iso
from enterprise.errors import EnterpriseError
from enterprise.permissions.file_acl import normalize


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class FileMetadata:
    def __init__(self, db: Database):
        self.db = db

    def register(self, virtual: str, real: Path, owner: str) -> dict:
        path = normalize(virtual)
        if not real.is_file():
            raise EnterpriseError("File không tồn tại", 404)
        digest, size = sha256_file(real), real.stat().st_size
        with self.db.connect() as c:
            c.execute(
                "INSERT INTO files(path, owner, size, sha256, created_at) VALUES (?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET size=excluded.size, sha256=excluded.sha256",
                (path, owner, size, digest, now_iso()))
            return dict(c.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone())

    def get(self, virtual: str) -> dict | None:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM files WHERE path=?", (normalize(virtual),)).fetchone()
        return dict(r) if r else None

    def delete(self, virtual: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM files WHERE path=?", (normalize(virtual),))
