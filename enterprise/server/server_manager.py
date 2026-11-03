"""Tạo và quản lý cấu hình Server (Giai đoạn 1)."""

from __future__ import annotations

from database.database import Database, now_iso
from database.seed_data import seed
from enterprise.errors import EnterpriseError
from enterprise.server.server_config import ServerConfig
from enterprise.server.server_identity import generate_server_id
from enterprise.server.server_validator import validate_server_config
from enterprise.workspace.workspace_manager import WorkspaceManager


class ServerManager:
    def __init__(self, db: Database):
        self.db = db
        seed(db)

    def get(self) -> ServerConfig | None:
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM servers LIMIT 1").fetchone()
        return ServerConfig(**dict(r)) if r else None

    def create(self, name, host, api_port, tftp_port, *, workspace_name=None,
               check_bind=False) -> ServerConfig:
        if self.get() is not None:
            raise EnterpriseError("Database này đã có Server", 409)
        cfg = validate_server_config(host, api_port, tftp_port, check_bind=check_bind)
        name = str(name).strip()
        if not name:
            raise EnterpriseError("Tên Server không được trống")
        server_id, created = generate_server_id(), now_iso()
        with self.db.connect() as c:
            c.execute("INSERT INTO servers(server_id, name, host, api_port, tftp_port, "
                      "created_at) VALUES (?,?,?,?,?,?)",
                      (server_id, name, cfg["host"], cfg["api_port"], cfg["tftp_port"], created))
        WorkspaceManager(self.db).create(workspace_name or name)
        return self.get()

    def update_ports(self, api_port, tftp_port) -> ServerConfig:
        cur = self.get()
        if cur is None:
            raise EnterpriseError("Server chưa được tạo", 404)
        cfg = validate_server_config(cur.host, api_port, tftp_port)
        with self.db.connect() as c:
            c.execute("UPDATE servers SET api_port=?, tftp_port=? WHERE server_id=?",
                      (cfg["api_port"], cfg["tftp_port"], cur.server_id))
        return self.get()
