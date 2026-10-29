import socket
import threading
import time
from pathlib import Path

from tftp_packet import (
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    pack_ack,
    pack_data,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)
from tftp_server import ServerConfig, TFTPServer


def start_server(root: Path, port: int = 0):
    config = ServerConfig(
        host="127.0.0.1",
        port=port,
        root=root,
        timeout=1.0,
        retries=2,
        max_clients=20,
        overwrite=False,
        blksize=512,
        log_level="ERROR",
    )
    server = TFTPServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.05)
    actual_port = server.sock.getsockname()[1]
    return server, thread, actual_port


def stop_server(server, thread):
    server.stop()
    thread.join(timeout=2)


def tftp_get(host, port, remote):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(pack_rrq(remote), (host, port))
    chunks = []
    block = 1
    while True:
        raw, peer = s.recvfrom(65539)
        pkt = unpack_packet(raw)
        if pkt.opcode == OP_OACK:
            s.sendto(pack_ack(0), peer)
            raw, peer = s.recvfrom(65539)
            pkt = unpack_packet(raw)
        assert pkt.opcode == OP_DATA, f"expected DATA got {pkt.opcode}"
        assert pkt.block == block, f"block mismatch {pkt.block} != {block}"
        chunks.append(pkt.data)
        s.sendto(pack_ack(block), peer)
        if len(pkt.data) < 512:
            break
        block = (block + 1) & 0xFFFF
    s.close()
    return b"".join(chunks)


def tftp_put(host, port, remote, data: bytes):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(2)
    s.sendto(pack_wrq(remote), (host, port))
    raw, peer = s.recvfrom(65539)
    pkt = unpack_packet(raw)
    if pkt.opcode == OP_OACK:
        s.sendto(pack_ack(0), peer)
    elif pkt.opcode == OP_ACK and pkt.block == 0:
        pass
    else:
        raise AssertionError(f"unexpected first response {pkt.opcode}")
    blksize = 512
    block = 1
    offset = 0
    while True:
        chunk = data[offset:offset + blksize]
        s.sendto(pack_data(block, chunk), peer)
        # wait ACK
        while True:
            raw, p = s.recvfrom(65539)
            if p != peer:
                continue
            ack = unpack_packet(raw)
            if ack.opcode == OP_ERROR:
                raise AssertionError(ack.error_msg)
            if ack.opcode == OP_ACK and ack.block == block:
                break
        offset += len(chunk)
        if len(chunk) < blksize:
            break
        block = (block + 1) & 0xFFFF
    s.close()


def test_rrq_existing_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    content = b"hello world" * 10
    (root / "a.txt").write_bytes(content)
    server, thread, port = start_server(root)
    try:
        got = tftp_get("127.0.0.1", port, "a.txt")
        assert got == content
    finally:
        stop_server(server, thread)


def test_rrq_missing_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    server, thread, port = start_server(root)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(pack_rrq("nope.txt"), ("127.0.0.1", port))
        raw, _peer = s.recvfrom(65539)
        pkt = unpack_packet(raw)
        # may come from ephemeral port
        assert pkt.opcode == OP_ERROR
        assert pkt.error_code == 1
        s.close()
    finally:
        stop_server(server, thread)


def test_wrq_new_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    data = b"upload data " * 50
    server, thread, port = start_server(root)
    try:
        tftp_put("127.0.0.1", port, "out.bin", data)
        assert (root / "out.bin").read_bytes() == data
    finally:
        stop_server(server, thread)


def test_wrq_existing_file_no_overwrite(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "exists.txt").write_bytes(b"old")
    server, thread, port = start_server(root)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(pack_wrq("exists.txt"), ("127.0.0.1", port))
        raw, _peer = s.recvfrom(65539)
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_ERROR
        assert pkt.error_code == 6
        s.close()
        assert (root / "exists.txt").read_bytes() == b"old"
    finally:
        stop_server(server, thread)


def test_path_traversal_rrq(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    server, thread, port = start_server(root)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(2)
        s.sendto(pack_rrq("../secret.txt"), ("127.0.0.1", port))
        raw, _peer = s.recvfrom(65539)
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_ERROR
        assert pkt.error_code == 2
        s.close()
    finally:
        stop_server(server, thread)


def test_zero_byte_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "empty.txt").write_bytes(b"")
    server, thread, port = start_server(root)
    try:
        got = tftp_get("127.0.0.1", port, "empty.txt")
        assert got == b""
    finally:
        stop_server(server, thread)


def test_512_byte_file(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    content = b"x" * 512
    (root / "exact.txt").write_bytes(content)
    server, thread, port = start_server(root)
    try:
        got = tftp_get("127.0.0.1", port, "exact.txt")
        assert got == content
    finally:
        stop_server(server, thread)


def test_unicode_filename(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    content = b"unicode"
    (root / "t\u00eap.txt").write_bytes(content)
    server, thread, port = start_server(root)
    try:
        got = tftp_get("127.0.0.1", port, "t\u00eap.txt")
        assert got == content
    finally:
        stop_server(server, thread)
