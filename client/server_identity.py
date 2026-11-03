"""Kiểm tra Server ID phía Client (chống nối nhầm Server)."""

from enterprise.server.server_identity import is_valid_server_id


def matches(expected_id: str, actual_id: str) -> bool:
    return is_valid_server_id(expected_id) and expected_id == actual_id
