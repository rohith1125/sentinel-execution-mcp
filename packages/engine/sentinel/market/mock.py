"""Deterministic mock market data provider for testing and paper trading."""

from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from sentinel.market.provider import Bar, Quote, Snapshot, SymbolNotFoundError

# Canonical universe with realistic seed prices
_UNIVERSE: dict[str, Decimal] = {
    "AAPL": Decimal("182.50"),
    "MSFT": Decimal("415.00"),
    "NVDA": Decimal("875.00"),
    "GOOGL": Decimal("175.00"),
    "AMZN": Decimal("185.00"),
    "META": Decimal("500.00"),
    "TSLA": Decimal("175.00"),
    "JPM": Decimal("195.00"),
    "GS": Decimal("455.00"),
    "SPY": Decimal("520.00"),
    "QQQ": Decimal("440.00"),
    "IWM": Decimal("200.00"),
    "DIA": Decimal("385.00"),
    "GLD": Decimal("225.00"),
    "TLT": Decimal("92.00"),
    "XLF": Decimal("42.00"),
    "XLE": Decimal("87.00"),
    "ARKK": Decimal("48.00"),
    "COIN": Decimal("195.00"),
    "AMD": Decimal("175.00"),
}

Scenario = Literal["bull_trend", "bear_trend", "choppy", "low_volume"]

_SCENARIO_PARAMS: dict[Scenario, dict[str, float]] = {
    "bull_trend":  {"drift": 0.0008,  "vol": 0.012, "volume_mult": 1.2},
    "bear_trend":  {"drift": -0.0008, "vol": 0.015, "volume_mult": 0.9},
    "choppy":      {"drift": 0.0,     "vol": 0.020, "volume_mult": 1.0},
    "low_volume":  {"drift": 0.0001,  "vol": 0.008, "volume_mult": 0.3},
}


class MockProvider:
    """Deterministic mock provider using seeded random walks."""

    def __init__(self, scenario: Scenario = "choppy", seed: int = 42) -> None:
        self._scenario: Scenario = scenario
        self._seed = seed
        self._price_cache: dict[str, Decimal] = {}

    def set_scenario(self, scenario: Scenario) -> None:
        self._scenario = scenario
        self._price_cache.clear()

    def _rng(self, symbol: str, call_type: str, index: int) -> random.Random:
        key = f"{self._seed}:{symbol}:{call_type}:{index}"
        seed_int = int(hashlib.md5(key.encode()).hexdigest(), 16) % (2**31)
        rng = random.Random(seed_int)
        return rng

    def _current_price(self, symbol: str) -> Decimal:
        if symbol in self._price_cache:
            return self._price_cache[symbol]
        if symbol not in _UNIVERSE:
            raise SymbolNotFoundError(f"Symbol not in mock universe: {symbol}")
        base = _UNIVERSE[symbol]
        params = _SCENARIO_PARAMS[self._scenario]
        rng = self._rng(symbol, "price", 0)
        # Apply a small session drift
        drift = Decimal(str(1 + params["drift"] * 20 + rng.gauss(0, params["vol"] * 0.5)))
        price = (base * drift).quantize(Decimal("0.01"))
        self._price_cache[symbol] = price
        return price

    def _spread_fraction(self, price: Decimal) -> Decimal:
        if price >= Decimal("200"):
            return Decimal("0.0005")
        if price >= Decimal("50"):
            return Decimal("0.0008")
        return Decimal("0.0015")

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Bar]:
        if symbol not in _UNIVERSE:
            raise SymbolNotFoundError(f"Symbol not in mock universe: {symbol}")

        params = _SCENARIO_PARAMS[self._scenario]
        base_price = self._current_price(symbol)
        price = float(base_price)
        bars: list[Bar] = []

        minutes = min(limit, max(1, int((end - start).total_seconds() / 60)))
        vol_mult = params["volume_mult"]

        for i in range(minutes):
            rng = self._rng(symbol, f"bar:{timeframe}", i)
            ret = rng.gauss(params["drift"], params["vol"] / (390**0.5))
            price = max(price * (1 + ret), 0.01)

            high_factor = 1 + abs(rng.gauss(0, params["vol"] * 0.3))
            low_factor = 1 - abs(rng.gauss(0, params["vol"] * 0.3))

            open_p = Decimal(str(round(price * (1 + rng.gauss(0, 0.001)), 2)))
            close_p = Decimal(str(round(price, 2)))
            high_p = max(open_p, close_p) * Decimal(str(round(high_factor, 6)))
            low_p = min(open_p, close_p) * Decimal(str(round(low_factor, 6)))

            vol = int(abs(rng.gauss(500_000, 200_000)) * vol_mult)
            vwap = ((open_p + high_p + low_p + close_p) / 4).quantize(Decimal("0.0001"))

            bars.append(
                Bar(
                    symbol=symbol,
                    timestamp=start + timedelta(minutes=i),
                    open=open_p.quantize(Decimal("0.01")),
                    high=high_p.quantize(Decimal("0.01")),
                    low=low_p.quantize(Decimal("0.01")),
                    close=close_p,
                    volume=max(100, vol),
                    vwap=vwap,
                    trade_count=max(10, vol // 200),
                )
            )
        return bars

    async def get_quote(self, symbol: str) -> Quote:
        if symbol not in _UNIVERSE:
            raise SymbolNotFoundError(f"Symbol not in mock universe: {symbol}")
        price = self._current_price(symbol)
        spread_frac = self._spread_fraction(price)
        half_spread = (price * spread_frac / 2).quantize(Decimal("0.01"))
        bid = price - half_spread
        ask = price + half_spread

        rng = self._rng(symbol, "quote", 0)
        bid_size = max(100, int(rng.gauss(500, 200)))
        ask_size = max(100, int(rng.gauss(500, 200)))

        return Quote(
            symbol=symbol,
            timestamp=datetime.utcnow(),
            bid=bid,
            ask=ask,
            bid_size=bid_size,
            ask_size=ask_size,
        )

    async def get_snapshot(self, symbol: str) -> Snapshot:
        if symbol not in _UNIVERSE:
            raise SymbolNotFoundError(f"Symbol not in mock universe: {symbol}")
        quote = await self.get_quote(symbol)
        price = self._current_price(symbol)
        params = _SCENARIO_PARAMS[self._scenario]
        rng = self._rng(symbol, "snap", 0)
        vol = int(abs(rng.gauss(2_000_000, 500_000)) * params["volume_mult"])

        latest_bar = Bar(
            symbol=symbol,
            timestamp=datetime.utcnow().replace(second=0, microsecond=0),
            open=(price * Decimal("0.999")).quantize(Decimal("0.01")),
            high=(price * Decimal("1.003")).quantize(Decimal("0.01")),
            low=(price * Decimal("0.997")).quantize(Decimal("0.01")),
            close=price,
            volume=vol,
            vwap=price,
        )

        prev_close = (price * (1 - Decimal(str(params["drift"] * 10)))).quantize(Decimal("0.01"))

        return Snapshot(
            symbol=symbol,
            quote=quote,
            latest_bar=latest_bar,
            prev_close=prev_close,
        )

    async def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        result: dict[str, Snapshot] = {}
        for symbol in symbols:
            try:
                result[symbol] = await self.get_snapshot(symbol)
            except SymbolNotFoundError:
                pass
        return result

    async def validate_symbol(self, symbol: str) -> bool:
        return symbol.upper() in _UNIVERSE

    async def get_tradeable_assets(self, asset_class: str = "us_equity") -> list[str]:
        return list(_UNIVERSE.keys())
