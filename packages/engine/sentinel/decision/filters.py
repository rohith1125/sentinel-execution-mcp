"""Individual vote filter functions for the DecisionCommittee.

All functions are pure — they take data and return a VoteRecord.
No side effects, no I/O.
"""

from __future__ import annotations

from datetime import datetime, time

from sentinel.decision.models import VoteRecord
from sentinel.domain.types import OrderSide, RegimeLabel
from sentinel.market.provider import Bar, Snapshot
from sentinel.regime.indicators import compute_atr
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategySignal

import pandas as pd

# Weights
_W_REGIME = 0.20
_W_LIQUIDITY = 0.15
_W_VOLATILITY = 0.12
_W_RR = 0.13
_W_CONCENTRATION = 0.15
_W_BETA = 0.10
_W_CONFIDENCE = 0.10
_W_TIME = 0.05

_ET_OPEN = time(9, 30)
_ET_CLOSE = time(16, 0)
_BUFFER_MINUTES = 15


def vote_regime_compatibility(
    signal: StrategySignal,
    regime: RegimeSnapshot,
    strategy: StrategyBase,
) -> VoteRecord:
    """Approve if regime is compatible with strategy, hard reject on anti-regime."""
    compatible, score = strategy.is_regime_compatible(regime)

    if not compatible:
        return VoteRecord(
            voter="regime_gate",
            vote="reject",
            weight=_W_REGIME,
            reason=(
                f"Regime '{regime.label.value}' is a hard anti-regime for {strategy.name}. "
                f"Compatibility score: {score:.2f}."
            ),
            metrics={
                "regime_score": score,
                "tradeability": regime.tradeability_score,
            },
        )

    if score >= 0.65:
        return VoteRecord(
            voter="regime_gate",
            vote="approve",
            weight=_W_REGIME,
            reason=f"Regime '{regime.label.value}' is compatible ({score:.2f}). Tradeability={regime.tradeability_score:.2f}.",
            metrics={"regime_score": score, "tradeability": regime.tradeability_score},
        )

    if score >= 0.40:
        return VoteRecord(
            voter="regime_gate",
            vote="abstain",
            weight=_W_REGIME,
            reason=f"Regime '{regime.label.value}' marginally compatible ({score:.2f}). Caution advised.",
            metrics={"regime_score": score, "tradeability": regime.tradeability_score},
        )

    return VoteRecord(
        voter="regime_gate",
        vote="reject",
        weight=_W_REGIME,
        reason=f"Regime '{regime.label.value}' score too low ({score:.2f}). Do not trade.",
        metrics={"regime_score": score, "tradeability": regime.tradeability_score},
    )


def vote_liquidity(
    snapshot: Snapshot,
    min_spread_bps: float = 20.0,
    min_volume: int = 100_000,
) -> VoteRecord:
    """Reject if spread is too wide or volume is too thin."""
    spread_bps = snapshot.quote.spread_bps
    # Approximate volume from the latest bar
    bar_volume = snapshot.latest_bar.volume

    too_wide_spread = spread_bps > min_spread_bps
    too_thin_volume = bar_volume < min_volume

    metrics = {
        "spread_bps": spread_bps,
        "bar_volume": float(bar_volume),
        "min_spread_bps": min_spread_bps,
        "min_volume": float(min_volume),
    }

    if too_wide_spread and too_thin_volume:
        return VoteRecord(
            voter="liquidity_filter",
            vote="reject",
            weight=_W_LIQUIDITY,
            reason=(
                f"Both spread ({spread_bps:.1f} bps > {min_spread_bps}) and "
                f"volume ({bar_volume:,} < {min_volume:,}) fail liquidity check."
            ),
            metrics=metrics,
        )

    if too_wide_spread:
        return VoteRecord(
            voter="liquidity_filter",
            vote="reject",
            weight=_W_LIQUIDITY,
            reason=f"Spread too wide: {spread_bps:.1f} bps > {min_spread_bps} bps threshold.",
            metrics=metrics,
        )

    if too_thin_volume:
        return VoteRecord(
            voter="liquidity_filter",
            vote="abstain",
            weight=_W_LIQUIDITY,
            reason=f"Volume thin: {bar_volume:,} < {min_volume:,} (abstain — monitor).",
            metrics=metrics,
        )

    return VoteRecord(
        voter="liquidity_filter",
        vote="approve",
        weight=_W_LIQUIDITY,
        reason=f"Adequate liquidity: spread={spread_bps:.1f} bps, volume={bar_volume:,}.",
        metrics=metrics,
    )


