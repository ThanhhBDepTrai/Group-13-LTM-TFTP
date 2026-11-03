"""TFTP client core (RFC 1350 + RFC 7440 window size) with retry, TID checks.

Thiết kế:
    * ``windowsize = 1`` cho hành vi stop-and-wait tương thích RFC 1350.
    * ``windowsize > 1``: gửi/nhận một cửa sổ nhiều DATA block trước khi chờ
      ACK của block cuối cửa sổ (RFC 7440).
    * Nếu server không trả ``windowsize`` trong OACK, client tự fallback về 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
from collections.abc import Callable
from pathlib import Path

from tftp_config import (
    DEFAULT_BLKSIZE,
    DEFAULT_CLIENT_HOST,
    DEFAULT_PORT,
    DEFAULT_RETRIES,
    DEFAULT_TIMEOUT,
    DEFAULT_WINDOWSIZE,
)
from tftp_options import TransferOptions
from tftp_packet import (
    MAX_PACKET_SIZE,
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    pack_ack,
    pack_data,
    pack_error,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)

_BLOCK_MASK = 0xFFFF
logger = logging.getLogger(__name__)


def _block_for_index(index: int) -> int:
    """Chỉ số tuyệt đối (bắt đầu 1) -> block number 16-bit (1,2,...,65535,0,1...)."""
    return index & _BLOCK_MASK


class TFTPErrorClient(Exception):
    pass


class TFTPClient:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_PORT,
        timeout: float = DEFAULT_TIMEOUT,
        retries: int = DEFAULT_RETRIES,
        blksize: int = DEFAULT_BLKSIZE,
        windowsize: int = DEFAULT_WINDOWSIZE,
        on_log: Callable[[str], None] | None = None,
        tracer=None,
    ):
        if not 1 <= windowsize <= 65535:
            raise ValueError("windowsize must be 1..65535")
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.blksize = blksize
        self.windowsize = windowsize
        self.on_log = on_log or (lambda message: None)
        self.tracer = tracer

    def _log(self, msg: str) -> None:
        self.on_log(msg)

    # ------------------------------------------------------------------ #
    # Packet trace (tùy chọn, không ảnh hưởng giao thức)
    # ------------------------------------------------------------------ #
    def _trace_packet(self, raw: bytes, sock, peer, direction: str) -> None:
        if self.tracer is None:
            return
        try:
            local = sock.getsockname()
            self.tracer.record_raw(
                raw,
                source=f"{local[0]}:{local[1]}",
                destination=f"{peer[0]}:{peer[1]}",
                direction=direction,
            )
        except Exception:
            logger.debug("Packet trace failed", exc_info=True)

    def _send(self, sock, raw: bytes, peer) -> None:
        sock.sendto(raw, peer)
        self._trace_packet(raw, sock, peer, "Client -> Server")

    def _recv(self, sock):
        raw, peer = sock.recvfrom(MAX_PACKET_SIZE)
        self._trace_packet(raw, sock, peer, "Server -> Client")
        return raw, peer

    def list_files(self) -> list[str]:
        """Get the current file list from the server over TCP port + 1."""
        list_port = self.port + 1

        with socket.create_connection(
            (self.host, list_port), timeout=self.timeout
        ) as sock:
            sock.settimeout(self.timeout)
            sock.sendall(b'{"action":"list"}\n')

            data = bytearray()
            while b"\n" not in data and len(data) < 1024 * 1024:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)

        if not data:
            raise TFTPErrorClient("Server không trả về danh sách file")

        response = json.loads(bytes(data).split(b"\n", 1)[0].decode("utf-8"))

        if "error" in response:
            raise TFTPErrorClient(response["error"])

        files = response.get("files")
        if not isinstance(files, list):
            raise TFTPErrorClient("Dữ liệu danh sách file không hợp lệ")

        return [str(name) for name in files]

    @staticmethod
    def _cancelled(cancel: object | None) -> bool:
        return cancel is not None and bool(getattr(cancel, "is_set", lambda: False)())

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
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

        requested = TransferOptions(
            blksize=self.blksize,
            timeout=self.timeout,
            windowsize=self.windowsize,
        )
        options = requested.as_request_options(upload=False)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        sock.settimeout(self.timeout)

        server_tid: tuple[str, int] | None = None
        blksize = self.blksize
        timeout = self.timeout
        windowsize = DEFAULT_WINDOWSIZE
        total_size: int | None = None
        received = 0
        expected_index = 1
        last_block = 0
        window_count = 0
        pending_packet = None
        file = None
        success = False

        try:
            if on_progress:
                on_progress(0, None)

            self._send(sock, pack_rrq(remote, options=options), (self.host, self.port))

            # ---- handshake: OACK (preferred) hoặc DATA đầu tiên (server cũ) ----
            attempts = 0
            while True:
                if self._cancelled(cancel):
                    raise TFTPErrorClient("Cancelled")
                try:
                    raw, peer = self._recv(sock)
                except (TimeoutError, ConnectionResetError):
                    attempts += 1
                    if attempts >= self.retries:
                        raise TimeoutError("No response from server") from None
                    self._log(f"Timeout waiting OACK, retry {attempts}")
                    self._send(sock, 
                        pack_rrq(remote, options=options), (self.host, self.port)
                    )
                    continue

                if server_tid is None:
                    server_tid = peer
                if peer != server_tid:
                    self._send(sock, pack_error(5, "Unknown TID"), peer)
                    continue

                packet = unpack_packet(raw)
                if packet.opcode == OP_ERROR:
                    raise TFTPErrorClient(
                        f"Server error {packet.error_code}: {packet.error_msg}"
                    )
                if packet.opcode == OP_OACK:
                    negotiated = TransferOptions.from_oack(
                        packet.options,
                        fallback_blksize=self.blksize,
                        fallback_timeout=self.timeout,
                    )
                    blksize = negotiated.blksize
                    timeout = negotiated.timeout
                    windowsize = negotiated.windowsize
                    total_size = negotiated.tsize
                    sock.settimeout(timeout)
                    self._send(sock, pack_ack(0), server_tid)
                    self._log(
                        f"OACK: blksize={blksize}, timeout={timeout:g}, "
                        f"windowsize={windowsize}, tsize={total_size}"
                    )
                    break
                if packet.opcode == OP_DATA:
                    # Server bỏ qua option và gửi thẳng dữ liệu.
                    pending_packet = packet
                    break
                raise TFTPErrorClient("Unexpected response to RRQ")

            file = open(tmp, "wb")  # noqa: SIM115 - đóng thủ công trong finally
            attempts = 0

            while True:
                if self._cancelled(cancel):
                    raise TFTPErrorClient("Cancelled")

                if pending_packet is not None:
                    packet = pending_packet
                    pending_packet = None
                else:
                    try:
                        raw, peer = self._recv(sock)
                    except (TimeoutError, ConnectionResetError):
                        attempts += 1
                        if attempts >= self.retries:
                            raise TimeoutError("No response from server") from None
                        self._log(f"Timeout waiting DATA, retry {attempts}")
                        # ACK block cuối đã nhận để server gửi lại từ đó.
                        if last_block or windowsize == 1:
                            self._send(sock, 
                                pack_ack(last_block),
                                server_tid or (self.host, self.port),
                            )
                        else:
                            self._send(sock, 
                                pack_rrq(remote, options=options),
                                (self.host, self.port),
                            )
                        continue

                    if peer != server_tid:
                        self._send(sock, pack_error(5, "Unknown TID"), peer)
                        continue
                    packet = unpack_packet(raw)

                if packet.opcode == OP_ERROR:
                    raise TFTPErrorClient(
                        f"Server error {packet.error_code}: {packet.error_msg}"
                    )
                if packet.opcode != OP_DATA:
                    continue

                attempts = 0
                expected_block = _block_for_index(expected_index)

                if packet.block == expected_block:
                    file.write(packet.data)
                    received += len(packet.data)
                    last_block = packet.block
                    expected_index += 1
                    window_count += 1
                    is_final = len(packet.data) < blksize

                    if is_final or window_count >= windowsize:
                        self._send(sock, pack_ack(packet.block), server_tid)
                        window_count = 0
                        if on_progress:
                            on_progress(received, total_size)
                    elif on_progress:
                        on_progress(received, total_size)

                    if is_final:
                        file.close()
                        file = None
                        os.replace(tmp, local)
                        success = True
                        self._log(f"Downloaded {received} bytes")
                        return received

                elif packet.block == last_block:
                    # ACK bị mất: nhắc lại block cuối đã nhận đúng.
                    self._send(sock, pack_ack(last_block), server_tid)
                else:
                    # Lỗi thứ tự: ACK block cuối đúng, server sẽ gửi lại từ đó.
                    self._send(sock, pack_ack(last_block), server_tid)

        finally:
            if file is not None:
                try:
                    file.close()
                except OSError:
                    pass
            sock.close()
            if not success:
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
        requested = TransferOptions(
            blksize=self.blksize,
            timeout=self.timeout,
            windowsize=self.windowsize,
        )
        options = requested.as_request_options(upload=True, file_size=size)

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("", 0))
        sock.settimeout(self.timeout)

        server_tid: tuple[str, int] | None = None
        blksize = self.blksize
        timeout = self.timeout
        windowsize = DEFAULT_WINDOWSIZE
        sent = 0

        try:
            if on_progress:
                on_progress(0, size)

            self._send(sock, pack_wrq(remote, options=options), (self.host, self.port))

            # ---- handshake ----
            attempts = 0
            while True:
                if self._cancelled(cancel):
                    raise TFTPErrorClient("Cancelled")
                try:
                    raw, peer = self._recv(sock)
                except (TimeoutError, ConnectionResetError):
                    attempts += 1
                    if attempts >= self.retries:
                        raise TimeoutError("No response from server") from None
                    self._log(f"Timeout waiting ACK0, retry {attempts}")
                    self._send(sock, 
                        pack_wrq(remote, options=options), (self.host, self.port)
                    )
                    continue

                if server_tid is None:
                    server_tid = peer
                if peer != server_tid:
                    self._send(sock, pack_error(5, "Unknown TID"), peer)
                    continue

                packet = unpack_packet(raw)
                if packet.opcode == OP_ERROR:
                    raise TFTPErrorClient(
                        f"Server error {packet.error_code}: {packet.error_msg}"
                    )
                if packet.opcode == OP_OACK:
                    negotiated = TransferOptions.from_oack(
                        packet.options,
                        fallback_blksize=self.blksize,
                        fallback_timeout=self.timeout,
                    )
                    blksize = negotiated.blksize
                    timeout = negotiated.timeout
                    windowsize = negotiated.windowsize
                    sock.settimeout(timeout)
                    self._send(sock, pack_ack(0), server_tid)
                    self._log(
                        f"OACK: blksize={blksize}, timeout={timeout:g}, "
                        f"windowsize={windowsize}"
                    )
                    break
                if packet.opcode == OP_ACK and packet.block == 0:
                    break
                raise TFTPErrorClient("Unexpected response to WRQ")

            total_blocks = size // blksize + 1  # luôn có block cuối ngắn/rỗng
            base_index = 1

            with open(local, "rb") as file:
                while base_index <= total_blocks:
                    if self._cancelled(cancel):
                        raise TFTPErrorClient("Cancelled")

                    last_index = min(base_index + windowsize - 1, total_blocks)
                    window: list[tuple[int, int, bytes]] = []
                    for index in range(base_index, last_index + 1):
                        file.seek((index - 1) * blksize)
                        window.append((index, _block_for_index(index), file.read(blksize)))

                    retry = 0
                    advanced = False
                    while retry < self.retries:
                        if self._cancelled(cancel):
                            raise TFTPErrorClient("Cancelled")

                        for _index, block, chunk in window:
                            self._send(sock, pack_data(block, chunk), server_tid)

                        try:
                            while True:
                                raw, peer = self._recv(sock)
                                if peer != server_tid:
                                    self._send(sock, pack_error(5, "Unknown TID"), peer)
                                    continue

                                ack = unpack_packet(raw)
                                if ack.opcode == OP_ERROR:
                                    raise TFTPErrorClient(
                                        f"Server error {ack.error_code}: {ack.error_msg}"
                                    )
                                if ack.opcode != OP_ACK:
                                    continue

                                # Tìm ACK trong cửa sổ hiện tại (kể cả ACK cũ).
                                acked_position = next(
                                    (
                                        position
                                        for position, (_i, b, _c) in enumerate(window)
                                        if b == ack.block
                                    ),
                                    None,
                                )
                                if acked_position is None:
                                    self._log(
                                        f"Ignoring duplicate/old ACK {ack.block}"
                                    )
                                    continue

                                for _i, _b, chunk in window[: acked_position + 1]:
                                    sent += len(chunk)
                                base_index = window[acked_position][0] + 1
                                if on_progress:
                                    on_progress(sent, size)
                                advanced = True
                                break
                        except (TimeoutError, ConnectionResetError):
                            retry += 1
                            if retry >= self.retries:
                                raise TimeoutError(
                                    f"No ACK for block {window[-1][1]}"
                                ) from None
                            self._log(
                                f"Timeout waiting ACK block {window[-1][1]}, "
                                f"retry {retry}"
                            )
                            continue

                        if advanced:
                            break
                    else:  # pragma: no cover - vòng lặp đã raise ở trên
                        raise TimeoutError(f"No ACK for block {window[-1][1]}")

            self._log(f"Uploaded {sent} bytes")
            return sent

        finally:
            sock.close()

    def __repr__(self) -> str:
        return f"TFTPClient({self.host!r}, {self.port})"


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(prog="tftp-client")
    parser.add_argument("--host", default=DEFAULT_CLIENT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--blksize", type=int, default=DEFAULT_BLKSIZE)
    parser.add_argument("--windowsize", type=int, default=DEFAULT_WINDOWSIZE)

    sub = parser.add_subparsers(dest="cmd", required=True)
    get = sub.add_parser("get")
    get.add_argument("remote")
    get.add_argument("local")

    put = sub.add_parser("put")
    put.add_argument("local")
    put.add_argument("remote", nargs="?")

    args = parser.parse_args(argv)
    client = TFTPClient(
        args.host,
        args.port,
        timeout=args.timeout,
        retries=args.retries,
        blksize=args.blksize,
        windowsize=args.windowsize,
        on_log=print,
    )

    if args.cmd == "get":
        count = client.download(args.remote, args.local)
        print(f"Downloaded {count} bytes to {args.local}")
    else:
        count = client.upload(args.local, args.remote)
        print(f"Uploaded {count} bytes")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
