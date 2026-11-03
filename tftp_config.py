"""Cấu hình cho TFTP server/client và hàm ``safe_join`` dùng chung.

Cấu hình đọc theo thứ tự ưu tiên: giá trị mặc định < file JSON < biến môi
trường ``TFTP_*`` < tham số truyền trực tiếp (CLI).
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from tftp_packet import (
    BLOCK_SIZE,
    MAX_BLKSIZE,
    MAX_TIMEOUT,
    MAX_WINDOWSIZE,
    MIN_BLKSIZE,
    MIN_TIMEOUT,
    MIN_WINDOWSIZE,
    TFTPError,
)

__all__ = ["ClientConfig", "ConfigError", "ServerConfig", "safe_join"]

PXE_MAX_BLKSIZE = 1456  # an toàn cho ROM PXE trên Ethernet MTU 1500

T = TypeVar("T")


class ConfigError(ValueError):
    """Cấu hình không hợp lệ."""


# --------------------------------------------------------------------------- #
# Đường dẫn an toàn
# --------------------------------------------------------------------------- #


def safe_join(root: str | os.PathLike[str], name: str, *, ignore_case: bool = False) -> Path:
    """Ghép ``name`` vào ``root`` và đảm bảo kết quả nằm trong ``root``.

    Chặn: byte NUL, ``..``, đường dẫn tuyệt đối kiểu Windows (``C:\\x``), và
    symlink trỏ ra ngoài. Dấu ``/`` hoặc ``\\`` ở đầu bị bỏ (client PXE hay gửi
    ``/boot/x``). Raise ``TFTPError(2)`` nếu vi phạm.
    """
    if not name or "\x00" in name:
        raise TFTPError(2, "Access violation")
    text = name.replace("\\", "/")
    if len(text) > 1 and text[1] == ":" and text[0].isalpha():
        raise TFTPError(2, "Access violation")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        raise TFTPError(2, "Access violation")

    base = Path(root).resolve()
    target = base
    for part in parts:
        candidate = target / part
        if ignore_case and not candidate.exists() and target.is_dir():
            lowered = part.lower()
            for entry in target.iterdir():
                if entry.name.lower() == lowered:
                    candidate = entry
                    break
        target = candidate
    resolved = target.resolve()
    if resolved != base and base not in resolved.parents:
        raise TFTPError(2, "Access violation")
    return resolved


# --------------------------------------------------------------------------- #
# Cấu hình
# --------------------------------------------------------------------------- #


class _ConfigBase:
    ENV_PREFIX = "TFTP_"

    def validate(self) -> None:  # pragma: no cover - ghi đè ở lớp con
        raise NotImplementedError

    @classmethod
    def load(
        cls: type[T],
        path: str | os.PathLike[str] | None = None,
        env: dict[str, str] | None = None,
        **overrides: Any,
    ) -> T:
        values: dict[str, Any] = {}
        if path:
            with open(path, encoding="utf-8") as handle:
                values.update(json.load(handle))
        environ = os.environ if env is None else env
        names = {f.name: f for f in dataclasses.fields(cls)}  # type: ignore[arg-type]
        for name in names:
            key = _ConfigBase.ENV_PREFIX + name.upper()
            if key in environ:
                values[name] = environ[key]
        values.update({k: v for k, v in overrides.items() if v is not None})
        unknown = set(values) - set(names)
        if unknown:
            raise ConfigError(f"Unknown config keys: {sorted(unknown)}")
        for name, raw in list(values.items()):
            values[name] = _coerce(names[name], raw)
        config = cls(**values)
        config.validate()  # type: ignore[attr-defined]
        return config


def _coerce(field: dataclasses.Field, raw: Any) -> Any:
    kind = field.type if isinstance(field.type, str) else getattr(field.type, "__name__", "")
    try:
        if kind == "bool":
            if isinstance(raw, str):
                return raw.strip().lower() in {"1", "true", "yes", "on"}
            return bool(raw)
        if kind == "int":
            return int(raw)
        if kind == "float":
            return float(raw)
        if kind == "Path":
            return Path(raw)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid value for {field.name}: {raw!r}") from exc
    return raw


@dataclass
class ServerConfig(_ConfigBase):
    host: str = "0.0.0.0"
    port: int = 6969
    root: Path = Path("tftp_root")
    timeout: float = 3.0  # giây, giá trị mặc định khi client không đề nghị
    retries: int = 5
    blksize: int = BLOCK_SIZE  # mặc định khi client không đề nghị
    max_blksize: int = 8192  # trần khi thương lượng
    windowsize: int = 1
    max_windowsize: int = 64
    max_timeout: int = 60
    max_clients: int = 32
    allow_write: bool = True
    allow_overwrite: bool = False
    max_file_size: int = 1 << 30  # 1 GiB
    pxe_mode: bool = False
    simulate_loss: float = 0.0  # chỉ dùng cho test/benchmark
    linger: float = -1.0  # <0: dùng timeout đã thương lượng

    def validate(self) -> None:
        self.root = Path(self.root)
        _check_range("port", self.port, 0, 65535)
        _check_range("blksize", self.blksize, MIN_BLKSIZE, MAX_BLKSIZE)
        _check_range("max_blksize", self.max_blksize, MIN_BLKSIZE, MAX_BLKSIZE)
        _check_range("windowsize", self.windowsize, MIN_WINDOWSIZE, MAX_WINDOWSIZE)
        _check_range("max_windowsize", self.max_windowsize, MIN_WINDOWSIZE, MAX_WINDOWSIZE)
        _check_range("max_timeout", self.max_timeout, MIN_TIMEOUT, MAX_TIMEOUT)
        _check_range("timeout", self.timeout, 0.01, MAX_TIMEOUT)
        _check_range("retries", self.retries, 0, 100)
        _check_range("max_clients", self.max_clients, 1, 10_000)
        _check_range("max_file_size", self.max_file_size, 0, 1 << 50)
        _check_range("simulate_loss", self.simulate_loss, 0.0, 0.95)
        if self.blksize > self.max_blksize:
            raise ConfigError("blksize must be <= max_blksize")
        if self.windowsize > self.max_windowsize:
            raise ConfigError("windowsize must be <= max_windowsize")


@dataclass
class ClientConfig(_ConfigBase):
    timeout: float = 3.0
    retries: int = 5
    blksize: int = BLOCK_SIZE
    windowsize: int = 1
    request_tsize: bool = True
    negotiate_timeout: bool = False  # gửi option timeout trong RRQ/WRQ
    linger: float = 0.2  # chờ sau ACK cuối để re-ACK nếu server gửi lại
    simulate_loss: float = 0.0  # chỉ dùng cho test/benchmark

    def validate(self) -> None:
        _check_range("blksize", self.blksize, MIN_BLKSIZE, MAX_BLKSIZE)
        _check_range("windowsize", self.windowsize, MIN_WINDOWSIZE, MAX_WINDOWSIZE)
        _check_range("timeout", self.timeout, 0.01, MAX_TIMEOUT)
        _check_range("retries", self.retries, 0, 100)
        _check_range("linger", self.linger, 0.0, 30.0)
        _check_range("simulate_loss", self.simulate_loss, 0.0, 0.95)


def _check_range(name: str, value: float, low: float, high: float) -> None:
    if not low <= value <= high:
        raise ConfigError(f"{name} must be in {low}..{high}, got {value}")
