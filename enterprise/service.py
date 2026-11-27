"""Facade gom toàn bộ module enterprise cho một Server.

Mọi thao tác nghiệp vụ đi qua đây để kiểm tra quyền (PermissionManager + FileACL)
và ghi audit. Các module TFTP cũ không bị thay đổi: chỉ cần gọi
``authorize_transfer`` trước khi cho phép RRQ/WRQ.
"""

from __future__ import annotations

import logging
from pathlib import Path

from database.database import Database
from enterprise.accounts import AccountManager
from enterprise.audit.audit_manager import AuditManager
from enterprise.employees.department_manager import DepartmentManager
from enterprise.employees.employee_manager import EmployeeManager
from enterprise.employees.position_manager import PositionManager
from enterprise.errors import EnterpriseError
from enterprise.files.file_metadata import FileMetadata
from enterprise.files.folder_manager import FolderManager
from enterprise.files.tag_manager import TagManager
from enterprise.permissions.file_acl import FileACL, normalize
from enterprise.permissions.permission_manager import PermissionManager
from enterprise.permissions.role_manager import RoleManager
from enterprise.server.join_code_manager import JoinCodeManager
from enterprise.server.server_manager import ServerManager
from enterprise.tasks.task_manager import TaskManager

_TRANSFER_ACTION = {"RRQ": ("read", "files.read"), "WRQ": ("write", "files.upload")}

logger = logging.getLogger(__name__)


