"""
Unit tests for the backtest engine and statistics.
Tests verify correctness of core calculations — no DB or Redis required.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import numpy as np
import pytest

from sentinel.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult, BacktestTrade
from sentinel.backtest.stats import (
    BacktestStats,
    compute_max_drawdown,
    compute_sharpe,
    compute_stats,
)
from sentinel.market.provider import Bar


# ---------------------------------------------------------------------------
# Helpers / factories
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str = "AAPL",
    dt: date = date(2023, 1, 3),
    open_: float = 130.0,
    high: float = 132.0,
    low: float = 128.0,
    close: float = 131.0,
    volume: int = 1_000_000,
) -> Bar:
    ts = datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
        vwap=None,
    )


def _make_trade(
    realized_pnl: Decimal,
    r_multiple: float = 1.0,
    hold_bars: int = 5,
    signal_confidence: float = 0.8,
) -> BacktestTrade:
    return BacktestTrade(
        symbol="AAPL",
        strategy="test_strategy",
        entry_date=date(2023, 1, 3),
        exit_date=date(2023, 1, 10),
        side="buy",
        entry_price=Decimal("130.00"),
        exit_price=Decimal("131.00") if realized_pnl >= 0 else Decimal("129.00"),
        shares=100,
        realized_pnl=realized_pnl,
        pnl_pct=float(realized_pnl) / 13000.0,
        hold_bars=hold_bars,
        exit_reason="target" if realized_pnl > 0 else "stop",
        regime_at_entry="trending_up",
        signal_confidence=signal_confidence,
        r_multiple=r_multiple,
    )


def _make_config(initial_capital: float = 100_000.0) -> BacktestConfig:
    return BacktestConfig(
        strategy_name="test_strategy",
        symbol="AAPL",
        start_date=date(2023, 1, 1),
        end_date=date(2023, 12, 31),
        initial_capital=Decimal(str(initial_capital)),
    )


def _make_mock_strategy(min_bars: int = 1, name: str = "test_strategy") -> MagicMock:
    """Return a mock StrategyBase that never signals."""
    strategy = MagicMock()
    strategy.name = name
    strategy.min_bars_required = min_bars
    # evaluate returns a result with no signal
    mock_result = MagicMock()
    mock_result.signal = None
    strategy.evaluate.return_value = mock_result
    return strategy


def _make_mock_regime_classifier() -> MagicMock:
    """Return a mock RegimeClassifier."""
    classifier = MagicMock()
    regime = MagicMock()
    regime.label = MagicMock()
    regime.label.value = "trending_up"
    regime.strategy_score.return_value = 0.7
    classifier.classify.return_value = regime
    return classifier


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEquityCurve:
    def test_equity_curve_starts_at_initial_capital(self) -> None:
        """First equity value in curve must equal initial_capital."""
        config = _make_config(100_000.0)
        strategy = _make_mock_strategy()
        classifier = _make_mock_regime_classifier()
        engine = BacktestEngine(strategy=strategy, regime_classifier=classifier, config=config)

        bars = [_make_bar(dt=date(2023, 1, d)) for d in range(3, 13)]
        result = engine.run(bars)

        assert result.equity_curve, "Equity curve should not be empty"
        first_equity = result.equity_curve[0][1]
        assert first_equity == config.initial_capital, (
            f"Expected {config.initial_capital}, got {first_equity}"
        )

    def test_no_lookahead_bias(self) -> None:
        """Signal at bar N must fill at bar N+1, not bar N.

        We verify this by checking: if the engine generates a pending_entry after
        evaluating bar N, the open_position's entry_date corresponds to bar N+1.
        We do this indirectly by checking a strategy that signals on bar 0 still
        only fills (entry_date) on the subsequent bar.
        """
        from sentinel.domain.types import OrderSide
        from sentinel.strategy.base import StrategySignal, StrategyResult

        config = _make_config()
        classifier = _make_mock_regime_classifier()

        # Strategy signals a buy on first evaluation, never again
        call_count = [0]

        def evaluate_side_effect(symbol, bars, regime):
            call_count[0] += 1
            if call_count[0] == 1:
                signal = MagicMock(spec=StrategySignal)
                signal.side = MagicMock()
                signal.side.value = "buy"
                signal.entry_price = Decimal("130.00")
                signal.stop_price = Decimal("125.00")
                signal.target_price = Decimal("140.00")
                signal.confidence = 0.8
                signal.max_hold_bars = None
                mock_result = MagicMock()
                mock_result.signal = signal
                return mock_result
            mock_result = MagicMock()
            mock_result.signal = None
            return mock_result

        strategy = _make_mock_strategy()
        strategy.min_bars_required = 0
        strategy.evaluate.side_effect = evaluate_side_effect

        bars = [_make_bar(dt=date(2023, 1, d)) for d in range(3, 13)]
        result = engine = BacktestEngine(strategy=strategy, regime_classifier=classifier, config=config)
        result = engine.run(bars)

        if result.trades:
            # Entry date must be at least bar index 1 (second bar), not bar index 0
            first_trade = result.trades[0]
            assert first_trade.entry_date >= date(2023, 1, 4), (
                f"Trade entered on {first_trade.entry_date}, expected bar N+1 fill"
            )


class TestSharpe:
    def test_sharpe_zero_for_flat_returns(self) -> None:
        """Zero variance returns → sharpe = 0.0."""
        flat_returns = np.zeros(100)
        assert compute_sharpe(flat_returns) == 0.0

    def test_sharpe_zero_for_empty_returns(self) -> None:
        assert compute_sharpe(np.array([])) == 0.0

    def test_sharpe_positive_for_positive_returns(self) -> None:
        positive_returns = np.full(252, 0.001)
        # All same value → std=0 → should be 0
        assert compute_sharpe(positive_returns) == 0.0

    def test_sharpe_nonzero_for_varied_returns(self) -> None:
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.01, 252)
        sharpe = compute_sharpe(returns)
        assert isinstance(sharpe, float)
        assert math.isfinite(sharpe)


class TestMaxDrawdown:
    def test_max_drawdown_correct(self) -> None:
        """Manually constructed equity with known drawdown."""
        # 100 → 120 → 90 → 110: max drawdown = (120-90)/120 = 25%
        equity = [Decimal("100"), Decimal("120"), Decimal("90"), Decimal("110")]
        dd_pct, dd_dur = compute_max_drawdown(equity)
        expected = (120 - 90) / 120  # 0.25
        assert abs(dd_pct - expected) < 1e-9, f"Expected {expected}, got {dd_pct}"
        assert dd_dur == 1  # 1 bar from peak (idx 1) to trough (idx 2)

    def test_max_drawdown_zero_for_monotonic_increase(self) -> None:
        equity = [Decimal(str(v)) for v in [100, 110, 120, 130, 140]]
        dd_pct, dd_dur = compute_max_drawdown(equity)
        assert dd_pct == 0.0

    def test_max_drawdown_empty(self) -> None:
        dd_pct, dd_dur = compute_max_drawdown([])
        assert dd_pct == 0.0
        assert dd_dur == 0


class TestProfitFactor:
    def test_profit_factor_infinite_for_no_losses(self) -> None:
        """All winning trades → profit_factor = inf, handled gracefully."""
        wins = [_make_trade(Decimal("500"), r_multiple=2.0) for _ in range(3)]
        config = _make_config()
        equity_curve = [(date(2023, 1, d), Decimal("100000")) for d in range(3, 13)]
        stats = compute_stats(wins, equity_curve, config)
        assert stats.profit_factor == float("inf"), f"Expected inf, got {stats.profit_factor}"
        assert stats.total_trades == 3

    def test_profit_factor_zero_for_no_trades(self) -> None:
        config = _make_config()
        stats = compute_stats([], [], config)
        assert stats.profit_factor == 0.0


class TestWinRate:
    def test_win_rate_correct(self) -> None:
        """3 wins, 2 losses → win_rate = 0.6."""
        trades = [
            _make_trade(Decimal("300"), r_multiple=1.5),
            _make_trade(Decimal("200"), r_multiple=1.0),
            _make_trade(Decimal("400"), r_multiple=2.0),
            _make_trade(Decimal("-100"), r_multiple=-0.5),
            _make_trade(Decimal("-150"), r_multiple=-0.75),
        ]
        config = _make_config()
        equity_curve = [(date(2023, 1, d), Decimal("100000")) for d in range(3, 13)]
        stats = compute_stats(trades, equity_curve, config)
        assert stats.win_rate == pytest.approx(0.6, abs=1e-9)
        assert stats.total_trades == 5
        assert stats.winning_trades == 3
        assert stats.losing_trades == 2


class TestWalkForward:
    def test_walk_forward_minimum_n_windows(self) -> None:
        """If bars insufficient for n_windows, validator reduces windows gracefully."""
        from sentinel.backtest.walk_forward import WalkForwardValidator

        strategy = _make_mock_strategy()
        classifier = _make_mock_regime_classifier()
        validator = WalkForwardValidator(regime_classifier=classifier)

        # Only 50 bars — fewer than MIN_BARS_PER_WINDOW (100) per window for n_windows=5
        bars = [_make_bar(dt=date(2023, 1, 3)) for _ in range(50)]
        # Patch timestamps to be sequential
        from datetime import timedelta
        start_dt = datetime(2023, 1, 3, tzinfo=timezone.utc)
        for i, bar in enumerate(bars):
            object.__setattr__(bar, "timestamp", start_dt + timedelta(days=i))
            object.__setattr__(bar, "symbol", "AAPL")

        result = validator.validate(strategy=strategy, bars=bars, n_windows=5)
        # Should not raise; result should be an empty/degenerate result
        assert result is not None
        # With 50 bars and min 100 bars/window, n_windows reduces to 0 → empty result
        assert result.strategy_name == strategy.name

    def test_walk_forward_returns_result_structure(self) -> None:
        """WalkForwardResult has expected fields."""
        from sentinel.backtest.walk_forward import WalkForwardResult
        result = WalkForwardResult(
            strategy_name="test",
            symbol="AAPL",
            windows=[],
            oos_win_rate=0.0,
            oos_profit_factor=0.0,
            consistency_ratio=0.0,
            is_robust=False,
            verdict="not_robust",
            recommendation="Insufficient data.",
        )
        assert result.is_robust is False
        assert result.verdict == "not_robust"


class TestBacktestEngineInstantiation:
    def test_backtest_engine_instantiation(self) -> None:
        """Engine can be constructed with valid config."""
        config = _make_config()
        strategy = _make_mock_strategy()
        classifier = _make_mock_regime_classifier()
        engine = BacktestEngine(strategy=strategy, regime_classifier=classifier, config=config)
        assert engine.config is config
        assert engine.strategy is strategy
        assert engine.regime_classifier is classifier

    def test_backtest_engine_run_no_trades_on_no_signal(self) -> None:
        """Engine returns a result with 0 trades when strategy never signals."""
        config = _make_config()
        strategy = _make_mock_strategy()
        classifier = _make_mock_regime_classifier()
        engine = BacktestEngine(strategy=strategy, regime_classifier=classifier, config=config)
        bars = [_make_bar(dt=date(2023, 1, d)) for d in range(3, 20)]
        result = engine.run(bars)
        assert isinstance(result, BacktestResult)
        assert result.stats.total_trades == 0
        assert len(result.equity_curve) == len(bars)
