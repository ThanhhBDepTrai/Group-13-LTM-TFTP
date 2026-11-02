"""Kiểm thử Admin API qua HTTP thật (không mock handler)."""

import hashlib

import pytest

from admin_api import AdminAPIServer
from admin_client import AdminAPIError, AdminClient
from auth import AuthManager
from metrics import MetricsCollector
from packet_trace import PacketTracer
from pxe_config import PxeManager
from storage_manager import StorageManager
from tftp_packet import pack_rrq


@pytest.fixture()
def api(tmp_path):
    storage = StorageManager(tmp_path / "root")
    storage.write_bytes("hello.txt", b"hello world")
    auth = AuthManager(tmp_path / "admin.db")
    auth.create_user("admin", "admin-pass", "admin")
    auth.create_user("alice", "alice-pass", "user")
    auth.create_user("guest", "guest-pass", "guest")
    metrics = MetricsCollector()
    tracer = PacketTracer()
    pxe = PxeManager(storage, tmp_path / "pxe_profiles.json")
    server = AdminAPIServer(
        storage,
        auth,
        metrics=metrics,
        tracer=tracer,
        pxe=pxe,
        host="127.0.0.1",
        port=0,
    )
    server.start()
    client = AdminClient(server.url)
    try:
        yield server, client, storage, auth, metrics, tracer
    finally:
        server.stop()


def login(client, username="admin", password="admin-pass"):
    return client.login(username, password)


def test_health_needs_no_auth(api):
    _server, client, *_ = api
    assert client.health()["status"] == "ok"


def test_login_me_logout(api):
    server, client, *_ = api
    result = login(client)
    assert result["role"] == "admin"
    assert client.me()["username"] == "admin"
    client.logout()
    with pytest.raises(AdminAPIError) as exc:
        client.me()
    assert exc.value.status == 401
    assert server.url.startswith("http://127.0.0.1:")


def test_requires_auth(api):
    _server, client, *_ = api
    with pytest.raises(AdminAPIError) as exc:
        client.list_dir()
    assert exc.value.status == 401


def test_upload_list_download_hash_verify(api):
    _server, client, storage, *_ = api
    login(client)
    payload = b"binary\x00data" * 100
    digest = hashlib.sha256(payload).hexdigest()
    result = client.upload("sub/up.bin", payload)
    assert result["sha256"] == digest
    assert storage.read_bytes("sub/up.bin") == payload

    listing = client.list_dir("sub")
    assert [entry["name"] for entry in listing["entries"]] == ["up.bin"]
    assert client.download("sub/up.bin") == payload
    assert client.hash("sub/up.bin") == digest
    assert client.verify("sub/up.bin", digest)["status"] == "VERIFIED"
    assert client.verify("sub/up.bin", "0" * 64)["status"] == "MISMATCH"
    assert client.metadata("sub/up.bin", include_hash=True)["entry"]["sha256"] == digest


def test_rename_mkdir_search_delete(api):
    _server, client, storage, *_ = api
    login(client)
    client.mkdir("docs/2026")
    client.upload("docs/2026/report.txt", b"report")
    assert client.search("report")["entries"][0]["relative_path"] == "docs/2026/report.txt"
    client.rename("docs/2026/report.txt", "docs/2026/final.txt")
    assert storage.exists("docs/2026/final.txt")
    client.delete("docs/2026/final.txt")
    assert not storage.exists("docs/2026/final.txt")
    client.delete("docs", recursive=True)
    assert not storage.exists("docs")


def test_path_traversal_rejected_by_api(api):
    _server, client, storage, *_ = api
    login(client)
    (storage.root.parent / "secret.txt").write_bytes(b"secret")
    with pytest.raises(AdminAPIError) as exc:
        client.download("../secret.txt")
    assert exc.value.status == 400
    with pytest.raises(AdminAPIError) as exc:
        client.upload("../escape.txt", b"x")
    assert exc.value.status == 400


