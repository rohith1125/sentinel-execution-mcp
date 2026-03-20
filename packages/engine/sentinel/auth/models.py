"""API client model for authentication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class APIClient:
    client_id: str
    name: str
    hashed_key: str
    scopes: list[str]           # e.g. ["read", "trade", "admin"]
    created_at: datetime
    last_used_at: datetime | None = None
    is_active: bool = True
    rate_limit_per_minute: int = 60
