"""Test StorageManager (File Manager) và PXEManager."""

import hashlib
import json
import os

import pytest

from pxe_config import ARCH_BY_CODE, PXEError, PXEManager
from storage_manager import StorageError, StorageManager


def make_storage(tmp_path):
    return StorageManager(tmp_path / "root")


def kind_of(callable_, *args, **kwargs):
    with pytest.raises(StorageError) as exc:
        callable_(*args, **kwargs)
    return exc.value.kind


# --------------------------------------------------------------------------- #
# Liệt kê
# --------------------------------------------------------------------------- #


def test_list_dir_sorts_folders_first_and_reports_sizes(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "b.txt").write_bytes(b"12345")
    (storage.root / "a.txt").write_bytes(b"")
    (storage.root / "zdir").mkdir()
    entries = storage.list_dir("")
    assert [e["name"] for e in entries] == ["zdir", "a.txt", "b.txt"]
    assert entries[0]["is_dir"] and entries[2]["size"] == 5
    assert entries[2]["path"] == "b.txt"


def test_list_dir_hides_partial_upload_files(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / ".upload.bin.1a2b3c4d.part").write_bytes(b"x")
    (storage.root / "real.bin").write_bytes(b"x")
    assert [e["name"] for e in storage.list_dir("")] == ["real.bin"]


def test_list_dir_errors(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "f.txt").write_text("x")
    assert kind_of(storage.list_dir, "missing") == "not_found"
    assert kind_of(storage.list_dir, "f.txt") == "invalid"


def test_stat(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "f.txt").write_bytes(b"abc")
    assert storage.stat("f.txt")["size"] == 3
    assert kind_of(storage.stat, "nope") == "not_found"


# --------------------------------------------------------------------------- #
# mkdir / delete / rename
# --------------------------------------------------------------------------- #


def test_mkdir_and_conflicts(tmp_path):
    storage = make_storage(tmp_path)
    assert storage.mkdir("a")["is_dir"]
    assert kind_of(storage.mkdir, "a") == "exists"
    assert kind_of(storage.mkdir, "x/y") == "not_found"
    assert storage.mkdir("x/y", parents=True)["path"] == "x/y"
    assert kind_of(storage.mkdir, "") == "invalid"


def test_delete_file_empty_dir_and_nonempty_dir(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "f.txt").write_text("x")
    storage.delete("f.txt")
    assert not (storage.root / "f.txt").exists()

    storage.mkdir("empty")
    storage.delete("empty")
    assert not (storage.root / "empty").exists()

    storage.mkdir("full")
    (storage.root / "full" / "a.txt").write_text("x")
    assert kind_of(storage.delete, "full") == "invalid"  # chưa rỗng, không recursive
    storage.delete("full", recursive=True)
    assert not (storage.root / "full").exists()


def test_delete_errors(tmp_path):
    storage = make_storage(tmp_path)
    assert kind_of(storage.delete, "nope") == "not_found"
    assert kind_of(storage.delete, "") == "forbidden"
    assert kind_of(storage.delete, "/") == "forbidden"


def test_delete_symlink_removes_link_not_target(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "real.txt").write_text("keep")
    try:
        os.symlink(storage.root / "real.txt", storage.root / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink không khả dụng")
    storage.delete("link.txt")
    assert (storage.root / "real.txt").exists()
    assert not (storage.root / "link.txt").is_symlink()


def test_rename_file_and_directory(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "a.txt").write_text("hello")
    info = storage.rename("a.txt", "b.txt")
    assert info["path"] == "b.txt" and (storage.root / "b.txt").read_text() == "hello"
    assert not (storage.root / "a.txt").exists()

    storage.mkdir("d1")
    storage.mkdir("d2")
    storage.rename("d1", "d2/inner")
    assert (storage.root / "d2" / "inner").is_dir()


def test_rename_conflicts_and_overwrite(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "a.txt").write_text("A")
    (storage.root / "b.txt").write_text("B")
    assert kind_of(storage.rename, "a.txt", "b.txt") == "exists"
    storage.rename("a.txt", "b.txt", overwrite=True)
    assert (storage.root / "b.txt").read_text() == "A"


def test_rename_never_overwrites_directories(tmp_path):
    storage = make_storage(tmp_path)
    storage.mkdir("d1")
    storage.mkdir("d2")
    assert kind_of(storage.rename, "d1", "d2", overwrite=True) == "exists"


def test_rename_errors(tmp_path):
    storage = make_storage(tmp_path)
    storage.mkdir("d")
    (storage.root / "f.txt").write_text("x")
    assert kind_of(storage.rename, "nope", "x") == "not_found"
    assert kind_of(storage.rename, "f.txt", "missing/dir/x") == "not_found"
    assert kind_of(storage.rename, "d", "d/sub") == "invalid"  # không chuyển thư mục vào chính nó
    assert kind_of(storage.rename, "", "x") == "forbidden"
    assert kind_of(storage.rename, "f.txt", "") == "forbidden"


# --------------------------------------------------------------------------- #
# Bảo mật đường dẫn cho mọi thao tác
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["../x", "a/../../x", "..\\x", "C:\\x", "x\x00y"])
def test_every_operation_rejects_escape_paths(tmp_path, bad):
    storage = make_storage(tmp_path)
    (tmp_path / "x").write_text("outside")
    for operation in (
        lambda: storage.list_dir(bad),
        lambda: storage.stat(bad),
        lambda: storage.mkdir(bad),
        lambda: storage.delete(bad),
        lambda: storage.rename(bad, "ok"),
        lambda: storage.rename("ok", bad),
        lambda: storage.sha256(bad),
    ):
        assert kind_of(operation) == "forbidden"
    assert (tmp_path / "x").read_text() == "outside"


