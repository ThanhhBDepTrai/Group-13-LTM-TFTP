"""Danh sách Server đã tham gia, lưu trên máy Client (JSON). Không lưu mật khẩu."""

from __future__ import annotations

import json
import os
from pathlib import Path

from enterprise.errors import EnterpriseError
from enterprise.server.server_identity import is_valid_server_id
from enterprise.server.server_validator import validate_host, validate_port

DEFAULT_PATH = Path.home() / ".efm" / "servers.json"


class ServerRegistry:
    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, server_id, name, host, api_port, tftp_port) -> dict:
        if not is_valid_server_id(server_id):
            raise EnterpriseError("Server ID không hợp lệ")
        entry = {"server_id": server_id, "name": str(name).strip() or server_id,
                 "host": validate_host(host), "api_port": validate_port(api_port),
                 "tftp_port": validate_port(tftp_port)}
        data = self._load()
        data[server_id] = entry
        self._save(data)
        return entry

    def list(self) -> list[dict]:
        return sorted(self._load().values(), key=lambda e: e["name"].lower())

    def get(self, server_id: str) -> dict:
        entry = self._load().get(server_id)
        if entry is None:
            raise EnterpriseError("Server chưa có trong danh sách", 404)
        return entry

    def remove(self, server_id: str) -> None:
        data = self._load()
        if data.pop(server_id, None) is None:
            raise EnterpriseError("Server chưa có trong danh sách", 404)
        self._save(data)
