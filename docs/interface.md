# Interface contract

Tài liệu này tóm tắt interface công khai của các module TFTP Pro 1.1.0. Chi tiết
giao thức xem `protocol.md`; chi tiết HTTP xem `docs/admin_api.md`.

## tftp_config.py

- Hằng số: `APP_NAME`, `VERSION`, `DEFAULT_HOST`, `DEFAULT_CLIENT_HOST`,
  `DEFAULT_PORT`, `DEFAULT_ROOT`, `DEFAULT_BLKSIZE`, `DEFAULT_TIMEOUT`,
  `DEFAULT_RETRIES`, `DEFAULT_MAX_CLIENTS`, `DEFAULT_WINDOWSIZE`,
  `DEFAULT_MAX_WINDOWSIZE`, `DEFAULT_ADMIN_HOST`, `DEFAULT_ADMIN_PORT`,
  `DEFAULT_ADMIN_DB`, `DEFAULT_TOKEN_TTL`, `DEFAULT_MAX_ADMIN_BODY`,
  `DEFAULT_PXE_PROFILES`, `PXE_BOOT_DIRS`, `LEGACY_LISTING_OFFSET`.
- `Defaults` dataclass và `DEFAULTS`.
- `VERSION = "1.1.0"`.

## tftp_packet.py

- Hằng số: `BLOCK_SIZE`, `MAX_BLKSIZE`, `MIN_BLKSIZE`, `MAX_TIMEOUT`,
  `MIN_TIMEOUT`, `MAX_PACKET_SIZE`, `OP_RRQ`, `OP_WRQ`, `OP_DATA`, `OP_ACK`,
  `OP_ERROR`, `OP_OACK`, `__version__`, `ERROR_CODES`.
- `class TFTPError(Exception)` với `.code`, `.message`.
- `@dataclass class Packet`.
- `pack_rrq(filename, mode="octet", options=None) -> bytes`
- `pack_wrq(...)`, `pack_data(block, data)`, `pack_ack(block)`,
  `pack_error(code, message=None)`, `pack_oack(options)`
- `unpack_packet(raw) -> Packet`
- `validate_options(options) -> dict[str, str]`

## tftp_options.py

- Hằng số: `MIN_WINDOWSIZE`, `MAX_WINDOWSIZE`.
- `negotiate_options(requested, *, is_upload=False, file_size=None, server_windowsize=DEFAULT_MAX_WINDOWSIZE) -> dict[str, str]`
- `@dataclass(frozen=True) class TransferOptions`:
  - fields `blksize`, `timeout`, `windowsize`, `tsize`
  - `from_oack(options, *, fallback_blksize=..., fallback_timeout=...)`
  - `as_request_options(*, upload, file_size=None) -> dict[str, str]`

## tftp_server.py

- `@dataclass class ServerConfig(host, port, root, timeout, retries, max_clients, overwrite, blksize, windowsize, log_level)`
- `@dataclass class TransferOutcome`
- `class TFTPServer(config, *, metrics=None, tracer=None)`:
  - `storage: StorageManager`
  - `serve_forever()`, `stop()`
  - `safe_path(filename) -> Path`
- CLI: `--host --port --root --timeout --retries --max-clients --overwrite --blksize --windowsize --log-level`

## tftp_client.py

- `class TFTPClient(host, port, timeout=3.0, retries=5, blksize=512, windowsize=1, on_log=None)`
- `download(remote, local, on_progress=None, cancel=None) -> int`
- `upload(local, remote=None, on_progress=None, cancel=None) -> int`
- `list_files()`
- CLI: `tftp-client get/put`, options `--host --port --timeout --retries --blksize --windowsize`

## tftp_gui.py

- `TFTPApp` Tkinter application, config file `%APPDATA%/TFTPPro/config.json`.
- Tabs: Transfer, File Manager, Dashboard, Packet Analyzer, Users, PXE.
- `load_config()`, `save_config(data)`, `sha256_file(path)`, `join_remote(base, name)`.
- CLI: `python tftp_gui.py` (no arguments).

## storage_manager.py

- `class StorageError(Exception)` với `.message`, `.status`.
- `@dataclass(frozen=True) class FileMetadata(name, relative_path, size, modified_at, is_dir, sha256)`
- `class StorageManager(root)`:
  - path: `clean_relative(relative)`, `resolve(relative, *, allow_root=False)`, `relative_path(path)`
  - IO: `exists`, `is_file`, `is_dir`, `read_bytes`, `write_bytes`, `open_read`
  - hash/metadata: `sha256`, `metadata`, `verify`
  - listing/search: `list_dir`, `list_files`, `search`
  - admin: `delete`, `rename`, `mkdir`, `ensure_layout`

