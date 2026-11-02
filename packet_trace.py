"""Thu thập sự kiện packet nội bộ (không cần quyền bắt gói của OS).

Cả client và server có thể gọi :meth:`PacketTracer.record_raw` ngay sau khi
gửi/nhận datagram. Dữ liệu được giữ trong ring buffer để Admin API/GUI hiển
thị. Đây là trace của chính ứng dụng, không phải packet sniffer toàn mạng.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from tftp_packet import (
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    OP_RRQ,
    OP_WRQ,
    TFTPError,
    unpack_packet,
)

OPCODE_NAMES = {
    OP_RRQ: "RRQ",
    OP_WRQ: "WRQ",
    OP_DATA: "DATA",
    OP_ACK: "ACK",
    OP_ERROR: "ERROR",
    OP_OACK: "OACK",
}


@dataclass(frozen=True)
class PacketEvent:
    """Sự kiện packet theo hợp đồng dữ liệu nội bộ."""

    timestamp: str
    source: str
    destination: str
    opcode: str
    block_number: int | None
    payload_length: int
    direction: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "source": self.source,
            "destination": self.destination,
            "opcode": self.opcode,
            "block_number": self.block_number,
            "payload_length": self.payload_length,
            "direction": self.direction,
        }


def opcode_name(opcode: int) -> str:
    return OPCODE_NAMES.get(opcode, f"OPCODE_{opcode}")


class PacketTracer:
    def __init__(self, max_events: int = 2000):
        self.max_events = max(1, max_events)
        self._events: deque[PacketEvent] = deque(maxlen=self.max_events)
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}

    def add(self, event: PacketEvent) -> None:
        with self._lock:
            self._events.append(event)
            self._counts[event.opcode] = self._counts.get(event.opcode, 0) + 1

    def record_raw(
        self,
        raw: bytes,
        *,
        source: str,
        destination: str,
        direction: str,
        timestamp: float | None = None,
    ) -> PacketEvent | None:
        """Giải mã datagram và ghi trace; trả về event hoặc ``None`` nếu hỏng."""
        try:
            packet = unpack_packet(raw)
        except TFTPError:
            return None
        event = PacketEvent(
            timestamp=time.strftime(
                "%H:%M:%S",
                time.localtime(timestamp if timestamp is not None else time.time()),
            )
            + f".{int((timestamp if timestamp is not None else time.time()) % 1 * 1000):03d}",
            source=source,
            destination=destination,
            opcode=opcode_name(packet.opcode),
            block_number=packet.block if packet.opcode in (OP_DATA, OP_ACK) else None,
            payload_length=len(packet.data) if packet.opcode == OP_DATA else len(raw),
            direction=direction,
        )
        self.add(event)
        return event

    def events(
        self, *, limit: int = 200, opcode: str | None = None
    ) -> list[PacketEvent]:
        with self._lock:
            items = list(self._events)
        if opcode:
            wanted = opcode.strip().upper()
            items = [item for item in items if item.opcode == wanted]
        return items[-max(1, limit) :]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._counts.clear()


__all__ = ["OPCODE_NAMES", "PacketEvent", "PacketTracer", "opcode_name"]
