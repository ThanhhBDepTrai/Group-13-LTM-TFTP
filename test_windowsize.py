"""Test Window Size (RFC 7440), retry/timeout và các trường hợp biên của bộ truyền."""

import io
import random
import socket
import threading

import pytest
from helpers import make_client, run_pair, running_server, sha256_bytes
from tftp_transfer import (
    Channel,
    RemoteError,
    TransferTimeout,
    make_udp_socket,
    receive_blocks,
    send_blocks,
)

from tftp_packet import (
    OP_ERROR,
    TFTPError,
    pack_ack,
    pack_data,
    pack_error,
    unpack_packet,
)


def payload(size: int, seed: int = 1) -> bytes:
    rng = random.Random(seed)
    return bytes(rng.randrange(256) for _ in range(size))


# --------------------------------------------------------------------------- #
# Đúng đắn: mọi tổ hợp window/blksize/kích thước
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("window", [1, 2, 4, 16, 64])
@pytest.mark.parametrize("blksize", [8, 512, 1428])
@pytest.mark.parametrize("size", [0, 1, 7, 8, 511, 512, 513, 5000, 40_000])
def test_roundtrip_matrix(window, blksize, size):
    data = payload(size, seed=size + window)
    result = run_pair(data, blksize=blksize, window=window)
    assert result.errors == {}
    assert result.received == data


@pytest.mark.parametrize("blksize", [512, 1428])
def test_size_multiple_of_blksize_ends_with_empty_block(blksize):
    data = payload(blksize * 3)
    result = run_pair(data, blksize=blksize, window=4)
    assert result.errors == {}
    assert result.received == data
    assert result.send_stats.blocks == 4  # 3 block đầy + 1 block rỗng báo hết file


def test_empty_file_sends_one_empty_block():
    result = run_pair(b"", blksize=512, window=8)
    assert result.errors == {}
    assert result.received == b""
    assert result.send_stats.blocks == 1


# --------------------------------------------------------------------------- #
# Hiệu quả của cửa sổ
# --------------------------------------------------------------------------- #


def test_window_cuts_ack_count():
    data = payload(512 * 64)
    one = run_pair(data, window=1)
    eight = run_pair(data, window=8)
    assert one.errors == eight.errors == {}
    assert one.recv_stats.packets_sent == 65  # 1 ACK cho mỗi block (64 đầy + 1 rỗng)
    assert eight.recv_stats.packets_sent <= 65 // 8 + 2
    assert eight.received == data


def test_stop_and_wait_ignores_duplicate_acks():
    """Chống 'Sorcerer's Apprentice': ACK trùng không được kích hoạt gửi lại DATA."""

    class DoubleAck(Channel):
        def send(self, data, addr=None):
            super().send(data, addr)
            if data[:2] == b"\x00\x04":
                super().send(data, addr)

    data = payload(512 * 20)
    result = run_pair(data, window=1, recv_cls=DoubleAck)
    assert result.errors == {}
    assert result.received == data
    assert result.send_stats.retransmits == 0
    assert result.send_stats.packets_sent == 21  # đúng một lần cho mỗi block


# --------------------------------------------------------------------------- #
# Mất gói / retry
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("window", [1, 4, 16])
def test_transfer_survives_random_loss(window):
    data = payload(30_000, seed=window)
    result = run_pair(data, window=window, loss=0.12, timeout=0.03, retries=60)
    assert result.errors == {}
    assert result.received == data
    assert result.send_stats.retransmits + result.recv_stats.retransmits > 0


def test_gap_triggers_fast_resend_without_waiting_for_timeout():
    """Mất block 2 trong cửa sổ 4: ACK trùng của bên nhận phải kéo theo gửi lại ngay."""

    class DropBlock2Once(Channel):
        dropped = False

        def send(self, data, addr=None):
            if not self.dropped and data[:2] == b"\x00\x03" and data[2:4] == b"\x00\x02":
                self.dropped = True
                return
            super().send(data, addr)

    data = payload(512 * 12)
    result = run_pair(data, window=4, timeout=1.0, send_cls=DropBlock2Once)
    assert result.errors == {}
    assert result.received == data
    assert result.elapsed < 0.6  # nếu phải chờ timeout 1 giây thì test này đã chậm hơn
    assert result.send_stats.retransmits >= 1


def test_lost_final_ack_is_recovered_by_linger():
    class DropFirstFinalAck(Channel):
        dropped = False

        def send(self, data, addr=None):
            if not self.dropped and data == pack_ack(2):
                self.dropped = True
                return
            super().send(data, addr)

    data = payload(1000)  # 2 block: 512 + 488
    result = run_pair(data, window=1, timeout=0.05, recv_cls=DropFirstFinalAck, linger=0.4)
    assert result.errors == {}
    assert result.received == data
    assert result.send_stats.retransmits >= 1


def test_sender_gives_up_after_retries_when_peer_is_silent():
    sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    silent = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    chan = Channel(sock, silent.getsockname())
    with pytest.raises(TransferTimeout):
        send_blocks(chan, io.BytesIO(b"x" * 1000), blksize=512, windowsize=2,
                    timeout=0.02, retries=2)
    assert chan.stats.retransmits >= 2
    sock.close()
    silent.close()


def test_receiver_gives_up_after_retries_when_sender_is_silent():
    sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    silent = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    chan = Channel(sock, silent.getsockname())
    with pytest.raises(TransferTimeout):
        receive_blocks(chan, io.BytesIO(), blksize=512, windowsize=1, timeout=0.02,
                       retries=2, last_sent=pack_ack(0))
    sock.close()
    silent.close()


