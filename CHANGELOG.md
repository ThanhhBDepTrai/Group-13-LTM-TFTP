# Changelog

## [1.0.0] - 2026-10-28
### Added
- Multi-client UDP server with ephemeral TID and thread pool
- Tkinter GUI with progress, cancel and packet log
- CLI client (get/put) and server
- Atomic upload/download, path traversal protection
- Optional blksize, timeout, tsize negotiation
### Fixed
- Correct retry when ACK/DATA is lost
- Duplicate DATA handling
- GUI thread safety
### Known limitations
- No encryption or authentication; use in trusted LAN only
- No resume support
- IPv4 only
