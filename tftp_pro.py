"""Chạy TFTP Server + Admin API trong cùng một tiến trình.

Đây là cách khuyến nghị cho demo: server và API dùng chung ``StorageManager``,
``MetricsCollector`` và ``PacketTracer`` nên Dashboard/Packet Analyzer phản ánh
đúng các phiên TFTP. Hai dịch vụ vẫn tách biệt về giao thức và cổng lắng nghe.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

from admin_api import AdminAPIServer
from auth import AuthManager
from metrics import MetricsCollector
from packet_trace import PacketTracer
from pxe_config import PxeManager
from tftp_config import (
    DEFAULT_ADMIN_DB,
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_BLKSIZE,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_WINDOWSIZE,
    DEFAULT_PORT,
    DEFAULT_PXE_PROFILES,
    DEFAULT_RETRIES,
    DEFAULT_ROOT,
    DEFAULT_TIMEOUT,
)
from tftp_server import ServerConfig, TFTPServer

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tftp-pro")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--max-clients", type=int, default=DEFAULT_MAX_CLIENTS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--blksize", type=int, default=DEFAULT_BLKSIZE)
    parser.add_argument(
        "--windowsize",
        type=int,
        default=DEFAULT_MAX_WINDOWSIZE,
        help="Cửa sổ tối đa server chấp nhận (client mặc định vẫn là 1)",
    )
    parser.add_argument("--admin-host", default=DEFAULT_ADMIN_HOST)
    parser.add_argument("--admin-port", type=int, default=DEFAULT_ADMIN_PORT)
    parser.add_argument("--admin-db", default=DEFAULT_ADMIN_DB)
    parser.add_argument("--metrics-db", default=None)
    parser.add_argument("--pxe-profiles", default=DEFAULT_PXE_PROFILES)
    parser.add_argument(
        "--enterprise-db",
        default=None,
        help="Bật Workspace Enterprise (roles/nhân viên/ACL) với database SQLite này",
    )
    parser.add_argument(
        "--enterprise-root",
        default="storage/company_files",
        help="Thư mục lưu file của Workspace Enterprise",
    )
    parser.add_argument(
        "--no-admin",
        action="store_true",
        help="Chỉ chạy TFTP Server, không mở Admin API",
    )
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    config = ServerConfig(
        host=args.host,
        port=args.port,
        root=Path(args.root),
        timeout=args.timeout,
        retries=args.retries,
        max_clients=args.max_clients,
        overwrite=args.overwrite,
        blksize=args.blksize,
        windowsize=args.windowsize,
        log_level=args.log_level,
    )

    metrics = MetricsCollector(
        args.metrics_db or Path(args.admin_db).with_suffix(".metrics.db")
    )
    tracer = PacketTracer()
    server = TFTPServer(config, metrics=metrics, tracer=tracer)
    server.storage.ensure_layout()
    pxe = PxeManager(server.storage, args.pxe_profiles)

    api: AdminAPIServer | None = None
    if not args.no_admin:
        if args.admin_host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                "Admin API đang bind %s, không chỉ localhost. Chỉ dùng trong "
                "LAN tin cậy và luôn đặt mật khẩu mạnh.",
                args.admin_host,
            )
        auth = AuthManager(args.admin_db)
        created = auth.ensure_default_admin()
        if created:
            logger.warning(
                "Đã tạo admin mặc định '%s'. Mật khẩu lấy từ TFTP_ADMIN_PASSWORD; "
                "hãy đổi mật khẩu trước khi dùng trong LAN.",
                created[0],
            )
        enterprise = None
        if args.enterprise_db:
            from enterprise.service import EnterpriseService

            Path(args.enterprise_root).mkdir(parents=True, exist_ok=True)
            enterprise = EnterpriseService(args.enterprise_db, args.enterprise_root)
            if not enterprise.accounts.list_users():
                super_user = (os.environ.get("TFTP_SUPER_ADMIN_USER") or "admin").strip()
                super_pass = (
                    os.environ.get("TFTP_SUPER_ADMIN_PASSWORD")
                    or os.environ.get("TFTP_ADMIN_PASSWORD")
                )
                if super_pass:
                    try:
                        enterprise.accounts.create_super_admin(super_user, super_pass)
                        logger.warning(
                            "Đã tạo SUPER_ADMIN '%s' cho Workspace Enterprise. "
                            "Mật khẩu lấy từ TFTP_SUPER_ADMIN_PASSWORD/TFTP_ADMIN_PASSWORD.",
                            super_user,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Không tạo được SUPER_ADMIN: %s", exc)
                else:
                    logger.warning(
                        "Workspace Enterprise chưa có tài khoản. Đặt "
                        "TFTP_SUPER_ADMIN_PASSWORD (tối thiểu 8 ký tự) rồi chạy lại."
                    )
        api = AdminAPIServer(
            server.storage,
            auth,
            metrics=metrics,
            tracer=tracer,
            pxe=pxe,
            enterprise=enterprise,
            host=args.admin_host,
            port=args.admin_port,
        )
        api.start()
        logger.info("Admin API: %s", api.url)

    logger.info(
        "TFTP: %s:%d root=%s windowsize<=%d",
        args.host,
        args.port,
        server.root,
        args.windowsize,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        if api is not None:
            api.stop()
    return 0


_RUNNING: dict[str, threading.Thread] = {}


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.bind((host, port))
        return False
    except OSError:
        return True


def ensure_local_server_running(admin_password: str, root_dir: str):
    """Kiểm tra cổng, tạo .env nếu thiếu và chạy Server nền (1-Click)."""
    root = Path(root_dir)
    root.mkdir(parents=True, exist_ok=True)
    env_path = root / ".env"
    if not env_path.exists():
        env_path.write_text(
            f"TFTP_ADMIN_PASSWORD={admin_password}\n", encoding="utf-8"
        )
    os.environ.setdefault("TFTP_ADMIN_PASSWORD", str(admin_password))

    # Nếu cả hai cổng đã bận thì coi như server đã chạy.
    if _port_in_use(6969) and _port_in_use(6971):
        return None

    key = str(root.resolve())
    if key in _RUNNING and _RUNNING[key].is_alive():
        return _RUNNING[key]

    from tftp_config import DEFAULT_ADMIN_DB

    admin_db = root / DEFAULT_ADMIN_DB

    def _run():
        try:
            config = ServerConfig(
                host="0.0.0.0",
                port=6969,
                root=root,
                log_level="ERROR",
            )
            metrics = MetricsCollector(
                str(admin_db).replace(".db", ".metrics.db")
            )
            tracer = PacketTracer()
            server = TFTPServer(config, metrics=metrics, tracer=tracer)
            server.storage.ensure_layout()
            pxe = PxeManager(server.storage, "pxe_profiles.json")
            auth = AuthManager(str(admin_db))
            auth.ensure_default_admin(password=str(admin_password))
            api = AdminAPIServer(
                server.storage,
                auth,
                metrics=metrics,
                tracer=tracer,
                pxe=pxe,
                host="127.0.0.1",
                port=6971,
            )
            api.start()
            server.serve_forever()
        except Exception:  # noqa: BLE001, S110
            pass

    thread = threading.Thread(target=_run, name="tftp-pro-local", daemon=True)
    thread.start()
    _RUNNING[key] = thread

    # Chờ cổng sẵn sàng (tối đa ~5s).
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if _port_in_use(6969) or _port_in_use(6971):
            break
        time.sleep(0.1)
    return thread


if __name__ == "__main__":
    raise SystemExit(main())
