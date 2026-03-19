"""RegimeClassifier — classifies market regime from price/volume bar history."""

from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal

import pandas as pd

from sentinel.domain.types import RegimeLabel
from sentinel.market.provider import Bar
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
    compute_vwap_deviation,
)
from sentinel.regime.models import RegimeSnapshot, StrategyCompatibility

# ---------------------------------------------------------------------------
# Compatibility tables
# ---------------------------------------------------------------------------

_COMPATIBILITY: dict[RegimeLabel, dict[str, float]] = {
    RegimeLabel.TRENDING_BULL: dict(
        momentum_breakout=0.9,
        vwap_reclaim=0.7,
        ema_trend=0.9,
        rsi_mean_reversion=0.3,
        atr_swing=0.7,
        orb=0.6,
    ),
    RegimeLabel.TRENDING_BEAR: dict(
        momentum_breakout=0.3,
        vwap_reclaim=0.4,
        ema_trend=0.8,
        rsi_mean_reversion=0.4,
        atr_swing=0.8,
        orb=0.3,
    ),
    RegimeLabel.MEAN_REVERTING: dict(
        momentum_breakout=0.2,
        vwap_reclaim=0.8,
        ema_trend=0.2,
        rsi_mean_reversion=0.9,
        atr_swing=0.3,
        orb=0.1,
    ),
    RegimeLabel.HIGH_VOL_UNSTABLE: dict(
        momentum_breakout=0.1,
        vwap_reclaim=0.1,
        ema_trend=0.2,
        rsi_mean_reversion=0.1,
        atr_swing=0.2,
        orb=0.1,
    ),
    RegimeLabel.LOW_LIQUIDITY: dict(
        momentum_breakout=0.1,
        vwap_reclaim=0.1,
        ema_trend=0.1,
        rsi_mean_reversion=0.1,
        atr_swing=0.1,
        orb=0.1,
    ),
    RegimeLabel.RISK_OFF: dict(
        momentum_breakout=0.2,
        vwap_reclaim=0.2,
        ema_trend=0.2,
        rsi_mean_reversion=0.2,
        atr_swing=0.2,
        orb=0.1,
    ),
    RegimeLabel.RISK_ON: dict(
        momentum_breakout=0.8,
        vwap_reclaim=0.7,
        ema_trend=0.8,
        rsi_mean_reversion=0.5,
        atr_swing=0.7,
        orb=0.6,
    ),
    RegimeLabel.OPENING_NOISE: dict(
        momentum_breakout=0.2,
        vwap_reclaim=0.3,
        ema_trend=0.1,
        rsi_mean_reversion=0.2,
        atr_swing=0.1,
        orb=0.6,
    ),
    RegimeLabel.EVENT_DISTORTED: dict(
        momentum_breakout=0.1,
        vwap_reclaim=0.1,
        ema_trend=0.1,
        rsi_mean_reversion=0.1,
        atr_swing=0.1,
        orb=0.1,
    ),
    RegimeLabel.UNKNOWN: dict(
        momentum_breakout=0.3,
        vwap_reclaim=0.3,
        ema_trend=0.3,
        rsi_mean_reversion=0.3,
        atr_swing=0.3,
        orb=0.3,
    ),
}

_TRADEABILITY: dict[RegimeLabel, float] = {
    RegimeLabel.TRENDING_BULL: 0.80,
    RegimeLabel.TRENDING_BEAR: 0.80,
    RegimeLabel.MEAN_REVERTING: 0.70,
    RegimeLabel.RISK_ON: 0.75,
    RegimeLabel.OPENING_NOISE: 0.40,
    RegimeLabel.HIGH_VOL_UNSTABLE: 0.15,
    RegimeLabel.LOW_LIQUIDITY: 0.10,
    RegimeLabel.RISK_OFF: 0.20,
    RegimeLabel.EVENT_DISTORTED: 0.10,
    RegimeLabel.UNKNOWN: 0.30,
}

# Thresholds
_ATR_PCT_HIGH_VOL = 2.5          # % of price
_ADX_TREND_THRESHOLD = 25.0
_HURST_MEAN_REVERTING = 0.45
_BB_WIDTH_MEAN_REV = 0.04        # tight bands
_VOL_RATIO_LOW_LIQ = 0.3
_SPY_RISK_OFF_PCT = -1.5         # SPY intraday change %
_MARKET_OPEN_MINUTES = 30        # opening noise window
_ET_OPEN = time(9, 30)


