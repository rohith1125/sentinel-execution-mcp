"""Backtest package for Sentinel Execution Engine."""

from sentinel.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult, BacktestTrade
from sentinel.backtest.stats import BacktestStats, compute_stats
from sentinel.backtest.walk_forward import (
    WalkForwardResult,
    WalkForwardValidator,
    WalkForwardWindow,
)

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "BacktestStats",
    "BacktestTrade",
    "WalkForwardResult",
    "WalkForwardValidator",
    "WalkForwardWindow",
    "compute_stats",
]
