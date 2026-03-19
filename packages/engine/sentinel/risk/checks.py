"""
Individual risk check functions.

Design principles:
- Every function is pure: no side effects, no I/O, fully unit-testable.
- Hard blocks (is_hard_block=True) cause immediate trade rejection.
- Soft checks (is_hard_block=False) generate warnings but do not block.
- Conservative bias: when in doubt, block.
"""
from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from sentinel.domain.types import OrderSide
from sentinel.risk.models import KillSwitchState, RiskCheckResult

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Hard blocks
# ---------------------------------------------------------------------------


def check_kill_switch(
    kill_state: KillSwitchState,
    strategy_name: str,
    symbol: str,
) -> RiskCheckResult:
    """HARD BLOCK: Global halt, strategy halt, or symbol halt."""
    check_name = "kill_switch"

    if kill_state.global_halt:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message=(
                f"Global trading halt is active. "
                f"Reason: {kill_state.halt_reason or 'unspecified'}. "
                f"Halted by: {kill_state.halted_by or 'unknown'} "
                f"at {kill_state.halted_at.isoformat() if kill_state.halted_at else 'unknown time'}."
            ),
            metrics={"global_halt": 1.0},
        )

    if strategy_name in kill_state.halted_strategies:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message=f"Strategy '{strategy_name}' is individually halted.",
            metrics={"strategy_halt": 1.0},
        )

    if symbol in kill_state.halted_symbols:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message=f"Symbol '{symbol}' is individually halted.",
            metrics={"symbol_halt": 1.0},
        )

    return RiskCheckResult(
        check_name=check_name,
        passed=True,
        is_hard_block=True,
        message="No kill switches engaged.",
        metrics={"global_halt": 0.0, "strategy_halt": 0.0, "symbol_halt": 0.0},
    )


def check_daily_drawdown(
    realized_pnl_today: Decimal,
    unrealized_pnl: Decimal,
    account_value: Decimal,
    max_daily_drawdown_pct: float,
) -> RiskCheckResult:
    """HARD BLOCK: Halt trading if daily loss exceeds limit."""
    check_name = "daily_drawdown"

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message="Account value is zero or negative — cannot assess drawdown.",
            metrics={"account_value": float(account_value)},
        )

    total_pnl_today = realized_pnl_today + unrealized_pnl
    drawdown_pct = float(-total_pnl_today / account_value) if total_pnl_today < 0 else 0.0

    passed = drawdown_pct < max_daily_drawdown_pct
    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Daily drawdown {drawdown_pct:.2%} {'within' if passed else 'EXCEEDS'} "
            f"limit of {max_daily_drawdown_pct:.2%}. "
            f"Realized: {realized_pnl_today:+.2f}, Unrealized: {unrealized_pnl:+.2f}."
        ),
        metrics={
            "drawdown_pct": drawdown_pct,
            "max_daily_drawdown_pct": max_daily_drawdown_pct,
            "realized_pnl_today": float(realized_pnl_today),
            "unrealized_pnl": float(unrealized_pnl),
        },
    )


def check_weekly_drawdown(
    realized_pnl_week: Decimal,
    account_value: Decimal,
    max_weekly_drawdown_pct: float = 0.04,
) -> RiskCheckResult:
    """HARD BLOCK: Halt if weekly loss exceeds 4% of account."""
    check_name = "weekly_drawdown"

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message="Account value is zero or negative — cannot assess weekly drawdown.",
            metrics={"account_value": float(account_value)},
        )

    weekly_loss_pct = float(-realized_pnl_week / account_value) if realized_pnl_week < 0 else 0.0
    passed = weekly_loss_pct < max_weekly_drawdown_pct

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Weekly drawdown {weekly_loss_pct:.2%} {'within' if passed else 'EXCEEDS'} "
            f"limit of {max_weekly_drawdown_pct:.2%}."
        ),
        metrics={
            "weekly_loss_pct": weekly_loss_pct,
            "max_weekly_drawdown_pct": max_weekly_drawdown_pct,
            "realized_pnl_week": float(realized_pnl_week),
        },
    )