def _to_dataframe(bars: list[Bar]) -> pd.DataFrame:
    data = {
        "open": [float(b.open) for b in bars],
        "high": [float(b.high) for b in bars],
        "low": [float(b.low) for b in bars],
        "close": [float(b.close) for b in bars],
        "volume": [float(b.volume) for b in bars],
        "timestamp": [b.timestamp for b in bars],
    }
    df = pd.DataFrame(data)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp").sort_index()
    return df


def _make_compatibility(label: RegimeLabel) -> StrategyCompatibility:
    c = _COMPATIBILITY.get(label, _COMPATIBILITY[RegimeLabel.UNKNOWN])
    return StrategyCompatibility(**c)


class RegimeClassifier:
    """Classifies market regime from a list of Bar objects.

    Heuristic priority (first match wins):
    1. Opening noise check
    2. Volatility check (high ATR%)
    3. Risk-off context (SPY bars supplied and down > 1.5%)
    4. Liquidity check (volume ratio too low)
    5. Trend check (ADX > 25)
    6. Mean reversion check (Hurst < 0.45 + tight BB)
    7. Fallback via price efficiency ratio
    """

    def classify(
        self,
        bars: list[Bar],
        symbol: str,
        context_bars: list[Bar] | None = None,
    ) -> RegimeSnapshot:
        """Classify regime from bar history.

        Args:
            bars: Price/volume history for the symbol (intraday or daily).
            symbol: Ticker being classified (for logging/reasoning).
            context_bars: Optional SPY/QQQ bars for macro context (risk-off check).
        """
        if len(bars) < 5:
            return self._unknown(bars, symbol, "Insufficient bar data (< 5 bars)")

        df = _to_dataframe(bars)
        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        # --- Core indicator calculations ---
        atr_series = compute_atr(high, low, close)
        adx_series = compute_adx(high, low, close)
        rsi_series = compute_rsi(close)
        bb_width_series = compute_bollinger_width(close)
        vwap_series = compute_vwap(high, low, close, volume)
        vol_ratio_series = compute_volume_ratio(volume)
        ema_20 = compute_ema(close, 20)
        ema_50 = compute_ema(close, 50)
        ema_200 = compute_ema(close, 200)

        # Scalar values from last bar
        latest_close = float(close.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        atr_pct = (atr_val / latest_close * 100) if latest_close > 0 else 0.0
        adx_val = float(adx_series.iloc[-1]) if not adx_series.isna().iloc[-1] else 0.0
        rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.isna().iloc[-1] else 50.0
        bb_width_val = float(bb_width_series.iloc[-1]) if not bb_width_series.isna().iloc[-1] else 0.0
        vol_ratio_val = float(vol_ratio_series.iloc[-1]) if not vol_ratio_series.isna().iloc[-1] else 1.0

        ema_20_val = float(ema_20.iloc[-1]) if not ema_20.isna().iloc[-1] else latest_close
        ema_50_val = float(ema_50.iloc[-1]) if not ema_50.isna().iloc[-1] else latest_close
        ema_200_val = float(ema_200.iloc[-1]) if not ema_200.isna().iloc[-1] else latest_close

        vwap_val = float(vwap_series.iloc[-1]) if not vwap_series.isna().iloc[-1] else latest_close
        vwap_dev = float(compute_vwap_deviation(close, vwap_series).iloc[-1])

        hurst = compute_hurst_exponent(close)
        efficiency = compute_price_efficiency(close)

        metrics = {
            "atr": atr_val,
            "atr_pct": atr_pct,
            "adx": adx_val,
            "rsi": rsi_val,
            "bb_width": bb_width_val,
            "vol_ratio": vol_ratio_val,
            "ema_20": ema_20_val,
            "ema_50": ema_50_val,
            "ema_200": ema_200_val,
            "vwap": vwap_val,
            "vwap_dev_pct": vwap_dev,
            "hurst": hurst,
            "price_efficiency": efficiency,
            "latest_close": latest_close,
        }

        last_bar = bars[-1]

        # ---------------------------------------------------------------
        # Priority 1: Opening noise check
        # ---------------------------------------------------------------
        bar_time = last_bar.timestamp
        # Normalize to time-of-day (handle tz-aware/naive)
        if hasattr(bar_time, "tzinfo") and bar_time.tzinfo is not None:
            # Convert to ET offset naively by comparing hour
            # We check if this is within 30 min of 09:30 ET
            # Use UTC offset: ET = UTC-5 (EST) or UTC-4 (EDT)
            # Safe approach: just check the time portion in the bar's tz
            bar_time_local = bar_time
        else:
            bar_time_local = bar_time

        minutes_since_open = self._minutes_since_open(bar_time_local)
        if 0 <= minutes_since_open < _MARKET_OPEN_MINUTES:
            tradeability = 0.4
            # First 15 min even less tradeable
            if minutes_since_open < 15:
                tradeability = 0.25
            return self._make_result(
                label=RegimeLabel.OPENING_NOISE,
                confidence=0.85,
                tradeability=tradeability,
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Opening noise: {minutes_since_open:.0f} min since market open. "
                    f"Price action unreliable in first {_MARKET_OPEN_MINUTES} min."
                ),
            )

        # ---------------------------------------------------------------
        # Priority 2: High volatility check
        # ---------------------------------------------------------------
        if atr_pct > _ATR_PCT_HIGH_VOL:
            return self._make_result(
                label=RegimeLabel.HIGH_VOL_UNSTABLE,
                confidence=0.80,
                tradeability=_TRADEABILITY[RegimeLabel.HIGH_VOL_UNSTABLE],
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"High volatility: ATR% = {atr_pct:.2f}% exceeds {_ATR_PCT_HIGH_VOL}% threshold. "
                    f"Regime is unstable and dangerous."
                ),
            )

        # ---------------------------------------------------------------
        # Priority 3: Risk-off check (context bars = SPY/QQQ)
        # ---------------------------------------------------------------
        if context_bars and len(context_bars) >= 2:
            ctx_df = _to_dataframe(context_bars)
            ctx_open = float(ctx_df["open"].iloc[0])
            ctx_latest = float(ctx_df["close"].iloc[-1])
            if ctx_open > 0:
                spy_change_pct = (ctx_latest - ctx_open) / ctx_open * 100
                metrics["spy_intraday_pct"] = spy_change_pct
                if spy_change_pct < _SPY_RISK_OFF_PCT:
                    return self._make_result(
                        label=RegimeLabel.RISK_OFF,
                        confidence=0.85,
                        tradeability=_TRADEABILITY[RegimeLabel.RISK_OFF],
                        metrics=metrics,
                        bars=bars,
                        symbol=symbol,
                        reasoning=(
                            f"Risk-off: Market context (SPY/QQQ) down {spy_change_pct:.2f}% intraday "
                            f"(threshold: {_SPY_RISK_OFF_PCT}%). Broad selling pressure detected."
                        ),
                    )

        # ---------------------------------------------------------------
        # Priority 4: Liquidity check
        # ---------------------------------------------------------------
        if vol_ratio_val < _VOL_RATIO_LOW_LIQ:
            return self._make_result(
                label=RegimeLabel.LOW_LIQUIDITY,
                confidence=0.75,
                tradeability=_TRADEABILITY[RegimeLabel.LOW_LIQUIDITY],
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Low liquidity: Volume ratio = {vol_ratio_val:.2f} (< {_VOL_RATIO_LOW_LIQ}). "
                    f"Thin market conditions — avoid execution."
                ),
            )

        # ---------------------------------------------------------------
        # Priority 5: Trend check via ADX
        # ---------------------------------------------------------------
        if adx_val > _ADX_TREND_THRESHOLD:
            is_bull = latest_close > ema_200_val
            label = RegimeLabel.TRENDING_BULL if is_bull else RegimeLabel.TRENDING_BEAR
            direction = "bullish" if is_bull else "bearish"
            confidence = min(0.95, 0.60 + (adx_val - _ADX_TREND_THRESHOLD) / 100)
            return self._make_result(
                label=label,
                confidence=confidence,
                tradeability=_TRADEABILITY[label],
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Trending {direction}: ADX = {adx_val:.1f} (> {_ADX_TREND_THRESHOLD}). "
                    f"Price {'above' if is_bull else 'below'} EMA200 ({ema_200_val:.2f}). "
                    f"EMA20={ema_20_val:.2f}, EMA50={ema_50_val:.2f}."
                ),
            )

        # ---------------------------------------------------------------
        # Priority 6: Mean reversion check
        # ---------------------------------------------------------------
        if hurst < _HURST_MEAN_REVERTING and bb_width_val < _BB_WIDTH_MEAN_REV:
            confidence = min(0.90, 0.55 + (_HURST_MEAN_REVERTING - hurst) * 2)
            return self._make_result(
                label=RegimeLabel.MEAN_REVERTING,
                confidence=confidence,
                tradeability=_TRADEABILITY[RegimeLabel.MEAN_REVERTING],
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Mean-reverting: Hurst exponent = {hurst:.3f} (< {_HURST_MEAN_REVERTING}), "
                    f"BB width = {bb_width_val:.4f} (< {_BB_WIDTH_MEAN_REV}). "
                    f"Price oscillation with tight bands."
                ),
            )

        # Partial mean-reversion: Hurst alone
        if hurst < _HURST_MEAN_REVERTING:
            return self._make_result(
                label=RegimeLabel.MEAN_REVERTING,
                confidence=0.55,
                tradeability=_TRADEABILITY[RegimeLabel.MEAN_REVERTING],
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Likely mean-reverting: Hurst exponent = {hurst:.3f} (< {_HURST_MEAN_REVERTING}). "
                    f"BB width = {bb_width_val:.4f} (no tight band confirmation)."
                ),
            )

        # ---------------------------------------------------------------
        # Priority 7: Classify by price efficiency
        # ---------------------------------------------------------------
        if efficiency > 0.6:
            # Efficient upward or downward movement
            is_bull = latest_close > ema_50_val
            label = RegimeLabel.TRENDING_BULL if is_bull else RegimeLabel.TRENDING_BEAR
            return self._make_result(
                label=label,
                confidence=0.55,
                tradeability=_TRADEABILITY[label] * 0.9,
                metrics=metrics,
                bars=bars,
                symbol=symbol,
                reasoning=(
                    f"Price efficiency = {efficiency:.3f} (> 0.6) suggests {'uptrend' if is_bull else 'downtrend'}. "
                    f"ADX below threshold ({adx_val:.1f}) but directional movement detected."
                ),
            )

        # Default unknown
        return self._unknown(
            bars,
            symbol,
            f"No dominant regime signal. ADX={adx_val:.1f}, Hurst={hurst:.3f}, "
            f"ATR%={atr_pct:.2f}%, efficiency={efficiency:.3f}.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_result(
        self,
        label: RegimeLabel,
        confidence: float,
        tradeability: float,
        metrics: dict[str, float],
        bars: list[Bar],
        symbol: str,
        reasoning: str,
    ) -> RegimeSnapshot:
        return RegimeSnapshot(
            label=label,
            confidence=round(min(1.0, max(0.0, confidence)), 4),
            tradeability_score=round(min(1.0, max(0.0, tradeability)), 4),
            supporting_metrics=metrics,
            strategy_compatibility=_make_compatibility(label),
            classified_at=datetime.now(tz=timezone.utc),
            bars_analyzed=len(bars),
            reasoning=f"[{symbol}] {reasoning}",
        )

    def _unknown(self, bars: list[Bar], symbol: str, reason: str) -> RegimeSnapshot:
        metrics: dict[str, float] = {"bars_count": float(len(bars))}
        return RegimeSnapshot(
            label=RegimeLabel.UNKNOWN,
            confidence=0.3,
            tradeability_score=_TRADEABILITY[RegimeLabel.UNKNOWN],
            supporting_metrics=metrics,
            strategy_compatibility=_make_compatibility(RegimeLabel.UNKNOWN),
            classified_at=datetime.now(tz=timezone.utc),
            bars_analyzed=len(bars),
            reasoning=f"[{symbol}] UNKNOWN: {reason}",
        )

    @staticmethod
    def _minutes_since_open(dt: datetime) -> float:
        """Return minutes since 09:30 ET. Returns -1 if time is before open or not calculable."""
        try:
            # If timezone-aware, convert to a fixed offset for ET
            # We use the hour/minute in the local time of the bar
            # (assumes bars are stamped in ET or user-provided tz)
            t = dt.time()
            open_minutes = _ET_OPEN.hour * 60 + _ET_OPEN.minute
            bar_minutes = t.hour * 60 + t.minute
            diff = bar_minutes - open_minutes
            return diff
        except Exception:
            return -1
