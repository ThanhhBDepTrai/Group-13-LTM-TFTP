"""Reset admin password in tftp_admin.db (does not delete database)."""
import sys
from pathlib import Path
from auth import AuthManager

DB = "tftp_admin.db"
NEW_PASSWORD = "MatKhauManh123!"

if not Path(DB).exists():
    print(f"Not found {DB} in current directory: {Path.cwd()}")
    sys.exit(1)

auth = AuthManager(DB)
try:
    auth.change_password("admin", NEW_PASSWORD)
    print(f"DONE: admin password changed to = {NEW_PASSWORD}")
except Exception as exc:
    print(f"ERROR: {exc}")
    sys.exit(1)
