"""StrategyBase abstract class and signal/result models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pandas as pd
from pydantic import BaseModel, computed_field, model_validator

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import compute_atr
from sentinel.regime.models import RegimeSnapshot


class StrategySignal(BaseModel):
    """A concrete trade signal produced by a strategy."""

    symbol: str
    side: OrderSide
    confidence: float  # 0-1
    entry_price: Decimal | None = None  # None = market order
    stop_price: Decimal
    target_price: Decimal
    timeframe: str  # "1min", "5min", "1day", etc.
    supporting_indicators: dict[str, float] = {}
    invalidation_conditions: list[str] = []
    max_hold_bars: int | None = None
    notes: str = ""

    @computed_field  # type: ignore[misc]
    @property
    def risk_reward_ratio(self) -> float:
        """R:R = |target - entry| / |entry - stop|."""
        entry = self.entry_price
        if entry is None:
            return 0.0
        risk = abs(float(entry) - float(self.stop_price))
        reward = abs(float(self.target_price) - float(entry))
        if risk == 0:
            return 0.0
        return round(reward / risk, 3)

    @model_validator(mode="after")
    def validate_prices(self) -> StrategySignal:
        if self.entry_price is not None and self.stop_price == self.entry_price:
            raise ValueError("stop_price must differ from entry_price")
        return self


class StrategyResult(BaseModel):
    """Output of a single strategy evaluation."""

    strategy_name: str
    symbol: str
    signal: StrategySignal | None  # None = no signal this bar
    evaluated_at: datetime
    bars_used: int
    regime_compatibility: float  # 0-1, how suitable was the regime
    rejection_reason: str | None = None  # why signal is None


class StrategyBase(ABC):
    """Abstract base for all strategy implementations."""

    name: ClassVar[str]
    supported_regimes: ClassVar[list[RegimeLabel]]
    anti_regimes: ClassVar[list[RegimeLabel]]  # hard blocks — always reject
    min_bars_required: ClassVar[int]
    default_timeframe: ClassVar[str]

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        regime: RegimeSnapshot,
    ) -> StrategyResult:
        """Evaluate strategy on current bar history and regime.

        Must return a StrategyResult. If no signal, set signal=None and
        provide a rejection_reason.
        """
        ...

    def is_regime_compatible(self, regime: RegimeSnapshot) -> tuple[bool, float]:
        """Return (compatible, score). Hard block if regime is in anti_regimes."""
        if regime.label in self.anti_regimes:
            return False, 0.0
        if regime.label in self.supported_regimes:
            score = regime.strategy_score(self.name.lower().replace(" ", "_"))
            return True, max(0.1, score)
        # Neutral regime — use compatibility table score with penalty
        score = regime.strategy_score(self.name.lower().replace(" ", "_"))
        return (score >= 0.4), score * 0.7

    def compute_stop(
        self,
        bars: list[Bar],
        side: OrderSide,
        atr_multiplier: float = 2.0,
    ) -> Decimal:
        """ATR-based stop calculation from latest bar."""
        high = pd.Series([float(b.high) for b in bars])
        low = pd.Series([float(b.low) for b in bars])
        close = pd.Series([float(b.close) for b in bars])
        atr_series = compute_atr(high, low, close)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        latest_close = float(bars[-1].close)

        if side == OrderSide.BUY:
            stop = latest_close - atr_multiplier * atr_val
        else:
            stop = latest_close + atr_multiplier * atr_val

        return Decimal(str(round(max(0.01, stop), 4)))

    def compute_target(
        self,
        entry: Decimal,
        stop: Decimal,
        rr_ratio: float = 2.0,
    ) -> Decimal:
        """Compute target price from entry, stop, and minimum R:R ratio."""
        risk = abs(float(entry) - float(stop))
        reward = risk * rr_ratio
        if float(entry) > float(stop):
            # Long position
            target = float(entry) + reward
        else:
            # Short position
            target = float(entry) - reward
        return Decimal(str(round(target, 4)))

    def _no_signal(
        self,
        strategy_name: str,
        symbol: str,
        bars: list[Bar],
        regime_compatibility: float,
        reason: str,
    ) -> StrategyResult:
        """Convenience builder for no-signal result."""
        return StrategyResult(
            strategy_name=strategy_name,
            symbol=symbol,
            signal=None,
            evaluated_at=datetime.now(tz=UTC),
            bars_used=len(bars),
            regime_compatibility=regime_compatibility,
            rejection_reason=reason,
        )