class EnterpriseService:
    def __init__(self, db_path: str | Path, storage_root: str | Path):
        self.db = Database(db_path)
        self.servers = ServerManager(self.db)  # seed roles/permissions
        self.accounts = AccountManager(self.db)
        self.permissions = PermissionManager(self.db)
        self.roles = RoleManager(self.db)
        self.acl = FileACL(self.db)
        self.audit = AuditManager(self.db)
        self.join_codes = JoinCodeManager(self.db)
        self.departments = DepartmentManager(self.db)
        self.positions = PositionManager(self.db)
        self.employees = EmployeeManager(self.db)
        self.folders = FolderManager(self.db, storage_root)
        self.files = FileMetadata(self.db)
        self.tags = TagManager(self.db)
        self.tasks = TaskManager(self.db)

    # --- Giai đoạn 1-2: Server + đăng nhập --------------------------------
    def setup_server(self, name, host, api_port, tftp_port, admin_user, admin_password,
                     **kw):
        cfg = self.servers.create(name, host, api_port, tftp_port, **kw)
        self.accounts.create_super_admin(admin_user, admin_password)
        self.audit.log(admin_user, "server.create", cfg.server_id)
        return cfg

    def login(self, username, password, client_ip=None) -> dict:
        try:
            session = self.accounts.login(username, password, client_ip)
        except EnterpriseError as exc:
            self.audit.log(username, "auth.login", None, exc.message, success=False)
            raise
        self.audit.log(username, "auth.login")
        return session

    def ensure_demo_accounts(self, password: str = "Admin123@") -> None:
        demos = [
            ("manager1", "MANAGER", "Quản lý mẫu"),
            ("staff1", "EMPLOYEE", "Nhân viên mẫu"),
            ("guest1", "GUEST", "Khách mẫu"),
        ]
        for username, role, full_name in demos:
            try:
                self.accounts.get_user(username)
            except EnterpriseError:
                try:
                    self.accounts.create_user(username, password, role)
                except EnterpriseError:
                    continue
            # ensure profile exists (best effort)
            try:
                self.employees.create(full_name, username=username)
            except EnterpriseError as exc:
                logger.debug("Bỏ qua lỗi tạo hồ sơ cho %s: %s", username, exc)

    def authenticate(self, token: str) -> dict:
        return self.accounts.validate_token(token)

    def join(self, code, username, password) -> dict:
        role = self.join_codes.consume(code)
        user = self.accounts.create_user(username, password, role)
        self.audit.log(username, "auth.join", None, f"role={role}")
        return user

    # --- quản lý người dùng / vai trò --------------------------------------
    def create_user(self, actor, username, password, role="EMPLOYEE") -> dict:
        self.permissions.require(actor, "users.manage")
        if not self.roles.can_assign(actor["role"], role):
            self.audit.log(actor["username"], "user.create", username, f"role={role}", False)
            raise EnterpriseError("Không được tạo tài khoản có vai trò ngang hoặc cao hơn", 403)
        user = self.accounts.create_user(username, password, role)
        self.audit.log(actor["username"], "user.create", username, f"role={role}")
        return user

    def set_role(self, actor, username, role) -> None:
        actor_role = str(actor.get("role", "")).upper()
        # SUPER_ADMIN: toàn quyền. ADMIN: được đổi vai trò thấp hơn cấp mình
        # (RoleManager.can_assign vẫn chặn gán ngang/cao hơn).
        if actor_role == "SUPER_ADMIN":
            pass
        elif actor_role == "ADMIN":
            if not self.permissions.has(actor_role, "users.manage"):
                raise EnterpriseError("Không có quyền đổi vai trò", 403)
        else:
            raise EnterpriseError("Chỉ Admin mới được đổi vai trò", 403)
        self.roles.set_role(actor, username, role)
        self.audit.log(actor["username"], "user.set_role", username, f"role={role}")

    def update_department(self, actor, department_id, **fields) -> dict:
        self.permissions.require(actor, "employees.manage")
        updated = self.departments.update(int(department_id), **fields)
        self.audit.log(
            actor["username"], "department.update", str(department_id),
            ",".join(sorted(fields)),
        )
        return updated

    def create_join_code(self, actor, *, role="EMPLOYEE", ttl=24 * 3600, max_uses=1) -> str:
        self.permissions.require(actor, "server.manage")
        if not self.roles.can_assign(actor["role"], role):
            raise EnterpriseError("Không được tạo mã cho vai trò ngang hoặc cao hơn", 403)
        code = self.join_codes.create(actor["username"], role=role, ttl=ttl, max_uses=max_uses)
        self.audit.log(actor["username"], "joincode.create", None, f"role={role}")
        return code

    # --- Giai đoạn 4: file -------------------------------------------------
    def _owner(self, path: str) -> str | None:
        meta = self.files.get(path)
        if meta:
            return meta["owner"]
        folder = self.folders.get(path)
        return folder["owner"] if folder else None

    def authorize_transfer(self, user: dict, op: str, path: str) -> Path:
        """Cấp/từ chối một lượt truyền TFTP (RRQ = đọc, WRQ = ghi). Trả về đường dẫn thật."""
        if op not in _TRANSFER_ACTION:
            raise EnterpriseError("Thao tác TFTP không hợp lệ")
        action, perm = _TRANSFER_ACTION[op]
        try:
            self.permissions.require(user, perm)
            virtual = normalize(path)
            owner = self._owner(virtual)
            if not self.acl.check(user, virtual, action, owner=owner) and not (op == "WRQ" and owner is None and self._parent_writable(user, virtual)):
                raise EnterpriseError("Không có quyền với tệp/thư mục này", 403)
            real = self.folders.real_path(virtual)
        except EnterpriseError as exc:
            self.audit.log(user["username"], f"tftp.{op.lower()}", path, exc.message, False)
            raise
        self.audit.log(user["username"], f"tftp.{op.lower()}", virtual)
        return real

    def _parent_writable(self, user, virtual) -> bool:
        parent = virtual.rsplit("/", 1)[0] or "/"
        return self.acl.check(user, parent, "write", owner=self._owner(parent))

    def register_upload(self, user: dict, path: str) -> dict:
        """Gọi sau khi WRQ hoàn tất để lưu metadata (owner, sha256)."""
        virtual = normalize(path)
        real = self.folders.real_path(virtual)
        folder = self._folder_for_path(virtual)
        if folder is not None and folder.get("department_id") is not None:
            quota = self.department_quota(folder["department_id"])
            old = self.files.get(virtual)
            old_size = int(old["size"]) if old else 0
            new_size = real.stat().st_size if real.is_file() else 0
            if quota["used_bytes"] - old_size + new_size > quota["limit_bytes"]:
                raise EnterpriseError("Vuot quota phong ban", 413)
        meta = self.files.register(virtual, real, user["username"])
        self.audit.log(user["username"], "file.upload", virtual, meta["sha256"])
        return meta

    def _folder_for_path(self, virtual: str) -> dict | None:
        virtual = normalize(virtual)
        parts = [p for p in virtual.strip("/").split("/") if p]
        candidates: list[str] = []
        cur = ""
        for part in parts[:-1]:
            cur += "/" + part
            candidates.append(cur)
        if not candidates:
            candidates.append("/")
        for candidate in reversed(candidates):
            folder = self.folders.get(candidate)
            if folder is not None:
                return folder
        return self.folders.get("/")

    def department_quota(self, department_id) -> dict:
        with self.db.connect() as c:
            row = c.execute(
                "SELECT * FROM departments WHERE id=?", (int(department_id),)
            ).fetchone()
            if row is None:
                raise EnterpriseError("Khong tim thay phong ban", 404)
            keys = row.keys()
            quota = int(row["quota_bytes"]) if "quota_bytes" in keys and row["quota_bytes"] is not None else 104857600
            folders = [
                r["path"]
                for r in c.execute(
                    "SELECT path FROM folders WHERE department_id=?", (int(department_id),)
                )
            ]
            owners = {
                r["username"]
                for r in c.execute(
                    "SELECT u.username FROM ent_users u "
                    "LEFT JOIN employees e ON e.username=u.username "
                    "WHERE e.department_id=?",
                    (int(department_id),),
                )
            }
            used = 0
            for f in c.execute("SELECT path, size, owner FROM files"):
                in_folder = False
                for folder in folders:
                    base = folder.rstrip("/")
                    if f["path"] == folder or f["path"].startswith(base + "/"):
                        in_folder = True
                        break
                if in_folder or (f["owner"] in owners):
                    used += int(f["size"] or 0)
            percent = (used / quota * 100.0) if quota else 0.0
            return {
                "used_bytes": used,
                "limit_bytes": quota,
                "percent": round(percent, 2),
            }

    def delete_file(self, user: dict, path: str) -> None:
        self.permissions.require(user, "files.delete")
        virtual = normalize(path)
        self.acl.require(user, virtual, "delete", owner=self._owner(virtual))
        real = self.folders.real_path(virtual)
        if real.is_file():
            real.unlink()
        self.files.delete(virtual)
        self.audit.log(user["username"], "file.delete", virtual)

    # --- Giai đoạn 5: task + audit ----------------------------------------
    def assign_task(self, actor, title, assignees, **kw) -> dict:
        self.permissions.require(actor, "tasks.manage")
        task = self.tasks.create(title, actor["username"], assignees=assignees, **kw)
        self.audit.log(actor["username"], "task.create", str(task["id"]), title)
        return task

    def update_task_status(self, actor, task_id, status) -> dict:
        task = self.tasks.get(task_id)
        is_assignee = actor["username"] in task["assignees"]
        if not (is_assignee and self.permissions.has(actor["role"], "tasks.update")):
            self.permissions.require(actor, "tasks.manage")
        task = self.tasks.set_status(task_id, status)
        self.audit.log(actor["username"], "task.status", str(task_id), status)
        return task

    def list_audit(self, actor, **kw):
        self.permissions.require(actor, "audit.read")
        return self.audit.list(**kw)
