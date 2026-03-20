"""API key management service."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime

import structlog

from sentinel.auth.models import APIClient

logger = structlog.get_logger(__name__)


class APIKeyService:
    """
    API key management. Keys are stored as SHA-256 hashes — the raw key is
    only returned once at creation time.

    In production this should back to the database. For now, uses a config-file
    store loaded from SENTINEL_API_KEYS_JSON env var (JSON list of clients) plus
    a master key from SENTINEL_MASTER_KEY env var.
    """

    def generate_key(self) -> tuple[str, str]:
        """Returns (raw_key, hashed_key). Store only the hash."""
        raw = f"sk-sentinel-{secrets.token_urlsafe(32)}"
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        return raw, hashed

    def verify_key(self, raw_key: str, hashed_key: str) -> bool:
        candidate = hashlib.sha256(raw_key.encode()).hexdigest()
        return secrets.compare_digest(candidate, hashed_key)

    def load_clients_from_env(self) -> dict[str, APIClient]:
        """
        Load clients from SENTINEL_API_KEYS_JSON env var (JSON).
        Also loads SENTINEL_MASTER_KEY as a super-admin client.
        Returns dict of hashed_key -> APIClient.
        """
        clients: dict[str, APIClient] = {}

        # Load master key if set
        master_key_raw = os.environ.get("SENTINEL_MASTER_KEY", "").strip()
        if master_key_raw:
            hashed = hashlib.sha256(master_key_raw.encode()).hexdigest()
            clients[hashed] = APIClient(
                client_id="master",
                name="Master Key",
                hashed_key=hashed,
                scopes=["read", "trade", "admin"],
                created_at=datetime.utcnow(),
                rate_limit_per_minute=1000,
            )
            logger.info("auth.master_key_loaded")

        # Load additional clients from JSON env var
        keys_json = os.environ.get("SENTINEL_API_KEYS_JSON", "").strip()
        if keys_json and keys_json not in ("[]", ""):
            try:
                raw_clients: list[dict] = json.loads(keys_json)
                for entry in raw_clients:
                    hashed = entry["hashed_key"]
                    clients[hashed] = APIClient(
                        client_id=entry["client_id"],
                        name=entry["name"],
                        hashed_key=hashed,
                        scopes=entry.get("scopes", ["read"]),
                        created_at=datetime.fromisoformat(entry.get("created_at", datetime.utcnow().isoformat())),
                        last_used_at=(
                            datetime.fromisoformat(entry["last_used_at"]) if entry.get("last_used_at") else None
                        ),
                        is_active=entry.get("is_active", True),
                        rate_limit_per_minute=entry.get("rate_limit_per_minute", 60),
                    )
                logger.info("auth.clients_loaded", count=len(raw_clients))
            except Exception as exc:
                logger.error("auth.clients_load_failed", error=str(exc))

        return clients

    async def authenticate(self, api_key: str) -> APIClient | None:
        """Look up client by hashed key. Return None if not found or inactive."""
        clients = self.load_clients_from_env()
        hashed = hashlib.sha256(api_key.encode()).hexdigest()
        # Use constant-time comparison across all keys to avoid timing attacks
        matched: APIClient | None = None
        for stored_hash, client in clients.items():
            if secrets.compare_digest(hashed, stored_hash):
                matched = client
                break
        if matched is None or not matched.is_active:
            return None
        return matched


_service_instance: APIKeyService | None = None


def get_key_service() -> APIKeyService:
    global _service_instance
    if _service_instance is None:
        _service_instance = APIKeyService()
    return _service_instance
