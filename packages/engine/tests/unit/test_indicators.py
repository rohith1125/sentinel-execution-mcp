"""
Unit tests for technical indicator functions.

Tests verify mathematical correctness, edge cases, and expected
behavior against known data series.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from sentinel.regime.indicators import (
    compute_adx,
    compute_atr,
    compute_bollinger_width,
    compute_ema,
    compute_hurst_exponent,
    compute_price_efficiency,
    compute_rsi,
    compute_volume_ratio,
    compute_vwap,
)


def _series(values: list[float], name: str = "close") -> pd.Series:
    return pd.Series(values, name=name, dtype=float)


def _ohlcv(
    n: int = 50,
    base: float = 100.0,
    drift: float = 0.001,
    vol: float = 0.01,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Generate synthetic OHLCV data for indicator testing."""
    rng = np.random.default_rng(seed=42)
    close = [base]
    for _ in range(n - 1):
        close.append(close[-1] * (1 + drift + rng.normal(0, vol)))
    close = pd.Series(close, dtype=float)
    high = close * (1 + rng.uniform(0.001, 0.005, n))
    low = close * (1 - rng.uniform(0.001, 0.005, n))
    volume = pd.Series(rng.integers(100_000, 1_000_000, n), dtype=float)
    return high, low, close, volume


# ---------------------------------------------------------------------------
# compute_atr
# ---------------------------------------------------------------------------


class TestComputeATR:
    def test_returns_series_same_length(self):
        high, low, close, _ = _ohlcv(50)
        atr = compute_atr(high, low, close)
        assert len(atr) == 50

    def test_values_are_non_negative(self):
        high, low, close, _ = _ohlcv(50)
        atr = compute_atr(high, low, close)
        assert (atr.dropna() >= 0).all()

    def test_atr_positive_for_volatile_data(self):
        high, low, close, _ = _ohlcv(50, vol=0.02)
        atr = compute_atr(high, low, close)
        # Last value should be positive and non-trivial
        assert float(atr.iloc[-1]) > 0

    def test_atr_zero_for_flat_data(self):
        n = 30
        close = _series([100.0] * n)
        high = _series([100.0] * n)
        low = _series([100.0] * n)
        atr = compute_atr(high, low, close)
        # For perfectly flat data, ATR should be 0
        valid = atr.dropna()
        assert (valid == 0.0).all()

    def test_larger_range_gives_larger_atr(self):
        high1, low1, close1, _ = _ohlcv(50, vol=0.005)
        high2, low2, close2, _ = _ohlcv(50, vol=0.03)
        atr1 = float(compute_atr(high1, low1, close1).dropna().mean())
        atr2 = float(compute_atr(high2, low2, close2).dropna().mean())
        assert atr2 > atr1


# ---------------------------------------------------------------------------
# compute_adx
# ---------------------------------------------------------------------------


class TestComputeADX:
    def test_returns_series_same_length(self):
        high, low, close, _ = _ohlcv(60)
        adx = compute_adx(high, low, close)
        assert len(adx) == 60

    def test_trending_data_has_higher_adx(self):
        n = 60
        # Strong uptrend
        close_trend = pd.Series([100 + i * 0.5 for i in range(n)], dtype=float)
        high_trend = close_trend * 1.003
        low_trend = close_trend * 0.997

        # Choppy
        rng = np.random.default_rng(42)
        close_choppy = pd.Series(100 + rng.normal(0, 0.3, n).cumsum(), dtype=float).abs() + 50
        high_choppy = close_choppy * 1.003
        low_choppy = close_choppy * 0.997

        adx_trend = float(compute_adx(high_trend, low_trend, close_trend).iloc[-1])
        adx_choppy = float(compute_adx(high_choppy, low_choppy, close_choppy).iloc[-1])
        assert adx_trend > adx_choppy

    def test_adx_bounded_0_to_100(self):
        high, low, close, _ = _ohlcv(80)
        adx = compute_adx(high, low, close)
        valid = adx.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_fillna_produces_no_nan(self):
        high, low, close, _ = _ohlcv(50)
        adx = compute_adx(high, low, close)
        assert not adx.isna().any()


# ---------------------------------------------------------------------------
# compute_rsi
# ---------------------------------------------------------------------------


