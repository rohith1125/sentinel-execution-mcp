"""Regime classification data models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sentinel.domain.types import RegimeLabel


@dataclass
class StrategyCompatibility:
    """Suitability scores (0-1) for each strategy given the current regime.

    1.0 = ideal conditions, 0.0 = avoid entirely.
    """

    momentum_breakout: float
    vwap_reclaim: float
    ema_trend: float
    rsi_mean_reversion: float
    atr_swing: float
    orb: float

    def as_dict(self) -> dict[str, float]:
        return {
            "momentum_breakout": self.momentum_breakout,
            "vwap_reclaim": self.vwap_reclaim,
            "ema_trend": self.ema_trend,
            "rsi_mean_reversion": self.rsi_mean_reversion,
            "atr_swing": self.atr_swing,
            "orb": self.orb,
        }


@dataclass
class RegimeSnapshot:
    """Full classification output for a symbol at a point in time."""

    label: RegimeLabel
    confidence: float  # 0-1
    tradeability_score: float  # 0-1, 0 = do not trade
    supporting_metrics: dict[str, float]  # adx, atr_pct, rsi, vol_ratio, hurst, etc.
    strategy_compatibility: StrategyCompatibility
    classified_at: datetime
    bars_analyzed: int
    reasoning: str  # human-readable explanation

    def is_tradeable(self, min_score: float = 0.4) -> bool:
        return self.tradeability_score >= min_score

    def strategy_score(self, strategy_name: str) -> float:
        """Return compatibility score for a named strategy."""
        return self.strategy_compatibility.as_dict().get(strategy_name, 0.0)
