"""Unit test cho tftp_packet.py - chạy nhanh, không cần mạng."""

import random
import struct

import pytest

from tftp_packet import (
    BLOCK_SIZE,
    ERROR_CODES,
    MAX_DATA_SIZE,
    MAX_PACKET_SIZE,
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    OP_RRQ,
    OP_WRQ,
    Packet,
    TFTPError,
    pack_ack,
    pack_data,
    pack_error,
    pack_oack,
    pack_rrq,
    pack_wrq,
    unpack_packet,
    validate_options,
)

# --------------------------------------------------------------------------- #
# Hằng số
# --------------------------------------------------------------------------- #


def test_constants_match_rfc():
    assert BLOCK_SIZE == 512
    assert (OP_RRQ, OP_WRQ, OP_DATA, OP_ACK, OP_ERROR, OP_OACK) == (1, 2, 3, 4, 5, 6)
    assert MAX_PACKET_SIZE == MAX_DATA_SIZE + 4


def test_error_codes_cover_0_to_8():
    assert sorted(ERROR_CODES) == list(range(9))


def test_tftp_error_carries_code_and_message():
    err = TFTPError(6, "File already exists")
    assert err.code == 6
    assert err.message == "File already exists"
    assert str(err) == "File already exists"


# --------------------------------------------------------------------------- #
# RRQ / WRQ
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["a.txt", "tệp tin.txt", "a b.txt", "日本語.bin", "x" * 255])
def test_rrq_roundtrip_keeps_filename(name):
    pkt = unpack_packet(pack_rrq(name))
    assert pkt.opcode == OP_RRQ
    assert pkt.filename == name
    assert pkt.mode == "octet"
    assert pkt.options == {}


def test_wrq_roundtrip():
    pkt = unpack_packet(pack_wrq("photo.jpg"))
    assert pkt.opcode == OP_WRQ
    assert pkt.filename == "photo.jpg"
    assert pkt.mode == "octet"


@pytest.mark.parametrize(
    "name", ["tệp tin.txt", "báo cáo Lập trình Mạng.docx", "日本語.bin"]
)
def test_wrq_roundtrip_keeps_unicode_filename(name):
    pkt = unpack_packet(pack_wrq(name))
    assert pkt.opcode == OP_WRQ
    assert pkt.filename == name
    assert pkt.mode == "octet"


def test_unicode_filename_is_utf8_on_the_wire():
    assert pack_wrq("tệp") == b"\x00\x02t\xe1\xbb\x87p\x00octet\x00"


def test_rrq_wire_format_is_exact():
    assert pack_rrq("a.txt") == b"\x00\x01a.txt\x00octet\x00"
    assert pack_wrq("a.txt") == b"\x00\x02a.txt\x00octet\x00"


def test_rrq_with_options_roundtrip_and_wire_format():
    raw = pack_rrq("big.bin", {"blksize": "1460", "tsize": "0"})
    assert raw == b"\x00\x01big.bin\x00octet\x00blksize\x001460\x00tsize\x000\x00"
    pkt = unpack_packet(raw)
    assert pkt.options == {"blksize": "1460", "tsize": "0"}


def test_option_names_are_lowercased_when_packing_and_parsing():
    assert unpack_packet(pack_wrq("f", {"BLKSIZE": "512"})).options == {"blksize": "512"}
    raw = b"\x00\x01f\x00octet\x00BlkSize\x00512\x00"
    assert unpack_packet(raw).options == {"blksize": "512"}


def test_request_mode_is_case_insensitive():
    assert unpack_packet(b"\x00\x01f\x00OCTET\x00").mode == "octet"
    assert unpack_packet(b"\x00\x01f\x00NetAscii\x00").mode == "netascii"


@pytest.mark.parametrize("bad", ["", "bad\x00name"])
def test_pack_request_rejects_bad_filename(bad):
    with pytest.raises(TFTPError) as exc:
        pack_rrq(bad)
    assert exc.value.code == 2
    with pytest.raises(TFTPError):
        pack_wrq(bad)


def test_pack_request_rejects_unencodable_filename():
    with pytest.raises(TFTPError) as exc:
        pack_rrq("bad\udc80name")  # surrogate lẻ không mã hóa được UTF-8
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "options",
    [
        {"": "1"},
        {"blk\x00size": "1"},
        {"blksize": "1\x002"},
        {"blksize": "٣"},  # giá trị không phải ASCII
        {"tên": "1"},
    ],
)
def test_pack_request_rejects_invalid_options_with_code_8(options):
    with pytest.raises(TFTPError) as exc:
        pack_rrq("a", options)
    assert exc.value.code == 8


def test_pack_rejects_case_duplicate_options():
    with pytest.raises(TFTPError) as exc:
        pack_rrq("a", {"blksize": "512", "BLKSIZE": "1024"})
    assert exc.value.code == 8


