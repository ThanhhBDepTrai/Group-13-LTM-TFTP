"""Quản lý file trong thư mục gốc TFTP: liệt kê, tạo thư mục, xóa, đổi tên, SHA-256.

Mọi đường dẫn đến từ người dùng đều đi qua ``tftp_config.safe_join`` nên không
thể thoát khỏi thư mục gốc (``..``, đường dẫn tuyệt đối, symlink ra ngoài).
Cuối file có ``ManagedHooks``: cầu nối giữa TFTP server và database/metrics/PXE.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

from tftp_config import safe_join
from tftp_packet import TFTPError
from tftp_server import ServerHooks, TransferRecord

__all__ = ["ManagedHooks", "StorageError", "StorageManager"]

log = logging.getLogger("tftp.storage")


class StorageError(Exception):
    """Lỗi thao tác file; ``kind`` giúp API chọn mã HTTP phù hợp."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind  # "not_found" | "exists" | "forbidden" | "invalid"
        self.message = message


def _is_partial(name: str) -> bool:
    """File tạm do server tạo khi đang nhận upload (``.name.xxxxxxxx.part``)."""
    return name.startswith(".") and name.endswith(".part")


class StorageManager:
    def __init__(self, root: str | Path, *, max_file_size: int | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()
        self.max_file_size = max_file_size
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #

    def resolve(self, relative: str, *, ignore_case: bool = False) -> Path:
        if relative in ("", ".", "/"):
            return self.root
        try:
            return safe_join(self.root, relative, ignore_case=ignore_case)
        except TFTPError:
            raise StorageError("forbidden", "Path is outside the storage root") from None

    def resolve_entry(self, relative: str) -> Path:
        """Như ``resolve`` nhưng KHÔNG đi theo symlink ở thành phần cuối.

        Dùng cho xóa/đổi tên: thao tác lên chính liên kết chứ không lên file đích.
        Thư mục cha vẫn phải nằm trong thư mục gốc.
        """
        if relative in ("", ".", "/"):
            return self.root
        text = relative.replace("\\", "/")
        parts = [p for p in text.split("/") if p not in ("", ".")]
        bad_drive = len(text) > 1 and text[1] == ":" and text[0].isalpha()
        if not parts or "\x00" in relative or ".." in parts or bad_drive:
            raise StorageError("forbidden", "Path is outside the storage root")
        return self.resolve("/".join(parts[:-1])) / parts[-1]

    def relative(self, path: Path) -> str:
        rel = path.resolve().relative_to(self.root)
        return "" if str(rel) == "." else rel.as_posix()

    def _info(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        return {
            "name": path.name,
            "path": self.relative(path),
            "is_dir": path.is_dir(),
            "size": 0 if path.is_dir() else stat.st_size,
            "modified": stat.st_mtime,
        }

    # ------------------------------------------------------------------ #
    # Thao tác
    # ------------------------------------------------------------------ #

    def stat(self, relative: str) -> dict[str, Any]:
        path = self.resolve(relative)
        if not path.exists():
            raise StorageError("not_found", "No such file or directory")
        return self._info(path)

    def list_dir(self, relative: str = "") -> list[dict[str, Any]]:
        path = self.resolve(relative)
        if not path.exists():
            raise StorageError("not_found", "No such directory")
        if not path.is_dir():
            raise StorageError("invalid", "Not a directory")
        entries = []
        for child in path.iterdir():
            if _is_partial(child.name):
                continue
            try:
                entries.append(self._info(child))
            except OSError:  # file biến mất giữa lúc liệt kê
                continue
        return sorted(entries, key=lambda e: (not e["is_dir"], e["name"].lower()))

    def mkdir(self, relative: str, *, parents: bool = False) -> dict[str, Any]:
        path = self.resolve(relative)
        if path == self.root:
            raise StorageError("invalid", "Invalid directory name")
        if path.exists():
            raise StorageError("exists", "Already exists")
        if not parents and not path.parent.is_dir():
            raise StorageError("not_found", "Parent directory does not exist")
        try:
            path.mkdir(parents=parents)
        except OSError as exc:
            raise StorageError("invalid", f"Cannot create directory: {exc.strerror}") from exc
        return self._info(path)

    def delete(self, relative: str, *, recursive: bool = False) -> None:
        path = self.resolve_entry(relative)
        if path == self.root:
            raise StorageError("forbidden", "Cannot delete the storage root")
        if not path.exists() and not path.is_symlink():
            raise StorageError("not_found", "No such file or directory")
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif recursive:
                shutil.rmtree(path)
            else:
                path.rmdir()
        except OSError as exc:
            kind = "invalid" if path.is_dir() else "forbidden"
            raise StorageError(kind, f"Cannot delete: {exc.strerror}") from exc

    def rename(self, source: str, destination: str, *, overwrite: bool = False) -> dict[str, Any]:
        src = self.resolve_entry(source)
        dst = self.resolve_entry(destination)
        if src == self.root or dst == self.root:
            raise StorageError("forbidden", "Cannot rename the storage root")
        if not src.exists() and not src.is_symlink():
            raise StorageError("not_found", "Source does not exist")
        if not dst.parent.is_dir():
            raise StorageError("not_found", "Destination directory does not exist")
        if src.is_dir() and (dst == src or src in dst.parents):
            raise StorageError("invalid", "Cannot move a directory into itself")
        with self._lock:
            if dst.exists() or dst.is_symlink():
                if not overwrite or dst.is_dir() or src.is_dir():
                    raise StorageError("exists", "Destination already exists")
            try:
                src.replace(dst)
            except OSError as exc:
                raise StorageError("invalid", f"Cannot rename: {exc.strerror}") from exc
        return self._info(dst)

    def sha256(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise StorageError("not_found", "No such file")
        return sha256_file(path)

    def usage(self) -> dict[str, int]:
        files = folders = total = 0
        for item in self.root.rglob("*"):
            if _is_partial(item.name):
                continue
            try:
                if item.is_dir():
                    folders += 1
                elif item.is_file():
                    files += 1
                    total += item.stat().st_size
            except OSError:
                continue
        free = shutil.disk_usage(self.root).free
        return {"files": files, "folders": folders, "bytes": total, "disk_free": free}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Cầu nối giữa TFTP server và các dịch vụ backend
# --------------------------------------------------------------------------- #


class ManagedHooks(ServerHooks):
    """Gắn StorageManager, Database, MetricsService và PXE vào TFTP server.

    * ``resolve``: đường dẫn an toàn qua StorageManager; với PXE bật thì không phân
      biệt hoa thường và có thể tìm boot file trong thư mục PXE.
    * ``authorize``: cấm ghi vào thư mục boot PXE (boot file chỉ đọc qua TFTP).
    * ``on_transfer``: tính SHA-256 (file không quá ``sha_max_bytes``), lưu lịch sử
      vào SQLite và cập nhật metrics. Lỗi ở đây không làm hỏng phiên truyền.
    """

    def __init__(
        self,
        storage: StorageManager,
        *,
        db: Any = None,
        metrics: Any = None,
        pxe: Any = None,
        sha_max_bytes: int = 256 * 1024 * 1024,
    ):
        self.storage = storage
        self.db = db
        self.metrics = metrics
        self.pxe = pxe
        self.sha_max_bytes = sha_max_bytes
        self._hash_cache: dict[tuple, str] = {}
        self._cache_lock = threading.Lock()

    def _pxe_on(self) -> bool:
        return bool(self.pxe and self.pxe.config.enabled)

    def authorize(self, op: str, filename: str, client: tuple) -> None:
        if op == "WRQ" and self._pxe_on():
            try:
                path = self.storage.resolve(filename, ignore_case=True)
            except StorageError:
                return  # để safe_join/resolve báo lỗi ở bước sau
            if self.pxe.is_boot_path(path):
                raise TFTPError(2, "PXE boot files are read-only over TFTP")

    def resolve(self, op: str, filename: str, client: tuple) -> Path:
        try:
            path = self.storage.resolve(filename, ignore_case=self._pxe_on())
        except StorageError as exc:
            raise TFTPError(2, "Access violation") from exc
        if op == "RRQ" and not path.exists() and self._pxe_on():
            alt = self.pxe.find_boot_file(filename)
            if alt is not None:
                return alt
        return path

    def _digest(self, path: Path) -> str:
        """SHA-256 có cache theo (đường dẫn, mtime, kích thước) để không băm lại mỗi lần tải."""
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        with self._cache_lock:
            cached = self._hash_cache.get(key)
        if cached:
            return cached
        digest = sha256_file(path)
        with self._cache_lock:
            if len(self._hash_cache) > 512:
                self._hash_cache.clear()
            self._hash_cache[key] = digest
        return digest

    def on_transfer(self, record: TransferRecord) -> None:
        path = Path(record.path) if record.path else None
        if path is not None and record.status == "ok" and path.is_file():
            if path.stat().st_size <= self.sha_max_bytes:
                try:
                    record.sha256 = self._digest(path)
                except OSError:
                    log.warning("Cannot hash %s", path)
        if self.db is not None:
            data = record.to_dict()
            try:  # không lộ đường dẫn tuyệt đối của server qua API
                data["path"] = self.storage.relative(path) if path else ""
            except ValueError:
                data["path"] = ""
            self.db.add_transfer(data)
        if self.metrics is not None:
            self.metrics.record_transfer(record)
