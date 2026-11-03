"""Client gọi Admin API từ GUI/CLI (không truy cập database trực tiếp)."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class AdminAPIError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


class AdminClient:
    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    # ------------------------------------------------------------------ #
    # HTTP helper
    # ------------------------------------------------------------------ #
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict | None = None,
        data: bytes | None = None,
        raw: bool = False,
        timeout: float | None = None,
    ):
        url = self.base_url + path
        if params:
            clean = {key: value for key, value in params.items() if value is not None}
            url += "?" + urllib.parse.urlencode(clean)

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        body = data
        if json_body is not None:
            body = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        elif data is not None:
            headers.setdefault("Content-Type", "application/octet-stream")

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                payload = response.read()
                if raw:
                    return payload, dict(response.headers)
                if not payload:
                    return {}
                return json.loads(payload.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            message = f"HTTP {exc.code}"
            try:
                parsed = json.loads(exc.read().decode("utf-8"))
                message = parsed.get("error", message)
            except (ValueError, OSError):
                pass
            raise AdminAPIError(message, exc.code) from exc
        except urllib.error.URLError as exc:
            raise AdminAPIError(f"Không kết nối được Admin API: {exc.reason}", 0) from exc
        except (TimeoutError, OSError) as exc:
            raise AdminAPIError(f"Lỗi kết nối Admin API: {exc}", 0) from exc

    # ------------------------------------------------------------------ #
    # Auth
    # ------------------------------------------------------------------ #
    def login(self, username: str, password: str) -> dict:
        result = self._request(
            "POST",
            "/api/auth/login",
            json_body={"username": username, "password": password},
        )
        self.token = result.get("token")
        return result

    def logout(self) -> None:
        if self.token:
            self._request("POST", "/api/auth/logout")
        self.token = None

    def me(self) -> dict:
        return self._request("GET", "/api/auth/me")

    def health(self) -> dict:
        return self._request("GET", "/api/health")

    # ------------------------------------------------------------------ #
    # Files
    # ------------------------------------------------------------------ #
    def list_dir(self, path: str = "") -> dict:
        return self._request("GET", "/api/files", params={"path": path})

    def search(self, query: str, limit: int = 200) -> dict:
        return self._request(
            "GET", "/api/files/search", params={"q": query, "limit": limit}
        )

    def metadata(self, path: str, *, include_hash: bool = False) -> dict:
        return self._request(
            "GET",
            "/api/files/metadata",
            params={"path": path, "hash": "1" if include_hash else "0"},
        )

    def hash(self, path: str) -> str:
        return self._request("GET", "/api/files/hash", params={"path": path})["sha256"]

    def download(self, path: str) -> bytes:
        body, _headers = self.download_with_headers(path)
        return body

    def download_with_headers(self, path: str) -> tuple[bytes, dict]:
        return self._request(
            "GET", "/api/files/download", params={"path": path}, raw=True
        )

    def upload(self, path: str, data: bytes) -> dict:
        return self._request(
            "POST", "/api/files/upload", params={"path": path}, data=data
        )

    def verify(self, path: str, sha256: str) -> dict:
        return self._request(
            "POST", "/api/files/verify", json_body={"path": path, "sha256": sha256}
        )

    def delete(self, path: str, *, recursive: bool = False) -> dict:
        return self._request(
            "DELETE",
            "/api/files",
            params={"path": path, "recursive": "1" if recursive else "0"},
        )

    def rename(self, path: str, new_path: str, *, overwrite: bool = False) -> dict:
        return self._request(
            "POST",
            "/api/files/rename",
            json_body={"path": path, "new_path": new_path, "overwrite": overwrite},
        )

    def mkdir(self, path: str, *, exist_ok: bool = False) -> dict:
        return self._request(
            "POST",
            "/api/files/mkdir",
            json_body={"path": path, "exist_ok": exist_ok},
        )

    # ------------------------------------------------------------------ #
    # Users / dashboard / packets / PXE
    # ------------------------------------------------------------------ #
    def list_users(self) -> list[dict]:
        return self._request("GET", "/api/users")["users"]

    def create_user(self, username: str, password: str, role: str = "user") -> dict:
        return self._request(
            "POST",
            "/api/users",
            json_body={"username": username, "password": password, "role": role},
        )

    def delete_user(self, username: str) -> dict:
        return self._request("DELETE", "/api/users", params={"username": username})

    def set_user_role(self, username: str, role: str) -> dict:
        return self._request(
            "POST", "/api/users/role", json_body={"username": username, "role": role}
        )["user"]

    def set_user_active(self, username: str, active: bool) -> dict:
        return self._request(
            "POST",
            "/api/users/active",
            json_body={"username": username, "active": active},
        )["user"]

    def change_password(
        self,
        new_password: str,
        *,
        username: str | None = None,
        current_password: str | None = None,
    ) -> dict:
        body = {"new_password": new_password}
        if username is not None:
            body["username"] = username
        if current_password is not None:
            body["current_password"] = current_password
        return self._request("POST", "/api/users/password", json_body=body)

    def audit_log(self, limit: int = 100) -> list[dict]:
        return self._request("GET", "/api/audit", params={"limit": limit})["events"]

    def dashboard(self, limit: int = 50) -> dict:
        return self._request("GET", "/api/dashboard", params={"limit": limit})

    def packets(self, limit: int = 200, opcode: str | None = None) -> dict:
        return self._request(
            "GET", "/api/packets", params={"limit": limit, "opcode": opcode}
        )

    def pxe_overview(self) -> dict:
        return self._request("GET", "/api/pxe/profiles")

    def pxe_profiles(self) -> list[dict]:
        return self.pxe_overview()["profiles"]

    def pxe_issues(self) -> list[dict]:
        return self.pxe_overview().get("issues", [])

    def upsert_pxe_profile(
        self, profile: dict, *, require_files: bool = False
    ) -> dict:
        return self._request(
            "POST",
            "/api/pxe/profiles",
            params={"require_files": "1" if require_files else "0"},
            json_body=profile,
        )["profile"]

    def delete_pxe_profile(self, name: str) -> dict:
        return self._request("DELETE", "/api/pxe/profiles", params={"name": name})

    def select_pxe_profile(self, arch: str = "bios", name: str | None = None) -> dict:
        return self._request(
            "GET", "/api/pxe/select", params={"arch": arch, "name": name}
        )["profile"]


__all__ = ["AdminAPIError", "AdminClient"]
