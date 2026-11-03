"""Quản lý boot profile cho lab PXE.

TFTP Pro **không** chạy DHCP server. Module này chỉ quản lý tên boot file và
profile BIOS/UEFI; DHCP độc lập trong lab sẽ trả ``next-server`` và
``bootfile-name`` trỏ tới TFTP Server. Mọi đường dẫn boot file đều đi qua
:class:`StorageManager` để không thoát khỏi thư mục chia sẻ.
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from storage_manager import StorageError, StorageManager
from tftp_config import DEFAULT_PXE_PROFILES, PXE_BOOT_DIRS

_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ARCH_ALIASES = {
    "bios": "bios",
    "legacy": "bios",
    "x86": "bios",
    "uefi": "uefi",
    "efi": "uefi",
    "uefi64": "uefi",
    "x86_64": "uefi",
    "amd64": "uefi",
    "arm64": "uefi",
}


class PxeError(Exception):
    """Lỗi cấu hình PXE kèm HTTP status gợi ý."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class PxeProfile:
    name: str
    bios_file: str | None = None
    uefi_file: str | None = None
    description: str = ""
    default: bool = False

    def to_dict(self, storage: StorageManager | None = None) -> dict:
        data = {
            "name": self.name,
            "description": self.description,
            "bios_file": self.bios_file,
            "uefi_file": self.uefi_file,
            "default": self.default,
        }
        if storage is not None:
            missing = []
            for key in ("bios_file", "uefi_file"):
                relative = data[key]
                exists = bool(relative) and storage.exists(relative)
                data[f"{key.removesuffix('_file')}_exists"] = exists
                if relative and not exists:
                    missing.append(relative)
            data["missing_files"] = missing
        return data


def normalize_arch(arch: str | None) -> str:
    """Chuẩn hóa tên kiến trúc firmware về ``bios`` hoặc ``uefi``."""
    text = str(arch or "bios").strip().lower()
    result = _ARCH_ALIASES.get(text)
    if result is None:
        raise PxeError(
            "Kiến trúc phải là bios/legacy hoặc uefi/efi/uefi64", 400
        )
    return result


def _default_data() -> dict:
    return {
        "version": 1,
        "profiles": [
            {
                "name": "lab-default",
                "description": "Profile PXE mẫu cho lab BIOS/UEFI (iPXE).",
                "bios_file": "pxe/boot/undionly.kpxe",
                "uefi_file": "pxe/efi/ipxe.efi",
                "default": True,
            }
        ],
    }


