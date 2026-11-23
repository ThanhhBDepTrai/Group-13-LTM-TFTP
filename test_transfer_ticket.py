"""Vé truyền tệp 1 lần: không vé -> ERROR 2; có vé -> truyền được và vé tự hủy sau 1 lần dùng.

Cần backend của TV2 (nhánh feat/ticket-versioning-backend); tự bỏ qua nếu chưa cấu hình.
Xem tests/contract_helpers.py để biết cách bật.
"""

import os
import socket
import uuid

import pytest
from contract_helpers import RemoteError, require_backend

from tftp_packet import OP_ERROR, pack_rrq, pack_wrq, unpack_packet

UNAUTHORIZED = "Access violation: Unauthorized transfer"


def unique(name: str) -> str:
    return f"ticket_{uuid.uuid4().hex[:8]}_{name}"


def raw_exchange(backend, packet: bytes):
    """Gửi một RRQ/WRQ thô và trả về gói trả lời đầu tiên đã giải mã."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(3)
    try:
        sock.sendto(packet, (backend.host, backend.port))
        return unpack_packet(sock.recvfrom(70000)[0])
    finally:
        sock.close()


def upload(backend, tmp_path, name: str, data: bytes) -> None:
    assert backend.authorize(name, "write") == 200
    source = tmp_path / "upload.bin"
    source.write_bytes(data)
    backend.client().put(source, name)


def test_rrq_without_ticket_is_rejected_with_error_2():
    backend = require_backend()
    reply = raw_exchange(backend, pack_rrq(unique("no-ticket.bin")))
    assert reply.opcode == OP_ERROR and reply.error_code == 2
    assert reply.error_msg == UNAUTHORIZED


def test_wrq_without_ticket_is_rejected_with_error_2():
    backend = require_backend()
    reply = raw_exchange(backend, pack_wrq(unique("no-ticket.bin")))
    assert reply.opcode == OP_ERROR and reply.error_code == 2
    assert reply.error_msg == UNAUTHORIZED


def test_authorize_requires_a_valid_token():
    backend = require_backend()
    assert backend.authorize(unique("x.bin"), "read", token=None) in (401, 403)
    assert backend.authorize(unique("x.bin"), "read", token="not-a-real-token") in (401, 403)


def test_authorize_returns_403_when_rbac_denies():
    backend = require_backend()
    if not backend.forbidden_token:
        pytest.skip("Chưa đặt TFTP_TEST_FORBIDDEN_TOKEN")
    assert backend.authorize(unique("x.bin"), "write", token=backend.forbidden_token) == 403
    assert backend.authorize(unique("x.bin"), "read", token=backend.forbidden_token) == 403


def test_authorize_rejects_malformed_requests():
    backend = require_backend()
    status, _ = backend.call("POST", "/api/v1/transfers/authorize",
                             {"filename": unique("x.bin"), "mode": "delete"})
    assert status in (400, 422)
    status, _ = backend.call("POST", "/api/v1/transfers/authorize", {"mode": "read"})
    assert status in (400, 422)


def test_ticket_allows_one_download_and_then_expires(tmp_path):
    backend = require_backend()
    name = unique("single-use.bin")
    data = os.urandom(5000)
    upload(backend, tmp_path, name, data)

    assert backend.authorize(name, "read") == 200
    backend.client().get(name, tmp_path / "first.bin")
    assert (tmp_path / "first.bin").read_bytes() == data

    # Vé đã dùng xong: lần tải thứ hai không xin vé mới phải bị từ chối
    with pytest.raises(RemoteError) as exc:
        backend.client().get(name, tmp_path / "second.bin")
    assert exc.value.code == 2
    assert not (tmp_path / "second.bin").exists()


def test_a_new_ticket_allows_another_download(tmp_path):
    backend = require_backend()
    name = unique("again.bin")
    data = os.urandom(1500)
    upload(backend, tmp_path, name, data)
    for attempt in range(2):
        assert backend.authorize(name, "read") == 200
        backend.client().get(name, tmp_path / f"copy{attempt}.bin")
        assert (tmp_path / f"copy{attempt}.bin").read_bytes() == data


def test_read_ticket_does_not_authorize_upload_and_other_files(tmp_path):
    backend = require_backend()
    name = unique("bound.bin")
    upload(backend, tmp_path, name, b"x" * 100)
    assert backend.authorize(name, "read") == 200
    # Vé gắn với (IP, tên file, chế độ): ghi đè bằng vé đọc, hoặc đọc file khác, đều bị từ chối
    reply = raw_exchange(backend, pack_wrq(name))
    assert reply.opcode == OP_ERROR and reply.error_code == 2
    reply = raw_exchange(backend, pack_rrq(unique("other.bin")))
    assert reply.opcode == OP_ERROR and reply.error_code == 2
