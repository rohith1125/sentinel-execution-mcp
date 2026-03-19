"""Sentinel API authentication package."""

from sentinel.auth.middleware import get_current_client, require_scope
from sentinel.auth.service import APIKeyService

__all__ = ["APIKeyService", "get_current_client", "require_scope"]
