"""Cấu hình và quản lý boot file cho PXE (boot qua mạng bằng TFTP).

Lưu ý thực tế: firmware PXE chỉ nói chuyện qua UDP cổng 69 và không đổi được cổng,
vì vậy khi dùng thật cần chạy TFTP server với ``--port 69`` (quyền admin/root).
"""

from __future__ import annotations

import ipaddress
import json
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from storage_manager import StorageError, StorageManager

__all__ = ["ARCH_BY_CODE", "DEFAULT_BOOT_FILES", "PXEConfig", "PXEError", "PXEManager"]

# Giá trị DHCP option 93 (Client System Architecture, RFC 4578)
ARCH_BY_CODE = {0: "bios", 6: "efi32", 7: "efi64", 9: "efi64", 11: "arm64"}
DHCP_CODES = {"bios": 0, "efi32": 6, "efi64": 7, "arm64": 11}
DEFAULT_BOOT_FILES = {
    "bios": "pxelinux.0",
    "efi32": "bootia32.efi",
    "efi64": "grubx64.efi",
    "arm64": "grubaa64.efi",
}


class PXEError(ValueError):
    """Cấu hình PXE không hợp lệ."""


@dataclass
class PXEConfig:
    enabled: bool = False
    boot_dir: str = "pxe"
    boot_files: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_BOOT_FILES))
    next_server: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class PXEManager:
    def __init__(self, storage: StorageManager, config_path: str | Path | None = None):
        self.storage = storage
        self.config_path = Path(config_path) if config_path else None
        self.config = PXEConfig()
        self._lock = threading.RLock()
        if self.config_path and self.config_path.exists():
            self._load()

    # ------------------------------------------------------------------ #
    # Lưu / nạp
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        assert self.config_path is not None
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            self.config = self._validated(PXEConfig(**data))
        except (OSError, ValueError, TypeError) as exc:
            raise PXEError(f"Cannot load PXE config: {exc}") from exc

    def _save(self) -> None:
        if self.config_path is None:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.config.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.config_path)

    # ------------------------------------------------------------------ #
    # Kiểm tra / cập nhật
    # ------------------------------------------------------------------ #

    def _validated(self, config: PXEConfig) -> PXEConfig:
        config.enabled = bool(config.enabled)
        try:
            boot_dir = self.storage.resolve(config.boot_dir)
        except StorageError as exc:
            raise PXEError("boot_dir must stay inside the storage root") from exc
        if boot_dir == self.storage.root:
            raise PXEError("boot_dir must be a sub-directory, not the storage root")
        config.boot_dir = self.storage.relative(boot_dir)

        files: dict[str, str] = {}
        for arch, name in config.boot_files.items():
            if arch not in DHCP_CODES:
                raise PXEError(f"Unknown architecture: {arch}")
            clean = str(name).strip().replace("\\", "/").lstrip("/")
            if not clean:
                continue
            try:
                self.storage.resolve(f"{config.boot_dir}/{clean}")
            except StorageError as exc:
                raise PXEError(f"Boot file outside boot_dir: {name}") from exc
            files[arch] = clean
        config.boot_files = files

        if config.next_server:
            try:
                config.next_server = str(ipaddress.ip_address(config.next_server.strip()))
            except ValueError as exc:
                raise PXEError("next_server must be an IP address") from exc
        return config

    def update(
        self,
        *,
        enabled: bool | None = None,
        boot_dir: str | None = None,
        boot_files: dict[str, str] | None = None,
        next_server: str | None = None,
    ) -> PXEConfig:
        with self._lock:
            draft = PXEConfig(**self.config.to_dict())
            if enabled is not None:
                draft.enabled = enabled
            if boot_dir is not None:
                draft.boot_dir = boot_dir
            if boot_files is not None:
                draft.boot_files = {**draft.boot_files, **boot_files}
            if next_server is not None:
                draft.next_server = next_server
            self.config = self._validated(draft)
            if self.config.enabled:
                self.boot_dir_path().mkdir(parents=True, exist_ok=True)
            self._save()
            return self.config

    # ------------------------------------------------------------------ #
    # Truy vấn
    # ------------------------------------------------------------------ #

    def boot_dir_path(self) -> Path:
        return self.storage.resolve(self.config.boot_dir)

    def is_boot_path(self, path: Path) -> bool:
        base = self.boot_dir_path()
        resolved = path.resolve()
        return resolved == base or base in resolved.parents

    def find_boot_file(self, name: str) -> Path | None:
        """Tìm ``name`` trong thư mục boot (client PXE thường chỉ xin tên file trần)."""
        try:
            candidate = self.storage.resolve(
                f"{self.config.boot_dir}/{name.replace(chr(92), '/').lstrip('/')}", ignore_case=True
            )
        except StorageError:
            return None
        return candidate if candidate.is_file() else None

    def list_boot_files(self) -> list[dict]:
        base = self.boot_dir_path()
        if not base.is_dir():
            return []
        files = []
        for item in sorted(base.rglob("*")):
            if item.is_file() and not item.name.startswith("."):
                files.append({
                    "path": self.storage.relative(item),
                    "name": item.relative_to(base).as_posix(),
                    "size": item.stat().st_size,
                })
        return files

    def bootfile_for_arch(self, arch: int | str) -> str | None:
        """Đường dẫn (tính từ gốc TFTP) cần đưa vào DHCP cho kiến trúc ``arch``."""
        key = ARCH_BY_CODE.get(arch) if isinstance(arch, int) else arch
        name = self.config.boot_files.get(key or "")
        return f"{self.config.boot_dir}/{name}" if name else None

    def validate(self) -> list[str]:
        """Danh sách vấn đề cần sửa; rỗng nghĩa là cấu hình dùng được."""
        problems: list[str] = []
        if not self.boot_dir_path().is_dir():
            problems.append(f"Boot directory '{self.config.boot_dir}' does not exist")
        for arch, name in sorted(self.config.boot_files.items()):
            if self.find_boot_file(name) is None:
                problems.append(f"Boot file for {arch} is missing: {self.config.boot_dir}/{name}")
        if not self.config.next_server:
            problems.append("next_server is not set (needed for DHCP snippets)")
        return problems

    def dhcp_snippet(self, fmt: str = "dnsmasq") -> str:
        server = self.config.next_server or "<TFTP-SERVER-IP>"
        entries = [
            (arch, DHCP_CODES[arch], f"{self.config.boot_dir}/{name}")
            for arch, name in sorted(self.config.boot_files.items(), key=lambda kv: DHCP_CODES[kv[0]])
        ]
        note = "# PXE chi dung UDP cong 69: chay TFTP server voi --port 69"
        if fmt == "dnsmasq":
            lines = [note]
            for arch, code, _ in entries:
                lines.append(f"dhcp-match=set:{arch},option:client-arch,{code}")
            for arch, _, path in entries:
                lines.append(f"dhcp-boot=tag:{arch},{path},,{server}")
            return "\n".join(lines) + "\n"
        if fmt == "isc":
            lines = [note, "option arch code 93 = unsigned integer 16;", f"next-server {server};"]
            for index, (_, code, path) in enumerate(entries):
                keyword = "if" if index == 0 else "elsif"
                lines.append(f'{keyword} option arch = 00:{code:02x} {{ filename "{path}"; }}')
            return "\n".join(lines) + "\n"
        raise PXEError("fmt must be 'dnsmasq' or 'isc'")
