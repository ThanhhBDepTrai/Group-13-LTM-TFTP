"""Test tích hợp TFTP server + client thật trên loopback."""

import os
import socket
import threading

import pytest
from helpers import make_client, running_server, sha256_bytes

from tftp_client import TFTPClient
from tftp_config import ClientConfig
from tftp_packet import (
    OP_ERROR,
    OP_OACK,
    TFTPError,
    pack_error,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)
from tftp_server import ServerHooks
from tftp_transfer import RemoteError, TransferError, TransferTimeout


def make_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def raw_request(server, packet: bytes) -> bytes:
    """Gửi một gói thô tới cổng chính và trả về gói trả lời đầu tiên."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(2.0)
    try:
        sock.sendto(packet, server.address)
        return sock.recvfrom(70000)[0]
    finally:
        sock.close()


# --------------------------------------------------------------------------- #
# Truyền cơ bản
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 1024, 100_000])
def test_put_then_get_roundtrip_without_options(tmp_path, size):
    root = make_root(tmp_path)
    data = os.urandom(size)
    (tmp_path / "src.bin").write_bytes(data)
    with running_server(root) as server:
        client = make_client(server)
        client.put(tmp_path / "src.bin", "file.bin")
        assert server.wait_idle(5)
        assert (root / "file.bin").read_bytes() == data
        client.get("file.bin", tmp_path / "back.bin")
        assert (tmp_path / "back.bin").read_bytes() == data


@pytest.mark.parametrize("blksize", [8, 1024, 1428, 8192])
def test_blksize_option_roundtrip(tmp_path, blksize):
    root = make_root(tmp_path)
    data = os.urandom(30_000)
    (root / "f.bin").write_bytes(data)
    with running_server(root) as server:
        make_client(server, blksize=blksize).get("f.bin", tmp_path / "o.bin")
        assert (tmp_path / "o.bin").read_bytes() == data
        assert server.wait_idle(5)
        assert server.history[-1].blksize == blksize


def test_history_record_describes_the_transfer(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(b"x" * 3000)
    with running_server(root) as server:
        make_client(server).get("f.bin", tmp_path / "o.bin")
        assert server.wait_idle(5)
        record = server.history[-1]
        assert (record.op, record.filename, record.status) == ("RRQ", "f.bin", "ok")
        assert record.bytes == 3000 and record.blocks == 6 and record.duration > 0


def test_subdirectory_download(tmp_path):
    root = make_root(tmp_path)
    (root / "boot").mkdir()
    (root / "boot" / "kernel").write_bytes(b"K" * 700)
    with running_server(root) as server:
        make_client(server).get("boot/kernel", tmp_path / "k")
        assert (tmp_path / "k").read_bytes() == b"K" * 700


def test_unicode_filename_roundtrip(tmp_path):
    root = make_root(tmp_path)
    (root / "báo cáo.txt").write_bytes("xin chào".encode())
    with running_server(root) as server:
        make_client(server).get("báo cáo.txt", tmp_path / "out.txt")
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "xin chào"


# --------------------------------------------------------------------------- #
# Lỗi nghiệp vụ
# --------------------------------------------------------------------------- #


def test_get_missing_file_is_error_1(tmp_path):
    with running_server(make_root(tmp_path)) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).get("nope.bin", tmp_path / "o.bin")
        assert exc.value.code == 1
        assert not (tmp_path / "o.bin").exists()
        assert list(tmp_path.glob(".*.part")) == []


@pytest.mark.parametrize(
    "name", ["../secret.txt", "..\\secret.txt", "a/../../secret.txt", "/../secret.txt"]
)
def test_path_traversal_is_blocked_on_read(tmp_path, name):
    root = make_root(tmp_path)
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    with running_server(root) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).get(name, tmp_path / "stolen.txt")
        assert exc.value.code == 2
        assert not (tmp_path / "stolen.txt").exists()


def test_path_traversal_is_blocked_on_write(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "src").write_bytes(b"payload")
    with running_server(root) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).put(tmp_path / "src", "../evil.txt")
        assert exc.value.code == 2
        assert not (tmp_path / "evil.txt").exists()


def test_symlink_escape_is_blocked(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    try:
        os.symlink(tmp_path / "secret.txt", root / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink không khả dụng")
    with running_server(root) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).get("link.txt", tmp_path / "o.txt")
        assert exc.value.code == 2


def test_upload_to_existing_file_is_error_6(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(b"old")
    (tmp_path / "new").write_bytes(b"new")
    with running_server(root) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).put(tmp_path / "new", "f.bin")
        assert exc.value.code == 6
        assert (root / "f.bin").read_bytes() == b"old"


def test_overwrite_allowed_by_config(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(b"old")
    (tmp_path / "new").write_bytes(b"new content")
    with running_server(root, allow_overwrite=True) as server:
        make_client(server).put(tmp_path / "new", "f.bin")
        assert server.wait_idle(5)
        assert (root / "f.bin").read_bytes() == b"new content"


def test_read_only_server_rejects_uploads(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "new").write_bytes(b"x")
    with running_server(root, allow_write=False) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).put(tmp_path / "new", "f.bin")
        assert exc.value.code == 2
        assert not (root / "f.bin").exists()


def test_upload_into_missing_directory_is_error_1(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "new").write_bytes(b"x")
    with running_server(root) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).put(tmp_path / "new", "nodir/f.bin")
        assert exc.value.code == 1


def test_declared_tsize_over_limit_is_rejected_up_front(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "big").write_bytes(b"x" * 5000)
    with running_server(root, max_file_size=1000) as server:
        with pytest.raises(RemoteError) as exc:
            make_client(server).put(tmp_path / "big", "big.bin")
        assert exc.value.code == 3
        assert not (root / "big.bin").exists()


def test_upload_over_limit_without_tsize_is_aborted_and_cleaned(tmp_path):
    root = make_root(tmp_path)
    (tmp_path / "big").write_bytes(b"x" * 5000)
    with running_server(root, max_file_size=1000) as server:
        client = make_client(server, request_tsize=False)
        with pytest.raises(RemoteError) as exc:
            client.put(tmp_path / "big", "big.bin")
        assert exc.value.code == 3
        assert server.wait_idle(5)
        assert list(root.iterdir()) == []  # không còn file thật lẫn file .part


def test_failed_upload_never_exposes_partial_file(tmp_path):
    root = make_root(tmp_path)
    with running_server(root, timeout=0.05, retries=2) as server:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(pack_wrq("partial.bin"), server.address)
        sock.recvfrom(100)  # ACK 0, rồi client biến mất
        assert server.wait_idle(5)
        sock.close()
        assert not (root / "partial.bin").exists()
        assert list(root.iterdir()) == []
        assert server.history[-1].status == "error"


def test_client_rejects_oack_that_raises_blksize(tmp_path):
    """Server giả trả OACK với blksize lớn hơn client yêu cầu: client phải gửi ERROR 8."""
    fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fake.bind(("127.0.0.1", 0))
    fake.settimeout(3)
    seen = {}

    def serve():
        raw, addr = fake.recvfrom(2048)
        seen["request"] = unpack_packet(raw)
        fake.sendto(b"\x00\x06blksize\x009000\x00", addr)
        seen["reply"] = unpack_packet(fake.recvfrom(2048)[0])

    thread = threading.Thread(target=serve)
    thread.start()
    client = TFTPClient("127.0.0.1", fake.getsockname()[1],
                        ClientConfig(blksize=1024, timeout=0.5, retries=1, linger=0))
    with pytest.raises(TransferError):
        client.get("x", tmp_path / "x")
    thread.join(5)
    fake.close()
    assert seen["request"].options["blksize"] == "1024"
    assert seen["reply"].opcode == OP_ERROR and seen["reply"].error_code == 8
    assert not (tmp_path / "x").exists()


def test_client_times_out_when_no_server(tmp_path):
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]  # cổng có thật nhưng không ai trả lời
    client = TFTPClient("127.0.0.1", port, ClientConfig(timeout=0.05, retries=1, linger=0))
    with pytest.raises(TransferTimeout):
        client.get("x", tmp_path / "x")
    probe.close()


def test_client_detects_size_mismatch_via_tsize(tmp_path):
    """tsize trong OACK khác số byte thực nhận -> báo lỗi và không để lại file."""
    fake = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    fake.bind(("127.0.0.1", 0))
    fake.settimeout(3)

    def serve():
        raw, addr = fake.recvfrom(2048)
        fake.sendto(b"\x00\x06tsize\x001000\x00", addr)
        fake.recvfrom(2048)  # ACK 0
        fake.sendto(b"\x00\x03\x00\x01" + b"a" * 10, addr)  # chỉ 10 byte, ngắn hơn 1000
        fake.recvfrom(2048)  # ACK 1

    thread = threading.Thread(target=serve)
    thread.start()
    client = TFTPClient("127.0.0.1", fake.getsockname()[1], ClientConfig(timeout=0.5, linger=0))
    with pytest.raises(TransferError, match="mismatch"):
        client.get("x", tmp_path / "x")
    thread.join(5)
    fake.close()
    assert not (tmp_path / "x").exists()


# --------------------------------------------------------------------------- #
# Độ bền của server
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "packet",
    [b"", b"\x00", b"\xff\xff", b"\x00\x03\x00\x01abc", b"\x00\x04\x00\x00", b"\x00\x01",
     b"\x00\x01file\x00", b"\x00\x01file\x00bogus\x00", b"\x00\x02\x00octet\x00"],
)
def test_server_answers_garbage_with_error_and_keeps_running(tmp_path, packet):
    root = make_root(tmp_path)
    (root / "ok.txt").write_bytes(b"still alive")
    with running_server(root) as server:
        if packet:  # gói rỗng không gửi được thành datagram có ý nghĩa; vẫn thử bình thường
            reply = unpack_packet(raw_request(server, packet))
            assert reply.opcode == OP_ERROR
            assert reply.error_code in (4, 8)
        make_client(server).get("ok.txt", tmp_path / "o.txt")
        assert (tmp_path / "o.txt").read_bytes() == b"still alive"


def test_server_busy_when_max_clients_reached(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(b"x" * 5000)
    with running_server(root, max_clients=1, timeout=1.0) as server:
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.settimeout(2)
        holder.sendto(pack_rrq("f.bin"), server.address)
        holder.recvfrom(1024)  # DATA 1; không ACK nên phiên này chiếm chỗ
        reply = unpack_packet(raw_request(server, pack_rrq("f.bin")))
        assert reply.opcode == OP_ERROR and reply.error_code == 0
        assert "busy" in reply.error_msg.lower()
        holder.close()


def test_duplicate_request_does_not_start_second_session(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(b"x" * 5000)
    with running_server(root, timeout=1.0) as server:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(pack_rrq("f.bin"), server.address)
        sock.sendto(pack_rrq("f.bin"), server.address)  # client "gửi lại" RRQ
        sock.recvfrom(1024)
        sock.settimeout(0.3)
        with pytest.raises(socket.timeout):
            sock.recvfrom(1024)  # không có DATA 1 thứ hai từ phiên trùng
        assert server.active_sessions == 1
        sock.close()


def test_concurrent_clients_get_correct_data(tmp_path):
    root = make_root(tmp_path)
    files = {f"f{i}.bin": os.urandom(20_000 + i * 777) for i in range(8)}
    for name, data in files.items():
        (root / name).write_bytes(data)
    failures = []

    def worker(name, data):
        try:
            client = make_client(server, blksize=1024, windowsize=4)
            out = tmp_path / f"out_{name}"
            client.get(name, out)
            if sha256_bytes(out.read_bytes()) != sha256_bytes(data):
                failures.append(name)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: {exc}")

    with running_server(root, max_windowsize=16) as server:
        threads = [threading.Thread(target=worker, args=item) for item in files.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert server.wait_idle(5)
    assert failures == []


def test_concurrent_uploads_to_different_files(tmp_path):
    root = make_root(tmp_path)
    sources = {}
    for i in range(5):
        data = os.urandom(15_000 + i)
        path = tmp_path / f"src{i}"
        path.write_bytes(data)
        sources[f"up{i}.bin"] = (path, data)
    errors = []

    def worker(remote, path):
        try:
            make_client(server).put(path, remote)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    with running_server(root) as server:
        threads = [threading.Thread(target=worker, args=(n, p)) for n, (p, _) in sources.items()]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert server.wait_idle(5)
    assert errors == []
    for name, (_, data) in sources.items():
        assert (root / name).read_bytes() == data


# --------------------------------------------------------------------------- #
# Hooks
# --------------------------------------------------------------------------- #


class RecordingHooks(ServerHooks):
    def __init__(self, deny=None):
        self.deny = deny
        self.records = []
        self.calls = []

    def authorize(self, op, filename, client):
        self.calls.append((op, filename))
        if self.deny and filename == self.deny:
            raise TFTPError(2, "Denied by policy")

    def on_transfer(self, record):
        self.records.append(record)


def test_hooks_can_deny_and_observe_transfers(tmp_path):
    root = make_root(tmp_path)
    (root / "ok.txt").write_bytes(b"fine")
    (root / "private.txt").write_bytes(b"nope")
    hooks = RecordingHooks(deny="private.txt")
    with running_server(root, hooks=hooks) as server:
        make_client(server).get("ok.txt", tmp_path / "a")
        with pytest.raises(RemoteError) as exc:
            make_client(server).get("private.txt", tmp_path / "b")
        assert exc.value.code == 2
        assert server.wait_idle(5)
    assert [r.status for r in hooks.records] == ["ok", "error"]
    assert hooks.records[1].error.startswith("2:")
    assert ("RRQ", "ok.txt") in hooks.calls


def test_failing_hook_does_not_break_server(tmp_path):
    class Boom(ServerHooks):
        def on_transfer(self, record):
            raise RuntimeError("hook bug")

    root = make_root(tmp_path)
    (root / "f.txt").write_bytes(b"data")
    with running_server(root, hooks=Boom()) as server:
        make_client(server).get("f.txt", tmp_path / "o")
        make_client(server).get("f.txt", tmp_path / "o2")
        assert (tmp_path / "o2").read_bytes() == b"data"


def test_custom_resolve_hook_redirects_path(tmp_path):
    root = make_root(tmp_path)
    elsewhere = tmp_path / "elsewhere.bin"
    elsewhere.write_bytes(b"redirected")

    class Redirect(ServerHooks):
        def resolve(self, op, filename, client):
            return elsewhere

    with running_server(root, hooks=Redirect()) as server:
        make_client(server).get("anything", tmp_path / "o")
        assert (tmp_path / "o").read_bytes() == b"redirected"


# --------------------------------------------------------------------------- #
# PXE
# --------------------------------------------------------------------------- #


def test_pxe_mode_tolerates_case_and_backslashes(tmp_path):
    root = make_root(tmp_path)
    (root / "boot").mkdir()
    (root / "boot" / "pxelinux.0").write_bytes(b"PXE" * 100)
    with running_server(root, pxe_mode=True) as server:
        make_client(server).get("BOOT\\PXELINUX.0", tmp_path / "o")
        assert (tmp_path / "o").read_bytes() == b"PXE" * 100


def test_pxe_mode_caps_blksize_in_oack(tmp_path):
    root = make_root(tmp_path)
    (root / "boot.img").write_bytes(os.urandom(9000))
    with running_server(root, pxe_mode=True, max_blksize=8192) as server:
        reply = unpack_packet(raw_request(
            server, pack_rrq("boot.img", {"blksize": "8192", "tsize": "0"})))
        assert reply.opcode == OP_OACK
        assert reply.options == {"blksize": "1456", "tsize": "9000"}


def test_pxe_missing_config_file_returns_error_1(tmp_path):
    """PXELINUX hỏi nhiều file cấu hình không tồn tại; server phải trả lỗi 1 gọn gàng."""
    root = make_root(tmp_path)
    with running_server(root, pxe_mode=True) as server:
        reply = unpack_packet(raw_request(server, pack_rrq("pxelinux.cfg/01-aa-bb-cc-dd-ee-ff")))
        assert reply.opcode == OP_ERROR and reply.error_code == 1


def test_error_packet_is_well_formed_on_the_wire(tmp_path):
    with running_server(make_root(tmp_path)) as server:
        raw = raw_request(server, pack_rrq("missing.bin"))
        assert raw == pack_error(1, "File not found")


def test_uploaded_file_is_visible_the_moment_put_returns(tmp_path):
    """Server chỉ ACK block cuối sau khi đã đổi tên file .part; tải lại ngay không được lỗi."""
    root = make_root(tmp_path)
    with running_server(root, linger=0.0) as server:
        client = make_client(server, retries=5)
        for i in range(40):
            data = os.urandom(300 + i)
            source = tmp_path / f"s{i}"
            source.write_bytes(data)
            client.put(source, f"f{i}.bin")
            client.get(f"f{i}.bin", tmp_path / f"o{i}")  # gọi ngay, không chờ server
            assert (tmp_path / f"o{i}").read_bytes() == data
