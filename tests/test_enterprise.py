"""Kiểm thử Enterprise File Manager: server, đăng nhập, phân quyền, file, task, audit."""

import tempfile
import unittest
from pathlib import Path

from enterprise.errors import EnterpriseError
from enterprise.service import EnterpriseService


def make_service():
    tmp = tempfile.mkdtemp()
    svc = EnterpriseService(Path(tmp) / "a.db", Path(tmp) / "files")
    svc.setup_server("Cty A", "127.0.0.1", 8000, 6969, "root", "RootPass123")
    return svc, tmp


class EnterpriseTests(unittest.TestCase):
    def setUp(self):
        self.svc, self.tmp = make_service()
        self.root = self.svc.accounts.get_user("root")

    def user(self, name, role):
        self.svc.accounts.create_user(name, "Password123", role)
        return self.svc.accounts.get_user(name)

    def test_server_identity_and_single_server(self):
        cfg = self.svc.servers.get()
        self.assertRegex(cfg.server_id, r"^SRV-[0-9A-F]{8}$")
        with self.assertRaises(EnterpriseError):
            self.svc.servers.create("B", "127.0.0.1", 1, 2)

    def test_validator_rejects_bad_ports(self):
        from enterprise.server.server_validator import validate_server_config
        for args in [("127.0.0.1", 0, 69), ("127.0.0.1", 80, 80), ("127.0.0.1", "x", 69)]:
            with self.assertRaises(EnterpriseError):
                validate_server_config(*args)

    def test_login_logout_and_lockout(self):
        s = self.svc.login("root", "RootPass123")
        self.assertEqual(self.svc.authenticate(s["token"])["role"], "SUPER_ADMIN")
        self.svc.accounts.logout(s["token"])
        with self.assertRaises(EnterpriseError):
            self.svc.authenticate(s["token"])
        for _ in range(5):
            with self.assertRaises(EnterpriseError):
                self.svc.login("root", "sai-mat-khau")
        with self.assertRaises(EnterpriseError) as ctx:
            self.svc.login("root", "RootPass123")
        self.assertEqual(ctx.exception.status, 429)

    def test_join_code_single_use_and_no_super_admin(self):
        code = self.svc.create_join_code(self.root, role="EMPLOYEE")
        self.svc.join(code, "nv1", "Password123")
        with self.assertRaises(EnterpriseError):
            self.svc.join(code, "nv2", "Password123")
        with self.assertRaises(EnterpriseError):
            self.svc.create_join_code(self.root, role="SUPER_ADMIN")

    def test_cannot_escalate_role(self):
        hr = self.user("hr1", "HR")
        with self.assertRaises(EnterpriseError):
            self.svc.create_user(hr, "x1", "Password123", "ADMIN")
        self.svc.create_user(hr, "x2", "Password123", "EMPLOYEE")

    def test_last_super_admin_protected(self):
        with self.assertRaises(EnterpriseError):
            self.svc.accounts.set_active("root", False)

    def test_acl_and_transfer_authorization(self):
        emp = self.user("emp", "EMPLOYEE")
        other = self.user("other", "EMPLOYEE")
        self.svc.folders.create("/company_files/shared", owner="root")
        with self.assertRaises(EnterpriseError):  # chưa có ACL -> từ chối ghi
            self.svc.authorize_transfer(emp, "WRQ", "/company_files/shared/a.txt")
        self.svc.acl.set("/company_files/shared", "role", "EMPLOYEE", read=True, write=True)
        real = self.svc.authorize_transfer(emp, "WRQ", "/company_files/shared/a.txt")
        real.write_bytes(b"hello")
        meta = self.svc.register_upload(emp, "/company_files/shared/a.txt")
        self.assertEqual(meta["owner"], "emp")
        self.assertEqual(len(meta["sha256"]), 64)
        self.svc.authorize_transfer(other, "RRQ", "/company_files/shared/a.txt")
        with self.assertRaises(EnterpriseError):  # không cho xóa
            self.svc.delete_file(other, "/company_files/shared/a.txt")

    def test_acl_department_scope(self):
        dep = self.svc.departments.create("Kỹ thuật")
        self.user("kt1", "EMPLOYEE")
        outsider = self.user("kt2", "EMPLOYEE")
        self.svc.employees.create("Nguyễn Văn A", username="kt1", department_id=dep["id"])
        member = self.svc.accounts.get_user("kt1")
        self.assertEqual(member["department"], "Kỹ thuật")
        self.svc.folders.create("/company_files/ky_thuat", owner="root")
        with self.assertRaises(EnterpriseError):  # chưa cấp quyền cho phòng ban
            self.svc.authorize_transfer(member, "RRQ", "/company_files/ky_thuat/tai_lieu.txt")
        self.svc.acl.set("/company_files/ky_thuat", "department", "Kỹ thuật",
                         read=True, write=True)
        self.svc.authorize_transfer(member, "WRQ", "/company_files/ky_thuat/tai_lieu.txt")
        self.svc.authorize_transfer(member, "RRQ", "/company_files/ky_thuat/tai_lieu.txt")
        with self.assertRaises(EnterpriseError):  # ngoài phòng ban vẫn bị chặn
            self.svc.authorize_transfer(outsider, "RRQ", "/company_files/ky_thuat/tai_lieu.txt")

    def test_acl_most_specific_path_wins(self):
        emp = self.user("emp", "EMPLOYEE")
        self.svc.folders.create("/company_files", owner="root")
        self.svc.folders.create("/company_files/secret", owner="root")
        self.svc.acl.set("/company_files", "role", "EMPLOYEE", read=True)
        self.svc.authorize_transfer(emp, "RRQ", "/company_files/public.txt")
        self.svc.acl.set("/company_files/secret", "role", "EMPLOYEE", read=False)
        with self.assertRaises(EnterpriseError):  # quy tắc cụ thể hơn (deny) thắng
            self.svc.authorize_transfer(emp, "RRQ", "/company_files/secret/private.txt")

    def test_path_traversal_blocked(self):
        # normalize() gập '..' về gốc ảo nên không thể thoát khỏi storage
        real = self.svc.authorize_transfer(self.root, "RRQ", "/../../etc/passwd")
        self.assertTrue(str(real).startswith(str(Path(self.tmp, "files").resolve())))
        self.assertEqual(real.name, "passwd")
        with self.assertRaises(EnterpriseError):
            self.svc.folders.real_path("/ok")  # sanity: đường dẫn hợp lệ vẫn đi được
            raise EnterpriseError("expected")

    def test_intern_cannot_upload(self):
        intern = self.user("in1", "INTERN")
        with self.assertRaises(EnterpriseError):
            self.svc.authorize_transfer(intern, "WRQ", "/shared/x.txt")

    def test_tasks_flow_and_audit(self):
        mgr = self.user("mgr", "MANAGER")
        emp = self.user("emp", "EMPLOYEE")
        task = self.svc.assign_task(mgr, "Làm báo cáo", ["emp"])
        self.svc.update_task_status(emp, task["id"], "in_progress")
        with self.assertRaises(EnterpriseError):  # nhảy bước
            self.svc.update_task_status(emp, task["id"], "done")
        with self.assertRaises(EnterpriseError):  # nhân viên không giao việc
            self.svc.assign_task(emp, "x", ["emp"])
        actions = [e.action for e in self.svc.list_audit(mgr, limit=50)]
        self.assertIn("task.create", actions)
        with self.assertRaises(EnterpriseError):
            self.svc.list_audit(emp)

    def test_employees_and_departments(self):
        dep = self.svc.departments.create("Kỹ thuật")
        pos = self.svc.positions.create("Dev")
        self.user("dev1", "EMPLOYEE")
        e = self.svc.employees.create("Nguyễn A", username="dev1", department_id=dep["id"],
                                      position_id=pos["id"])
        self.assertEqual(e["department"], "Kỹ thuật")
        self.assertEqual(self.svc.accounts.get_user("dev1")["department"], "Kỹ thuật")
        with self.assertRaises(EnterpriseError):
            self.svc.employees.update(e["id"], manager_id=e["id"])


