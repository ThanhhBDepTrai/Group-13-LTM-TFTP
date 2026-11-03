import threading
import time

from tftp_client import TFTPClient
from tftp_server import ServerConfig, TFTPServer


def test_integration_rrq_wrq(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a.txt").write_bytes(b"hello integration")

    config = ServerConfig(host="127.0.0.1", port=0, root=root, timeout=1, retries=2, log_level="ERROR")
    server = TFTPServer(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    while server.sock is None:
        time.sleep(0.05)
    port = server.sock.getsockname()[1]

    try:
        out = tmp_path / "downloaded.txt"
        n = TFTPClient("127.0.0.1", port, timeout=1, retries=2).download("a.txt", out)
        assert n == len(b"hello integration")
        assert out.read_bytes() == b"hello integration"

        up = tmp_path / "up.txt"
        up.write_bytes(b"upload me")
        TFTPClient("127.0.0.1", port, timeout=1, retries=2).upload(up, "uploaded.txt")
        assert (root / "uploaded.txt").read_bytes() == b"upload me"
    finally:
        server.stop()
        thread.join(timeout=2)