class PxeManager:
    """Đọc/ghi ``pxe_profiles.json`` an toàn và kiểm tra boot file."""

    def __init__(
        self,
        storage: StorageManager,
        path: str | Path = DEFAULT_PXE_PROFILES,
        *,
        auto_create: bool = True,
    ):
        self.storage = storage
        self.path = Path(path).expanduser()
        self._lock = threading.RLock()
        self.storage.ensure_layout()
        if auto_create and not self.path.exists():
            self._write_data(_default_data())
        self._data = self._load_data()

    # ------------------------------------------------------------------ #
    # I/O
    # ------------------------------------------------------------------ #
    def _load_data(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"version": 1, "profiles": []}
        except (OSError, json.JSONDecodeError) as exc:
            raise PxeError(f"Không đọc được cấu hình PXE: {exc}", 500) from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raise PxeError("Cấu hình PXE phải có mảng 'profiles'", 400)
        return raw

    def _write_data(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with open(temp, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2, ensure_ascii=False)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp, self.path)
        except OSError as exc:
            raise PxeError(f"Không ghi được cấu hình PXE: {exc}", 500) from exc
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Chuẩn hóa profile
    # ------------------------------------------------------------------ #
    def _normalise(self, data: dict, *, require_files: bool) -> dict:
        if not isinstance(data, dict):
            raise PxeError("Profile phải là JSON object", 400)
        name = str(data.get("name", "")).strip()
        if not _PROFILE_NAME_RE.fullmatch(name):
            raise PxeError(
                "Tên profile chỉ gồm chữ, số, _ . - (1-64 ký tự)", 400
            )

        description = str(data.get("description", "")).strip()
        if len(description) > 500:
            raise PxeError("Mô tả profile tối đa 500 ký tự", 400)

        files: dict[str, str | None] = {}
        for key, label in (("bios_file", "BIOS"), ("uefi_file", "UEFI")):
            value = data.get(key)
            if value is None or str(value).strip() == "":
                files[key] = None
                continue
            try:
                relative = self.storage.clean_relative(str(value))
                path = self.storage.resolve(relative)
            except StorageError as exc:
                raise PxeError(
                    f"Đường dẫn {label} không hợp lệ: {exc.message}", exc.status
                ) from exc
            if not relative:
                files[key] = None
                continue
            if require_files and not path.is_file():
                raise PxeError(f"Boot file {label} không tồn tại: {relative}", 400)
            files[key] = relative

        if not files["bios_file"] and not files["uefi_file"]:
            raise PxeError("Profile phải có ít nhất bios_file hoặc uefi_file", 400)

        return {
            "name": name,
            "description": description,
            "bios_file": files["bios_file"],
            "uefi_file": files["uefi_file"],
            "default": bool(data.get("default", False)),
        }

    @staticmethod
    def _profile_from_dict(data: dict) -> PxeProfile:
        return PxeProfile(
            name=str(data["name"]),
            description=str(data.get("description", "")),
            bios_file=data.get("bios_file"),
            uefi_file=data.get("uefi_file"),
            default=bool(data.get("default", False)),
        )

    def _profiles(self) -> list[dict]:
        return list(self._data.get("profiles", []))

    def _set_profiles(self, profiles: list[dict]) -> None:
        data = dict(self._data)
        data["version"] = int(data.get("version", 1))
        data["profiles"] = profiles
        self._write_data(data)
        self._data = data

    @staticmethod
    def _ensure_one_default(profiles: list[dict]) -> list[dict]:
        if not profiles:
            return profiles
        defaults = [profile for profile in profiles if profile.get("default")]
        if not defaults:
            profiles[0]["default"] = True
        elif len(defaults) > 1:
            first = defaults[0]["name"]
            for profile in profiles:
                profile["default"] = profile["name"] == first
        return profiles

    # ------------------------------------------------------------------ #
    # API quản trị
    # ------------------------------------------------------------------ #
    def list_profiles(self) -> list[dict]:
        with self._lock:
            profiles = [
                self._profile_from_dict(item) for item in self._profiles()
            ]
        return [profile.to_dict(self.storage) for profile in profiles]

    def get_profile(self, name: str) -> dict:
        text = str(name).strip()
        with self._lock:
            for item in self._profiles():
                if item["name"] == text:
                    return self._profile_from_dict(item).to_dict(self.storage)
        raise PxeError("Không tìm thấy profile PXE", 404)

    def upsert_profile(self, data: dict, *, require_files: bool = False) -> dict:
        incoming = self._normalise(data, require_files=require_files)
        with self._lock:
            profiles = self._profiles()
            existing = next(
                (item for item in profiles if item["name"] == incoming["name"]),
                None,
            )
            if existing is not None and "default" not in data:
                incoming["default"] = bool(existing.get("default"))
            if incoming["default"]:
                for item in profiles:
                    item["default"] = item["name"] == incoming["name"]
            if existing is None:
                profiles.append(incoming)
            else:
                existing.update(incoming)
            self._set_profiles(self._ensure_one_default(profiles))
        return self.get_profile(incoming["name"])

    def delete_profile(self, name: str) -> None:
        text = str(name).strip()
        with self._lock:
            profiles = self._profiles()
            remaining = [item for item in profiles if item["name"] != text]
            if len(remaining) == len(profiles):
                raise PxeError("Không tìm thấy profile PXE", 404)
            self._set_profiles(self._ensure_one_default(remaining))

    def select_profile(self, arch: str = "bios", *, name: str | None = None) -> dict:
        arch_group = normalize_arch(arch)
        with self._lock:
            profiles = self._profiles()
            if name:
                item = next(
                    (profile for profile in profiles if profile["name"] == name),
                    None,
                )
                if item is None:
                    raise PxeError("Không tìm thấy profile PXE", 404)
            else:
                item = next(
                    (profile for profile in profiles if profile.get("default")),
                    profiles[0] if profiles else None,
                )
            if item is None:
                raise PxeError("Chưa cấu hình profile PXE nào", 404)

        profile = self._profile_from_dict(item)
        boot_file = (
            profile.uefi_file if arch_group == "uefi" else profile.bios_file
        )
        public = profile.to_dict(self.storage)
        public.update(
            {
                "arch": arch_group,
                "boot_file": boot_file,
                "boot_file_exists": bool(boot_file)
                and self.storage.exists(boot_file),
            }
        )
        return public

    def validate_all(self) -> list[dict]:
        """Trả danh sách vấn đề của toàn bộ profile (không ném lỗi)."""
        issues = []
        with self._lock:
            raw_profiles = self._profiles()
        for item in raw_profiles:
            try:
                profile = self._profile_from_dict(item)
            except (KeyError, TypeError, ValueError):
                issues.append({"name": item.get("name"), "error": "Profile hỏng"})
                continue
            for key in ("bios_file", "uefi_file"):
                relative = getattr(profile, key)
                if relative and not self.storage.exists(relative):
                    issues.append(
                        {
                            "name": profile.name,
                            "file": relative,
                            "error": f"{key} không tồn tại",
                        }
                    )
        return issues

    def ensure_directories(self) -> None:
        for relative in PXE_BOOT_DIRS:
            self.storage.mkdir(relative, parents=True, exist_ok=True)


__all__ = [
    "PxeError",
    "PxeManager",
    "PxeProfile",
    "normalize_arch",
]
