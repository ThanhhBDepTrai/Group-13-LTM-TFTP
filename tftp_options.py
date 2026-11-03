"""Thương lượng option TFTP (RFC 2347/2348/2349/7440) cho server và client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from tftp_config import PXE_MAX_BLKSIZE, ClientConfig, ServerConfig
from tftp_packet import (
    BLOCK_SIZE,
    MAX_BLKSIZE,
    TFTPError,
    validate_options,
)

__all__ = ["Negotiated", "build_request_options", "negotiate_server", "parse_oack"]


@dataclass(frozen=True)
class Negotiated:
    """Tham số truyền cuối cùng của một phiên."""

    blksize: int = BLOCK_SIZE
    timeout: float = 3.0
    windowsize: int = 1
    tsize: int | None = None
    oack: dict[str, str] = field(default_factory=dict)  # option server xác nhận


def negotiate_server(
    requested: Mapping[str, str],
    config: ServerConfig,
    *,
    file_size: int | None = None,
    is_write: bool = False,
) -> Negotiated:
    """Quyết định tham số phiên từ option client đề nghị.

    * ``blksize``/``windowsize``: server được phép trả giá trị *nhỏ hơn* đề nghị.
    * ``timeout``: phải đúng giá trị đề nghị, vượt trần thì từ chối (code 8).
    * ``tsize``: RRQ trả kích thước file thật; WRQ phải <= ``max_file_size`` (code 3).
    * Option lạ bị bỏ qua và không xuất hiện trong OACK.
    """
    opts = validate_options(requested)
    blksize = config.blksize
    timeout = float(config.timeout)
    windowsize = config.windowsize
    tsize: int | None = None
    oack: dict[str, str] = {}

    ceiling = min(config.max_blksize, MAX_BLKSIZE)
    if config.pxe_mode:
        ceiling = min(ceiling, PXE_MAX_BLKSIZE)

    if "blksize" in opts:
        blksize = min(int(opts["blksize"]), ceiling)
        oack["blksize"] = str(blksize)
    if "timeout" in opts:
        seconds = int(opts["timeout"])
        if seconds > config.max_timeout:
            raise TFTPError(8, f"timeout must be <= {config.max_timeout}")
        timeout = float(seconds)
        oack["timeout"] = str(seconds)
    if "windowsize" in opts:
        windowsize = min(int(opts["windowsize"]), config.max_windowsize)
        oack["windowsize"] = str(windowsize)
    if "tsize" in opts:
        if is_write:
            tsize = int(opts["tsize"])
            if tsize > config.max_file_size:
                raise TFTPError(3, "File too large")
            oack["tsize"] = str(tsize)
        elif file_size is not None:
            tsize = file_size
            oack["tsize"] = str(file_size)

    return Negotiated(blksize, timeout, windowsize, tsize, oack)


def build_request_options(
    config: ClientConfig, *, tsize: int | None = None
) -> dict[str, str]:
    """Option client đưa vào RRQ/WRQ (chỉ gửi những gì khác mặc định)."""
    options: dict[str, str] = {}
    if config.blksize != BLOCK_SIZE:
        options["blksize"] = str(config.blksize)
    if config.negotiate_timeout:
        options["timeout"] = str(max(1, round(config.timeout)))
    if config.windowsize > 1:
        options["windowsize"] = str(config.windowsize)
    if config.request_tsize and tsize is not None:
        options["tsize"] = str(tsize)
    return options


def parse_oack(
    requested: Mapping[str, str],
    oack: Mapping[str, str],
    *,
    default_timeout: float = 3.0,
) -> Negotiated:
    """Kiểm tra OACK của server so với những gì client đã đề nghị.

    Raise ``TFTPError(8)`` nếu server trả option chưa được hỏi, hoặc trả giá trị
    lớn hơn đề nghị (blksize/windowsize), hoặc đổi giá trị timeout.
    """
    asked = {k.lower(): v for k, v in requested.items()}
    for key in oack:
        if key.lower() not in asked:
            raise TFTPError(8, f"Unrequested option in OACK: {key}")
    values = validate_options(oack)
    blksize = BLOCK_SIZE
    windowsize = 1
    timeout = default_timeout
    tsize: int | None = None

    if "blksize" in values:
        blksize = int(values["blksize"])
        if blksize > int(asked["blksize"]):
            raise TFTPError(8, "Server raised blksize above request")
    if "windowsize" in values:
        windowsize = int(values["windowsize"])
        if windowsize > int(asked["windowsize"]):
            raise TFTPError(8, "Server raised windowsize above request")
    if "timeout" in values:
        if values["timeout"] != str(int(asked["timeout"])):
            raise TFTPError(8, "Server changed timeout")
        timeout = float(values["timeout"])
    if "tsize" in values:
        tsize = int(values["tsize"])
    return Negotiated(blksize, timeout, windowsize, tsize, dict(values))
