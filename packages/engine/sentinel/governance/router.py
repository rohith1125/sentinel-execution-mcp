"""FastAPI router for governance endpoints."""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.config import Settings, get_settings
from sentinel.db.base import get_session
from sentinel.governance.service import GovernanceError, GovernanceService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/governance", tags=["governance"])


def _get_service(session: AsyncSession = Depends(lambda: None)) -> GovernanceService:
    return GovernanceService(db=session)


def _record_to_dict(r: Any) -> dict:
    return {
        "name": r.name,
        "description": r.description,
        "state": r.state,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


class PromoteRequest(BaseModel):
    target_state: str
    approved_by: str
    notes: str = ""


class SuspendRequest(BaseModel):
    reason: str
    operator: str


@router.post("/evaluate-promotion")
async def evaluate_promotion(
    body: dict,
    session: AsyncSession = Depends(lambda: None),
) -> dict:
    from sentinel.domain.types import StrategyState
    strategy = body.get("strategy", "")
    target_raw = body.get("target_state", "")
    try:
        target = StrategyState(target_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid target_state: {target_raw}")
    svc = GovernanceService(db=session)
    eligible, evaluation = await svc.evaluate_promotion(strategy, target)
    return {"eligible": eligible, **evaluation}


@router.post("/promote")
async def promote_strategy(
    body: dict,
    session: AsyncSession = Depends(lambda: None),
) -> dict:
    from sentinel.domain.types import StrategyState
    strategy = body.get("strategy", "")
    target_raw = body.get("target_state", "")
    approved_by = body.get("approved_by", "")
    notes = body.get("notes", "")
    try:
        target = StrategyState(target_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid target_state: {target_raw}")
    svc = GovernanceService(db=session)
    try:
        record = await svc.promote_strategy(strategy, target, approved_by, notes)
        return _record_to_dict(record)
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/suspend")
async def suspend_strategy(
    body: dict,
    session: AsyncSession = Depends(lambda: None),
) -> dict:
    strategy = body.get("strategy", "")
    reason = body.get("reason", "")
    operator = body.get("operator", "system")
    svc = GovernanceService(db=session)
    try:
        record = await svc.suspend_strategy(strategy, reason, operator)
        return _record_to_dict(record)
    except GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/strategies")
async def list_strategy_states(
    session: AsyncSession = Depends(lambda: None),
) -> list[dict]:
    svc = GovernanceService(db=session)
    records = await svc.list_strategies()
    return [_record_to_dict(r) for r in records]


@router.get("/drift/{strategy}")
async def check_drift(
    strategy: str,
    session: AsyncSession = Depends(lambda: None),
) -> dict:
    svc = GovernanceService(db=session)
    return await svc.check_strategy_drift(strategy)
