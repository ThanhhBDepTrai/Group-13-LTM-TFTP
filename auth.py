"""Tài khoản, vai trò và token cho Admin API.

Không lưu mật khẩu dạng rõ. Mật khẩu được băm bằng PBKDF2-HMAC-SHA256 với salt
ngẫu nhiên; token phiên được lưu dưới dạng SHA-256 (không lưu token gốc).

Đây là lớp xác thực tối giản phục vụ lab, không thay thế một hệ thống IAM đầy
đủ. Admin API mặc định chỉ bind localhost (xem tftp_config.py).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tftp_config import DEFAULT_TOKEN_TTL

_PBKDF2_ITERATIONS = 200_000
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ROLES = ("admin", "user", "guest")
MIN_PASSWORD_LENGTH = 4

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        "files.list",
        "files.read",
        "files.download",
        "files.upload",
        "files.delete",
        "files.rename",
        "files.mkdir",
        "users.manage",
        "stats.read",
        "pxe.read",
        "pxe.manage",
    },
    "user": {
        "files.list",
        "files.read",
        "files.download",
        "files.upload",
        "stats.read",
        "pxe.read",
    },
    "guest": {
        "files.list",
        "files.read",
        "files.download",
        "pxe.read",
    },
}


class AuthError(Exception):
    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.message = message
        self.status = status


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS
    ).hex()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthManager:
    def __init__(self, db_path: str | Path, *, token_ttl: int = DEFAULT_TOKEN_TTL):
        self.db_path = str(db_path)
        self.token_ttl = token_ttl
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    role TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS tokens (
                    token_hash TEXT PRIMARY KEY,
                    username TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    FOREIGN KEY (username) REFERENCES users(username) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    username TEXT,
                    action TEXT NOT NULL,
                    detail TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_tokens_expires ON tokens(expires_at);
                """
            )

    # ------------------------------------------------------------------ #
    # Tài khoản
    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_username(username: str) -> str:
        text = str(username).strip()
        if not _USERNAME_RE.fullmatch(text):
            raise AuthError("Username chỉ gồm chữ, số, _ . - (1-64 ký tự)", 400)
        return text

    @staticmethod
    def _validate_role(role: str) -> str:
        text = str(role).strip().lower()
        if text not in _ROLES:
            raise AuthError(f"Role phải là một trong: {', '.join(_ROLES)}", 400)
        return text

    @staticmethod
    def _validate_password(password: str) -> str:
        text = str(password)
        if len(text) < MIN_PASSWORD_LENGTH:
            raise AuthError(
                f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự", 400
            )
        return text

    def create_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        *,
        active: bool = True,
    ) -> dict:
        username = self._validate_username(username)
        role = self._validate_role(role)
        password = self._validate_password(password)
        salt = os.urandom(16)
        password_hash = _hash_password(password, salt)
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO users(username, password_hash, salt, role, "
                    "created_at, active) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        username,
                        password_hash,
                        salt.hex(),
                        role,
                        _now_iso(),
                        1 if active else 0,
                    ),
                )
                self._audit(connection, username, "user.create", f"role={role}")
        except sqlite3.IntegrityError as exc:
            raise AuthError("Username đã tồn tại", 409) from exc
        return self.get_user(username)

    def get_user(self, username: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username, role, created_at, active FROM users WHERE username=?",
                (username,),
            ).fetchone()
        if row is None:
            raise AuthError("Không tìm thấy người dùng", 404)
        return dict(row)

    def list_users(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT username, role, created_at, active FROM users ORDER BY username"
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_user(self, username: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role FROM users WHERE username=?", (username,)
            ).fetchone()
            if row is None:
                raise AuthError("Không tìm thấy người dùng", 404)
            if row["role"] == "admin":
                admin_count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
                ).fetchone()[0]
                if admin_count <= 1:
                    raise AuthError("Không thể xóa admin cuối cùng", 409)
            connection.execute("DELETE FROM users WHERE username=?", (username,))
            self._audit(connection, None, "user.delete", username)

    def _ensure_not_last_admin(self, connection, username: str) -> None:
        row = connection.execute(
            "SELECT role, active FROM users WHERE username=?", (username,)
        ).fetchone()
        if row is None:
            raise AuthError("Không tìm thấy người dùng", 404)
        if row["role"] != "admin" or not row["active"]:
            return
        admin_count = connection.execute(
            "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
        ).fetchone()[0]
        if admin_count <= 1:
            raise AuthError("Không thể bỏ quyền admin cuối cùng", 409)

    def set_role(self, username: str, role: str) -> dict:
        role = self._validate_role(role)
        with self._connect() as connection:
            self._ensure_not_last_admin(connection, username)
            cursor = connection.execute(
                "UPDATE users SET role=? WHERE username=?", (role, username)
            )
            if cursor.rowcount == 0:
                raise AuthError("Không tìm thấy người dùng", 404)
            if role != "admin":
                connection.execute("DELETE FROM tokens WHERE username=?", (username,))
            self._audit(connection, None, "user.set_role", f"{username}={role}")
        return self.get_user(username)

    def set_active(self, username: str, active: bool) -> dict:
        with self._connect() as connection:
            if not active:
                self._ensure_not_last_admin(connection, username)
            cursor = connection.execute(
                "UPDATE users SET active=? WHERE username=?",
                (1 if active else 0, username),
            )
            if cursor.rowcount == 0:
                raise AuthError("Không tìm thấy người dùng", 404)
            if not active:
                connection.execute("DELETE FROM tokens WHERE username=?", (username,))
            self._audit(connection, None, "user.set_active", f"{username}={active}")
        return self.get_user(username)

    def change_password(self, username: str, new_password: str) -> None:
        new_password = self._validate_password(new_password)
        salt = os.urandom(16)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_hash=?, salt=? WHERE username=?",
                (_hash_password(new_password, salt), salt.hex(), username),
            )
            if cursor.rowcount == 0:
                raise AuthError("Không tìm thấy người dùng", 404)
            connection.execute("DELETE FROM tokens WHERE username=?", (username,))
            self._audit(connection, None, "user.change_password", username)

    # ------------------------------------------------------------------ #
    # Đăng nhập / token
    # ------------------------------------------------------------------ #
    def authenticate(self, username: str, password: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT username, password_hash, salt, role, active "
                "FROM users WHERE username=?",
                (str(username).strip(),),
            ).fetchone()
        if row is None or not row["active"]:
            raise AuthError("Sai tên đăng nhập hoặc mật khẩu", 401)
        expected = row["password_hash"]
        actual = _hash_password(str(password), bytes.fromhex(row["salt"]))
        if not hmac.compare_digest(actual, expected):
            raise AuthError("Sai tên đăng nhập hoặc mật khẩu", 401)
        return {
            "username": row["username"],
            "role": row["role"],
        }

    def login(self, username: str, password: str) -> dict:
        user = self.authenticate(username, password)
        token = secrets.token_urlsafe(32)
        now = time.time()
        expires_at = now + self.token_ttl
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tokens(token_hash, username, created_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (_hash_token(token), user["username"], now, expires_at),
            )
            connection.execute("DELETE FROM tokens WHERE expires_at < ?", (now,))
            self._audit(connection, user["username"], "auth.login", None)
        return {
            "token": token,
            "username": user["username"],
            "role": user["role"],
            "expires_at": expires_at,
        }

    def validate_token(self, token: str) -> dict:
        if not token:
            raise AuthError("Thiếu token", 401)
        digest = _hash_token(token)
        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT t.username, t.expires_at, u.role, u.active "
                "FROM tokens t JOIN users u ON u.username=t.username "
                "WHERE t.token_hash=?",
                (digest,),
            ).fetchone()
            if row is None:
                raise AuthError("Token không hợp lệ", 401)
            if row["expires_at"] < now or not row["active"]:
                connection.execute(
                    "DELETE FROM tokens WHERE token_hash=?", (digest,)
                )
                raise AuthError("Token đã hết hạn", 401)
        return {"username": row["username"], "role": row["role"]}

    def revoke_token(self, token: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM tokens WHERE token_hash=?", (_hash_token(token),)
            )

    # ------------------------------------------------------------------ #
    # Phân quyền / audit
    # ------------------------------------------------------------------ #
    @staticmethod
    def has_permission(user: dict, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(user.get("role", ""), set())

    def require(self, user: dict, permission: str) -> None:
        if not self.has_permission(user, permission):
            raise AuthError("Không có quyền thực hiện thao tác này", 403)

    def list_audit(self, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp, username, action, detail FROM audit "
                "ORDER BY id DESC LIMIT ?",
                (max(1, limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def _audit(self, connection, username, action, detail) -> None:
        connection.execute(
            "INSERT INTO audit(timestamp, username, action, detail) VALUES (?, ?, ?, ?)",
            (_now_iso(), username, action, detail),
        )

    def ensure_default_admin(self, password: str | None = None) -> tuple[str, str] | None:
        """Tạo admin đầu tiên nếu database chưa có user nào.

        Mật khẩu lấy từ tham số hoặc biến môi trường ``TFTP_ADMIN_PASSWORD``.
        Không có mật khẩu mặc định; nếu thiếu cả hai sẽ báo lỗi. Trả về
        ``(username, password)`` khi vừa tạo, ngược lại trả ``None``. Caller
        nên cảnh báo người dùng đổi mật khẩu.
        """
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count:
            return None
        username = "admin"
        secret = password or os.environ.get("TFTP_ADMIN_PASSWORD")
        if not secret:
            raise ValueError(
                "TFTP_ADMIN_PASSWORD is required to create the initial admin account"
            )
        self.create_user(username, secret, "admin")
        return username, secret


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "ROLE_PERMISSIONS",
    "AuthError",
    "AuthManager",
]
