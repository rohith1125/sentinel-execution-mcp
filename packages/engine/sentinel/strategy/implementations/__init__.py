"""Strategy implementations — auto-registers all strategies on import."""

from sentinel.strategy.implementations.atr_swing import ATRSwingTrendStrategy
from sentinel.strategy.implementations.ema_trend import EMATrendContinuationStrategy
from sentinel.strategy.implementations.momentum_breakout import MomentumBreakoutStrategy
from sentinel.strategy.implementations.orb import OpeningRangeBreakoutStrategy
from sentinel.strategy.implementations.rsi_mean_reversion import RSIMeanReversionStrategy
from sentinel.strategy.implementations.vwap_reclaim import VWAPReclaimStrategy

__all__ = [
    "ATRSwingTrendStrategy",
    "EMATrendContinuationStrategy",
    "MomentumBreakoutStrategy",
    "OpeningRangeBreakoutStrategy",
    "RSIMeanReversionStrategy",
    "VWAPReclaimStrategy",
]
