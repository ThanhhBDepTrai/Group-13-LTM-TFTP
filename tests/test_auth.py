"""Kiểm thử AuthManager: băm mật khẩu, token, role và phân quyền."""

import time

import pytest

from auth import ROLE_PERMISSIONS, AuthError, AuthManager


@pytest.fixture()
def auth(tmp_path):
    manager = AuthManager(tmp_path / "admin.db")
    manager.create_user("admin", "admin-pass", "admin")
    manager.create_user("alice", "alice-pass", "user")
    manager.create_user("guest", "guest-pass", "guest")
    return manager


def test_no_plaintext_password_in_database(auth):
    import sqlite3

    with sqlite3.connect(auth.db_path) as connection:
        row = connection.execute(
            "SELECT password_hash, salt FROM users WHERE username='alice'"
        ).fetchone()
    assert "alice-pass" not in row[0]
    assert len(row[0]) == 64
    assert row[1]


def test_login_and_validate_token(auth):
    result = auth.login("alice", "alice-pass")
    assert result["role"] == "user"
    user = auth.validate_token(result["token"])
    assert user == {"username": "alice", "role": "user"}


def test_wrong_password_and_unknown_user(auth):
    for username, password in (("alice", "wrong"), ("nobody", "x")):
        with pytest.raises(AuthError) as exc:
            auth.login(username, password)
        assert exc.value.status == 401


def test_logout_revokes_token(auth):
    result = auth.login("alice", "alice-pass")
    auth.revoke_token(result["token"])
    with pytest.raises(AuthError):
        auth.validate_token(result["token"])


def test_token_expiry(auth):
    auth.token_ttl = 0
    result = auth.login("alice", "alice-pass")
    time.sleep(1.1)
    with pytest.raises(AuthError):
        auth.validate_token(result["token"])


def test_roles_have_expected_permissions():
    assert "users.manage" in ROLE_PERMISSIONS["admin"]
    assert "files.upload" in ROLE_PERMISSIONS["user"]
    assert "files.delete" not in ROLE_PERMISSIONS["user"]
    assert "files.download" in ROLE_PERMISSIONS["guest"]
    assert "files.upload" not in ROLE_PERMISSIONS["guest"]


def test_require_permission(auth):
    admin = {"username": "admin", "role": "admin"}
    guest = {"username": "guest", "role": "guest"}
    auth.require(admin, "users.manage")
    with pytest.raises(AuthError) as exc:
        auth.require(guest, "files.upload")
    assert exc.value.status == 403


def test_create_duplicate_and_invalid_users(auth):
    with pytest.raises(AuthError) as exc:
        auth.create_user("alice", "another", "user")
    assert exc.value.status == 409
    with pytest.raises(AuthError):
        auth.create_user("bad name", "password", "user")
    with pytest.raises(AuthError):
        auth.create_user("bob", "x", "user")
    with pytest.raises(AuthError):
        auth.create_user("bob", "password", "superuser")


def test_list_users_has_no_secrets(auth):
    users = auth.list_users()
    assert {user["username"] for user in users} == {"admin", "alice", "guest"}
    assert all("password_hash" not in user and "salt" not in user for user in users)


def test_delete_last_admin_rejected(auth):
    with pytest.raises(AuthError) as exc:
        auth.delete_user("admin")
    assert exc.value.status == 409
    auth.create_user("admin2", "admin2-pass", "admin")
    auth.delete_user("admin")
    assert {user["username"] for user in auth.list_users()} == {
        "admin2",
        "alice",
        "guest",
    }


def test_set_role_and_active(auth):
    assert auth.set_role("alice", "guest")["role"] == "guest"
    assert auth.set_active("alice", False)["active"] == 0
    with pytest.raises(AuthError):
        auth.login("alice", "alice-pass")


def test_cannot_demote_or_disable_last_admin(auth):
    with pytest.raises(AuthError) as exc:
        auth.set_role("admin", "user")
    assert exc.value.status == 409
    with pytest.raises(AuthError) as exc:
        auth.set_active("admin", False)
    assert exc.value.status == 409


def test_change_password_invalidates_tokens(auth):
    result = auth.login("alice", "alice-pass")
    auth.change_password("alice", "new-pass")
    with pytest.raises(AuthError):
        auth.validate_token(result["token"])
    assert auth.login("alice", "new-pass")["role"] == "user"


def test_default_admin_bootstrap(tmp_path):
    manager = AuthManager(tmp_path / "fresh.db")
    created = manager.ensure_default_admin("secret")
    assert created == ("admin", "secret")
    assert manager.ensure_default_admin("other") is None
    assert manager.login("admin", "secret")["role"] == "admin"
