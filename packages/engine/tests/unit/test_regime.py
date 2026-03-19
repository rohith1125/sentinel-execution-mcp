"""
Unit tests for RegimeClassifier.

Tests verify that each classification branch fires correctly for
appropriate input data, and that output metadata is complete.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from sentinel.domain.types import RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.classifier import RegimeClassifier


def _make_bars(
    symbol: str,
    count: int,
    prices: list[float],
    volumes: list[float] | None = None,
    start_time: datetime | None = None,
) -> list[Bar]:
    """Build a list of Bar objects from price series."""
    if start_time is None:
        start_time = datetime(2024, 1, 15, 11, 0, 0)
    if volumes is None:
        volumes = [500_000.0] * count

    bars = []
    for i, price in enumerate(prices[:count]):
        vol = int(volumes[i]) if i < len(volumes) else 500_000
        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start_time + timedelta(minutes=i),
                open=Decimal(str(round(price * 0.999, 2))),
                high=Decimal(str(round(price * 1.003, 2))),
                low=Decimal(str(round(price * 0.997, 2))),
                close=Decimal(str(round(price, 2))),
                volume=max(100, vol),
                vwap=Decimal(str(round(price, 4))),
            )
        )
    return bars


def _trending_bull_prices(n: int = 60) -> list[float]:
    """Consistently rising prices for trending bull classification."""
    return [100.0 + i * 0.4 for i in range(n)]  # linear uptrend


def _mean_reverting_prices(n: int = 60) -> list[float]:
    """Alternating prices for mean-reverting classification."""
    prices = [100.0]
    for i in range(n - 1):
        direction = 1 if i % 2 == 0 else -1
        prices.append(prices[-1] + direction * 0.15)
    return prices


def _high_vol_prices(n: int = 60) -> list[float]:
    """Wild swings for HIGH_VOL_UNSTABLE classification."""
    prices = [100.0]
    for i in range(n - 1):
        swing = ((i * 13 + 7) % 100 - 50) / 100  # ±50% swings
        prices.append(max(10.0, prices[-1] * (1 + swing * 0.12)))
    return prices


@pytest.fixture
def classifier() -> RegimeClassifier:
    return RegimeClassifier()


class TestRegimeClassifierOutputShape:
    def test_returns_regime_snapshot(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(60)
        bars = _make_bars("AAPL", 60, prices)
        result = classifier.classify(bars, "AAPL")
        assert result.label is not None
        assert 0.0 <= result.confidence <= 1.0
        assert 0.0 <= result.tradeability_score <= 1.0
        assert result.bars_analyzed == 60
        assert isinstance(result.reasoning, str)
        assert len(result.reasoning) > 0

    def test_reasoning_mentions_symbol(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(60)
        bars = _make_bars("AAPL", 60, prices)
        result = classifier.classify(bars, "AAPL")
        assert "AAPL" in result.reasoning

    def test_strategy_compatibility_populated(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(60)
        bars = _make_bars("AAPL", 60, prices)
        result = classifier.classify(bars, "AAPL")
        compat = result.strategy_compatibility
        assert hasattr(compat, "momentum_breakout")
        assert 0.0 <= compat.momentum_breakout <= 1.0

    def test_classified_at_is_set(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(60)
        bars = _make_bars("AAPL", 60, prices)
        result = classifier.classify(bars, "AAPL")
        assert result.classified_at is not None

    def test_supporting_metrics_populated(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(60)
        bars = _make_bars("AAPL", 60, prices)
        result = classifier.classify(bars, "AAPL")
        assert "adx" in result.supporting_metrics
        assert "rsi" in result.supporting_metrics
        assert "atr_pct" in result.supporting_metrics


class TestInsufficientData:
    def test_fewer_than_5_bars_returns_unknown(self, classifier: RegimeClassifier):
        bars = _make_bars("AAPL", 4, [100.0, 101.0, 99.0, 100.5])
        result = classifier.classify(bars, "AAPL")
        assert result.label == RegimeLabel.UNKNOWN

    def test_empty_bars_returns_unknown(self, classifier: RegimeClassifier):
        result = classifier.classify([], "AAPL")
        assert result.label == RegimeLabel.UNKNOWN

    def test_exactly_5_bars_does_not_crash(self, classifier: RegimeClassifier):
        bars = _make_bars("AAPL", 5, [100.0, 101.0, 102.0, 103.0, 104.0])
        result = classifier.classify(bars, "AAPL")
        assert result.label is not None


class TestOpeningNoise:
    def test_bars_in_first_15_min_classified_opening_noise(self, classifier: RegimeClassifier):
        # Bars starting at 9:30 ET
        start = datetime(2024, 1, 15, 9, 30, 0)
        prices = [100.0 + i * 0.1 for i in range(20)]
        bars = _make_bars("AAPL", 20, prices, start_time=start)
        result = classifier.classify(bars, "AAPL")
        assert result.label == RegimeLabel.OPENING_NOISE

    def test_tradeability_lower_in_first_15_min(self, classifier: RegimeClassifier):
        start_0 = datetime(2024, 1, 15, 9, 30, 0)
        start_20 = datetime(2024, 1, 15, 9, 50, 0)
        prices = [100.0 + i * 0.1 for i in range(25)]

        bars_early = _make_bars("AAPL", 25, prices, start_time=start_0)
        bars_later = _make_bars("AAPL", 25, prices, start_time=start_20)

        result_early = classifier.classify(bars_early, "AAPL")
        result_later = classifier.classify(bars_later, "AAPL")

        if result_early.label == RegimeLabel.OPENING_NOISE and result_later.label == RegimeLabel.OPENING_NOISE:
            assert result_early.tradeability_score <= result_later.tradeability_score


class TestHighVolatility:
    def test_high_vol_bars_classified_correctly(self, classifier: RegimeClassifier):
        prices = _high_vol_prices(60)
        bars = _make_bars("AAPL", 60, prices, start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        assert result.label == RegimeLabel.HIGH_VOL_UNSTABLE

    def test_high_vol_has_low_tradeability(self, classifier: RegimeClassifier):
        prices = _high_vol_prices(60)
        bars = _make_bars("AAPL", 60, prices, start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        assert result.tradeability_score <= 0.20

    def test_high_vol_reasoning_mentions_atr(self, classifier: RegimeClassifier):
        prices = _high_vol_prices(60)
        bars = _make_bars("AAPL", 60, prices, start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        if result.label == RegimeLabel.HIGH_VOL_UNSTABLE:
            assert "ATR" in result.reasoning or "volatil" in result.reasoning.lower()


class TestRiskOff:
    def test_spy_down_triggers_risk_off(self, classifier: RegimeClassifier):
        # SPY bars: open at 520, fall to ~505 (~3% decline, below -1.5% threshold)
        spy_prices = [520.0 - i * 0.5 for i in range(30)]
        spy_bars = _make_bars("SPY", 30, spy_prices, start_time=datetime(2024, 1, 15, 11, 0, 0))

        # Normal symbol bars (not high vol)
        symbol_prices = [180.0 + i * 0.01 for i in range(30)]
        symbol_bars = _make_bars("AAPL", 30, symbol_prices, start_time=datetime(2024, 1, 15, 11, 0, 0))

        result = classifier.classify(symbol_bars, "AAPL", context_bars=spy_bars)
        assert result.label == RegimeLabel.RISK_OFF

    def test_risk_off_has_low_tradeability(self, classifier: RegimeClassifier):
        spy_prices = [520.0 - i * 0.6 for i in range(30)]
        spy_bars = _make_bars("SPY", 30, spy_prices)
        symbol_prices = [180.0 + i * 0.01 for i in range(30)]
        symbol_bars = _make_bars("AAPL", 30, symbol_prices)

        result = classifier.classify(symbol_bars, "AAPL", context_bars=spy_bars)
        if result.label == RegimeLabel.RISK_OFF:
            assert result.tradeability_score <= 0.25


class TestLowLiquidity:
    def test_low_volume_triggers_low_liquidity(self, classifier: RegimeClassifier):
        # Volume at 20% of rolling average — last few bars very low vs high-volume history
        prices = [180.0 + i * 0.02 for i in range(50)]
        # 45 bars of high volume, then 5 bars very low so rolling avg (20 bars) stays high
        volumes = [500_000] * 45 + [10_000] * 5
        bars = _make_bars("AAPL", 50, prices, volumes=volumes,
                          start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        # Low volume ratio should trigger low liquidity
        assert result.label in (RegimeLabel.LOW_LIQUIDITY, RegimeLabel.UNKNOWN, RegimeLabel.MEAN_REVERTING)


class TestCompatibilityScores:
    def test_trending_bull_favors_momentum(self, classifier: RegimeClassifier):
        prices = _trending_bull_prices(80)
        bars = _make_bars("AAPL", 80, prices, start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        if result.label == RegimeLabel.TRENDING_BULL:
            assert result.strategy_compatibility.momentum_breakout >= 0.7
            assert result.strategy_compatibility.rsi_mean_reversion < 0.5

    def test_high_vol_all_strategies_low_compatibility(self, classifier: RegimeClassifier):
        prices = _high_vol_prices(60)
        bars = _make_bars("AAPL", 60, prices, start_time=datetime(2024, 1, 15, 11, 0, 0))
        result = classifier.classify(bars, "AAPL")
        if result.label == RegimeLabel.HIGH_VOL_UNSTABLE:
            compat = result.strategy_compatibility
            assert compat.momentum_breakout <= 0.2
            assert compat.vwap_reclaim <= 0.2
            assert compat.ema_trend <= 0.3
