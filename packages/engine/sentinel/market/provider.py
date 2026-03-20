"""Market data provider protocol and data models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, computed_field, field_validator


class Bar(BaseModel):
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None = None
    trade_count: int | None = None

    @field_validator("open", "high", "low", "close", "vwap", mode="before")
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))


class Quote(BaseModel):
    symbol: str
    timestamp: datetime
    bid: Decimal
    ask: Decimal
    bid_size: int
    ask_size: int

    @field_validator("bid", "ask", mode="before")
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal:
        return Decimal(str(v))

    @computed_field  # type: ignore[misc]
    @property
    def spread_bps(self) -> float:
        if self.bid == 0:
            return 0.0
        return float((self.ask - self.bid) / self.bid * 10000)

    @computed_field  # type: ignore[misc]
    @property
    def mid(self) -> Decimal:
        return (self.bid + self.ask) / 2


class Snapshot(BaseModel):
    symbol: str
    quote: Quote
    latest_bar: Bar
    daily_bar: Bar | None = None
    prev_close: Decimal | None = None

    @field_validator("prev_close", mode="before")
    @classmethod
    def coerce_decimal(cls, v: object) -> Decimal | None:
        if v is None:
            return None
        return Decimal(str(v))

    @property
    def intraday_change_pct(self) -> float | None:
        if self.prev_close is None or self.prev_close == 0:
            return None
        return float(
            (self.latest_bar.close - self.prev_close) / self.prev_close * 100
        )


class ProviderError(Exception):
    """Base error for market data provider failures."""


class SymbolNotFoundError(ProviderError):
    """Symbol does not exist or is not tradeable."""


class RateLimitError(ProviderError):
    """Provider rate limit exceeded."""


@runtime_checkable
class MarketDataProvider(Protocol):
    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Bar]: ...

    async def get_quote(self, symbol: str) -> Quote: ...

    async def get_snapshot(self, symbol: str) -> Snapshot: ...

    async def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]: ...

    async def validate_symbol(self, symbol: str) -> bool: ...

    async def get_tradeable_assets(self, asset_class: str = "us_equity") -> list[str]: ...
