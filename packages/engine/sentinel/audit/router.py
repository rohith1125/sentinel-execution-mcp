"""FastAPI router for audit endpoints."""

from __future__ import annotations

from datetime import UTC

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.audit.journal import AuditJournal
from sentinel.audit.reports import ReportGenerator
from sentinel.db.base import db_session_placeholder

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/audit", tags=["audit"])


def _event_to_dict(e: object) -> dict:
    from sentinel.db.models import AuditEvent as AE

    ev: AE = e  # type: ignore[assignment]
    return {
        "event_id": ev.event_id,
        "event_type": ev.event_type,
        "symbol": ev.symbol,
        "strategy_id": ev.strategy_id,
        "outcome": ev.outcome,
        "explanation": ev.explanation,
        "created_at": ev.created_at.isoformat() if ev.created_at else None,
    }


@router.get("/events")
async def get_recent_events(
    limit: int = Query(50),
    symbol: str | None = Query(None),
    strategy: str | None = Query(None),
    session: AsyncSession = Depends(db_session_placeholder),
) -> list[dict]:
    journal = AuditJournal(db=session)
    events = await journal.get_recent_events(limit=limit, symbol=symbol, strategy=strategy)
    return [_event_to_dict(e) for e in events]


@router.get("/explain/{audit_event_id}")
async def explain_trade(
    audit_event_id: str,
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict:
    journal = AuditJournal(db=session)
    return await journal.explain_trade(audit_event_id)


@router.get("/summary/daily")
async def daily_summary(
    date: str | None = Query(None),
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict:
    rg = ReportGenerator(db=session)
    report_date = None
    if date:
        from datetime import date as _date

        try:
            report_date = _date.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid date format: {date}")
    return await rg.daily_summary(report_date=report_date)


@router.get("/summary/weekly")
async def weekly_summary(
    week_ending: str | None = Query(None),
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict:
    rg = ReportGenerator(db=session)
    we_date = None
    if week_ending:
        from datetime import date as _date

        try:
            we_date = _date.fromisoformat(week_ending)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid week_ending format: {week_ending}")
    return await rg.weekly_summary(week_ending=we_date)


@router.get("/scorecard/{strategy}")
async def strategy_scorecard(
    strategy: str,
    days: int = Query(30),
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict:
    rg = ReportGenerator(db=session)
    return await rg.strategy_scorecard(strategy_name=strategy, days=days)


@router.get("/blotter")
async def trade_blotter(
    start: str = Query(...),
    end: str = Query(...),
    strategy: str | None = Query(None),
    session: AsyncSession = Depends(db_session_placeholder),
) -> list[dict]:
    from datetime import datetime

    try:
        start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
        end_dt = datetime.fromisoformat(end).replace(tzinfo=UTC)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    rg = ReportGenerator(db=session)
    return await rg.trade_blotter(start=start_dt, end=end_dt, strategy=strategy)
