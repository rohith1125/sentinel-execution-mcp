"""MarketDataService — provider registry, Redis caching, and bulk operations."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import structlog

from sentinel.market.provider import Bar, MarketDataProvider, Quote, Snapshot

logger = structlog.get_logger(__name__)

# Cache TTLs in seconds
_TTL_QUOTE = 10
_TTL_BAR = 60
_TTL_SNAPSHOT = 15


def _params_hash(params: dict[str, Any]) -> str:
    serialized = json.dumps(params, sort_keys=True, default=str)
    return hashlib.md5(serialized.encode()).hexdigest()[:8]


class MarketDataService:
    """Provider registry with Redis caching and fallback support."""

    def __init__(
        self,
        providers: dict[str, MarketDataProvider],
        primary: str,
        redis_client: Any | None = None,
    ) -> None:
        if primary not in providers:
            raise ValueError(f"Primary provider '{primary}' not in registry: {list(providers)}")
        self._providers = providers
        self._primary = primary
        self._redis = redis_client

    def _cache_key(self, provider: str, data_type: str, symbol: str, params: str = "") -> str:
        parts = ["sentinel", "mkt", provider, data_type, symbol.upper()]
        if params:
            parts.append(params)
        return ":".join(parts)

    async def _cache_get(self, key: str) -> dict[str, Any] | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
            if raw is not None:
                return json.loads(raw)
        except Exception as exc:
            logger.warning("cache.get_error", key=key, error=str(exc))
        return None

    async def _cache_set(self, key: str, data: dict[str, Any], ttl: int) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.setex(key, ttl, json.dumps(data, default=str))
        except Exception as exc:
            logger.warning("cache.set_error", key=key, error=str(exc))

    def _active_provider(self) -> MarketDataProvider:
        return self._providers[self._primary]

    def _fallback_provider(self) -> MarketDataProvider | None:
        for name, provider in self._providers.items():
            if name != self._primary:
                return provider
        return None

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Bar]:
        params_hash = _params_hash({"tf": timeframe, "start": str(start), "end": str(end), "limit": limit})
        cache_key = self._cache_key(self._primary, "bars", symbol, params_hash)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return [Bar(**b) for b in cached]

        provider = self._active_provider()
        try:
            bars = await provider.get_bars(symbol, timeframe, start, end, limit)
        except Exception as exc:
            fallback = self._fallback_provider()
            if fallback is None:
                raise
            logger.warning(
                "market.primary_failed_bars",
                symbol=symbol,
                error=str(exc),
                fallback=True,
            )
            bars = await fallback.get_bars(symbol, timeframe, start, end, limit)

        await self._cache_set(cache_key, [b.model_dump() for b in bars], _TTL_BAR)
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        cache_key = self._cache_key(self._primary, "quote", symbol)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return Quote(**cached)

        provider = self._active_provider()
        try:
            quote = await provider.get_quote(symbol)
        except Exception as exc:
            fallback = self._fallback_provider()
            if fallback is None:
                raise
            logger.warning(
                "market.primary_failed_quote",
                symbol=symbol,
                error=str(exc),
                fallback=True,
            )
            quote = await fallback.get_quote(symbol)

        await self._cache_set(cache_key, quote.model_dump(), _TTL_QUOTE)
        return quote

    async def get_snapshot(self, symbol: str) -> Snapshot:
        cache_key = self._cache_key(self._primary, "snapshot", symbol)
        cached = await self._cache_get(cache_key)
        if cached is not None:
            return Snapshot(**cached)

        provider = self._active_provider()
        try:
            snapshot = await provider.get_snapshot(symbol)
        except Exception as exc:
            fallback = self._fallback_provider()
            if fallback is None:
                raise
            logger.warning(
                "market.primary_failed_snapshot",
                symbol=symbol,
                error=str(exc),
                fallback=True,
            )
            snapshot = await fallback.get_snapshot(symbol)

        await self._cache_set(cache_key, snapshot.model_dump(), _TTL_SNAPSHOT)
        return snapshot

    async def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        results: dict[str, Snapshot] = {}
        cache_misses: list[str] = []

        for symbol in symbols:
            cache_key = self._cache_key(self._primary, "snapshot", symbol)
            cached = await self._cache_get(cache_key)
            if cached is not None:
                results[symbol] = Snapshot(**cached)
            else:
                cache_misses.append(symbol)

        if cache_misses:
            provider = self._active_provider()
            try:
                fetched = await provider.get_snapshots(cache_misses)
            except Exception as exc:
                fallback = self._fallback_provider()
                if fallback is None:
                    raise
                logger.warning(
                    "market.primary_failed_snapshots",
                    count=len(cache_misses),
                    error=str(exc),
                    fallback=True,
                )
                fetched = await fallback.get_snapshots(cache_misses)

            for symbol, snapshot in fetched.items():
                cache_key = self._cache_key(self._primary, "snapshot", symbol)
                await self._cache_set(cache_key, snapshot.model_dump(), _TTL_SNAPSHOT)
                results[symbol] = snapshot

        return results

    async def get_bulk_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        """Efficient bulk snapshot fetch for watchlist evaluation."""
        return await self.get_snapshots(symbols)

    async def validate_symbol(self, symbol: str) -> bool:
        provider = self._active_provider()
        return await provider.validate_symbol(symbol)

    async def get_tradeable_assets(self, asset_class: str = "us_equity") -> list[str]:
        provider = self._active_provider()
        return await provider.get_tradeable_assets(asset_class)
