"""TFTP packet encoding/decoding for RFC 1350 (+ RFC 2347/2348/2349/7440 options).

Module này chỉ làm một việc: chuyển đổi giữa ``bytes`` trên đường truyền và
đối tượng :class:`Packet`. Không có socket, không có I/O, không có ``print``.
Mọi lỗi nghiệp vụ được báo bằng :class:`TFTPError` kèm mã lỗi TFTP (0-8) để
server/client có thể gửi thẳng cho đối phương bằng :func:`pack_error`.

Quy ước mã lỗi dùng trong module:
    * 4 - gói tin sai định dạng / opcode lạ / block ngoài phạm vi.
    * 8 - option không hợp lệ (tên, giá trị, trùng lặp, ngoài khoảng cho phép).
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass, field

__version__ = "1.1.0"

__all__ = [
    "BLOCK_SIZE",
    "ERROR_CODES",
    "MAX_BLKSIZE",
    "MAX_DATA_SIZE",
    "MAX_PACKET_SIZE",
    "MAX_TIMEOUT",
    "MAX_WINDOWSIZE",
    "MIN_BLKSIZE",
    "MIN_TIMEOUT",
    "MIN_WINDOWSIZE",
    "OP_ACK",
    "OP_DATA",
    "OP_ERROR",
    "OP_OACK",
    "OP_RRQ",
    "OP_WRQ",
    "Packet",
    "TFTPError",
    "pack_ack",
    "pack_data",
    "pack_error",
    "pack_oack",
    "pack_rrq",
    "pack_wrq",
    "unpack_packet",
    "validate_options",
]

# --------------------------------------------------------------------------- #
# Hằng số
# --------------------------------------------------------------------------- #

BLOCK_SIZE = 512  # kích thước block mặc định (RFC 1350)
MIN_BLKSIZE = 8  # RFC 2348
MAX_DATA_SIZE = 65464  # blksize tối đa theo RFC 2348
MAX_BLKSIZE = MAX_DATA_SIZE
MAX_PACKET_SIZE = 4 + MAX_DATA_SIZE  # đủ lớn để recvfrom() mọi gói hợp lệ
MIN_TIMEOUT = 1  # RFC 2349 (giây)
MAX_TIMEOUT = 255
MIN_WINDOWSIZE = 1  # RFC 7440
MAX_WINDOWSIZE = 65535

OP_RRQ = 1
OP_WRQ = 2
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5
OP_OACK = 6

MODE_OCTET = "octet"
_VALID_MODES = frozenset({"netascii", "octet", "mail"})
_MAX_BLOCK = 0xFFFF

ERROR_CODES: dict[int, str] = {
    0: "Not defined",
    1: "File not found",
    2: "Access violation",
    3: "Disk full or allocation exceeded",
    4: "Illegal TFTP operation",
    5: "Unknown transfer ID",
    6: "File already exists",
    7: "No such user",
    8: "Option negotiation failed",
}

_NUL = b"\x00"
_OPCODE = struct.Struct("!H")
_OPCODE_BLOCK = struct.Struct("!HH")


# --------------------------------------------------------------------------- #
# Kiểu dữ liệu
# --------------------------------------------------------------------------- #


class TFTPError(Exception):
    """Lỗi nghiệp vụ TFTP, mang theo mã lỗi gửi cho đối phương."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Packet:
    """Kết quả giải mã một gói tin. Trường không dùng giữ giá trị mặc định."""

    opcode: int
    filename: str = ""
    mode: str = "octet"
    block: int = 0
    data: bytes = b""
    error_code: int = 0
    error_msg: str = ""
    options: dict[str, str] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Hàm nội bộ
# --------------------------------------------------------------------------- #


def _check_block(block: int) -> None:
    if not isinstance(block, int) or isinstance(block, bool) or not 0 <= block <= _MAX_BLOCK:
        raise TFTPError(4, f"Invalid block number: {block!r}")


def _encode_options(options: Mapping[str, str] | None) -> bytes:
    """Mã hóa các cặp option/value thành ``key\\0value\\0...`` (ASCII)."""
    parts: list[bytes] = []
    seen: set[str] = set()
    for key, value in (options or {}).items():
        name = str(key).lower()
        text = str(value)
        if not name or "\x00" in name or "\x00" in text:
            raise TFTPError(8, f"Invalid option: {key!r}")
        if name in seen:
            raise TFTPError(8, f"Duplicate option: {name}")
        seen.add(name)
        try:
            parts.extend([name.encode("ascii"), _NUL, text.encode("ascii"), _NUL])
        except UnicodeEncodeError as exc:
            raise TFTPError(8, f"Option must be ASCII: {key!r}") from exc
    return b"".join(parts)


