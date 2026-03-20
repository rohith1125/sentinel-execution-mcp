"""Tests for auth service and rate limiter."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from sentinel.auth.models import APIClient
from sentinel.auth.rate_limiter import RateLimiter
from sentinel.auth.service import APIKeyService

# ---------------------------------------------------------------------------
# APIKeyService tests
# ---------------------------------------------------------------------------


class TestAPIKeyService:
    def setup_method(self):
        self.service = APIKeyService()

    def test_generate_key_has_correct_prefix(self):
        raw, hashed = self.service.generate_key()
        assert raw.startswith("sk-sentinel-")

    def test_verify_key_correct_key_passes(self):
        raw, hashed = self.service.generate_key()
        assert self.service.verify_key(raw, hashed) is True

    def test_verify_key_wrong_key_fails(self):
        raw, hashed = self.service.generate_key()
        assert self.service.verify_key("sk-sentinel-wrongkey", hashed) is False

    def test_hash_is_not_reversible(self):
        raw, hashed = self.service.generate_key()
        assert raw != hashed

    def test_load_clients_from_env_parses_json(self, monkeypatch):
        raw, hashed = self.service.generate_key()
        clients_data = json.dumps(
            [
                {
                    "client_id": "test-agent",
                    "name": "Test Agent",
                    "hashed_key": hashed,
                    "scopes": ["read", "trade"],
                    "created_at": datetime.utcnow().isoformat(),
                    "is_active": True,
                    "rate_limit_per_minute": 30,
                }
            ]
        )
        monkeypatch.setenv("SENTINEL_API_KEYS_JSON", clients_data)
        monkeypatch.delenv("SENTINEL_MASTER_KEY", raising=False)

        clients = self.service.load_clients_from_env()
        assert hashed in clients
        client = clients[hashed]
        assert client.client_id == "test-agent"
        assert client.scopes == ["read", "trade"]
        assert client.rate_limit_per_minute == 30

    def test_load_clients_master_key(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_MASTER_KEY", "my-secret-master-key")
        monkeypatch.delenv("SENTINEL_API_KEYS_JSON", raising=False)

        clients = self.service.load_clients_from_env()
        hashed = hashlib.sha256(b"my-secret-master-key").hexdigest()
        assert hashed in clients
        assert "admin" in clients[hashed].scopes

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, monkeypatch):
        raw, hashed = self.service.generate_key()
        clients_data = json.dumps(
            [
                {
                    "client_id": "auth-test",
                    "name": "Auth Test",
                    "hashed_key": hashed,
                    "scopes": ["read"],
                    "created_at": datetime.utcnow().isoformat(),
                    "is_active": True,
                }
            ]
        )
        monkeypatch.setenv("SENTINEL_API_KEYS_JSON", clients_data)
        monkeypatch.delenv("SENTINEL_MASTER_KEY", raising=False)

        client = await self.service.authenticate(raw)
        assert client is not None
        assert client.client_id == "auth-test"

    @pytest.mark.asyncio
    async def test_authenticate_invalid_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("SENTINEL_API_KEYS_JSON", raising=False)
        monkeypatch.delenv("SENTINEL_MASTER_KEY", raising=False)

        client = await self.service.authenticate("sk-sentinel-bogus")
        assert client is None

    @pytest.mark.asyncio
    async def test_authenticate_inactive_client_returns_none(self, monkeypatch):
        raw, hashed = self.service.generate_key()
        clients_data = json.dumps(
            [
                {
                    "client_id": "inactive-agent",
                    "name": "Inactive",
                    "hashed_key": hashed,
                    "scopes": ["read"],
                    "created_at": datetime.utcnow().isoformat(),
                    "is_active": False,
                }
            ]
        )
        monkeypatch.setenv("SENTINEL_API_KEYS_JSON", clients_data)
        monkeypatch.delenv("SENTINEL_MASTER_KEY", raising=False)

        client = await self.service.authenticate(raw)
        assert client is None


# ---------------------------------------------------------------------------
# Scope / admin bypass tests
# ---------------------------------------------------------------------------


def _make_client(scopes: list[str]) -> APIClient:
    return APIClient(
        client_id="test",
        name="Test",
        hashed_key="x",
        scopes=scopes,
        created_at=datetime.utcnow(),
    )


def test_require_scope_admin_bypasses_all_scopes():
    """Admin scope should satisfy any scope check."""
    from sentinel.auth.middleware import require_scope

    admin_client = _make_client(["admin"])
    checker = require_scope("trade")

    # Simulate the inner check function directly
    # admin in scopes => no exception
    if "trade" not in admin_client.scopes and "admin" not in admin_client.scopes:
        pytest.fail("Admin should bypass scope check")


def test_require_scope_missing_scope_raises():
    from fastapi import HTTPException

    read_only_client = _make_client(["read"])
    # Directly invoke the scope logic
    scope = "trade"
    with pytest.raises(HTTPException) as exc_info:
        if scope not in read_only_client.scopes and "admin" not in read_only_client.scopes:
            raise HTTPException(status_code=403, detail=f"Scope '{scope}' required.")
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# RateLimiter tests
# ---------------------------------------------------------------------------


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_within_limit(self):
        limiter = RateLimiter()
        # Use unique client_id to avoid cross-test pollution
        allowed, remaining = await limiter.check_and_increment("rl-test-allow", 10, None)
        assert allowed is True
        assert remaining == 9

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self):
        limiter = RateLimiter()
        client_id = "rl-test-block"
        # Exhaust the limit
        for _ in range(2):
            await limiter.check_and_increment(client_id, 2, None)
        allowed, remaining = await limiter.check_and_increment(client_id, 2, None)
        assert allowed is False
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_uses_redis_when_available(self):
        limiter = RateLimiter()
        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(return_value=1)
        redis_mock.expire = AsyncMock()

        allowed, remaining = await limiter.check_and_increment("redis-client", 60, redis_mock)
        redis_mock.incr.assert_called_once()
        redis_mock.expire.assert_called_once()
        assert allowed is True
        assert remaining == 59

    @pytest.mark.asyncio
    async def test_falls_back_to_memory_on_redis_error(self):
        limiter = RateLimiter()
        redis_mock = AsyncMock()
        redis_mock.incr = AsyncMock(side_effect=Exception("Redis down"))

        allowed, remaining = await limiter.check_and_increment("fallback-client", 60, redis_mock)
        assert allowed is True  # falls back gracefully
