"""VWAPReclaimStrategy — intraday VWAP dip-and-reclaim pattern."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import (
    compute_atr,
    compute_rsi,
    compute_volume_ratio,
    compute_vwap,
    compute_vwap_deviation,
)
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_DIP_LOOKBACK = 5           # look for VWAP dip within last N bars
_VOLUME_RECLAIM_MIN = 1.2   # reclaim bar must have >= 1.2x avg volume
_RSI_MIN = 50.0
_RSI_MAX = 70.0
_RR_MIN = 1.5


class VWAPReclaimStrategy(StrategyBase):
    """Intraday mean-reversion via VWAP dip and reclaim.

    Setup:
    - Price dipped below VWAP within last 5 bars
    - Current bar closes above VWAP (reclaim)
    - Volume on reclaim bar >= 1.2x average
    - RSI recovering: 50-70 range
    """

    name: ClassVar[str] = "vwap_reclaim"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.MEAN_REVERTING,
        RegimeLabel.TRENDING_BULL,
        RegimeLabel.RISK_ON,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.HIGH_VOL_UNSTABLE,
        RegimeLabel.LOW_LIQUIDITY,
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

        vwap_series = compute_vwap(high, low, close, volume)
        vwap_dev = compute_vwap_deviation(close, vwap_series)
        rsi_series = compute_rsi(close)
        vol_ratio_series = compute_volume_ratio(volume)
        atr_series = compute_atr(high, low, close)

        latest_close = float(close.iloc[-1])
        latest_vwap = float(vwap_series.iloc[-1])
        rsi_val = float(rsi_series.iloc[-1])
        vol_ratio_val = float(vol_ratio_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0

        # Check: price is now above VWAP (reclaim)
        if latest_close <= latest_vwap:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"No VWAP reclaim: close={latest_close:.2f} <= VWAP={latest_vwap:.2f}.",
            )

        # Check: price dipped below VWAP within last _DIP_LOOKBACK bars
        recent_dev = vwap_dev.iloc[-(1 + _DIP_LOOKBACK):-1]
        had_dip = (recent_dev < 0).any()
        if not had_dip:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"No prior VWAP dip in last {_DIP_LOOKBACK} bars.",
            )

        # Volume confirmation on reclaim
        if vol_ratio_val < _VOLUME_RECLAIM_MIN:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Reclaim volume insufficient: {vol_ratio_val:.2f} < {_VOLUME_RECLAIM_MIN}.",
            )

        # RSI in recovery zone
        if not (_RSI_MIN <= rsi_val <= _RSI_MAX):
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"RSI={rsi_val:.1f} not in recovery zone [{_RSI_MIN}, {_RSI_MAX}].",
            )

        # Find the dip low
        dip_window_lows = [float(b.low) for b in bars[-(1 + _DIP_LOOKBACK):-1]]
        dip_low = min(dip_window_lows)

        entry = Decimal(str(round(latest_close * 1.0005, 4)))  # slight buffer above close
        stop = Decimal(str(round(dip_low * 0.999, 4)))         # just below dip low

        # Target: prior day high or 1.5x risk
        entry_f = float(entry)
        stop_f = float(stop)
        risk = entry_f - stop_f
        target_min = Decimal(str(round(entry_f + risk * _RR_MIN, 4)))

        # Use prior session high as a reference target
        prior_high = float(high.iloc[:-1].max())
        if prior_high > float(target_min):
            target = Decimal(str(round(prior_high, 4)))
        else:
            target = target_min

        indicators = {
            "vwap": latest_vwap,
            "vwap_dev_pct": float(vwap_dev.iloc[-1]),
            "rsi": rsi_val,
            "vol_ratio": vol_ratio_val,
            "dip_low": dip_low,
            "atr": atr_val,
        }

        confidence = min(
            0.90,
            compat_score * 0.5
            + (vol_ratio_val / 4.0) * 0.25
            + ((rsi_val - _RSI_MIN) / (_RSI_MAX - _RSI_MIN)) * 0.25,
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
                f"Price drops back below VWAP ({latest_vwap:.2f})",
                f"Price makes new low below dip low ({dip_low:.2f})",
                "RSI collapses below 45",
            ],
            max_hold_bars=20,
            notes=(
                f"VWAP reclaim at {latest_close:.2f} (VWAP={latest_vwap:.2f}). "
                f"Dip low={dip_low:.2f}, RSI={rsi_val:.1f}."
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


registry.register(VWAPReclaimStrategy())
