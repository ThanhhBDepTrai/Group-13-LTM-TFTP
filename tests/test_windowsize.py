"""Kiểm thử RFC 7440: thương lượng, fallback, truyền cửa sổ, mất ACK/trùng gói."""

import hashlib
import socket
import threading
import time

import pytest

from tftp_client import TFTPClient
from tftp_options import TransferOptions, negotiate_options
from tftp_packet import (
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    TFTPError,
    pack_ack,
    pack_data,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)
from tftp_server import ServerConfig, TFTPServer


def start_server(root, *, windowsize=8, timeout=0.4, retries=4, blksize=512):
    config = ServerConfig(
        host="127.0.0.1",
        port=0,
        root=root,
        timeout=timeout,
        retries=retries,
        max_clients=20,
        overwrite=True,
        blksize=blksize,
        windowsize=windowsize,
        log_level="ERROR",
    )
    server = TFTPServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.02)
    return server, thread, server.sock.getsockname()[1]


def stop_server(server, thread):
    server.stop()
    thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# Unit: negotiation
# --------------------------------------------------------------------------- #


def test_negotiate_accepts_windowsize_and_caps_to_server():
    assert negotiate_options({"windowsize": "4"}, server_windowsize=8) == {
        "windowsize": "4"
    }
    assert negotiate_options({"windowsize": "64"}, server_windowsize=8) == {
        "windowsize": "8"
    }


def test_negotiate_returns_empty_for_unknown_options():
    assert negotiate_options({"foobar": "1"}, server_windowsize=8) == {}


def test_negotiate_tsize_direction():
    up = negotiate_options({"tsize": "123"}, is_upload=True)
    assert up == {"tsize": "123"}
    down = negotiate_options({"tsize": "0"}, is_upload=False, file_size=999)
    assert down == {"tsize": "999"}


@pytest.mark.parametrize("bad", ["0", "65536", "-1", "abc", "", "1.5", " 4"])
def test_negotiate_rejects_invalid_windowsize(bad):
    with pytest.raises(TFTPError) as exc:
        negotiate_options({"windowsize": bad})
    assert exc.value.code == 8


def test_transfer_options_fallback_without_oack():
    opts = TransferOptions.from_oack({}, fallback_blksize=1024, fallback_timeout=2)
    assert opts.blksize == 1024
    assert opts.timeout == 2
    assert opts.windowsize == 1  # RFC 7440 fallback an toàn


def test_transfer_options_parse_oack():
    opts = TransferOptions.from_oack(
        {"blksize": "1456", "timeout": "2", "windowsize": "4", "tsize": "100"}
    )
    assert (opts.blksize, opts.timeout, opts.windowsize, opts.tsize) == (
        1456,
        2.0,
        4,
        100,
    )


# --------------------------------------------------------------------------- #
# Integration: windowsize 1 / 4 / 8, blksize khác nhau
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "blksize,windowsize",
    [(512, 1), (512, 4), (1024, 4), (1456, 8)],
)
def test_roundtrip_with_window_sizes(tmp_path, blksize, windowsize):
    root = tmp_path / "root"
    root.mkdir()
    payload = bytes(i % 251 for i in range(50_000))
    (root / "source.bin").write_bytes(payload)
    server, thread, port = start_server(root, windowsize=16)
    logs: list[str] = []
    try:
        client = TFTPClient(
            "127.0.0.1",
            port,
            timeout=2,
            retries=4,
            blksize=blksize,
            windowsize=windowsize,
            on_log=logs.append,
        )
        out = tmp_path / f"down-{blksize}-{windowsize}.bin"
        assert client.download("source.bin", out) == len(payload)
        assert hashlib.sha256(out.read_bytes()).digest() == hashlib.sha256(payload).digest()
        if windowsize > 1:
            assert "windowsize=1" not in " ".join(logs)

        upload = tmp_path / "upload.bin"
        upload.write_bytes(payload)
        assert client.upload(upload, f"up-{blksize}-{windowsize}.bin") == len(payload)
        assert (root / f"up-{blksize}-{windowsize}.bin").read_bytes() == payload
    finally:
        stop_server(server, thread)


