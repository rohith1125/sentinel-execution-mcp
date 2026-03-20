"""
Walk-forward validation: tests strategy robustness across multiple out-of-sample periods.

Method:
- Divide data into N windows
- For each window: train on in-sample, test on out-of-sample
- Report how strategy performs on data it was NOT optimized for
- Consistency ratio: what % of OOS windows were profitable?

This is the primary guard against overfitting. A strategy that works in IS
but fails in OOS is curve-fitted and should NOT be promoted.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sentinel.market.provider import Bar
from sentinel.regime.classifier import RegimeClassifier
from sentinel.strategy.base import StrategyBase


@dataclass
class WalkForwardWindow:
    window_number: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    train_result: object   # BacktestResult (avoid circular at module level)
    test_result: object    # BacktestResult
    is_profitable_oos: bool


@dataclass
class WalkForwardResult:
    strategy_name: str
    symbol: str
    windows: list[WalkForwardWindow]
    oos_win_rate: float           # avg win rate across OOS windows
    oos_profit_factor: float      # avg profit factor across OOS windows
    consistency_ratio: float      # % of OOS windows that were profitable
    is_robust: bool               # consistency_ratio >= 0.6 AND oos_profit_factor >= 1.1
    verdict: str                  # "robust" | "marginal" | "not_robust"
    recommendation: str           # human-readable next step


class WalkForwardValidator:
    """Runs walk-forward validation for a strategy."""

    MIN_BARS_PER_WINDOW = 100

    def __init__(
        self,
        regime_classifier: RegimeClassifier | None = None,
    ) -> None:
        self.regime_classifier = regime_classifier or RegimeClassifier()

    def validate(
        self,
        strategy: StrategyBase,
        bars: list[Bar],
        n_windows: int = 5,
        train_pct: float = 0.7,
        config_overrides: dict | None = None,
    ) -> WalkForwardResult:
        """
        Split bars into N windows, run IS/OOS backtests, compute robustness metrics.
        Minimum bars per window: 100 (skip if insufficient data).
        """
        from sentinel.backtest.engine import BacktestConfig, BacktestEngine

        if not bars:
            return self._empty_result(strategy.name, "")

        symbol = bars[0].symbol
        total_bars = len(bars)
        window_size = total_bars // n_windows

        if window_size < self.MIN_BARS_PER_WINDOW:
            # Reduce n_windows to meet minimum
            n_windows = max(1, total_bars // self.MIN_BARS_PER_WINDOW)
            window_size = total_bars // n_windows

        if n_windows < 1:
            return self._empty_result(strategy.name, symbol)

        windows: list[WalkForwardWindow] = []

        for w in range(n_windows):
            start_idx = w * window_size
            end_idx = start_idx + window_size if w < n_windows - 1 else total_bars
            window_bars = bars[start_idx:end_idx]

            split = int(len(window_bars) * train_pct)
            train_bars = window_bars[:split]
            test_bars = window_bars[split:]

            if len(train_bars) < self.MIN_BARS_PER_WINDOW or len(test_bars) < 10:
                continue

            train_start = train_bars[0].timestamp.date()
            train_end = train_bars[-1].timestamp.date()
            test_start = test_bars[0].timestamp.date()
            test_end = test_bars[-1].timestamp.date()

            base_config = dict(
                strategy_name=strategy.name,
                symbol=symbol,
                start_date=train_start,
                end_date=train_end,
            )
            if config_overrides:
                base_config.update(config_overrides)

            train_config = BacktestConfig(**base_config)
            test_config = BacktestConfig(
                **{**base_config, "start_date": test_start, "end_date": test_end}
            )

            train_engine = BacktestEngine(strategy, self.regime_classifier, train_config)
            test_engine = BacktestEngine(strategy, self.regime_classifier, test_config)

            train_result = train_engine.run(train_bars)
            test_result = test_engine.run(test_bars)

            is_profitable = test_result.stats.net_profit > Decimal("0")

            windows.append(WalkForwardWindow(
                window_number=w + 1,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                train_result=train_result,
                test_result=test_result,
                is_profitable_oos=is_profitable,
            ))

        if not windows:
            return self._empty_result(strategy.name, symbol)

        oos_win_rates = [w.test_result.stats.win_rate for w in windows]
        oos_profit_factors = [w.test_result.stats.profit_factor for w in windows]
        profitable_windows = [w for w in windows if w.is_profitable_oos]

        oos_win_rate = sum(oos_win_rates) / len(oos_win_rates)
        # Filter out inf values for averaging
        finite_pfs = [pf for pf in oos_profit_factors if pf != float("inf")]
        oos_profit_factor = sum(finite_pfs) / len(finite_pfs) if finite_pfs else (
            float("inf") if oos_profit_factors else 0.0
        )
        consistency_ratio = len(profitable_windows) / len(windows)

        is_robust = consistency_ratio >= 0.6 and oos_profit_factor >= 1.1

        if is_robust:
            verdict = "robust"
            recommendation = (
                f"Strategy '{strategy.name}' is robust. "
                f"Consistency ratio {consistency_ratio:.0%} and profit factor {oos_profit_factor:.2f} "
                f"meet robustness thresholds. Consider promoting to paper trading."
            )
        elif consistency_ratio >= 0.4 or oos_profit_factor >= 1.0:
            verdict = "marginal"
            recommendation = (
                f"Strategy '{strategy.name}' shows marginal robustness. "
                f"Consistency ratio {consistency_ratio:.0%}, profit factor {oos_profit_factor:.2f}. "
                f"Collect more data or refine entry/exit logic before promotion."
            )
        else:
            verdict = "not_robust"
            recommendation = (
                f"Strategy '{strategy.name}' is NOT robust. "
                f"Consistency ratio {consistency_ratio:.0%}, profit factor {oos_profit_factor:.2f}. "
                f"Do NOT promote — likely curve-fitted to in-sample data."
            )

        return WalkForwardResult(
            strategy_name=strategy.name,
            symbol=symbol,
            windows=windows,
            oos_win_rate=oos_win_rate,
            oos_profit_factor=oos_profit_factor,
            consistency_ratio=consistency_ratio,
            is_robust=is_robust,
            verdict=verdict,
            recommendation=recommendation,
        )

    def _empty_result(self, strategy_name: str, symbol: str) -> WalkForwardResult:
        return WalkForwardResult(
            strategy_name=strategy_name,
            symbol=symbol,
            windows=[],
            oos_win_rate=0.0,
            oos_profit_factor=0.0,
            consistency_ratio=0.0,
            is_robust=False,
            verdict="not_robust",
            recommendation="Insufficient data to run walk-forward validation.",
        )
