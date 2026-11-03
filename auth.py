"""Xác thực và phân quyền Admin / User / Guest.

Chỉ dùng thư viện chuẩn:
* mật khẩu: PBKDF2-HMAC-SHA256 với salt ngẫu nhiên, so sánh hằng thời gian;
* token: ``base64url(payload).base64url(HMAC-SHA256)`` có hạn dùng (``exp``);
* chống dò mật khẩu: khóa tạm tên đăng nhập sau nhiều lần sai liên tiếp.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from dataclasses import dataclass

from database import Database, DatabaseError

__all__ = [
    "ADMIN", "GUEST", "USER", "ROLE_PERMISSIONS", "AuthError", "AuthService",
    "PermissionDenied", "User",
]

ADMIN, USER, GUEST = "admin", "user", "guest"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ADMIN: frozenset({
        "files:read", "files:write", "files:delete",
        "transfers:read", "metrics:read",
        "pxe:read", "pxe:write", "users:manage",
    }),
    USER: frozenset({"files:read", "files:write", "transfers:read", "pxe:read"}),
    GUEST: frozenset({"files:read"}),
}

_USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
_MIN_PASSWORD = 8


class AuthError(Exception):
    """Sai thông tin đăng nhập, token không hợp lệ/hết hạn, hoặc dữ liệu đầu vào sai."""


class PermissionDenied(Exception):
    """Đã đăng nhập nhưng vai trò không đủ quyền."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    role: str
    disabled: bool = False

    def can(self, permission: str) -> bool:
        return permission in ROLE_PERMISSIONS.get(self.role, frozenset())


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class AuthService:
    def __init__(
        self,
        db: Database,
        secret: str | bytes,
        *,
        token_ttl: int = 3600,
        iterations: int = 200_000,
        max_failures: int = 5,
        lockout_seconds: int = 60,
    ):
        key = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(key) < 16:
            raise ValueError("Secret must be at least 16 bytes")
        self.db = db
        self._key = key
        self.token_ttl = token_ttl
        self.iterations = iterations
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self._failures: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()
        self._dummy_hash = self.hash_password("dummy-password-for-timing")

    # ------------------------------------------------------------------ #
    # Mật khẩu
    # ------------------------------------------------------------------ #

    def hash_password(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, self.iterations)
        return f"pbkdf2_sha256${self.iterations}${_b64e(salt)}${_b64e(digest)}"

    @staticmethod
    def verify_password(password: str, stored: str) -> bool:
        try:
            scheme, iterations, salt, digest = stored.split("$")
            if scheme != "pbkdf2_sha256":
                return False
            candidate = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), _b64d(salt), int(iterations)
            )
            return hmac.compare_digest(candidate, _b64d(digest))
        except (ValueError, binascii.Error):
            return False

    # ------------------------------------------------------------------ #
    # Người dùng
    # ------------------------------------------------------------------ #

    @staticmethod
    def _to_user(row: dict) -> User:
        return User(row["id"], row["username"], row["role"], bool(row["disabled"]))

    def create_user(self, username: str, password: str, role: str = USER) -> User:
        if not _USERNAME.match(username):
            raise AuthError("Username must be 3-32 chars: letters, digits, '_', '.', '-'")
        if len(password) < _MIN_PASSWORD:
            raise AuthError(f"Password must be at least {_MIN_PASSWORD} characters")
        if role not in ROLE_PERMISSIONS:
            raise AuthError(f"Unknown role: {role}")
        try:
            user_id = self.db.add_user(username, self.hash_password(password), role)
        except DatabaseError as exc:
            raise AuthError(str(exc)) from exc
        return User(user_id, username, role)

    def ensure_admin(self, username: str = "admin", password: str | None = None) -> tuple[User, str | None]:
        """Tạo admin đầu tiên nếu chưa có người dùng nào.

        Trả về ``(user, mật khẩu được sinh ra hoặc None)``. Nếu đã có sẵn người
        dùng thì trả về admin đầu tiên tìm thấy và ``None``.
        """
        if self.db.count_users() == 0:
            generated = password is None
            secret = password or secrets.token_urlsafe(12)
            return self.create_user(username, secret, ADMIN), (secret if generated else None)
        for row in self.db.list_users():
            if row["role"] == ADMIN:
                return self._to_user(row), None
        raise AuthError("Database has users but no admin")

    def get_user(self, user_id: int) -> User | None:
        row = self.db.get_user_by_id(user_id)
        return self._to_user(row) if row else None

    def update_user(
        self,
        user_id: int,
        *,
        role: str | None = None,
        disabled: bool | None = None,
        password: str | None = None,
    ) -> User:
        current = self.db.get_user_by_id(user_id)
        if current is None:
            raise AuthError("User not found")
        changes: dict[str, object] = {}
        if role is not None:
            if role not in ROLE_PERMISSIONS:
                raise AuthError(f"Unknown role: {role}")
            changes["role"] = role
        if disabled is not None:
            changes["disabled"] = int(disabled)
        if password is not None:
            if len(password) < _MIN_PASSWORD:
                raise AuthError(f"Password must be at least {_MIN_PASSWORD} characters")
            changes["password_hash"] = self.hash_password(password)
        losing_admin = current["role"] == ADMIN and not current["disabled"] and (
            changes.get("role", ADMIN) != ADMIN or changes.get("disabled", 0)
        )
        if losing_admin and self.db.count_active_admins() <= 1:
            raise AuthError("Cannot remove the last active admin")
        self.db.update_user(user_id, **changes)
        return self._to_user(self.db.get_user_by_id(user_id))

    def delete_user(self, user_id: int) -> None:
        current = self.db.get_user_by_id(user_id)
        if current is None:
            raise AuthError("User not found")
        if current["role"] == ADMIN and not current["disabled"] and self.db.count_active_admins() <= 1:
            raise AuthError("Cannot remove the last active admin")
        self.db.delete_user(user_id)

    # ------------------------------------------------------------------ #
    # Đăng nhập / token
    # ------------------------------------------------------------------ #

    def _locked(self, username: str) -> bool:
        key = username.lower()
        with self._lock:
            count, since = self._failures.get(key, (0, 0.0))
            if count >= self.max_failures:
                if time.monotonic() - since < self.lockout_seconds:
                    return True
                del self._failures[key]
            return False

    def _record_failure(self, username: str) -> None:
        key = username.lower()
        with self._lock:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, time.monotonic())

    def authenticate(self, username: str, password: str) -> User:
        if self._locked(username):
            raise AuthError("Too many failed attempts, try again later")
        row = self.db.get_user(username)
        stored = row["password_hash"] if row else self._dummy_hash  # giữ thời gian xử lý đều nhau
        valid = self.verify_password(password, stored)
        if not row or not valid or row["disabled"]:
            self._record_failure(username)
            raise AuthError("Invalid username or password")
        with self._lock:
            self._failures.pop(username.lower(), None)
        return self._to_user(row)

    def issue_token(self, user: User, *, now: float | None = None) -> str:
        issued = time.time() if now is None else now
        payload = _b64e(json.dumps(
            {"uid": user.id, "sub": user.username, "exp": int(issued + self.token_ttl)},
            separators=(",", ":"),
        ).encode("utf-8"))
        return f"{payload}.{_b64e(self._sign(payload))}"

    def login(self, username: str, password: str) -> tuple[str, User]:
        user = self.authenticate(username, password)
        return self.issue_token(user), user

    def user_from_token(self, token: str, *, now: float | None = None) -> User:
        try:
            payload, signature = token.split(".")
            if not hmac.compare_digest(_b64d(signature), self._sign(payload)):
                raise AuthError("Invalid token")
            data = json.loads(_b64d(payload))
            expires = float(data["exp"])
            user_id = int(data["uid"])
        except (ValueError, KeyError, TypeError, binascii.Error) as exc:
            raise AuthError("Invalid token") from exc
        if (time.time() if now is None else now) >= expires:
            raise AuthError("Token expired")
        user = self.get_user(user_id)  # đọc lại DB: đổi vai trò/khóa tài khoản có hiệu lực ngay
        if user is None or user.disabled:
            raise AuthError("Account is not available")
        return user

    def _sign(self, payload: str) -> bytes:
        return hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest()

    # ------------------------------------------------------------------ #
    # Phân quyền
    # ------------------------------------------------------------------ #

    @staticmethod
    def require(user: User, permission: str) -> None:
        if not user.can(permission):
            raise PermissionDenied(f"Role '{user.role}' lacks permission '{permission}'")
