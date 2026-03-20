"""
Performance statistics for backtest results.
All calculations are standard and verifiable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentinel.backtest.engine import BacktestConfig, BacktestTrade


@dataclass
class BacktestStats:
    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # P&L statistics
    gross_profit: Decimal
    gross_loss: Decimal
    net_profit: Decimal
    profit_factor: float      # gross_profit / abs(gross_loss)

    # Risk-adjusted metrics
    sharpe_ratio: float       # annualized, risk-free=0
    sortino_ratio: float      # downside deviation only
    calmar_ratio: float       # net_profit / max_drawdown

    # Drawdown
    max_drawdown_pct: float
    max_drawdown_duration_bars: int
    avg_drawdown_pct: float

    # Expectancy
    avg_win: Decimal
    avg_loss: Decimal
    avg_r_multiple: float     # average trade in R-multiples (key metric)
    expectancy_per_trade: Decimal

    # Trade quality
    avg_hold_bars: float
    avg_entry_efficiency: float   # how close to optimal entry (0-1)
    largest_win: Decimal
    largest_loss: Decimal


def compute_stats(
    trades: list[BacktestTrade],
    equity_curve: list[tuple[date, Decimal]],
    config: BacktestConfig,
) -> BacktestStats:
    """Compute all statistics from trade list and equity curve."""
    zero = Decimal("0")

    if not trades:
        return BacktestStats(
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            win_rate=0.0,
            gross_profit=zero,
            gross_loss=zero,
            net_profit=zero,
            profit_factor=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            calmar_ratio=0.0,
            max_drawdown_pct=0.0,
            max_drawdown_duration_bars=0,
            avg_drawdown_pct=0.0,
            avg_win=zero,
            avg_loss=zero,
            avg_r_multiple=0.0,
            expectancy_per_trade=zero,
            avg_hold_bars=0.0,
            avg_entry_efficiency=0.0,
            largest_win=zero,
            largest_loss=zero,
        )

    # Trade stats
    winning = [t for t in trades if t.realized_pnl > zero]
    losing = [t for t in trades if t.realized_pnl <= zero]

    total = len(trades)
    n_win = len(winning)
    n_lose = len(losing)
    win_rate = n_win / total if total > 0 else 0.0

    gross_profit = sum((t.realized_pnl for t in winning), zero)
    gross_loss = sum((t.realized_pnl for t in losing), zero)
    net_profit = gross_profit + gross_loss

    profit_factor = (
        float(gross_profit / abs(gross_loss))
        if gross_loss != zero
        else (float("inf") if gross_profit > zero else 0.0)
    )

    avg_win = gross_profit / n_win if n_win > 0 else zero
    avg_loss = gross_loss / n_lose if n_lose > 0 else zero
    largest_win = max((t.realized_pnl for t in trades), default=zero)
    largest_loss = min((t.realized_pnl for t in trades), default=zero)

    avg_r = float(sum(t.r_multiple for t in trades) / total) if total > 0 else 0.0
    expectancy = net_profit / total if total > 0 else zero

    avg_hold = float(sum(t.hold_bars for t in trades) / total) if total > 0 else 0.0
    # Entry efficiency: use signal confidence as proxy (0-1)
    avg_entry_eff = float(sum(t.signal_confidence for t in trades) / total) if total > 0 else 0.0

    # Equity-curve based metrics
    equity_values = [float(v) for _, v in equity_curve]
    equity_arr = np.array(equity_values, dtype=float)

    max_dd_pct, max_dd_dur = compute_max_drawdown([Decimal(str(v)) for v in equity_values])
    avg_dd_pct = _compute_avg_drawdown(equity_arr)

    # Daily returns
    if len(equity_arr) > 1:
        returns = np.diff(equity_arr) / equity_arr[:-1]
        returns = returns[np.isfinite(returns)]
    else:
        returns = np.array([])

    sharpe = compute_sharpe(returns)
    sortino = compute_sortino(returns)
    calmar = float(net_profit) / float(config.initial_capital * Decimal(str(max_dd_pct))) if max_dd_pct > 0 else 0.0

    return BacktestStats(
        total_trades=total,
        winning_trades=n_win,
        losing_trades=n_lose,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_profit=net_profit,
        profit_factor=profit_factor,
        sharpe_ratio=sharpe,
        sortino_ratio=sortino,
        calmar_ratio=calmar,
        max_drawdown_pct=max_dd_pct,
        max_drawdown_duration_bars=max_dd_dur,
        avg_drawdown_pct=avg_dd_pct,
        avg_win=avg_win,
        avg_loss=avg_loss,
        avg_r_multiple=avg_r,
        expectancy_per_trade=expectancy,
        avg_hold_bars=avg_hold,
        avg_entry_efficiency=avg_entry_eff,
        largest_win=largest_win,
        largest_loss=largest_loss,
    )


def compute_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """Annualized Sharpe. Returns 0 if no variance."""
    if len(returns) < 2:
        return 0.0
    std = float(np.std(returns, ddof=1))
    if std < 1e-10:
        return 0.0
    mean = float(np.mean(returns))
    return float(mean / std * np.sqrt(periods_per_year))


def compute_sortino(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """Annualized Sortino using downside deviation."""
    if len(returns) < 2:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0:
        return float("inf") if float(np.mean(returns)) > 0 else 0.0
    downside_std = float(np.std(downside, ddof=1))
    if downside_std == 0:
        return 0.0
    mean = float(np.mean(returns))
    return float(mean / downside_std * np.sqrt(periods_per_year))


def compute_max_drawdown(equity_curve: list[Decimal]) -> tuple[float, int]:
    """Returns (max_dd_pct, max_dd_duration_bars)."""
    if len(equity_curve) < 2:
        return 0.0, 0

    values = [float(v) for v in equity_curve]
    peak = values[0]
    _peak_idx = 0
    max_dd = 0.0
    max_dur = 0
    in_dd_since = 0

    for i, v in enumerate(values):
        if v >= peak:
            peak = v
            _peak_idx = i
            in_dd_since = i
        else:
            dd = (peak - v) / peak if peak > 0 else 0.0
            dur = i - in_dd_since
            max_dd = max(max_dd, dd)
            max_dur = max(max_dur, dur)

    return max_dd, max_dur


def _compute_avg_drawdown(equity_arr: np.ndarray) -> float:
    """Compute average drawdown percentage across all drawdown periods."""
    if len(equity_arr) < 2:
        return 0.0
    peak = equity_arr[0]
    drawdowns = []
    for v in equity_arr:
        if v >= peak:
            peak = v
        else:
            dd = (peak - v) / peak if peak > 0 else 0.0
            drawdowns.append(dd)
    return float(np.mean(drawdowns)) if drawdowns else 0.0
