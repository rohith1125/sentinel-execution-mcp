"""FastAPI router for regime classification endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from sentinel.config import Settings, get_settings
from sentinel.market.mock import MockProvider as MockMarketDataProvider
from sentinel.market.service import MarketDataService
from sentinel.regime.classifier import RegimeClassifier

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/regime", tags=["regime"])

_classifier = RegimeClassifier()


def _get_market_service(settings: Settings) -> MarketDataService:
    provider = MockMarketDataProvider(seed=42)
    return MarketDataService(providers={"mock": provider}, primary="mock")


class BulkEvaluateRequest(BaseModel):
    symbols: list[str]
    timeframe: str = "5Min"


def _snapshot_to_dict(snap: object) -> dict:
    from sentinel.regime.models import RegimeSnapshot as RS
    s: RS = snap  # type: ignore[assignment]
    return {
        "label": s.label.value,
        "confidence": s.confidence,
        "tradeability_score": s.tradeability_score,
        "supporting_metrics": s.supporting_metrics,
        "strategy_compatibility": {
            k: v for k, v in s.strategy_compatibility.__dict__.items()
        } if s.strategy_compatibility else {},
        "classified_at": s.classified_at.isoformat(),
        "bars_analyzed": s.bars_analyzed,
        "reasoning": s.reasoning,
    }


@router.get("/evaluate/{symbol}")
async def evaluate_regime(
    symbol: str,
    timeframe: str = Query("5Min"),
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> dict:
    svc = _get_market_service(settings or get_settings())
    try:
        _end = datetime.utcnow()
        _start = _end - timedelta(minutes=200 * 5)
        bars = await svc.get_bars(symbol, timeframe=timeframe, start=_start, end=_end, limit=200)
        snap = _classifier.classify(bars, symbol)
        return _snapshot_to_dict(snap)
    except Exception as exc:
        msg = str(exc)
        code = 404 if "not in mock universe" in msg or "not found" in msg.lower() else 502
        raise HTTPException(status_code=code, detail=msg)


@router.post("/evaluate/bulk")
async def evaluate_regime_bulk(
    body: BulkEvaluateRequest,
    settings: Annotated[Settings, Depends(get_settings)] = None,
) -> dict:
    svc = _get_market_service(settings or get_settings())
    results = {}
    for symbol in body.symbols:
        try:
            _end = datetime.utcnow()
            _start = _end - timedelta(minutes=200 * 5)
            bars = await svc.get_bars(symbol, timeframe=body.timeframe, start=_start, end=_end, limit=200)
            snap = _classifier.classify(bars, symbol)
            results[symbol] = _snapshot_to_dict(snap)
        except Exception as exc:
            results[symbol] = {"error": str(exc)}
    return results
