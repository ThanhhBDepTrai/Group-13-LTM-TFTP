"""Đặt lại mật khẩu tài khoản legacy trong database Admin API.

``TFTP_ADMIN_PASSWORD`` chỉ được dùng để tạo tài khoản ``admin`` khi database
chưa có người dùng nào. Nếu database đã tồn tại (ví dụ từ lần chạy trước), biến
môi trường không thay đổi mật khẩu cũ; dùng script này để đổi:

    python scripts/set_admin_password.py
    python scripts/set_admin_password.py --username admin --db tftp_admin.db
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from auth import MIN_PASSWORD_LENGTH, AuthError, AuthManager  # noqa: E402


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def _read_password(args: argparse.Namespace) -> str:
    if args.password:
        return args.password
    first = getpass.getpass("Mật khẩu mới: ")
    second = getpass.getpass("Nhập lại mật khẩu: ")
    if first != second:
        raise SystemExit("Mật khẩu nhập lại không khớp.")
    return first


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(
        prog="set-admin-password",
        description="Đặt lại mật khẩu tài khoản admin (legacy hoặc Workspace Enterprise).",
    )
    parser.add_argument("--db", default="tftp_admin.db", help="Đường dẫn database (mặc định: tftp_admin.db)")
    parser.add_argument("--username", default="admin", help="Tài khoản cần đổi (mặc định: admin)")
    parser.add_argument(
        "--enterprise",
        action="store_true",
        help="Đổi mật khẩu trong Workspace Enterprise (ent_users) thay vì legacy",
    )
    parser.add_argument(
        "--storage",
        default="web_workspace/files",
        help="Thư mục file của Enterprise khi dùng --enterprise",
    )
    parser.add_argument(
        "--password",
        help="Mật khẩu mới (Enterprise tối thiểu 8 ký tự); bỏ trống để nhập ẩn",
    )
    args = parser.parse_args(argv)

    if args.enterprise and args.db == "tftp_admin.db":
        args.db = "web_workspace/efm.db"
    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"Không tìm thấy database: {db_path}", file=sys.stderr)
        print("Chạy lệnh này trong thư mục dự án hoặc truyền --db đúng đường dẫn.", file=sys.stderr)
        return 2

    password = _read_password(args)
    if args.enterprise:
        from enterprise.errors import EnterpriseError
        from enterprise.service import EnterpriseService

        try:
            EnterpriseService(db_path, args.storage).accounts.change_password(
                args.username, password
            )
        except (EnterpriseError, ValueError) as exc:
            print(f"Không đổi được mật khẩu: {getattr(exc, 'message', exc)}", file=sys.stderr)
            return 1
        print(f"Đã đặt lại mật khẩu Enterprise cho '{args.username}' trong {db_path}.")
        print("Các phiên đăng nhập cũ của tài khoản này đã bị thu hồi.")
        return 0

    try:
        AuthManager(db_path).change_password(args.username, password)
    except AuthError as exc:
        print(f"Không đổi được mật khẩu: {exc.message}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Mật khẩu không hợp lệ: {exc}", file=sys.stderr)
        return 1

    print(f"Đã đặt lại mật khẩu cho tài khoản '{args.username}' trong {db_path}.")
    print("Các phiên đăng nhập cũ của tài khoản này đã bị thu hồi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
