from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuditEntry:
    id: int
    timestamp: str
    actor: str | None
    action: str
    target: str | None
    detail: str | None
    success: bool
