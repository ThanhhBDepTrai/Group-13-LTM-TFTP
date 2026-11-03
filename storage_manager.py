"""Quản lý vùng lưu trữ TFTP Pro.

Đây là nơi duy nhất được phép chuyển đổi đường dẫn tương đối của client thành
đường dẫn thật trên đĩa. Mọi thao tác quản trị (list/upload/download/delete/
rename/mkdir/search/hash) đều đi qua lớp này để bảo đảm:

* không thoát khỏi ``root`` bằng ``..``, đường dẫn tuyệt đối, ổ đĩa Windows;
* không thoát khỏi ``root`` qua symlink/junction (kiểm tra ``resolve()``);
* ghi file nguyên tử (temp + ``os.replace``), không để file dở dang.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_CHUNK = 1024 * 1024


class StorageError(Exception):
    """Lỗi storage kèm HTTP status gợi ý cho Admin API."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class FileMetadata:
    """Metadata thống nhất giữa Storage, Admin API và GUI."""

    name: str
    relative_path: str
    size: int
    modified_at: str
    is_dir: bool = False
    sha256: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "size": self.size,
            "modified_at": self.modified_at,
            "is_dir": self.is_dir,
            "sha256": self.sha256,
        }


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(
        timespec="seconds"
    )


class StorageManager:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Đường dẫn
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean_relative(relative: str | Path | None) -> str:
        if relative is None:
            return ""
        text = str(relative).strip().replace("\\", "/")
        if "\x00" in text:
            raise StorageError("Đường dẫn chứa ký tự không hợp lệ", 400)
        if text in ("", "."):
            return ""
        if text.startswith(("/", "~")):
            raise StorageError("Không cho phép đường dẫn tuyệt đối", 400)
        # "C:" hoặc "C:/..." trên Windows
        first = text.split("/", 1)[0]
        if ":" in first:
            raise StorageError("Không cho phép đường dẫn ổ đĩa", 400)

        parts: list[str] = []
        for part in text.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise StorageError("Path traversal không được phép", 400)
            if part.endswith((" ", ".")):
                # Windows tự bỏ trailing space/dot, dễ gây nhầm lẫn.
                raise StorageError("Tên file không hợp lệ", 400)
            parts.append(part)
        return "/".join(parts)

    def clean_relative(self, relative: str | Path | None) -> str:
        """Chuẩn hóa đường dẫn tương đối và từ chối path traversal."""
        return self._clean_relative(relative)

    def resolve(self, relative: str | Path | None, *, allow_root: bool = False) -> Path:
        rel = self._clean_relative(relative)
        if not rel and not allow_root:
            raise StorageError("Thiếu tên file/thư mục", 400)
        candidate = (self.root / Path(*rel.split("/"))).resolve(strict=False) if rel else self.root

        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise StorageError("Đường dẫn nằm ngoài thư mục chia sẻ", 400) from exc

        # Kiểm tra thêm lần nữa sau khi mọi thành phần đã tồn tại (chống symlink).
        if candidate.exists():
            real = candidate.resolve(strict=True)
            try:
                real.relative_to(self.root)
            except ValueError as exc:
                raise StorageError("Đường dẫn nằm ngoài thư mục chia sẻ", 400) from exc
        return candidate

    def relative_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise StorageError("Đường dẫn nằm ngoài thư mục chia sẻ", 400) from exc

    @staticmethod
    def _is_internal(name: str) -> bool:
        return name.startswith(".") and name.endswith(".part") or name.startswith(".tftp")

    # ------------------------------------------------------------------ #
    # Đọc / ghi
    # ------------------------------------------------------------------ #
    def exists(self, relative: str) -> bool:
        return self.resolve(relative, allow_root=True).exists()

    def is_file(self, relative: str) -> bool:
        return self.resolve(relative).is_file()

    def is_dir(self, relative: str) -> bool:
        return self.resolve(relative, allow_root=True).is_dir()

    def read_bytes(self, relative: str) -> bytes:
        path = self.resolve(relative)
        if not path.is_file():
            raise StorageError("File không tồn tại", 404)
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StorageError(f"Không đọc được file: {exc}", 500) from exc

    def write_bytes(
        self,
        relative: str,
        data: bytes,
        *,
        overwrite: bool = True,
    ) -> FileMetadata:
        path = self.resolve(relative)
        if path.exists() and not overwrite:
            raise StorageError("File đã tồn tại", 409)
        if path.is_dir():
            raise StorageError("Đường dẫn là thư mục", 409)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp_path, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        except OSError as exc:
            raise StorageError(f"Không ghi được file: {exc}", 500) from exc
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return self.metadata(relative, include_hash=True)

    def open_read(self, relative: str):
        path = self.resolve(relative)
        if not path.is_file():
            raise StorageError("File không tồn tại", 404)
        return open(path, "rb")

    # ------------------------------------------------------------------ #
    # Metadata / hash
    # ------------------------------------------------------------------ #
    def sha256(self, relative: str) -> str:
        path = self.resolve(relative)
        if not path.is_file():
            raise StorageError("File không tồn tại", 404)
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as file:
                for chunk in iter(lambda: file.read(_CHUNK), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise StorageError(f"Không tính được SHA-256: {exc}", 500) from exc
        return digest.hexdigest()

    def metadata(self, relative: str, *, include_hash: bool = False) -> FileMetadata:
        path = self.resolve(relative, allow_root=True)
        if not path.exists():
            raise StorageError("Không tìm thấy file/thư mục", 404)
        stat = path.stat()
        rel = self.relative_path(path)
        return FileMetadata(
            name=path.name if path != self.root else "",
            relative_path=rel,
            size=0 if path.is_dir() else stat.st_size,
            modified_at=_iso_timestamp(stat.st_mtime),
            is_dir=path.is_dir(),
            sha256=self.sha256(rel) if include_hash and path.is_file() else None,
        )

    def verify(self, relative: str, expected_sha256: str) -> dict:
        expected = str(expected_sha256).strip().lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise StorageError("SHA-256 không hợp lệ", 400)
        actual = self.sha256(relative)
        return {
            "status": "VERIFIED" if actual == expected else "MISMATCH",
            "expected": expected,
            "actual": actual,
            "relative_path": self.clean_relative(relative),
        }

    # ------------------------------------------------------------------ #
    # Liệt kê / tìm kiếm
    # ------------------------------------------------------------------ #
    def list_dir(self, relative: str = "") -> list[FileMetadata]:
        path = self.resolve(relative, allow_root=True)
        if not path.exists():
            raise StorageError("Thư mục không tồn tại", 404)
        if not path.is_dir():
            raise StorageError("Đường dẫn không phải thư mục", 400)

        entries: list[FileMetadata] = []
        try:
            for child in path.iterdir():
                if self._is_internal(child.name):
                    continue
                stat = child.stat()
                entries.append(
                    FileMetadata(
                        name=child.name,
                        relative_path=self.relative_path(child),
                        size=0 if child.is_dir() else stat.st_size,
                        modified_at=_iso_timestamp(stat.st_mtime),
                        is_dir=child.is_dir(),
                    )
                )
        except OSError as exc:
            raise StorageError(f"Không đọc được thư mục: {exc}", 500) from exc
        entries.sort(key=lambda item: (not item.is_dir, item.name.lower()))
        return entries

    def list_files(self, relative: str = "", *, recursive: bool = False) -> list[FileMetadata]:
        base = self.resolve(relative, allow_root=True)
        if not base.is_dir():
            raise StorageError("Đường dẫn không phải thư mục", 400)
        iterator = base.rglob("*") if recursive else base.iterdir()
        files: list[FileMetadata] = []
        try:
            for child in iterator:
                if not child.is_file() or self._is_internal(child.name):
                    continue
                stat = child.stat()
                files.append(
                    FileMetadata(
                        name=child.name,
                        relative_path=self.relative_path(child),
                        size=stat.st_size,
                        modified_at=_iso_timestamp(stat.st_mtime),
                    )
                )
        except OSError as exc:
            raise StorageError(f"Không đọc được thư mục: {exc}", 500) from exc
        files.sort(key=lambda item: item.relative_path.lower())
        return files

    def search(self, query: str, *, limit: int = 200) -> list[FileMetadata]:
        needle = str(query).strip().lower()
        if not needle:
            return []
        matches = [
            item
            for item in self.list_files(recursive=True)
            if needle in item.relative_path.lower()
        ]
        return matches[: max(1, limit)]

    # ------------------------------------------------------------------ #
    # Thao tác quản trị
    # ------------------------------------------------------------------ #
    def delete(self, relative: str, *, recursive: bool = False) -> None:
        path = self.resolve(relative)
        if not path.exists():
            raise StorageError("Không tìm thấy file/thư mục", 404)
        try:
            if path.is_dir():
                if recursive:
                    shutil.rmtree(path)
                else:
                    path.rmdir()  # lỗi nếu thư mục còn file
            else:
                path.unlink()
        except OSError as exc:
            raise StorageError(f"Không xóa được: {exc}", 409) from exc

    def rename(self, old: str, new: str, *, overwrite: bool = False) -> FileMetadata:
        source = self.resolve(old)
        target = self.resolve(new)
        if not source.exists():
            raise StorageError("Không tìm thấy file/thư mục nguồn", 404)
        if source == self.root or target == self.root:
            raise StorageError("Không thể đổi tên thư mục gốc", 400)
        if target.exists() and not overwrite:
            raise StorageError("Đích đã tồn tại", 409)
        if target.exists() and overwrite:
            if target.is_dir():
                raise StorageError("Không ghi đè thư mục", 409)
            target.unlink()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(source, target)
        except OSError as exc:
            raise StorageError(f"Không đổi tên được: {exc}", 409) from exc
        return self.metadata(self.relative_path(target))

    def mkdir(self, relative: str, *, parents: bool = True, exist_ok: bool = False) -> FileMetadata:
        path = self.resolve(relative)
        if path.exists():
            if exist_ok and path.is_dir():
                return self.metadata(self.relative_path(path))
            raise StorageError("Thư mục đã tồn tại", 409)
        try:
            path.mkdir(parents=parents, exist_ok=exist_ok)
        except OSError as exc:
            raise StorageError(f"Không tạo được thư mục: {exc}", 409) from exc
        return self.metadata(self.relative_path(path))

    def ensure_layout(self) -> None:
        """Tạo các thư mục mẫu cho PXE (không tạo file boot giả)."""
        for relative in ("pxe/boot", "pxe/efi", "pxe/config"):
            self.mkdir(relative, parents=True, exist_ok=True)


__all__ = ["FileMetadata", "StorageError", "StorageManager"]
