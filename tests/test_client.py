import socket
import threading

from tftp_client import TFTPClient
from tftp_packet import OP_ACK, OP_RRQ, pack_data, unpack_packet


def test_download_fake_server(tmp_path):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def fake_server():
        raw, addr = srv.recvfrom(2048)
        assert unpack_packet(raw).opcode == OP_RRQ
        srv.sendto(pack_data(1, b"hello"), addr)
        raw, _ = srv.recvfrom(2048)
        assert unpack_packet(raw).opcode == OP_ACK
        srv.close()

    t = threading.Thread(target=fake_server, daemon=True)
    t.start()
    out = tmp_path / "out.txt"
    n = TFTPClient("127.0.0.1", port, timeout=1, retries=2).download("x", out)
    assert n == 5
    assert out.read_bytes() == b"hello"
    t.join(timeout=2)
