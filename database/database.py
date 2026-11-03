"""Kết nối SQLite và schema Enterprise File Manager (mỗi Server một database)."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA journal_mode = WAL;
CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS servers (
    server_id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
    api_port INTEGER NOT NULL, tftp_port INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspaces (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roles (
    name TEXT PRIMARY KEY, description TEXT, level INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS permissions (
    code TEXT PRIMARY KEY, description TEXT);
CREATE TABLE IF NOT EXISTS role_permissions (
    role TEXT NOT NULL REFERENCES roles(name) ON DELETE CASCADE,
    permission TEXT NOT NULL REFERENCES permissions(code) ON DELETE CASCADE,
    PRIMARY KEY (role, permission));
CREATE TABLE IF NOT EXISTS departments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT);
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, description TEXT);
CREATE TABLE IF NOT EXISTS ent_users (
    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, salt TEXT NOT NULL,
    role TEXT NOT NULL REFERENCES roles(name), active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE REFERENCES ent_users(username) ON DELETE SET NULL,
    full_name TEXT NOT NULL, email TEXT,
    department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    position_id INTEGER REFERENCES positions(id) ON DELETE SET NULL,
    manager_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS folders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
    owner TEXT, department_id INTEGER REFERENCES departments(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, owner TEXT,
    size INTEGER NOT NULL DEFAULT 0, sha256 TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS file_tags (
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (file_id, tag_id));
CREATE TABLE IF NOT EXISTS file_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('user','role','department')),
    subject TEXT NOT NULL, can_read INTEGER NOT NULL DEFAULT 0,
    can_write INTEGER NOT NULL DEFAULT 0, can_delete INTEGER NOT NULL DEFAULT 0,
    UNIQUE (path, subject_type, subject));
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT,
    status TEXT NOT NULL DEFAULT 'todo', created_by TEXT NOT NULL,
    due_date TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_assignments (
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    username TEXT NOT NULL, assigned_at TEXT NOT NULL, PRIMARY KEY (task_id, username));
CREATE TABLE IF NOT EXISTS join_codes (
    code_hash TEXT PRIMARY KEY, role TEXT NOT NULL, created_by TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, max_uses INTEGER NOT NULL DEFAULT 1,
    uses INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    username TEXT NOT NULL REFERENCES ent_users(username) ON DELETE CASCADE,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, client_ip TEXT);
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, actor TEXT,
    action TEXT NOT NULL, target TEXT, detail TEXT, success INTEGER NOT NULL DEFAULT 1);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);
"""


def now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class Database:
    """Bọc SQLite: mỗi lần dùng mở một kết nối ngắn, an toàn với nhiều thread."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            if conn.execute("SELECT COUNT(*) FROM schema_info").fetchone()[0] == 0:
                conn.execute("INSERT INTO schema_info(version) VALUES (?)", (SCHEMA_VERSION,))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
            conn.commit()
        finally:
            conn.close()


__all__ = ["Database", "SCHEMA_VERSION", "now_iso"]
