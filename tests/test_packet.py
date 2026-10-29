from tftp_packet import (
    OP_ACK,
    OP_DATA,
    OP_ERROR,
    OP_OACK,
    OP_RRQ,
    OP_WRQ,
    TFTPError,
    pack_ack,
    pack_data,
    pack_error,
    pack_oack,
    pack_rrq,
    pack_wrq,
    unpack_packet,
)


def test_rrq_roundtrip_ascii():
    raw = pack_rrq("file.txt", options={"blksize": "1460"})
    pkt = unpack_packet(raw)
    assert pkt.opcode == OP_RRQ
    assert pkt.filename == "file.txt"
    assert pkt.options["blksize"] == "1460"


def test_rrq_unicode():
    raw = pack_rrq("tập.txt")
    pkt = unpack_packet(raw)
    assert pkt.filename == "tập.txt"


def test_wrq_roundtrip():
    raw = pack_wrq("out.bin")
    pkt = unpack_packet(raw)
    assert pkt.opcode == OP_WRQ
    assert pkt.filename == "out.bin"


def test_data_sizes():
    for size in (0, 1, 511, 512, 65464):
        data = b"x" * size
        raw = pack_data(1, data)
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_DATA
        assert pkt.block == 1
        assert pkt.data == data


def test_ack_block():
    for b in (0, 1, 65535):
        raw = pack_ack(b)
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_ACK
        assert pkt.block == (b & 0xFFFF)


def test_error_codes():
    for code in range(9):
        raw = pack_error(code, "msg")
        pkt = unpack_packet(raw)
        assert pkt.opcode == OP_ERROR
        assert pkt.error_code == code


def test_invalid_packet():
    try:
        unpack_packet(b"\x00")
    except TFTPError:
        pass
    else:
        raise AssertionError("expected TFTPError")


def test_oack_parse():
    raw = pack_oack({"blksize": "1460", "timeout": "3"})
    pkt = unpack_packet(raw)
    assert pkt.opcode == OP_OACK
    assert pkt.options["blksize"] == "1460"
    assert pkt.options["timeout"] == "3"
