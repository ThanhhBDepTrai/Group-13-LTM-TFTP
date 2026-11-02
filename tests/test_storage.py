"""Kiểm thử StorageManager: path safety, CRUD, metadata và SHA-256."""

import os

import pytest

from storage_manager import StorageError, StorageManager


@pytest.fixture()
def storage(tmp_path):
    return StorageManager(tmp_path / "root")


def test_write_read_list_metadata_and_hash(storage):
    meta = storage.write_bytes("docs/a.txt", b"hello")
    assert meta.relative_path == "docs/a.txt"
    assert meta.size == 5
    assert meta.sha256
    assert storage.read_bytes("docs/a.txt") == b"hello"
    assert storage.sha256("docs/a.txt") == meta.sha256
    assert storage.metadata("docs/a.txt", include_hash=True).sha256 == meta.sha256

    entries = storage.list_dir("docs")
    assert [entry.name for entry in entries] == ["a.txt"]
    assert entries[0].size == 5


def test_list_dir_sorts_dirs_first(storage):
    storage.write_bytes("b.txt", b"b")
    storage.write_bytes("a.txt", b"a")
    storage.mkdir("folder")
    names = [entry.name for entry in storage.list_dir()]
    assert names == ["folder", "a.txt", "b.txt"]


def test_recursive_list_and_search(storage):
    storage.write_bytes("a/one.txt", b"1")
    storage.write_bytes("a/b/two.log", b"2")
    storage.write_bytes("top.txt", b"3")
    files = storage.list_files(recursive=True)
    assert {item.relative_path for item in files} == {
        "a/one.txt",
        "a/b/two.log",
        "top.txt",
    }
    assert [item.relative_path for item in storage.search("two")] == ["a/b/two.log"]
    assert storage.search("") == []


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.txt",
        "a/../../secret.txt",
        "/etc/passwd",
        "C:/Windows/system.ini",
        "C:\\Windows\\system.ini",
        "..\\secret.txt",
        "~/.ssh/id_rsa",
        "bad\x00name",
        "trailing.",
    ],
)
def test_rejects_unsafe_paths(storage, bad):
    with pytest.raises(StorageError):
        storage.resolve(bad)
    with pytest.raises(StorageError):
        storage.write_bytes(bad, b"x")


def test_path_is_normalized(storage):
    meta = storage.write_bytes("./a//b/c.txt", b"x")
    assert meta.relative_path == "a/b/c.txt"


def test_rename_and_delete(storage):
    storage.write_bytes("a.txt", b"a")
    renamed = storage.rename("a.txt", "sub/b.txt")
    assert renamed.relative_path == "sub/b.txt"
    assert storage.read_bytes("sub/b.txt") == b"a"
    with pytest.raises(StorageError) as exc:
        storage.rename("sub/b.txt", "sub/b.txt")
    assert exc.value.status in (400, 409)
    storage.delete("sub/b.txt")
    assert not storage.exists("sub/b.txt")


def test_delete_non_empty_directory_requires_recursive(storage):
    storage.write_bytes("d/a.txt", b"a")
    with pytest.raises(StorageError) as exc:
        storage.delete("d")
    assert exc.value.status == 409
    storage.delete("d", recursive=True)
    assert not storage.exists("d")


def test_mkdir_exist_ok(storage):
    storage.mkdir("a/b")
    assert storage.is_dir("a/b")
    with pytest.raises(StorageError):
        storage.mkdir("a/b")
    assert storage.mkdir("a/b", exist_ok=True).is_dir


def test_write_no_overwrite(storage):
    storage.write_bytes("a.txt", b"first")
    with pytest.raises(StorageError) as exc:
        storage.write_bytes("a.txt", b"second", overwrite=False)
    assert exc.value.status == 409
    assert storage.read_bytes("a.txt") == b"first"


def test_no_temp_file_left_after_write(storage):
    storage.write_bytes("a.txt", b"x")
    names = os.listdir(storage.root)
    assert names == ["a.txt"]


def test_verify_verified_and_mismatch(storage):
    meta = storage.write_bytes("a.txt", b"abc")
    good = storage.verify("a.txt", meta.sha256)
    assert good["status"] == "VERIFIED"
    bad = storage.verify("a.txt", "0" * 64)
    assert bad["status"] == "MISMATCH"
    with pytest.raises(StorageError):
        storage.verify("a.txt", "xyz")


def test_missing_paths_raise_404(storage):
    for action in (
        lambda: storage.read_bytes("nope"),
        lambda: storage.metadata("nope"),
        lambda: storage.list_dir("nope"),
        lambda: storage.delete("nope"),
    ):
        with pytest.raises(StorageError) as exc:
            action()
        assert exc.value.status == 404


def test_symlink_escape_rejected(storage, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = storage.root / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Không tạo được symlink trên môi trường này")
    with pytest.raises(StorageError):
        storage.read_bytes("link/secret.txt")


def test_internal_temp_files_hidden(storage):
    (storage.root / ".x.abc.part").write_bytes(b"tmp")
    storage.write_bytes("visible.txt", b"x")
    assert [entry.name for entry in storage.list_dir()] == ["visible.txt"]
