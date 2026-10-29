"""TFTP packet layer (RFC 1350 + option extension RFC 2347)."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

OP_RRQ = 1
OP_WRQ = 2
OP_DATA = 3
OP_ACK = 4
OP_ERROR = 5
OP_OACK = 6

MODE_OCTET = "octet"
BLOCK_SIZE = 512
MAX_PACKET_SIZE = 65535 + 4

ERROR_CODES = {
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


class TFTPError(Exception):
    def __init__(self, error_code: int, error_msg: str = ""):
        super().__init__(error_msg or ERROR_CODES.get(error_code, "Error"))
        self.error_code = error_code
        self.error_msg = error_msg or ERROR_CODES.get(error_code, "Error")


@dataclass
class Packet:
    opcode: int
    filename: str = ""
    mode: str = MODE_OCTET
    options: dict[str, str] = field(default_factory=dict)
    block: int = 0
    data: bytes = b""
    error_code: int = 0
    error_msg: str = ""


def _split_cstr(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.find(b"\x00", pos)
    if end < 0:
        raise TFTPError(4, "Missing null terminator")
    raw = buf[pos:end]
    try:
        value = raw.decode("utf-8")
    except UnicodeDecodeError:
        value = raw.decode("latin-1")
    return value, end + 1


def pack_rrq(filename: str, mode: str = MODE_OCTET, options: dict[str, str] | None = None) -> bytes:
    return _pack_request(OP_RRQ, filename, mode, options or {})


def pack_wrq(filename: str, mode: str = MODE_OCTET, options: dict[str, str] | None = None) -> bytes:
    return _pack_request(OP_WRQ, filename, mode, options or {})


def _pack_request(opcode: int, filename: str, mode: str, options: dict[str, str]) -> bytes:
    out = struct.pack("!H", opcode) + filename.encode("utf-8") + b"\x00" + mode.encode("ascii") + b"\x00"
    for key, value in options.items():
        out += key.encode("ascii") + b"\x00" + str(value).encode("ascii") + b"\x00"
    return out


def pack_data(block: int, data: bytes) -> bytes:
    if len(data) > 65535:
        raise ValueError("DATA payload too large")
    return struct.pack("!HH", OP_DATA, block & 0xFFFF) + data


def pack_ack(block: int) -> bytes:
    return struct.pack("!HH", OP_ACK, block & 0xFFFF)


def pack_error(code: int, msg: str) -> bytes:
    return struct.pack("!HH", OP_ERROR, code) + msg.encode("utf-8", errors="replace") + b"\x00"


def pack_oack(options: dict[str, str]) -> bytes:
    out = struct.pack("!H", OP_OACK)
    for key, value in options.items():
        out += key.encode("ascii") + b"\x00" + str(value).encode("ascii") + b"\x00"
    return out


def unpack_packet(raw: bytes) -> Packet:
    if len(raw) < 2:
        raise TFTPError(4, "Packet too short")
    opcode = struct.unpack("!H", raw[:2])[0]
    if opcode == OP_RRQ or opcode == OP_WRQ:
        pos = 2
        filename, pos = _split_cstr(raw, pos)
        mode, pos = _split_cstr(raw, pos)
        options: dict[str, str] = {}
        while pos < len(raw):
            key, pos = _split_cstr(raw, pos)
            if not key:
                break
            value, pos = _split_cstr(raw, pos)
            options[key.lower()] = value
        return Packet(opcode=opcode, filename=filename, mode=mode, options=options)
    if opcode == OP_DATA:
        if len(raw) < 4:
            raise TFTPError(4, "DATA packet too short")
        block = struct.unpack("!H", raw[2:4])[0]
        return Packet(opcode=opcode, block=block, data=raw[4:])
    if opcode == OP_ACK:
        if len(raw) < 4:
            raise TFTPError(4, "ACK packet too short")
        block = struct.unpack("!H", raw[2:4])[0]
        return Packet(opcode=opcode, block=block)
    if opcode == OP_ERROR:
        if len(raw) < 4:
            raise TFTPError(4, "ERROR packet too short")
        code = struct.unpack("!H", raw[2:4])[0]
        msg = raw[4:]
        if msg.endswith(b"\x00"):
            msg = msg[:-1]
        try:
            text = msg.decode("utf-8")
        except UnicodeDecodeError:
            text = msg.decode("latin-1")
        return Packet(opcode=opcode, error_code=code, error_msg=text)
    if opcode == OP_OACK:
        pos = 2
        options = {}
        while pos < len(raw):
            key, pos = _split_cstr(raw, pos)
            if not key:
                break
            value, pos = _split_cstr(raw, pos)
            options[key.lower()] = value
        return Packet(opcode=opcode, options=options)
    raise TFTPError(4, f"Unknown opcode {opcode}")
