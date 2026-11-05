"""Lớp truy cập SQLite: tài khoản và lịch sử truyền file.

Dùng một kết nối + khóa để an toàn khi nhiều thread (TFTP session, API) cùng ghi.
Mọi câu lệnh SQL dùng tham số ``?``, không ghép chuỗi từ dữ liệu người dùng.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

__all__ = ["Database", "DatabaseError"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'user', 'guest')),
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transfers (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    op              TEXT NOT NULL,
    filename        TEXT NOT NULL,
    client          TEXT NOT NULL,
    path            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL,
    error           TEXT NOT NULL DEFAULT '',
    bytes           INTEGER NOT NULL DEFAULT 0,
    blocks          INTEGER NOT NULL DEFAULT 0,
    retransmits     INTEGER NOT NULL DEFAULT 0,
    blksize         INTEGER NOT NULL DEFAULT 512,
    windowsize      INTEGER NOT NULL DEFAULT 1,
    duration        REAL NOT NULL DEFAULT 0,
    throughput_mbps REAL NOT NULL DEFAULT 0,
    sha256          TEXT NOT NULL DEFAULT '',
    started_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transfers_started ON transfers (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers (status);
"""

_TRANSFER_COLUMNS = (
    "op", "filename", "client", "path", "status", "error", "bytes", "blocks",
    "retransmits", "blksize", "windowsize", "duration", "throughput_mbps",
    "sha256", "started_at",
)


class DatabaseError(Exception):
    """Lỗi nghiệp vụ của lớp dữ liệu (ví dụ trùng tên người dùng)."""


class Database:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #

    def _run(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self._all(sql, params)
        return rows[0] if rows else None

    # ------------------------------------------------------------------ #
    # Người dùng
    # ------------------------------------------------------------------ #

    def add_user(self, username: str, password_hash: str, role: str) -> int:
        try:
            cursor = self._run(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, time.time()),
            )
        except sqlite3.IntegrityError as exc:
            if "UNIQUE" in str(exc):
                raise DatabaseError(f"User already exists: {username}") from exc
            raise DatabaseError(f"Invalid user data: {exc}") from exc
        return int(cursor.lastrowid)

    def get_user(self, username: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE username = ?", (username,))

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE id = ?", (user_id,))

    def list_users(self) -> list[dict[str, Any]]:
        return self._all(
            "SELECT id, username, role, disabled, created_at FROM users ORDER BY id"
        )

    def count_users(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    def count_active_admins(self) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0")
        return int(row["n"]) if row else 0

    def update_user(self, user_id: int, **changes: Any) -> bool:
        allowed = {"password_hash", "role", "disabled"}
        unknown = set(changes) - allowed
        if unknown:
            raise DatabaseError(f"Cannot update fields: {sorted(unknown)}")
        if not changes:
            return False
        assignments = ", ".join(f"{name} = ?" for name in changes)  # tên cột nằm trong whitelist
        try:
            cursor = self._run(
                f"UPDATE users SET {assignments} WHERE id = ?",
                (*changes.values(), user_id),
            )
        except sqlite3.IntegrityError as exc:
            raise DatabaseError(f"Invalid user data: {exc}") from exc
        return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        return self._run("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0

    # ------------------------------------------------------------------ #
    # Lịch sử truyền file
    # ------------------------------------------------------------------ #

    def add_transfer(self, record: dict[str, Any]) -> int:
        values = [record.get(column, "" if column in ("path", "error", "sha256") else 0)
                  for column in _TRANSFER_COLUMNS]
        placeholders = ", ".join("?" for _ in _TRANSFER_COLUMNS)
        cursor = self._run(
            f"INSERT INTO transfers ({', '.join(_TRANSFER_COLUMNS)}) VALUES ({placeholders})",
            values,
        )
        return int(cursor.lastrowid)

    def set_transfer_sha256(self, transfer_id: int, digest: str) -> None:
        self._run("UPDATE transfers SET sha256 = ? WHERE id = ?", (digest, transfer_id))

    def get_transfer(self, transfer_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM transfers WHERE id = ?", (transfer_id,))

    def list_transfers(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        op: str | None = None,
        filename: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if op:
            clauses.append("op = ?")
            params.append(op.upper())
        if filename:
            escaped = filename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("filename LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        return self._all(
            f"SELECT * FROM transfers {where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )

    def transfer_summary(self) -> dict[str, Any]:
        row = self._one(
            """
            SELECT COUNT(*) AS total,
                   COALESCE(SUM(status = 'ok'), 0) AS ok,
                   COALESCE(SUM(status != 'ok'), 0) AS failed,
                   COALESCE(SUM(CASE WHEN status = 'ok' AND op = 'RRQ' THEN bytes END), 0) AS bytes_read,
                   COALESCE(SUM(CASE WHEN status = 'ok' AND op = 'WRQ' THEN bytes END), 0) AS bytes_written,
                   COALESCE(SUM(retransmits), 0) AS retransmits
            FROM transfers
            """
        )
        return row or {}

    def prune_transfers(self, keep_last: int) -> int:
        """Giữ ``keep_last`` bản ghi mới nhất, xóa phần còn lại; trả về số bản ghi đã xóa."""
        return self._run(
            "DELETE FROM transfers WHERE id NOT IN "
            "(SELECT id FROM transfers ORDER BY started_at DESC, id DESC LIMIT ?)",
            (max(0, keep_last),),
        ).rowcount
