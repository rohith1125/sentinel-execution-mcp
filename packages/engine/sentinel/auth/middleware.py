"""FastAPI auth dependency functions."""

from __future__ import annotations

import os

import structlog
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from sentinel.auth.models import APIClient
from sentinel.auth.service import APIKeyService, get_key_service

logger = structlog.get_logger(__name__)

API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)


def _auth_enabled() -> bool:
    """Auth bypass only works in APP_ENV=development."""
    enabled_env = os.environ.get("SENTINEL_AUTH_ENABLED", "true").lower()
    app_env = os.environ.get("APP_ENV", "paper").lower()
    if enabled_env == "false" and app_env == "development":
        return False
    return True


async def get_current_client(
    request: Request,
    api_key: str | None = Security(API_KEY_HEADER),
    key_service: APIKeyService = Depends(get_key_service),
) -> APIClient:
    """
    FastAPI dependency. Raises 401 if key missing, 403 if invalid.
    Skips auth if SENTINEL_AUTH_ENABLED=false AND APP_ENV=development.
    """
    if not _auth_enabled():
        # Dev bypass — return a synthetic admin client
        return APIClient(
            client_id="dev-bypass",
            name="Dev Bypass",
            hashed_key="",
            scopes=["read", "trade", "admin"],
            created_at=__import__("datetime").datetime.utcnow(),
            rate_limit_per_minute=10000,
        )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    client = await key_service.authenticate(api_key)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or inactive API key.",
        )

    logger.debug("auth.client_authenticated", client_id=client.client_id)
    return client


def require_scope(scope: str):
    """Dependency factory: require a specific scope."""

    def check(client: APIClient = Depends(get_current_client)) -> APIClient:
        if scope not in client.scopes and "admin" not in client.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Scope '{scope}' required.",
            )
        return client

    # Give the inner function a unique name so FastAPI can distinguish them
    check.__name__ = f"require_scope_{scope}"
    return check


# Convenience scope dependencies
ReadAccess = Depends(require_scope("read"))
TradeAccess = Depends(require_scope("trade"))
AdminAccess = Depends(require_scope("admin"))
