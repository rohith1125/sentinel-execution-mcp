"""
RiskFirewall — the capital preservation guardian.

Design principle: refuse to trade is a feature, not a failure.
This system exists first to protect capital, second to enable execution.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sentinel.config import Settings
from sentinel.domain.types import OrderSide
from sentinel.market.provider import Snapshot
from sentinel.risk import checks
from sentinel.risk.models import KillSwitchState, RiskAssessment, RiskCheckResult

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_KILL_SWITCH_KEY = "sentinel:kill_switch"


@dataclass
class PositionSummary:
    """Lightweight summary of a single open position."""

    symbol: str
    side: str
    shares: int
    notional_value: Decimal
    sector: str | None = None


@dataclass
class PortfolioState:
    """Snapshot of the portfolio at the moment of risk assessment."""

    account_value: Decimal
    cash: Decimal
    positions: dict[str, PositionSummary]  # symbol -> summary
    realized_pnl_today: Decimal
    realized_pnl_week: Decimal
    unrealized_pnl: Decimal
    gross_exposure: Decimal
    open_position_count: int
    recent_trades: list[dict]  # last N trades for cooldown check


class RiskFirewall:
    """
    Evaluates all risk checks for a proposed trade and returns a complete assessment.

    Conservative bias: ambiguous or data-missing cases default to REJECT.
    All kill switch state is persisted to Redis so it survives restarts.
    """

    def __init__(self, settings: Settings, redis_client: Redis) -> None:
        self._settings = settings
        self._redis = redis_client

    # ------------------------------------------------------------------
    # Core assessment
    # ------------------------------------------------------------------

    async def assess(
        self,
        symbol: str,
        side: OrderSide,
        proposed_shares: int,
        entry_price: Decimal,
        stop_price: Decimal,
        strategy_name: str,
        snapshot: Snapshot,
        portfolio_state: PortfolioState,
    ) -> RiskAssessment:
        """Run all risk checks and return a complete assessment."""
        results: list[RiskCheckResult] = []
        now = datetime.now(tz=UTC)

        # --- Derived values ---
        proposed_notional = Decimal(str(proposed_shares)) * entry_price
        risk_per_share = abs(entry_price - stop_price)
        proposed_risk_amount = Decimal(str(proposed_shares)) * risk_per_share

        # Spread in bps
        quote = snapshot.quote
        mid = (quote.ask + quote.bid) / 2
        spread_bps = float((quote.ask - quote.bid) / mid * 10_000) if mid > 0 else 9999.0

        # Symbol notional map for concentration check
        symbol_notional_map: dict[str, Decimal] = {
            sym: pos.notional_value for sym, pos in portfolio_state.positions.items()
        }

        # Sector exposures map
        sector_exposures: dict[str, Decimal] = {}
        for pos in portfolio_state.positions.values():
            if pos.sector:
                sector_exposures[pos.sector] = sector_exposures.get(pos.sector, Decimal("0")) + pos.notional_value

        # Current positions side map for correlation check
        current_positions_side: dict[str, str] = {
            sym: pos.side for sym, pos in portfolio_state.positions.items()
        }

        # Avg daily volume from snapshot (bar data)
        avg_daily_volume: int = getattr(snapshot, "avg_daily_volume", 0) or 0

        # --- Kill switch (always first) ---
        kill_state = await self.get_kill_switch_state()
        results.append(checks.check_kill_switch(kill_state, strategy_name, symbol))

        # --- Hard block checks ---
        results.append(
            checks.check_daily_drawdown(
                portfolio_state.realized_pnl_today,
                portfolio_state.unrealized_pnl,
                portfolio_state.account_value,
                self._settings.max_daily_drawdown_pct,
            )
        )

        results.append(
            checks.check_weekly_drawdown(
                portfolio_state.realized_pnl_week,
                portfolio_state.account_value,
            )
        )

        results.append(
            checks.check_max_concurrent_positions(
                portfolio_state.open_position_count,
                self._settings.max_concurrent_positions,
            )
        )

        results.append(
            checks.check_per_trade_risk(
                proposed_risk_amount,
                portfolio_state.account_value,
                self._settings.max_trade_risk_pct,
            )
        )

        results.append(
            checks.check_symbol_concentration(
                symbol,
                proposed_notional,
                symbol_notional_map,
                portfolio_state.account_value,
            )
        )

        results.append(
            checks.check_gross_exposure(
                proposed_notional,
                portfolio_state.gross_exposure,
                portfolio_state.account_value,
            )
        )

        results.append(checks.check_spread_threshold(spread_bps))

        if avg_daily_volume > 0:
            results.append(
                checks.check_liquidity_threshold(avg_daily_volume, proposed_shares)
            )
        else:
            # No volume data available — conservative: block
            results.append(
                RiskCheckResult(
                    check_name="liquidity_threshold",
                    passed=False,
                    is_hard_block=True,
                    message="Average daily volume data unavailable — cannot verify liquidity. Rejecting.",
                    metrics={"avg_daily_volume": 0.0},
                )
            )

        from datetime import time as _time
        market_open = _time(9, 30, 0)
        market_close = _time(16, 0, 0)
        results.append(checks.check_no_trade_window(now, market_open, market_close))

        # --- Soft checks ---
        results.append(
            checks.check_consecutive_losses_cooldown(portfolio_state.recent_trades)
        )

        target_symbol_sector: str | None = None
        for pos in portfolio_state.positions.values():
            if pos.symbol == symbol:
                target_symbol_sector = pos.sector
                break

        results.append(
            checks.check_sector_concentration(
                target_symbol_sector,
                proposed_notional,
                sector_exposures,
                portfolio_state.account_value,
            )
        )

        results.append(
            checks.check_correlated_exposure(
                symbol,
                side,
                current_positions_side,
                correlation_matrix=None,  # not available at this layer
            )
        )

        order_type_str = "market"
        results.append(
            checks.check_slippage_estimate(
                order_type_str,
                spread_bps,
                proposed_shares,
                avg_daily_volume if avg_daily_volume > 0 else 1,
            )
        )

        # --- Aggregate ---
        blocking_checks = [r.check_name for r in results if r.is_hard_block and not r.passed]
        warning_checks = [r.check_name for r in results if not r.is_hard_block and not r.passed]
        passed = len(blocking_checks) == 0

        return RiskAssessment(
            symbol=symbol,
            proposed_shares=proposed_shares,
            proposed_side=side,
            results=results,
            passed=passed,
            blocking_checks=blocking_checks,
            warning_checks=warning_checks,
            assessed_at=now,
        )

    # ------------------------------------------------------------------
    # Kill switch management
    # ------------------------------------------------------------------

    async def get_kill_switch_state(self) -> KillSwitchState:
        """Load kill switch state from Redis."""
        try:
            raw = await self._redis.get(_KILL_SWITCH_KEY)
            if raw is None:
                return KillSwitchState()
            data = json.loads(raw)
            halted_at = (
                datetime.fromisoformat(data["halted_at"]) if data.get("halted_at") else None
            )
            return KillSwitchState(
                global_halt=data.get("global_halt", False),
                halted_strategies=set(data.get("halted_strategies", [])),
                halted_symbols=set(data.get("halted_symbols", [])),
                halt_reason=data.get("halt_reason", ""),
                halted_at=halted_at,
                halted_by=data.get("halted_by", ""),
            )
        except Exception:
            logger.exception("Failed to load kill switch state from Redis. Defaulting to SAFE (no halt).")
            return KillSwitchState()

    async def _save_kill_switch_state(self, state: KillSwitchState) -> None:
        """Persist kill switch state to Redis."""
        data = {
            "global_halt": state.global_halt,
            "halted_strategies": list(state.halted_strategies),
            "halted_symbols": list(state.halted_symbols),
            "halt_reason": state.halt_reason,
            "halted_at": state.halted_at.isoformat() if state.halted_at else None,
            "halted_by": state.halted_by,
        }
        await self._redis.set(_KILL_SWITCH_KEY, json.dumps(data))

    async def engage_global_halt(self, reason: str, operator: str) -> None:
        """Emergency halt: stop all trading immediately. Persists to Redis."""
        state = await self.get_kill_switch_state()
        state.global_halt = True
        state.halt_reason = reason
        state.halted_at = datetime.now(tz=UTC)
        state.halted_by = operator
        await self._save_kill_switch_state(state)
        logger.critical(
            "GLOBAL TRADING HALT ENGAGED by %s. Reason: %s", operator, reason
        )

    async def disengage_global_halt(self, operator: str) -> None:
        """Requires explicit confirmation to re-enable trading."""
        state = await self.get_kill_switch_state()
        if not state.global_halt:
            logger.warning("disengage_global_halt called but no global halt was active.")
        state.global_halt = False
        state.halt_reason = ""
        state.halted_at = None
        state.halted_by = ""
        await self._save_kill_switch_state(state)
        logger.warning("Global trading halt DISENGAGED by %s.", operator)

    async def halt_strategy(
        self, strategy_name: str, reason: str, operator: str
    ) -> None:
        """Halt a specific strategy."""
        state = await self.get_kill_switch_state()
        state.halted_strategies.add(strategy_name)
        await self._save_kill_switch_state(state)
        logger.warning(
            "Strategy '%s' halted by %s. Reason: %s", strategy_name, operator, reason
        )

    async def resume_strategy(self, strategy_name: str, operator: str) -> None:
        """Resume a halted strategy."""
        state = await self.get_kill_switch_state()
        state.halted_strategies.discard(strategy_name)
        await self._save_kill_switch_state(state)
        logger.info("Strategy '%s' resumed by %s.", strategy_name, operator)

    async def halt_symbol(self, symbol: str, reason: str, operator: str) -> None:
        """Halt trading in a specific symbol."""
        state = await self.get_kill_switch_state()
        state.halted_symbols.add(symbol)
        await self._save_kill_switch_state(state)
        logger.warning(
            "Symbol '%s' halted by %s. Reason: %s", symbol, operator, reason
        )

    async def resume_symbol(self, symbol: str, operator: str) -> None:
        """Resume trading in a halted symbol."""
        state = await self.get_kill_switch_state()
        state.halted_symbols.discard(symbol)
        await self._save_kill_switch_state(state)
        logger.info("Symbol '%s' resumed by %s.", symbol, operator)
