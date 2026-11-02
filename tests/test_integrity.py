"""Kiểm thử toàn vẹn SHA-256 xuyên suốt TFTP và Admin API."""

import hashlib
import threading
import time

import pytest

from admin_api import AdminAPIServer
from admin_client import AdminClient
from auth import AuthManager
from metrics import MetricsCollector
from packet_trace import PacketTracer
from pxe_config import PxeManager
from storage_manager import StorageManager
from tftp_client import TFTPClient
from tftp_server import ServerConfig, TFTPServer


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def stack(tmp_path):
    root = tmp_path / "root"
    storage = StorageManager(root)
    storage.ensure_layout()
    metrics = MetricsCollector()
    tracer = PacketTracer()
    server = TFTPServer(
        ServerConfig(
            host="127.0.0.1",
            port=0,
            root=root,
            timeout=1,
            retries=4,
            overwrite=True,
            windowsize=8,
            log_level="ERROR",
        ),
        metrics=metrics,
        tracer=tracer,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.01)
    port = server.sock.getsockname()[1]

    auth = AuthManager(tmp_path / "admin.db")
    auth.create_user("admin", "admin-pass", "admin")
    pxe = PxeManager(storage, tmp_path / "profiles.json")
    api = AdminAPIServer(
        storage,
        auth,
        metrics=metrics,
        tracer=tracer,
        pxe=pxe,
        host="127.0.0.1",
        port=0,
    )
    api.start()
    admin = AdminClient(api.url)
    admin.login("admin", "admin-pass")
    try:
        yield {
            "root": root,
            "storage": storage,
            "port": port,
            "admin": admin,
            "metrics": metrics,
            "tracer": tracer,
        }
    finally:
        api.stop()
        server.stop()
        thread.join(timeout=2)


def test_tftp_upload_is_verified_by_admin_api(stack, tmp_path):
    payload = b"integrity\x00payload" * 500
    local = tmp_path / "source.bin"
    local.write_bytes(payload)
    expected = sha(local.read_bytes())

    client = TFTPClient(
        "127.0.0.1", stack["port"], timeout=2, windowsize=4
    )
    assert client.upload(local, "source.bin") == len(payload)

    result = stack["admin"].verify("source.bin", expected)
    assert result["status"] == "VERIFIED"
    assert result["actual"] == expected


def test_tftp_download_matches_server_sha256(stack, tmp_path):
    payload = bytes(range(256)) * 300
    (stack["root"] / "download.bin").write_bytes(payload)
    expected = stack["admin"].hash("download.bin")
    assert expected == sha(payload)

    client = TFTPClient("127.0.0.1", stack["port"], timeout=2, windowsize=8)
    local = tmp_path / "download.bin"
    assert client.download("download.bin", local) == len(payload)
    assert sha(local.read_bytes()) == expected
    assert stack["admin"].verify("download.bin", expected)["status"] == "VERIFIED"


def test_tampered_file_reports_mismatch(stack, tmp_path):
    payload = b"original"
    (stack["root"] / "tamper.bin").write_bytes(payload)
    expected = sha(payload)
    assert stack["admin"].verify("tamper.bin", expected)["status"] == "VERIFIED"

    with open(stack["root"] / "tamper.bin", "ab") as file:
        file.write(b"tampered")

    result = stack["admin"].verify("tamper.bin", expected)
    assert result["status"] == "MISMATCH"
    assert result["actual"] != expected


def test_admin_api_upload_then_tftp_download(stack, tmp_path):
    payload = b"uploaded through admin api" * 100
    expected = sha(payload)
    result = stack["admin"].upload("api/upload.bin", payload)
    assert result["sha256"] == expected

    client = TFTPClient("127.0.0.1", stack["port"], timeout=2, windowsize=4)
    local = tmp_path / "api-download.bin"
    assert client.download("api/upload.bin", local) == len(payload)
    assert sha(local.read_bytes()) == expected
