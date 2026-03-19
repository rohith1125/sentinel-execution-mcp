"""
BrokerAdapter protocol and shared order models.

All broker implementations must satisfy this interface. The protocol design
ensures that PaperBroker and AlpacaLiveBroker are interchangeable at runtime.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from sentinel.domain.types import OrderSide, OrderStatus, OrderType, TimeInForce


class OrderRequest(BaseModel):
    """Immutable order submission request. caller provides the idempotency key."""

    client_order_id: str  # idempotency key, caller provides
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: int
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    strategy_id: str | None = None

    model_config = {"frozen": True}


class OrderUpdate(BaseModel):
    """Broker response for any order state transition."""

    broker_order_id: str
    client_order_id: str = ""
    status: OrderStatus
    filled_qty: int = 0
    filled_avg_price: Decimal | None = None
    rejection_reason: str | None = None
    timestamp: datetime

    model_config = {"frozen": True}


@runtime_checkable
class BrokerAdapter(Protocol):
    """
    Protocol that all brokers implement. Brokers are fully replaceable.

    Implementation note: every method is async. Brokers must not block the event loop.
    """

    async def submit_order(self, request: OrderRequest) -> OrderUpdate:
        """Submit a new order. Must be idempotent on client_order_id."""
        ...

    async def cancel_order(self, broker_order_id: str) -> OrderUpdate:
        """Request cancellation. Returns final status."""
        ...

    async def get_order(self, broker_order_id: str) -> OrderUpdate:
        """Fetch current status of an order by broker ID."""
        ...

    async def get_positions(self) -> list[dict]:
        """Return current open positions as a list of dicts."""
        ...

    async def get_account(self) -> dict:
        """Return account summary: cash, equity, buying power, etc."""
        ...

    async def is_market_open(self) -> bool:
        """True if regular session is currently open."""
        ...
