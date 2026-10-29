"""TFTP server (RFC 1350) with optional blksize/timeout/tsize negotiation."""

from __future__ import annotations

import argparse
import logging

logger = logging.getLogger(__name__)
import os
import socket
import threading
from dataclasses import dataclass
from pathlib import Path

from tftp_packet import (
    BLOCK_SIZE,
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


# Local negotiate to avoid import cycle
def negotiate_options(requested: dict[str, str], file_size: int | None = None) -> dict[str, str]:
    accepted: dict[str, str] = {}
    if "blksize" in requested:
        try:
            value = int(requested["blksize"])
        except (TypeError, ValueError):
            raise TFTPError(8, "Invalid blksize")
        if not (8 <= value <= 65464):
            raise TFTPError(8, "blksize must be 8..65464")
        accepted["blksize"] = str(value)
    if "timeout" in requested:
        try:
            value = int(requested["timeout"])
        except (TypeError, ValueError):
            raise TFTPError(8, "Invalid timeout")
        if not (1 <= value <= 255):
            raise TFTPError(8, "timeout must be 1..255")
        accepted["timeout"] = str(value)
    if "tsize" in requested:
        if file_size is None:
            accepted["tsize"] = requested["tsize"]
        else:
            accepted["tsize"] = str(file_size)
    return accepted


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 6969
    root: Path = Path("server_storage")
    timeout: float = 3.0
    retries: int = 5
    max_clients: int = 20
    overwrite: bool = False
    blksize: int = BLOCK_SIZE
    log_level: str = "INFO"


class TFTPServer:
    def __init__(self, config: ServerConfig):
        self.config = config
        self.root = config.root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._sem = threading.Semaphore(config.max_clients)
        self._stop = threading.Event()
        self.sock: socket.socket | None = None

    # ---------- path safety ----------
    def safe_path(self, filename: str) -> Path:
        if not filename or "\x00" in filename:
            raise TFTPError(2, "Invalid filename")
        # Normalize separators to POSIX then to local
        name = filename.replace("\\", "/")
        if name.startswith("/") or ":" in name.split("/")[0]:
            raise TFTPError(2, "Absolute path not allowed")
        parts = [p for p in name.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            raise TFTPError(2, "Path traversal not allowed")
        candidate = self.root.joinpath(*parts).resolve() if parts else self.root
        # Ensure within root
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise TFTPError(2, "Path outside root")
        # Symlink escape check
        if candidate.exists() and candidate.resolve() != candidate:
            pass
        return candidate

    def _send_error(self, sock: socket.socket, addr, code: int, msg: str) -> None:
        try:
            sock.sendto(pack_error(code, msg), addr)
        except OSError:
            pass

    # ---------- handlers ----------
    def handle_rrq(self, pkt, client_addr, data_sock) -> None:
        try:
            path = self.safe_path(pkt.filename)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.error_code, e.error_msg)
            return
        if not path.is_file():
            self._send_error(data_sock, client_addr, 1, "File not found")
            return

        cfg = self.config
        blksize = cfg.blksize
        timeout = cfg.timeout
        accepted: dict[str, str] = {}
        try:
            if pkt.options:
                accepted = negotiate_options(pkt.options, file_size=path.stat().st_size)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.error_code, e.error_msg)
            return

        if "blksize" in accepted:
            blksize = int(accepted["blksize"])
        if "timeout" in accepted:
            timeout = float(accepted["timeout"])
        data_sock.settimeout(timeout)

        # OACK handshake
        if accepted:
            data_sock.sendto(pack_oack(accepted), client_addr)
            # wait ACK 0
            if not self._wait_ack(data_sock, client_addr, 0, timeout):
                logger.warning("No ACK0 after OACK from %s", client_addr)
                return

        block = 1
        with open(path, "rb") as f:
            while True:
                chunk = f.read(blksize)
                for attempt in range(1, cfg.retries + 1):
                    try:
                        data_sock.sendto(pack_data(block, chunk), client_addr)
                        raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
                    except TimeoutError:
                        logger.debug("Timeout waiting ACK block=%d attempt=%d", block, attempt)
                        continue
                    if peer != client_addr:
                        self._send_error(data_sock, peer, 5, "Unknown TID")
                        continue
                    try:
                        ack = unpack_packet(raw)
                    except TFTPError:
                        continue
                    if ack.opcode == OP_ERROR:
                        logger.warning("Client error: %s", ack.error_msg)
                        return
                    if ack.opcode == OP_ACK and ack.block == block:
                        break
                    # duplicate / old ACK: resend
                else:
                    logger.error("No ACK for block %d", block)
                    return
                if len(chunk) < blksize:
                    logger.info("RRQ done: %s (%d bytes)", pkt.filename, (block - 1) * blksize + len(chunk))
                    return
                block = (block + 1) & 0xFFFF

    def handle_wrq(self, pkt, client_addr, data_sock) -> None:
        try:
            path = self.safe_path(pkt.filename)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.error_code, e.error_msg)
            return
        if path.exists() and not self.config.overwrite:
            self._send_error(data_sock, client_addr, 6, "File already exists")
            return

        cfg = self.config
        blksize = cfg.blksize
        timeout = cfg.timeout
        accepted: dict[str, str] = {}
        try:
            if pkt.options:
                accepted = negotiate_options(pkt.options, file_size=None)
        except TFTPError as e:
            self._send_error(data_sock, client_addr, e.error_code, e.error_msg)
            return

        if "blksize" in accepted:
            blksize = int(accepted["blksize"])
        if "timeout" in accepted:
            timeout = float(accepted["timeout"])
        data_sock.settimeout(timeout)

        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".part")

        # Initial response
        if accepted:
            data_sock.sendto(pack_oack(accepted), client_addr)
            if not self._wait_ack(data_sock, client_addr, 0, timeout):
                logger.warning("No ACK0 after OACK from %s", client_addr)
                # cleanup
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                return
        else:
            data_sock.sendto(pack_ack(0), client_addr)

        expected_block = 1
        try:
            with open(temp_path, "wb") as f:
                while True:
                    try:
                        raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
                    except TimeoutError:
                        logger.error("Timeout waiting DATA block %d", expected_block)
                        raise TimeoutError
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
                    if incoming.block == expected_block:
                        f.write(incoming.data)
                        f.flush()
                        is_last = len(incoming.data) < blksize
                        if is_last:
                            f.close()
                            os.replace(temp_path, path)
                            data_sock.sendto(pack_ack(expected_block), client_addr)
                            break
                        data_sock.sendto(pack_ack(expected_block), client_addr)
                        expected_block = (expected_block + 1) & 0xFFFF
                    elif incoming.block < expected_block:
                        # duplicate DATA
                        data_sock.sendto(pack_ack(incoming.block), client_addr)
                    # else ignore out-of-order
            os.replace(temp_path, path)
            logger.info("WRQ done: %s", pkt.filename)
        except Exception as e:  # noqa: BLE001
            logger.error("WRQ failed: %s", e)
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            if not isinstance(e, TFTPError):
                self._send_error(data_sock, client_addr, 0, str(e))

    def _wait_ack(self, data_sock, client_addr, block: int, timeout: float) -> bool:
        data_sock.settimeout(timeout)
        for _ in range(self.config.retries):
            try:
                raw, peer = data_sock.recvfrom(MAX_PACKET_SIZE)
            except TimeoutError:
                continue
            if peer != client_addr:
                self._send_error(data_sock, peer, 5, "Unknown TID")
                continue
            try:
                pkt = unpack_packet(raw)
            except TFTPError:
                continue
            if pkt.opcode == OP_ERROR:
                return False
            if pkt.opcode == OP_ACK and pkt.block == block:
                return True
        return False

    def handle_transfer(self, pkt, client_addr) -> None:
        with self._sem:
            data_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            bind_host = "" if self.config.host in ("0.0.0.0", "") else self.config.host
            data_sock.bind((bind_host, 0))
            try:
                if pkt.opcode == OP_RRQ:
                    self.handle_rrq(pkt, client_addr, data_sock)
                elif pkt.opcode == OP_WRQ:
                    self.handle_wrq(pkt, client_addr, data_sock)
            except Exception as e:
                logger.exception("Transfer error")
                self._send_error(data_sock, client_addr, 0, str(e))
            finally:
                data_sock.close()

    def serve_forever(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((self.config.host, self.config.port))
        logger.info("TFTP server listening on %s:%d, root=%s", self.config.host, self.config.port, self.root)
        while not self._stop.is_set():
            try:
                self.sock.settimeout(1.0)
                data, addr = self.sock.recvfrom(MAX_PACKET_SIZE)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                pkt = unpack_packet(data)
            except TFTPError as e:
                self._send_error(self.sock, addr, e.error_code, e.error_msg)
                continue
            if pkt.opcode not in (OP_RRQ, OP_WRQ):
                self._send_error(self.sock, addr, 4, "Illegal operation")
                continue
            t = threading.Thread(target=self.handle_transfer, args=(pkt, addr), daemon=True)
            t.start()

    def stop(self) -> None:
        self._stop.set()
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tftp-server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6969)
    parser.add_argument("--root", default="server_storage")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--max-clients", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--blksize", type=int, default=BLOCK_SIZE)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logger.basicConfig(
        level=getattr(logging, args.log_level.upper(), logger.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
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
