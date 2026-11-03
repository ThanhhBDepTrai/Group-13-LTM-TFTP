"""Phiên đăng nhập hiện tại trên Client (token chỉ giữ trong bộ nhớ)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Session:
    server_id: str
    username: str
    role: str
    token: str
    expires_at: float
    permissions: set[str] = field(default_factory=set)

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def can(self, permission: str) -> bool:
        return not self.expired and permission in self.permissions


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self.current_server: str | None = None

    def start(self, session: Session) -> None:
        self._sessions[session.server_id] = session
        self.current_server = session.server_id

    def current(self) -> Session | None:
        s = self._sessions.get(self.current_server or "")
        return s if s and not s.expired else None

    def end(self, server_id: str | None = None) -> None:
        sid = server_id or self.current_server
        self._sessions.pop(sid or "", None)
        if sid == self.current_server:
            self.current_server = None
