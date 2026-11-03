from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ServerConfig:
    server_id: str
    name: str
    host: str
    api_port: int
    tftp_port: int
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