def test_user_and_guest_permissions(api):
    _server, client, storage, *_ = api

    login(client, "alice", "alice-pass")
    client.upload("alice.txt", b"ok")
    assert storage.exists("alice.txt")
    with pytest.raises(AdminAPIError) as exc:
        client.delete("alice.txt")
    assert exc.value.status == 403
    with pytest.raises(AdminAPIError) as exc:
        client.list_users()
    assert exc.value.status == 403

    client.token = None
    login(client, "guest", "guest-pass")
    assert client.download("hello.txt") == b"hello world"
    with pytest.raises(AdminAPIError) as exc:
        client.upload("guest.txt", b"no")
    assert exc.value.status == 403
    with pytest.raises(AdminAPIError) as exc:
        client.delete("hello.txt")
    assert exc.value.status == 403


def test_admin_user_management(api):
    _server, client, *_ = api
    login(client)
    created = client.create_user("bob", "bob-pass", "user")
    assert created["user"]["role"] == "user"
    assert "bob" in {user["username"] for user in client.list_users()}
    client.delete_user("bob")
    assert "bob" not in {user["username"] for user in client.list_users()}


def test_dashboard_and_packets(api):
    _server, client, _storage, _auth, metrics, tracer = api
    metrics.record(
        client_ip="127.0.0.1",
        filename="hello.txt",
        direction="download",
        bytes_total=11,
        duration=0.5,
        retries=1,
        timeouts=1,
    )
    tracer.record_raw(
        pack_rrq("hello.txt"), source="127.0.0.1:1234", destination="127.0.0.1:6969",
        direction="Client -> Server",
    )
    login(client)
    dashboard = client.dashboard()
    assert dashboard["stats"]["total_transfers"] == 1
    assert dashboard["stats"]["bytes_download"] == 11
    assert dashboard["recent"][0]["filename"] == "hello.txt"
    packets = client.packets()
    assert packets["events"][0]["opcode"] == "RRQ"
    assert packets["counts"]["RRQ"] == 1


def test_invalid_login_and_json(api):
    _server, client, *_ = api
    with pytest.raises(AdminAPIError) as exc:
        client.login("admin", "wrong")
    assert exc.value.status == 401


def test_body_too_large(api):
    server, client, *_ = api
    login(client)
    client.upload("small.bin", b"x" * 10)
    server.max_body = 5
    with pytest.raises(AdminAPIError) as exc:
        client.upload("big.bin", b"x" * 100)
    assert exc.value.status == 413


def test_role_active_password_and_audit(api):
    _server, client, storage, _auth, *_ = api
    login(client)
    assert client.set_user_role("alice", "guest")["role"] == "guest"
    assert client.set_user_active("alice", False)["active"] == 0

    with pytest.raises(AdminAPIError) as exc:
        client.set_user_role("admin", "user")
    assert exc.value.status == 409

    assert client.change_password("alice-new-pass", username="alice")["status"] == "password_changed"
    assert client.set_user_active("alice", True)["active"] == 1
    events = client.audit_log()
    assert any(event["action"] == "user.set_role" for event in events)

    # Admin đổi mật khẩu của mình: token cũ bị thu hồi, phải đăng nhập lại.
    client.change_password("admin-new-pass")
    with pytest.raises(AdminAPIError) as exc:
        client.me()
    assert exc.value.status == 401
    login(client, "admin", "admin-new-pass")
    assert client.me()["role"] == "admin"
    assert storage.exists("hello.txt")


def test_pxe_profile_endpoints(api):
    server, client, storage, *_ = api
    storage.write_bytes("pxe/boot/undionly.kpxe", b"bios")
    storage.write_bytes("pxe/efi/ipxe.efi", b"uefi")
    login(client)
    profile = client.upsert_pxe_profile(
        {
            "name": "lab",
            "bios_file": "pxe/boot/undionly.kpxe",
            "uefi_file": "pxe/efi/ipxe.efi",
            "default": True,
        },
        require_files=True,
    )
    assert profile["bios_exists"] is True
    assert any(item["name"] == "lab" for item in client.pxe_profiles())
    selected = client.select_pxe_profile("uefi")
    assert selected["name"] == "lab"
    assert selected["boot_file"] == "pxe/efi/ipxe.efi"
    client.delete_pxe_profile("lab")
    assert all(item["name"] != "lab" for item in client.pxe_profiles())
    assert server.pxe is not None
