"""Nhập tài khoản/nhân sự từ một workspace efm.db sang workspace khác.

Dùng khi tài khoản được tạo ở GUI (``%APPDATA%\\TFTPPro\\servers\\<id>\\efm.db``)
nhưng Web UI lại chạy với ``web_workspace/efm.db``.

Ví dụ::

    python scripts/import_workspace.py \
        --from "%APPDATA%\\TFTPPro\\servers\\admin\\efm.db" \
        --to web_workspace/efm.db

Mặc định bỏ qua bản ghi đã tồn tại. Thêm ``--overwrite`` để cập nhật vai trò/
trạng thái của tài khoản trùng tên (KHÔNG đổi mật khẩu trừ khi có ``--overwrite``).
"""

# ruff: noqa: SIM118
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from database.database import Database
from database.seed_data import seed


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_target(path: Path) -> None:
    db = Database(path)
    seed(db)


def _copy_departments(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in src.execute("SELECT * FROM departments"):
        dst.execute(
            "INSERT INTO departments(name, description, quota_bytes) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "description=COALESCE(excluded.description, departments.description), "
            "quota_bytes=excluded.quota_bytes",
            (
                row["name"],
                row["description"] if "description" in row.keys() else "",
                row["quota_bytes"] if "quota_bytes" in row.keys() else 104857600,
            ),
        )
        target = dst.execute("SELECT id FROM departments WHERE name=?", (row["name"],)).fetchone()
        mapping[int(row["id"])] = int(target["id"])
    return mapping


def _copy_positions(src: sqlite3.Connection, dst: sqlite3.Connection) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for row in src.execute("SELECT * FROM positions"):
        dst.execute(
            "INSERT INTO positions(name, description) VALUES (?,?) "
            "ON CONFLICT(name) DO UPDATE SET description=COALESCE(excluded.description, positions.description)",
            (row["name"], row["description"] if "description" in row.keys() else ""),
        )
        target = dst.execute("SELECT id FROM positions WHERE name=?", (row["name"],)).fetchone()
        mapping[int(row["id"])] = int(target["id"])
    return mapping


def _copy_users(
    src: sqlite3.Connection, dst: sqlite3.Connection, *, overwrite: bool
) -> tuple[int, int]:
    inserted = skipped = 0
    for row in src.execute("SELECT * FROM ent_users"):
        exists = dst.execute(
            "SELECT 1 FROM ent_users WHERE username=?", (row["username"],)
        ).fetchone()
        if exists:
            if overwrite:
                dst.execute(
                    "UPDATE ent_users SET password_hash=?, salt=?, role=?, active=? "
                    "WHERE username=?",
                    (
                        row["password_hash"],
                        row["salt"],
                        row["role"],
                        row["active"],
                        row["username"],
                    ),
                )
                inserted += 1
            else:
                skipped += 1
            continue
        dst.execute(
            "INSERT INTO ent_users(username, password_hash, salt, role, active, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                row["username"],
                row["password_hash"],
                row["salt"],
                row["role"],
                row["active"],
                row["created_at"],
            ),
        )
        inserted += 1
    return inserted, skipped


def _copy_employees(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    dept_map: dict[int, int],
    pos_map: dict[int, int],
) -> int:
    inserted = 0
    for row in src.execute("SELECT * FROM employees"):
        username = row["username"] if "username" in row.keys() else None
        if username and dst.execute(
            "SELECT 1 FROM employees WHERE username=?", (username,)
        ).fetchone():
            continue
        dept_id = row["department_id"] if "department_id" in row.keys() else None
        pos_id = row["position_id"] if "position_id" in row.keys() else None
        dst.execute(
            "INSERT INTO employees(username, full_name, email, department_id, position_id, "
            "manager_id, created_at) VALUES (?,?,?,?,?,NULL,?)",
            (
                username,
                row["full_name"],
                row["email"] if "email" in row.keys() else None,
                dept_map.get(int(dept_id)) if dept_id is not None else None,
                pos_map.get(int(pos_id)) if pos_id is not None else None,
                row["created_at"] if "created_at" in row.keys() else "",
            ),
        )
        inserted += 1
    return inserted


def main() -> int:
    parser = argparse.ArgumentParser(description="Import workspace efm.db")
    parser.add_argument("--from", dest="source", required=True, help="efm.db nguồn")
    parser.add_argument("--to", dest="target", required=True, help="efm.db đích")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Ghi đè tài khoản trùng tên (bao gồm mật khẩu hash)",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    target = Path(args.target).expanduser()
    if not source.is_file():
        raise SystemExit(f"Không tìm thấy DB nguồn: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    _ensure_target(target)

    src = _connect(source)
    dst = _connect(target)
    try:
        dst.execute("PRAGMA foreign_keys=ON")
        with dst:
            dept_map = _copy_departments(src, dst)
            pos_map = _copy_positions(src, dst)
            users, skipped = _copy_users(src, dst, overwrite=args.overwrite)
            employees = _copy_employees(src, dst, dept_map, pos_map)
    finally:
        src.close()
        dst.close()

    print(f"Đã nhập {users} tài khoản ({skipped} bỏ qua), {employees} hồ sơ nhân viên.")
    print(f"  từ: {source}")
    print(f"  -> : {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
