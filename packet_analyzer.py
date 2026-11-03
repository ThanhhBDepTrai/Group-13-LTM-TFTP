"""Lọc, nhóm và hiển thị sự kiện packet do ``packet_trace`` ghi lại.

Đây là lớp thuần dữ liệu, không mở socket và không cần quyền bắt gói của hệ
điều hành. Cùng một module được dùng bởi GUI, test và (nếu cần) CLI để bảo đảm
Dashboard/Packet Analyzer nhìn thấy cùng một cách diễn giải sự kiện.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

PACKET_COLUMNS = (
    "timestamp",
    "source",
    "destination",
    "opcode",
    "block_number",
    "payload_length",
    "direction",
)

_OPCODE_ORDER = ("RRQ", "WRQ", "OACK", "DATA", "ACK", "ERROR")


def _event_dict(event: Any) -> dict:
    if isinstance(event, dict):
        return dict(event)
    to_dict = getattr(event, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    raise TypeError(f"Unsupported packet event: {type(event)!r}")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _host_part(endpoint: Any) -> str:
    """Bỏ phần port để nhóm RRQ (cổng chính) với DATA (TID tạm)."""
    text = _text(endpoint)
    if text.startswith("["):  # [IPv6]:port
        return text[1:].split("]", 1)[0]
    if ":" in text:
        return text.rsplit(":", 1)[0]
    return text


@dataclass(frozen=True)
class PacketFilter:
    """Bộ lọc packet đơn giản, thân thiện với ô nhập trên GUI."""

    opcode: str | None = None
    direction: str | None = None
    source: str | None = None
    destination: str | None = None
    block_number: int | None = None

    def normalized(self) -> PacketFilter:
        return PacketFilter(
            opcode=_text(self.opcode).upper() or None,
            direction=_text(self.direction).lower() or None,
            source=_text(self.source).lower() or None,
            destination=_text(self.destination).lower() or None,
            block_number=self.block_number,
        )

    def matches(self, event: Any) -> bool:
        item = _event_dict(event)
        wanted = self.normalized()
        if wanted.opcode and _text(item.get("opcode")).upper() != wanted.opcode:
            return False
        if wanted.direction and wanted.direction not in _text(
            item.get("direction")
        ).lower():
            return False
        if wanted.source and wanted.source not in _text(item.get("source")).lower():
            return False
        if wanted.destination and wanted.destination not in _text(
            item.get("destination")
        ).lower():
            return False
        if wanted.block_number is not None:
            try:
                if int(item.get("block_number")) != wanted.block_number:
                    return False
            except (TypeError, ValueError):
                return False
        return True


def filter_packets(
    events: Iterable[Any],
    packet_filter: PacketFilter | None = None,
) -> list[dict]:
    items = [_event_dict(event) for event in events]
    if packet_filter is not None:
        items = [item for item in items if packet_filter.matches(item)]
    return items


def summarize_packets(events: Iterable[Any]) -> dict:
    """Trả về số liệu tổng hợp phục vụ Packet Analyzer/Dashboard."""
    items = [_event_dict(event) for event in events]
    counts = Counter(_text(item.get("opcode")).upper() or "UNKNOWN" for item in items)
    directions = Counter(_text(item.get("direction")) or "unknown" for item in items)
    payload_bytes = 0
    data_bytes = 0
    for item in items:
        try:
            length = max(int(item.get("payload_length") or 0), 0)
        except (TypeError, ValueError):
            length = 0
        payload_bytes += length
        if _text(item.get("opcode")).upper() == "DATA":
            data_bytes += length

    seen_data_blocks: set[tuple[str, str, str, int]] = set()
    retransmissions = 0
    for item in items:
        if _text(item.get("opcode")).upper() != "DATA":
            continue
        try:
            block = int(item.get("block_number"))
        except (TypeError, ValueError):
            continue
        key = (
            _text(item.get("source")),
            _text(item.get("destination")),
            _text(item.get("direction")),
            block,
        )
        if key in seen_data_blocks:
            retransmissions += 1
        else:
            seen_data_blocks.add(key)

    ordered_counts = {
        name: counts[name]
        for name in _OPCODE_ORDER
        if counts.get(name)
    }
    for name in sorted(counts):
        if name not in ordered_counts:
            ordered_counts[name] = counts[name]

    timestamps = [item.get("timestamp") for item in items if item.get("timestamp")]
    return {
        "total": len(items),
        "counts": ordered_counts,
        "directions": dict(directions),
        "payload_bytes": payload_bytes,
        "data_bytes": data_bytes,
        "data_retransmissions": retransmissions,
        "first_timestamp": timestamps[0] if timestamps else None,
        "last_timestamp": timestamps[-1] if timestamps else None,
    }


def group_by_conversation(events: Iterable[Any]) -> list[dict]:
    """Nhóm hai chiều của cùng một cặp endpoint thành một conversation.

    Packet trace nội bộ không có session id, nên cặp endpoint là khóa ổn định
    nhất để người học đối chiếu RRQ → OACK → DATA → ACK với Wireshark.
    """
    groups: dict[tuple[str, ...], dict] = {}
    for item in filter_packets(events):
        endpoints = tuple(
            sorted(
                {
                    _host_part(item.get("source")),
                    _host_part(item.get("destination")),
                }
            )
        )
        key = endpoints
        group = groups.setdefault(
            key,
            {
                "endpoints": list(endpoints),
                "packets": 0,
                "payload_bytes": 0,
                "opcodes": Counter(),
                "first_timestamp": item.get("timestamp"),
                "last_timestamp": item.get("timestamp"),
            },
        )
        group["packets"] += 1
        try:
            group["payload_bytes"] += max(int(item.get("payload_length") or 0), 0)
        except (TypeError, ValueError):
            pass
        group["opcodes"][_text(item.get("opcode")).upper() or "UNKNOWN"] += 1
        group["last_timestamp"] = item.get("timestamp")

    result = []
    for group in groups.values():
        group["opcodes"] = dict(group["opcodes"])
        result.append(group)
    result.sort(key=lambda item: item["first_timestamp"] or "")
    return result


def format_table(events: Iterable[Any], *, limit: int = 200) -> str:
    """Định dạng bảng text để dùng trong log, test hoặc CLI."""
    items = filter_packets(events)[: max(1, limit)]
    if not items:
        return "Không có packet."
    header = (
        f"{'Timestamp':<16} {'Source':<22} {'Destination':<22} "
        f"{'Opcode':<7} {'Block':>5} {'Bytes':>7} Direction"
    )
    lines = [header, "-" * len(header)]
    for item in items:
        block = item.get("block_number")
        lines.append(
            f"{_text(item.get('timestamp')):<16} "
            f"{_text(item.get('source')):<22} "
            f"{_text(item.get('destination')):<22} "
            f"{_text(item.get('opcode')):<7} "
            f"{'' if block is None else str(block):>5} "
            f"{int(item.get('payload_length') or 0):>7} "
            f"{_text(item.get('direction'))}"
        )
    return "\n".join(lines)


class PacketAnalyzer:
    """Bọc danh sách sự kiện và cung cấp các thao tác cho GUI."""

    def __init__(self, events: Iterable[Any] | None = None):
        self._events = filter_packets(events or [])

    @classmethod
    def from_api(cls, payload: dict) -> PacketAnalyzer:
        events = payload.get("events", []) if isinstance(payload, dict) else []
        return cls(events if isinstance(events, list) else [])

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def rows(
        self,
        *,
        packet_filter: PacketFilter | None = None,
        limit: int = 200,
    ) -> list[dict]:
        items = filter_packets(self._events, packet_filter)
        return items[-max(1, limit) :]

    def summary(self, *, packet_filter: PacketFilter | None = None) -> dict:
        return summarize_packets(
            filter_packets(self._events, packet_filter)
        )

    def conversations(self, *, packet_filter: PacketFilter | None = None) -> list[dict]:
        return group_by_conversation(
            filter_packets(self._events, packet_filter)
        )

    def format_table(
        self,
        *,
        packet_filter: PacketFilter | None = None,
        limit: int = 200,
    ) -> str:
        return format_table(
            filter_packets(self._events, packet_filter),
            limit=limit,
        )


__all__ = [
    "PACKET_COLUMNS",
    "PacketAnalyzer",
    "PacketFilter",
    "filter_packets",
    "format_table",
    "group_by_conversation",
    "summarize_packets",
]