def vote_volatility_sanity(
    bars: list[Bar],
    signal: StrategySignal,
) -> VoteRecord:
    """Reject if ATR% makes the stop unrealistic or trade size dangerous."""
    if not bars:
        return VoteRecord(
            voter="volatility_sanity",
            vote="abstain",
            weight=_W_VOLATILITY,
            reason="No bars available for ATR calculation.",
        )

    high = pd.Series([float(b.high) for b in bars])
    low = pd.Series([float(b.low) for b in bars])
    close = pd.Series([float(b.close) for b in bars])

    atr_series = compute_atr(high, low, close)
    atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().iloc[-1] else 0.0
    latest_close = float(close.iloc[-1])
    atr_pct = atr_val / latest_close * 100 if latest_close > 0 else 0.0

    # Compute stop distance
    if signal.entry_price is not None:
        stop_distance = abs(float(signal.entry_price) - float(signal.stop_price))
        stop_pct = stop_distance / float(signal.entry_price) * 100 if float(signal.entry_price) > 0 else 0.0
    else:
        stop_pct = 0.0

    metrics = {
        "atr": atr_val,
        "atr_pct": atr_pct,
        "stop_pct": stop_pct,
    }

    if atr_pct > 5.0:
        return VoteRecord(
            voter="volatility_sanity",
            vote="reject",
            weight=_W_VOLATILITY,
            reason=f"Extreme volatility: ATR% = {atr_pct:.2f}% (> 5%). Size risk unacceptable.",
            metrics=metrics,
        )

    if stop_pct > atr_pct * 3:
        return VoteRecord(
            voter="volatility_sanity",
            vote="reject",
            weight=_W_VOLATILITY,
            reason=(
                f"Stop distance ({stop_pct:.2f}%) is {stop_pct/atr_pct:.1f}x ATR — unrealistically wide. "
                "Stop may never be triggered."
            ),
            metrics=metrics,
        )

    if stop_pct < atr_pct * 0.3:
        return VoteRecord(
            voter="volatility_sanity",
            vote="abstain",
            weight=_W_VOLATILITY,
            reason=(
                f"Stop distance ({stop_pct:.2f}%) is very tight vs ATR ({atr_pct:.2f}%). "
                "High probability of premature stop-out."
            ),
            metrics=metrics,
        )

    return VoteRecord(
        voter="volatility_sanity",
        vote="approve",
        weight=_W_VOLATILITY,
        reason=f"Volatility reasonable: ATR%={atr_pct:.2f}%, stop_pct={stop_pct:.2f}%.",
        metrics=metrics,
    )


def vote_risk_reward(signal: StrategySignal, min_rr: float = 1.5) -> VoteRecord:
    """Reject if R:R below minimum acceptable threshold."""
    rr = signal.risk_reward_ratio
    metrics = {"rr_ratio": rr, "min_rr": min_rr}

    if rr < min_rr:
        return VoteRecord(
            voter="risk_reward_filter",
            vote="reject",
            weight=_W_RR,
            reason=f"R:R = {rr:.2f} below minimum {min_rr}. Trade not worth the risk.",
            metrics=metrics,
        )

    if rr < min_rr * 1.2:
        return VoteRecord(
            voter="risk_reward_filter",
            vote="abstain",
            weight=_W_RR,
            reason=f"R:R = {rr:.2f} meets minimum but is marginal (< {min_rr * 1.2:.1f}).",
            metrics=metrics,
        )

    return VoteRecord(
        voter="risk_reward_filter",
        vote="approve",
        weight=_W_RR,
        reason=f"R:R = {rr:.2f} is attractive (>= {min_rr}).",
        metrics=metrics,
    )


def vote_portfolio_concentration(
    symbol: str,
    side: OrderSide,
    portfolio_context: dict,
    max_pct: float = 0.10,
) -> VoteRecord:
    """Reject if adding the position would exceed concentration limit."""
    positions = portfolio_context.get("positions", {})
    account_value = float(portfolio_context.get("account_value", 1.0))
    existing_exposure = float(positions.get(symbol, {}).get("notional", 0.0))
    existing_pct = existing_exposure / account_value if account_value > 0 else 0.0

    # Count existing positions in same side to detect over-concentration in direction
    long_count = sum(
        1 for p in positions.values()
        if p.get("side", "long") == "long"
    )
    short_count = sum(
        1 for p in positions.values()
        if p.get("side", "short") == "short"
    )
    total_gross_exposure_pct = float(portfolio_context.get("gross_exposure_pct", 0.0))

    metrics = {
        "existing_pct": existing_pct,
        "max_pct": max_pct,
        "total_gross_exposure_pct": total_gross_exposure_pct,
        "long_count": float(long_count),
        "short_count": float(short_count),
    }

    if existing_pct >= max_pct:
        return VoteRecord(
            voter="concentration_filter",
            vote="reject",
            weight=_W_CONCENTRATION,
            reason=(
                f"Position already at {existing_pct:.1%} of account (max {max_pct:.0%}). "
                "Cannot add more exposure."
            ),
            metrics=metrics,
        )

    if total_gross_exposure_pct > 0.85:
        return VoteRecord(
            voter="concentration_filter",
            vote="reject",
            weight=_W_CONCENTRATION,
            reason=f"Portfolio gross exposure {total_gross_exposure_pct:.1%} exceeds 85% cap.",
            metrics=metrics,
        )

    if existing_pct > max_pct * 0.7:
        return VoteRecord(
            voter="concentration_filter",
            vote="abstain",
            weight=_W_CONCENTRATION,
            reason=f"Near concentration limit: {existing_pct:.1%} vs max {max_pct:.0%}.",
            metrics=metrics,
        )

    return VoteRecord(
        voter="concentration_filter",
        vote="approve",
        weight=_W_CONCENTRATION,
        reason=f"Concentration acceptable: {existing_pct:.1%} in {symbol} (max {max_pct:.0%}).",
        metrics=metrics,
    )


