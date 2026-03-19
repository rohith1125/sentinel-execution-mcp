"""
AuditJournal — immutable event log for every material action in the system.

Design: events are written but never updated. Corrections are new events.
Every trade decision — approval or rejection — gets a record.
This is the system's conscience.

AuditJournal must never raise — exceptions are caught and logged.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sentinel.db.models import AuditEvent, Order, Position, TradeJournal
from sentinel.domain.types import DecisionOutcome

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class AuditJournal:
    """
    Writes immutable audit events for every material action.

    Never raises to callers. All exceptions are absorbed and logged so that
    audit failures never interrupt the trading system.
    """

    def __init__(self, db: "AsyncSession") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Event recording
    # ------------------------------------------------------------------

    async def record_trade_decision(
        self,
        symbol: str,
        strategy_id: str,
        regime_snapshot: dict,
        signal_details: dict,
        risk_check_results: list[dict],
        decision_outcome: DecisionOutcome,
        decision_explanation: str,
        sizing_details: dict,
        execution_details: dict | None = None,
    ) -> AuditEvent | None:
        """Record a complete trade decision with all context."""
        try:
            now = datetime.now(tz=timezone.utc)
            event = AuditEvent(
                event_type="trade_decision",
                symbol=symbol,
                strategy_id=strategy_id,
                timestamp=now,
                regime_snapshot=regime_snapshot,
                signal_details=signal_details,
                risk_check_results=risk_check_results,
                decision_outcome=decision_outcome.value,
                decision_explanation=decision_explanation,
                sizing_details=sizing_details,
                execution_details=execution_details,
                created_at=now,
            )
            self._db.add(event)
            await self._db.flush()
            return event
        except Exception:
            logger.exception(
                "AuditJournal.record_trade_decision: failed to write event for symbol=%s strategy=%s",
                symbol,
                strategy_id,
            )
            return None

    async def record_execution_outcome(
        self,
        audit_event_id: str,
        execution_details: dict,
        outcome: dict,
    ) -> AuditEvent | None:
        """
        Update audit event with actual execution results.
        Creates a new event linked to the original (events are never mutated).
        """
        try:
            now = datetime.now(tz=timezone.utc)
            event = AuditEvent(
                event_type="execution_outcome",
                symbol=outcome.get("symbol", ""),
                strategy_id=outcome.get("strategy_id", ""),
                timestamp=now,
                decision_outcome=outcome.get("status", "unknown"),
                decision_explanation=outcome.get("explanation", ""),
                execution_details={"linked_event_id": audit_event_id, **execution_details},
                outcome=outcome,
                created_at=now,
            )
            self._db.add(event)
            await self._db.flush()
            return event
        except Exception:
            logger.exception(
                "AuditJournal.record_execution_outcome: failed to write event linked to %s",
                audit_event_id,
            )
            return None

    async def record_risk_halt(
        self, reason: str, operator: str, affected_scope: str
    ) -> AuditEvent | None:
        """Record a risk halt event."""
        try:
            now = datetime.now(tz=timezone.utc)
            event = AuditEvent(
                event_type="risk_halt",
                symbol=affected_scope,
                strategy_id="",
                timestamp=now,
                decision_outcome="halted",
                decision_explanation=reason,
                outcome={"operator": operator, "affected_scope": affected_scope, "reason": reason},
                created_at=now,
            )
            self._db.add(event)
            await self._db.flush()
            logger.warning(
                "AuditJournal: risk halt recorded. Scope: %s, Operator: %s, Reason: %s",
                affected_scope,
                operator,
                reason,
            )
            return event
        except Exception:
            logger.exception("AuditJournal.record_risk_halt: failed to write halt event")
            return None

    async def record_strategy_promotion(
        self,
        strategy_name: str,
        from_state: str,
        to_state: str,
        operator: str,
    ) -> AuditEvent | None:
        """Record a strategy state transition."""
        try:
            now = datetime.now(tz=timezone.utc)
            event = AuditEvent(
                event_type="strategy_promotion",
                symbol="",
                strategy_id=strategy_name,
                timestamp=now,
                decision_outcome=f"{from_state}_to_{to_state}",
                decision_explanation=f"Strategy '{strategy_name}' promoted from {from_state} to {to_state} by {operator}",
                outcome={"strategy_name": strategy_name, "from_state": from_state, "to_state": to_state, "operator": operator},
                created_at=now,
            )
            self._db.add(event)
            await self._db.flush()
            return event
        except Exception:
            logger.exception(
                "AuditJournal.record_strategy_promotion: failed for '%s'", strategy_name
            )
            return None

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def explain_trade(self, audit_event_id: str) -> dict:
        """
        Return complete human-readable explanation of a trade decision.
        Includes: signal, regime, risk checks, sizing, execution, outcome.
        """
        try:
            from sqlalchemy import select
            stmt = select(AuditEvent).where(AuditEvent.id == audit_event_id)
            result = await self._db.execute(stmt)
            event = result.scalar_one_or_none()

            if event is None:
                return {"error": f"Audit event '{audit_event_id}' not found"}

            risk_checks = event.risk_check_results or []
            passed_checks = [r for r in risk_checks if r.get("passed")]
            failed_checks = [r for r in risk_checks if not r.get("passed")]

            explanation: dict = {
                "event_id": event.id,
                "event_type": event.event_type,
                "symbol": event.symbol,
                "strategy": event.strategy_id,
                "outcome": event.decision_outcome,
                "recorded_at": event.created_at.isoformat() if event.created_at else None,
                "decision_explanation": event.decision_explanation,
                "signal": event.signal_details or {},
                "regime": event.regime_snapshot or {},
                "risk_assessment": {
                    "passed_checks": passed_checks,
                    "failed_checks": failed_checks,
                    "total_checks": len(risk_checks),
                },
                "sizing": event.sizing_details or {},
                "execution": event.execution_details or {},
            }

            return explanation
        except Exception:
            logger.exception(
                "AuditJournal.explain_trade: failed to explain event %s", audit_event_id
            )
            return {"error": "Failed to retrieve trade explanation", "event_id": audit_event_id}

    async def get_recent_events(
        self,
        limit: int = 50,
        symbol: str | None = None,
        strategy: str | None = None,
    ) -> list[AuditEvent]:
        """Fetch recent audit events with optional filters."""
        try:
            from sqlalchemy import select, desc
            stmt = select(AuditEvent).order_by(desc(AuditEvent.created_at)).limit(limit)
            if symbol:
                stmt = stmt.where(AuditEvent.symbol == symbol)
            if strategy:
                stmt = stmt.where(AuditEvent.strategy_id == strategy)
            result = await self._db.execute(stmt)
            return list(result.scalars().all())
        except Exception:
            logger.exception("AuditJournal.get_recent_events: DB error")
            return []

    # ------------------------------------------------------------------
    # Trade journal
    # ------------------------------------------------------------------

    async def write_to_journal(
        self,
        order: Order,
        position: Position | None,
        regime: dict,
        decision: dict,
    ) -> TradeJournal | None:
        """Write completed trade to human-readable journal."""
        try:
            now = datetime.now(tz=timezone.utc)

            pnl: float | None = None
            r_multiple: float | None = None
            slippage_bps: float | None = None

            if position is not None:
                entry = getattr(position, "avg_entry_price", None)
                stop = getattr(position, "stop_price", None)
                filled_avg = getattr(order, "filled_avg_price", None)

                if entry and filled_avg and stop and entry != stop:
                    risk_per_share = abs(float(entry) - float(stop))
                    pnl_per_share = float(filled_avg) - float(entry)
                    if order.side == "sell":
                        pnl_per_share = float(entry) - float(filled_avg)
                    pnl = pnl_per_share * (order.filled_qty or 0)
                    r_multiple = pnl_per_share / risk_per_share if risk_per_share > 0 else 0.0

                    # Slippage vs mid (simplified: vs entry price)
                    if entry and filled_avg:
                        slippage_pct = abs(float(filled_avg) - float(entry)) / float(entry)
                        slippage_bps = slippage_pct * 10_000

            journal = TradeJournal(
                journal_id=str(uuid.uuid4()),
                strategy_name=order.strategy_id or "",
                symbol=order.symbol,
                side=order.side,
                quantity=order.quantity,
                filled_qty=order.filled_qty or 0,
                entry_price=float(order.filled_avg_price) if order.filled_avg_price else None,
                pnl=pnl,
                r_multiple=r_multiple,
                slippage_bps=slippage_bps,
                regime_label=regime.get("label"),
                regime_mismatch=decision.get("regime_mismatch", False),
                order_count=1,
                fill_count=1 if (order.filled_qty or 0) > 0 else 0,
                opened_at=getattr(position, "opened_at", now) if position else now,
                closed_at=now,
            )
            self._db.add(journal)
            await self._db.flush()
            return journal
        except Exception:
            logger.exception(
                "AuditJournal.write_to_journal: failed for order %s",
                getattr(order, "client_order_id", "unknown"),
            )
            return None
