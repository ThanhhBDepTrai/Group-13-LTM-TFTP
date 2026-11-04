"""REST Admin API tách biệt hoàn toàn khỏi giao thức TFTP.

Mặc định bind ``127.0.0.1:6971``. Mọi endpoint quản trị (trừ health/login) cần
``Authorization: Bearer <token>``. API không bao giờ ghi token/mật khẩu vào log.

Chạy độc lập:
    python admin_api.py --root server_storage --db tftp_admin.db
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from auth import AuthError, AuthManager
from enterprise import api_routes as enterprise_routes
from enterprise.errors import EnterpriseError
from metrics import MetricsCollector
from packet_trace import PacketTracer
from pxe_config import PxeError, PxeManager
from storage_manager import StorageError, StorageManager
from tftp_config import (
    DEFAULT_ADMIN_DB,
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_MAX_ADMIN_BODY,
    DEFAULT_PXE_PROFILES,
    DEFAULT_ROOT,
    VERSION,
)
from tftp_packet import TFTPError

logger = logging.getLogger(__name__)


class AdminAPIError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class _AdminHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, app):
        self.app = app
        super().__init__(address, handler)


class _AdminRequestHandler(BaseHTTPRequestHandler):
    server_version = f"TFTPProAdmin/{VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------------ #
    # Hạ tầng HTTP
    # ------------------------------------------------------------------ #
    def log_message(self, fmt, *args):
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        filename: str | None = None,
        sha256: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if filename:
            self.send_header(
                "Content-Disposition",
                f"attachment; filename*=UTF-8''{quote(filename)}",
            )
        if sha256:
            self.send_header("X-File-SHA256", sha256)
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> bytes:
        app = self.server.app
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise AdminAPIError("Thiếu Content-Length", 411)
        try:
            length = int(length_header)
        except ValueError as exc:
            raise AdminAPIError("Content-Length không hợp lệ", 400) from exc
        if length < 0 or length > app.max_body:
            raise AdminAPIError("Body quá lớn", 413)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdminAPIError("JSON không hợp lệ", 400) from exc
        if not isinstance(data, dict):
            raise AdminAPIError("JSON phải là object", 400)
        return data

    @staticmethod
    def _query(path: str) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query)

    @staticmethod
    def _first(query: dict[str, list[str]], key: str, default: str = "") -> str:
        values = query.get(key)
        return values[0] if values else default

    def _authenticate(self, permission: str | None = None) -> tuple[dict, str]:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise AuthError("Thiếu hoặc sai Authorization header", 401)
        token = header[len("Bearer ") :].strip()
        app = self.server.app
        try:
            user = app.auth.validate_token(token)
            if permission:
                app.auth.require(user, permission)
            return user, token
        except AuthError:
            # Enterprise sessions are also accepted by the legacy TFTP Pro
            # endpoints so the existing tabs can use the new account system.
            if app.enterprise is None:
                raise
            user = app.enterprise.authenticate(token)
            if permission:
                app.enterprise.permissions.require(user, permission)
            return user, token

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_PUT(self):
        self._handle("PUT")

    def _handle(self, method: str) -> None:
        try:
            self._route(method)
        except AdminAPIError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except AuthError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except EnterpriseError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except StorageError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except PxeError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except TFTPError as exc:
            self._send_json(400, {"error": exc.message})
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.exception("Admin API error")
            try:
                self._send_json(500, {"error": "Lỗi máy chủ nội bộ"})
            except OSError:
                pass

    def _route(self, method: str) -> None:
        app = self.server.app
        path, query = self._query(self.path)

        if path == "/api/health" and method == "GET":
            payload = {"status": "ok", "version": VERSION}
            if app.enterprise is not None:
                cfg = app.enterprise.servers.get()
                if cfg is not None:
                    payload["server_id"] = cfg.server_id
            self._send_json(200, payload)
            return

        if app.enterprise is not None and enterprise_routes.handle(
            self, app.enterprise, method, path, query
        ):
            return

        if path == "/api/auth/login" and method == "POST":
            body = self._json_body()
            result = app.auth.login(
                str(body.get("username", "")), str(body.get("password", ""))
            )
            self._send_json(200, result)
            return

        if path == "/api/auth/logout" and method == "POST":
            user, token = self._authenticate()
            if app.enterprise is not None:
                try:
                    app.enterprise.authenticate(token)
                    app.enterprise.accounts.logout(token)
                except EnterpriseError:
                    app.auth.revoke_token(token)
            else:
                app.auth.revoke_token(token)
            self._send_json(200, {"status": "logged_out"})
            return

        if path == "/api/auth/me" and method == "GET":
            user, _token = self._authenticate()
            self._send_json(200, user)
            return

        # ---------------- files ----------------
        if path == "/api/files" and method == "GET":
            self._authenticate("files.list")
            relative = self._first(query, "path", "")
            entries = app.storage.list_dir(relative)
            self._send_json(
                200,
                {
                    "path": app.storage.clean_relative(relative),
                    "entries": [entry.to_dict() for entry in entries],
                },
            )
            return

        if path == "/api/files/search" and method == "GET":
            self._authenticate("files.list")
            needle = self._first(query, "q", "")
            entries = app.storage.search(needle, limit=int(self._first(query, "limit", "200")))
            self._send_json(200, {"query": needle, "entries": [e.to_dict() for e in entries]})
            return

        if path == "/api/files/metadata" and method == "GET":
            self._authenticate("files.read")
            relative = self._first(query, "path")
            include_hash = self._first(query, "hash", "0") not in ("0", "false", "")
            entry = app.storage.metadata(relative, include_hash=include_hash)
            self._send_json(200, {"entry": entry.to_dict()})
            return

        if path == "/api/files/hash" and method == "GET":
            self._authenticate("files.read")
            relative = self._first(query, "path")
            self._send_json(
                200,
                {
                    "path": app.storage.clean_relative(relative),
                    "sha256": app.storage.sha256(relative),
                },
            )
            return

        if path == "/api/files/download" and method == "GET":
            self._authenticate("files.download")
            relative = self._first(query, "path")
            entry = app.storage.metadata(relative, include_hash=True)
            body = app.storage.read_bytes(relative)
            self._send_bytes(
                200,
                body,
                filename=entry.name,
                sha256=entry.sha256,
            )
            return

        if path == "/api/files/upload" and method == "POST":
            self._authenticate("files.upload")
            relative = self._first(query, "path")
            body = self._read_body()
            entry = app.storage.write_bytes(relative, body, overwrite=True)
            self._send_json(201, {"entry": entry.to_dict(), "sha256": entry.sha256})
            return

        if path == "/api/files/verify" and method == "POST":
            self._authenticate("files.read")
            body = self._json_body()
            result = app.storage.verify(
                str(body.get("path", "")), str(body.get("sha256", ""))
            )
            self._send_json(200, result)
            return

        if path == "/api/files/rename" and method == "POST":
            self._authenticate("files.rename")
            body = self._json_body()
            entry = app.storage.rename(
                str(body.get("path", "")),
                str(body.get("new_path", "")),
                overwrite=bool(body.get("overwrite", False)),
            )
            self._send_json(200, {"entry": entry.to_dict()})
            return

        if path == "/api/files/mkdir" and method == "POST":
            self._authenticate("files.mkdir")
            body = self._json_body()
            entry = app.storage.mkdir(
                str(body.get("path", "")),
                parents=True,
                exist_ok=bool(body.get("exist_ok", False)),
            )
            self._send_json(201, {"entry": entry.to_dict()})
            return

        if path == "/api/files" and method == "DELETE":
            self._authenticate("files.delete")
            relative = self._first(query, "path")
            recursive = self._first(query, "recursive", "0") in ("1", "true", "yes")
            app.storage.delete(relative, recursive=recursive)
            self._send_json(200, {"status": "deleted", "path": relative})
            return

        # ---------------- users ----------------
        if path == "/api/users" and method == "GET":
            self._authenticate("users.manage")
            self._send_json(200, {"users": app.auth.list_users()})
            return

        if path == "/api/users" and method == "POST":
            self._authenticate("users.manage")
            body = self._json_body()
            user = app.auth.create_user(
                str(body.get("username", "")),
                str(body.get("password", "")),
                str(body.get("role", "user")),
            )
            self._send_json(201, {"user": user})
            return

        if path == "/api/users" and method == "DELETE":
            self._authenticate("users.manage")
            app.auth.delete_user(self._first(query, "username"))
            self._send_json(200, {"status": "deleted"})
            return

        if path == "/api/users/role" and method == "POST":
            self._authenticate("users.manage")
            body = self._json_body()
            user = app.auth.set_role(
                str(body.get("username", "")), str(body.get("role", ""))
            )
            self._send_json(200, {"user": user})
            return

        if path == "/api/users/active" and method == "POST":
            self._authenticate("users.manage")
            body = self._json_body()
            user = app.auth.set_active(
                str(body.get("username", "")), bool(body.get("active", True))
            )
            self._send_json(200, {"user": user})
            return

        if path == "/api/users/password" and method == "POST":
            caller, _token = self._authenticate()
            body = self._json_body()
            target = str(body.get("username") or caller["username"])
            new_password = str(body.get("new_password", ""))
            if caller["role"] != "admin":
                if target != caller["username"]:
                    raise AuthError("Chỉ được đổi mật khẩu của chính mình", 403)
                app.auth.authenticate(
                    caller["username"], str(body.get("current_password", ""))
                )
            app.auth.change_password(target, new_password)
            self._send_json(
                200,
                {
                    "status": "password_changed",
                    "username": target,
                    "relogin_required": True,
                },
            )
            return

        if path == "/api/audit" and method == "GET":
            self._authenticate("users.manage")
            limit = int(self._first(query, "limit", "100"))
            self._send_json(200, {"events": app.auth.list_audit(limit)})
            return

        # ---------------- dashboard / trace ----------------
        if path == "/api/dashboard" and method == "GET":
            self._authenticate("stats.read")
            stats = app.metrics.snapshot() if app.metrics else {}
            recent = (
                app.metrics.recent_transfers(int(self._first(query, "limit", "50")))
                if app.metrics
                else []
            )
            self._send_json(200, {"stats": stats, "recent": recent})
            return

        if path == "/api/packets" and method == "GET":
            self._authenticate("stats.read")
            limit = int(self._first(query, "limit", "200"))
            opcode = self._first(query, "opcode") or None
            events = app.tracer.events(limit=limit, opcode=opcode) if app.tracer else []
            counts = app.tracer.counts() if app.tracer else {}
            self._send_json(
                200,
                {
                    "events": [event.to_dict() for event in events],
                    "counts": counts,
                },
            )
            return

        # ---------------- PXE profiles ----------------
        if path == "/api/pxe/profiles" and method == "GET":
            self._authenticate("pxe.read")
            self._send_json(
                200,
                {
                    "profiles": app.pxe.list_profiles() if app.pxe else [],
                    "issues": app.pxe.validate_all() if app.pxe else [],
                },
            )
            return

        if path == "/api/pxe/profiles" and method in ("POST", "PUT"):
            self._authenticate("pxe.manage")
            if app.pxe is None:
                raise AdminAPIError("PXE manager chưa được cấu hình", 501)
            body = self._json_body()
            require_files = self._first(query, "require_files", "0") in (
                "1",
                "true",
                "yes",
            )
            profile = app.pxe.upsert_profile(body, require_files=require_files)
            self._send_json(200, {"profile": profile})
            return

        if path == "/api/pxe/profiles" and method == "DELETE":
            self._authenticate("pxe.manage")
            if app.pxe is None:
                raise AdminAPIError("PXE manager chưa được cấu hình", 501)
            app.pxe.delete_profile(self._first(query, "name"))
            self._send_json(200, {"status": "deleted"})
            return

        if path == "/api/pxe/select" and method == "GET":
            self._authenticate("pxe.read")
            if app.pxe is None:
                raise AdminAPIError("PXE manager chưa được cấu hình", 501)
            profile = app.pxe.select_profile(
                self._first(query, "arch", "bios"),
                name=self._first(query, "name") or None,
            )
            self._send_json(200, {"profile": profile})
            return

        raise AdminAPIError("Không tìm thấy endpoint", 404)


class AdminAPIServer:
    def __init__(
        self,
        storage: StorageManager,
        auth: AuthManager,
        *,
        metrics: MetricsCollector | None = None,
        tracer: PacketTracer | None = None,
        pxe=None,
        enterprise=None,
        host: str = DEFAULT_ADMIN_HOST,
        port: int = DEFAULT_ADMIN_PORT,
        max_body: int = DEFAULT_MAX_ADMIN_BODY,
    ):
        self.storage = storage
        self.auth = auth
        self.metrics = metrics
        self.tracer = tracer
        self.pxe = pxe
        self.enterprise = enterprise
        self.max_body = max_body
        self.httpd = _AdminHTTPServer((host, port), _AdminRequestHandler, self)
        self.host, self.port = self.httpd.server_address[:2]
        self._thread: threading.Thread | None = None

    def start(self, *, background: bool = True) -> None:
        if background:
            self._thread = threading.Thread(
                target=self.httpd.serve_forever, name="admin-api", daemon=True
            )
            self._thread.start()
        else:
            self.httpd.serve_forever()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="tftp-admin-api")
    parser.add_argument("--host", default=DEFAULT_ADMIN_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_ADMIN_PORT)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--db", default=DEFAULT_ADMIN_DB)
    parser.add_argument("--metrics-db", default=None)
    parser.add_argument("--pxe-profiles", default=DEFAULT_PXE_PROFILES)
    parser.add_argument("--enterprise-db", default=None,
                        help="Bật Enterprise File Manager với database này")
    parser.add_argument("--enterprise-root", default="storage/company_files")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    storage = StorageManager(Path(args.root))
    storage.ensure_layout()
    auth = AuthManager(args.db)
    created = auth.ensure_default_admin()
    if created:
        logger.warning(
            "Đã tạo tài khoản admin mặc định '%s' (mật khẩu: '%s'). "
            "Hãy đổi mật khẩu trước khi dùng trong LAN.",
            created[0],
            created[1],
        )
    metrics = MetricsCollector(args.metrics_db or Path(args.db).with_suffix(".metrics.db"))
    tracer = PacketTracer()
    pxe = PxeManager(storage, args.pxe_profiles)
    enterprise = None
    if args.enterprise_db:
        from enterprise.service import EnterpriseService

        enterprise = EnterpriseService(args.enterprise_db, args.enterprise_root)
    api = AdminAPIServer(
        storage, auth, metrics=metrics, tracer=tracer, pxe=pxe, enterprise=enterprise
    )
    logger.info("Admin API: %s (root=%s)", api.url, storage.root)
    try:
        api.start(background=False)
    except KeyboardInterrupt:
        pass
    finally:
        api.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
