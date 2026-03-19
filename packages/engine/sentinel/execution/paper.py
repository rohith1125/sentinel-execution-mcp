"""
PaperBroker — full paper trading simulator.

Simulates realistic execution:
- Market orders: fill at ask (buy) or bid (sell) with slippage
- Limit orders: fill when price crosses limit (next bar)
- Stop orders: trigger when price hits stop, then fill at market
- Configurable slippage_bps
- Realistic partial fills for large orders (> 1% of avg volume)
- In-memory order book with Redis persistence
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING

from sentinel.config import Settings
from sentinel.domain.types import OrderSide, OrderStatus, OrderType
from sentinel.execution.broker import OrderRequest, OrderUpdate
from sentinel.market.provider import Bar, Quote

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from sentinel.market.provider import MarketDataService

logger = logging.getLogger(__name__)

_PAPER_ACCOUNT_KEY = "sentinel:paper:account"
_PAPER_ORDERS_PREFIX = "sentinel:paper:orders:"
_PAPER_POSITIONS_KEY = "sentinel:paper:positions"

# Partial fill threshold: orders > 1% of ADV get partial-filled
_PARTIAL_FILL_ADV_PCT = 0.01


class PaperBroker:
    """
    Paper trading simulator implementing the BrokerAdapter protocol.

    All state is persisted to Redis so restarts do not lose account data.
    Starting cash is configurable; default is $100,000.
    """

    def __init__(
        self,
        settings: Settings,
        market_service: "MarketDataService",
        redis: "Redis",
    ) -> None:
        self._settings = settings
        self._market = market_service
        self._redis = redis
        self._slippage_bps: float = getattr(settings, "paper_slippage_bps", 5.0)

    # ------------------------------------------------------------------
    # BrokerAdapter interface
    # ------------------------------------------------------------------

    async def submit_order(self, request: OrderRequest) -> OrderUpdate:
        """Assign broker_order_id, determine fill strategy, execute fill."""
        broker_order_id = f"PAPER-{uuid.uuid4().hex[:12].upper()}"
        now = datetime.now(tz=timezone.utc)

        try:
            quote = await self._market.get_quote(request.symbol)
        except Exception:
            logger.exception("PaperBroker: failed to get quote for %s", request.symbol)
            return OrderUpdate(
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason="Market data unavailable",
                timestamp=now,
            )

        # Determine fill strategy
        if request.order_type == OrderType.MARKET:
            fill_price = self._compute_fill_price(request, quote)
            qty = request.quantity

            # Check partial fill
            avg_daily_volume = await self._get_avg_daily_volume(request.symbol)
            partial, filled_qty = self._should_partial_fill(qty, avg_daily_volume)
            if partial:
                # Store remainder as pending
                remaining_qty = qty - filled_qty
                await self._store_pending_order(
                    broker_order_id,
                    request,
                    remaining_qty,
                    trigger_price=None,
                    is_partial_remainder=True,
                )
            else:
                filled_qty = qty

            await self._apply_fill(request, filled_qty, fill_price, broker_order_id)

            final_status = OrderStatus.FILLED if not partial else OrderStatus.PARTIALLY_FILLED
            update = OrderUpdate(
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status=final_status,
                filled_qty=filled_qty,
                filled_avg_price=fill_price,
                timestamp=now,
            )

        elif request.order_type == OrderType.LIMIT:
            if request.limit_price is None:
                return OrderUpdate(
                    broker_order_id=broker_order_id,
                    client_order_id=request.client_order_id,
                    status=OrderStatus.REJECTED,
                    rejection_reason="Limit price required for LIMIT order",
                    timestamp=now,
                )
            # Check if immediately fillable
            immediately_fillable = (
                (request.side == OrderSide.BUY and quote.ask_price <= request.limit_price)
                or (request.side == OrderSide.SELL and quote.bid_price >= request.limit_price)
            )
            if immediately_fillable:
                fill_price = self._compute_fill_price(request, quote)
                await self._apply_fill(request, request.quantity, fill_price, broker_order_id)
                update = OrderUpdate(
                    broker_order_id=broker_order_id,
                    client_order_id=request.client_order_id,
                    status=OrderStatus.FILLED,
                    filled_qty=request.quantity,
                    filled_avg_price=fill_price,
                    timestamp=now,
                )
            else:
                await self._store_pending_order(
                    broker_order_id, request, request.quantity,
                    trigger_price=request.limit_price,
                )
                update = OrderUpdate(
                    broker_order_id=broker_order_id,
                    client_order_id=request.client_order_id,
                    status=OrderStatus.ACCEPTED,
                    timestamp=now,
                )

        elif request.order_type == OrderType.STOP:
            if request.stop_price is None:
                return OrderUpdate(
                    broker_order_id=broker_order_id,
                    client_order_id=request.client_order_id,
                    status=OrderStatus.REJECTED,
                    rejection_reason="Stop price required for STOP order",
                    timestamp=now,
                )
            await self._store_pending_order(
                broker_order_id, request, request.quantity,
                trigger_price=request.stop_price,
            )
            update = OrderUpdate(
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status=OrderStatus.ACCEPTED,
                timestamp=now,
            )

        else:
            update = OrderUpdate(
                broker_order_id=broker_order_id,
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Unsupported order type: {request.order_type}",
                timestamp=now,
            )

        await self._persist_order_update(broker_order_id, update)
        return update

    async def cancel_order(self, broker_order_id: str) -> OrderUpdate:
        """Cancel a pending order."""
        now = datetime.now(tz=timezone.utc)
        key = f"{_PAPER_ORDERS_PREFIX}{broker_order_id}"
        raw = await self._redis.get(key)

        if raw is None:
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason="Order not found",
                timestamp=now,
            )

        data = json.loads(raw)
        current_status = data.get("status", "")

        if current_status in (OrderStatus.FILLED.value, OrderStatus.CANCELED.value):
            return OrderUpdate(
                broker_order_id=broker_order_id,
                client_order_id=data.get("client_order_id", ""),
                status=OrderStatus(current_status),
                rejection_reason="Order already terminal",
                timestamp=now,
            )

        data["status"] = OrderStatus.CANCELED.value
        data["timestamp"] = now.isoformat()
        await self._redis.set(key, json.dumps(data))

        return OrderUpdate(
            broker_order_id=broker_order_id,
            client_order_id=data.get("client_order_id", ""),
            status=OrderStatus.CANCELED,
            timestamp=now,
        )

    async def get_order(self, broker_order_id: str) -> OrderUpdate:
        """Fetch current status of a paper order."""
        now = datetime.now(tz=timezone.utc)
        key = f"{_PAPER_ORDERS_PREFIX}{broker_order_id}"
        raw = await self._redis.get(key)

        if raw is None:
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason="Order not found",
                timestamp=now,
            )

        data = json.loads(raw)
        return OrderUpdate(
            broker_order_id=broker_order_id,
            client_order_id=data.get("client_order_id", ""),
            status=OrderStatus(data["status"]),
            filled_qty=data.get("filled_qty", 0),
            filled_avg_price=(
                Decimal(str(data["filled_avg_price"])) if data.get("filled_avg_price") else None
            ),
            timestamp=datetime.fromisoformat(data["timestamp"]),
        )

    async def get_positions(self) -> list[dict]:
        """Return current paper positions."""
        raw = await self._redis.get(_PAPER_POSITIONS_KEY)
        if raw is None:
            return []
        return list(json.loads(raw).values())

    async def get_account(self) -> dict:
        """Returns paper account state: cash, equity, positions, day P&L."""
        raw = await self._redis.get(_PAPER_ACCOUNT_KEY)
        if raw is None:
            return {
                "cash": "100000.00",
                "equity": "100000.00",
                "day_pnl": "0.00",
                "positions": [],
            }
        data = json.loads(raw)
        positions = await self.get_positions()
        data["positions"] = positions
        return data

    async def is_market_open(self) -> bool:
        """Check if market is currently open (simplified: check time)."""
        from datetime import time as _time
        now = datetime.now(tz=timezone.utc)
        # EST offset (simplified)
        from datetime import timedelta
        est_now = now - timedelta(hours=5)
        market_open = _time(9, 30)
        market_close = _time(16, 0)
        if est_now.weekday() >= 5:  # Saturday or Sunday
            return False
        return market_open <= est_now.time() < market_close

    # ------------------------------------------------------------------
    # Periodic processing
    # ------------------------------------------------------------------

    async def process_pending_orders(
        self, current_bars: dict[str, Bar]
    ) -> list[OrderUpdate]:
        """
        Called periodically with latest bars.
        Processes all pending limit/stop orders.
        Returns list of OrderUpdates for any orders that filled.
        """
        updates: list[OrderUpdate] = []
        now = datetime.now(tz=timezone.utc)

        # Scan all pending order keys
        pattern = f"{_PAPER_ORDERS_PREFIX}*"
        keys: list[bytes] = []
        async for key in self._redis.scan_iter(pattern):
            keys.append(key)

        for key in keys:
            raw = await self._redis.get(key)
            if raw is None:
                continue
            data = json.loads(raw)

            if data.get("status") not in (
                OrderStatus.ACCEPTED.value,
                OrderStatus.PARTIALLY_FILLED.value,
            ):
                continue

            symbol: str = data["symbol"]
            bar = current_bars.get(symbol)
            if bar is None:
                continue

            order_type = data.get("order_type", "")
            trigger_price = Decimal(str(data["trigger_price"])) if data.get("trigger_price") else None
            side = OrderSide(data["side"])
            broker_order_id: str = data["broker_order_id"]
            remaining_qty: int = data.get("remaining_qty", data.get("quantity", 0))

            triggered = False
            fill_price: Decimal | None = None

            if order_type == OrderType.LIMIT.value and trigger_price is not None:
                if side == OrderSide.BUY and bar.low <= trigger_price:
                    triggered = True
                    fill_price = min(trigger_price, bar.open)
                elif side == OrderSide.SELL and bar.high >= trigger_price:
                    triggered = True
                    fill_price = max(trigger_price, bar.open)

            elif order_type == OrderType.STOP.value and trigger_price is not None:
                if side == OrderSide.BUY and bar.high >= trigger_price:
                    triggered = True
                    # Stop triggered — fill at open of next period (simulate as bar close)
                    fill_price = bar.close
                elif side == OrderSide.SELL and bar.low <= trigger_price:
                    triggered = True
                    fill_price = bar.close

            if triggered and fill_price is not None:
                # Apply slippage
                slippage_multiplier = Decimal(str(self._slippage_bps / 10_000))
                if side == OrderSide.BUY:
                    fill_price = fill_price * (1 + slippage_multiplier)
                else:
                    fill_price = fill_price * (1 - slippage_multiplier)
                fill_price = fill_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Reconstruct a minimal request-like object for _apply_fill
                request = _MinimalOrderInfo(
                    client_order_id=data.get("client_order_id", ""),
                    symbol=symbol,
                    side=side,
                    quantity=remaining_qty,
                )
                await self._apply_fill(request, remaining_qty, fill_price, broker_order_id)  # type: ignore[arg-type]

                data["status"] = OrderStatus.FILLED.value
                data["filled_qty"] = data.get("filled_qty", 0) + remaining_qty
                data["filled_avg_price"] = str(fill_price)
                data["timestamp"] = now.isoformat()
                await self._redis.set(key, json.dumps(data))

                update = OrderUpdate(
                    broker_order_id=broker_order_id,
                    client_order_id=data.get("client_order_id", ""),
                    status=OrderStatus.FILLED,
                    filled_qty=data["filled_qty"],
                    filled_avg_price=fill_price,
                    timestamp=now,
                )
                updates.append(update)

        return updates

    # ------------------------------------------------------------------
    # Account management
    # ------------------------------------------------------------------

    async def get_paper_account_value(self) -> Decimal:
        """Total account value: cash + open position market value."""
        account = await self.get_account()
        cash = Decimal(str(account.get("cash", "100000")))
        positions = await self.get_positions()

        position_value = Decimal("0")
        for pos in positions:
            shares = Decimal(str(pos.get("shares", 0)))
            avg_price = Decimal(str(pos.get("avg_entry_price", 0)))
            position_value += shares * avg_price

        return cash + position_value

    async def reset_paper_account(
        self, starting_cash: Decimal = Decimal("100000")
    ) -> None:
        """Reset paper account to starting state."""
        account_data = {
            "cash": str(starting_cash),
            "equity": str(starting_cash),
            "day_pnl": "0.00",
            "starting_cash": str(starting_cash),
            "reset_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        await self._redis.set(_PAPER_ACCOUNT_KEY, json.dumps(account_data))
        await self._redis.delete(_PAPER_POSITIONS_KEY)
        logger.info("Paper account reset to $%s", starting_cash)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_fill_price(self, request: OrderRequest, quote: Quote) -> Decimal:
        """Apply slippage model: buy fills at ask + slippage, sell at bid - slippage."""
        slippage_mult = Decimal(str(self._slippage_bps / 10_000))
        if request.side == OrderSide.BUY:
            raw_price = quote.ask_price * (1 + slippage_mult)
        else:
            raw_price = quote.bid_price * (1 - slippage_mult)
        return raw_price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    def _should_partial_fill(
        self, quantity: int, avg_daily_volume: int
    ) -> tuple[bool, int]:
        """
        Large orders (> 1% of ADV) get partial fills across multiple ticks.
        Returns (is_partial, filled_qty_this_tick).
        """
        if avg_daily_volume <= 0:
            return False, quantity

        threshold = int(avg_daily_volume * _PARTIAL_FILL_ADV_PCT)
        if quantity <= threshold:
            return False, quantity

        # Fill ~50% of the order on first pass
        filled_this_tick = max(1, quantity // 2)
        return True, filled_this_tick

    async def _get_avg_daily_volume(self, symbol: str) -> int:
        """Attempt to get ADV from market service. Returns 0 on failure."""
        try:
            snapshot = await self._market.get_snapshot(symbol)
            return getattr(snapshot, "avg_daily_volume", 0) or 0
        except Exception:
            return 0

    async def _apply_fill(
        self,
        request: "OrderRequest | _MinimalOrderInfo",
        filled_qty: int,
        fill_price: Decimal,
        broker_order_id: str,
    ) -> None:
        """Update cash and positions after a fill."""
        raw_account = await self._redis.get(_PAPER_ACCOUNT_KEY)
        if raw_account:
            account = json.loads(raw_account)
        else:
            account = {"cash": "100000.00", "equity": "100000.00", "day_pnl": "0.00"}

        cash = Decimal(str(account["cash"]))
        notional = Decimal(str(filled_qty)) * fill_price

        raw_positions = await self._redis.get(_PAPER_POSITIONS_KEY)
        positions: dict[str, dict] = json.loads(raw_positions) if raw_positions else {}

        symbol = request.symbol
        side = request.side

        if side == OrderSide.BUY:
            cash -= notional
            if symbol in positions:
                existing = positions[symbol]
                existing_shares = Decimal(str(existing["shares"]))
                existing_avg = Decimal(str(existing["avg_entry_price"]))
                new_shares = existing_shares + Decimal(str(filled_qty))
                new_avg = (existing_shares * existing_avg + notional) / new_shares
                positions[symbol] = {
                    "symbol": symbol,
                    "side": "buy",
                    "shares": str(new_shares),
                    "avg_entry_price": str(new_avg.quantize(Decimal("0.0001"))),
                }
            else:
                positions[symbol] = {
                    "symbol": symbol,
                    "side": "buy",
                    "shares": str(filled_qty),
                    "avg_entry_price": str(fill_price),
                }
        else:  # SELL
            cash += notional
            if symbol in positions:
                existing = positions[symbol]
                existing_shares = Decimal(str(existing["shares"]))
                remaining_shares = existing_shares - Decimal(str(filled_qty))
                if remaining_shares <= 0:
                    del positions[symbol]
                else:
                    positions[symbol]["shares"] = str(remaining_shares)

        account["cash"] = str(cash.quantize(Decimal("0.01")))
        await self._redis.set(_PAPER_ACCOUNT_KEY, json.dumps(account))
        await self._redis.set(_PAPER_POSITIONS_KEY, json.dumps(positions))

    async def _store_pending_order(
        self,
        broker_order_id: str,
        request: OrderRequest,
        remaining_qty: int,
        trigger_price: Decimal | None,
        is_partial_remainder: bool = False,
    ) -> None:
        """Persist a pending order to Redis for later processing."""
        data = {
            "broker_order_id": broker_order_id,
            "client_order_id": request.client_order_id,
            "symbol": request.symbol,
            "side": request.side.value,
            "order_type": request.order_type.value,
            "quantity": request.quantity,
            "remaining_qty": remaining_qty,
            "limit_price": str(request.limit_price) if request.limit_price else None,
            "stop_price": str(request.stop_price) if request.stop_price else None,
            "trigger_price": str(trigger_price) if trigger_price else None,
            "status": OrderStatus.ACCEPTED.value,
            "filled_qty": request.quantity - remaining_qty,
            "filled_avg_price": None,
            "is_partial_remainder": is_partial_remainder,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        key = f"{_PAPER_ORDERS_PREFIX}{broker_order_id}"
        await self._redis.set(key, json.dumps(data))

    async def _persist_order_update(
        self, broker_order_id: str, update: OrderUpdate
    ) -> None:
        """Store order update in Redis."""
        key = f"{_PAPER_ORDERS_PREFIX}{broker_order_id}"
        existing_raw = await self._redis.get(key)
        if existing_raw:
            data = json.loads(existing_raw)
        else:
            data = {}
        data.update(
            {
                "broker_order_id": broker_order_id,
                "client_order_id": update.client_order_id,
                "status": update.status.value,
                "filled_qty": update.filled_qty,
                "filled_avg_price": str(update.filled_avg_price) if update.filled_avg_price else None,
                "rejection_reason": update.rejection_reason,
                "timestamp": update.timestamp.isoformat(),
            }
        )
        await self._redis.set(key, json.dumps(data))


class _MinimalOrderInfo:
    """Lightweight stand-in for OrderRequest used in internal fill processing."""

    def __init__(
        self,
        client_order_id: str,
        symbol: str,
        side: OrderSide,
        quantity: int,
    ) -> None:
        self.client_order_id = client_order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
