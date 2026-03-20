"""FastAPI router for risk management endpoints."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from sentinel.config import Settings, get_settings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/risk", tags=["risk"])


def _get_firewall(request: Request, settings: Settings) -> Any:
    from sentinel.risk.firewall import RiskFirewall

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise HTTPException(status_code=503, detail="Redis unavailable — risk firewall requires Redis")
    return RiskFirewall(settings=settings, redis_client=redis)


class HaltRequest(BaseModel):
    reason: str
    operator: str


class DisengageRequest(BaseModel):
    operator: str


class StrategyHaltRequest(BaseModel):
    strategy: str
    reason: str
    operator: str


class SymbolHaltRequest(BaseModel):
    symbol: str
    reason: str
    operator: str


def _kill_switch_to_dict(state: Any) -> dict:
    return {
        "global_halt": state.global_halt,
        "halted_strategies": list(state.halted_strategies),
        "halted_symbols": list(state.halted_symbols),
        "halt_reason": state.halt_reason,
        "halted_at": state.halted_at.isoformat() if state.halted_at else None,
        "halted_by": state.halted_by,
    }


@router.get("/kill-switch")
async def get_kill_switch(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    state = await fw.get_kill_switch_state()
    return _kill_switch_to_dict(state)


class ValidateRequest(BaseModel):
    symbol: str
    side: str
    quantity: int = 0
    shares: int = 0  # alias for quantity
    entry_price: float = 0.0
    stop_price: float = 0.0
    strategy_id: str = ""
    strategy_name: str = ""  # alias for strategy_id
    signal_confidence: float = 0.8


@router.post("/validate")
async def validate_trade(
    body: ValidateRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    from decimal import Decimal

    from sentinel.domain.types import OrderSide
    from sentinel.market.mock import MockProvider
    from sentinel.market.service import MarketDataService
    from sentinel.risk.firewall import PortfolioState

    fw = _get_firewall(request, settings)

    # Build a mock snapshot for the symbol
    mock_svc = MarketDataService(providers={"mock": MockProvider(seed=42)}, primary="mock")
    try:
        snapshot = await mock_svc.get_snapshot(body.symbol)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Market data error: {exc}")

    portfolio_state = PortfolioState(
        account_value=Decimal("100000"),
        cash=Decimal("100000"),
        positions={},
        realized_pnl_today=Decimal("0"),
        realized_pnl_week=Decimal("0"),
        unrealized_pnl=Decimal("0"),
        gross_exposure=Decimal("0"),
        open_position_count=0,
        recent_trades=[],
    )

    try:
        side = OrderSide(body.side.lower())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    qty = body.quantity or body.shares or 1
    strat = body.strategy_id or body.strategy_name or "unknown"
    # Use snapshot price as entry if not provided
    entry_px = body.entry_price or float(snapshot.latest_bar.close) if snapshot.latest_bar else 0.0
    stop_px = body.stop_price or (entry_px * 0.98)

    assessment = await fw.assess(
        symbol=body.symbol,
        side=side,
        proposed_shares=qty,
        entry_price=Decimal(str(entry_px)),
        stop_price=Decimal(str(stop_px)),
        strategy_name=strat,
        snapshot=snapshot,
        portfolio_state=portfolio_state,
    )

    return {
        "approved": assessment.passed,
        "blocking_checks": assessment.blocking_checks,
        "warning_checks": assessment.warning_checks,
        "checks_run": len(assessment.results),
        "assessed_at": assessment.assessed_at.isoformat(),
    }


@router.post("/halt/engage")
async def engage_halt(
    body: HaltRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    await fw.engage_global_halt(reason=body.reason, operator=body.operator)
    return {"status": "halted", "reason": body.reason, "operator": body.operator}


@router.post("/halt/disengage")
async def disengage_halt(
    body: DisengageRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    await fw.disengage_global_halt(operator=body.operator)
    return {"status": "resumed", "operator": body.operator}


@router.post("/halt/strategy")
async def halt_strategy(
    body: StrategyHaltRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    await fw.halt_strategy(strategy_name=body.strategy, reason=body.reason, operator=body.operator)
    return {"status": "halted", "strategy": body.strategy}


@router.post("/halt/symbol")
async def halt_symbol(
    body: SymbolHaltRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    await fw.halt_symbol(symbol=body.symbol, reason=body.reason, operator=body.operator)
    return {"status": "halted", "symbol": body.symbol}


@router.post("/halt/symbol/resume")
async def resume_symbol(
    body: SymbolHaltRequest,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    fw = _get_firewall(request, settings)
    await fw.resume_symbol(symbol=body.symbol, operator=body.operator)
    return {"status": "resumed", "symbol": body.symbol}
