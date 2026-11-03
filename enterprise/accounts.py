"""Tài khoản + phiên đăng nhập của Enterprise File Manager (Giai đoạn 2).

Dùng cùng thuật toán băm với auth.py (PBKDF2-HMAC-SHA256, token chỉ lưu SHA-256).
"""

from __future__ import annotations

import hmac
import os
import re
import secrets
import time

from auth import _hash_password, _hash_token
from database.database import Database, now_iso
from enterprise.errors import EnterpriseError

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
MIN_PASSWORD_LENGTH = 8
DEFAULT_SESSION_TTL = 8 * 3600
MAX_FAILED_LOGINS = 5
LOCK_SECONDS = 300


class AccountManager:
    def __init__(self, db: Database, *, session_ttl: int = DEFAULT_SESSION_TTL):
        self.db, self.session_ttl = db, session_ttl
        self._failures: dict[str, tuple[int, float]] = {}

    # --- tài khoản ---------------------------------------------------------
    def create_user(self, username, password, role="EMPLOYEE") -> dict:
        username = str(username).strip()
        if not _USERNAME_RE.fullmatch(username):
            raise EnterpriseError("Username chỉ gồm chữ, số, _ . - (1-64 ký tự)")
        if len(str(password)) < MIN_PASSWORD_LENGTH:
            raise EnterpriseError(f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự")
        salt = os.urandom(16)
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM roles WHERE name=?", (role,)).fetchone() is None:
                raise EnterpriseError("Vai trò không tồn tại")
            try:
                c.execute("INSERT INTO ent_users(username, password_hash, salt, role, created_at)"
                          " VALUES (?,?,?,?,?)",
                          (username, _hash_password(str(password), salt), salt.hex(), role,
                           now_iso()))
            except Exception as exc:
                raise EnterpriseError("Username đã tồn tại", 409) from exc
        return self.get_user(username)

    def create_super_admin(self, username, password) -> dict:
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM ent_users WHERE role='SUPER_ADMIN'").fetchone():
                raise EnterpriseError("SUPER_ADMIN đã tồn tại", 409)
        return self.create_user(username, password, "SUPER_ADMIN")

    def get_user(self, username: str) -> dict:
        with self.db.connect() as c:
            r = c.execute(
                "SELECT u.username, u.role, u.active, u.created_at, d.name AS department "
                "FROM ent_users u LEFT JOIN employees e ON e.username=u.username "
                "LEFT JOIN departments d ON d.id=e.department_id WHERE u.username=?",
                (username,)).fetchone()
        if r is None:
            raise EnterpriseError("Không tìm thấy người dùng", 404)
        return dict(r)

    def list_users(self) -> list[dict]:
        with self.db.connect() as c:
            names = [r["username"] for r in c.execute("SELECT username FROM ent_users "
                                                      "ORDER BY username")]
        return [self.get_user(n) for n in names]

    def set_active(self, username: str, active: bool) -> None:
        with self.db.connect() as c:
            row = c.execute("SELECT role FROM ent_users WHERE username=?", (username,)).fetchone()
            if row is None:
                raise EnterpriseError("Không tìm thấy người dùng", 404)
            if not active and row["role"] == "SUPER_ADMIN":
                n = c.execute("SELECT COUNT(*) FROM ent_users WHERE role='SUPER_ADMIN' "
                              "AND active=1").fetchone()[0]
                if n <= 1:
                    raise EnterpriseError("Không thể khóa SUPER_ADMIN cuối cùng", 409)
            c.execute("UPDATE ent_users SET active=? WHERE username=?", (int(active), username))
            if not active:
                c.execute("DELETE FROM sessions WHERE username=?", (username,))

    def change_password(self, username: str, new_password: str) -> None:
        if len(str(new_password)) < MIN_PASSWORD_LENGTH:
            raise EnterpriseError(f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự")
        salt = os.urandom(16)
        with self.db.connect() as c:
            cur = c.execute("UPDATE ent_users SET password_hash=?, salt=? WHERE username=?",
                            (_hash_password(str(new_password), salt), salt.hex(), username))
            if cur.rowcount == 0:
                raise EnterpriseError("Không tìm thấy người dùng", 404)
            c.execute("DELETE FROM sessions WHERE username=?", (username,))

    # --- đăng nhập / phiên -------------------------------------------------
    def login(self, username: str, password: str, client_ip: str | None = None) -> dict:
        username = str(username).strip()
        count, until = self._failures.get(username, (0, 0.0))
        if count >= MAX_FAILED_LOGINS and time.time() < until:
            raise EnterpriseError("Tài khoản tạm khóa do đăng nhập sai nhiều lần", 429)
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM ent_users WHERE username=?", (username,)).fetchone()
        ok = bool(row and row["active"] and hmac.compare_digest(
            _hash_password(str(password), bytes.fromhex(row["salt"])), row["password_hash"]))
        if not ok:
            self._failures[username] = (count + 1, time.time() + LOCK_SECONDS)
            raise EnterpriseError("Sai tên đăng nhập hoặc mật khẩu", 401)
        self._failures.pop(username, None)
        token, now = secrets.token_urlsafe(32), time.time()
        with self.db.connect() as c:
            c.execute("INSERT INTO sessions(token_hash, username, created_at, expires_at, "
                      "client_ip) VALUES (?,?,?,?,?)",
                      (_hash_token(token), username, now, now + self.session_ttl, client_ip))
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        user = self.get_user(username)
        return {"token": token, "expires_at": now + self.session_ttl, **user}

    def validate_token(self, token: str) -> dict:
        if not token:
            raise EnterpriseError("Thiếu token", 401)
        digest, now = _hash_token(token), time.time()
        with self.db.connect() as c:
            r = c.execute("SELECT s.username, s.expires_at, u.active FROM sessions s "
                          "JOIN ent_users u ON u.username=s.username WHERE s.token_hash=?",
                          (digest,)).fetchone()
            if r is None:
                raise EnterpriseError("Token không hợp lệ", 401)
            if r["expires_at"] < now or not r["active"]:
                c.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))
                raise EnterpriseError("Phiên đã hết hạn", 401)
        return self.get_user(r["username"])

    def logout(self, token: str) -> None:
        with self.db.connect() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash_token(token),))
