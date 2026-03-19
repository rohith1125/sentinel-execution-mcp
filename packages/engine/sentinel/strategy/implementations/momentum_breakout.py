"""MomentumBreakoutStrategy — new N-day high with volume confirmation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import (
    compute_atr,
    compute_ema,
    compute_rsi,
    compute_volume_ratio,
)
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_BREAKOUT_LOOKBACK = 20          # N-day high window
_VOLUME_MULTIPLIER = 1.5         # relative volume required at breakout
_RSI_MIN = 55.0
_RSI_MAX = 80.0
_ENTRY_BUFFER_PCT = 0.001        # 0.1% above breakout level
_RR_RATIO = 2.0
_ATR_STOP_MULTIPLIER = 2.0


class MomentumBreakoutStrategy(StrategyBase):
    """Long-only momentum breakout on new N-day highs with volume confirmation.

    Entry conditions:
    - Price making new 20-bar high
    - Relative volume >= 1.5x 20-bar average
    - EMA20 > EMA50 (upward stack)
    - RSI(14) between 55 and 80
    """

    name: ClassVar[str] = "momentum_breakout"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.TRENDING_BULL,
        RegimeLabel.RISK_ON,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.HIGH_VOL_UNSTABLE,
        RegimeLabel.MEAN_REVERTING,
        RegimeLabel.LOW_LIQUIDITY,
        RegimeLabel.RISK_OFF,
        RegimeLabel.OPENING_NOISE,
    ]
    min_bars_required: ClassVar[int] = 55   # need 50 bars for EMA50 + buffer
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
                f"Regime {regime.label.value} is incompatible (anti-regime or low score).",
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

        atr_series = compute_atr(high, low, close)
        rsi_series = compute_rsi(close)
        ema_20 = compute_ema(close, 20)
        ema_50 = compute_ema(close, 50)
        vol_ratio_series = compute_volume_ratio(volume)

        latest_close = float(close.iloc[-1])
        latest_high = float(high.iloc[-1])
        rsi_val = float(rsi_series.iloc[-1])
        ema_20_val = float(ema_20.iloc[-1])
        ema_50_val = float(ema_50.iloc[-1])
        vol_ratio_val = float(vol_ratio_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1])

        # Prior N-bar high (excluding current bar)
        prior_high = float(high.iloc[-(1 + _BREAKOUT_LOOKBACK):-1].max())

        indicators = {
            "rsi": rsi_val,
            "ema_20": ema_20_val,
            "ema_50": ema_50_val,
            "vol_ratio": vol_ratio_val,
            "atr": atr_val,
            "prior_high": prior_high,
            "latest_high": latest_high,
        }

        # --- Condition checks ---
        if latest_high <= prior_high:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"No breakout: latest_high={latest_high:.2f} <= prior_{_BREAKOUT_LOOKBACK}bar_high={prior_high:.2f}.",
            )

        if vol_ratio_val < _VOLUME_MULTIPLIER:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Volume confirmation failed: vol_ratio={vol_ratio_val:.2f} < {_VOLUME_MULTIPLIER}.",
            )

        if not (ema_20_val > ema_50_val):
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"EMA stack not bullish: EMA20={ema_20_val:.2f}, EMA50={ema_50_val:.2f}.",
            )

        if not (_RSI_MIN <= rsi_val <= _RSI_MAX):
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"RSI={rsi_val:.1f} outside acceptable range [{_RSI_MIN}, {_RSI_MAX}].",
            )

        # --- Build signal ---
        breakout_level = prior_high
        entry = Decimal(str(round(breakout_level * (1 + _ENTRY_BUFFER_PCT), 4)))

        # Stop = min(breakout bar low, 2x ATR below entry)
        breakout_bar_low = float(low.iloc[-1])
        stop_atr = float(entry) - _ATR_STOP_MULTIPLIER * atr_val
        stop_raw = max(breakout_bar_low, stop_atr)
        stop = Decimal(str(round(stop_raw, 4)))

        target = self.compute_target(entry, stop, rr_ratio=_RR_RATIO)

        confidence = min(
            0.95,
            compat_score * 0.4
            + min(vol_ratio_val / 3.0, 0.3)
            + (1.0 if ema_20_val > ema_50_val else 0.0) * 0.15
            + ((rsi_val - _RSI_MIN) / (_RSI_MAX - _RSI_MIN)) * 0.15,
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
                f"Price closes below breakout level {breakout_level:.2f}",
                f"RSI drops below {_RSI_MIN}",
                "Volume collapses below 1x average",
                f"EMA20 crosses below EMA50",
            ],
            max_hold_bars=48,
            notes=(
                f"Breakout above {prior_high:.2f} with {vol_ratio_val:.1f}x volume. "
                f"EMA stack bullish. RSI={rsi_val:.1f}."
            ),
        )

        return StrategyResult(
            strategy_name=self.name,
            symbol=symbol,
            signal=signal,
            evaluated_at=datetime.now(tz=timezone.utc),
            bars_used=len(bars),
            regime_compatibility=compat_score,
        )


# Self-register
registry.register(MomentumBreakoutStrategy())
