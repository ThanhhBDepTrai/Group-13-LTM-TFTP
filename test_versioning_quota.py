"""File Versioning (.versions/*.bak) và Quota phòng ban.

Cần backend của TV2 (nhánh feat/ticket-versioning-backend); tự bỏ qua nếu chưa cấu hình.
Xem tests/contract_helpers.py để biết cách bật.
"""

import hashlib
import os
import uuid

import pytest
from contract_helpers import RemoteError, require_backend, restore_body, version_list


def unique(name: str) -> str:
    return f"ver_{uuid.uuid4().hex[:8]}_{name}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(backend, tmp_path, name: str, data: bytes) -> None:
    assert backend.authorize(name, "write") == 200
    source = tmp_path / f"src_{uuid.uuid4().hex[:6]}"
    source.write_bytes(data)
    backend.client().put(source, name)


def get(backend, tmp_path, name: str) -> bytes:
    assert backend.authorize(name, "read") == 200
    target = tmp_path / f"dst_{uuid.uuid4().hex[:6]}"
    backend.client().get(name, target)
    return target.read_bytes()


def list_versions(backend, name: str) -> list[dict]:
    status, payload = backend.call("GET", f"/api/v1/files/{name}/versions")
    assert status == 200, payload
    return version_list(payload)


# --------------------------------------------------------------------------- #
# Versioning
# --------------------------------------------------------------------------- #


def test_first_upload_creates_no_backup(tmp_path):
    backend = require_backend()
    name = unique("fresh.bin")
    put(backend, tmp_path, name, os.urandom(2000))
    assert list_versions(backend, name) == []


def test_overwrite_keeps_the_old_content_as_a_backup(tmp_path):
    backend = require_backend()
    name = unique("doc.bin")
    old, new = os.urandom(3000), os.urandom(4000)
    put(backend, tmp_path, name, old)
    put(backend, tmp_path, name, new)

    versions = list_versions(backend, name)
    assert len(versions) == 1
    entry = versions[0]
    for key in ("timestamp", "size", "sha256", "author"):
        assert key in entry, f"thiếu trường {key} trong {entry}"
    assert entry["sha256"] == sha(old) and entry["size"] == len(old)
    assert get(backend, tmp_path, name) == new  # file chính là bản mới


def test_restore_brings_back_the_exact_old_bytes(tmp_path):
    backend = require_backend()
    name = unique("restore.bin")
    old, new = os.urandom(2500), os.urandom(2600)
    put(backend, tmp_path, name, old)
    put(backend, tmp_path, name, new)

    chosen = list_versions(backend, name)[0]
    status, payload = backend.call("POST", f"/api/v1/files/{name}/restore", restore_body(chosen))
    assert status == 200, payload
    restored = get(backend, tmp_path, name)
    assert restored == old and sha(restored) == chosen["sha256"]


def test_restore_unknown_version_is_rejected(tmp_path):
    backend = require_backend()
    name = unique("norestore.bin")
    put(backend, tmp_path, name, b"a" * 100)
    status, _ = backend.call("POST", f"/api/v1/files/{name}/restore",
                             {"version": "does-not-exist", "timestamp": "0"})
    assert status in (400, 404, 422)


def test_several_overwrites_keep_every_previous_version(tmp_path):
    backend = require_backend()
    name = unique("history.bin")
    contents = [os.urandom(1000 + i) for i in range(4)]
    for data in contents:
        put(backend, tmp_path, name, data)
    hashes = {entry["sha256"] for entry in list_versions(backend, name)}
    assert hashes == {sha(data) for data in contents[:-1]}


def test_versions_endpoint_requires_authentication():
    backend = require_backend()
    status, _ = backend.call("GET", "/api/v1/files/anything.bin/versions", token=None)
    assert status in (401, 403)


# --------------------------------------------------------------------------- #
# Quota
# --------------------------------------------------------------------------- #


def test_quota_endpoint_reports_usage():
    backend = require_backend()
    if not backend.dept_id:
        pytest.skip("Chưa đặt TFTP_TEST_DEPT_ID")
    status, quota = backend.call("GET", f"/api/v1/departments/{backend.dept_id}/quota")
    assert status == 200, quota
    assert {"used_bytes", "limit_bytes", "percent"} <= set(quota)
    assert quota["used_bytes"] >= 0 and quota["limit_bytes"] > 0
    expected = quota["used_bytes"] / quota["limit_bytes"] * 100
    assert abs(quota["percent"] - expected) < 1.0


def test_quota_grows_after_an_upload(tmp_path):
    backend = require_backend()
    if not backend.dept_id:
        pytest.skip("Chưa đặt TFTP_TEST_DEPT_ID")
    path = f"/api/v1/departments/{backend.dept_id}/quota"
    before = backend.call("GET", path)[1]["used_bytes"]
    put(backend, tmp_path, unique("grow.bin"), os.urandom(20_000))
    after = backend.call("GET", path)[1]["used_bytes"]
    assert after >= before + 20_000


def test_upload_over_department_quota_is_blocked(tmp_path):
    backend = require_backend()
    dept = backend.small_quota_dept_id
    if not dept:
        pytest.skip("Chưa đặt TFTP_TEST_SMALL_QUOTA_DEPT_ID (phòng ban có hạn mức nhỏ)")
    quota = backend.call("GET", f"/api/v1/departments/{dept}/quota")[1]
    remaining = quota["limit_bytes"] - quota["used_bytes"]
    assert remaining < 20 * 1024 * 1024, "Hạn mức quá lớn để test; hãy dùng phòng ban nhỏ hơn"

    name = unique("too-big.bin")
    source = tmp_path / "big.bin"
    source.write_bytes(os.urandom(remaining + 1024))
    status = backend.authorize(name, "write")
    if status == 200:  # nếu vé vẫn được cấp thì việc chặn phải xảy ra ở bước ghi
        with pytest.raises(RemoteError) as exc:
            backend.client().put(source, name)
        assert exc.value.code in (2, 3)
    else:
        assert status in (403, 413, 507)
    after = backend.call("GET", f"/api/v1/departments/{dept}/quota")[1]
    assert after["used_bytes"] <= quota["used_bytes"]  # dung lượng không bị vượt


def test_quota_requires_authentication():
    backend = require_backend()
    status, _ = backend.call("GET", "/api/v1/departments/1/quota", token=None)
    assert status in (401, 403)
