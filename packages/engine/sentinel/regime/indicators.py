"""Pure technical indicator functions. No side effects. Fully typed.

All functions accept pandas Series and return Series or scalar floats.
These are designed to be unit-testable in isolation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average True Range.

    TR = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = EMA(TR, period)
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(span=period, min_periods=period, adjust=False).mean()


def compute_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Average Directional Index (Wilder's smoothing).

    Returns ADX values as a Series (same index as inputs).
    """
    atr = compute_atr(high, low, close, period)

    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=high.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=high.index,
    )

    smoothed_plus = plus_dm.ewm(span=period, min_periods=period, adjust=False).mean()
    smoothed_minus = minus_dm.ewm(span=period, min_periods=period, adjust=False).mean()

    # Avoid division by zero
    plus_di = 100 * smoothed_plus / atr.replace(0, np.nan)
    minus_di = 100 * smoothed_minus / atr.replace(0, np.nan)

    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(span=period, min_periods=period, adjust=False).mean()
    return adx.fillna(0.0)


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (EMA with alpha=1/period)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    # When avg_loss is 0, RSI = 100 (pure uptrend). fillna(50) would mask this.
    rsi = avg_gain.copy()
    nonzero_loss = avg_loss != 0
    zero_loss = (avg_loss == 0) & avg_gain.notna() & avg_loss.notna()
    rs = avg_gain[nonzero_loss] / avg_loss[nonzero_loss]
    rsi = pd.Series(np.nan, index=close.index)
    rsi[nonzero_loss] = 100 - (100 / (1 + rs))
    rsi[zero_loss & (avg_gain > 0)] = 100.0
    rsi[zero_loss & (avg_gain == 0)] = 50.0
    return rsi.fillna(50.0)


def compute_bollinger_width(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.Series:
    """Bollinger Band width = (upper - lower) / middle, as a fraction."""
    sma = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    width = (upper - lower) / sma.replace(0, np.nan)
    return width.fillna(0.0)


def compute_vwap(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
) -> pd.Series:
    """Cumulative intraday VWAP = cumsum(typical_price * volume) / cumsum(volume)."""
    typical_price = (high + low + close) / 3
    cum_tp_vol = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def compute_vwap_deviation(close: pd.Series, vwap: pd.Series) -> pd.Series:
    """VWAP deviation as percentage: (close - vwap) / vwap * 100."""
    return (close - vwap) / vwap.replace(0, np.nan) * 100


def compute_ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, min_periods=period, adjust=False).mean()


def compute_volume_ratio(volume: pd.Series, avg_period: int = 20) -> pd.Series:
    """Volume relative to its rolling average. Values > 1 = above-average activity."""
    avg = volume.rolling(window=avg_period, min_periods=1).mean()
    return volume / avg.replace(0, np.nan)


def compute_price_efficiency(close: pd.Series, period: int = 14) -> float:
    """Kaufman Efficiency Ratio: directional movement / total path length.

    Values near 1.0 = highly efficient (trending), near 0 = choppy/ranging.
    Returns scalar float computed on the last `period` bars.
    """
    if len(close) < period + 1:
        return 0.5  # neutral when insufficient data

    recent = close.iloc[-(period + 1):]
    direction = abs(float(recent.iloc[-1]) - float(recent.iloc[0]))
    path = float(recent.diff().abs().sum())
    if path == 0:
        return 0.0
    return direction / path


def compute_hurst_exponent(close: pd.Series, max_lag: int = 20) -> float:
    """Estimate Hurst exponent via R/S analysis on log-returns.

    H > 0.5  → trending / persistent
    H ≈ 0.5  → random walk
    H < 0.5  → mean-reverting / anti-persistent
    """
    if len(close) < max_lag * 2:
        return 0.5  # neutral when insufficient data

    log_returns = np.log(close / close.shift(1)).dropna().values
    n = len(log_returns)
    if n < max_lag * 2:
        return 0.5

    # Scale lag range to available data — accurate R/S requires multiple scales.
    # max_lag is a minimum-data threshold, not an upper lag bound.
    upper_lag = max(n // 4, max_lag)
    upper_lag = min(upper_lag, n // 2)  # ensure at least 2 segments per lag
    if upper_lag < 4:
        return 0.5

    n_points = min(12, upper_lag - 1)
    lags_list = sorted(set(
        max(2, int(round(2 ** (i * math.log2(upper_lag) / n_points))))
        for i in range(1, n_points + 1)
    ))
    rs_values: list[tuple[float, float]] = []

    for lag in lags_list:
        segments = n // lag
        if segments < 2:
            continue
        rs_list = []
        for i in range(segments):
            seg = log_returns[i * lag : (i + 1) * lag]
            mean_seg = np.mean(seg)
            deviation = np.cumsum(seg - mean_seg)
            r = deviation.max() - deviation.min()
            s = np.std(seg, ddof=1)
            if s > 0:
                rs_list.append(r / s)
        if rs_list:
            rs_values.append((math.log(lag), math.log(np.mean(rs_list))))

    if len(rs_values) < 2:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])
    # Linear regression slope = Hurst exponent
    coeffs = np.polyfit(x, y, 1)
    h = float(coeffs[0])
    # Clamp to [0, 1]
    return max(0.0, min(1.0, h))
