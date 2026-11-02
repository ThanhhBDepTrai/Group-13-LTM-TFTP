"""Thống kê hoạt động server, lưu lịch sử bằng SQLite.

``TransferEvent`` là hợp đồng dữ liệu tối thiểu giữa server, metrics, Admin API
và GUI. Dashboard không truy vấn trực tiếp database của server; nó gọi Admin
API, API gọi :class:`MetricsCollector`.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TransferEvent:
    timestamp: str
    client_ip: str
    filename: str
    direction: str  # "upload" | "download"
    bytes_total: int
    duration: float
    retries: int
    timeouts: int
    status: str  # "success" | "failed" | "cancelled"
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "client_ip": self.client_ip,
            "filename": self.filename,
            "direction": self.direction,
            "bytes_total": self.bytes_total,
            "duration": round(self.duration, 6),
            "retries": self.retries,
            "timeouts": self.timeouts,
            "status": self.status,
            "error": self.error,
        }


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


class MetricsCollector:
    def __init__(self, db_path: str | Path | None = None, *, max_recent: int = 500):
        self.db_path = str(db_path) if db_path else None
        self.max_recent = max(1, max_recent)
        self._lock = threading.RLock()
        self._active_clients = 0
        self._recent: list[TransferEvent] = []
        self._memory = {
            "total_transfers": 0,
            "successful": 0,
            "failed": 0,
            "uploads": 0,
            "downloads": 0,
            "bytes_upload": 0,
            "bytes_download": 0,
            "total_duration": 0.0,
            "timeouts": 0,
            "retries": 0,
        }
        if self.db_path:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self._initialize_db()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    client_ip TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    bytes_total INTEGER NOT NULL,
                    duration REAL NOT NULL,
                    retries INTEGER NOT NULL,
                    timeouts INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_transfers_time ON transfers(id);
                """
            )

    # ------------------------------------------------------------------ #
    # Phiên đang hoạt động
    # ------------------------------------------------------------------ #
    def start_session(self) -> None:
        with self._lock:
            self._active_clients += 1

    def end_session(self) -> None:
        with self._lock:
            self._active_clients = max(0, self._active_clients - 1)

    @property
    def active_clients(self) -> int:
        with self._lock:
            return self._active_clients

    # ------------------------------------------------------------------ #
    # Ghi nhận
    # ------------------------------------------------------------------ #
    def record_transfer(self, event: TransferEvent) -> None:
        with self._lock:
            self._memory["total_transfers"] += 1
            if event.status == "success":
                self._memory["successful"] += 1
            else:
                self._memory["failed"] += 1
            if event.direction == "upload":
                self._memory["uploads"] += 1
                self._memory["bytes_upload"] += event.bytes_total
            else:
                self._memory["downloads"] += 1
                self._memory["bytes_download"] += event.bytes_total
            self._memory["total_duration"] += max(event.duration, 0)
            self._memory["timeouts"] += event.timeouts
            self._memory["retries"] += event.retries

            self._recent.append(event)
            if len(self._recent) > self.max_recent:
                del self._recent[: len(self._recent) - self.max_recent]

            if self.db_path:
                try:
                    with self._connect() as connection:
                        connection.execute(
                            "INSERT INTO transfers(timestamp, client_ip, filename, "
                            "direction, bytes_total, duration, retries, timeouts, "
                            "status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                event.timestamp,
                                event.client_ip,
                                event.filename,
                                event.direction,
                                event.bytes_total,
                                event.duration,
                                event.retries,
                                event.timeouts,
                                event.status,
                                event.error,
                            ),
                        )
                except sqlite3.Error:
                    # Dashboard không được làm hỏng phiên truyền file.
                    pass

    def record(
        self,
        *,
        client_ip: str,
        filename: str,
        direction: str,
        bytes_total: int,
        duration: float,
        retries: int = 0,
        timeouts: int = 0,
        status: str = "success",
        error: str | None = None,
    ) -> TransferEvent:
        event = TransferEvent(
            timestamp=_now_iso(),
            client_ip=client_ip,
            filename=filename,
            direction=direction,
            bytes_total=bytes_total,
            duration=duration,
            retries=retries,
            timeouts=timeouts,
            status=status,
            error=error,
        )
        self.record_transfer(event)
        return event

    # ------------------------------------------------------------------ #
    # Truy vấn
    # ------------------------------------------------------------------ #
    def snapshot(self) -> dict:
        with self._lock:
            active = self._active_clients
            if self.db_path:
                try:
                    with self._connect() as connection:
                        row = connection.execute(
                            """
                            SELECT COUNT(*) AS total,
                                   COALESCE(SUM(status='success'), 0) AS successful,
                                   COALESCE(SUM(status!='success'), 0) AS failed,
                                   COALESCE(SUM(direction='upload'), 0) AS uploads,
                                   COALESCE(SUM(direction='download'), 0) AS downloads,
                                   COALESCE(SUM(CASE WHEN direction='upload'
                                        THEN bytes_total ELSE 0 END), 0) AS bytes_upload,
                                   COALESCE(SUM(CASE WHEN direction='download'
                                        THEN bytes_total ELSE 0 END), 0) AS bytes_download,
                                   COALESCE(SUM(duration), 0) AS total_duration,
                                   COALESCE(SUM(timeouts), 0) AS timeouts,
                                   COALESCE(SUM(retries), 0) AS retries
                            FROM transfers
                            """
                        ).fetchone()
                    data = dict(row)
                except sqlite3.Error:
                    data = dict(self._memory)
                    data["total"] = data.get("total_transfers", 0)
            else:
                data = {
                    "total": self._memory["total_transfers"],
                    "successful": self._memory["successful"],
                    "failed": self._memory["failed"],
                    "uploads": self._memory["uploads"],
                    "downloads": self._memory["downloads"],
                    "bytes_upload": self._memory["bytes_upload"],
                    "bytes_download": self._memory["bytes_download"],
                    "total_duration": self._memory["total_duration"],
                    "timeouts": self._memory["timeouts"],
                    "retries": self._memory["retries"],
                }

        total_duration = float(data.get("total_duration") or 0)
        total_bytes = data["bytes_upload"] + data["bytes_download"]
        return {
            "active_clients": active,
            "total_transfers": data["total"],
            "successful": data["successful"],
            "failed": data["failed"],
            "uploads": data["uploads"],
            "downloads": data["downloads"],
            "bytes_upload": data["bytes_upload"],
            "bytes_download": data["bytes_download"],
            "total_bytes": total_bytes,
            "avg_speed_bytes_per_second": (
                total_bytes / total_duration if total_duration > 0 else 0
            ),
            "timeouts": data["timeouts"],
            "retries": data["retries"],
        }

    def recent_transfers(self, limit: int = 50) -> list[dict]:
        limit = max(1, limit)
        if self.db_path:
            try:
                with self._connect() as connection:
                    rows = connection.execute(
                        "SELECT timestamp, client_ip, filename, direction, "
                        "bytes_total, duration, retries, timeouts, status, error "
                        "FROM transfers ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
                return [dict(row) for row in rows]
            except sqlite3.Error:
                pass
        with self._lock:
            items = list(self._recent)[-limit:]
        return [item.to_dict() for item in reversed(items)]

    def reset(self) -> None:
        with self._lock:
            self._recent.clear()
            for key in self._memory:
                self._memory[key] = 0
            if self.db_path:
                try:
                    with self._connect() as connection:
                        connection.execute("DELETE FROM transfers")
                except sqlite3.Error:
                    pass


__all__ = ["MetricsCollector", "TransferEvent"]
