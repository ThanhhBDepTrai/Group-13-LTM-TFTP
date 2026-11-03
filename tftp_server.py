"""TFTP server đa luồng (RFC 1350 + 2347/2348/2349/7440).

Mỗi yêu cầu RRQ/WRQ được xử lý trong một thread với socket UDP riêng (TID riêng).
Server không biết gì về database, tài khoản hay API: mọi thứ đó gắn vào qua
:class:`ServerHooks` (xem ``storage_manager.ManagedHooks``).
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import random
import socket
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from tftp_config import ServerConfig, safe_join
from tftp_options import Negotiated, negotiate_server
from tftp_packet import (
    OP_ACK,
    OP_ERROR,
    OP_RRQ,
    OP_WRQ,
    Packet,
    TFTPError,
    pack_ack,
    pack_error,
    pack_oack,
    unpack_packet,
)
from tftp_transfer import (
    Channel,
    RemoteError,
    TransferError,
    TransferStats,
    TransferTimeout,
    linger,
    make_udp_socket,
    receive_blocks,
    send_blocks,
)

__all__ = ["ServerHooks", "TFTPServer", "TransferRecord", "main"]

log = logging.getLogger("tftp.server")


@dataclass
class TransferRecord:
    """Bản ghi một phiên truyền, gửi cho ``ServerHooks.on_transfer``."""

    op: str  # "RRQ" | "WRQ"
    filename: str
    client: str
    path: str = ""
    status: str = "error"  # "ok" | "error"
    error: str = ""
    bytes: int = 0
    blocks: int = 0
    retransmits: int = 0
    packets_sent: int = 0
    blksize: int = 512
    windowsize: int = 1
    duration: float = 0.0
    throughput_mbps: float = 0.0
    started_at: float = 0.0
    sha256: str = ""  # do hook điền sau khi phiên kết thúc

    def to_dict(self) -> dict:
        return asdict(self)


class ServerHooks:
    """Điểm mở rộng cho server; mặc định không làm gì."""

    def authorize(self, op: str, filename: str, client: tuple) -> None:
        """Raise ``TFTPError(2, ...)`` để từ chối yêu cầu."""

    def resolve(self, op: str, filename: str, client: tuple) -> Path | None:
        """Trả về đường dẫn thật trên đĩa, hoặc ``None`` để dùng thư mục gốc mặc định."""
        return None

    def on_transfer(self, record: TransferRecord) -> None:
        """Gọi một lần khi phiên kết thúc (thành công hay thất bại)."""


class TFTPServer:
    def __init__(self, config: ServerConfig | None = None, hooks: ServerHooks | None = None):
        self.config = config or ServerConfig()
        self.config.validate()
        self.hooks = hooks or ServerHooks()
        self.history: deque[TransferRecord] = deque(maxlen=1000)
        self._sock: socket.socket | None = None
        self._family = socket.AF_INET
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._slots = threading.BoundedSemaphore(self.config.max_clients)
        self._active: set[tuple] = set()
        self._cond = threading.Condition()

    # ------------------------------------------------------------------ #
    # Vòng đời
    # ------------------------------------------------------------------ #

    @property
    def address(self) -> tuple[str, int]:
        if self._sock is None:
            raise RuntimeError("Server is not bound yet")
        return self._sock.getsockname()[:2]

    @property
    def active_sessions(self) -> int:
        with self._cond:
            return len(self._active)

    def bind(self) -> tuple[str, int]:
        if self._sock is None:
            Path(self.config.root).mkdir(parents=True, exist_ok=True)
            info = socket.getaddrinfo(
                self.config.host or None,
                self.config.port,
                type=socket.SOCK_DGRAM,
                flags=socket.AI_PASSIVE,
            )[0]
            self._family = info[0]
            self._sock = make_udp_socket(self._family, self.config.host, self.config.port)
        return self.address

    def start(self) -> TFTPServer:
        """Chạy server nền (dùng cho test, benchmark, run_all)."""
        self.bind()
        self._thread = threading.Thread(target=self.serve_forever, name="tftp-main", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> TFTPServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Chờ cho tới khi không còn phiên nào đang chạy (hữu ích cho test)."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._active, timeout)

    def serve_forever(self) -> None:
        self.bind()
        sock = self._sock
        assert sock is not None
        log.info("TFTP server listening on %s:%s root=%s", *self.address, self.config.root)
        while not self._stop.is_set():
            sock.settimeout(0.2)
            try:
                raw, addr = sock.recvfrom(2048)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                break
            self._dispatch(raw, addr)

    # ------------------------------------------------------------------ #
    # Tiếp nhận yêu cầu
    # ------------------------------------------------------------------ #

    def _reply_error(self, addr: tuple, code: int, message: str) -> None:
        if self._sock is not None:
            with contextlib.suppress(OSError):
                self._sock.sendto(pack_error(code, message), addr)

    def _dispatch(self, raw: bytes, addr: tuple) -> None:
        try:
            pkt = unpack_packet(raw)
        except TFTPError as exc:
            self._reply_error(addr, exc.code, exc.message)
            return
        if pkt.opcode not in (OP_RRQ, OP_WRQ):
            self._reply_error(addr, 4, "Illegal TFTP operation")
            return
        key = (addr[:2], pkt.opcode, pkt.filename)
        with self._cond:
            if key in self._active:
                return  # client gửi lại yêu cầu trong lúc phiên đang chạy
            if not self._slots.acquire(blocking=False):
                self._reply_error(addr, 0, "Server busy")
                return
            self._active.add(key)
        threading.Thread(
            target=self._run_session, args=(pkt, addr, key), name="tftp-session", daemon=True
        ).start()

    # ------------------------------------------------------------------ #
    # Một phiên
    # ------------------------------------------------------------------ #

    def _run_session(self, pkt: Packet, addr: tuple, key: tuple) -> None:
        cfg = self.config
        op = "RRQ" if pkt.opcode == OP_RRQ else "WRQ"
        record = TransferRecord(
            op=op,
            filename=pkt.filename,
            client=f"{addr[0]}:{addr[1]}",
            blksize=cfg.blksize,
            windowsize=cfg.windowsize,
            started_at=time.time(),
        )
        sock = make_udp_socket(self._family, "" if cfg.host in ("0.0.0.0", "::") else cfg.host)
        stats = TransferStats()
        chan = Channel(sock, addr, loss=cfg.simulate_loss, rng=random.Random(), stats=stats)
        dally = None
        try:
            self.hooks.authorize(op, pkt.filename, addr)
            path = self.hooks.resolve(op, pkt.filename, addr)
            if path is None:
                path = safe_join(cfg.root, pkt.filename, ignore_case=cfg.pxe_mode)
            record.path = str(path)
            if pkt.opcode == OP_RRQ:
                self._serve_read(chan, pkt, path, record)
            else:
                dally = self._serve_write(chan, pkt, path, record)
            record.status = "ok"
        except TFTPError as exc:
            record.error = f"{exc.code}: {exc.message}"
            chan.send(pack_error(exc.code, exc.message))
        except RemoteError as exc:
            record.error = str(exc)
        except TransferTimeout:
            record.error = "timeout"
        except TransferError as exc:
            record.error = str(exc)
        except OSError as exc:
            record.error = f"OS error: {exc}"
            chan.send(pack_error(2, "Access violation"))
        except Exception as exc:  # noqa: BLE001 - phiên lỗi không được làm sập server
            log.exception("Unexpected error in session %s", record.client)
            record.error = f"internal: {exc}"
            chan.send(pack_error(0, "Internal server error"))

        stats.finish()
        record.bytes = stats.bytes
        record.blocks = stats.blocks
        record.retransmits = stats.retransmits
        record.packets_sent = stats.packets_sent
        record.duration = stats.duration
        record.throughput_mbps = stats.throughput_mbps if record.status == "ok" else 0.0
        self.history.append(record)
        log.info(
            "%s %s from %s -> %s (%d bytes, %.3fs)",
            op, pkt.filename, record.client, record.status, record.bytes, record.duration,
        )
        try:
            self.hooks.on_transfer(record)
        except Exception:  # noqa: BLE001
            log.exception("on_transfer hook failed")
        try:
            if dally is not None:
                dally()
        finally:
            sock.close()
            with self._cond:
                self._active.discard(key)
                self._cond.notify_all()
            self._slots.release()

    def _handshake(self, chan: Channel, oack: bytes, neg: Negotiated) -> None:
        """Gửi OACK và chờ ACK 0 (gửi lại OACK khi timeout)."""
        misses = 0
        chan.send(oack)
        while True:
            try:
                pkt = chan.recv(neg.timeout)
            except TimeoutError:
                misses += 1
                if misses > self.config.retries:
                    raise TransferTimeout("No ACK for OACK") from None
                chan.send(oack)
                chan.stats.retransmits += 1
                continue
            if pkt.opcode == OP_ERROR:
                raise RemoteError(pkt.error_code, pkt.error_msg)
            if pkt.opcode == OP_ACK and pkt.block == 0:
                return

    def _serve_read(self, chan: Channel, pkt: Packet, path: Path, record: TransferRecord) -> None:
        if not path.is_file():
            raise TFTPError(1, "File not found")
        cfg = self.config
        neg = negotiate_server(pkt.options, cfg, file_size=path.stat().st_size)
        record.blksize, record.windowsize = neg.blksize, neg.windowsize
        try:
            handle = path.open("rb")
        except PermissionError:
            raise TFTPError(2, "Access violation") from None
        with handle:
            if neg.oack:
                self._handshake(chan, pack_oack(neg.oack), neg)
            send_blocks(
                chan,
                handle,
                blksize=neg.blksize,
                windowsize=neg.windowsize,
                timeout=neg.timeout,
                retries=cfg.retries,
            )

    def _serve_write(self, chan: Channel, pkt: Packet, path: Path, record: TransferRecord):
        cfg = self.config
        if not cfg.allow_write:
            raise TFTPError(2, "Write not allowed")
        if path.is_dir():
            raise TFTPError(2, "Access violation")
        if path.exists() and not cfg.allow_overwrite:
            raise TFTPError(6, "File already exists")
        if not path.parent.is_dir():
            raise TFTPError(1, "Directory not found")
        neg = negotiate_server(pkt.options, cfg, is_write=True)
        record.blksize, record.windowsize = neg.blksize, neg.windowsize

        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.part")
        first = pack_oack(neg.oack) if neg.oack else pack_ack(0)
        chan.send(first)
        try:
            with tmp.open("wb") as handle:
                receive_blocks(
                    chan,
                    handle,
                    blksize=neg.blksize,
                    windowsize=neg.windowsize,
                    timeout=neg.timeout,
                    retries=cfg.retries,
                    last_sent=first,
                    max_bytes=cfg.max_file_size,
                )
            os.replace(tmp, path)  # chỉ lộ file khi nhận đủ
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise
        seconds = neg.timeout if cfg.linger < 0 else cfg.linger
        final_ack = pack_ack(chan.stats.blocks & 0xFFFF)
        return lambda: linger(chan, final_ack, seconds)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TFTP Pro server")
    parser.add_argument("--config", help="file cấu hình JSON")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--root")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--max-blksize", type=int, dest="max_blksize")
    parser.add_argument("--max-windowsize", type=int, dest="max_windowsize")
    parser.add_argument("--max-clients", type=int, dest="max_clients")
    parser.add_argument("--read-only", action="store_true", help="không cho upload")
    parser.add_argument("--allow-overwrite", action="store_true")
    parser.add_argument("--pxe", action="store_true", help="chế độ PXE/boot file")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    overrides = {
        k: getattr(args, k)
        for k in ("host", "port", "root", "timeout", "retries", "max_blksize",
                  "max_windowsize", "max_clients")
    }
    if args.read_only:
        overrides["allow_write"] = False
    if args.allow_overwrite:
        overrides["allow_overwrite"] = True
    if args.pxe:
        overrides["pxe_mode"] = True
    config = ServerConfig.load(args.config, **overrides)

    server = TFTPServer(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
