"""Kiểm thử thống kê phiên truyền và packet trace nội bộ."""

import time

import pytest

from metrics import MetricsCollector
from packet_trace import PacketTracer
from tftp_packet import pack_ack, pack_data, pack_oack, pack_rrq
from tftp_server import ServerConfig, TFTPServer


def test_metrics_memory_and_sqlite(tmp_path):
    db_path = tmp_path / "metrics.db"
    metrics = MetricsCollector(db_path)
    metrics.start_session()
    assert metrics.active_clients == 1
    metrics.end_session()
    assert metrics.active_clients == 0

    metrics.record(
        client_ip="127.0.0.1",
        filename="a.bin",
        direction="download",
        bytes_total=100,
        duration=0.5,
        retries=1,
        timeouts=2,
    )
    metrics.record(
        client_ip="127.0.0.1",
        filename="b.bin",
        direction="upload",
        bytes_total=50,
        duration=0.25,
        status="failed",
        error="timeout",
    )
    stats = metrics.snapshot()
    assert stats["total_transfers"] == 2
    assert stats["successful"] == 1
    assert stats["failed"] == 1
    assert stats["bytes_download"] == 100
    assert stats["bytes_upload"] == 50
    assert stats["timeouts"] == 2
    assert stats["retries"] == 1
    recent = metrics.recent_transfers()
    assert recent[0]["filename"] == "b.bin"
    assert recent[0]["status"] == "failed"

    # Dữ liệu lịch sử vẫn còn khi mở collector mới trên cùng DB.
    reopened = MetricsCollector(db_path)
    assert reopened.snapshot()["total_transfers"] == 2
    reopened.reset()
    assert MetricsCollector(db_path).snapshot()["total_transfers"] == 0


def test_packet_tracer_records_and_filters():
    tracer = PacketTracer()
    event = tracer.record_raw(
        pack_rrq("hello.txt"),
        source="10.0.0.2:50000",
        destination="10.0.0.1:6969",
        direction="Client -> Server",
    )
    assert event is not None
    assert event.opcode == "RRQ"
    tracer.record_raw(
        pack_oack({"windowsize": "4"}),
        source="10.0.0.1:40000",
        destination="10.0.0.2:50000",
        direction="Server -> Client",
    )
    tracer.record_raw(
        pack_data(1, b"abc"),
        source="10.0.0.1:40000",
        destination="10.0.0.2:50000",
        direction="Server -> Client",
    )
    tracer.record_raw(
        pack_ack(1),
        source="10.0.0.2:50000",
        destination="10.0.0.1:40000",
        direction="Client -> Server",
    )
    assert tracer.counts() == {"RRQ": 1, "OACK": 1, "DATA": 1, "ACK": 1}
    assert [event.opcode for event in tracer.events(opcode="data")] == ["DATA"]
    tracer.reset()
    assert tracer.events() == []
    assert tracer.counts() == {}


def test_server_transfer_records_metrics_and_trace(tmp_path):
    # Import ở đây để tránh vòng import khi chạy test độc lập.
    from tftp_client import TFTPClient

    metrics = MetricsCollector()
    tracer = PacketTracer()
    root = tmp_path / "root"
    root.mkdir()
    payload = b"server metrics payload" * 200
    (root / "download.bin").write_bytes(payload)

    server = TFTPServer(
        ServerConfig(
            host="127.0.0.1",
            port=0,
            root=root,
            timeout=1,
            retries=3,
            overwrite=True,
            windowsize=4,
            log_level="ERROR",
        ),
        metrics=metrics,
        tracer=tracer,
    )

    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.01)
    port = server.sock.getsockname()[1]
    try:
        client = TFTPClient("127.0.0.1", port, timeout=2, windowsize=1)
        out = tmp_path / "download.bin"
        assert client.download("download.bin", out) == len(payload)
        assert out.read_bytes() == payload

        upload = tmp_path / "upload.bin"
        upload.write_bytes(payload)
        assert client.upload(upload, "upload.bin") == len(payload)
        assert (root / "upload.bin").read_bytes() == payload

        deadline = time.time() + 2
        while metrics.snapshot()["total_transfers"] < 2 and time.time() < deadline:
            time.sleep(0.02)
        stats = metrics.snapshot()
        assert stats["total_transfers"] == 2
        assert stats["downloads"] == 1
        assert stats["uploads"] == 1
        assert stats["bytes_download"] == len(payload)
        assert stats["bytes_upload"] == len(payload)
        assert stats["active_clients"] == 0
        recount = tracer.counts()
        assert recount.get("RRQ") == 1
        assert recount.get("WRQ") == 1
        assert recount.get("DATA", 0) >= 2
        assert recount.get("ACK", 0) >= 2
    finally:
        server.stop()
        thread.join(timeout=2)


def test_server_records_failed_transfer(tmp_path):
    from tftp_client import TFTPClient, TFTPErrorClient

    metrics = MetricsCollector()
    root = tmp_path / "root"
    root.mkdir()
    server = TFTPServer(
        ServerConfig(
            host="127.0.0.1",
            port=0,
            root=root,
            timeout=0.2,
            retries=2,
            log_level="ERROR",
        ),
        metrics=metrics,
    )
    import threading

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.01)
    port = server.sock.getsockname()[1]
    try:
        client = TFTPClient("127.0.0.1", port, timeout=0.2, retries=1)
        with pytest.raises(TFTPErrorClient):
            client.download("missing.bin", tmp_path / "missing.bin")
        deadline = time.time() + 2
        while metrics.snapshot()["total_transfers"] < 1 and time.time() < deadline:
            time.sleep(0.02)
        recent = metrics.recent_transfers()
        assert recent[0]["status"] == "failed"
        assert recent[0]["error"] == "File not found"
    finally:
        server.stop()
        thread.join(timeout=2)