def check_max_concurrent_positions(
    open_position_count: int,
    max_concurrent: int,
) -> RiskCheckResult:
    """HARD BLOCK: No new positions when at capacity."""
    check_name = "max_concurrent_positions"
    passed = open_position_count < max_concurrent

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Open positions: {open_position_count} / {max_concurrent} max. "
            f"{'Capacity available.' if passed else 'AT CAPACITY — no new positions.'}"
        ),
        metrics={
            "open_position_count": float(open_position_count),
            "max_concurrent": float(max_concurrent),
            "utilization_pct": open_position_count / max(max_concurrent, 1),
        },
    )


def check_per_trade_risk(
    proposed_risk_amount: Decimal,
    account_value: Decimal,
    max_trade_risk_pct: float = 0.02,
) -> RiskCheckResult:
    """HARD BLOCK: Single trade can't risk more than max_trade_risk_pct of account."""
    check_name = "per_trade_risk"

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message="Account value is zero or negative — cannot assess per-trade risk.",
            metrics={"account_value": float(account_value)},
        )

    risk_pct = float(proposed_risk_amount / account_value)
    passed = risk_pct <= max_trade_risk_pct

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Trade risk {risk_pct:.2%} {'within' if passed else 'EXCEEDS'} "
            f"limit of {max_trade_risk_pct:.2%} "
            f"(${proposed_risk_amount:.2f} of ${account_value:.2f} account)."
        ),
        metrics={
            "risk_pct": risk_pct,
            "max_trade_risk_pct": max_trade_risk_pct,
            "proposed_risk_amount": float(proposed_risk_amount),
            "account_value": float(account_value),
        },
    )


def check_symbol_concentration(
    symbol: str,
    proposed_notional: Decimal,
    current_positions: dict[str, Decimal],
    account_value: Decimal,
    max_symbol_pct: float = 0.10,
) -> RiskCheckResult:
    """HARD BLOCK: No position > 10% of account in one symbol."""
    check_name = "symbol_concentration"

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message="Account value is zero or negative — cannot assess symbol concentration.",
            metrics={"account_value": float(account_value)},
        )

    existing_notional = current_positions.get(symbol, Decimal("0"))
    total_notional = existing_notional + proposed_notional
    concentration_pct = float(total_notional / account_value)
    passed = concentration_pct <= max_symbol_pct

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"{symbol} concentration {concentration_pct:.2%} {'within' if passed else 'EXCEEDS'} "
            f"limit of {max_symbol_pct:.2%} "
            f"(existing: ${existing_notional:.2f}, adding: ${proposed_notional:.2f})."
        ),
        metrics={
            "concentration_pct": concentration_pct,
            "max_symbol_pct": max_symbol_pct,
            "existing_notional": float(existing_notional),
            "proposed_notional": float(proposed_notional),
            "total_notional": float(total_notional),
        },
    )


def check_gross_exposure(
    proposed_notional: Decimal,
    current_gross_exposure: Decimal,
    account_value: Decimal,
    max_gross_pct: float = 0.80,
) -> RiskCheckResult:
    """HARD BLOCK: Total exposure cap."""
    check_name = "gross_exposure"

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message="Account value is zero or negative — cannot assess gross exposure.",
            metrics={"account_value": float(account_value)},
        )

    projected_exposure = current_gross_exposure + proposed_notional
    projected_pct = float(projected_exposure / account_value)
    passed = projected_pct <= max_gross_pct

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Projected gross exposure {projected_pct:.2%} {'within' if passed else 'EXCEEDS'} "
            f"cap of {max_gross_pct:.2%} "
            f"(current: ${current_gross_exposure:.2f}, adding: ${proposed_notional:.2f})."
        ),
        metrics={
            "projected_pct": projected_pct,
            "max_gross_pct": max_gross_pct,
            "current_gross_exposure": float(current_gross_exposure),
            "proposed_notional": float(proposed_notional),
        },
    )