class TestComputeRSI:
    def test_returns_series_same_length(self):
        close = _series([100 + i * 0.1 for i in range(50)])
        rsi = compute_rsi(close)
        assert len(rsi) == 50

    def test_rsi_bounded_0_to_100(self):
        high, _, close, _ = _ohlcv(50)
        rsi = compute_rsi(close)
        assert (rsi >= 0).all()
        assert (rsi <= 100).all()

    def test_persistent_uptrend_yields_high_rsi(self):
        close = _series([100 + i * 1.0 for i in range(50)])  # constant rise
        rsi = compute_rsi(close)
        # Should be well above 70 in a pure uptrend
        assert float(rsi.iloc[-1]) > 70

    def test_persistent_downtrend_yields_low_rsi(self):
        close = _series([200 - i * 1.0 for i in range(50)])  # constant decline
        rsi = compute_rsi(close)
        assert float(rsi.iloc[-1]) < 30

    def test_flat_data_rsi_is_50(self):
        close = _series([100.0] * 50)
        rsi = compute_rsi(close)
        # No gains, no losses — fillna(50) applies
        assert (rsi == 50.0).all()

    def test_no_nan_output(self):
        high, _, close, _ = _ohlcv(50)
        rsi = compute_rsi(close)
        assert not rsi.isna().any()


# ---------------------------------------------------------------------------
# compute_bollinger_width
# ---------------------------------------------------------------------------


class TestComputeBollingerWidth:
    def test_returns_series_same_length(self):
        close = _series([100 + i * 0.1 for i in range(50)])
        bb = compute_bollinger_width(close)
        assert len(bb) == 50

    def test_volatile_data_has_wider_bands(self):
        close_low_vol = _series([100 + i * 0.01 for i in range(50)])
        rng = np.random.default_rng(42)
        close_high_vol = _series((100 + rng.normal(0, 3, 50).cumsum()).tolist())

        bb_low = float(compute_bollinger_width(close_low_vol).dropna().mean())
        bb_high = float(compute_bollinger_width(close_high_vol).dropna().mean())
        assert bb_high > bb_low

    def test_flat_data_has_near_zero_width(self):
        close = _series([100.0] * 50)
        bb = compute_bollinger_width(close)
        valid = bb.dropna()
        assert (valid == 0.0).all()

    def test_width_is_non_negative(self):
        high, _, close, _ = _ohlcv(50)
        bb = compute_bollinger_width(close)
        assert (bb >= 0).all()


# ---------------------------------------------------------------------------
# compute_vwap
# ---------------------------------------------------------------------------


class TestComputeVWAP:
    def test_returns_series_same_length(self):
        high, low, close, volume = _ohlcv(30)
        vwap = compute_vwap(high, low, close, volume)
        assert len(vwap) == 30

    def test_vwap_between_low_and_high(self):
        high, low, close, volume = _ohlcv(30)
        vwap = compute_vwap(high, low, close, volume)
        # Cumulative VWAP is a session average, not bounded per-bar.
        # Check that the final VWAP is within the full session's H/L range.
        assert float(vwap.iloc[-1]) >= float(low.min())
        assert float(vwap.iloc[-1]) <= float(high.max())

    def test_equal_volume_vwap_is_average_typical_price(self):
        n = 10
        high = _series([105.0] * n)
        low = _series([95.0] * n)
        close = _series([100.0] * n)
        volume = _series([1000.0] * n)
        vwap = compute_vwap(high, low, close, volume)
        expected_tp = (105 + 95 + 100) / 3  # = 100.0
        assert abs(float(vwap.iloc[-1]) - expected_tp) < 1e-6

    def test_zero_volume_handled(self):
        n = 5
        high = _series([105.0] * n)
        low = _series([95.0] * n)
        close = _series([100.0] * n)
        volume = _series([0.0] * n)
        vwap = compute_vwap(high, low, close, volume)
        # Should not raise, even with zero volume (handled via replace)
        assert len(vwap) == n


# ---------------------------------------------------------------------------
# compute_ema
# ---------------------------------------------------------------------------


class TestComputeEMA:
    def test_ema_length_matches_input(self):
        close = _series([float(i) for i in range(50)])
        ema = compute_ema(close, 20)
        assert len(ema) == 50

    def test_ema_lags_close_in_uptrend(self):
        close = _series([float(i) for i in range(50)])
        ema = compute_ema(close, 10)
        # EMA should lag below rising close
        valid = ema.dropna()
        assert float(close.iloc[-1]) > float(valid.iloc[-1])

    def test_shorter_ema_more_responsive(self):
        """EMA-5 reacts faster than EMA-20 to recent price change."""
        n = 50
        close_vals = [100.0] * 30 + [150.0] * 20  # step jump at bar 30
        close = _series(close_vals)
        ema5 = compute_ema(close, 5)
        ema20 = compute_ema(close, 20)
        # After jump, EMA-5 should be closer to 150 than EMA-20
        assert float(ema5.iloc[-1]) > float(ema20.iloc[-1])


# ---------------------------------------------------------------------------
# compute_price_efficiency
# ---------------------------------------------------------------------------


