"""Luồng Create Server -> đăng ký -> đăng nhập, và hai Server độc lập."""

import socket
import tempfile
import unittest
from pathlib import Path

from client.login_manager import LoginManager
from client.server_registry import ServerRegistry
from client.session_manager import SessionManager
from enterprise.errors import EnterpriseError
from enterprise.server_runtime import ServerRuntime


def free_port(udp=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MultiServerTests(unittest.TestCase):
    def test_two_independent_servers(self):
        tmp = Path(tempfile.mkdtemp())
        reg, sessions = ServerRegistry(tmp / "reg.json"), SessionManager()
        runtimes, ids = [], []
        try:
            for name, pw in (("Cty A", "MatKhauA1234"), ("Cty B", "MatKhauB1234")):
                rt = ServerRuntime(tmp / name.replace(" ", "_"))
                cfg = rt.create(name, "127.0.0.1", free_port(), free_port(udp=True),
                                "superadmin", pw)
                runtimes.append(rt)
                ids.append(cfg.server_id)
                reg.add(cfg.server_id, cfg.name, cfg.host, cfg.api_port, cfg.tftp_port)
            self.assertNotEqual(*ids)
            login = LoginManager(reg, sessions)
            self.assertEqual(login.login(ids[0], "superadmin", "MatKhauA1234").role,
                             "SUPER_ADMIN")
            with self.assertRaises(EnterpriseError):  # tài khoản Server A không dùng được ở B
                login.login(ids[1], "superadmin", "MatKhauA1234")
        finally:
            for rt in runtimes:
                rt.stop()

    def test_port_in_use_rejected_and_no_leftover(self):
        tmp = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen()
            busy = s.getsockname()[1]
            rt = ServerRuntime(tmp / "x")
            with self.assertRaises(EnterpriseError):
                rt.create("X", "127.0.0.1", busy, free_port(udp=True), "sa", "MatKhau12345")
        self.assertFalse(rt.exists)


if __name__ == "__main__":
    unittest.main()