@pytest.mark.parametrize(
    "raw",
    [
        b"\x00\x01",  # không có gì sau opcode
        b"\x00\x01a.txt",  # thiếu NUL cuối
        b"\x00\x01a.txt\x00",  # thiếu mode
        b"\x00\x01\x00octet\x00",  # tên file rỗng
        b"\x00\x01a.txt\x00zip\x00",  # mode lạ
        b"\x00\x01a.txt\x00\x00",  # mode rỗng
        b"\x00\x01\xff\xfe\x00octet\x00",  # tên file không phải UTF-8
        b"\x00\x02a\x00octet\x00blksize\x00",  # option thiếu value (lẻ cặp)
    ],
)
def test_unpack_malformed_request_raises_code_4(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


@pytest.mark.parametrize(
    "raw",
    [
        b"\x00\x01a\x00octet\x00\x001\x00",  # tên option rỗng
        b"\x00\x01a\x00octet\x00blksize\x00512\x00blksize\x00600\x00",  # trùng
        b"\x00\x01a\x00octet\x00blksize\x00\xff\x00",  # value không phải ASCII
    ],
)
def test_unpack_invalid_options_raises_code_8(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 8


# --------------------------------------------------------------------------- #
# DATA
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("size", [0, 1, 511, 512, 513, 1460, MAX_DATA_SIZE])
def test_data_roundtrip_boundary_sizes(size):
    data = bytes(i % 251 for i in range(size))
    pkt = unpack_packet(pack_data(7, data))
    assert pkt.opcode == OP_DATA
    assert pkt.block == 7
    assert pkt.data == data


def test_data_wire_format_is_exact():
    assert pack_data(1, b"abc") == b"\x00\x03\x00\x01abc"
    assert pack_data(0xABCD, b"") == b"\x00\x03\xab\xcd"


def test_data_may_contain_nul_bytes():
    payload = b"\x00\x01\x00\x00\xff"
    assert unpack_packet(pack_data(2, payload)).data == payload


def test_data_accepts_bytearray_and_memoryview():
    assert unpack_packet(pack_data(1, bytearray(b"xy"))).data == b"xy"
    assert unpack_packet(pack_data(1, memoryview(b"xy"))).data == b"xy"


def test_pack_data_rejects_oversize():
    with pytest.raises(TFTPError) as exc:
        pack_data(1, b"x" * (MAX_DATA_SIZE + 1))
    assert exc.value.code == 4


def test_unpack_data_rejects_oversize():
    raw = struct.pack("!HH", OP_DATA, 1) + b"x" * (MAX_DATA_SIZE + 1)
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


@pytest.mark.parametrize("raw", [b"\x00\x03", b"\x00\x03\x00"])
def test_unpack_truncated_data_raises(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


# --------------------------------------------------------------------------- #
# ACK
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("block", [0, 1, 255, 256, 65534, 65535])
def test_ack_roundtrip(block):
    pkt = unpack_packet(pack_ack(block))
    assert pkt.opcode == OP_ACK
    assert pkt.block == block


def test_ack_block_counter_wraps_from_65535_to_0():
    block = 65535
    wrapped = (block + 1) & 0xFFFF  # cách server/client tăng block
    assert wrapped == 0
    assert unpack_packet(pack_ack(wrapped)).block == 0
    assert unpack_packet(pack_data(wrapped, b"x")).block == 0


def test_ack_wire_format_is_exact():
    assert pack_ack(0) == b"\x00\x04\x00\x00"
    assert pack_ack(65535) == b"\x00\x04\xff\xff"


@pytest.mark.parametrize("block", [-1, 65536, 1.5, "1", None, True])
def test_pack_ack_and_data_reject_invalid_block(block):
    with pytest.raises(TFTPError) as exc:
        pack_ack(block)
    assert exc.value.code == 4
    with pytest.raises(TFTPError):
        pack_data(block, b"x")


@pytest.mark.parametrize("raw", [b"\x00\x04", b"\x00\x04\x00", b"\x00\x04\x00\x01\x00"])
def test_unpack_ack_with_wrong_length_raises(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


# --------------------------------------------------------------------------- #
# ERROR
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_error_roundtrip_all_standard_codes(code):
    pkt = unpack_packet(pack_error(code, "boom"))
    assert pkt.opcode == OP_ERROR
    assert pkt.error_code == code
    assert pkt.error_msg == "boom"


def test_error_default_message_comes_from_table():
    assert unpack_packet(pack_error(1)).error_msg == "File not found"
    assert unpack_packet(pack_error(99)).error_msg == "Unknown error"


def test_error_unicode_message_roundtrip():
    msg = "Không tìm thấy tệp"
    assert unpack_packet(pack_error(1, msg)).error_msg == msg


def test_error_wire_format_is_exact():
    assert pack_error(1, "x") == b"\x00\x05\x00\x01x\x00"


def test_error_message_nul_is_stripped():
    assert unpack_packet(pack_error(0, "a\x00b")).error_msg == "ab"


def test_unpack_error_without_trailing_nul_keeps_last_char():
    pkt = unpack_packet(b"\x00\x05\x00\x01abc")
    assert pkt.error_msg == "abc"


def test_unpack_error_stops_at_first_nul():
    assert unpack_packet(b"\x00\x05\x00\x02abc\x00junk\x00").error_msg == "abc"


def test_unpack_error_with_invalid_utf8_does_not_crash():
    pkt = unpack_packet(b"\x00\x05\x00\x00\xff\xfe\x00")
    assert pkt.error_code == 0
    assert "\ufffd" in pkt.error_msg


@pytest.mark.parametrize("raw", [b"\x00\x05", b"\x00\x05\x00\x01"])
def test_unpack_truncated_error_raises(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


@pytest.mark.parametrize("code", [-1, 65536, "1", None])
def test_pack_error_rejects_invalid_code(code):
    with pytest.raises(TFTPError):
        pack_error(code, "x")


# --------------------------------------------------------------------------- #
# OACK
# --------------------------------------------------------------------------- #


def test_oack_roundtrip():
    opts = {"blksize": "1460", "timeout": "3", "tsize": "10485760"}
    pkt = unpack_packet(pack_oack(opts))
    assert pkt.opcode == OP_OACK
    assert pkt.options == opts


def test_oack_many_options_keep_all_pairs_and_order():
    opts = {"blksize": "1460", "timeout": "2", "tsize": "123456", "windowsize": "4"}
    pkt = unpack_packet(pack_oack(opts))
    assert pkt.options == opts
    assert list(pkt.options) == list(opts)


def test_oack_wire_format_is_exact():
    assert pack_oack({"blksize": "1024"}) == b"\x00\x06blksize\x001024\x00"


def test_pack_oack_requires_options():
    with pytest.raises(TFTPError) as exc:
        pack_oack({})
    assert exc.value.code == 8


def test_pack_oack_rejects_non_ascii_option():
    with pytest.raises(TFTPError) as exc:
        pack_oack({"blksize": "٣"})
    assert exc.value.code == 8


def test_unpack_empty_oack_raises_code_8():
    with pytest.raises(TFTPError) as exc:
        unpack_packet(b"\x00\x06")
    assert exc.value.code == 8


@pytest.mark.parametrize("raw", [b"\x00\x06blksize\x00512", b"\x00\x06blksize\x00\x00x"])
def test_unpack_oack_without_nul_terminator_raises(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


def test_unpack_oack_with_odd_fields_raises():
    with pytest.raises(TFTPError) as exc:
        unpack_packet(b"\x00\x06blksize\x00")
    assert exc.value.code == 4


# --------------------------------------------------------------------------- #
# Gói hỏng / fuzz
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw", [b"", b"\x00", b"\x00\x00", b"\x00\x07", b"\x00\x63", b"\xff\xff"])
def test_unknown_or_short_packets_raise_code_4(raw):
    with pytest.raises(TFTPError) as exc:
        unpack_packet(raw)
    assert exc.value.code == 4


def test_unpack_never_raises_anything_but_tftp_error():
    rng = random.Random(1350)
    for _ in range(3000):
        raw = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 40)))
        if rng.random() < 0.8 and len(raw) >= 2:  # ép opcode hợp lệ để đi sâu vào parser
            raw = bytes([0, rng.randrange(1, 7)]) + raw[2:]
        try:
            pkt = unpack_packet(raw)
        except TFTPError:
            continue
        assert isinstance(pkt, Packet)


# --------------------------------------------------------------------------- #
# validate_options
# --------------------------------------------------------------------------- #


def test_validate_options_accepts_valid_values():
    opts = {"blksize": "1460", "timeout": "3", "tsize": "0"}
    assert validate_options(opts) == opts


@pytest.mark.parametrize("value", ["8", "65464"])
def test_validate_blksize_boundaries_accepted(value):
    assert validate_options({"blksize": value}) == {"blksize": value}


@pytest.mark.parametrize(
    "value", ["1", "7", "0", "65465", "70000", "abc", "", "-8", "+512", " 512", "٥١٢"]
)
def test_validate_blksize_invalid_raises_code_8(value):
    with pytest.raises(TFTPError) as exc:
        validate_options({"blksize": value})
    assert exc.value.code == 8


@pytest.mark.parametrize("value", ["1", "255"])
def test_validate_timeout_boundaries_accepted(value):
    assert validate_options({"timeout": value}) == {"timeout": value}


@pytest.mark.parametrize("value", ["0", "256", "x", "-1"])
def test_validate_timeout_invalid_raises_code_8(value):
    with pytest.raises(TFTPError) as exc:
        validate_options({"timeout": value})
    assert exc.value.code == 8


@pytest.mark.parametrize("value", ["0", "1", "10485760", "99999999999"])
def test_validate_tsize_accepts_non_negative(value):
    assert validate_options({"tsize": value}) == {"tsize": value}


@pytest.mark.parametrize("value", ["-1", "ten", ""])
def test_validate_tsize_invalid_raises_code_8(value):
    with pytest.raises(TFTPError):
        validate_options({"tsize": value})


def test_validate_options_ignores_unknown_and_normalizes():
    out = validate_options({"BLKSIZE": "0512", "windowsize": "4"})
    assert out == {"blksize": "512"}


def test_validate_options_empty_returns_empty():
    assert validate_options({}) == {}
