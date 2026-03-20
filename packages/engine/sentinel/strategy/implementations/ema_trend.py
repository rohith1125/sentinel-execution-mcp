"""EMATrendContinuationStrategy — pullback to EMA21 in confirmed trend."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import compute_adx, compute_atr, compute_ema
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_ADX_MIN = 20.0
_PULLBACK_MAX_BARS = 3
_PULLBACK_TOUCH_TOLERANCE = 0.005   # 0.5% tolerance for "touching" EMA21
_RR_RATIO = 2.5
_ATR_STOP_MULT = 2.0


class EMATrendContinuationStrategy(StrategyBase):
    """EMA trend continuation on clean pullback to EMA21.

    Setup (long):
    - EMA8 > EMA21 > EMA50 (bullish stack)
    - Price pulled back to EMA21 within last 3 bars
    - ADX > 20 (confirmed trend)
    - Entry on bounce above prior bar high
    """

    name: ClassVar[str] = "ema_trend"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.TRENDING_BULL,
        RegimeLabel.TRENDING_BEAR,
        RegimeLabel.RISK_ON,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.MEAN_REVERTING,
        RegimeLabel.HIGH_VOL_UNSTABLE,
    ]
    min_bars_required: ClassVar[int] = 60
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

        ema_8 = compute_ema(close, 8)
        ema_21 = compute_ema(close, 21)
        ema_50 = compute_ema(close, 50)
        adx_series = compute_adx(high, low, close)
        atr_series = compute_atr(high, low, close)

        e8 = float(ema_8.iloc[-1])
        e21 = float(ema_21.iloc[-1])
        e50 = float(ema_50.iloc[-1])
        adx_val = float(adx_series.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        latest_close = float(close.iloc[-1])
        prior_bar_high = float(high.iloc[-2])

        bullish_stack = e8 > e21 > e50
        bearish_stack = e8 < e21 < e50

        if not (bullish_stack or bearish_stack):
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"EMA stack not aligned: EMA8={e8:.2f}, EMA21={e21:.2f}, EMA50={e50:.2f}.",
            )

        # ADX confirmation
        if adx_val < _ADX_MIN:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"ADX={adx_val:.1f} below threshold {_ADX_MIN} — trend not confirmed.",
            )

        # Check pullback: price touched EMA21 within last _PULLBACK_MAX_BARS bars
        pullback_found = False
        for i in range(1, _PULLBACK_MAX_BARS + 2):
            idx = -(i + 1)
            if abs(len(bars)) < abs(idx):
                break
            bar_low = float(low.iloc[idx])
            bar_close = float(close.iloc[idx])
            e21_at = float(ema_21.iloc[idx])
            if bullish_stack:
                # Low should have touched or come within tolerance of EMA21
                if bar_low <= e21_at * (1 + _PULLBACK_TOUCH_TOLERANCE):
                    pullback_found = True
                    break
            # Bearish: high should have touched EMA21 from below
            elif bar_close >= e21_at * (1 - _PULLBACK_TOUCH_TOLERANCE):
                pullback_found = True
                break

        if not pullback_found:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"No clean pullback to EMA21 ({e21:.2f}) in last {_PULLBACK_MAX_BARS} bars.",
            )

        # Make sure current bar has bounced back (for long: close above EMA21)
        if bullish_stack and latest_close < e21:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Price ({latest_close:.2f}) still below EMA21 ({e21:.2f}) — no bounce yet.",
            )
        if bearish_stack and latest_close > e21:
            return self._no_signal(
                self.name, symbol, bars, compat_score,
                f"Price ({latest_close:.2f}) still above EMA21 ({e21:.2f}) — no bounce yet.",
            )

        # Build signal
        if bullish_stack:
            side = OrderSide.BUY
            entry = Decimal(str(round(prior_bar_high * 1.001, 4)))
            stop = Decimal(str(round(max(float(e50) - _ATR_STOP_MULT * atr_val,
                                         float(e50) * 0.995), 4)))
        else:
            side = OrderSide.SELL
            prior_bar_low = float(low.iloc[-2])
            entry = Decimal(str(round(prior_bar_low * 0.999, 4)))
            stop = Decimal(str(round(min(float(e50) + _ATR_STOP_MULT * atr_val,
                                         float(e50) * 1.005), 4)))

        target = self.compute_target(entry, stop, rr_ratio=_RR_RATIO)

        confidence = min(
            0.90,
            compat_score * 0.45
            + min((adx_val - _ADX_MIN) / 50.0, 0.3)
            + 0.25,
        )

        indicators = {
            "ema_8": e8,
            "ema_21": e21,
            "ema_50": e50,
            "adx": adx_val,
            "atr": atr_val,
        }

        signal = StrategySignal(
            symbol=symbol,
            side=side,
            confidence=round(confidence, 4),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            timeframe=self.default_timeframe,
            supporting_indicators=indicators,
            invalidation_conditions=[
                "EMA8 crosses below EMA21",
                f"Price closes below EMA50 ({e50:.2f})",
                f"ADX drops below {_ADX_MIN}",
            ],
            max_hold_bars=30,
            notes=(
                f"EMA {'bull' if bullish_stack else 'bear'} stack: "
                f"EMA8={e8:.2f} {'>' if bullish_stack else '<'} "
                f"EMA21={e21:.2f} {'>' if bullish_stack else '<'} EMA50={e50:.2f}. "
                f"ADX={adx_val:.1f}."
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


registry.register(EMATrendContinuationStrategy())
