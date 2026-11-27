"""Reset admin password in tftp_admin.db (does not delete database)."""

from pathlib import Path

from auth import AuthManager

DB = "tftp_admin.db"
NEW_PASSWORD = "Admin123@"


def main() -> int:
    if not Path(DB).exists():
        print(f"Not found {DB} in current directory: {Path.cwd()}")
        return 1

    auth = AuthManager(DB)
    try:
        auth.change_password("admin", NEW_PASSWORD)
        print(f"DONE: admin password changed to = {NEW_PASSWORD}")
    except Exception as exc:  # noqa: BLE001 - script CLI, in lỗi rồi thoát
        print(f"ERROR: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
