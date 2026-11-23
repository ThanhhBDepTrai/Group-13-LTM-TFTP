"""Hồi quy rớt gói: truyền file khi mạng mất gói/trễ, phải có retry và SHA-256 vẫn đúng.

Chạy trên server thật qua loopback; ``NetworkFaultInjector`` được gắn vào server đúng
như hợp đồng (kiểm tra ``should_drop()`` / ``apply_delay()`` ở bước gửi gói).
"""

import hashlib
import os
import time

import pytest
from helpers import make_client, run_pair, running_server

from network_simulator import NetworkFaultInjector
from retransmit_analysis import retransmit_summary
from tftp_transfer import Channel, RemoteError


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_root(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    return root


def server_with(injector, root, **overrides):
    values = {"timeout": 0.05, "retries": 80, "linger": 0.5}
    values.update(overrides)
    return running_server(root, fault_injector=injector, **values)


def test_server_exposes_the_injector_for_the_api(tmp_path):
    injector = NetworkFaultInjector()
    with server_with(injector, make_root(tmp_path)) as server:
        assert server.fault_injector is injector  # API gọi server.fault_injector.update(...)


@pytest.mark.parametrize("window", [1, 8])
def test_download_with_10_percent_loss_retries_and_stays_verified(tmp_path, window):
    root = make_root(tmp_path)
    data = os.urandom(150_000)
    (root / "f.bin").write_bytes(data)
    injector = NetworkFaultInjector(loss_rate=0.10, seed=100 + window)
    with server_with(injector, root, max_windowsize=16) as server:
        client = make_client(server, timeout=0.05, retries=80, windowsize=window)
        stats = client.get("f.bin", tmp_path / "out.bin")
        assert server.wait_idle(5)
        record = server.history[-1]

    assert sha((tmp_path / "out.bin").read_bytes()) == sha(data)  # VERIFIED
    assert injector.snapshot()["packets_dropped"] > 0  # thật sự có gói bị mất
    assert stats.retransmits + record.retransmits > 0  # và đã có retry để bù
    assert record.status == "ok"


@pytest.mark.parametrize("window", [1, 8])
def test_upload_with_10_percent_loss_retries_and_stays_verified(tmp_path, window):
    root = make_root(tmp_path)
    data = os.urandom(120_000)
    (tmp_path / "src.bin").write_bytes(data)
    injector = NetworkFaultInjector(loss_rate=0.10, seed=200 + window)
    with server_with(injector, root, max_windowsize=16) as server:
        client = make_client(server, timeout=0.05, retries=80, windowsize=window)
        stats = client.put(tmp_path / "src.bin", "up.bin")
        assert server.wait_idle(5)
        record = server.history[-1]

    assert sha((root / "up.bin").read_bytes()) == sha(data)
    assert injector.snapshot()["packets_dropped"] > 0
    assert stats.retransmits + record.retransmits > 0


def test_no_loss_means_no_retries(tmp_path):
    root = make_root(tmp_path)
    (root / "f.bin").write_bytes(os.urandom(60_000))
    injector = NetworkFaultInjector(0.0, 0)
    with server_with(injector, root, timeout=0.5, retries=5) as server:
        stats = make_client(server, timeout=0.5).get("f.bin", tmp_path / "o.bin")
        assert server.wait_idle(5)
        assert stats.retransmits == 0 and server.history[-1].retransmits == 0
    assert injector.snapshot()["packets_dropped"] == 0


def test_loss_rate_can_be_changed_while_server_keeps_running(tmp_path):
    root = make_root(tmp_path)
    data = os.urandom(80_000)
    (root / "f.bin").write_bytes(data)
    injector = NetworkFaultInjector(0.0, 0, seed=7)
    with server_with(injector, root) as server:
        client = make_client(server, timeout=0.05, retries=80)

        client.get("f.bin", tmp_path / "a.bin")
        assert injector.snapshot()["packets_dropped"] == 0  # giai đoạn 1: mạng sạch

        injector.update(loss_rate=0.15)  # như POST /api/v1/simulator/config, không restart
        client.get("f.bin", tmp_path / "b.bin")
        assert injector.snapshot()["packets_dropped"] > 0  # giai đoạn 2: có mất gói

        injector.update(loss_rate=0.0)
        before = injector.snapshot()["packets_dropped"]
        client.get("f.bin", tmp_path / "c.bin")
        assert injector.snapshot()["packets_dropped"] == before  # giai đoạn 3: sạch trở lại
        assert server.wait_idle(5)
    for name in ("a.bin", "b.bin", "c.bin"):
        assert sha((tmp_path / name).read_bytes()) == sha(data)


def test_delay_slows_the_transfer_but_not_the_data(tmp_path):
    root = make_root(tmp_path)
    data = os.urandom(512 * 10)  # đúng 10 block đầy + 1 block rỗng = 11 gói DATA
    (root / "f.bin").write_bytes(data)

    with server_with(NetworkFaultInjector(0.0, 0), root, timeout=0.5, retries=5) as server:
        started = time.perf_counter()
        make_client(server, timeout=0.5).get("f.bin", tmp_path / "fast.bin")
        fast = time.perf_counter() - started

    slow_injector = NetworkFaultInjector(0.0, 20)
    with server_with(slow_injector, root, timeout=0.5, retries=5) as server:
        started = time.perf_counter()
        make_client(server, timeout=0.5).get("f.bin", tmp_path / "slow.bin")
        slow = time.perf_counter() - started

    assert slow - fast >= 0.15  # 11 gói x 20 ms
    assert slow_injector.snapshot()["packets_delayed"] >= 11
    assert (tmp_path / "slow.bin").read_bytes() == data


def test_error_packets_are_never_dropped_by_the_simulator(tmp_path):
    """Dù mất gói 50%, lỗi 'File not found' phải đến nơi ngay (không bị treo vì mất ERROR)."""
    injector = NetworkFaultInjector(0.5, 0, seed=1)
    with server_with(injector, make_root(tmp_path), timeout=0.5, retries=3) as server:
        for _ in range(20):
            with pytest.raises(RemoteError) as exc:
                make_client(server, timeout=0.5).get("missing.bin", tmp_path / "x")
            assert exc.value.code == 1


def test_client_side_injector_also_works(tmp_path):
    root = make_root(tmp_path)
    data = os.urandom(70_000)
    (root / "f.bin").write_bytes(data)
    injector = NetworkFaultInjector(0.10, 0, seed=3)
    with running_server(root, timeout=0.05, retries=80) as server:
        client = make_client(server, timeout=0.05, retries=80)
        client.fault_injector = injector
        stats = client.get("f.bin", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data
    assert injector.snapshot()["packets_dropped"] > 0 and stats.retransmits > 0


def test_extreme_loss_is_survivable_with_enough_retries(tmp_path):
    root = make_root(tmp_path)
    data = os.urandom(8_000)
    (root / "f.bin").write_bytes(data)
    injector = NetworkFaultInjector(0.5, 0, seed=21)  # trần 50% theo kế hoạch
    with server_with(injector, root, timeout=0.03, retries=100) as server:
        stats = make_client(server, timeout=0.03, retries=100).get("f.bin", tmp_path / "o.bin")
    assert (tmp_path / "o.bin").read_bytes() == data
    assert stats.retransmits > 0


@pytest.mark.parametrize("window", [1, 4])
def test_matches_a_real_lossy_transfer(window):
    """Capture ở phía gửi: số gói DATA bị đánh dấu phát lại phải bằng số lần gửi lại thật."""

    captured = []

    class Recorder(Channel):
        def send(self, packet, addr=None):
            if packet[:2] == b"\x00\x03":
                captured.append({"opcode": "DATA", "block": int.from_bytes(packet[2:4], "big")})
            super().send(packet, addr)

    payload = bytes(range(256)) * 200  # 51 200 byte = 100 block đầy + 1 block rỗng
    result = run_pair(payload, window=window, loss=0.12, timeout=0.03, retries=80,
                      send_cls=Recorder)
    assert result.errors == {} and result.received == payload
    summary = retransmit_summary(captured)
    assert summary["retransmitted_data"] == result.send_stats.retransmits > 0
    assert summary["data_packets"] == len(captured)
