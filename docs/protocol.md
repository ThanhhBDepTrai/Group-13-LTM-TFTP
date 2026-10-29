# Protocol notes (RFC 1350)

Opcodes: RRQ=1, WRQ=2, DATA=3, ACK=4, ERROR=5, OACK=6.

Packet formats (big-endian):
- RRQ/WRQ: 2B opcode | filename | 0x00 | mode | 0x00 | [opt | 0x00 | value | 0x00]...
- DATA: 2B opcode=3 | 2B block | data (0..blksize)
- ACK: 2B opcode=4 | 2B block
- ERROR: 2B opcode=5 | 2B error code | message | 0x00
- OACK: 2B opcode=6 | [opt | 0x00 | value | 0x00]...

File ends when DATA payload < blksize (including 0 bytes).
Block number wraps 16-bit.
