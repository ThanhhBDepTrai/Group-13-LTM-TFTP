"""Mô phỏng lỗi mạng (rớt gói / độ trễ) cho TFTP Pro.

Module này cung cấp ``NetworkFaultInjector`` an toàn với nhiều thread, cho phép
thay đổi ``loss_rate`` (0.0..0.5) và ``delay_ms`` (0..500) khi Server đang chạy
mà không cần khởi động lại.
"""

from __future__ import annotations

import random
import threading
import time


class NetworkFaultInjector:
    def __init__(self, loss_rate: float = 0.0, delay_ms: int = 0):
        self._lock = threading.Lock()
        self._loss_rate = self._clamp_loss(loss_rate)
        self._delay_ms = self._clamp_delay(delay_ms)

    @staticmethod
    def _clamp_loss(value: float) -> float:
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 0.0
        return max(0.0, min(0.5, value))

    @staticmethod
    def _clamp_delay(value) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        return max(0, min(500, value))

    @property
    def loss_rate(self) -> float:
        with self._lock:
            return self._loss_rate

    @property
    def delay_ms(self) -> int:
        with self._lock:
            return self._delay_ms

    def update(self, loss_rate: float | None = None, delay_ms: int | None = None) -> None:
        with self._lock:
            if loss_rate is not None:
                self._loss_rate = self._clamp_loss(loss_rate)
            if delay_ms is not None:
                self._delay_ms = self._clamp_delay(delay_ms)

    def should_drop(self) -> bool:
        with self._lock:
            rate = self._loss_rate
        if rate <= 0:
            return False
        return random.random() < rate

    def apply_delay(self) -> None:
        with self._lock:
            delay = self._delay_ms
        if delay > 0:
            time.sleep(delay / 1000.0)


__all__ = ["NetworkFaultInjector"]
