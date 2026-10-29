# TFTP Pro

A small, reliable TFTP client/server with GUI, implementing RFC 1350 and optional blksize/timeout/tsize (RFC 2347-2349).

## Features
- RFC 1350: RRQ, WRQ, DATA, ACK, ERROR
- Multi-client server, ephemeral TID, stop-and-wait retry
- GUI with progress, cancel and packet log
- CLI: `tftp-server`, `tftp-client get/put`
- Optional blksize/timeout/tsize
- Atomic transfer, path traversal protection

## Quick start (from source)
```bash
git clone https://github.com/<owner>/tftp-pro.git
cd tftp-pro
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python tftp_server.py --root ./server_storage
python tftp_gui.py
```

## CLI
```bash
python tftp_client.py --host 127.0.0.1 --port 6969 put ./README.md README.md
python tftp_client.py --host 127.0.0.1 --port 6969 get README.md ./README.downloaded.md
```

## Server options
| Option | Default | Description |
|---|---|---|
| `--host` | 0.0.0.0 | Bind address |
| `--port` | 6969 | UDP port |
| `--root` | server_storage | Shared folder |
| `--timeout` | 3.0 | Seconds |
| `--retries` | 5 | Retransmissions |
| `--max-clients` | 20 | Concurrent sessions |
| `--overwrite` | off | Allow overwrite |

## Security
TFTP has no encryption or authentication. Use only in a trusted LAN. Do not expose to the public Internet.

## License
MIT
