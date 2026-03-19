"""
Integration tests for paper execution lifecycle.
Tests the PaperBroker end-to-end including order submission, fills, and account state.
"""
from __future__ import annotations

import json
import pytest
import pytest_asyncio
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from sentinel.config import Settings
from sentinel.domain.types import OrderSide, OrderStatus, OrderType, TimeInForce
from sentinel.execution.broker import OrderRequest, OrderUpdate
from sentinel.execution.paper import PaperBroker
from sentinel.market.provider import Bar, Quote


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="development",
        database_url="postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test",
        redis_url="redis://localhost:6379/0",
        market_data_provider="mock",
    )


@pytest_asyncio.fixture
async def redis_client():
    """Real Redis client, flushed before each test."""
    import redis.asyncio as aioredis
    client = aioredis.from_url("redis://localhost:6379/0")
    # Clear paper keys only
    async for key in client.scan_iter("sentinel:paper:*"):
        await client.delete(key)
    yield client
    # Cleanup after test
    async for key in client.scan_iter("sentinel:paper:*"):
        await client.delete(key)
    await client.aclose()


def _make_market_service(
    bid: Decimal = Decimal("149.90"),
    ask: Decimal = Decimal("150.10"),
    adv: int = 10_000_000,
) -> AsyncMock:
    """Return a mock MarketDataService with configurable quote/snapshot."""
    svc = AsyncMock()
    quote = Quote(
        symbol="AAPL",
        bid=bid,
        ask=ask,
        bid_size=100,
        ask_size=100,
        timestamp=datetime.now(tz=timezone.utc),
    )
    svc.get_quote.return_value = quote
    snapshot = MagicMock()
    snapshot.avg_daily_volume = adv
    svc.get_snapshot.return_value = snapshot
    return svc


def _make_broker(settings, market_service, redis) -> PaperBroker:
    return PaperBroker(settings=settings, market_service=market_service, redis=redis)


def _order(
    symbol: str = "AAPL",
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: int = 5,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    tif: TimeInForce = TimeInForce.DAY,
) -> OrderRequest:
    import uuid
    return OrderRequest(
        client_order_id=f"test-{uuid.uuid4().hex[:8]}",
        symbol=symbol,
        side=side,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        stop_price=stop_price,
        time_in_force=tif,
    )


def _bar(
    symbol: str = "AAPL",
    open_: Decimal = Decimal("150.00"),
    high: Decimal = Decimal("152.00"),
    low: Decimal = Decimal("148.00"),
    close: Decimal = Decimal("151.00"),
) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=datetime.now(tz=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=500_000,
        vwap=Decimal("150.50"),
    )


