"""Tạo Server mới: python -m enterprise.cli setup --name "Cty A" ..."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from enterprise.errors import EnterpriseError
from enterprise.service import EnterpriseService


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="efm")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="Tạo Server, Workspace và Super Admin")
    s.add_argument("--name", required=True)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--api-port", type=int, default=6971)
    s.add_argument("--tftp-port", type=int, default=6969)
    s.add_argument("--db", default="database/tftp_admin.db")
    s.add_argument("--root", default="storage/company_files")
    s.add_argument("--admin-user", default="superadmin")
    s.add_argument("--check-bind", action="store_true", help="Kiểm tra port còn trống")
    a = p.parse_args(argv)
    password = os.environ.get("EFM_ADMIN_PASSWORD") or getpass.getpass("Mật khẩu Super Admin: ")
    try:
        svc = EnterpriseService(a.db, a.root)
        cfg = svc.setup_server(a.name, a.host, a.api_port, a.tftp_port, a.admin_user, password,
                               check_bind=a.check_bind)
    except EnterpriseError as exc:
        print(f"Lỗi: {exc.message}", file=sys.stderr)
        return 1
    print(f"Đã tạo Server {cfg.server_id} ({cfg.name}) tại {cfg.host}:{cfg.api_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
