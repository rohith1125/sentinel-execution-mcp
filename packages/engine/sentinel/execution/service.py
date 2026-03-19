"""
ExecutionService — orchestrates the full order lifecycle.

Flow:
1. Generate client_order_id (idempotent)
2. Persist order with PENDING status
3. Submit to broker
4. Update order with broker response
5. Fire audit event
6. Return result
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from sentinel.db.models import Order, Position
from sentinel.domain.types import OrderSide, OrderStatus
from sentinel.execution.broker import BrokerAdapter, OrderRequest, OrderUpdate
from sentinel.risk.firewall import PortfolioState, PositionSummary

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from sentinel.audit.journal import AuditJournal
    from sentinel.decision.models import DecisionResult
    from sentinel.risk.models import RiskAssessment

logger = logging.getLogger(__name__)


class ExecutionService:
    """
    Orchestrates the full order lifecycle from submission through audit.

    All dependencies are injected: broker, database session, and audit journal.
    This service never raises — failures are recorded and returned as REJECTED updates.
    """

    def __init__(
        self,
        broker: BrokerAdapter,
        db: "AsyncSession",
        audit_journal: "AuditJournal",
    ) -> None:
        self._broker = broker
        self._db = db
        self._journal = audit_journal

    # ------------------------------------------------------------------
    # Order submission
    # ------------------------------------------------------------------

    async def submit_order(
        self,
        request: OrderRequest,
        risk_assessment: "RiskAssessment",
        decision_result: "DecisionResult",
        sizing_details: dict,
    ) -> OrderUpdate:
        """Full order submission with complete audit trail."""
        now = datetime.now(tz=timezone.utc)

        # 1. Persist order as PENDING
        db_order = await self._persist_order(request, now)

        # 2. Submit to broker
        try:
            update = await self._broker.submit_order(request)
        except Exception as exc:
            logger.exception(
                "ExecutionService: broker submission raised unexpectedly for %s",
                request.client_order_id,
            )
            update = OrderUpdate(
                broker_order_id="",
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Broker error: {exc}",
                timestamp=now,
            )

        # 3. Update order record with broker response
        try:
            await self._update_order_record(db_order, update)
        except Exception:
            logger.exception(
                "ExecutionService: failed to update order record for %s",
                request.client_order_id,
            )

        # 4. Record audit event
        try:
            execution_details = {
                "broker_order_id": update.broker_order_id,
                "status": update.status.value,
                "filled_qty": update.filled_qty,
                "filled_avg_price": str(update.filled_avg_price) if update.filled_avg_price else None,
                "rejection_reason": update.rejection_reason,
                "submitted_at": now.isoformat(),
            }
            from sentinel.domain.types import DecisionOutcome
            outcome = (
                DecisionOutcome.EXECUTED
                if update.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED, OrderStatus.ACCEPTED)
                else DecisionOutcome.REJECTED
            )
            await self._journal.record_trade_decision(
                symbol=request.symbol,
                strategy_id=request.strategy_id or "",
                regime_snapshot={},
                signal_details={},
                risk_check_results=[
                    {
                        "check_name": r.check_name,
                        "passed": r.passed,
                        "is_hard_block": r.is_hard_block,
                        "message": r.message,
                    }
                    for r in risk_assessment.results
                ],
                decision_outcome=outcome,
                decision_explanation=risk_assessment.to_explanation(),
                sizing_details=sizing_details,
                execution_details=execution_details,
            )
        except Exception:
            logger.exception(
                "ExecutionService: failed to record audit event for %s. "
                "Trading continues — audit failure must not block execution.",
                request.client_order_id,
            )

        return update

    async def cancel_order(self, order_id: str, reason: str) -> OrderUpdate:
        """Cancel an order by broker order ID."""
        logger.info("ExecutionService: cancelling order %s. Reason: %s", order_id, reason)
        try:
            update = await self._broker.cancel_order(order_id)
        except Exception as exc:
            logger.exception("ExecutionService: cancel raised for order %s", order_id)
            return OrderUpdate(
                broker_order_id=order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Cancel error: {exc}",
                timestamp=datetime.now(tz=timezone.utc),
            )
        return update

    # ------------------------------------------------------------------
    # Portfolio state
    # ------------------------------------------------------------------

    async def get_portfolio_state(self) -> PortfolioState:
        """Compute current portfolio state from broker positions + today's P&L."""
        account = await self._broker.get_account()
        raw_positions = await self._broker.get_positions()

        account_value = Decimal(str(account.get("equity", account.get("cash", "0"))))
        cash = Decimal(str(account.get("cash", "0")))

        positions: dict[str, PositionSummary] = {}
        gross_exposure = Decimal("0")

        for pos in raw_positions:
            symbol = pos.get("symbol", "")
            side = pos.get("side", "long")
            shares_raw = pos.get("qty", pos.get("shares", "0"))
            shares = int(Decimal(str(shares_raw)))
            avg_price_raw = pos.get("avg_entry_price", pos.get("avg_entry_price", "0"))
            avg_price = Decimal(str(avg_price_raw))
            notional = Decimal(str(shares)) * avg_price

            positions[symbol] = PositionSummary(
                symbol=symbol,
                side=side,
                shares=shares,
                notional_value=notional,
                sector=pos.get("sector"),
            )
            gross_exposure += notional

        # P&L from account
        realized_pnl_today = Decimal(str(account.get("realized_pnl", account.get("day_trade_buying_power", "0"))))
        unrealized_pnl = Decimal(str(account.get("unrealized_pl", account.get("unrealized_pnl", "0"))))

        # Fetch recent trades for cooldown check
        recent_trades = await self._get_recent_trades(limit=20)

        return PortfolioState(
            account_value=account_value,
            cash=cash,
            positions=positions,
            realized_pnl_today=realized_pnl_today,
            realized_pnl_week=Decimal("0"),  # requires separate query
            unrealized_pnl=unrealized_pnl,
            gross_exposure=gross_exposure,
            open_position_count=len(positions),
            recent_trades=recent_trades,
        )

    # ------------------------------------------------------------------
    # Emergency operations
    # ------------------------------------------------------------------

    async def flatten_all(self, reason: str, operator: str) -> list[OrderUpdate]:
        """
        Emergency: market-sell all open positions immediately.

        This is a last resort. Every position gets a market sell order.
        Failures are logged but do not stop subsequent flattening attempts.
        """
        logger.critical(
            "FLATTEN ALL triggered by %s. Reason: %s", operator, reason
        )
        updates: list[OrderUpdate] = []
        positions = await self._broker.get_positions()

        for pos in positions:
            symbol = pos.get("symbol", "")
            shares_raw = pos.get("qty", pos.get("shares", "0"))
            shares = int(Decimal(str(shares_raw)))
            side_str = pos.get("side", "long")

            if shares <= 0:
                continue

            # Determine which side to place
            sell_side = OrderSide.SELL if side_str in ("long", "buy") else OrderSide.BUY

            from sentinel.domain.types import OrderType, TimeInForce
            request = OrderRequest(
                client_order_id=f"FLATTEN-{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                side=sell_side,
                order_type=OrderType.MARKET,
                quantity=shares,
                time_in_force=TimeInForce.DAY,
            )

            try:
                update = await self._broker.submit_order(request)
                updates.append(update)
                logger.warning(
                    "Flatten: %s %d shares of %s -> status %s",
                    sell_side.value,
                    shares,
                    symbol,
                    update.status.value,
                )
            except Exception:
                logger.exception("Flatten: failed to submit flatten order for %s", symbol)

        return updates

    async def sync_fills(self) -> int:
        """
        Poll broker for fill updates on open orders.
        Returns count of updated orders.
        """
        from sqlalchemy import select

        try:
            stmt = select(Order).where(
                Order.status.in_(
                    [
                        OrderStatus.ACCEPTED.value,
                        OrderStatus.SUBMITTED.value,
                        OrderStatus.PARTIALLY_FILLED.value,
                    ]
                )
            )
            result = await self._db.execute(stmt)
            open_orders: list[Order] = list(result.scalars().all())
        except Exception:
            logger.exception("ExecutionService.sync_fills: failed to query open orders")
            return 0

        update_count = 0
        for order in open_orders:
            if not order.broker_order_id:
                continue
            try:
                update = await self._broker.get_order(order.broker_order_id)
                if update.status.value != order.status:
                    await self._update_order_record(order, update)
                    update_count += 1
            except Exception:
                logger.exception(
                    "ExecutionService.sync_fills: failed to sync order %s",
                    order.broker_order_id,
                )

        return update_count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _persist_order(self, request: OrderRequest, now: datetime) -> Order:
        """Create and persist an Order record with PENDING status."""
        order = Order(
            client_order_id=request.client_order_id,
            symbol=request.symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            quantity=request.quantity,
            limit_price=float(request.limit_price) if request.limit_price else None,
            stop_price=float(request.stop_price) if request.stop_price else None,
            time_in_force=request.time_in_force.value,
            strategy_id=request.strategy_id,
            status=OrderStatus.SUBMITTED.value,
            created_at=now,
            updated_at=now,
        )
        self._db.add(order)
        try:
            await self._db.flush()
        except Exception:
            logger.exception(
                "ExecutionService: failed to persist order %s to DB",
                request.client_order_id,
            )
        return order

    async def _update_order_record(self, order: Order, update: OrderUpdate) -> None:
        """Update order record with broker response."""
        order.broker_order_id = update.broker_order_id
        order.status = update.status.value
        order.filled_qty = update.filled_qty
        if update.filled_avg_price is not None:
            order.filled_avg_price = float(update.filled_avg_price)
        order.rejection_reason = update.rejection_reason
        order.updated_at = datetime.now(tz=timezone.utc)
        try:
            await self._db.flush()
        except Exception:
            logger.exception(
                "ExecutionService: failed to update order record for broker_id=%s",
                update.broker_order_id,
            )

    async def _get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Fetch recent closed trades from the journal."""
        from sqlalchemy import select, desc
        try:
            from sentinel.db.models import TradeJournal
            stmt = (
                select(TradeJournal)
                .order_by(desc(TradeJournal.closed_at))
                .limit(limit)
            )
            result = await self._db.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "symbol": t.symbol,
                    "pnl": float(t.pnl) if t.pnl is not None else 0.0,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                }
                for t in rows
            ]
        except Exception:
            logger.exception("ExecutionService: failed to fetch recent trades")
            return []