class TestComputePriceEfficiency:
    def test_trending_data_has_high_efficiency(self):
        close = _series([100 + i * 1.0 for i in range(50)])  # linear uptrend
        efficiency = compute_price_efficiency(close)
        assert efficiency > 0.8  # nearly perfect efficiency

    def test_choppy_data_has_low_efficiency(self):
        # Alternating up/down, net movement near zero
        vals = [100.0]
        for i in range(49):
            direction = 1 if i % 2 == 0 else -1
            vals.append(vals[-1] + direction * 0.5)
        close = _series(vals)
        efficiency = compute_price_efficiency(close)
        assert efficiency < 0.3

    def test_returns_neutral_for_short_series(self):
        close = _series([100.0, 101.0, 99.0])
        efficiency = compute_price_efficiency(close, period=14)
        assert efficiency == 0.5  # neutral default

    def test_returns_float(self):
        close = _series([100 + i for i in range(30)])
        result = compute_price_efficiency(close)
        assert isinstance(result, float)

    def test_bounded_0_to_1(self):
        high, _, close, _ = _ohlcv(50)
        efficiency = compute_price_efficiency(close)
        assert 0.0 <= efficiency <= 1.0


# ---------------------------------------------------------------------------
# compute_hurst_exponent
# ---------------------------------------------------------------------------


class TestComputeHurstExponent:
    def test_returns_float(self):
        close = _series([100 + i * 0.1 + i * i * 0.001 for i in range(100)])
        h = compute_hurst_exponent(close)
        assert isinstance(h, float)

    def test_bounded_0_to_1(self):
        high, _, close, _ = _ohlcv(100)
        h = compute_hurst_exponent(close)
        assert 0.0 <= h <= 1.0

    def test_random_walk_near_half(self):
        """Geometric Brownian Motion should yield Hurst near 0.5."""
        rng = np.random.default_rng(42)
        log_returns = rng.normal(0, 0.01, 500)
        prices = [100.0]
        for r in log_returns:
            prices.append(prices[-1] * math.exp(r))
        close = _series(prices)
        h = compute_hurst_exponent(close, max_lag=20)
        # Random walk: expect Hurst in [0.3, 0.7] range
        assert 0.3 < h < 0.7

    def test_trending_series_has_high_hurst(self):
        """Persistent trend → Hurst > 0.5."""
        close = _series([100 + i * 0.5 + (i * 0.01) ** 2 for i in range(200)])
        h = compute_hurst_exponent(close)
        assert h > 0.5

    def test_mean_reverting_series_has_low_hurst(self):
        """Anti-persistent (mean-reverting) → Hurst < 0.5."""
        # Strong mean-reverting process
        vals = [100.0]
        for i in range(199):
            mean_pull = (100.0 - vals[-1]) * 0.4  # strong pull toward 100
            noise = ((i * 7 + 3) % 20 - 10) * 0.02
            vals.append(vals[-1] + mean_pull + noise)
        close = _series(vals)
        h = compute_hurst_exponent(close)
        assert h < 0.5

    def test_short_series_returns_neutral(self):
        close = _series([100.0, 101.0, 99.0, 100.5, 98.5])
        h = compute_hurst_exponent(close)
        assert h == 0.5  # neutral default

    def test_no_nan_or_inf(self):
        high, _, close, _ = _ohlcv(100)
        h = compute_hurst_exponent(close)
        assert not math.isnan(h)
        assert not math.isinf(h)


# ---------------------------------------------------------------------------
# compute_volume_ratio
# ---------------------------------------------------------------------------


class TestComputeVolumeRatio:
    def test_returns_series_same_length(self):
        volume = _series([float(v) for v in range(100_000, 600_000, 10_000)])
        ratio = compute_volume_ratio(volume)
        assert len(ratio) == len(volume)

    def test_constant_volume_ratio_is_one(self):
        volume = _series([500_000.0] * 40)
        ratio = compute_volume_ratio(volume, avg_period=20)
        # After warm-up period, ratio should be 1.0
        valid = ratio.iloc[20:]
        assert (abs(valid - 1.0) < 1e-6).all()

    def test_spike_in_volume_shows_high_ratio(self):
        base = [500_000.0] * 30
        spike = [5_000_000.0]  # 10x normal
        volume = _series(base + spike)
        ratio = compute_volume_ratio(volume, avg_period=20)
        assert float(ratio.iloc[-1]) > 5.0  # should be significantly above average

    def test_no_nan_with_min_periods_one(self):
        volume = _series([100_000.0] * 30)
        ratio = compute_volume_ratio(volume, avg_period=20)
        assert not ratio.isna().any()
