"""TFTP client core (RFC 1350) with retry, TID validation and atomic writes."""

from __future__ import annotations

import argparse
import os
import socket
from collections.abc import Callable
from pathlib import Path

from tftp_packet import (
    BLOCK_SIZE,
    MAX_PACKET_SIZE,
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    TFTPError,
    pack_ack,
    pack_data,
    pack_error,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)


class TFTPErrorClient(Exception):
    pass


class TFTPClient:
    def __init__(
        self,
        host: str,
        port: int = 69,
        timeout: float = 3.0,
        retries: int = 5,
        blksize: int = BLOCK_SIZE,
        on_log: Callable[[str], None] | None = None,
    ):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.blksize = blksize
        self.on_log = on_log or (lambda m: None)

    def _log(self, msg: str) -> None:
        self.on_log(msg)

    def download(
        self,
        remote: str,
        local: str | Path,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: object | None = None,
    ) -> int:
        local = Path(local)
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(local.name + ".part")
        if tmp.exists():
            tmp.unlink()

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        sock.settimeout(self.timeout)
        server_tid = None
        total_size = None
        expected_block = 1
        blksize = self.blksize
        timeout = self.timeout
        received = 0

        options = {}
        if blksize != BLOCK_SIZE:
            options["blksize"] = str(blksize)
        options["timeout"] = str(int(timeout))
        options["tsize"] = "0"

        try:
            sock.sendto(pack_rrq(remote, options=options), (self.host, self.port))
            while True:
                if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                    raise TFTPErrorClient("Cancelled")
                for attempt in range(1, self.retries + 1):
                    try:
                        raw, peer = sock.recvfrom(MAX_PACKET_SIZE)
                        break
                    except TimeoutError:
                        self._log(f"Timeout waiting DATA, retry {attempt}")
                        if expected_block > 1:
                            sock.sendto(pack_ack(expected_block - 1), server_tid or (self.host, self.port))
                        else:
                            sock.sendto(pack_rrq(remote, options=options), (self.host, self.port))
                else:
                    raise TimeoutError("No response from server")

                if server_tid is None:
                    server_tid = peer
                if peer != server_tid:
                    sock.sendto(pack_error(5, "Unknown TID"), peer)
                    continue

                try:
                    pkt = unpack_packet(raw)
                except TFTPError as e:
                    raise TFTPErrorClient(str(e))

                if pkt.opcode == OP_ERROR:
                    raise TFTPErrorClient(f"Server error {pkt.error_code}: {pkt.error_msg}")
                if pkt.opcode == OP_OACK:
                    if "blksize" in pkt.options:
                        blksize = int(pkt.options["blksize"])
                    if "timeout" in pkt.options:
                        timeout = float(pkt.options["timeout"])
                        sock.settimeout(timeout)
                    if "tsize" in pkt.options:
                        try:
                            total_size = int(pkt.options["tsize"])
                        except ValueError:
                            total_size = None
                    sock.sendto(pack_ack(0), server_tid)
                    continue
                if pkt.opcode != OP_DATA:
                    continue

                if pkt.block == expected_block:
                    with open(tmp, "ab") as f:
                        f.write(pkt.data)
                    received += len(pkt.data)
                    sock.sendto(pack_ack(expected_block), server_tid)
                    if on_progress:
                        on_progress(received, total_size)
                    if len(pkt.data) < blksize:
                        os.replace(tmp, local)
                        self._log(f"Downloaded {received} bytes")
                        return received
                    expected_block = (expected_block + 1) & 0xFFFF
                elif pkt.block < expected_block:
                    sock.sendto(pack_ack(pkt.block), server_tid)
        finally:
            sock.close()
            if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def upload(
        self,
        local: str | Path,
        remote: str | None = None,
        on_progress: Callable[[int, int | None], None] | None = None,
        cancel: object | None = None,
    ) -> int:
        local = Path(local)
        if remote is None:
            remote = local.name
        size = local.stat().st_size

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        sock.settimeout(self.timeout)
        server_tid = None
        blksize = self.blksize
        timeout = self.timeout
        sent = 0
        block = 1

        options = {}
        if blksize != BLOCK_SIZE:
            options["blksize"] = str(blksize)
        options["timeout"] = str(int(timeout))
        options["tsize"] = str(size)

        try:
            sock.sendto(pack_wrq(remote, options=options), (self.host, self.port))
            for attempt in range(1, self.retries + 1):
                try:
                    raw, peer = sock.recvfrom(MAX_PACKET_SIZE)
                    break
                except TimeoutError:
                    self._log(f"Timeout waiting ACK0, retry {attempt}")
                    sock.sendto(pack_wrq(remote, options=options), (self.host, self.port))
            else:
                raise TimeoutError("No response from server")

            server_tid = peer
            pkt = unpack_packet(raw)
            if pkt.opcode == OP_ERROR:
                raise TFTPErrorClient(f"Server error {pkt.error_code}: {pkt.error_msg}")
            if pkt.opcode == OP_OACK:
                if "blksize" in pkt.options:
                    blksize = int(pkt.options["blksize"])
                if "timeout" in pkt.options:
                    timeout = float(pkt.options["timeout"])
                    sock.settimeout(timeout)
                sock.sendto(pack_ack(0), server_tid)
            elif pkt.opcode == OP_ACK and pkt.block == 0:
                pass
            else:
                raise TFTPErrorClient("Unexpected response to WRQ")

            with open(local, "rb") as f:
                while True:
                    if cancel is not None and getattr(cancel, "is_set", lambda: False)():
                        raise TFTPErrorClient("Cancelled")
                    chunk = f.read(blksize)
                    for attempt in range(1, self.retries + 1):
                        sock.sendto(pack_data(block, chunk), server_tid)
                        try:
                            raw, peer = sock.recvfrom(MAX_PACKET_SIZE)
                            break
                        except TimeoutError:
                            self._log(f"Timeout waiting ACK block {block}, retry {attempt}")
                    else:
                        raise TimeoutError(f"No ACK for block {block}")
                    if peer != server_tid:
                        sock.sendto(pack_error(5, "Unknown TID"), peer)
                        continue
                    ack = unpack_packet(raw)
                    if ack.opcode == OP_ERROR:
                        raise TFTPErrorClient(f"Server error {ack.error_code}: {ack.error_msg}")
                    if ack.opcode == OP_ACK and ack.block == block:
                        sent += len(chunk)
                        if on_progress:
                            on_progress(sent, size)
                        if len(chunk) < blksize:
                            self._log(f"Uploaded {sent} bytes")
                            return sent
                        block = (block + 1) & 0xFFFF
                        break
        finally:
            sock.close()

    def __repr__(self) -> str:
        return f"TFTPClient({self.host!r}, {self.port})"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tftp-client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=69)
    sub = parser.add_subparsers(dest="cmd", required=True)
    get = sub.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local")
    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote", nargs="?")
    args = parser.parse_args(argv)

    client = TFTPClient(args.host, args.port, on_log=print)
    if args.cmd == "get":
        n = client.download(args.remote, args.local)
        print(f"Downloaded {n} bytes to {args.local}")
    else:
        n = client.upload(args.local, args.remote)
        print(f"Uploaded {n} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
