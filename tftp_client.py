"""TFTP client (get/put) với option negotiation, window size và retry.

Dùng như thư viện::

    client = TFTPClient("127.0.0.1", 6969, ClientConfig(blksize=1428, windowsize=8))
    stats = client.get("boot/kernel", "kernel.bin")
    print(stats.throughput_mbps)

hoặc từ dòng lệnh::

    python tftp_client.py --host 127.0.0.1 --port 6969 get README.md out.md
    python tftp_client.py --host 127.0.0.1 --port 6969 put out.md README.md
"""

from __future__ import annotations

import argparse
import contextlib
import os
import random
import shutil
import socket
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from tftp_config import ClientConfig, ConfigError
from tftp_options import Negotiated, build_request_options, parse_oack
from tftp_packet import (
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    Packet,
    TFTPError,
    pack_ack,
    pack_error,
    pack_rrq,
    pack_wrq,
)
from tftp_transfer import (
    Channel,
    RemoteError,
    TransferError,
    TransferStats,
    TransferTimeout,
    make_udp_socket,
    receive_blocks,
    send_blocks,
)

__all__ = ["TFTPClient", "main"]

Progress = Callable[[int], None]


class TFTPClient:
    def __init__(self, host: str, port: int = 69, config: ClientConfig | None = None):
        self.config = config or ClientConfig()
        self.config.validate()
        info = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
        self.family = info[0]
        self.server = info[4][:2]

    # ------------------------------------------------------------------ #

    def _channel(self, sock: socket.socket) -> Channel:
        return Channel(
            sock,
            self.server,
            tid_locked=False,
            loss=self.config.simulate_loss,
            rng=random.Random(),
            stats=TransferStats(),
        )

    def _first_reply(self, chan: Channel, request: bytes, accept: tuple[int, ...]) -> Packet:
        """Gửi yêu cầu và chờ trả lời đầu tiên; gửi lại yêu cầu khi timeout."""
        cfg = self.config
        misses = 0
        chan.send(request)
        while True:
            try:
                pkt = chan.recv(cfg.timeout)
            except TimeoutError:
                misses += 1
                if misses > cfg.retries:
                    raise TransferTimeout("No response from server") from None
                chan.send(request)
                chan.stats.retransmits += 1
                continue
            if pkt.opcode == OP_ERROR:
                raise RemoteError(pkt.error_code, pkt.error_msg)
            if pkt.opcode in accept:
                return pkt

    def _read_oack(self, chan: Channel, requested: dict[str, str], pkt: Packet) -> Negotiated:
        try:
            return parse_oack(requested, pkt.options, default_timeout=self.config.timeout)
        except TFTPError as exc:
            chan.send(pack_error(exc.code, exc.message))
            raise TransferError(f"Bad OACK: {exc.message}") from exc

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #

    def get(self, remote: str, local: str | os.PathLike[str], *,
            progress: Progress | None = None) -> TransferStats:
        cfg = self.config
        requested = build_request_options(cfg, tsize=0)
        destination = Path(local)
        tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex[:8]}.part")
        sock = make_udp_socket(self.family)
        chan = self._channel(sock)
        try:
            request = pack_rrq(remote, requested)
            pkt = self._first_reply(chan, request, (OP_OACK, OP_DATA))
            first: Packet | None = None
            neg = Negotiated(timeout=cfg.timeout)
            last_sent = request
            if pkt.opcode == OP_OACK:
                neg = self._read_oack(chan, requested, pkt)
                last_sent = pack_ack(0)
                chan.send(last_sent)
            else:
                first = pkt  # server bỏ qua option: DATA 1 đến ngay

            if neg.tsize is not None:
                free = shutil.disk_usage(destination.resolve().parent).free
                if neg.tsize > free:
                    chan.send(pack_error(3, "Disk full"))
                    raise TransferError("Not enough disk space for download")

            with tmp.open("wb") as handle:
                stats = receive_blocks(
                    chan,
                    handle,
                    blksize=neg.blksize,
                    windowsize=neg.windowsize,
                    timeout=neg.timeout,
                    retries=cfg.retries,
                    last_sent=last_sent,
                    first=first,
                    linger_seconds=cfg.linger,
                    progress=progress,
                )
            if neg.tsize is not None and neg.tsize != stats.bytes:
                raise TransferError(f"Size mismatch: expected {neg.tsize}, got {stats.bytes}")
            os.replace(tmp, destination)
            return stats
        finally:
            sock.close()
            with contextlib.suppress(OSError):
                tmp.unlink()

    # ------------------------------------------------------------------ #
    # Upload
    # ------------------------------------------------------------------ #

    def put(self, local: str | os.PathLike[str], remote: str, *,
            progress: Progress | None = None) -> TransferStats:
        cfg = self.config
        source = Path(local)
        size = source.stat().st_size
        requested = build_request_options(cfg, tsize=size)
        sock = make_udp_socket(self.family)
        chan = self._channel(sock)
        try:
            pkt = self._first_reply(chan, pack_wrq(remote, requested), (OP_OACK, OP_ACK))
            if pkt.opcode == OP_OACK:
                neg = self._read_oack(chan, requested, pkt)
            elif pkt.block == 0:
                neg = Negotiated(timeout=cfg.timeout)
            else:
                raise TransferError("Unexpected ACK block in upload handshake")
            with source.open("rb") as handle:
                return send_blocks(
                    chan,
                    handle,
                    blksize=neg.blksize,
                    windowsize=neg.windowsize,
                    timeout=neg.timeout,
                    retries=cfg.retries,
                    progress=progress,
                )
        finally:
            sock.close()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TFTP Pro client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=69)
    parser.add_argument("--blksize", type=int)
    parser.add_argument("--windowsize", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--retries", type=int)
    parser.add_argument("--no-tsize", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    get = sub.add_parser("get", help="tải file từ server")
    get.add_argument("remote")
    get.add_argument("local")
    put = sub.add_parser("put", help="gửi file lên server")
    put.add_argument("local")
    put.add_argument("remote")
    args = parser.parse_args(argv)

    overrides = {k: getattr(args, k) for k in ("blksize", "windowsize", "timeout", "retries")}
    if args.no_tsize:
        overrides["request_tsize"] = False
    try:
        config = ClientConfig.load(None, **overrides)
        client = TFTPClient(args.host, args.port, config)
        if args.command == "get":
            stats = client.get(args.remote, args.local)
        else:
            stats = client.put(args.local, args.remote)
    except (ConfigError, TransferError, TFTPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not args.quiet:
        print(
            f"{args.command}: {stats.bytes} bytes in {stats.duration:.3f}s "
            f"({stats.throughput_mbps:.2f} Mbit/s), retransmits={stats.retransmits}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
