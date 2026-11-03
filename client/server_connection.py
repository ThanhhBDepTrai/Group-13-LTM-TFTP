"""Kết nối HTTP tới Admin API của một Server (stdlib, không phụ thuộc ngoài)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from client.server_identity import matches
from enterprise.errors import EnterpriseError


class ServerConnection:
    def __init__(self, entry: dict, *, timeout: float = 5.0):
        self.entry, self.timeout = entry, timeout
        self.base_url = f"http://{entry['host']}:{entry['api_port']}"

    def request(self, method: str, path: str, body: dict | None = None,
                token: str | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base_url + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                msg = json.loads(exc.read()).get("error", exc.reason)
            except Exception:
                msg = str(exc.reason)
            raise EnterpriseError(msg, exc.code) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise EnterpriseError(f"Không kết nối được Server: {exc}", 503) from exc

    def verify_identity(self) -> bool:
        """So khớp Server ID mà Server báo với ID đã lưu trong registry."""
        info = self.request("GET", "/api/health")
        return matches(self.entry["server_id"], info.get("server_id", ""))