def _decode_options(chunks: list[bytes]) -> dict[str, str]:
    """Giải mã danh sách ``[key, value, key, value, ...]`` thành dict."""
    if len(chunks) % 2:
        raise TFTPError(4, "Malformed options: key without value")
    options: dict[str, str] = {}
    for i in range(0, len(chunks), 2):
        try:
            key = chunks[i].decode("ascii").lower()
            value = chunks[i + 1].decode("ascii")
        except UnicodeDecodeError as exc:
            raise TFTPError(8, "Option must be ASCII") from exc
        if not key:
            raise TFTPError(8, "Empty option name")
        if key in options:
            raise TFTPError(8, f"Duplicate option: {key}")
        options[key] = value
    return options


def _pack_request(opcode: int, filename: str, options: Mapping[str, str] | None) -> bytes:
    if not filename or "\x00" in filename:
        raise TFTPError(2, "Invalid filename")
    try:
        name = filename.encode("utf-8")
    except UnicodeEncodeError as exc:  # ví dụ: chuỗi chứa surrogate lẻ
        raise TFTPError(2, "Filename is not valid UTF-8") from exc
    return b"".join(
        [_OPCODE.pack(opcode), name, _NUL, MODE_OCTET.encode("ascii"), _NUL]
    ) + _encode_options(options)


def _unpack_request(opcode: int, raw: bytes) -> Packet:
    body = raw[2:]
    if not body.endswith(_NUL):
        raise TFTPError(4, "Malformed request: missing NUL terminator")
    fields = body[:-1].split(_NUL)
    if not fields[0]:
        raise TFTPError(4, "Missing filename")
    if len(fields) < 2:
        raise TFTPError(4, "Missing mode")
    try:
        filename = fields[0].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TFTPError(4, "Filename is not valid UTF-8") from exc
    mode = fields[1].decode("ascii", errors="replace").lower()
    if mode not in _VALID_MODES:
        raise TFTPError(4, f"Unsupported mode: {mode!r}")
    options = _decode_options(fields[2:])
    return Packet(opcode=opcode, filename=filename, mode=mode, options=options)


# --------------------------------------------------------------------------- #
# Đóng gói (pack)
# --------------------------------------------------------------------------- #


def pack_rrq(filename: str, options: dict[str, str] | None = None) -> bytes:
    """Đóng gói RRQ (mode luôn là ``octet``)."""
    return _pack_request(OP_RRQ, filename, options)


def pack_wrq(filename: str, options: dict[str, str] | None = None) -> bytes:
    """Đóng gói WRQ (mode luôn là ``octet``)."""
    return _pack_request(OP_WRQ, filename, options)


def pack_data(block: int, data: bytes) -> bytes:
    """Đóng gói DATA; ``data`` tối đa :data:`MAX_DATA_SIZE` byte (có thể rỗng)."""
    _check_block(block)
    if len(data) > MAX_DATA_SIZE:
        raise TFTPError(4, "Data too large")
    return _OPCODE_BLOCK.pack(OP_DATA, block) + bytes(data)


def pack_ack(block: int) -> bytes:
    """Đóng gói ACK cho ``block`` (0..65535)."""
    _check_block(block)
    return _OPCODE_BLOCK.pack(OP_ACK, block)


def pack_error(code: int, message: str | None = None) -> bytes:
    """Đóng gói ERROR; nếu không có ``message`` thì dùng mô tả chuẩn của mã lỗi."""
    if not isinstance(code, int) or isinstance(code, bool) or not 0 <= code <= _MAX_BLOCK:
        raise TFTPError(4, f"Invalid error code: {code!r}")
    text = (message or ERROR_CODES.get(code, "Unknown error")).replace("\x00", "")
    return _OPCODE_BLOCK.pack(OP_ERROR, code) + text.encode("utf-8", errors="replace") + _NUL


def pack_oack(options: dict[str, str]) -> bytes:
    """Đóng gói OACK; phải có ít nhất một option."""
    if not options:
        raise TFTPError(8, "OACK requires at least one option")
    return _OPCODE.pack(OP_OACK) + _encode_options(options)


