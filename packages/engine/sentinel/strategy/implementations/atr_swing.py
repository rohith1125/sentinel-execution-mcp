"""ATRSwingTrendStrategy — daily ATR channel pullback in confirmed swing trend."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import compute_atr, compute_ema
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_EMA_PERIOD = 50
_ATR_PERIOD = 14
_ATR_LOWER_BAND_MULT = 1.5      # EMA - 1.5 ATR = lower band
_ATR_STOP_MULT = 2.0
_ATR_TARGET_MULT = 4.0
_WEEKLY_BARS = 5                # approx 1 trading week


class ATRSwingTrendStrategy(StrategyBase):
    """Daily ATR channel swing trade in confirmed uptrend.

    Setup (long):
    - Price above rising 50-day EMA
    - Weekly trend intact (last 5 bars trending up)
    - Price pulls back to lower ATR band (EMA50 - 1.5*ATR)
    - Entry: daily close above prior day high
    - Stop: 2 ATR below entry
    - Target: 4 ATR above entry
    """

    name: ClassVar[str] = "atr_swing"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.TRENDING_BULL,
        RegimeLabel.TRENDING_BEAR,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.HIGH_VOL_UNSTABLE,
        RegimeLabel.LOW_LIQUIDITY,
    ]
    min_bars_required: ClassVar[int] = 60
    default_timeframe: ClassVar[str] = "1day"

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

        ema_50 = compute_ema(close, _EMA_PERIOD)
        atr_series = compute_atr(high, low, close, _ATR_PERIOD)

        e50 = float(ema_50.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        latest_close = float(close.iloc[-1])
        latest_low = float(low.iloc[-1])
        prior_high = float(high.iloc[-2])

        lower_band = e50 - _ATR_LOWER_BAND_MULT * atr_val

        # Check if EMA50 is rising (positive slope over last 5 bars)
        ema_5_ago = float(ema_50.iloc[-6]) if len(ema_50) > 6 else float(ema_50.iloc[0])
        ema_rising = e50 > ema_5_ago

        # Determine direction from regime
        if regime.label == RegimeLabel.TRENDING_BEAR:
            # Short side
            upper_band = e50 + _ATR_LOWER_BAND_MULT * atr_val
            ema_falling = e50 < ema_5_ago

            if not ema_falling:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"EMA50 not falling for short setup (EMA50={e50:.2f}, 5-bars-ago={ema_5_ago:.2f}).",
                )

            # Weekly trend check (approx: last 5 bars declining)
            weekly_close = close.iloc[-_WEEKLY_BARS:]
            weekly_trending_down = float(weekly_close.iloc[-1]) < float(weekly_close.iloc[0])

            if not weekly_trending_down:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    "Weekly trend not down — short setup invalid.",
                )

            # Price rallied to upper ATR band
            latest_high = float(high.iloc[-1])
            if latest_high < upper_band * 0.99:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"Price not near upper ATR band: high={latest_high:.2f}, upper_band={upper_band:.2f}.",
                )

            prior_bar_low = float(low.iloc[-2])
            entry = Decimal(str(round(prior_bar_low * 0.999, 4)))
            stop = Decimal(str(round(float(entry) + _ATR_STOP_MULT * atr_val, 4)))
            target = Decimal(str(round(float(entry) - _ATR_TARGET_MULT * atr_val, 4)))
            side = OrderSide.SELL

        else:
            # Long side (TRENDING_BULL or default)
            if not ema_rising:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"EMA50 not rising: current={e50:.2f}, 5-bars-ago={ema_5_ago:.2f}.",
                )

            # Price must be above EMA50
            if latest_close < e50:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"Price ({latest_close:.2f}) below EMA50 ({e50:.2f}).",
                )

            # Weekly trend intact (last 5 bars trending up)
            weekly_close = close.iloc[-_WEEKLY_BARS:]
            weekly_trending_up = float(weekly_close.iloc[-1]) > float(weekly_close.iloc[0])

            if not weekly_trending_up:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    "Weekly trend not up — long setup invalid.",
                )

            # Price must have pulled back to the lower ATR band
            # Check: any of last 3 bars' lows touched the lower band
            touched_lower = False
            for i in range(1, 4):
                if len(bars) > i:
                    bar_low = float(low.iloc[-i])
                    if bar_low <= lower_band * 1.01:
                        touched_lower = True
                        break

            if not touched_lower:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"No ATR band pullback: lower_band={lower_band:.2f}, recent lows={float(low.iloc[-1]):.2f}.",
                )

            # Entry: daily close above prior day high
            if latest_close <= prior_high:
                return self._no_signal(
                    self.name, symbol, bars, compat_score,
                    f"Entry trigger not met: close={latest_close:.2f} <= prior_high={prior_high:.2f}.",
                )

            entry = Decimal(str(round(latest_close, 4)))
            stop = Decimal(str(round(float(entry) - _ATR_STOP_MULT * atr_val, 4)))
            target = Decimal(str(round(float(entry) + _ATR_TARGET_MULT * atr_val, 4)))
            side = OrderSide.BUY

        risk = abs(float(entry) - float(stop))
        reward = abs(float(target) - float(entry))
        rr = reward / risk if risk > 0 else 0.0

        confidence = min(
            0.88,
            compat_score * 0.5
            + min(atr_val / (float(entry) * 0.02), 0.3)   # higher ATR = more movement potential
            + 0.20,
        )

        indicators = {
            "ema_50": e50,
            "ema_50_5ago": ema_5_ago,
            "atr": atr_val,
            "lower_band": lower_band,
            "rr_ratio": rr,
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
                f"Price closes below EMA50 ({e50:.2f})" if side == OrderSide.BUY else f"Price closes above EMA50 ({e50:.2f})",
                f"EMA50 turns flat or reverses direction",
                f"Stop hit at {float(stop):.2f}",
            ],
            max_hold_bars=20,
            notes=(
                f"ATR swing {'long' if side == OrderSide.BUY else 'short'}: "
                f"EMA50={e50:.2f}, ATR={atr_val:.2f}, lower_band={lower_band:.2f}. "
                f"Target R:R={rr:.1f}."
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


registry.register(ATRSwingTrendStrategy())
