"""Metrics Service: thống kê hoạt động của TFTP server trong bộ nhớ.

Thread-safe, không phụ thuộc thư viện ngoài. ``snapshot()`` trả dict cho API JSON,
``to_prometheus()`` trả văn bản theo định dạng Prometheus.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from collections.abc import Callable
from typing import Any

__all__ = ["MetricsService"]


class MetricsService:
    def __init__(self, *, recent_window: int = 200, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self.started_at = clock()
        self._transfers: Counter[str] = Counter()  # "ok", "failed", "rrq", "wrq"
        self._bytes: Counter[str] = Counter()  # "read", "written"
        self._retransmits = 0
        self._weighted_seconds = 0.0
        self._ok_bytes = 0
        self._files: Counter[str] = Counter()
        self._errors: Counter[str] = Counter()
        self._recent: deque[tuple[float, int]] = deque(maxlen=recent_window)
        self._active_provider: Callable[[], int] | None = None

    def set_active_provider(self, provider: Callable[[], int]) -> None:
        """Hàm trả về số phiên đang chạy (ví dụ ``lambda: server.active_sessions``)."""
        self._active_provider = provider

    def record_transfer(self, record: Any) -> None:
        """Nhận ``TransferRecord`` hoặc dict cùng trường."""
        get = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
        op = str(get("op", "")).upper()
        ok = get("status") == "ok"
        size = int(get("bytes", 0) or 0)
        with self._lock:
            self._transfers["ok" if ok else "failed"] += 1
            self._transfers[op.lower()] += 1
            self._retransmits += int(get("retransmits", 0) or 0)
            if ok:
                self._bytes["read" if op == "RRQ" else "written"] += size
                self._ok_bytes += size
                self._weighted_seconds += float(get("duration", 0) or 0)
                self._files[str(get("filename", ""))] += 1
                self._recent.append((self._clock(), size))
            else:
                reason = str(get("error", "") or "unknown")
                self._errors[reason.split(":")[0][:40]] += 1

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        with self._lock:
            total = self._transfers["ok"] + self._transfers["failed"]
            average = (
                self._ok_bytes * 8 / self._weighted_seconds / 1e6 if self._weighted_seconds else 0.0
            )
            last_minute = sum(size for ts, size in self._recent if now - ts <= 60)
            data = {
                "uptime_seconds": round(now - self.started_at, 3),
                "transfers": {
                    "total": total,
                    "ok": self._transfers["ok"],
                    "failed": self._transfers["failed"],
                    "read": self._transfers["rrq"],
                    "write": self._transfers["wrq"],
                    "success_rate": round(self._transfers["ok"] / total, 4) if total else 1.0,
                },
                "bytes": {
                    "read": self._bytes["read"],
                    "written": self._bytes["written"],
                    "total": self._bytes["read"] + self._bytes["written"],
                    "last_minute": last_minute,
                },
                "retransmits": self._retransmits,
                "avg_throughput_mbps": round(average, 3),
                "top_files": [{"filename": n, "count": c} for n, c in self._files.most_common(5)],
                "errors": dict(self._errors.most_common(10)),
            }
        data["active_sessions"] = self._active_provider() if self._active_provider else 0
        return data

    def to_prometheus(self) -> str:
        snap = self.snapshot()
        lines = [
            "# TYPE tftp_transfers_total counter",
            f'tftp_transfers_total{{status="ok"}} {snap["transfers"]["ok"]}',
            f'tftp_transfers_total{{status="failed"}} {snap["transfers"]["failed"]}',
            "# TYPE tftp_bytes_total counter",
            f'tftp_bytes_total{{direction="read"}} {snap["bytes"]["read"]}',
            f'tftp_bytes_total{{direction="written"}} {snap["bytes"]["written"]}',
            "# TYPE tftp_retransmits_total counter",
            f'tftp_retransmits_total {snap["retransmits"]}',
            "# TYPE tftp_active_sessions gauge",
            f'tftp_active_sessions {snap["active_sessions"]}',
            "# TYPE tftp_uptime_seconds gauge",
            f'tftp_uptime_seconds {snap["uptime_seconds"]}',
        ]
        return "\n".join(lines) + "\n"
