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
