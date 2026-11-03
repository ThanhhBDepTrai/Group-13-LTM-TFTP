"""Mã tham gia Server: tạo, xác minh, thu hồi. Chỉ lưu hash của mã."""

from __future__ import annotations

import hashlib
import secrets
import time

from database.database import Database
from enterprise.errors import EnterpriseError

_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # bỏ ký tự dễ nhầm


def _hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


class JoinCodeManager:
    def __init__(self, db: Database):
        self.db = db

    def create(self, created_by: str, *, role="EMPLOYEE", ttl=24 * 3600, max_uses=1) -> str:
        if role == "SUPER_ADMIN":
            raise EnterpriseError("Join code không được cấp vai trò SUPER_ADMIN", 403)
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        code = f"{raw[:5]}-{raw[5:]}"
        now = time.time()
        with self.db.connect() as c:
            if c.execute("SELECT 1 FROM roles WHERE name=?", (role,)).fetchone() is None:
                raise EnterpriseError("Vai trò không tồn tại")
            c.execute(
                "INSERT INTO join_codes(code_hash, role, created_by, created_at, expires_at, "
                "max_uses) VALUES (?,?,?,?,?,?)",
                (_hash(code), role, created_by, now, now + ttl, max(1, int(max_uses))),
            )
        return code

    def consume(self, code: str) -> str:
        """Dùng một lượt mã; trả về role được cấp."""
        digest, now = _hash(code), time.time()
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM join_codes WHERE code_hash=?", (digest,)).fetchone()
            if (row is None or row["revoked"] or row["expires_at"] < now
                    or row["uses"] >= row["max_uses"]):
                raise EnterpriseError("Mã tham gia không hợp lệ hoặc đã hết hạn", 403)
            c.execute("UPDATE join_codes SET uses=uses+1 WHERE code_hash=?", (digest,))
            return row["role"]

    def verify(self, code: str) -> bool:
        try:
            digest, now = _hash(code), time.time()
            with self.db.connect() as c:
                row = c.execute("SELECT * FROM join_codes WHERE code_hash=?",
                                (digest,)).fetchone()
            return bool(row and not row["revoked"] and row["expires_at"] >= now
                        and row["uses"] < row["max_uses"])
        except Exception:
            return False

    def revoke(self, code: str) -> None:
        with self.db.connect() as c:
            cur = c.execute("UPDATE join_codes SET revoked=1 WHERE code_hash=?", (_hash(code),))
            if cur.rowcount == 0:
                raise EnterpriseError("Không tìm thấy mã", 404)
