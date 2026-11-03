"""Kiểm tra IP và Port trước khi tạo Server."""

from __future__ import annotations

import ipaddress
import socket

from enterprise.errors import EnterpriseError


def validate_host(host: str) -> str:
    text = str(host).strip()
    try:
        ipaddress.ip_address(text)
    except ValueError as exc:
        if not text or len(text) > 253 or any(ch.isspace() for ch in text):
            raise EnterpriseError("Địa chỉ Server không hợp lệ") from exc
    return text


def validate_port(port) -> int:
    try:
        value = int(port)
    except (TypeError, ValueError) as exc:
        raise EnterpriseError("Port phải là số") from exc
    if not 1 <= value <= 65535:
        raise EnterpriseError("Port phải trong khoảng 1-65535")
    return value


def port_available(host: str, port: int, *, udp: bool = False) -> bool:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, kind) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def validate_server_config(host, api_port, tftp_port, *, check_bind: bool = False) -> dict:
    host = validate_host(host)
    api_port, tftp_port = validate_port(api_port), validate_port(tftp_port)
    if api_port == tftp_port:
        raise EnterpriseError("API Port và TFTP Port không được trùng nhau")
    if check_bind:
        bind = host if host not in ("localhost",) else "127.0.0.1"
        if not port_available(bind, api_port):
            raise EnterpriseError(f"API Port {api_port} đang được sử dụng", 409)
        if not port_available(bind, tftp_port, udp=True):
            raise EnterpriseError(f"TFTP Port {tftp_port} đang được sử dụng", 409)
    return {"host": host, "api_port": api_port, "tftp_port": tftp_port}