def vote_beta_context(
    regime: RegimeSnapshot,
    signal: StrategySignal,
) -> VoteRecord:
    """Reduce confidence if macro context is adverse for the signal direction."""
    spy_change = regime.supporting_metrics.get("spy_intraday_pct", 0.0)
    tradeability = regime.tradeability_score
    label = regime.label

    metrics = {
        "spy_intraday_pct": spy_change,
        "regime_tradeability": tradeability,
    }

    # Strong adverse context
    if label == RegimeLabel.RISK_OFF:
        return VoteRecord(
            voter="beta_context_filter",
            vote="reject",
            weight=_W_BETA,
            reason=f"Macro regime is RISK_OFF. SPY change: {spy_change:.2f}%. Adverse for longs.",
            metrics=metrics,
        )

    if spy_change < -1.0 and signal.side == OrderSide.BUY:
        return VoteRecord(
            voter="beta_context_filter",
            vote="abstain",
            weight=_W_BETA,
            reason=f"SPY down {spy_change:.2f}% intraday — headwind for long positions.",
            metrics=metrics,
        )

    if spy_change > 1.0 and signal.side == OrderSide.SELL:
        return VoteRecord(
            voter="beta_context_filter",
            vote="abstain",
            weight=_W_BETA,
            reason=f"SPY up {spy_change:.2f}% intraday — headwind for short positions.",
            metrics=metrics,
        )

    return VoteRecord(
        voter="beta_context_filter",
        vote="approve",
        weight=_W_BETA,
        reason=f"Macro context neutral/favourable. SPY change: {spy_change:.2f}%, tradeability={tradeability:.2f}.",
        metrics=metrics,
    )


def vote_signal_confidence(
    signal: StrategySignal,
    min_confidence: float = 0.55,
) -> VoteRecord:
    """Reject if underlying strategy signal confidence is below threshold."""
    conf = signal.confidence
    metrics = {"signal_confidence": conf, "min_confidence": min_confidence}

    if conf < min_confidence:
        return VoteRecord(
            voter="signal_confidence_filter",
            vote="reject",
            weight=_W_CONFIDENCE,
            reason=f"Signal confidence {conf:.2f} below threshold {min_confidence:.2f}.",
            metrics=metrics,
        )

    if conf < min_confidence * 1.15:
        return VoteRecord(
            voter="signal_confidence_filter",
            vote="abstain",
            weight=_W_CONFIDENCE,
            reason=f"Signal confidence {conf:.2f} is marginal (barely above {min_confidence:.2f}).",
            metrics=metrics,
        )

    return VoteRecord(
        voter="signal_confidence_filter",
        vote="approve",
        weight=_W_CONFIDENCE,
        reason=f"Signal confidence {conf:.2f} meets quality bar.",
        metrics=metrics,
    )


def vote_time_of_day(current_time: datetime) -> VoteRecord:
    """Abstain during first and last 15 min of regular trading session."""
    t = current_time.time()
    open_buffer = time(
        _ET_OPEN.hour,
        _ET_OPEN.minute + _BUFFER_MINUTES,
    )
    close_buffer = time(
        _ET_CLOSE.hour,
        _ET_CLOSE.minute - _BUFFER_MINUTES,
    )

    metrics = {
        "current_hour": float(t.hour),
        "current_minute": float(t.minute),
    }

    if t < _ET_OPEN:
        return VoteRecord(
            voter="time_of_day_filter",
            vote="reject",
            weight=_W_TIME,
            reason=f"Pre-market: {t.strftime('%H:%M')} ET. Market not yet open.",
            metrics=metrics,
        )

    if t >= _ET_CLOSE:
        return VoteRecord(
            voter="time_of_day_filter",
            vote="reject",
            weight=_W_TIME,
            reason=f"After-hours: {t.strftime('%H:%M')} ET. Market closed.",
            metrics=metrics,
        )

    if t < open_buffer:
        return VoteRecord(
            voter="time_of_day_filter",
            vote="abstain",
            weight=_W_TIME,
            reason=(
                f"Opening noise window: {t.strftime('%H:%M')} ET "
                f"(< {open_buffer.strftime('%H:%M')}). Low confidence entries."
            ),
            metrics=metrics,
        )

    if t >= close_buffer:
        return VoteRecord(
            voter="time_of_day_filter",
            vote="abstain",
            weight=_W_TIME,
            reason=(
                f"Near close: {t.strftime('%H:%M')} ET "
                f"(>= {close_buffer.strftime('%H:%M')}). Avoid new positions."
            ),
            metrics=metrics,
        )

    return VoteRecord(
        voter="time_of_day_filter",
        vote="approve",
        weight=_W_TIME,
        reason=f"Good trading time: {t.strftime('%H:%M')} ET.",
        metrics=metrics,
    )