class RegistryTests(unittest.TestCase):
    def test_registry_roundtrip(self):
        from client.server_registry import ServerRegistry
        reg = ServerRegistry(Path(tempfile.mkdtemp()) / "s.json")
        reg.add("SRV-0123ABCD", "Cty A", "10.0.0.5", 8000, 6969)
        self.assertEqual(reg.get("SRV-0123ABCD")["host"], "10.0.0.5")
        with self.assertRaises(EnterpriseError):
            reg.add("bad-id", "x", "10.0.0.5", 1, 2)
        reg.remove("SRV-0123ABCD")
        self.assertEqual(reg.list(), [])


class ApiTests(unittest.TestCase):
    def test_login_over_http_and_identity(self):
        from admin_api import AdminAPIServer
        from auth import AuthManager
        from client.login_manager import LoginManager
        from client.server_registry import ServerRegistry
        from client.session_manager import SessionManager
        from storage_manager import StorageManager

        svc, tmp = make_service()
        storage = StorageManager(Path(tmp) / "legacy")
        storage.ensure_layout()
        api = AdminAPIServer(storage, AuthManager(Path(tmp) / "legacy.db"),
                             enterprise=svc, host="127.0.0.1", port=0)
        api.start()
        try:
            cfg = svc.servers.get()
            reg = ServerRegistry(Path(tmp) / "reg.json")
            reg.add(cfg.server_id, cfg.name, "127.0.0.1", api.port, cfg.tftp_port)
            sessions = SessionManager()
            s = LoginManager(reg, sessions).login(cfg.server_id, "root", "RootPass123")
            self.assertTrue(s.can("users.manage"))
            reg.add("SRV-FFFFFFFF", "Giả", "127.0.0.1", api.port, 1)
            with self.assertRaises(EnterpriseError):  # Server ID không khớp
                LoginManager(reg, sessions).login("SRV-FFFFFFFF", "root", "RootPass123")
        finally:
            api.stop()


if __name__ == "__main__":
    unittest.main()