def test_symlink_pointing_outside_is_forbidden(tmp_path):
    storage = make_storage(tmp_path)
    (tmp_path / "secret").mkdir()
    (tmp_path / "secret" / "s.txt").write_text("secret")
    try:
        os.symlink(tmp_path / "secret", storage.root / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlink không khả dụng")
    assert kind_of(storage.list_dir, "link") == "forbidden"
    assert kind_of(storage.sha256, "link/s.txt") == "forbidden"


# --------------------------------------------------------------------------- #
# SHA-256 và thống kê dung lượng
# --------------------------------------------------------------------------- #


def test_sha256_matches_hashlib_for_large_file(tmp_path):
    storage = make_storage(tmp_path)
    data = os.urandom(3 * 1024 * 1024 + 17)  # nhiều hơn một khối đọc 1 MiB
    (storage.root / "big.bin").write_bytes(data)
    assert storage.sha256("big.bin") == hashlib.sha256(data).hexdigest()


def test_sha256_known_vector_and_errors(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "abc").write_bytes(b"abc")
    assert storage.sha256("abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    storage.mkdir("d")
    assert kind_of(storage.sha256, "d") == "not_found"
    assert kind_of(storage.sha256, "nope") == "not_found"


def test_usage_counts_files_folders_bytes(tmp_path):
    storage = make_storage(tmp_path)
    storage.mkdir("d")
    (storage.root / "a").write_bytes(b"12345")
    (storage.root / "d" / "b").write_bytes(b"123")
    (storage.root / ".x.1.part").write_bytes(b"ignored")
    usage = storage.usage()
    assert (usage["files"], usage["folders"], usage["bytes"]) == (2, 1, 8)
    assert usage["disk_free"] > 0


# --------------------------------------------------------------------------- #
# PXE
# --------------------------------------------------------------------------- #


def make_pxe(tmp_path, with_config=False):
    storage = make_storage(tmp_path)
    path = tmp_path / "pxe.json" if with_config else None
    return storage, PXEManager(storage, path)


def populate_boot_files(storage, names=("pxelinux.0", "bootia32.efi", "grubx64.efi", "grubaa64.efi")):
    (storage.root / "pxe").mkdir(exist_ok=True)
    for name in names:
        (storage.root / "pxe" / name).write_bytes(b"boot")


def test_pxe_defaults_and_validation_problems(tmp_path):
    _storage, pxe = make_pxe(tmp_path)
    assert pxe.config.enabled is False
    problems = pxe.validate()
    assert any("does not exist" in p for p in problems)
    assert any("next_server" in p for p in problems)


def test_pxe_update_creates_boot_dir_and_validates_clean(tmp_path):
    storage, pxe = make_pxe(tmp_path)
    pxe.update(enabled=True, next_server="192.168.1.10")
    assert (storage.root / "pxe").is_dir()
    populate_boot_files(storage)
    assert pxe.validate() == []


def test_pxe_config_persists_across_instances(tmp_path):
    storage, pxe = make_pxe(tmp_path, with_config=True)
    pxe.update(enabled=True, boot_dir="boot/net", next_server="10.0.0.5",
               boot_files={"bios": "lpxelinux.0"})
    again = PXEManager(storage, tmp_path / "pxe.json")
    assert again.config.enabled is True
    assert again.config.boot_dir == "boot/net"
    assert again.config.boot_files["bios"] == "lpxelinux.0"
    assert again.config.boot_files["efi64"] == "grubx64.efi"  # giữ giá trị mặc định khác
    assert json.loads((tmp_path / "pxe.json").read_text())["next_server"] == "10.0.0.5"


@pytest.mark.parametrize(
    "changes",
    [
        {"boot_dir": ""},  # chính là thư mục gốc
        {"boot_dir": "../outside"},
        {"boot_files": {"bios": "../../etc/passwd"}},
        {"boot_files": {"sparc": "x"}},
        {"next_server": "not-an-ip"},
    ],
)
def test_pxe_rejects_invalid_updates_and_keeps_old_config(tmp_path, changes):
    _, pxe = make_pxe(tmp_path)
    before = pxe.config.to_dict()
    with pytest.raises(PXEError):
        pxe.update(**changes)
    assert pxe.config.to_dict() == before


def test_pxe_normalizes_boot_file_and_ip(tmp_path):
    _, pxe = make_pxe(tmp_path)
    pxe.update(boot_files={"bios": "\\sub\\pxelinux.0"}, next_server=" 192.168.1.5 ")
    assert pxe.config.boot_files["bios"] == "sub/pxelinux.0"
    assert pxe.config.next_server == "192.168.1.5"


def test_pxe_list_boot_files_and_lookup_is_case_insensitive(tmp_path):
    storage, pxe = make_pxe(tmp_path)
    populate_boot_files(storage, ("pxelinux.0", "grubx64.efi"))
    (storage.root / "pxe" / ".hidden").write_bytes(b"x")
    names = [f["name"] for f in pxe.list_boot_files()]
    assert names == ["grubx64.efi", "pxelinux.0"]
    found = pxe.find_boot_file("PXELINUX.0")
    assert found is not None and found.name == "pxelinux.0"
    assert pxe.find_boot_file("missing.0") is None
    assert pxe.find_boot_file("../x") is None


def test_pxe_is_boot_path(tmp_path):
    storage, pxe = make_pxe(tmp_path)
    populate_boot_files(storage, ("pxelinux.0",))
    (storage.root / "other.txt").write_text("x")
    assert pxe.is_boot_path(storage.root / "pxe" / "pxelinux.0")
    assert not pxe.is_boot_path(storage.root / "other.txt")


def test_pxe_bootfile_for_arch_codes_and_names(tmp_path):
    _, pxe = make_pxe(tmp_path)
    assert pxe.bootfile_for_arch(0) == "pxe/pxelinux.0"
    assert pxe.bootfile_for_arch(7) == pxe.bootfile_for_arch(9) == "pxe/grubx64.efi"
    assert pxe.bootfile_for_arch("arm64") == "pxe/grubaa64.efi"
    assert pxe.bootfile_for_arch(99) is None
    assert set(ARCH_BY_CODE.values()) == {"bios", "efi32", "efi64", "arm64"}


def test_pxe_dhcp_snippets(tmp_path):
    _, pxe = make_pxe(tmp_path)
    pxe.update(next_server="192.168.1.5")
    dnsmasq = pxe.dhcp_snippet("dnsmasq")
    assert "dhcp-match=set:bios,option:client-arch,0" in dnsmasq
    assert "dhcp-boot=tag:efi64,pxe/grubx64.efi,,192.168.1.5" in dnsmasq
    isc = pxe.dhcp_snippet("isc")
    assert "next-server 192.168.1.5;" in isc
    assert 'if option arch = 00:00 { filename "pxe/pxelinux.0"; }' in isc
    assert 'option arch = 00:0b { filename "pxe/grubaa64.efi"; }' in isc
    with pytest.raises(PXEError):
        pxe.dhcp_snippet("bind9")


def test_pxe_snippet_without_server_uses_placeholder(tmp_path):
    _, pxe = make_pxe(tmp_path)
    assert "<TFTP-SERVER-IP>" in pxe.dhcp_snippet()


def test_pxe_corrupt_config_file_raises_clear_error(tmp_path):
    storage = make_storage(tmp_path)
    (tmp_path / "pxe.json").write_text("{not json")
    with pytest.raises(PXEError):
        PXEManager(storage, tmp_path / "pxe.json")


def test_rename_symlink_moves_the_link_not_the_target(tmp_path):
    storage = make_storage(tmp_path)
    (storage.root / "real.txt").write_text("keep")
    try:
        os.symlink(storage.root / "real.txt", storage.root / "link.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlink không khả dụng")
    storage.rename("link.txt", "renamed.txt")
    assert (storage.root / "real.txt").read_text() == "keep"
    assert (storage.root / "renamed.txt").is_symlink()
    assert not (storage.root / "link.txt").is_symlink()


def test_deleting_a_link_that_points_outside_is_allowed_and_safe(tmp_path):
    storage = make_storage(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("precious")
    try:
        os.symlink(outside, storage.root / "bad-link")
    except (OSError, NotImplementedError):
        pytest.skip("symlink không khả dụng")
    storage.delete("bad-link")
    assert outside.read_text() == "precious"
    assert not (storage.root / "bad-link").is_symlink()
