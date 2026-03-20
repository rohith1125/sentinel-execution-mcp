"""FastAPI router for strategy scanning endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from sentinel.config import Settings, get_settings
from sentinel.market.mock import MockProvider as MockMarketDataProvider
from sentinel.market.service import MarketDataService
from sentinel.strategy.registry import registry as global_registry
from sentinel.strategy.scanner import WatchlistScanner

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/strategy", tags=["strategy"])


def _build_scanner(settings: Settings) -> WatchlistScanner:
    provider = MockMarketDataProvider(seed=42)
    svc = MarketDataService(providers={"mock": provider}, primary="mock")
    return WatchlistScanner(market_service=svc)


class ScanWatchlistRequest(BaseModel):
    symbols: list[str] = []
    group: str | None = None
    strategies: list[str] | None = None


class ScanSymbolRequest(BaseModel):
    strategy_name: str | None = None
    strategies: list[str] | None = None


def _result_to_dict(r: object) -> dict:
    from sentinel.strategy.base import StrategyResult as SR

    res: SR = r  # type: ignore[assignment]
    return {
        "symbol": res.symbol,
        "strategy": res.strategy_name,
        "signal": res.signal.value if res.signal else None,
        "confidence": res.confidence,
        "entry_price": float(res.entry_price) if res.entry_price else None,
        "stop_price": float(res.stop_price) if res.stop_price else None,
        "target_price": float(res.target_price) if res.target_price else None,
        "reasoning": res.reasoning,
        "regime_label": res.regime_label.value if res.regime_label else None,
    }


@router.post("/scan/watchlist")
async def scan_watchlist(
    body: ScanWatchlistRequest,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> list[dict]:
    scanner = _build_scanner(settings or get_settings())
    try:
        results = await scanner.scan(
            symbols=body.symbols,
            strategy_names=body.strategies,
        )
        return [_result_to_dict(r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/scan/{symbol}")
async def scan_symbol(
    symbol: str,
    body: ScanSymbolRequest,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> list[dict]:
    scanner = _build_scanner(settings or get_settings())
    try:
        strategy_names = body.strategies or ([body.strategy_name] if body.strategy_name else None)
        results = await scanner.scan(
            symbols=[symbol],
            strategy_names=strategy_names,
        )
        return [_result_to_dict(r) for r in results]
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/list")
async def list_strategies() -> list[str]:
    return list(global_registry.list_strategies())
