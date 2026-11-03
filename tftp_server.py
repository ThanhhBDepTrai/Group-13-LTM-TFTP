"""TFTP server (RFC 1350) with optional blksize/timeout/tsize negotiation."""

from __future__ import annotations

import argparse
import json
import logging

logger = logging.getLogger(__name__)
import os
import socket
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from metrics import MetricsCollector
from packet_trace import PacketTracer
from storage_manager import StorageError, StorageManager
from tftp_config import (
    DEFAULT_BLKSIZE,
    DEFAULT_HOST,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_MAX_WINDOWSIZE,
    DEFAULT_PORT,
    DEFAULT_RETRIES,
    DEFAULT_ROOT,
    DEFAULT_TIMEOUT,
)
from tftp_options import negotiate_options
from tftp_packet import (
    MAX_PACKET_SIZE,
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_RRQ,
    OP_WRQ,
    TFTPError,
    pack_ack,
    pack_data,
    pack_error,
    pack_oack,
    unpack_packet,
)

_BLOCK_MASK = 0xFFFF


def _block_for_index(index: int) -> int:
    """Chỉ số tuyệt đối (bắt đầu 1) -> block number 16-bit."""
    return index & _BLOCK_MASK


@dataclass
class ServerConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    root: Path = Path(DEFAULT_ROOT)
    timeout: float = DEFAULT_TIMEOUT
    retries: int = DEFAULT_RETRIES
    max_clients: int = DEFAULT_MAX_CLIENTS
    overwrite: bool = False
    blksize: int = DEFAULT_BLKSIZE
    # Cửa sổ tối đa server chấp nhận; client có thể yêu cầu nhỏ hơn.
    windowsize: int = DEFAULT_MAX_WINDOWSIZE
    log_level: str = "INFO"


@dataclass
class TransferOutcome:
    bytes_transferred: int = 0
    retries: int = 0
    timeouts: int = 0
    status: str = "success"
    error: str | None = None


def _endpoint(addr) -> str:
    """Định dạng (host, port) cho Packet Analyzer."""
    try:
        return f"{addr[0]}:{addr[1]}"
    except (IndexError, TypeError):
        return str(addr)


