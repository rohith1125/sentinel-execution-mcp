"""FastAPI router for portfolio/account endpoints (backed by paper broker)."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from sentinel.config import Settings, get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _get_paper_broker(request: Request, settings: Settings) -> Any:
    from sentinel.execution.paper import PaperBroker
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable — paper broker requires Redis")
    return PaperBroker(settings=settings, redis_client=redis)


@router.get("/status")
async def portfolio_status(
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


@router.get("/positions")
async def get_positions(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict]:
    broker = _get_paper_broker(request, settings)
    try:
        return await broker.get_positions()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/account")
async def get_account(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    broker = _get_paper_broker(request, settings)
    try:
        account = await broker.get_account()
        cash = Decimal(str(account.get("cash", "0")))
        equity = Decimal(str(account.get("equity", str(cash))))
        return {
            "value": float(equity),
            "cash": float(cash),
            "equity": float(equity),
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
