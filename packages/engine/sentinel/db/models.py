"""All SQLAlchemy ORM models for Sentinel Execution MCP."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sentinel.db.base import Base
from sentinel.domain.types import (
    AssetClass,
    DecisionOutcome,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    StrategyState,
    TimeInForce,
)


def _utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# WatchlistEntry
# ---------------------------------------------------------------------------


class WatchlistEntry(Base):
    __tablename__ = "watchlist_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    asset_class: Mapped[str] = mapped_column(
        String(20), nullable=False, default=AssetClass.EQUITY.value
    )
    group_tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True, default=list
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<WatchlistEntry {self.symbol} active={self.is_active}>"


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    client_order_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    order_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    time_in_force: Mapped[str] = mapped_column(String(10), nullable=False, default=TimeInForce.DAY.value)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OrderStatus.PENDING.value, index=True
    )
    filled_qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False, default=Decimal("0"))
    filled_avg_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    trade_journals: Mapped[list[TradeJournal]] = relationship(
        "TradeJournal", back_populates="order", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<Order {self.client_order_id} {self.symbol} {self.side} {self.status}>"


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False, default=PositionSide.LONG.value)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    avg_entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    unrealized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    realized_pnl: Mapped[Decimal] = mapped_column(
        Numeric(18, 8), nullable=False, default=Decimal("0")
    )
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(10), nullable=False, default="paper")

    def __repr__(self) -> str:
        return f"<Position {self.symbol} {self.side} qty={self.quantity}>"


# ---------------------------------------------------------------------------
# StrategyRecord
# ---------------------------------------------------------------------------


class StrategyRecord(Base):
    __tablename__ = "strategy_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(
        String(30), nullable=False, default=StrategyState.DRAFT.value, index=True
    )
    config: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    performance_metrics: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    promoted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    demotion_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    promotions: Mapped[list[StrategyPromotion]] = relationship(
        "StrategyPromotion", back_populates="strategy", lazy="select"
    )

    def __repr__(self) -> str:
        return f"<StrategyRecord {self.name} state={self.state}>"


# ---------------------------------------------------------------------------
# AuditEvent (append-only)
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    """Append-only audit trail for all trading decisions and executions."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    symbol: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    regime_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    signal_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_check_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    decision_outcome: Mapped[str | None] = mapped_column(String(40), nullable=True)
    decision_explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    sizing_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    execution_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    outcome: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:
        return f"<AuditEvent {self.event_type} {self.symbol} {self.timestamp}>"


# ---------------------------------------------------------------------------
# TradeJournal
# ---------------------------------------------------------------------------


class TradeJournal(Base):
    __tablename__ = "trade_journals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("orders.id"), nullable=False, index=True
    )
    entry_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    exit_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    entry_price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    realized_pnl: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    pnl_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)
    mae: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, comment="Max adverse excursion"
    )
    mfe: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 8), nullable=True, comment="Max favorable excursion"
    )
    holding_period_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    regime_at_entry: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)

    order: Mapped[Order] = relationship("Order", back_populates="trade_journals")

    def __repr__(self) -> str:
        return f"<TradeJournal {self.symbol} {self.side} pnl={self.realized_pnl}>"


# ---------------------------------------------------------------------------
# StrategyPromotion
# ---------------------------------------------------------------------------


class StrategyPromotion(Base):
    __tablename__ = "strategy_promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    strategy_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("strategy_records.id"), nullable=False, index=True
    )
    from_state: Mapped[str] = mapped_column(String(30), nullable=False)
    to_state: Mapped[str] = mapped_column(String(30), nullable=False)
    criteria_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metrics_at_promotion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    strategy: Mapped[StrategyRecord] = relationship("StrategyRecord", back_populates="promotions")

    def __repr__(self) -> str:
        return (
            f"<StrategyPromotion {self.strategy_id} "
            f"{self.from_state} -> {self.to_state}>"
        )
