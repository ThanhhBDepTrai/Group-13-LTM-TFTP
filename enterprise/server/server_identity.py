"""Sinh và kiểm tra Server ID (dạng SRV-XXXXXXXX)."""

from __future__ import annotations

import re
import secrets

_ID_RE = re.compile(r"^SRV-[0-9A-F]{8}$")


def generate_server_id() -> str:
    return "SRV-" + secrets.token_hex(4).upper()


def is_valid_server_id(value: str) -> bool:
    return bool(_ID_RE.fullmatch(str(value)))
