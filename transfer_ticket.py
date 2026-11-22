"""Vé truyền tệp 1 lần (Pre-authorized Transfer Ticket).

Lưu cặp (client_ip, filename, mode) -> expires_at với TTL 30 giây. Vé bị xóa
ngay sau khi dùng (hoặc hết hạn) để đảm bảo chỉ truyền 1 lần.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TransferTicketManager:
    def __init__(self, ttl: float = 30.0):
        self.ttl = float(ttl)
        self._lock = threading.Lock()
        self._tickets: dict[tuple, float] = {}

    @staticmethod
    def _key(client_ip: str, filename: str, mode: str) -> tuple:
        return (str(client_ip), str(filename), str(mode).strip().lower())

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        mode = str(mode).strip().lower()
        if mode in ("read", "rrq", "download"):
            return "read"
        if mode in ("write", "wrq", "upload"):
            return "write"
        return mode

    def issue(self, client_ip: str, filename: str, mode: str) -> dict[str, Any]:
        key = self._key(client_ip, filename, self._normalize_mode(mode))
        expires_at = time.time() + self.ttl
        with self._lock:
            self._tickets[key] = expires_at
        return {
            "client_ip": str(client_ip),
            "filename": str(filename),
            "mode": self._normalize_mode(mode),
            "expires_at": expires_at,
        }

    # aliases
    def create(self, *a, **k):
        return self.issue(*a, **k)

    def authorize(self, *a, **k):
        return self.issue(*a, **k)

    def add(self, *a, **k):
        return self.issue(*a, **k)

    def _is_valid(self, key: tuple, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        with self._lock:
            expires_at = self._tickets.get(key)
        if expires_at is None:
            return False
        if expires_at < now:
            with self._lock:
                self._tickets.pop(key, None)
            return False
        return True

    def has_valid(self, client_ip: str, filename: str, mode: str, now: float | None = None) -> bool:
        key = self._key(client_ip, filename, self._normalize_mode(mode))
        return self._is_valid(key, now)

    # aliases
    def valid(self, *a, **k):
        return self.has_valid(*a, **k)

    def is_valid(self, *a, **k):
        return self.has_valid(*a, **k)

    def check(self, *a, **k):
        return self.has_valid(*a, **k)

    def consume(self, client_ip: str, filename: str, mode: str, now: float | None = None) -> bool:
        key = self._key(client_ip, filename, self._normalize_mode(mode))
        now = time.time() if now is None else now
        with self._lock:
            expires_at = self._tickets.get(key)
            if expires_at is None or expires_at < now:
                self._tickets.pop(key, None)
                return False
            del self._tickets[key]
            return True

    # aliases
    def use(self, *a, **k):
        return self.consume(*a, **k)

    def take(self, *a, **k):
        return self.consume(*a, **k)

    def purge_expired(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        removed = 0
        with self._lock:
            expired = [k for k, v in self._tickets.items() if v < now]
            for k in expired:
                del self._tickets[k]
                removed += 1
        return removed


__all__ = ["TransferTicketManager"]
