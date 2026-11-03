"""Test cấu hình tập trung và các giá trị mặc định đã thống nhất."""

from pathlib import Path

import pytest

from tftp_client import TFTPClient
from tftp_config import (
    DEFAULT_ADMIN_HOST,
    DEFAULT_ADMIN_PORT,
    DEFAULT_BLKSIZE,
    DEFAULT_CLIENT_HOST,
    DEFAULT_HOST,
    DEFAULT_MAX_WINDOWSIZE,
    DEFAULT_PORT,
    DEFAULT_ROOT,
    DEFAULT_WINDOWSIZE,
    DEFAULTS,
)
from tftp_server import ServerConfig


def test_server_and_client_share_default_port():
    assert DEFAULT_PORT == 6969
    assert ServerConfig().port == DEFAULT_PORT
    assert TFTPClient("127.0.0.1").port == DEFAULT_PORT


def test_server_defaults_come_from_central_config():
    cfg = ServerConfig()
    assert cfg.host == DEFAULT_HOST
    assert cfg.root == Path(DEFAULT_ROOT)
    assert cfg.blksize == DEFAULT_BLKSIZE
    assert cfg.windowsize == DEFAULT_MAX_WINDOWSIZE


def test_client_default_host_and_window():
    client = TFTPClient(DEFAULT_CLIENT_HOST)
    assert client.windowsize == DEFAULT_WINDOWSIZE == 1


def test_admin_api_defaults_are_localhost_only():
    assert DEFAULT_ADMIN_HOST == "127.0.0.1"
    assert DEFAULT_ADMIN_PORT != DEFAULT_PORT
    assert DEFAULT_ADMIN_PORT != DEFAULT_PORT + 1  # không đụng listing TCP


def test_defaults_dataclass_matches_constants():
    assert DEFAULTS.port == DEFAULT_PORT
    assert DEFAULTS.windowsize == DEFAULT_WINDOWSIZE
    assert DEFAULTS.max_windowsize == DEFAULT_MAX_WINDOWSIZE


def test_gui_fallback_config_uses_central_defaults(monkeypatch, tmp_path):
    # Không tạo Tk root; chỉ kiểm tra hàm đọc config khi file không tồn tại.
    pytest.importorskip("tkinter")
    import tftp_gui

    monkeypatch.setattr(tftp_gui, "CONFIG_PATH", tmp_path / "missing.json")
    cfg = tftp_gui.load_config()
    assert cfg["host"] == DEFAULT_CLIENT_HOST
    assert cfg["port"] == DEFAULT_PORT
    assert cfg["windowsize"] == DEFAULT_WINDOWSIZE
