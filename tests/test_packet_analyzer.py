"""Kiểm thử lọc, tổng hợp và nhóm packet của PacketAnalyzer."""

from packet_analyzer import (
    PacketAnalyzer,
    PacketFilter,
    filter_packets,
    format_table,
    group_by_conversation,
    summarize_packets,
)
from packet_trace import PacketTracer
from tftp_packet import pack_ack, pack_data, pack_rrq


def make_events():
    tracer = PacketTracer()
    tracer.record_raw(
        pack_rrq("a.bin"),
        source="10.0.0.2:50000",
        destination="10.0.0.1:6969",
        direction="Client -> Server",
    )
    for block, payload in ((1, b"a" * 512), (2, b"b")):
        tracer.record_raw(
            pack_data(block, payload),
            source="10.0.0.1:40000",
            destination="10.0.0.2:50000",
            direction="Server -> Client",
        )
        tracer.record_raw(
            pack_ack(block),
            source="10.0.0.2:50000",
            destination="10.0.0.1:40000",
            direction="Client -> Server",
        )
    # DATA block 1 bị gửi lại.
    tracer.record_raw(
        pack_data(1, b"a" * 512),
        source="10.0.0.1:40000",
        destination="10.0.0.2:50000",
        direction="Server -> Client",
    )
    return tracer.events()


def test_filter_by_opcode_direction_and_block():
    events = make_events()
    only_data = filter_packets(events, PacketFilter(opcode="data"))
    assert len(only_data) == 3
    assert {event["opcode"] for event in only_data} == {"DATA"}

    from_server = filter_packets(events, PacketFilter(direction="server -> client"))
    assert all("Server -> Client" in event["direction"] for event in from_server)
    assert filter_packets(events, PacketFilter(block_number=2))[0]["opcode"] == "DATA"
    assert filter_packets(events, PacketFilter(source="10.0.0.2"))[0]["opcode"] == "RRQ"
    assert filter_packets(events, PacketFilter(opcode="ERROR")) == []


def test_summary_counts_and_retransmission():
    summary = summarize_packets(make_events())
    assert summary["total"] == 6
    assert summary["counts"] == {"RRQ": 1, "DATA": 3, "ACK": 2}
    assert summary["data_bytes"] == 2 * 512 + 1
    assert summary["payload_bytes"] > summary["data_bytes"]
    assert summary["data_retransmissions"] == 1
    assert summary["first_timestamp"]
    assert summary["last_timestamp"]


def test_groups_and_api_wrapper():
    events = make_events()
    groups = group_by_conversation(events)
    assert len(groups) == 1
    assert groups[0]["packets"] == 6
    assert groups[0]["opcodes"]["DATA"] == 3

    analyzer = PacketAnalyzer.from_api({"events": events})
    assert analyzer.summary()["total"] == 6
    assert len(analyzer.rows(packet_filter=PacketFilter(opcode="ACK"))) == 2
    assert "DATA" in analyzer.format_table()
    assert "Không có packet" in format_table([])