class TFTPServer:
    def __init__(
        self,
        config: ServerConfig,
        *,
        metrics: MetricsCollector | None = None,
        tracer: PacketTracer | None = None,
    ):
        self.config = config
        self.root = config.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        if not 1 <= config.windowsize <= 65535:
            raise ValueError("windowsize must be 1..65535")
        if config.max_clients < 1:
            raise ValueError("max_clients must be >= 1")
        self.storage = StorageManager(self.root)
        self.metrics = metrics
        self.tracer = tracer
        self._sem = threading.Semaphore(config.max_clients)
        self._stop = threading.Event()
        self.sock: socket.socket | None = None
        self._list_sock: socket.socket | None = None

    # ---------- path safety ----------
    def safe_path(self, filename: str) -> Path:
        try:
            return self.storage.resolve(filename)
        except StorageError as exc:
            raise TFTPError(2, exc.message) from exc

    # ---------- trace / gửi gói ----------
    def _trace(self, raw: bytes, *, source: str, destination: str, direction: str) -> None:
        if self.tracer is None:
            return
        try:
            self.tracer.record_raw(
                raw,
                source=source,
                destination=destination,
                direction=direction,
            )
        except Exception:
            logger.exception("Packet trace failed")

    def _trace_received(self, raw: bytes, sock: socket.socket, peer) -> None:
        try:
            local = sock.getsockname()
        except OSError:
            local = (self.config.host, self.config.port)
        self._trace(
            raw,
            source=_endpoint(peer),
            destination=_endpoint(local),
            direction="Client -> Server",
        )

    def _send_raw(self, sock: socket.socket, raw: bytes, addr) -> None:
        sock.sendto(raw, addr)
        try:
            local = sock.getsockname()
        except OSError:
            local = (self.config.host, self.config.port)
        self._trace(
            raw,
            source=_endpoint(local),
            destination=_endpoint(addr),
            direction="Server -> Client",
        )

    def _send_error(self, sock: socket.socket, addr, code: int, msg: str) -> None:
        try:
            self._send_raw(sock, pack_error(code, msg), addr)
        except OSError:
            pass

    # ---------- handlers ----------
    def handle_rrq(self, pkt, client_addr, data_sock) -> TransferOutcome:
        try:
            path = self.safe_path(pkt.filename)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.code, e.message)
            return TransferOutcome(status="failed", error=e.message)
        if not path.is_file():
            self._send_error(data_sock, client_addr, 1, "File not found")
            return TransferOutcome(status="failed", error="File not found")

        cfg = self.config
        size = path.stat().st_size
        try:
            accepted = negotiate_options(
                pkt.options,
                is_upload=False,
                file_size=size,
                server_windowsize=cfg.windowsize,
            )
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.code, e.message)
            return TransferOutcome(status="failed", error=e.message)

        blksize = int(accepted.get("blksize", cfg.blksize))
        timeout = float(accepted.get("timeout", cfg.timeout))
        windowsize = int(accepted.get("windowsize", 1))
        data_sock.settimeout(timeout)

        total_retries = 0
        total_timeouts = 0

        # OACK handshake; nếu ACK0 thất lạc thì gửi lại OACK thay vì bỏ phiên.
        if accepted:
            oack = pack_oack(accepted)
            self._send_raw(data_sock, oack, client_addr)
            acked, retries, timeouts = self._wait_ack(
                data_sock, client_addr, 0, timeout, retransmit=oack
            )
            total_retries += retries
            total_timeouts += timeouts
            if not acked:
                logger.warning("No ACK0 after OACK from %s", client_addr)
                return TransferOutcome(
                    status="failed",
                    error="No ACK0 after OACK",
                    retries=total_retries,
                    timeouts=total_timeouts,
                )

        total_blocks = size // blksize + 1  # luôn có block cuối ngắn/rỗng
        base_index = 1
        try:
            with open(path, "rb") as file:
                while base_index <= total_blocks:
                    last_index = min(base_index + windowsize - 1, total_blocks)
                    window: list[tuple[int, int, bytes]] = []
                    for index in range(base_index, last_index + 1):
                        file.seek((index - 1) * blksize)
                        window.append(
                            (index, _block_for_index(index), file.read(blksize))
                        )

                    retries = 0
                    while True:
                        for _index, block, chunk in window:
                            self._send_raw(
                                data_sock, pack_data(block, chunk), client_addr
                            )

                        try:
                            while True:
                                raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
                                self._trace_received(raw, data_sock, peer)
                                if peer != client_addr:
                                    self._send_error(data_sock, peer, 5, "Unknown TID")
                                    continue
                                try:
                                    ack = unpack_packet(raw)
                                except TFTPError:
                                    continue

                                if ack.opcode == OP_ERROR:
                                    logger.warning(
                                        "Client error: %s", ack.error_msg
                                    )
                                    return TransferOutcome(
                                        status="failed",
                                        error=ack.error_msg,
                                        retries=total_retries,
                                        timeouts=total_timeouts,
                                    )
                                if ack.opcode != OP_ACK:
                                    continue

                                position = next(
                                    (
                                        pos
                                        for pos, (_i, block, _c) in enumerate(window)
                                        if block == ack.block
                                    ),
                                    None,
                                )
                                if position is not None:
                                    base_index = window[position][0] + 1
                                    break
                                # ACK cũ/trùng: tiếp tục chờ ACK của cửa sổ.
                        except (TimeoutError, ConnectionResetError):
                            retries += 1
                            total_retries += 1
                            total_timeouts += 1
                            if retries >= cfg.retries:
                                logger.error(
                                    "No ACK for window ending block %d",
                                    window[-1][1],
                                )
                                return TransferOutcome(
                                    status="failed",
                                    error="No ACK for data window",
                                    retries=total_retries,
                                    timeouts=total_timeouts,
                                )
                            logger.debug(
                                "Timeout waiting ACK block=%d attempt=%d",
                                window[-1][1],
                                retries,
                            )
                            continue
                        break
        except OSError as exc:
            logger.error("RRQ read failed: %s", exc)
            self._send_error(data_sock, client_addr, 0, str(exc))
            return TransferOutcome(
                status="failed",
                error=str(exc),
                retries=total_retries,
                timeouts=total_timeouts,
            )

        logger.info(
            "RRQ done: %s (%d bytes, windowsize=%d)",
            pkt.filename,
            size,
            windowsize,
        )
        return TransferOutcome(
            bytes_transferred=size,
            retries=total_retries,
            timeouts=total_timeouts,
        )

    def handle_wrq(self, pkt, client_addr, data_sock) -> TransferOutcome:
        try:
            path = self.safe_path(pkt.filename)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.code, e.message)
            return TransferOutcome(status="failed", error=e.message)
        if path.exists() and not self.config.overwrite:
            self._send_error(data_sock, client_addr, 6, "File already exists")
            return TransferOutcome(status="failed", error="File already exists")

        cfg = self.config
        try:
            accepted = negotiate_options(
                pkt.options,
                is_upload=True,
                server_windowsize=cfg.windowsize,
            )
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.code, e.message)
            return TransferOutcome(status="failed", error=e.message)

        blksize = int(accepted.get("blksize", cfg.blksize))
        timeout = float(accepted.get("timeout", cfg.timeout))
        windowsize = int(accepted.get("windowsize", 1))
        data_sock.settimeout(timeout)

        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.part")
        output = None
        received = 0
        retries = 0
        timeouts = 0

        # Initial response
        if accepted:
            oack = pack_oack(accepted)
            self._send_raw(data_sock, oack, client_addr)
            acked, extra_retries, extra_timeouts = self._wait_ack(
                data_sock, client_addr, 0, timeout, retransmit=oack
            )
            retries += extra_retries
            timeouts += extra_timeouts
            if not acked:
                logger.warning("No ACK0 after OACK from %s", client_addr)
                return TransferOutcome(
                    status="failed",
                    error="No ACK0 after OACK",
                    retries=retries,
                    timeouts=timeouts,
                )
        else:
            self._send_raw(data_sock, pack_ack(0), client_addr)

        expected_index = 1
        last_block = 0
        window_count = 0
        consecutive_timeouts = 0

        try:
            output = open(temp_path, "wb")  # noqa: SIM115 - đóng trong finally
            while True:
                try:
                    raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
                    self._trace_received(raw, data_sock, peer)
                except (TimeoutError, ConnectionResetError):
                    timeouts += 1
                    consecutive_timeouts += 1
                    if consecutive_timeouts >= cfg.retries:
                        raise TimeoutError(
                            f"Timeout waiting DATA block "
                            f"{_block_for_index(expected_index)}"
                        ) from None
                    logger.debug(
                        "Timeout waiting DATA block=%d attempt=%d",
                        _block_for_index(expected_index),
                        consecutive_timeouts,
                    )
                    self._send_raw(data_sock, pack_ack(last_block), client_addr)
                    retries += 1
                    continue

                consecutive_timeouts = 0
                if peer != client_addr:
                    self._send_error(data_sock, peer, 5, "Unknown TID")
                    continue
                try:
                    incoming = unpack_packet(raw)
                except TFTPError:
                    continue

                if incoming.opcode == OP_ERROR:
                    logger.warning("Client error: %s", incoming.error_msg)
                    raise TFTPError(incoming.error_code, incoming.error_msg)
                if incoming.opcode != OP_DATA:
                    continue

                expected_block = _block_for_index(expected_index)
                if incoming.block == expected_block:
                    output.write(incoming.data)
                    received += len(incoming.data)
                    last_block = incoming.block
                    expected_index += 1
                    window_count += 1
                    is_final = len(incoming.data) < blksize

                    if is_final:
                        output.flush()
                        output.close()
                        output = None
                        os.replace(temp_path, path)
                        self._send_raw(
                            data_sock, pack_ack(incoming.block), client_addr
                        )
                        logger.info(
                            "WRQ done: %s (%d bytes, windowsize=%d)",
                            pkt.filename,
                            received,
                            windowsize,
                        )
                        return TransferOutcome(
                            bytes_transferred=received,
                            retries=retries,
                            timeouts=timeouts,
                        )

                    if window_count >= windowsize:
                        self._send_raw(
                            data_sock, pack_ack(incoming.block), client_addr
                        )
                        window_count = 0

                elif incoming.block == last_block:
                    # DATA trùng: ACK lại block cuối đã nhận.
                    self._send_raw(data_sock, pack_ack(last_block), client_addr)
                else:
                    # Sai thứ tự: ACK block cuối đúng để client gửi lại từ đó.
                    self._send_raw(data_sock, pack_ack(last_block), client_addr)

        except TFTPError as e:
            logger.warning("WRQ failed: %s", e.message)
            return TransferOutcome(
                status="failed",
                error=e.message,
                bytes_transferred=received,
                retries=retries,
                timeouts=timeouts,
            )
        except Exception as e:  # noqa: BLE001 - ghi nhận mọi lỗi phiên
            logger.error("WRQ failed: %s", e)
            self._send_error(data_sock, client_addr, 0, str(e))
            return TransferOutcome(
                status="failed",
                error=str(e),
                bytes_transferred=received,
                retries=retries,
                timeouts=timeouts,
            )
        finally:
            if output is not None:
                try:
                    output.close()
                except OSError:
                    pass
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _wait_ack(
        self,
        data_sock,
        client_addr,
        block: int,
        timeout: float,
        *,
        retransmit: bytes | None = None,
    ) -> tuple[bool, int, int]:
        """Chờ ACK; trả ``(thành_công, số_lần_gửi_lại, số_timeout)``."""
        data_sock.settimeout(timeout)
        retries = 0
        timeouts = 0
        for _ in range(self.config.retries):
            try:
                raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
            except (TimeoutError, ConnectionResetError):
                timeouts += 1
                if retransmit is not None:
                    try:
                        self._send_raw(data_sock, retransmit, client_addr)
                        retries += 1
                    except OSError:
                        pass
                continue
            self._trace_received(raw, data_sock, peer)
            if peer != client_addr:
                self._send_error(data_sock, peer, 5, "Unknown TID")
                continue
            try:
                pkt = unpack_packet(raw)
            except TFTPError:
                continue
            if pkt.opcode == OP_ERROR:
                return False, retries, timeouts
            if pkt.opcode == OP_ACK and pkt.block == block:
                return True, retries, timeouts
        return False, retries, timeouts

    def handle_transfer(self, pkt, client_addr) -> None:
        """Chạy một phiên, ghi metrics/trace và luôn trả slot semaphore."""
        direction = "download" if pkt.opcode == OP_RRQ else "upload"
        started = time.monotonic()
        outcome = TransferOutcome(status="failed", error="Phiên truyền không chạy")
        data_sock: socket.socket | None = None

        if self.metrics:
            self.metrics.start_session()
        try:
            data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            bind_host = "" if self.config.host in ("0.0.0.0", "") else self.config.host
            data_sock.bind((bind_host, 0))
            if pkt.opcode == OP_RRQ:
                outcome = self.handle_rrq(pkt, client_addr, data_sock)
            elif pkt.opcode == OP_WRQ:
                outcome = self.handle_wrq(pkt, client_addr, data_sock)
        except Exception as e:
            logger.exception("Transfer error")
            if data_sock is not None:
                self._send_error(data_sock, client_addr, 0, str(e))
            outcome = TransferOutcome(status="failed", error=str(e))
        finally:
            if data_sock is not None:
                data_sock.close()
            if self.metrics:
                self.metrics.end_session()
                try:
                    self.metrics.record(
                        client_ip=str(client_addr[0]),
                        filename=pkt.filename,
                        direction=direction,
                        bytes_total=max(outcome.bytes_transferred, 0),
                        duration=max(time.monotonic() - started, 0.0),
                        retries=outcome.retries,
                        timeouts=outcome.timeouts,
                        status=outcome.status,
                        error=outcome.error,
                    )
                except Exception:
                    logger.exception("Cannot record transfer metrics")
            # Slot được lấy trước khi tạo thread trong serve_forever().
            self._sem.release()

    def _tcp_listing_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        bind_host = "" if self.config.host in ("0.0.0.0", "") else self.config.host
        try:
            sock.bind((bind_host, self.config.port + 1))
        except OSError as e:
            logger.warning("Cannot bind listing port %d: %s", self.config.port + 1, e)
            return
        sock.listen(8)
        self._list_sock = sock
        logger.info("TFTP listing service listening on %s:%d", bind_host or "0.0.0.0", self.config.port + 1)
        while not self._stop.is_set():
            try:
                sock.settimeout(1.0)
                conn, _addr = sock.accept()
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_listing_conn, args=(conn,), daemon=True).start()

    def _handle_listing_conn(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(self.config.timeout)
            data = bytearray()
            while b"\n" not in data and len(data) < 1024 * 1024:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
            request: dict = {}
            if data:
                try:
                    request = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    request = {}
            if request.get("action") != "list":
                response = {"error": "Unknown action"}
            else:
                try:
                    files = sorted(p.name for p in self.root.iterdir() if p.is_file())
                except OSError as e:
                    response = {"error": str(e)}
                else:
                    response = {"files": files}
            conn.sendall((json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            try:
                conn.sendall((json.dumps({"error": str(e)}) + "\n").encode("utf-8"))
            except OSError:
                pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def serve_forever(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((self.config.host, self.config.port))
        self.sock = sock
        self._list_sock = None
        threading.Thread(target=self._tcp_listing_loop, daemon=True).start()
        logger.info("TFTP server listening on %s:%d, root=%s", self.config.host, self.config.port, self.root)
        while not self._stop.is_set():
            try:
                self.sock.settimeout(1.0)
                data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                break
            try:
                pkt = unpack_packet(data)
            except TFTPError as e:
                self._send_error(self.sock, addr, e.code, e.message)
                continue
            self._trace_received(data, self.sock, addr)
            if pkt.opcode not in (OP_RRQ, OP_WRQ):
                self._send_error(self.sock, addr, 4, "Illegal operation")
                continue
            # Giới hạn số thread chuyển file đang chạy. Không tạo thread rồi
            # để nó chờ semaphore vì sẽ tích tụ vô hạn khi bị flood.
            if not self._sem.acquire(blocking=False):
                logger.warning("Too many clients, rejecting request from %s", addr)
                self._send_error(self.sock, addr, 0, "Server busy")
                continue
            try:
                t = threading.Thread(
                    target=self.handle_transfer, args=(pkt, addr), daemon=True
                )
                t.start()
            except Exception:
                self._sem.release()
                logger.exception("Cannot start transfer thread")
                self._send_error(self.sock, addr, 0, "Server busy")

    def stop(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        list_sock = getattr(self, "_list_sock", None)
        if list_sock:
            try:
                list_sock.close()
            except OSError:
                pass


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="tftp-server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--root", default=DEFAULT_ROOT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--max-clients", type=int, default=DEFAULT_MAX_CLIENTS)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--blksize", type=int, default=DEFAULT_BLKSIZE)
    parser.add_argument("--windowsize", type=int, default=DEFAULT_MAX_WINDOWSIZE)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(message)s"
    )

    config = ServerConfig(
        host=args.host,
        port=args.port,
        root=Path(args.root),
        timeout=args.timeout,
        retries=args.retries,
        max_clients=args.max_clients,
        overwrite=args.overwrite,
        blksize=args.blksize,
        windowsize=args.windowsize,
        log_level=args.log_level,
    )
    server = TFTPServer(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
