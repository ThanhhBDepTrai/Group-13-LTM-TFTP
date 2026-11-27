"""Mô phỏng mạng lỗi (rớt gói, độ trễ) có thể chỉnh khi server đang chạy.

Server giữ một ``NetworkFaultInjector`` dùng chung cho mọi phiên truyền; API
``POST /api/v1/simulator/config`` chỉ cần gọi :meth:`update` là có hiệu lực ngay,
không cần khởi động lại.

Cách gắn vào bước gửi gói::

    if injector.should_drop():
        return                      # coi như gói bị mất trên đường truyền
    injector.apply_delay()          # giả lập độ trễ mạng
    sock.sendto(packet, addr)

Mọi phương thức đều an toàn khi gọi từ nhiều thread. ``apply_delay`` ngủ *ngoài*
khóa nên một phiên chờ không làm chậm các phiên khác.
"""

from __future__ import annotations

import math
import random
import threading
import time
from collections.abc import Callable
from typing import Any

__all__ = [
    "MAX_DELAY_MS",
    "MAX_LOSS_RATE",
    "NetworkFaultInjector",
]

MAX_LOSS_RATE = 0.5  # trần theo kế hoạch: quá cao thì TFTP gần như không truyền được
MAX_DELAY_MS = 500


def _validate_loss(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"loss_rate must be a number, got {value!r}")
    number = float(value)
    if math.isnan(number) or not 0.0 <= number <= MAX_LOSS_RATE:
        raise ValueError(f"loss_rate must be in 0.0..{MAX_LOSS_RATE}, got {value!r}")
    return number


def _validate_delay(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"delay_ms must be a number, got {value!r}")
    if isinstance(value, float):
        if math.isnan(value) or not value.is_integer():
            raise ValueError(f"delay_ms must be a whole number, got {value!r}")
        value = int(value)
    if not 0 <= value <= MAX_DELAY_MS:
        raise ValueError(f"delay_ms must be in 0..{MAX_DELAY_MS}, got {value!r}")
    return int(value)


class NetworkFaultInjector:
    """Quyết định bỏ gói / làm chậm gói theo cấu hình hiện tại (thread-safe)."""

    def __init__(
        self,
        loss_rate: float = 0.0,
        delay_ms: int = 0,
        *,
        seed: int | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._lock = threading.Lock()
        self._rng = random.Random(seed)
        self._sleep = sleep
        self._loss_rate = _validate_loss(loss_rate)
        self._delay_ms = _validate_delay(delay_ms)
        self._seen = 0
        self._dropped = 0
        self._delayed = 0

    # ------------------------------------------------------------------ #
    # Cấu hình
    # ------------------------------------------------------------------ #

    def update(self, loss_rate: float | None = None, delay_ms: int | None = None) -> None:
        """Đổi cấu hình tại chỗ. Tham số bỏ trống (``None``) giữ nguyên giá trị cũ.

        Cả hai giá trị được kiểm tra trước khi áp dụng nên một lệnh sai không bao giờ
        để lại cấu hình đổi một nửa. Raise ``ValueError`` nếu ngoài khoảng cho phép.
        """
        new_loss = None if loss_rate is None else _validate_loss(loss_rate)
        new_delay = None if delay_ms is None else _validate_delay(delay_ms)
        with self._lock:
            if new_loss is not None:
                self._loss_rate = new_loss
            if new_delay is not None:
                self._delay_ms = new_delay

    @property
    def loss_rate(self) -> float:
        with self._lock:
            return self._loss_rate

    @property
    def delay_ms(self) -> int:
        with self._lock:
            return self._delay_ms

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._loss_rate > 0 or self._delay_ms > 0

    # ------------------------------------------------------------------ #
    # Dùng trong bước gửi gói
    # ------------------------------------------------------------------ #

    def should_drop(self) -> bool:
        """``True`` nếu gói này phải bị bỏ (xác suất = ``loss_rate``)."""
        with self._lock:
            self._seen += 1
            if self._loss_rate > 0 and self._rng.random() < self._loss_rate:
                self._dropped += 1
                return True
            return False

    def apply_delay(self) -> None:
        """Ngủ ``delay_ms`` mili-giây (không làm gì nếu ``delay_ms`` = 0)."""
        with self._lock:
            delay = self._delay_ms
            if delay:
                self._delayed += 1
        if delay:
            self._sleep(delay / 1000.0)

    # ------------------------------------------------------------------ #
    # Thống kê (để hiển thị trên GUI/Demo)
    # ------------------------------------------------------------------ #

    def snapshot(self) -> dict[str, Any]:
        """Cấu hình hiện tại và số liệu từ lần ``reset_stats`` gần nhất."""
        with self._lock:
            return {
                "loss_rate": self._loss_rate,
                "delay_ms": self._delay_ms,
                "packets_seen": self._seen,
                "packets_dropped": self._dropped,
                "packets_delayed": self._delayed,
            }

    def reset_stats(self) -> None:
        with self._lock:
            self._seen = self._dropped = self._delayed = 0

    def __repr__(self) -> str:
        snap = self.snapshot()
        return f"NetworkFaultInjector(loss_rate={snap['loss_rate']}, delay_ms={snap['delay_ms']})"
