"""Domain event dataclasses for the Sentinel event bus."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sentinel.domain.types import (
    AssetClass,
    DecisionOutcome,
    OrderSide,
    OrderType,
    PositionSide,
    RegimeLabel,
    StrategyState,
    TimeInForce,
)


def _now() -> datetime:
    return datetime.utcnow()


def _uuid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainEvent:
    event_id: str = field(default_factory=_uuid)
    occurred_at: datetime = field(default_factory=_now)


# ---------------------------------------------------------------------------
# Order events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderSubmitted(DomainEvent):
    client_order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    order_type: OrderType = OrderType.MARKET
    quantity: Decimal = Decimal("0")
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_id: str = ""
    account_id: str = ""
    environment: str = "paper"


@dataclass(frozen=True)
class OrderFilled(DomainEvent):
    client_order_id: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    filled_qty: Decimal = Decimal("0")
    filled_avg_price: Decimal = Decimal("0")
    strategy_id: str = ""
    account_id: str = ""


@dataclass(frozen=True)
class OrderPartiallyFilled(DomainEvent):
    client_order_id: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    filled_qty: Decimal = Decimal("0")
    remaining_qty: Decimal = Decimal("0")
    filled_avg_price: Decimal = Decimal("0")


@dataclass(frozen=True)
class OrderCancelled(DomainEvent):
    client_order_id: str = ""
    broker_order_id: str = ""
    symbol: str = ""
    reason: str = ""


@dataclass(frozen=True)
class OrderRejected(DomainEvent):
    client_order_id: str = ""
    symbol: str = ""
    rejection_reason: str = ""


# ---------------------------------------------------------------------------
# Position events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionOpened(DomainEvent):
    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    quantity: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    strategy_id: str = ""
    account_id: str = ""
    environment: str = "paper"


@dataclass(frozen=True)
class PositionClosed(DomainEvent):
    symbol: str = ""
    side: PositionSide = PositionSide.LONG
    quantity: Decimal = Decimal("0")
    avg_entry_price: Decimal = Decimal("0")
    exit_price: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")
    exit_reason: str = ""
    strategy_id: str = ""
    account_id: str = ""


@dataclass(frozen=True)
class PositionUpdated(DomainEvent):
    symbol: str = ""
    current_price: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    strategy_id: str = ""


# ---------------------------------------------------------------------------
# Strategy events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyStateChanged(DomainEvent):
    strategy_id: str = ""
    strategy_name: str = ""
    from_state: StrategyState = StrategyState.DRAFT
    to_state: StrategyState = StrategyState.RESEARCH
    changed_by: str = ""
    reason: str = ""


# ---------------------------------------------------------------------------
# Signal / decision events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalGenerated(DomainEvent):
    strategy_id: str = ""
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    confidence: float = 0.0
    regime_label: RegimeLabel = RegimeLabel.UNKNOWN
    signal_details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskDecisionMade(DomainEvent):
    strategy_id: str = ""
    symbol: str = ""
    outcome: DecisionOutcome = DecisionOutcome.REJECTED
    explanation: str = ""
    risk_check_results: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Market data events
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WatchlistSymbolAdded(DomainEvent):
    symbol: str = ""
    asset_class: AssetClass = AssetClass.EQUITY
    group: str | None = None


@dataclass(frozen=True)
class WatchlistSymbolRemoved(DomainEvent):
    symbol: str = ""
