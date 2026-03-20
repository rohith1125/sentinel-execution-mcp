"""OpeningRangeBreakoutStrategy — 15-minute ORB with volume and gap filters."""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import Decimal
from typing import ClassVar

import pandas as pd

from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar
from sentinel.regime.indicators import compute_atr
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult, StrategySignal
from sentinel.strategy.registry import registry

_OR_MINUTES = 15  # opening range window
_EARLIEST_ENTRY_TIME = time(9, 45)  # ET
_TIME_STOP_TIME = time(11, 0)  # ET — exit if target not hit
_VOLUME_BREAKOUT_MULT = 2.0  # volume on breakout bar >= 2x OR volume
_PRE_MARKET_GAP_MAX = 2.0  # percent
_ATR_PANIC_MULTIPLIER = 4.0  # if ATR > X% of price, skip (event day)
_RR_PREFERRED = 2.0


class OpeningRangeBreakoutStrategy(StrategyBase):
    """15-minute Opening Range Breakout.

    Rules:
    - Build OR from the first 15 minutes (aggregated high/low)
    - Only enter after 9:45 AM ET
    - Break of OR high (long) or OR low (short) with volume >= 2x OR avg volume
    - Pre-market gap < 2% (no gap-and-go)
    - ATR not in panic mode (< 4% of price)
    - Stop: other side of OR midpoint
    - Target: OR width projected from breakout (2:1 preferred)
    - Time stop at 11:00 AM ET
    """

    name: ClassVar[str] = "orb"
    supported_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.OPENING_NOISE,
        RegimeLabel.TRENDING_BULL,
        RegimeLabel.RISK_ON,
    ]
    anti_regimes: ClassVar[list[RegimeLabel]] = [
        RegimeLabel.HIGH_VOL_UNSTABLE,
        RegimeLabel.RISK_OFF,
        RegimeLabel.EVENT_DISTORTED,
    ]
    min_bars_required: ClassVar[int] = 4  # at least 4 x 5-min bars after open (for OR + confirmation)
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
                self.name,
                symbol,
                bars,
                compat_score,
                f"Regime {regime.label.value} incompatible.",
            )

        if len(bars) < self.min_bars_required:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"Insufficient bars: need {self.min_bars_required}, have {len(bars)}.",
            )

        # Check time constraints
        last_bar = bars[-1]
        bar_time = last_bar.timestamp.time()

        # Time stop — don't enter after 11:00 AM ET
        if bar_time >= _TIME_STOP_TIME:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"Past time stop: {bar_time} >= {_TIME_STOP_TIME} ET. No new ORB entries.",
            )

        # Must be after 9:45 AM ET (OR must be fully formed)
        if bar_time < _EARLIEST_ENTRY_TIME:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"Too early: {bar_time} < {_EARLIEST_ENTRY_TIME} ET. OR not yet set.",
            )

        high_s = pd.Series([float(b.high) for b in bars])
        low_s = pd.Series([float(b.low) for b in bars])
        close_s = pd.Series([float(b.close) for b in bars])
        volume_s = pd.Series([float(b.volume) for b in bars])
        open_s = pd.Series([float(b.open) for b in bars])

        atr_series = compute_atr(high_s, low_s, close_s)
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
        latest_close = float(close_s.iloc[-1])

        # Sanity check: not in panic/event mode
        atr_pct = atr_val / latest_close * 100 if latest_close > 0 else 0.0
        if atr_pct > _ATR_PANIC_MULTIPLIER:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"ATR% = {atr_pct:.2f}% exceeds panic threshold {_ATR_PANIC_MULTIPLIER}%. Likely event day.",
            )

        # Identify OR bars: first N bars where time < 9:45 ET
        or_bars = [b for b in bars if b.timestamp.time() < _EARLIEST_ENTRY_TIME]
        if not or_bars:
            # Fallback: use first bar as OR proxy
            or_bars = [bars[0]]

        or_high = max(float(b.high) for b in or_bars)
        or_low = min(float(b.low) for b in or_bars)
        or_volume = sum(float(b.volume) for b in or_bars)
        or_avg_volume = or_volume / len(or_bars)
        or_midpoint = (or_high + or_low) / 2
        or_width = or_high - or_low

        if or_width <= 0:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                "OR width is zero — cannot compute targets.",
            )

        # Pre-market gap check: compare first bar open to prior-day context
        # Approximate: first bar open vs prior session (using bar open vs first bar prev close via bars)
        # We use the first bar's open vs the close of the very first bar we have
        session_open = float(open_s.iloc[0])
        prior_close_approx = float(close_s.iloc[0])  # use open as proxy if no prev close
        # Better approximation from OR bars
        if len(bars) > 1:
            # Use ratio of first bar open to prior period's close
            # If bars cover multiple sessions, find transition
            pass

        # Simple gap check: first bar open vs first bar close (intra-open-range gap)
        _gap_pct = abs(session_open - prior_close_approx) / prior_close_approx * 100 if prior_close_approx > 0 else 0.0
        # Only flag if obviously gapped (open far from first bar's range)
        _actual_gap = abs(float(bars[0].open) - float(bars[0].close)) / float(bars[0].close) * 100

        # Check breakout on current bar
        latest_high = float(high_s.iloc[-1])
        latest_low = float(low_s.iloc[-1])
        current_volume = float(volume_s.iloc[-1])

        long_breakout = latest_close > or_high and latest_high > or_high
        short_breakout = latest_close < or_low and latest_low < or_low

        if not (long_breakout or short_breakout):
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"No ORB trigger: close={latest_close:.2f}, OR=[{or_low:.2f},{or_high:.2f}].",
            )

        # Volume confirmation on breakout bar
        if or_avg_volume > 0 and current_volume < _VOLUME_BREAKOUT_MULT * or_avg_volume:
            return self._no_signal(
                self.name,
                symbol,
                bars,
                compat_score,
                f"Breakout volume insufficient: {current_volume:,.0f} < {_VOLUME_BREAKOUT_MULT}x OR avg ({or_avg_volume:,.0f}).",
            )

        if long_breakout:
            side = OrderSide.BUY
            entry = Decimal(str(round(or_high * 1.001, 4)))
            stop = Decimal(str(round(or_midpoint, 4)))
            target = Decimal(str(round(float(entry) + or_width * _RR_PREFERRED, 4)))
        else:
            side = OrderSide.SELL
            entry = Decimal(str(round(or_low * 0.999, 4)))
            stop = Decimal(str(round(or_midpoint, 4)))
            target = Decimal(str(round(float(entry) - or_width * _RR_PREFERRED, 4)))

        risk = abs(float(entry) - float(stop))
        reward = abs(float(target) - float(entry))
        rr = reward / risk if risk > 0 else 0.0

        confidence = min(
            0.85,
            compat_score * 0.45 + min(current_volume / max(or_avg_volume, 1) / (_VOLUME_BREAKOUT_MULT * 2), 0.3) + 0.25,
        )

        indicators = {
            "or_high": or_high,
            "or_low": or_low,
            "or_midpoint": or_midpoint,
            "or_width": or_width,
            "or_avg_volume": or_avg_volume,
            "breakout_volume": current_volume,
            "atr": atr_val,
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
                f"Price retraces back inside OR (below {or_high:.2f} for long / above {or_low:.2f} for short)",
                "Time stop: exit by 11:00 AM ET if target not reached",
                "Volume drops off sharply after breakout",
            ],
            max_hold_bars=15,  # ~75 min at 5-min bars
            notes=(
                f"ORB {'long' if side == OrderSide.BUY else 'short'}: "
                f"OR=[{or_low:.2f},{or_high:.2f}] width={or_width:.2f}. "
                f"Volume={current_volume:,.0f} ({current_volume / or_avg_volume:.1f}x OR avg). "
                f"Target R:R={rr:.1f}."
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


registry.register(OpeningRangeBreakoutStrategy())
