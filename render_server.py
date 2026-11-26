"""Entry point cho Render: chay TFTP Pro o nen va phuc vu Web UI + proxy /api/*."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PORT = int(os.environ.get("PORT", "10000"))
ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
WORKSPACE_DIR = Path(
    os.environ.get("EFM_WORKSPACE_DIR") or (ROOT / "web_workspace")
)
ENTERPRISE_DB = WORKSPACE_DIR / "efm.db"
ENTERPRISE_ROOT = WORKSPACE_DIR / "files"


def _start_backend() -> subprocess.Popen:
    env = os.environ.copy()
    if "TFTP_ADMIN_PASSWORD" not in env:
        env["TFTP_ADMIN_PASSWORD"] = "Admin123@"
    env.setdefault("TFTP_DEMO_ACCOUNTS", "1")
    cmd = [
        sys.executable,
        str(ROOT / "tftp_pro.py"),
        "--enterprise-db",
        str(ENTERPRISE_DB),
        "--enterprise-root",
        str(ENTERPRISE_ROOT),
        "--root",
        str(ENTERPRISE_ROOT),
    ]
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    return proc


def _wait_backend(timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:6971/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    raise RuntimeError("Backend did not start in time")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self) -> None:
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        url = "http://127.0.0.1:6971" + self.path
        req = urllib.request.Request(url, data=body, method=self.command)
        for key, value in self.headers.items():
            if key.lower() not in ("host", "content-length", "connection", "transfer-encoding"):
                req.add_header(key, value)
        if length:
            req.add_header("Content-Length", str(length))
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                for key, value in resp.headers.items():
                    if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                        self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                if key.lower() not in ("transfer-encoding", "connection", "content-length"):
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def _route(self, method: str) -> None:
        path = urlsplit(self.path).path
        if path == "/health":
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/"):
            self._proxy()
            return
        # static files
        if path in ("/", "/index.html"):
            target = WEB_DIR / "index.html"
        else:
            target = WEB_DIR / path.lstrip("/")
        if not target.is_file() or WEB_DIR not in target.resolve().parents:
            target = WEB_DIR / "index.html"
        if not target.is_file():
            body = b"Not Found"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        data = target.read_bytes()
        content_type = "text/html; charset=utf-8"
        # Cache control handled by meta tags in HTML
        if target.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif target.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif target.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass


def main() -> None:
    backend = _start_backend()
    try:
        _wait_backend()
    except Exception as exc:  # noqa: BLE001
        backend.terminate()
        raise SystemExit(f"Backend failed to start: {exc}")
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Render server listening on 0.0.0.0:{PORT}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        backend.terminate()
        try:
            backend.wait(timeout=5)
        except Exception:  # noqa: BLE001
            backend.kill()


if __name__ == "__main__":
    main()