def check_spread_threshold(
    spread_bps: float,
    max_spread_bps: float = 30.0,
) -> RiskCheckResult:
    """HARD BLOCK: Wide spreads mean bad fills. Don't trade."""
    check_name = "spread_threshold"
    passed = spread_bps <= max_spread_bps

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=(
            f"Spread {spread_bps:.1f} bps {'acceptable' if passed else 'TOO WIDE'} "
            f"(limit: {max_spread_bps:.1f} bps). "
            f"{'Fill quality acceptable.' if passed else 'Wide spread indicates poor liquidity or volatility event.'}"
        ),
        metrics={
            "spread_bps": spread_bps,
            "max_spread_bps": max_spread_bps,
        },
    )


def check_liquidity_threshold(
    avg_daily_volume: int,
    proposed_shares: int,
    min_adv: int = 500_000,
    max_adv_pct: float = 0.01,
) -> RiskCheckResult:
    """HARD BLOCK: Shares must be tradeable without moving market."""
    check_name = "liquidity_threshold"

    adv_too_low = avg_daily_volume < min_adv
    max_shares = int(avg_daily_volume * max_adv_pct)
    order_too_large = proposed_shares > max_shares

    passed = not adv_too_low and not order_too_large

    if adv_too_low:
        message = (
            f"ADV {avg_daily_volume:,} shares below minimum {min_adv:,}. "
            f"Stock is illiquid — market impact risk too high."
        )
    elif order_too_large:
        message = (
            f"Order size {proposed_shares:,} shares exceeds {max_adv_pct:.1%} of ADV "
            f"({max_shares:,} shares max). Would cause unacceptable market impact."
        )
    else:
        message = (
            f"Liquidity acceptable. ADV: {avg_daily_volume:,}, "
            f"order: {proposed_shares:,} ({proposed_shares / avg_daily_volume:.3%} of ADV)."
        )

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=True,
        message=message,
        metrics={
            "avg_daily_volume": float(avg_daily_volume),
            "proposed_shares": float(proposed_shares),
            "adv_pct": proposed_shares / max(avg_daily_volume, 1),
            "max_adv_pct": max_adv_pct,
            "min_adv": float(min_adv),
        },
    )


def check_no_trade_window(
    current_time: datetime,
    market_open: time,
    market_close: time,
) -> RiskCheckResult:
    """HARD BLOCK: No trades in final 5 minutes of session or pre-market/after-hours."""
    check_name = "no_trade_window"

    current_t = current_time.time()

    # Pre-market / after-hours check
    if current_t < market_open or current_t >= market_close:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message=(
                f"Outside regular trading hours. "
                f"Current time: {current_t.strftime('%H:%M:%S')}, "
                f"Market: {market_open.strftime('%H:%M')} - {market_close.strftime('%H:%M')}."
            ),
            metrics={
                "current_hour": current_t.hour + current_t.minute / 60,
                "market_open_hour": market_open.hour + market_open.minute / 60,
                "market_close_hour": market_close.hour + market_close.minute / 60,
            },
        )

    # Final 5-minute window check
    close_dt = current_time.replace(
        hour=market_close.hour,
        minute=market_close.minute,
        second=market_close.second,
        microsecond=0,
    )
    from datetime import timedelta
    blackout_start = close_dt - timedelta(minutes=5)

    if current_time >= blackout_start:
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=True,
            message=(
                f"In final 5-minute no-trade window before market close. "
                f"No new orders until next session. "
                f"Current: {current_t.strftime('%H:%M:%S')}, "
                f"Close: {market_close.strftime('%H:%M')}."
            ),
            metrics={
                "minutes_to_close": (close_dt - current_time).total_seconds() / 60,
            },
        )

    minutes_to_close = (close_dt - current_time).total_seconds() / 60
    return RiskCheckResult(
        check_name=check_name,
        passed=True,
        is_hard_block=True,
        message=(
            f"Within regular trading hours. "
            f"{minutes_to_close:.0f} minutes until close blackout window."
        ),
        metrics={
            "minutes_to_close": minutes_to_close,
        },
    )