## auth.py

- `class AuthError(Exception)` với `.message`, `.status`.
- `MIN_PASSWORD_LENGTH = 4`
- `ROLE_PERMISSIONS: dict[str, set[str]]` cho `admin`, `user`, `guest`.
- `class AuthManager(db_path, *, token_ttl=DEFAULT_TOKEN_TTL)`:
  - users: `create_user`, `get_user`, `list_users`, `delete_user`, `set_role`, `set_active`, `change_password`
  - auth/token: `authenticate`, `login`, `validate_token`, `revoke_token`
  - permission/audit: `has_permission`, `require`, `list_audit`
  - bootstrap: `ensure_default_admin(password=None) -> tuple[str, str] | None`

## admin_api.py

- `class AdminAPIError(Exception)`
- `class AdminAPIServer(storage, auth, *, metrics=None, tracer=None, pxe=None, host=DEFAULT_ADMIN_HOST, port=DEFAULT_ADMIN_PORT, max_body=DEFAULT_MAX_ADMIN_BODY)`:
  - `start(*, background=True)`, `stop()`, `url`
- `main(argv=None) -> int`
- CLI: `--host --port --root --db --metrics-db --pxe-profiles --log-level`

## admin_client.py

- `class AdminAPIError(Exception)`
- `class AdminClient(base_url, token=None, *, timeout=30.0)`:
  - auth: `login`, `logout`, `me`, `health`
  - files: `list_dir`, `search`, `metadata`, `hash`, `download`, `download_with_headers`, `upload`, `verify`, `delete`, `rename`, `mkdir`
  - users: `list_users`, `create_user`, `delete_user`, `set_user_role`, `set_user_active`, `change_password`, `audit_log`
  - monitoring: `dashboard`, `packets`
  - PXE: `pxe_overview`, `pxe_profiles`, `pxe_issues`, `upsert_pxe_profile`, `delete_pxe_profile`, `select_pxe_profile`

## metrics.py

- `@dataclass(frozen=True) class TransferEvent`
- `class MetricsCollector(db_path=None, *, max_recent=500)`:
  - sessions: `start_session`, `end_session`, `active_clients`
  - recording: `record_transfer`, `record`
  - query: `snapshot`, `recent_transfers`, `reset`

## packet_trace.py

- `OPCODE_NAMES`, `opcode_name(opcode)`
- `@dataclass(frozen=True) class PacketEvent`
- `class PacketTracer(max_events=2000)`:
  - `add(event)`, `record_raw(raw, *, source, destination, direction, timestamp=None)`
  - `events(*, limit=200, opcode=None)`, `counts()`, `reset()`

## packet_analyzer.py

- `PACKET_COLUMNS`
- `@dataclass(frozen=True) class PacketFilter`
- `filter_packets(events, packet_filter=None) -> list[dict]`
- `summarize_packets(events) -> dict`
- `group_by_conversation(events) -> list[dict]`
- `format_table(events, *, limit=200) -> str`
- `class PacketAnalyzer(events=None)` with `from_api(payload)`, `rows(...)`, `summary(...)`, `conversations(...)`, `format_table(...)`

## pxe_config.py

- `class PxeError(Exception)`
- `@dataclass(frozen=True) class PxeProfile`
- `normalize_arch(arch) -> str`
- `class PxeManager(storage, path=DEFAULT_PXE_PROFILES, *, auto_create=True)`:
  - `list_profiles`, `get_profile`, `upsert_profile`, `delete_profile`, `select_profile`, `validate_all`, `ensure_directories`

## tftp_pro.py

- `build_parser() -> argparse.ArgumentParser`
- `main(argv=None) -> int`
- CLI: `--host --port --root --timeout --retries --max-clients --overwrite --blksize --windowsize --admin-host --admin-port --admin-db --metrics-db --pxe-profiles --no-admin --log-level`

## scripts/benchmark_tftp.py

- `parse_size(text) -> int`
- `sha256_file(path) -> str`
- `start_server(root)`
- `run_case(host, port, source, root, workdir, blksize, windowsize, runs)`
- CLI: `--size --runs --json-out`

## GUI config

- `APP_DIR = %APPDATA%/TFTPPro`, `CONFIG_PATH = APP_DIR/config.json`.
- Config keys: `host`, `port`, `timeout`, `windowsize`, `admin_url`, `admin_user`.
- Default admin URL: `http://127.0.0.1:6971`.