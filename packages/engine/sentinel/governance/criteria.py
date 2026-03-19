"""
PromotionCriteria — thresholds required to promote a strategy to a given state.

Criteria are deliberately conservative. A strategy must earn each promotion
by demonstrating real edge in progressively more demanding conditions.
"""
from __future__ import annotations

from dataclasses import dataclass

from sentinel.domain.types import StrategyState


@dataclass(frozen=True)
class PromotionCriteria:
    """Thresholds required to promote to a given state."""

    target_state: StrategyState

    # Performance requirements
    min_trades: int  # Minimum number of trades in evaluation period
    min_win_rate: float  # e.g. 0.45 (45%)
    min_profit_factor: float  # gross_wins / gross_losses, e.g. 1.3
    max_drawdown_pct: float  # max allowed drawdown, e.g. 0.08 (8%)
    min_sharpe_ratio: float  # risk-adjusted, e.g. 0.8
    min_expectancy_r: float  # average trade in R-multiples, e.g. 0.2

    # Quality requirements
    max_avg_slippage_bps: float  # fill quality check
    min_fill_rate: float  # % of orders filled (not cancelled)
    evaluation_period_days: int  # lookback window

    # Approval
    requires_human_sign_off: bool  # paper->live always requires human


# Predefined criteria for each promotion target state.
# Conservative by design — requirements tighten as we approach real money.
CRITERIA: dict[StrategyState, PromotionCriteria] = {
    StrategyState.BACKTEST_APPROVED: PromotionCriteria(
        target_state=StrategyState.BACKTEST_APPROVED,
        min_trades=30,
        min_win_rate=0.40,
        min_profit_factor=1.25,
        max_drawdown_pct=0.15,
        min_sharpe_ratio=0.5,
        min_expectancy_r=0.15,
        max_avg_slippage_bps=999.0,  # no execution data in backtest
        min_fill_rate=0.0,
        evaluation_period_days=252,
        requires_human_sign_off=False,
    ),
    StrategyState.PAPER_APPROVED: PromotionCriteria(
        target_state=StrategyState.PAPER_APPROVED,
        min_trades=20,
        min_win_rate=0.42,
        min_profit_factor=1.3,
        max_drawdown_pct=0.10,
        min_sharpe_ratio=0.7,
        min_expectancy_r=0.2,
        max_avg_slippage_bps=15.0,
        min_fill_rate=0.85,
        evaluation_period_days=60,
        requires_human_sign_off=False,
    ),
    StrategyState.LIVE_APPROVED: PromotionCriteria(
        target_state=StrategyState.LIVE_APPROVED,
        min_trades=15,
        min_win_rate=0.44,
        min_profit_factor=1.35,
        max_drawdown_pct=0.08,
        min_sharpe_ratio=0.8,
        min_expectancy_r=0.25,
        max_avg_slippage_bps=12.0,
        min_fill_rate=0.88,
        evaluation_period_days=30,
        requires_human_sign_off=True,  # ALWAYS human for live
    ),
}
