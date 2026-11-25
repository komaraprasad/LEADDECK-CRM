"""Shared record types produced by mappers and consumed by the merge service."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class NormalizedRecord:
    tool: str
    kind: Literal["person", "company", "job"]
    source_key: str
    raw: dict
    fields: dict
    collected_at: datetime | None = None


@dataclass
class MapResult:
    records: list[NormalizedRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