# ---------------------------------------------------------------------------
# Test 1: Market order fills immediately
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_market_order_fills_immediately(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    update = await broker.submit_order(_order(side=OrderSide.BUY, quantity=5))

    assert update.status == OrderStatus.FILLED
    assert update.filled_qty == 5
    assert update.filled_avg_price is not None
    # Buy fills at ask + slippage: should be above bid and close to ask
    assert update.filled_avg_price > Decimal("149.90")


# ---------------------------------------------------------------------------
# Test 2: Limit order stays pending until price crosses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_limit_order_fills_when_price_crosses(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    limit_px = Decimal("148.00")
    req = _order(order_type=OrderType.LIMIT, quantity=10, limit_price=limit_px)
    update = await broker.submit_order(req)

    # Should be queued, not filled
    assert update.status == OrderStatus.SUBMITTED
    assert update.filled_qty == 0

    # Now process a bar whose low dips to/below limit price
    bar = _bar(open_=Decimal("150.00"), high=Decimal("150.50"), low=Decimal("147.50"), close=Decimal("149.00"))
    fills = await broker.process_pending_orders({"AAPL": bar})

    assert len(fills) == 1
    assert fills[0].status == OrderStatus.FILLED
    assert fills[0].broker_order_id == update.broker_order_id


# ---------------------------------------------------------------------------
# Test 3: Cancel pending order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_pending_limit_order(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    req = _order(order_type=OrderType.LIMIT, quantity=10, limit_price=Decimal("120.00"))
    update = await broker.submit_order(req)
    assert update.status == OrderStatus.SUBMITTED

    cancel = await broker.cancel_order(update.broker_order_id)
    assert cancel.status == OrderStatus.CANCELLED

    # Confirm via get_order
    fetched = await broker.get_order(update.broker_order_id)
    assert fetched.status == OrderStatus.CANCELLED


# ---------------------------------------------------------------------------
# Test 4: Paper account reset restores starting cash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_account_reset_restores_cash(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    # Buy something to consume cash
    await broker.submit_order(_order(side=OrderSide.BUY, quantity=100))

    # Reset
    await broker.reset_paper_account(starting_cash=Decimal("100000"))
    account = await broker.get_account()

    assert Decimal(account["cash"]) == Decimal("100000")
    assert account["positions"] == []


# ---------------------------------------------------------------------------
# Test 5: DAY orders cancelled on session reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_day_orders_cancelled_on_session_reset(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    req = _order(order_type=OrderType.LIMIT, quantity=10, limit_price=Decimal("120.00"), tif=TimeInForce.DAY)
    update = await broker.submit_order(req)
    assert update.status == OrderStatus.SUBMITTED

    cancelled_count = await broker.reset_for_new_session()
    assert cancelled_count >= 1

    fetched = await broker.get_order(update.broker_order_id)
    assert fetched.status == OrderStatus.CANCELLED


# ---------------------------------------------------------------------------
# Test 6: GTC order survives session reset
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gtc_order_survives_session_reset(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    req = _order(order_type=OrderType.LIMIT, quantity=10, limit_price=Decimal("120.00"), tif=TimeInForce.GTC)
    update = await broker.submit_order(req)
    assert update.status == OrderStatus.SUBMITTED

    cancelled_count = await broker.reset_for_new_session()
    # This GTC order should NOT have been cancelled
    assert cancelled_count == 0

    fetched = await broker.get_order(update.broker_order_id)
    assert fetched.status == OrderStatus.SUBMITTED


# ---------------------------------------------------------------------------
# Test 7: Gap fill on stop order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gap_fill_on_sell_stop(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    stop_px = Decimal("100.00")
    req = _order(
        side=OrderSide.SELL,
        order_type=OrderType.STOP,
        quantity=10,
        stop_price=stop_px,
    )
    update = await broker.submit_order(req)
    assert update.status == OrderStatus.SUBMITTED

    # Bar opens at $95 — gaps BELOW stop price. Fill should be at $95 (or close), not $100.
    bar = _bar(
        open_=Decimal("95.00"),
        high=Decimal("96.00"),
        low=Decimal("94.00"),
        close=Decimal("95.50"),
    )
    fills = await broker.process_pending_orders({"AAPL": bar})

    assert len(fills) == 1
    fill = fills[0]
    assert fill.status == OrderStatus.FILLED
    # Gap fill: should fill at ~$95 (bar open), not $100 (stop price)
    # After slippage deduction for sell, fill_price < open slightly
    assert fill.filled_avg_price < Decimal("100.00"), (
        f"Expected gap fill at ~$95, got {fill.filled_avg_price}"
    )
    assert fill.filled_avg_price > Decimal("90.00"), (
        f"Fill price unrealistically low: {fill.filled_avg_price}"
    )


# ---------------------------------------------------------------------------
# Test 8: Large order gets partial fill
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_large_order_gets_partial_fill(settings, redis_client):
    # ADV = 100,000; 2% threshold = 2,000 shares. Order > 2,000 should partial-fill.
    svc = _make_market_service(adv=100_000)
    broker = _make_broker(settings, svc, redis_client)

    req = _order(side=OrderSide.BUY, quantity=5_000)  # 5% of ADV → partial
    update = await broker.submit_order(req)

    assert update.status == OrderStatus.PARTIAL
    assert update.filled_qty > 0
    assert update.filled_qty < 5_000


# ---------------------------------------------------------------------------
# Test 9: get_open_orders returns only open orders
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_open_orders_returns_submitted(settings, redis_client):
    svc = _make_market_service()
    broker = _make_broker(settings, svc, redis_client)

    # Submit a limit order (will remain SUBMITTED)
    req = _order(order_type=OrderType.LIMIT, quantity=10, limit_price=Decimal("50.00"))
    update = await broker.submit_order(req)
    assert update.status == OrderStatus.SUBMITTED

    open_orders = await broker.get_open_orders()
    broker_ids = [o["broker_order_id"] for o in open_orders]
    assert update.broker_order_id in broker_ids
