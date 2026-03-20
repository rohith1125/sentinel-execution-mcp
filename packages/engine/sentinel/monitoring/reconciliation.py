"""
Position reconciliation: detect drift between our DB state and the broker.

Runs periodically (configurable, default every 60s) and flags discrepancies.
This is critical for live trading — silent drift means unexpected exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class ReconciliationDiscrepancy:
    symbol: str
    discrepancy_type: str  # "position_missing_in_db", "position_missing_at_broker",
    # "quantity_mismatch", "side_mismatch"
    db_quantity: int | None
    broker_quantity: int | None
    db_side: str | None
    broker_side: str | None
    detected_at: datetime
    severity: str  # "warning" | "critical"


@dataclass
class ReconciliationResult:
    reconciled_at: datetime
    total_positions_checked: int
    discrepancies: list[ReconciliationDiscrepancy]
    is_clean: bool
    summary: str


class PositionReconciler:
    """
    Compares DB positions against broker positions.

    Logic:
    1. Fetch all open positions from DB (environment=current)
    2. Fetch all positions from broker adapter
    3. Compare: symbol by symbol
    4. Flag discrepancies
    5. Auto-resolve minor qty differences (< 1 share rounding)
    6. Escalate critical discrepancies via alert system
    """

    # Qty differences below this threshold are treated as rounding — not flagged.
    _ROUNDING_THRESHOLD = 1

    def __init__(self, broker_adapter: Any, db_session: Any, alert_service: Any) -> None:
        self._broker = broker_adapter
        self._db = db_session
        self._alerts = alert_service
        self._last_result: ReconciliationResult | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def reconcile(self) -> ReconciliationResult:
        """Full reconciliation pass. Returns result and fires alerts for critical discrepancies."""
        now = datetime.utcnow()
        try:
            db_positions, db_error = await self._fetch_db_positions_safe()
            broker_positions, broker_error = await self._fetch_broker_positions_safe()
            fetch_failed = db_error or broker_error

            all_symbols = set(db_positions) | set(broker_positions)
            discrepancies: list[ReconciliationDiscrepancy] = []

            for symbol in all_symbols:
                d = self._compare_symbol(symbol, db_positions, broker_positions, now)
                if d is not None:
                    discrepancies.append(d)

            is_clean = len(discrepancies) == 0 and not fetch_failed
            if fetch_failed:
                errors = []
                if db_error:
                    errors.append(f"db: {db_error}")
                if broker_error:
                    errors.append(f"broker: {broker_error}")
                summary = f"Reconciliation incomplete due to fetch errors: {'; '.join(errors)}"
            else:
                summary = f"Reconciled {len(all_symbols)} symbol(s): " + (
                    "no discrepancies" if is_clean else f"{len(discrepancies)} discrepancy(ies) found"
                )

            result = ReconciliationResult(
                reconciled_at=now,
                total_positions_checked=len(all_symbols),
                discrepancies=discrepancies,
                is_clean=is_clean,
                summary=summary,
            )

            # Fire alerts for critical discrepancies
            critical = [d for d in discrepancies if d.severity == "critical"]
            if critical and self._alerts is not None:
                try:
                    from sentinel.monitoring.alerts import alert_position_reconciliation_failed

                    tmpl = alert_position_reconciliation_failed(critical)
                    await self._alerts.fire_critical(
                        title=tmpl["title"],
                        message=tmpl["message"],
                        source="reconciler",
                        context=tmpl.get("context", {}),
                    )
                except Exception as exc:
                    logger.warning("reconciler.alert_failed", error=str(exc))

            self._last_result = result
            logger.info(
                "reconciler.complete",
                symbols_checked=len(all_symbols),
                discrepancies=len(discrepancies),
                is_clean=is_clean,
            )
            return result

        except Exception as exc:
            logger.exception("reconciler.error", error=str(exc))
            result = ReconciliationResult(
                reconciled_at=now,
                total_positions_checked=0,
                discrepancies=[],
                is_clean=False,
                summary=f"Reconciliation failed: {exc}",
            )
            self._last_result = result
            return result

    async def reconcile_symbol(self, symbol: str) -> ReconciliationDiscrepancy | None:
        """Single-symbol reconciliation."""
        now = datetime.utcnow()
        try:
            db_positions = await self._fetch_db_positions()
            broker_positions = await self._fetch_broker_positions()
            return self._compare_symbol(symbol, db_positions, broker_positions, now)
        except Exception as exc:
            logger.exception("reconciler.symbol_error", symbol=symbol, error=str(exc))
            return None

    async def get_last_result(self) -> ReconciliationResult | None:
        """Return cached last reconciliation result."""
        return self._last_result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_db_positions(self) -> dict[str, dict[str, Any]]:
        """Returns {symbol: {quantity, side}} from DB open positions."""
        try:
            from sqlalchemy import text

            result = await self._db.execute(text("SELECT symbol, quantity, side FROM positions WHERE status = 'open'"))
            rows = result.fetchall()
            return {row.symbol: {"quantity": row.quantity, "side": row.side} for row in rows}
        except Exception as exc:
            logger.warning("reconciler.db_fetch_failed", error=str(exc))
            return {}

    async def _fetch_db_positions_safe(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Returns (positions, error_message). error_message is None on success."""
        try:
            from sqlalchemy import text

            result = await self._db.execute(text("SELECT symbol, quantity, side FROM positions WHERE status = 'open'"))
            rows = result.fetchall()
            return {row.symbol: {"quantity": row.quantity, "side": row.side} for row in rows}, None
        except Exception as exc:
            logger.warning("reconciler.db_fetch_failed", error=str(exc))
            return {}, str(exc)

    async def _fetch_broker_positions_safe(self) -> tuple[dict[str, dict[str, Any]], str | None]:
        """Returns (positions, error_message). error_message is None on success."""
        try:
            positions = await self._broker.get_positions()
            result: dict[str, dict[str, Any]] = {}
            for pos in positions:
                symbol = getattr(pos, "symbol", None) or pos.get("symbol")
                qty = getattr(pos, "quantity", None) or pos.get("quantity", 0)
                side = getattr(pos, "side", None) or pos.get("side", "long")
                result[symbol] = {"quantity": qty, "side": side}
            return result, None
        except Exception as exc:
            logger.warning("reconciler.broker_fetch_failed", error=str(exc))
            return {}, str(exc)

    async def _fetch_broker_positions(self) -> dict[str, dict[str, Any]]:
        """Returns {symbol: {quantity, side}} from broker adapter."""
        positions, _ = await self._fetch_broker_positions_safe()
        return positions

    def _compare_symbol(
        self,
        symbol: str,
        db_positions: dict[str, dict[str, Any]],
        broker_positions: dict[str, dict[str, Any]],
        now: datetime,
    ) -> ReconciliationDiscrepancy | None:
        """Compare a single symbol across DB and broker. Returns discrepancy or None."""
        in_db = symbol in db_positions
        in_broker = symbol in broker_positions

        if in_db and not in_broker:
            return ReconciliationDiscrepancy(
                symbol=symbol,
                discrepancy_type="position_missing_at_broker",
                db_quantity=db_positions[symbol]["quantity"],
                broker_quantity=None,
                db_side=db_positions[symbol]["side"],
                broker_side=None,
                detected_at=now,
                severity="critical",
            )

        if in_broker and not in_db:
            return ReconciliationDiscrepancy(
                symbol=symbol,
                discrepancy_type="position_missing_in_db",
                db_quantity=None,
                broker_quantity=broker_positions[symbol]["quantity"],
                db_side=None,
                broker_side=broker_positions[symbol]["side"],
                detected_at=now,
                severity="critical",
            )

        # Both exist — check side
        db_side = db_positions[symbol]["side"]
        broker_side = broker_positions[symbol]["side"]
        if db_side != broker_side:
            return ReconciliationDiscrepancy(
                symbol=symbol,
                discrepancy_type="side_mismatch",
                db_quantity=db_positions[symbol]["quantity"],
                broker_quantity=broker_positions[symbol]["quantity"],
                db_side=db_side,
                broker_side=broker_side,
                detected_at=now,
                severity="critical",
            )

        # Check quantity
        db_qty = db_positions[symbol]["quantity"]
        broker_qty = broker_positions[symbol]["quantity"]
        qty_diff = abs((db_qty or 0) - (broker_qty or 0))
        if qty_diff >= self._ROUNDING_THRESHOLD:
            severity = "critical" if qty_diff > 5 else "warning"
            return ReconciliationDiscrepancy(
                symbol=symbol,
                discrepancy_type="quantity_mismatch",
                db_quantity=db_qty,
                broker_quantity=broker_qty,
                db_side=db_side,
                broker_side=broker_side,
                detected_at=now,
                severity=severity,
            )

        return None
