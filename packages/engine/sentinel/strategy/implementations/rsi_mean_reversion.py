"""RSIMeanReversionStrategy — oversold RSI recovery in mean-reverting regime."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import (
    compute_adx,
    compute_atr,
    compute_rsi,
    compute_vwap,
    compute_vwap_deviation,
)
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_RSI_OVERSOLD = 30.0
_RSI_RECOVERY = 35.0
_ADX_MAX = 20.0                 # No strong trend allowed
_VWAP_DEVIATION_MAX = 2.0       # price within 2% of VWAP
_MIN_AVG_VOLUME = 1_000_000     # bars must average >= 1M shares
_RR_MIN = 2.0


class RSIMeanReversionStrategy(StrategyBase):
    """RSI dip-and-recovery on liquid large-caps.

    Setup:
    - Only on liquid names (avg bar volume >= 1M shares)
    - RSI(14) dropped below 30 and recovered above 35
    - Price within 2% of VWAP
    - ADX < 20 (no strong downtrend)
    - Regime: MEAN_REVERTING only
    """

    name: ClassVar[str] = "rsi_mean_reversion"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.MEAN_REVERTING,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.TRENDING_BEAR,
        RegimeLabel.HIGH_VOL_UNSTABLE,
        RegimeLabel.RISK_OFF,
    ]
    min_bars_required: ClassVar[int] = 30
    default_timeframe: ClassVar[str] = "5min"

    def evaluate(
        self,
        symbol: str,
        bars: list[Bar],
        regime: RegimeSnapshot,
    ) -> StrategyResult:
        compatible, compat_score = self.is_regime_compatible(regime)
        if not compatible:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Regime {regime.label.value} incompatible.",
            )

        if len(bars) < self.min_bars_required:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Insufficient bars: need {self.min_bars_required}, have {len(bars)}.",
            )

        high = pd.Series([float(b.high) for b in bars])
        low = pd.Series([float(b.low) for b in bars])
        close = pd.Series([float(b.close) for b in bars])
        volume = pd.Series([float(b.volume) for b in bars])

        avg_volume = float(volume.mean())
        if avg_volume < _MIN_AVG_VOLUME:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Insufficient average volume: {avg_volume:,.0f} < {_MIN_AVG_VOLUME:,}.",
            )

        rsi_series = compute_rsi(close)
        adx_series = compute_adx(high, low, close)
        vwap_series = compute_vwap(high, low, close, volume)
        vwap_dev = compute_vwap_deviation(close, vwap_series)
        atr_series = compute_atr(high, low, close)

        latest_close = float(close.iloc[-1])
        latest_rsi = float(rsi_series.iloc[-1])
        prior_rsi = float(rsi_series.iloc[-2])
        adx_val = float(adx_series.iloc[-1])
        vwap_dev_val = float(vwap_dev.iloc[-1])
        latest_vwap = float(vwap_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0

        # Must have had oversold dip in recent history (within last 10 bars)
        recent_rsi_min = float(rsi_series.iloc[-10:].min())
        if recent_rsi_min > _RSI_OVERSOLD:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"No oversold dip: min RSI in last 10 bars = {recent_rsi_min:.1f} (need < {_RSI_OVERSOLD}).",
            )

        # Current RSI must have recovered above 35
        if latest_rsi <= _RSI_RECOVERY:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"RSI={latest_rsi:.1f} has not recovered above {_RSI_RECOVERY} yet.",
            )

        # Confirm upward RSI direction
        if latest_rsi <= prior_rsi:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"RSI not rising: current={latest_rsi:.1f}, prior={prior_rsi:.1f}.",
            )

        # Price near VWAP
        if abs(vwap_dev_val) > _VWAP_DEVIATION_MAX:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Price too far from VWAP: deviation={vwap_dev_val:.2f}% (max ±{_VWAP_DEVIATION_MAX}%).",
            )

        # No strong downtrend
        if adx_val > _ADX_MAX:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"ADX={adx_val:.1f} exceeds {_ADX_MAX} — possible downtrend.",
            )

        # Find the RSI swing low for stop placement
        _rsi_low_idx = int(rsi_series.iloc[-10:].idxmin()) if hasattr(rsi_series.iloc[-10:], 'idxmin') else -5
        try:
            swing_low_price = float(low.iloc[-10:].min())
        except Exception:
            swing_low_price = latest_close - 2 * atr_val

        entry = Decimal(str(round(latest_close, 4)))
        stop = Decimal(str(round(swing_low_price * 0.999, 4)))

        # Target: VWAP or 2:1 R:R, whichever is better
        risk = float(entry) - float(stop)
        target_rr = Decimal(str(round(float(entry) + risk * _RR_MIN, 4)))
        # VWAP target only if above entry
        if latest_vwap > float(entry):
            target_vwap = Decimal(str(round(latest_vwap, 4)))
            target = max(target_rr, target_vwap)
        else:
            target = target_rr

        indicators = {
            "rsi": latest_rsi,
            "prior_rsi": prior_rsi,
            "rsi_min_recent": recent_rsi_min,
            "adx": adx_val,
            "vwap": latest_vwap,
            "vwap_dev_pct": vwap_dev_val,
            "atr": atr_val,
            "avg_volume": avg_volume,
        }

        confidence = min(
            0.88,
            compat_score * 0.4
            + ((_RSI_OVERSOLD - recent_rsi_min) / _RSI_OVERSOLD) * 0.25
            + ((latest_rsi - _RSI_RECOVERY) / (70.0 - _RSI_RECOVERY)) * 0.2
            + (1 - abs(vwap_dev_val) / _VWAP_DEVIATION_MAX) * 0.15,
        )

        signal = StrategySignal(
            symbol=symbol,
            side=OrderSide.BUY,
            confidence=round(confidence, 4),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            timeframe=self.default_timeframe,
            supporting_indicators=indicators,
            invalidation_conditions=[
                f"RSI drops back below {_RSI_RECOVERY}",
                f"Price breaks below swing low ({swing_low_price:.2f})",
                f"ADX rises above {_ADX_MAX}",
                "Volume dries up significantly",
            ],
            max_hold_bars=15,
            notes=(
                f"RSI recovery: {prior_rsi:.1f} → {latest_rsi:.1f} (was oversold at {recent_rsi_min:.1f}). "
                f"VWAP deviation={vwap_dev_val:.2f}%."
            ),
        )

        return StrategyResult(
            strategy_name=self.name,
            symbol=symbol,
            signal=signal,
            evaluated_at=datetime.now(tz=UTC),
            bars_used=len(bars),
            regime_compatibility=compat_score,
        )


registry.register(RSIMeanReversionStrategy())