def test_server_falls_back_to_windowsize_one(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.bin").write_bytes(b"x" * 5000)
    server, thread, port = start_server(root, windowsize=1)
    logs: list[str] = []
    try:
        client = TFTPClient(
            "127.0.0.1", port, timeout=2, blksize=512, windowsize=8, on_log=logs.append
        )
        out = tmp_path / "a.bin"
        assert client.download("a.bin", out) == 5000
        assert out.read_bytes() == b"x" * 5000
        assert any("windowsize=1" in line for line in logs)
    finally:
        stop_server(server, thread)


# --------------------------------------------------------------------------- #
# Raw-socket fault injection
# --------------------------------------------------------------------------- #


def test_server_retransmits_unacked_data(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.bin").write_bytes(b"abc" * 400)  # > 1 block: 1200 bytes
    server, thread, port = start_server(root, windowsize=1, timeout=0.2, retries=5)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    try:
        s.sendto(pack_rrq("a.bin"), ("127.0.0.1", port))
        first, peer = s.recvfrom(65539)
        first_pkt = unpack_packet(first)
        assert first_pkt.opcode == OP_DATA
        # Cố tình không ACK -> server phải gửi lại đúng block.
        second, peer2 = s.recvfrom(65539)
        second_pkt = unpack_packet(second)
        assert peer2 == peer
        assert second_pkt.opcode == OP_DATA
        assert second_pkt.block == first_pkt.block
        assert second_pkt.data == first_pkt.data
        s.sendto(pack_ack(first_pkt.block), peer)
        # Nhận DATA cuối và ACK.
        while True:
            raw, _ = s.recvfrom(65539)
            pkt = unpack_packet(raw)
            if pkt.opcode == OP_DATA:
                s.sendto(pack_ack(pkt.block), peer)
                if len(pkt.data) < 512:
                    break
    finally:
        s.close()
        stop_server(server, thread)


def test_server_tolerates_duplicate_data(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    server, thread, port = start_server(root, windowsize=1, timeout=0.5, retries=5)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    payload = b"d" * 512  # chưa phải block cuối vì đúng bằng blksize
    try:
        s.sendto(pack_wrq("dup.bin"), ("127.0.0.1", port))
        raw, peer = s.recvfrom(65539)
        assert unpack_packet(raw).opcode == OP_ACK
        s.sendto(pack_data(1, payload), peer)
        s.sendto(pack_data(1, payload), peer)  # gửi trùng
        # Server ACK cả hai (ACK idempotent) rồi nhận block cuối rỗng.
        s.sendto(pack_data(2, b""), peer)
        while True:
            raw, _ = s.recvfrom(65539)
            pkt = unpack_packet(raw)
            if pkt.opcode == OP_ERROR:
                raise AssertionError(pkt.error_msg)
            if pkt.opcode == OP_ACK and pkt.block == 2:
                break
    finally:
        s.close()
        stop_server(server, thread)
    assert (root / "dup.bin").read_bytes() == payload


def test_wrong_tid_gets_error(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.bin").write_bytes(b"tid")
    server, thread, port = start_server(root, windowsize=1, timeout=0.5, retries=3)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    intruder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    intruder.settimeout(2)
    try:
        s.sendto(pack_rrq("a.bin"), ("127.0.0.1", port))
        raw, peer = s.recvfrom(65539)
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_DATA
        intruder.sendto(pack_ack(pkt.block), peer)
        raw, _ = intruder.recvfrom(65539)
        err = unpack_packet(raw)
        assert err.opcode == OP_ERROR
        assert err.error_code == 5
        # Kết thúc phiên cho sạch.
        s.sendto(pack_ack(pkt.block), peer)
    finally:
        s.close()
        intruder.close()
        stop_server(server, thread)


def test_client_reacks_on_timeout(tmp_path):
    """Client nhận DATA, ACK bị mất, sau timeout phải ACK lại block cuối."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    srv.settimeout(2)
    port = srv.getsockname()[1]
    acked = []

    def fake_server():
        raw, addr = srv.recvfrom(2048)
        assert unpack_packet(raw).opcode == 1
        srv.sendto(pack_data(1, b"a" * 512), addr)
        raw, _ = srv.recvfrom(2048)
        assert unpack_packet(raw).opcode == OP_ACK
        # Không gửi block cuối ngay; chờ client ACK lại.
        raw, _ = srv.recvfrom(2048)
        acked.append(unpack_packet(raw).block)
        srv.close()

    t = threading.Thread(target=fake_server, daemon=True)
    t.start()
    out = tmp_path / "out.bin"
    # timeout >= 1 vì option timeout chỉ nhận số nguyên giây.
    client = TFTPClient("127.0.0.1", port, timeout=1, retries=3, windowsize=1)
    with pytest.raises(TimeoutError):
        client.download("x", out)
    t.join(timeout=3)
    assert acked == [1]
    assert not out.exists()
    assert not (tmp_path / "out.bin.part").exists()