# --------------------------------------------------------------------------- #
# Giải mã (unpack)
# --------------------------------------------------------------------------- #


def unpack_packet(raw: bytes) -> Packet:
    """Giải mã một gói tin thô thành :class:`Packet`.

    Raises:
        TFTPError: gói quá ngắn, sai cấu trúc, opcode lạ (code 4) hoặc option
            không hợp lệ (code 8). Không bao giờ ném exception khác với đầu
            vào là ``bytes`` bất kỳ.
    """
    if len(raw) < 2:
        raise TFTPError(4, "Packet too short")
    opcode = _OPCODE.unpack_from(raw, 0)[0]

    if opcode in (OP_RRQ, OP_WRQ):
        return _unpack_request(opcode, raw)

    if opcode == OP_DATA:
        if len(raw) < 4:
            raise TFTPError(4, "Malformed DATA")
        if len(raw) - 4 > MAX_DATA_SIZE:
            raise TFTPError(4, "DATA too large")
        block = _OPCODE_BLOCK.unpack_from(raw, 0)[1]
        return Packet(opcode=opcode, block=block, data=bytes(raw[4:]))

    if opcode == OP_ACK:
        if len(raw) != 4:
            raise TFTPError(4, "Malformed ACK")
        return Packet(opcode=opcode, block=_OPCODE_BLOCK.unpack_from(raw, 0)[1])

    if opcode == OP_ERROR:
        if len(raw) < 5:
            raise TFTPError(4, "Malformed ERROR")
        code = _OPCODE_BLOCK.unpack_from(raw, 0)[1]
        message = raw[4:].split(_NUL, 1)[0]  # đến NUL đầu tiên; thiếu NUL vẫn đọc được
        return Packet(
            opcode=opcode,
            error_code=code,
            error_msg=message.decode("utf-8", errors="replace"),
        )

    if opcode == OP_OACK:
        body = raw[2:]
        if not body:
            raise TFTPError(8, "Empty OACK")
        if not body.endswith(_NUL):
            raise TFTPError(4, "Malformed OACK: missing NUL terminator")
        return Packet(opcode=opcode, options=_decode_options(body[:-1].split(_NUL)))

    raise TFTPError(4, f"Unsupported opcode: {opcode}")


# --------------------------------------------------------------------------- #
# Kiểm tra option (RFC 2347/2348/2349) - dùng chung cho server và client
# --------------------------------------------------------------------------- #


def _parse_int(name: str, value: str, low: int, high: int | None) -> int:
    # isascii() + isdigit() loại bỏ "+5", " 5", "-1", "٣" (chữ số Unicode)...
    if not (value.isascii() and value.isdigit()):
        raise TFTPError(8, f"Invalid {name}: {value!r}")
    number = int(value)
    if number < low or (high is not None and number > high):
        bound = f"{low}..{high}" if high is not None else f">= {low}"
        raise TFTPError(8, f"{name} must be {bound}")
    return number


def validate_options(options: Mapping[str, str]) -> dict[str, str]:
    """Kiểm tra các option mà TFTP Pro hỗ trợ và trả về dict đã chuẩn hóa.

    * ``blksize``: 8..65464 (RFC 2348)
    * ``timeout``: 1..255 giây (RFC 2349)
    * ``tsize``: số nguyên >= 0 (RFC 2349)
    * ``windowsize``: 1..65535 block mỗi cửa sổ (RFC 7440)

    Option không nhận diện bị bỏ qua - đúng RFC 2347:
    bên nhận được phép lờ đi option lạ. Giá trị sai ném ``TFTPError(8)``.
    """
    accepted: dict[str, str] = {}
    for key, value in options.items():
        name = key.lower()
        if name == "blksize":
            accepted[name] = str(_parse_int(name, value, MIN_BLKSIZE, MAX_BLKSIZE))
        elif name == "timeout":
            accepted[name] = str(_parse_int(name, value, MIN_TIMEOUT, MAX_TIMEOUT))
        elif name == "tsize":
            accepted[name] = str(_parse_int(name, value, 0, None))
        elif name == "windowsize":
            accepted[name] = str(_parse_int(name, value, MIN_WINDOWSIZE, MAX_WINDOWSIZE))
    return accepted
