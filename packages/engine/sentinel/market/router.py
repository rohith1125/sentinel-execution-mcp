"""FastAPI router for market data endpoints."""

from __future__ import annotations

import time
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.config import Settings, get_settings
from sentinel.market.mock import MockMarketDataProvider
from sentinel.market.service import MarketDataService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/market", tags=["market"])


def _get_market_service(
    settings: Annotated[Settings, Depends(get_settings)],
    request: object = None,
) -> MarketDataService:
    """Build a MarketDataService backed by the mock provider (stateless per-request)."""
    from fastapi import Request as _Request

    redis = None
    # Try to pull redis from app.state if we have a Request
    # We use a workaround: import Request inline
    provider = MockMarketDataProvider(seed=42)
    return MarketDataService(
        providers={"mock": provider},
        primary="mock",
        redis_client=redis,
    )


class SnapshotsRequest(BaseModel):
    symbols: list[str]


@router.get("/snapshot/{symbol}")
async def get_snapshot(
    symbol: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    svc = _get_market_service(settings)
    try:
        snap = await svc.get_snapshot(symbol)
        return {
            "symbol": symbol,
            "price": float(snap.quote.ask_price),
            "bid": float(snap.quote.bid_price),
            "ask": float(snap.quote.ask_price),
            "volume": snap.quote.ask_size + snap.quote.bid_size,
            "timestamp": snap.quote.timestamp.isoformat() if hasattr(snap.quote, "timestamp") else None,
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/snapshots")
async def get_snapshots(
    body: SnapshotsRequest,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    svc = _get_market_service(settings)
    result = {}
    for symbol in body.symbols:
        try:
            snap = await svc.get_snapshot(symbol)
            result[symbol] = {
                "symbol": symbol,
                "bid": float(snap.quote.bid_price),
                "ask": float(snap.quote.ask_price),
            }
        except Exception:
            result[symbol] = {"symbol": symbol, "error": "unavailable"}
    return result


@router.get("/bars/{symbol}")
async def get_bars(
    symbol: str,
    timeframe: str = Query("5Min"),
    limit: int = Query(100),
    settings: Annotated[Settings, Depends(get_settings)],
) -> list[dict]:
    svc = _get_market_service(settings)
    try:
        bars = await svc.get_bars(symbol, timeframe=timeframe, limit=limit)
        return [
            {
                "timestamp": b.timestamp.isoformat(),
                "open": float(b.open),
                "high": float(b.high),
                "low": float(b.low),
                "close": float(b.close),
                "volume": float(b.volume),
            }
            for b in bars
        ]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/health")
async def market_health(
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    t0 = time.perf_counter()
    svc = _get_market_service(settings)
    try:
        await svc.get_snapshot("SPY")
        latency_ms = (time.perf_counter() - t0) * 1000
        return {"status": "ok", "provider": settings.market_data_provider, "latency_ms": round(latency_ms, 2)}
    except Exception as exc:
        return {"status": "error", "provider": settings.market_data_provider, "error": str(exc), "latency_ms": 0}
