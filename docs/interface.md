# Interface contract (mục 3.7)

## tftp_packet.py (TV1)
- `pack_rrq(filename, mode="octet", options=None) -> bytes`
- `pack_wrq(...)`, `pack_data(block, data)`, `pack_ack(block)`, `pack_error(code, msg)`, `pack_oack(options)`
- `unpack_packet(raw) -> Packet`
- `TFTPError(error_code, error_msg)`

## tftp_server.py (TV2)
- `TFTPServer(config: ServerConfig)`
- `serve_forever()`, `stop()`
- CLI: `--host --port --root --timeout --retries --max-clients --overwrite --blksize --log-level`

## tftp_client.py (TV3)
- `TFTPClient(host, port, timeout=3.0, retries=5, blksize=512, on_log=None)`
- `download(remote, local, on_progress=None, cancel=None) -> int`
- `upload(local, remote=None, on_progress=None, cancel=None) -> int`
- CLI: `tftp-client get/put`

## tftp_gui.py (TV3)
- Tkinter app, queue-based UI updates, config in %APPDATA%/TFTPPro/config.json.
