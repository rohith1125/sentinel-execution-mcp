"""Loads historical bars for backtesting, with local file cache."""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sentinel.market.provider import Bar


class HistoricalDataLoader:
    """
    Loads bars from:
    1. Local JSON cache (fastest)
    2. Market data provider (slower, cached after first fetch)

    Cache location: ~/.sentinel/bar_cache/{symbol}_{timeframe}_{start}_{end}.json
    """

    DEFAULT_CACHE_DIR = Path.home() / ".sentinel" / "bar_cache"

    def __init__(self, market_service: Any, cache_dir: Path | None = None) -> None:
        self.market_service = market_service
        self.cache_dir = cache_dir or self.DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def load_bars(
        self,
        symbol: str,
        timeframe: str,
        start: date,
        end: date,
    ) -> list[Bar]:
        """Load bars with caching. Returns sorted chronologically."""
        key = self._cache_key(symbol, timeframe, start, end)
        cached = self._load_from_cache(key)
        if cached is not None:
            return cached

        # Fetch from provider
        start_dt = datetime(start.year, start.month, start.day, tzinfo=UTC)
        end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=UTC)

        bars = await self.market_service.get_bars(
            symbol=symbol,
            timeframe=timeframe,
            start=start_dt,
            end=end_dt,
        )

        # Sort chronologically
        bars = sorted(bars, key=lambda b: b.timestamp)

        self._save_to_cache(key, bars)
        return bars

    def _cache_key(self, symbol: str, timeframe: str, start: date, end: date) -> str:
        return f"{symbol.upper()}_{timeframe}_{start.isoformat()}_{end.isoformat()}"

    def _save_to_cache(self, key: str, bars: list[Bar]) -> None:
        path = self.cache_dir / f"{key}.json"
        try:
            data = [
                {
                    "symbol": b.symbol,
                    "timestamp": b.timestamp.isoformat(),
                    "open": str(b.open),
                    "high": str(b.high),
                    "low": str(b.low),
                    "close": str(b.close),
                    "volume": b.volume,
                    "vwap": str(b.vwap) if b.vwap is not None else None,
                    "trade_count": b.trade_count,
                }
                for b in bars
            ]
            path.write_text(json.dumps(data, indent=2))
        except OSError:
            pass  # Cache write failure is non-fatal

    def _load_from_cache(self, key: str) -> list[Bar] | None:
        path = self.cache_dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            bars = []
            for item in data:
                bars.append(Bar(
                    symbol=item["symbol"],
                    timestamp=datetime.fromisoformat(item["timestamp"]),
                    open=Decimal(item["open"]),
                    high=Decimal(item["high"]),
                    low=Decimal(item["low"]),
                    close=Decimal(item["close"]),
                    volume=int(item["volume"]),
                    vwap=Decimal(item["vwap"]) if item.get("vwap") else None,
                    trade_count=item.get("trade_count"),
                ))
            return sorted(bars, key=lambda b: b.timestamp)
        except (OSError, json.JSONDecodeError, KeyError, ValueError):
            return None
