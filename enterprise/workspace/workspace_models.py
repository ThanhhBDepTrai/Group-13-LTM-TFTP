from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Workspace:
    id: int
    name: str
    created_at: str
