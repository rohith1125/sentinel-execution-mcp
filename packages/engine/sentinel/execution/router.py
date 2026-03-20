"""FastAPI router for execution endpoints."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from sentinel.config import Settings, get_settings
from sentinel.domain.types import OrderSide, OrderType, TimeInForce

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/execution", tags=["execution"])


def _get_paper_broker(request: Request, settings: Settings) -> Any:
    from sentinel.execution.paper import PaperBroker
    from sentinel.market.mock import MockProvider
    from sentinel.market.service import MarketDataService
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable — paper broker requires Redis")
    market_service = MarketDataService(providers={"mock": MockProvider(seed=42)}, primary="mock", redis_client=redis)
    return PaperBroker(settings=settings, market_service=market_service, redis=redis)


class PaperOrderRequest(BaseModel):
    symbol: str
    side: str  # "buy" | "sell"
    quantity: int
    order_type: str = "market"

    from pydantic import field_validator

    @field_validator("quantity")
    @classmethod
    def quantity_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"quantity must be positive, got {v}")
        return v
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "day"
    strategy_id: str | None = None


class CancelRequest(BaseModel):
    reason: str


class FlattenRequest(BaseModel):
    reason: str
    operator: str


class ResetPaperRequest(BaseModel):
    starting_cash: float = 100_000.0


def _update_to_dict(u: Any) -> dict:
    return {
        "order_id": u.broker_order_id,
        "broker_order_id": u.broker_order_id,
        "client_order_id": u.client_order_id,
        "status": u.status.value,
        "filled_qty": u.filled_qty,
        "filled_avg_price": float(u.filled_avg_price) if u.filled_avg_price else None,
        "rejection_reason": u.rejection_reason,
        "timestamp": u.timestamp.isoformat(),
    }


@router.post("/paper/order")
async def submit_paper_order(
    body: PaperOrderRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    from sentinel.execution.broker import OrderRequest
    from sentinel.risk.firewall import RiskFirewall
    broker = _get_paper_broker(request, settings)

    # Check kill switch before submitting
    redis = getattr(request.app.state, "redis", None)
    fw = RiskFirewall(settings=settings, redis_client=redis)
    ks = await fw.get_kill_switch_state()
    if ks.global_halt:
        raise HTTPException(status_code=403, detail="Global halt is active — all order submission blocked")
    if body.symbol in ks.halted_symbols:
        raise HTTPException(status_code=403, detail=f"Symbol {body.symbol} is halted")

    try:
        side = OrderSide(body.side.lower())
        otype = OrderType(body.order_type.lower())
        tif = TimeInForce(body.time_in_force.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    req = OrderRequest(
        client_order_id=f"paper-{uuid.uuid4().hex[:12]}",
        symbol=body.symbol,
        side=side,
        order_type=otype,
        quantity=body.quantity,
        limit_price=Decimal(str(body.limit_price)) if body.limit_price else None,
        stop_price=Decimal(str(body.stop_price)) if body.stop_price else None,
        time_in_force=tif,
        strategy_id=body.strategy_id,
    )
    try:
        update = await broker.submit_order(req)
        return _update_to_dict(update)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/order/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    body: CancelRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        update = await broker.cancel_order(order_id)
        return _update_to_dict(update)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/order/{order_id}")
async def get_order(
    order_id: str,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        update = await broker.get_order(order_id)
        return _update_to_dict(update)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/orders/open")
async def get_open_orders(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict]:
    broker = _get_paper_broker(request, settings)
    try:
        orders = await broker.get_open_orders()
        return orders
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/portfolio")
async def get_portfolio(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        account = await broker.get_account()
        positions = await broker.get_positions()
        return {"account": account, "positions": positions}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/flatten-all")
async def flatten_all(
    body: FlattenRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        positions = await broker.get_positions()
        updates = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            shares_raw = pos.get("qty", pos.get("shares", "0"))
            shares = int(Decimal(str(shares_raw)))
            if shares <= 0:
                continue
            side_str = pos.get("side", "long")
            sell_side = OrderSide.SELL if side_str in ("long", "buy") else OrderSide.BUY
            from sentinel.execution.broker import OrderRequest
            req = OrderRequest(
                client_order_id=f"FLATTEN-{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                side=sell_side,
                order_type=OrderType.MARKET,
                quantity=shares,
                time_in_force=TimeInForce.DAY,
            )
            update = await broker.submit_order(req)
            updates.append(_update_to_dict(update))
        logger.warning("flatten_all", reason=body.reason, operator=body.operator, count=len(updates))
        return {"flattened": len(updates), "orders": updates}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/paper/reset")
async def reset_paper(
    body: ResetPaperRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        await broker.reset_paper_account(starting_cash=Decimal(str(body.starting_cash)))
        return {"status": "reset", "starting_cash": body.starting_cash}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


# Portfolio-level routes (aliased from /portfolio prefix used by engine-client)

@router.get("/portfolio/status")
async def portfolio_status(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    return await get_portfolio(request, settings)
