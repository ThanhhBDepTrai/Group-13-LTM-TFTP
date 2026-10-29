import socket
import threading
import time

from tftp_client import TFTPClient, TFTPErrorClient
from tftp_packet import pack_data


def test_download_cancel(tmp_path):
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    def fake_server():
        try:
            _raw, addr = srv.recvfrom(2048)
            # send first block then stall
            srv.sendto(pack_data(1, b"a" * 512), addr)
            time.sleep(5)
        except Exception:  # noqa: BLE001, S110
            pass
        finally:
            try:
                srv.close()
            except Exception:  # noqa: BLE001, S110
                pass

    t = threading.Thread(target=fake_server, daemon=True)
    t.start()

    cancel = threading.Event()
    cancel.set()

    out = tmp_path / "out.bin"
    client = TFTPClient("127.0.0.1", port, timeout=1, retries=1)
    try:
        client.download("x", out, cancel=cancel)
    except TFTPErrorClient as e:
        assert "Cancelled" in str(e)
    # partial file should be removed
    assert not out.exists() or not (tmp_path / "out.bin.part").exists()
    t.join(timeout=1)
