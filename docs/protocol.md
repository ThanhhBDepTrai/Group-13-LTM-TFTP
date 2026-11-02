# Protocol notes (RFC 1350, RFC 2347-2349, RFC 7440)

Opcodes: RRQ=1, WRQ=2, DATA=3, ACK=4, ERROR=5, OACK=6. No new opcode is added
for windowsize; it is negotiated as an option.

Packet formats (big-endian):
- RRQ/WRQ: 2B opcode | filename | 0x00 | mode | 0x00 | [opt | 0x00 | value | 0x00]...
- DATA: 2B opcode=3 | 2B block | data (0..blksize)
- ACK: 2B opcode=4 | 2B block
- ERROR: 2B opcode=5 | 2B error code | message | 0x00
- OACK: 2B opcode=6 | [opt | 0x00 | value | 0x00]...

Options:
- blksize: 8..65464, default 512
- timeout: 1..255 seconds, default 3
- tsize: integer >= 0; RRQ sends 0, server answers with real size
- windowsize: 1..65535, default 1; OACK value is <= client request and server max

File ends when DATA payload < blksize (including 0 bytes). Block number wraps 16-bit.

RFC 7440 windowed transfer:
- Sender sends up to `windowsize` DATA blocks before waiting for the ACK of the
  last block in the window.
- ACK is cumulative: ACK #n confirms all blocks up to n in the window.
- Receiver ACKs after a full window or the final block; missing blocks cause
  timeout and retransmission from the first unacknowledged block.
- Duplicate/old blocks are ignored and re-ACKed; data is not written twice.
- If the option is not accepted, both sides fall back to windowsize = 1.
- Client default remains 1; server default maximum window is 16.

Error codes: 0 not defined, 1 file not found, 2 access violation, 3 disk full,
4 illegal operation, 5 unknown transfer ID, 6 file already exists, 7 no such user,
8 option negotiation failed.

Path and management boundaries:
- TFTP has no authentication; keep it in trusted LAN/lab only.
- Management operations go through the separate Admin API, not TFTP opcodes.
- Admin API defaults to 127.0.0.1:6971.
- StorageManager is the only path validation/filesystem layer.