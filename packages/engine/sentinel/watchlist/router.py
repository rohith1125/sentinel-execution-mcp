"""FastAPI router for watchlist endpoints."""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.config import Settings, get_settings
from sentinel.db.base import db_session_placeholder
from sentinel.domain.types import AssetClass
from sentinel.watchlist.service import WatchlistService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class AddSymbolsRequest(BaseModel):
    symbols: list[str]
    group: str | None = None
    notes: str | None = None
    asset_class: AssetClass = AssetClass.EQUITY

    @field_validator("symbols")
    @classmethod
    def validate_symbols_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("symbols list must not be empty")
        return v


class RemoveSymbolsRequest(BaseModel):
    symbols: list[str]


class TagSymbolsRequest(BaseModel):
    symbols: list[str]
    group: str


class ValidateSymbolsRequest(BaseModel):
    symbols: list[str]


class ImportRequest(BaseModel):
    data: dict[str, object]


class WatchlistEntryOut(BaseModel):
    id: int
    symbol: str
    asset_class: str
    group_tags: list[str]
    notes: str | None
    is_active: bool

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _get_service(
    session: Annotated[AsyncSession, Depends(db_session_placeholder)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WatchlistService:
    return WatchlistService(session=session)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/symbols", response_model=list[WatchlistEntryOut], status_code=status.HTTP_201_CREATED)
async def add_symbols(
    body: AddSymbolsRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: AsyncSession = Depends(db_session_placeholder),
) -> list[WatchlistEntryOut]:
    svc = WatchlistService(session=session)
    entries = await svc.add_symbols(
        symbols=body.symbols,
        group=body.group,
        notes=body.notes,
        asset_class=body.asset_class,
    )
    return [WatchlistEntryOut.model_validate(e) for e in entries]


@router.delete("/symbols", status_code=status.HTTP_200_OK)
async def remove_symbols(
    body: RemoveSymbolsRequest,
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict[str, int]:
    svc = WatchlistService(session=session)
    count = await svc.remove_symbols(symbols=body.symbols)
    return {"removed": count}


@router.get("/symbols", response_model=list[WatchlistEntryOut])
async def list_symbols(
    group: str | None = Query(None),
    active_only: bool = Query(True),
    session: AsyncSession = Depends(db_session_placeholder),
) -> list[WatchlistEntryOut]:
    svc = WatchlistService(session=session)
    entries = await svc.list_symbols(group=group, active_only=active_only)
    return [WatchlistEntryOut.model_validate(e) for e in entries]


@router.get("/groups", response_model=list[str])
async def get_groups(
    session: AsyncSession = Depends(db_session_placeholder),
) -> list[str]:
    svc = WatchlistService(session=session)
    return await svc.get_groups()


@router.post("/symbols/tag", status_code=status.HTTP_200_OK)
async def tag_symbols(
    body: TagSymbolsRequest,
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict[str, int]:
    svc = WatchlistService(session=session)
    count = await svc.tag_symbols(symbols=body.symbols, group=body.group)
    return {"tagged": count}


@router.post("/symbols/validate")
async def validate_symbols(
    body: ValidateSymbolsRequest,
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict[str, bool]:
    svc = WatchlistService(session=session)
    return await svc.validate_symbols(symbols=body.symbols)


@router.get("/export")
async def export_watchlist(
    group: str | None = Query(None),
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict[str, object]:
    svc = WatchlistService(session=session)
    return await svc.export_watchlist(group=group)


@router.post("/import", status_code=status.HTTP_200_OK)
async def import_watchlist(
    body: ImportRequest,
    session: AsyncSession = Depends(db_session_placeholder),
) -> dict[str, int]:
    svc = WatchlistService(session=session)
    count = await svc.import_watchlist(data=body.data)
    return {"imported": count}
