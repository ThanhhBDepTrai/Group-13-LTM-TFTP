"""Chạy TFTP Server + Admin API trong cùng một tiến trình.

Đây là cách khuyến nghị cho demo: server và API dùng chung ``StorageManager``,
``MetricsCollector`` và ``PacketTracer`` nên Dashboard/Packet Analyzer phản ánh
đúng các phiên TFTP. Hai dịch vụ vẫn tách biệt về giao thức và cổng lắng nghe.
"""

from __future__ import annotations

import argparse
import logging
import sys
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
                "Đã tạo admin mặc định '%s' (mật khẩu '%s'). Hãy đổi mật khẩu "
                "trước khi dùng trong LAN.",
                created[0],
                created[1],
            )
        api = AdminAPIServer(
            server.storage,
            auth,
            metrics=metrics,
            tracer=tracer,
            pxe=pxe,
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


if __name__ == "__main__":
    raise SystemExit(main())