# ---------------------------------------------------------------------------
# Soft checks
# ---------------------------------------------------------------------------


def check_consecutive_losses_cooldown(
    recent_trades: list[dict],
    max_consecutive_losses: int = 3,
    cooldown_minutes: int = 30,
    hard_block: bool = False,
) -> RiskCheckResult:
    """SOFT: Recommend pause after consecutive losses. Soft by default, configurable to hard."""
    check_name = "consecutive_losses_cooldown"

    if not recent_trades:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=hard_block,
            message="No recent trade history.",
            metrics={"consecutive_losses": 0.0},
        )

    # Count consecutive losses from most recent trade backward
    consecutive_losses = 0
    last_loss_time: datetime | None = None

    for trade in reversed(recent_trades):
        pnl = trade.get("pnl", 0)
        if pnl is None or pnl >= 0:
            break
        consecutive_losses += 1
        if consecutive_losses == 1:
            closed_at = trade.get("closed_at") or trade.get("timestamp")
            if isinstance(closed_at, str):
                from datetime import datetime as _dt
                try:
                    last_loss_time = _dt.fromisoformat(closed_at)
                except ValueError:
                    last_loss_time = None
            elif isinstance(closed_at, datetime):
                last_loss_time = closed_at

    if consecutive_losses < max_consecutive_losses:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=hard_block,
            message=f"{consecutive_losses} consecutive losses (threshold: {max_consecutive_losses}). OK.",
            metrics={"consecutive_losses": float(consecutive_losses)},
        )

    # Check if cooldown has expired
    if last_loss_time is not None:
        from datetime import timezone, timedelta
        now = datetime.now(tz=timezone.utc) if last_loss_time.tzinfo else datetime.utcnow()
        elapsed_minutes = (now - last_loss_time).total_seconds() / 60
        if elapsed_minutes >= cooldown_minutes:
            return RiskCheckResult(
                check_name=check_name,
                passed=True,
                is_hard_block=hard_block,
                message=(
                    f"{consecutive_losses} consecutive losses but cooldown of {cooldown_minutes}m "
                    f"has elapsed ({elapsed_minutes:.0f}m ago). Proceeding with caution."
                ),
                metrics={
                    "consecutive_losses": float(consecutive_losses),
                    "elapsed_minutes": elapsed_minutes,
                    "cooldown_minutes": float(cooldown_minutes),
                },
            )
        remaining = cooldown_minutes - elapsed_minutes
        return RiskCheckResult(
            check_name=check_name,
            passed=False,
            is_hard_block=hard_block,
            message=(
                f"{consecutive_losses} consecutive losses. "
                f"Cooldown active: {remaining:.0f}m remaining before next trade."
            ),
            metrics={
                "consecutive_losses": float(consecutive_losses),
                "elapsed_minutes": elapsed_minutes,
                "remaining_minutes": remaining,
                "cooldown_minutes": float(cooldown_minutes),
            },
        )

    return RiskCheckResult(
        check_name=check_name,
        passed=False,
        is_hard_block=hard_block,
        message=f"{consecutive_losses} consecutive losses. Recommend cooldown of {cooldown_minutes}m.",
        metrics={"consecutive_losses": float(consecutive_losses)},
    )


def check_sector_concentration(
    symbol_sector: str | None,
    proposed_notional: Decimal,
    sector_exposures: dict[str, Decimal],
    account_value: Decimal,
    max_sector_pct: float = 0.25,
) -> RiskCheckResult:
    """SOFT: Sector concentration warning."""
    check_name = "sector_concentration"

    if symbol_sector is None:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=False,
            message="Sector data unavailable — skipping sector concentration check.",
            metrics={},
        )

    if account_value <= 0:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=False,
            message="Account value zero — skipping sector concentration check.",
            metrics={},
        )

    current_sector_exposure = sector_exposures.get(symbol_sector, Decimal("0"))
    projected_exposure = current_sector_exposure + proposed_notional
    concentration_pct = float(projected_exposure / account_value)
    passed = concentration_pct <= max_sector_pct

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=False,
        message=(
            f"Sector '{symbol_sector}' concentration {concentration_pct:.2%} "
            f"{'within' if passed else 'EXCEEDS'} limit of {max_sector_pct:.2%}."
        ),
        metrics={
            "sector": 0.0,  # sentinel for sector name in message
            "concentration_pct": concentration_pct,
            "max_sector_pct": max_sector_pct,
            "current_sector_exposure": float(current_sector_exposure),
            "proposed_notional": float(proposed_notional),
        },
    )


