"""Luồng Create Server -> đăng ký -> đăng nhập, và hai Server độc lập."""

import socket
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from admin_client import AdminClient
from client.login_manager import LoginManager
from client.server_registry import ServerRegistry
from client.session_manager import SessionManager
from enterprise.errors import EnterpriseError
from enterprise.server_runtime import ServerRuntime
from tftp_client import TFTPClient
from tftp_gui import _format_modified_at


def free_port(udp=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_file_manager_timestamp_displays_local_date_and_time_only():
    expected = datetime.fromisoformat("2026-11-05T05:13:27+00:00").astimezone()
    assert _format_modified_at("2026-11-05T05:13:27+00:00") == expected.strftime(
        "%d/%m/%Y %H:%M:%S"
    )
    assert _format_modified_at("2026-11-05T12:13:27") == "05/11/2026 12:13:27"
    assert _format_modified_at("invalid timestamp") == "invalid timestamp"


class MultiServerTests(unittest.TestCase):
    def test_workspace_file_manager_transfers_appear_in_dashboard(self):
        tmp = Path(tempfile.mkdtemp())
        runtime = ServerRuntime(tmp / "workspace")
        try:
            cfg = runtime.create(
                "Workspace",
                "127.0.0.1",
                free_port(),
                free_port(udp=True),
                "superadmin",
                "MatKhauA1234",
            )
            client = AdminClient(f"http://127.0.0.1:{cfg.api_port}")
            client.login("superadmin", "MatKhauA1234")
            client.upload("report.txt", b"report contents")
            self.assertEqual(client.download("report.txt"), b"report contents")

            tftp_source = tmp / "tftp-upload.txt"
            tftp_source.write_bytes(b"uploaded over TFTP")
            TFTPClient("127.0.0.1", cfg.tftp_port).upload(
                tftp_source, "tftp-upload.txt"
            )

            dashboard = client.dashboard()
            self.assertEqual(dashboard["stats"]["total_transfers"], 3)
            self.assertEqual(dashboard["stats"]["uploads"], 2)
            self.assertEqual(dashboard["stats"]["downloads"], 1)
            self.assertEqual(
                {item["direction"] for item in dashboard["recent"]},
                {"upload", "download"},
            )
            self.assertIn(
                "tftp-upload.txt",
                {item["filename"] for item in dashboard["recent"]},
            )
        finally:
            runtime.stop()

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