# --------------------------------------------------------------------------- #
# Block number quay vòng 65535 -> 0
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("window", [1, 16])
def test_block_number_wraps_past_65535(window):
    blksize = 8
    data = payload(blksize * 65_600 + 3)  # vượt block 65535 và quay về 0
    result = run_pair(data, blksize=blksize, window=window, timeout=0.5)
    assert result.errors == {}
    assert result.send_stats.blocks == 65_601
    assert sha256_bytes(result.received) == sha256_bytes(data)


# --------------------------------------------------------------------------- #
# Lỗi, TID lạ, gói rác
# --------------------------------------------------------------------------- #


def test_remote_error_aborts_sender():
    sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    peer = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    chan = Channel(sock, peer.getsockname())
    peer.sendto(pack_error(3, "Disk full"), sock.getsockname())
    with pytest.raises(RemoteError) as exc:
        send_blocks(chan, io.BytesIO(b"x" * 2000), blksize=512, windowsize=1,
                    timeout=0.5, retries=3)
    assert exc.value.code == 3
    sock.close()
    peer.close()


def test_receiver_raises_when_file_exceeds_max_bytes():
    result = run_pair(payload(5000), max_bytes=1000)
    assert isinstance(result.errors["recv"], TFTPError)
    assert result.errors["recv"].code == 3


def test_stray_tid_gets_error_5_and_transfer_is_unaffected():
    s_sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    r_sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    stray = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    stray.settimeout(1.0)
    s_chan = Channel(s_sock, r_sock.getsockname())
    r_chan = Channel(r_sock, s_sock.getsockname())
    stray.sendto(pack_data(1, b"evil"), r_sock.getsockname())  # gói từ TID lạ

    data = payload(3000)
    out = io.BytesIO()
    thread = threading.Thread(
        target=send_blocks, args=(s_chan, io.BytesIO(data)),
        kwargs={"blksize": 512, "windowsize": 4, "timeout": 0.2, "retries": 5},
    )
    thread.start()
    receive_blocks(r_chan, out, blksize=512, windowsize=4, timeout=0.2, retries=5,
                   last_sent=pack_ack(0))
    thread.join(5)

    reply = unpack_packet(stray.recvfrom(100)[0])
    assert reply.opcode == OP_ERROR and reply.error_code == 5
    assert out.getvalue() == data  # dữ liệu "evil" không lọt vào file
    for sock in (s_sock, r_sock, stray):
        sock.close()


def test_garbage_from_peer_is_ignored():
    s_sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    r_sock = make_udp_socket(socket.AF_INET, "127.0.0.1", 0)
    s_chan = Channel(s_sock, r_sock.getsockname())
    r_chan = Channel(r_sock, s_sock.getsockname())
    for junk in (b"", b"\x00", b"\xff\xff\xff", b"\x00\x04\x00", b"\x00\x09abc"):
        s_sock.sendto(junk, r_sock.getsockname())  # từ đúng TID nhưng là rác
    data = payload(2000)
    out = io.BytesIO()
    thread = threading.Thread(
        target=send_blocks, args=(s_chan, io.BytesIO(data)),
        kwargs={"blksize": 512, "windowsize": 2, "timeout": 0.2, "retries": 5},
    )
    thread.start()
    receive_blocks(r_chan, out, blksize=512, windowsize=2, timeout=0.2, retries=5,
                   last_sent=pack_ack(0))
    thread.join(5)
    assert out.getvalue() == data
    s_sock.close()
    r_sock.close()


def test_stats_track_bytes_blocks_and_throughput():
    data = payload(5000)
    result = run_pair(data, window=4)
    assert result.send_stats.bytes == result.recv_stats.bytes == 5000
    assert result.send_stats.blocks == result.recv_stats.blocks == 10
    assert result.send_stats.throughput_mbps > 0
    assert result.send_stats.duration > 0


# --------------------------------------------------------------------------- #
# End-to-end qua server/client thật
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("window", [1, 4, 16])
def test_end_to_end_get_and_put_with_window(tmp_path, window):
    data = payload(200_000, seed=window)
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "in.bin").write_bytes(data)
    with running_server(tmp_path / "root", max_windowsize=32) as server:
        client = make_client(server, blksize=1428, windowsize=window)
        client.get("in.bin", tmp_path / "out.bin")
        assert (tmp_path / "out.bin").read_bytes() == data
        client.put(tmp_path / "out.bin", "up.bin")
        assert server.wait_idle(5)
        assert (tmp_path / "root" / "up.bin").read_bytes() == data
        assert server.history[-1].windowsize == window


def test_server_clamps_windowsize_to_its_maximum(tmp_path):
    data = payload(50_000)
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "f.bin").write_bytes(data)
    with running_server(tmp_path / "root", max_windowsize=4) as server:
        client = make_client(server, windowsize=32)
        client.get("f.bin", tmp_path / "o.bin")
        assert (tmp_path / "o.bin").read_bytes() == data
        assert server.wait_idle(5)
        assert server.history[-1].windowsize == 4


def test_end_to_end_with_loss_and_window(tmp_path):
    data = payload(60_000)
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "f.bin").write_bytes(data)
    with running_server(tmp_path / "root", timeout=0.05, retries=40, simulate_loss=0.08,
                        max_windowsize=16) as server:
        client = make_client(server, timeout=0.05, retries=40, windowsize=8,
                             simulate_loss=0.08)
        stats = client.get("f.bin", tmp_path / "o.bin")
        assert (tmp_path / "o.bin").read_bytes() == data
        assert stats.bytes == len(data)