def check_correlated_exposure(
    symbol: str,
    proposed_side: OrderSide,
    current_positions: dict[str, str],  # symbol -> side
    correlation_matrix: dict[str, dict[str, float]] | None,
) -> RiskCheckResult:
    """SOFT: Warn if adding correlated position. Skip gracefully if correlation data unavailable."""
    check_name = "correlated_exposure"

    if not correlation_matrix:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=False,
            message="Correlation data unavailable — skipping correlated exposure check.",
            metrics={},
        )

    symbol_correlations = correlation_matrix.get(symbol, {})
    if not symbol_correlations:
        return RiskCheckResult(
            check_name=check_name,
            passed=True,
            is_hard_block=False,
            message=f"No correlation data for {symbol} — skipping.",
            metrics={},
        )

    HIGH_CORRELATION_THRESHOLD = 0.70
    highly_correlated: list[str] = []

    for pos_symbol, pos_side in current_positions.items():
        if pos_symbol == symbol:
            continue
        corr = symbol_correlations.get(pos_symbol, 0.0)
        # Correlated positions in same direction amplify risk
        if abs(corr) >= HIGH_CORRELATION_THRESHOLD and pos_side == proposed_side.value:
            highly_correlated.append(f"{pos_symbol} (r={corr:.2f})")

    passed = len(highly_correlated) == 0
    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=False,
        message=(
            f"No highly correlated existing positions."
            if passed
            else f"Adding {symbol} creates correlated exposure with: {', '.join(highly_correlated)}. "
            f"Consider portfolio diversification."
        ),
        metrics={
            "correlated_position_count": float(len(highly_correlated)),
            "correlation_threshold": HIGH_CORRELATION_THRESHOLD,
        },
    )


def check_slippage_estimate(
    order_type: str,
    spread_bps: float,
    proposed_shares: int,
    avg_daily_volume: int,
) -> RiskCheckResult:
    """SOFT: Estimate slippage cost and warn if excessive (> 0.1% of trade value)."""
    check_name = "slippage_estimate"

    # Market impact component: proportional to order size vs ADV
    adv_pct = proposed_shares / max(avg_daily_volume, 1)
    market_impact_bps = adv_pct * 100 * 10  # rough square-root law approximation (simplified)

    # Spread component: pay half spread on limit, full spread on market
    if order_type.lower() in ("market", "stop"):
        spread_cost_bps = spread_bps  # full spread
    else:
        spread_cost_bps = spread_bps / 2  # half spread for limits

    total_slippage_bps = spread_cost_bps + market_impact_bps
    WARN_THRESHOLD_BPS = 10.0  # 0.1%

    passed = total_slippage_bps <= WARN_THRESHOLD_BPS

    return RiskCheckResult(
        check_name=check_name,
        passed=passed,
        is_hard_block=False,
        message=(
            f"Estimated slippage {total_slippage_bps:.1f} bps "
            f"({'within' if passed else 'exceeds'} {WARN_THRESHOLD_BPS:.1f} bps threshold). "
            f"Spread cost: {spread_cost_bps:.1f} bps, market impact: {market_impact_bps:.1f} bps."
        ),
        metrics={
            "total_slippage_bps": total_slippage_bps,
            "spread_cost_bps": spread_cost_bps,
            "market_impact_bps": market_impact_bps,
            "warn_threshold_bps": WARN_THRESHOLD_BPS,
            "adv_pct": adv_pct,
        },
    )
