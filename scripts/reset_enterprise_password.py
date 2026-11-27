"""Đặt lại mật khẩu tài khoản trong workspace Enterprise (efm.db).

Dùng khi tài khoản SUPER_ADMIN/ADMIN bị quên mật khẩu hoặc bị đổi. Script thao tác
trực tiếp trên file database, không cần server đang chạy.

Ví dụ::

    python scripts/reset_enterprise_password.py
    python scripts/reset_enterprise_password.py --db web_workspace/efm.db --username admin
    python scripts/reset_enterprise_password.py --db web_workspace/efm.db --username admin --password "NewPass123!"
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from enterprise.accounts import AccountManager
from enterprise.errors import EnterpriseError


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Đặt lại mật khẩu Enterprise")
    parser.add_argument("--db", default="web_workspace/efm.db", help="Đường dẫn efm.db")
    parser.add_argument("--username", default="admin", help="Tên tài khoản cần đặt lại")
    parser.add_argument("--password", default=None, help="Mật khẩu mới (nếu bỏ qua sẽ hỏi)")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Không tìm thấy database: {db_path}")
        return 1

    password = args.password
    if password is None:
        password = getpass.getpass(f"Mật khẩu mới cho '{args.username}': ")
        confirm = getpass.getpass("Nhập lại mật khẩu: ")
        if password != confirm:
            print("Mật khẩu xác nhận không khớp.")
            return 1

    try:
        from database.database import Database
        db = Database(db_path)
        accounts = AccountManager(db)
        accounts.change_password(args.username, password)
        print(f"Đã đặt lại mật khẩu cho '{args.username}' trong {db_path}.")
        print("Các phiên đăng nhập cũ đã bị thu hồi; hãy đăng nhập lại.")
    except EnterpriseError as exc:
        print(f"Lỗi: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - script CLI
        print(f"Lỗi không xác định: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
