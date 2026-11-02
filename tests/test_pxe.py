"""Kiểm thử PXE profile: JSON, path safety, mặc định BIOS/UEFI."""

import json

import pytest

from pxe_config import PxeError, PxeManager, normalize_arch
from storage_manager import StorageManager


@pytest.fixture()
def pxe(tmp_path):
    storage = StorageManager(tmp_path / "root")
    storage.write_bytes("pxe/boot/undionly.kpxe", b"bios")
    storage.write_bytes("pxe/efi/ipxe.efi", b"uefi")
    manager = PxeManager(storage, tmp_path / "pxe_profiles.json")
    return manager, storage


def test_default_profile_created_and_reports_missing_files(tmp_path):
    storage = StorageManager(tmp_path / "root")
    manager = PxeManager(storage, tmp_path / "profiles.json")
    profiles = manager.list_profiles()
    assert len(profiles) == 1
    assert profiles[0]["default"] is True
    assert set(profiles[0]["missing_files"]) == {
        "pxe/boot/undionly.kpxe",
        "pxe/efi/ipxe.efi",
    }
    assert (tmp_path / "profiles.json").exists()


def test_upsert_select_and_persist(pxe):
    manager, storage = pxe
    profile = manager.upsert_profile(
        {
            "name": "lab-01",
            "description": "Lab",
            "bios_file": "pxe/boot/undionly.kpxe",
            "uefi_file": "pxe/efi/ipxe.efi",
            "default": True,
        },
        require_files=True,
    )
    assert profile["bios_exists"] is True
    assert profile["uefi_exists"] is True
    assert profile["default"] is True

    selected_bios = manager.select_profile("bios")
    assert selected_bios["name"] == "lab-01"
    assert selected_bios["boot_file"] == "pxe/boot/undionly.kpxe"
    assert selected_bios["boot_file_exists"] is True

    selected_uefi = manager.select_profile("uefi64")
    assert selected_uefi["arch"] == "uefi"
    assert selected_uefi["boot_file"] == "pxe/efi/ipxe.efi"

    reopened = PxeManager(storage, manager.path, auto_create=False)
    assert reopened.get_profile("lab-01")["bios_file"] == "pxe/boot/undionly.kpxe"


def test_only_one_default_profile(pxe):
    manager, _storage = pxe
    manager.upsert_profile(
        {"name": "second", "bios_file": "pxe/boot/undionly.kpxe", "default": True}
    )
    defaults = [profile for profile in manager.list_profiles() if profile["default"]]
    assert [profile["name"] for profile in defaults] == ["second"]


def test_require_files_and_unsafe_paths(pxe):
    manager, _storage = pxe
    with pytest.raises(PxeError) as exc:
        manager.upsert_profile(
            {"name": "missing", "bios_file": "pxe/boot/nope.kpxe"},
            require_files=True,
        )
    assert exc.value.status == 400

    with pytest.raises(PxeError) as exc:
        manager.upsert_profile({"name": "escape", "bios_file": "../secret"})
    assert exc.value.status == 400

    with pytest.raises(PxeError):
        manager.upsert_profile({"name": "empty"})
    with pytest.raises(PxeError):
        manager.upsert_profile({"name": "bad name", "bios_file": "pxe/boot/x"})


def test_delete_and_validate_all(pxe):
    manager, _storage = pxe
    manager.upsert_profile(
        {"name": "missing", "uefi_file": "pxe/efi/not-there.efi", "default": True}
    )
    issues = manager.validate_all()
    assert any(issue["name"] == "missing" for issue in issues)
    manager.delete_profile("missing")
    assert manager.get_profile("lab-default")["default"] is True
    with pytest.raises(PxeError) as exc:
        manager.delete_profile("missing")
    assert exc.value.status == 404


def test_normalize_arch():
    assert normalize_arch("Legacy") == "bios"
    assert normalize_arch("EFI") == "uefi"
    with pytest.raises(PxeError):
        normalize_arch("mips")


def test_repository_sample_file_is_valid():
    with open("pxe_profiles.json", encoding="utf-8") as file:
        data = json.load(file)
    assert data["version"] == 1
    assert data["profiles"]
    assert sum(1 for profile in data["profiles"] if profile["default"]) == 1
