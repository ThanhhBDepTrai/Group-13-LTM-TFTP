"""Đăng nhập vào Server đã chọn trong registry."""

from __future__ import annotations

from client.server_connection import ServerConnection
from client.server_registry import ServerRegistry
from client.session_manager import Session, SessionManager
from enterprise.errors import EnterpriseError


class LoginManager:
    def __init__(self, registry: ServerRegistry, sessions: SessionManager):
        self.registry, self.sessions = registry, sessions

    def login(self, server_id: str, username: str, password: str) -> Session:
        conn = ServerConnection(self.registry.get(server_id))
        if not conn.verify_identity():
            raise EnterpriseError("Server ID không khớp: có thể đã kết nối nhầm Server", 409)
        r = conn.request("POST", "/api/ent/login", {"username": username, "password": password})
        session = Session(server_id, r["username"], r["role"], r["token"], r["expires_at"],
                          set(r.get("permissions", [])))
        self.sessions.start(session)
        return session
