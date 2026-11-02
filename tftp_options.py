"""Thương lượng option TFTP (RFC 2347/2348/2349/7440).

Module này là nơi duy nhất quyết định option nào được chấp nhận và giá trị
cuối cùng gửi trong OACK. ``tftp_packet.py`` chỉ đóng gói/giải mã, không tự
thương lượng.

Quy ước:
    * Option lạ bị bỏ qua (RFC 2347).
    * Giá trị sai định dạng/ngoài khoảng -> :class:`TFTPError` mã 8.
    * ``windowsize`` trong OACK luôn <= giá trị client yêu cầu (RFC 7440).
    * Khi không có OACK, hai bên dùng ``windowsize = 1`` (tương thích RFC 1350).
"""

from __future__ import annotations

from dataclasses import dataclass

from tftp_config import (
    DEFAULT_BLKSIZE,
    DEFAULT_MAX_WINDOWSIZE,
    DEFAULT_TIMEOUT,
    DEFAULT_WINDOWSIZE,
)
from tftp_packet import (
    MAX_BLKSIZE,
    MAX_TIMEOUT,
    MIN_BLKSIZE,
    MIN_TIMEOUT,
    TFTPError,
    validate_options,
)

MIN_WINDOWSIZE = 1
MAX_WINDOWSIZE = 65535


def _parse_int(name: str, value: str, low: int, high: int | None = None) -> int:
    if not (value.isascii() and value.isdigit()):
        raise TFTPError(8, f"Invalid {name}: {value!r}")
    number = int(value)
    if number < low or (high is not None and number > high):
        bound = f"{low}..{high}" if high is not None else f">= {low}"
        raise TFTPError(8, f"{name} must be {bound}")
    return number


def negotiate_options(
    requested: dict[str, str],
    *,
    is_upload: bool = False,
    file_size: int | None = None,
    server_windowsize: int = DEFAULT_MAX_WINDOWSIZE,
) -> dict[str, str]:
    """Trả về dict option sẽ đưa vào OACK (rỗng nếu không có option hợp lệ).

    Args:
        requested: option client gửi trong RRQ/WRQ.
        is_upload: ``True`` nếu là WRQ (client upload lên server).
        file_size: kích thước thật của file, dùng để trả lời ``tsize`` cho RRQ.
        server_windowsize: cửa sổ tối đa server chấp nhận.
    """
    server_windowsize = max(server_windowsize, MIN_WINDOWSIZE)

    lowered = {str(key).lower(): str(value) for key, value in requested.items()}
    # validate_options xử lý blksize/timeout/tsize và tự bỏ qua option lạ.
    accepted = validate_options(
        {key: value for key, value in lowered.items() if key != "windowsize"}
    )

    if "tsize" in accepted:
        if is_upload:
            # Client đã khai báo kích thước; server xác nhận lại.
            accepted["tsize"] = lowered["tsize"]
        else:
            accepted["tsize"] = str(file_size if file_size is not None else 0)

    if "windowsize" in lowered:
        requested_window = _parse_int(
            "windowsize", lowered["windowsize"], MIN_WINDOWSIZE, MAX_WINDOWSIZE
        )
        accepted["windowsize"] = str(min(requested_window, server_windowsize))

    return accepted


@dataclass(frozen=True)
class TransferOptions:
    """Giá trị hiệu lực sau khi client nhận OACK (hoặc fallback)."""

    blksize: int = DEFAULT_BLKSIZE
    timeout: float = DEFAULT_TIMEOUT
    windowsize: int = DEFAULT_WINDOWSIZE
    tsize: int | None = None

    @classmethod
    def from_oack(
        cls,
        options: dict[str, str],
        *,
        fallback_blksize: int = DEFAULT_BLKSIZE,
        fallback_timeout: float = DEFAULT_TIMEOUT,
    ) -> TransferOptions:
        blksize = fallback_blksize
        timeout = fallback_timeout
        windowsize = DEFAULT_WINDOWSIZE
        tsize: int | None = None

        if "blksize" in options:
            blksize = _parse_int(
                "blksize", str(options["blksize"]), MIN_BLKSIZE, MAX_BLKSIZE
            )
        if "timeout" in options:
            timeout = float(
                _parse_int("timeout", str(options["timeout"]), MIN_TIMEOUT, MAX_TIMEOUT)
            )
        if "windowsize" in options:
            windowsize = _parse_int(
                "windowsize", str(options["windowsize"]), MIN_WINDOWSIZE, MAX_WINDOWSIZE
            )
        if "tsize" in options:
            tsize = _parse_int("tsize", str(options["tsize"]), 0)

        return cls(
            blksize=blksize,
            timeout=timeout,
            windowsize=windowsize,
            tsize=tsize,
        )

    def as_request_options(self, *, upload: bool, file_size: int | None = None) -> dict[str, str]:
        """Tạo option gửi trong RRQ/WRQ từ cấu hình client."""
        options: dict[str, str] = {}
        if self.blksize != DEFAULT_BLKSIZE:
            options["blksize"] = str(self.blksize)
        if self.timeout >= 1:
            options["timeout"] = str(int(self.timeout))
        if upload and file_size is not None:
            options["tsize"] = str(file_size)
        else:
            options["tsize"] = "0"
        if self.windowsize != DEFAULT_WINDOWSIZE:
            options["windowsize"] = str(self.windowsize)
        return options


__all__ = [
    "MAX_WINDOWSIZE",
    "MIN_WINDOWSIZE",
    "TransferOptions",
    "negotiate_options",
]
