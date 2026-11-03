"""Test Database, AuthService, MetricsService và ManagedHooks (tích hợp với TFTP server)."""

import hashlib
import os
import time

import pytest
from helpers import make_client, running_server

from auth import ADMIN, GUEST, ROLE_PERMISSIONS, USER, AuthError, AuthService, PermissionDenied
from database import Database, DatabaseError
from metrics import MetricsService
from pxe_config import PXEManager
from storage_manager import ManagedHooks, StorageManager
from tftp_transfer import RemoteError

FAST = {"iterations": 1000}  # PBKDF2 nhẹ để test nhanh


def make_auth(**kwargs):
    options = {**FAST, **kwargs}
    return AuthService(Database(), "a-test-secret-with-enough-length", **options)


def transfer(**overrides):
    record = {
        "op": "RRQ", "filename": "f.bin", "client": "127.0.0.1:1", "path": "f.bin",
        "status": "ok", "bytes": 1000, "blocks": 2, "retransmits": 0, "blksize": 512,
        "windowsize": 1, "duration": 0.5, "throughput_mbps": 0.016, "started_at": 100.0,
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #


def test_users_are_unique_case_insensitively():
    db = Database()
    db.add_user("Alice", "h", "user")
    with pytest.raises(DatabaseError):
        db.add_user("alice", "h", "user")
    assert db.get_user("ALICE")["username"] == "Alice"


def test_invalid_role_is_rejected_by_schema():
    with pytest.raises(DatabaseError):
        Database().add_user("bob", "h", "superuser")


def test_update_and_delete_user():
    db = Database()
    uid = db.add_user("carol", "h", "user")
    assert db.update_user(uid, role="admin", disabled=1)
    row = db.get_user_by_id(uid)
    assert row["role"] == "admin" and row["disabled"] == 1
    with pytest.raises(DatabaseError):
        db.update_user(uid, username="hacker")  # cột không cho phép sửa
    assert db.delete_user(uid) and db.get_user_by_id(uid) is None
    assert db.update_user(999, role="user") is False


def test_list_users_never_exposes_password_hash():
    db = Database()
    db.add_user("dave", "secret-hash", "user")
    assert "password_hash" not in db.list_users()[0]


def test_transfer_insert_filter_and_order():
    db = Database()
    db.add_transfer(transfer(filename="a.bin", started_at=1))
    db.add_transfer(transfer(filename="b.bin", op="WRQ", started_at=2))
    db.add_transfer(transfer(filename="c.bin", status="error", error="1: File not found",
                             started_at=3))
    assert [r["filename"] for r in db.list_transfers()] == ["c.bin", "b.bin", "a.bin"]
    assert [r["filename"] for r in db.list_transfers(status="error")] == ["c.bin"]
    assert [r["filename"] for r in db.list_transfers(op="wrq")] == ["b.bin"]
    assert [r["filename"] for r in db.list_transfers(limit=1, offset=1)] == ["b.bin"]


def test_filename_filter_escapes_like_wildcards():
    db = Database()
    db.add_transfer(transfer(filename="100%_done.bin"))
    db.add_transfer(transfer(filename="other.bin"))
    assert [r["filename"] for r in db.list_transfers(filename="%")] == ["100%_done.bin"]
    assert [r["filename"] for r in db.list_transfers(filename="_")] == ["100%_done.bin"]
    assert len(db.list_transfers(filename="bin")) == 2


def test_sql_injection_in_filters_is_harmless():
    db = Database()
    db.add_transfer(transfer())
    assert db.list_transfers(filename="x'; DROP TABLE transfers; --") == []
    assert len(db.list_transfers()) == 1


def test_transfer_summary_and_prune():
    db = Database()
    db.add_transfer(transfer(op="RRQ", bytes=100, started_at=1))
    db.add_transfer(transfer(op="WRQ", bytes=50, started_at=2))
    db.add_transfer(transfer(status="error", bytes=999, retransmits=4, started_at=3))
    summary = db.transfer_summary()
    assert (summary["total"], summary["ok"], summary["failed"]) == (3, 2, 1)
    assert (summary["bytes_read"], summary["bytes_written"], summary["retransmits"]) == (100, 50, 4)
    assert db.prune_transfers(keep_last=1) == 2
    assert len(db.list_transfers()) == 1


def test_database_persists_to_file(tmp_path):
    path = tmp_path / "sub" / "t.db"
    db = Database(path)
    db.add_transfer(transfer(filename="persisted.bin"))
    db.close()
    assert Database(path).list_transfers()[0]["filename"] == "persisted.bin"


# --------------------------------------------------------------------------- #
# AuthService
# --------------------------------------------------------------------------- #


def test_password_hash_is_salted_and_verifiable():
    auth = make_auth()
    first, second = auth.hash_password("correct horse"), auth.hash_password("correct horse")
    assert first != second
    assert auth.verify_password("correct horse", first)
    assert not auth.verify_password("wrong", first)
    assert not auth.verify_password("x", "garbage")
    assert not auth.verify_password("x", "md5$1$a$b")


@pytest.mark.parametrize("username", ["ab", "has space", "a" * 33, "bad/name", ""])
def test_create_user_rejects_bad_usernames(username):
    with pytest.raises(AuthError):
        make_auth().create_user(username, "longenough1")


def test_create_user_rejects_weak_password_unknown_role_and_duplicates():
    auth = make_auth()
    with pytest.raises(AuthError):
        auth.create_user("alice", "short")
    with pytest.raises(AuthError):
        auth.create_user("alice", "longenough1", "root")
    auth.create_user("alice", "longenough1")
    with pytest.raises(AuthError):
        auth.create_user("ALICE", "longenough1")


def test_login_success_and_failure_messages_are_identical():
    auth = make_auth()
    auth.create_user("alice", "longenough1", USER)
    token, user = auth.login("alice", "longenough1")
    assert user.username == "alice" and auth.user_from_token(token).id == user.id
    with pytest.raises(AuthError) as wrong_password:
        auth.login("alice", "nope-nope-nope")
    with pytest.raises(AuthError) as unknown_user:
        auth.login("ghost", "nope-nope-nope")
    assert str(wrong_password.value) == str(unknown_user.value)  # không lộ tài khoản có tồn tại


def test_disabled_user_cannot_login_or_use_existing_token():
    auth = make_auth()
    user = auth.create_user("alice", "longenough1")
    auth.create_user("root-admin", "longenough1", ADMIN)
    token, _ = auth.login("alice", "longenough1")
    auth.update_user(user.id, disabled=True)
    with pytest.raises(AuthError):
        auth.login("alice", "longenough1")
    with pytest.raises(AuthError):
        auth.user_from_token(token)


def test_token_tampering_expiry_and_garbage():
    auth = make_auth(token_ttl=60)
    user = auth.create_user("alice", "longenough1")
    token = auth.issue_token(user, now=1000)
    assert auth.user_from_token(token, now=1059).username == "alice"
    with pytest.raises(AuthError, match="expired"):
        auth.user_from_token(token, now=1060)
    payload, signature = token.split(".")
    forged = payload[:-2] + ("AA" if not payload.endswith("AA") else "BB") + "." + signature
    for bad in (forged, token + "x", "", "a.b.c", "....", "not-a-token", payload + "."):
        with pytest.raises(AuthError):
            auth.user_from_token(bad, now=1000)


def test_token_from_other_secret_is_rejected():
    one, other = make_auth(), AuthService(Database(), "a-completely-different-secret", **FAST)
    user = one.create_user("alice", "longenough1")
    other.create_user("alice", "longenough1")
    with pytest.raises(AuthError):
        other.user_from_token(one.issue_token(user))


def test_short_secret_is_refused():
    with pytest.raises(ValueError):
        AuthService(Database(), "short")


def test_role_change_takes_effect_for_existing_tokens():
    auth = make_auth()
    admin = auth.create_user("boss", "longenough1", ADMIN)
    user = auth.create_user("alice", "longenough1", ADMIN)
    token = auth.issue_token(user)
    auth.update_user(user.id, role=GUEST)
    assert auth.user_from_token(token).role == GUEST
    assert admin.id != user.id


def test_brute_force_lockout_then_recovery():
    auth = make_auth(max_failures=3, lockout_seconds=0.2)
    auth.create_user("alice", "longenough1")
    for _ in range(3):
        with pytest.raises(AuthError):
            auth.authenticate("alice", "bad-password")
    with pytest.raises(AuthError, match="Too many"):
        auth.authenticate("alice", "longenough1")  # đúng mật khẩu nhưng đang bị khóa
    time.sleep(0.25)
    assert auth.authenticate("alice", "longenough1").username == "alice"


def test_successful_login_resets_failure_counter():
    auth = make_auth(max_failures=3, lockout_seconds=60)
    auth.create_user("alice", "longenough1")
    for _ in range(2):
        with pytest.raises(AuthError):
            auth.authenticate("alice", "bad-password")
    auth.authenticate("alice", "longenough1")
    for _ in range(2):
        with pytest.raises(AuthError):
            auth.authenticate("alice", "bad-password")
    assert auth.authenticate("alice", "longenough1")  # chưa chạm ngưỡng 3 lần liên tiếp


def test_last_active_admin_cannot_be_removed():
    auth = make_auth()
    admin = auth.create_user("boss", "longenough1", ADMIN)
    with pytest.raises(AuthError, match="last active admin"):
        auth.update_user(admin.id, role=USER)
    with pytest.raises(AuthError, match="last active admin"):
        auth.update_user(admin.id, disabled=True)
    with pytest.raises(AuthError, match="last active admin"):
        auth.delete_user(admin.id)
    second = auth.create_user("boss2", "longenough1", ADMIN)
    auth.update_user(admin.id, role=USER)  # đã có admin thứ hai nên được phép
    with pytest.raises(AuthError):
        auth.delete_user(second.id)


def test_update_user_validation():
    auth = make_auth()
    user = auth.create_user("alice", "longenough1")
    with pytest.raises(AuthError):
        auth.update_user(user.id, role="root")
    with pytest.raises(AuthError):
        auth.update_user(user.id, password="short")
    with pytest.raises(AuthError):
        auth.update_user(999, role=USER)
    auth.update_user(user.id, password="a-new-password")
    assert auth.authenticate("alice", "a-new-password")


def test_permission_matrix():
    auth = make_auth()
    admin = auth.create_user("adm", "longenough1", ADMIN)
    user = auth.create_user("usr", "longenough1", USER)
    guest = auth.create_user("gst", "longenough1", GUEST)
    assert all(admin.can(p) for p in ROLE_PERMISSIONS[ADMIN])
    assert user.can("files:write") and not user.can("files:delete")
    assert not user.can("users:manage") and not user.can("metrics:read")
    assert guest.can("files:read") and not guest.can("files:write")
    assert not guest.can("transfers:read")
    auth.require(admin, "users:manage")
    with pytest.raises(PermissionDenied):
        auth.require(guest, "files:write")
    with pytest.raises(PermissionDenied):
        auth.require(user, "files:delete")


def test_ensure_admin_creates_once():
    auth = make_auth()
    user, generated = auth.ensure_admin()
    assert user.role == ADMIN and generated and len(generated) >= 12
    assert auth.authenticate("admin", generated).id == user.id
    again, none = auth.ensure_admin()
    assert again.id == user.id and none is None
    assert auth.db.count_users() == 1


def test_ensure_admin_with_given_password_returns_none():
    auth = make_auth()
    _, generated = auth.ensure_admin(password="my-chosen-password")
    assert generated is None
    assert auth.authenticate("admin", "my-chosen-password")


# --------------------------------------------------------------------------- #
# MetricsService
# --------------------------------------------------------------------------- #


def test_metrics_counts_transfers_bytes_and_errors():
    clock = [1000.0]
    metrics = MetricsService(clock=lambda: clock[0])
    metrics.record_transfer(transfer(op="RRQ", bytes=1_000_000, duration=1.0, retransmits=2))
    metrics.record_transfer(transfer(op="WRQ", bytes=500_000, duration=1.0, filename="up.bin"))
    metrics.record_transfer(transfer(status="error", error="1: File not found", bytes=0))
    metrics.record_transfer(transfer(status="error", error="timeout", bytes=0))
    clock[0] = 1030.0
    snap = metrics.snapshot()
    assert snap["uptime_seconds"] == 30
    assert snap["transfers"] == {
        "total": 4, "ok": 2, "failed": 2, "read": 3, "write": 1, "success_rate": 0.5,
    }
    assert snap["bytes"] == {"read": 1_000_000, "written": 500_000, "total": 1_500_000,
                             "last_minute": 1_500_000}
    assert snap["retransmits"] == 2
    assert snap["avg_throughput_mbps"] == 6.0  # 1.5 MB * 8 / 2 s
    assert snap["errors"] == {"1": 1, "timeout": 1}
    assert snap["top_files"][0]["filename"] == "f.bin"


def test_metrics_last_minute_window_expires():
    clock = [0.0]
    metrics = MetricsService(clock=lambda: clock[0])
    metrics.record_transfer(transfer(bytes=100))
    clock[0] = 61.0
    assert metrics.snapshot()["bytes"]["last_minute"] == 0


def test_metrics_empty_snapshot_and_active_provider():
    metrics = MetricsService()
    snap = metrics.snapshot()
    assert snap["transfers"]["total"] == 0 and snap["transfers"]["success_rate"] == 1.0
    assert snap["avg_throughput_mbps"] == 0 and snap["active_sessions"] == 0
    metrics.set_active_provider(lambda: 7)
    assert metrics.snapshot()["active_sessions"] == 7


def test_metrics_prometheus_text():
    metrics = MetricsService()
    metrics.record_transfer(transfer(bytes=42))
    text = metrics.to_prometheus()
    assert 'tftp_transfers_total{status="ok"} 1' in text
    assert 'tftp_bytes_total{direction="read"} 42' in text
    assert text.endswith("\n")


# --------------------------------------------------------------------------- #
# ManagedHooks + TFTP server thật
# --------------------------------------------------------------------------- #


def build_stack(tmp_path, with_pxe=False):
    storage = StorageManager(tmp_path / "root")
    db = Database()
    metrics = MetricsService()
    pxe = PXEManager(storage) if with_pxe else None
    if pxe:
        pxe.update(enabled=True, next_server="127.0.0.1")
    hooks = ManagedHooks(storage, db=db, metrics=metrics, pxe=pxe)
    return storage, db, metrics, pxe, hooks


def test_upload_is_recorded_with_sha256_and_relative_path(tmp_path):
    storage, db, metrics, _, hooks = build_stack(tmp_path)
    data = os.urandom(40_000)
    (tmp_path / "src").write_bytes(data)
    with running_server(storage.root, hooks=hooks) as server:
        make_client(server).put(tmp_path / "src", "up.bin")
        assert server.wait_idle(5)
    row = db.list_transfers()[0]
    assert row["op"] == "WRQ" and row["status"] == "ok" and row["bytes"] == len(data)
    assert row["sha256"] == hashlib.sha256(data).hexdigest()
    assert row["path"] == "up.bin"  # không lộ đường dẫn tuyệt đối
    assert str(tmp_path) not in row["path"]
    snap = metrics.snapshot()
    assert snap["transfers"]["write"] == 1 and snap["bytes"]["written"] == len(data)


def test_download_and_failure_are_recorded(tmp_path):
    storage, db, metrics, _, hooks = build_stack(tmp_path)
    (storage.root / "f.bin").write_bytes(b"x" * 3000)
    with running_server(storage.root, hooks=hooks) as server:
        make_client(server).get("f.bin", tmp_path / "o")
        with pytest.raises(RemoteError):
            make_client(server).get("missing.bin", tmp_path / "o2")
        with pytest.raises(RemoteError):
            make_client(server).get("../escape", tmp_path / "o3")
        assert server.wait_idle(5)
    rows = db.list_transfers()
    assert sorted(r["status"] for r in rows) == ["error", "error", "ok"]
    assert db.list_transfers(status="ok")[0]["sha256"] == hashlib.sha256(b"x" * 3000).hexdigest()
    assert metrics.snapshot()["transfers"] == {
        "total": 3, "ok": 1, "failed": 2, "read": 3, "write": 0, "success_rate": 0.3333,
    }


def test_sha256_is_cached_until_file_changes(tmp_path):
    storage, db, _, _, hooks = build_stack(tmp_path)
    (storage.root / "f.bin").write_bytes(b"first")
    with running_server(storage.root, hooks=hooks) as server:
        client = make_client(server)
        client.get("f.bin", tmp_path / "a")
        assert server.wait_idle(5)
        assert len(hooks._hash_cache) == 1
        client.get("f.bin", tmp_path / "b")
        assert server.wait_idle(5)
        assert len(hooks._hash_cache) == 1  # lần hai dùng cache
        (storage.root / "f.bin").write_bytes(b"second version")
        client.get("f.bin", tmp_path / "c")
        assert server.wait_idle(5)
    newest = db.list_transfers()[0]
    assert newest["sha256"] == hashlib.sha256(b"second version").hexdigest()


def test_pxe_boot_dir_is_read_only_over_tftp(tmp_path):
    storage, db, _, pxe, hooks = build_stack(tmp_path, with_pxe=True)
    (pxe.boot_dir_path() / "pxelinux.0").write_bytes(b"ORIGINAL")
    (tmp_path / "evil").write_bytes(b"EVIL")
    with running_server(storage.root, hooks=hooks, allow_overwrite=True) as server:
        for target in ("pxe/pxelinux.0", "PXE/new.bin", "pxe/sub/x.bin"):
            with pytest.raises(RemoteError) as exc:
                make_client(server).put(tmp_path / "evil", target)
            assert exc.value.code == 2
        make_client(server).put(tmp_path / "evil", "elsewhere.bin")  # ngoài boot dir thì được
        assert server.wait_idle(5)
    assert (pxe.boot_dir_path() / "pxelinux.0").read_bytes() == b"ORIGINAL"


def test_pxe_bare_filename_falls_back_to_boot_dir(tmp_path):
    storage, _, _, pxe, hooks = build_stack(tmp_path, with_pxe=True)
    (pxe.boot_dir_path() / "undionly.kpxe").write_bytes(b"IPXE" * 50)
    with running_server(storage.root, hooks=hooks, pxe_mode=True) as server:
        make_client(server).get("UNDIONLY.KPXE", tmp_path / "o")  # tên trần, sai hoa thường
        assert (tmp_path / "o").read_bytes() == b"IPXE" * 50


def test_pxe_fallback_cannot_escape_boot_dir(tmp_path):
    storage, _, _, pxe, hooks = build_stack(tmp_path, with_pxe=True)
    (storage.root / "private.txt").write_bytes(b"private")
    with running_server(storage.root, hooks=hooks, pxe_mode=True) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).get("pxe/../private.txt", tmp_path / "o")
        assert exc.value.code == 2


def test_hook_failure_in_database_does_not_break_transfer(tmp_path):
    storage, db, metrics, _, hooks = build_stack(tmp_path)
    db.close()  # DB hỏng: on_transfer sẽ ném lỗi, server chỉ log
    (storage.root / "f.bin").write_bytes(b"data")
    with running_server(storage.root, hooks=hooks) as server:
        make_client(server).get("f.bin", tmp_path / "o")
        assert (tmp_path / "o").read_bytes() == b"data"
        make_client(server).get("f.bin", tmp_path / "o2")
        assert (tmp_path / "o2").read_bytes() == b"data"
