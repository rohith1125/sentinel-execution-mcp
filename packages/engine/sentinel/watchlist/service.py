"""WatchlistService — full CRUD, group tagging, and symbol validation."""

from __future__ import annotations

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from sentinel.db.models import WatchlistEntry
from sentinel.domain.types import AssetClass

logger = structlog.get_logger(__name__)


class WatchlistService:
    def __init__(
        self,
        session: AsyncSession,
        market_provider: object | None = None,
    ) -> None:
        self._session = session
        self._market_provider = market_provider

    async def add_symbols(
        self,
        symbols: list[str],
        group: str | None = None,
        notes: str | None = None,
        asset_class: AssetClass = AssetClass.EQUITY,
    ) -> list[WatchlistEntry]:
        symbols_upper = [s.upper() for s in symbols]

        # fetch existing symbols in one query to avoid duplicates
        existing_result = await self._session.execute(
            select(WatchlistEntry).where(WatchlistEntry.symbol.in_(symbols_upper))
        )
        existing_map = {e.symbol: e for e in existing_result.scalars().all()}

        added: list[WatchlistEntry] = []
        for ticker in symbols_upper:
            if ticker in existing_map:
                entry = existing_map[ticker]
                if not entry.is_active:
                    entry.is_active = True
                if group and (entry.group_tags is None or group not in entry.group_tags):
                    entry.group_tags = list(entry.group_tags or []) + [group]
                added.append(entry)
            else:
                entry = WatchlistEntry(
                    symbol=ticker,
                    asset_class=asset_class.value,
                    group_tags=[group] if group else [],
                    notes=notes,
                    is_active=True,
                )
                self._session.add(entry)
                added.append(entry)

        await self._session.flush()
        logger.info("watchlist.add_symbols", count=len(added), group=group)
        return added

    async def remove_symbols(self, symbols: list[str]) -> int:
        symbols_upper = [s.upper() for s in symbols]
        result = await self._session.execute(
            update(WatchlistEntry)
            .where(WatchlistEntry.symbol.in_(symbols_upper))
            .where(WatchlistEntry.is_active.is_(True))
            .values(is_active=False)
            .returning(WatchlistEntry.id)
        )
        count = len(result.fetchall())
        logger.info("watchlist.remove_symbols", count=count)
        return count

    async def list_symbols(
        self,
        group: str | None = None,
        active_only: bool = True,
    ) -> list[WatchlistEntry]:
        stmt = select(WatchlistEntry)
        if active_only:
            stmt = stmt.where(WatchlistEntry.is_active.is_(True))
        if group is not None:
            stmt = stmt.where(WatchlistEntry.group_tags.contains([group]))
        stmt = stmt.order_by(WatchlistEntry.symbol)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_groups(self) -> list[str]:
        result = await self._session.execute(
            select(WatchlistEntry.group_tags).where(WatchlistEntry.is_active.is_(True))
        )
        groups: set[str] = set()
        for row in result.scalars().all():
            if row:
                groups.update(row)
        return sorted(groups)

    async def tag_symbols(self, symbols: list[str], group: str) -> int:
        symbols_upper = [s.upper() for s in symbols]
        result = await self._session.execute(
            select(WatchlistEntry).where(
                WatchlistEntry.symbol.in_(symbols_upper),
                WatchlistEntry.is_active.is_(True),
            )
        )
        entries = result.scalars().all()
        count = 0
        for entry in entries:
            tags = list(entry.group_tags or [])
            if group not in tags:
                tags.append(group)
                entry.group_tags = tags
                count += 1
        await self._session.flush()
        logger.info("watchlist.tag_symbols", count=count, group=group)
        return count

    async def validate_symbols(self, symbols: list[str]) -> dict[str, bool]:
        if self._market_provider is None:
            return {s.upper(): True for s in symbols}

        results: dict[str, bool] = {}
        for symbol in symbols:
            try:
                valid = await self._market_provider.validate_symbol(symbol)  # type: ignore[union-attr]
                results[symbol.upper()] = valid
            except Exception:
                results[symbol.upper()] = False
        return results

    async def export_watchlist(self, group: str | None = None) -> dict[str, object]:
        entries = await self.list_symbols(group=group, active_only=True)
        return {
            "version": "1.0",
            "group_filter": group,
            "count": len(entries),
            "symbols": [
                {
                    "symbol": e.symbol,
                    "asset_class": e.asset_class,
                    "group_tags": e.group_tags or [],
                    "notes": e.notes,
                    "added_at": e.added_at.isoformat() if e.added_at else None,
                }
                for e in entries
            ],
        }

    async def import_watchlist(self, data: dict[str, object]) -> int:
        symbols_data: list[dict[str, object]] = data.get("symbols", [])  # type: ignore[assignment]
        imported = 0
        for item in symbols_data:
            ticker = str(item["symbol"]).upper()
            group_tags: list[str] = item.get("group_tags", [])  # type: ignore[assignment]
            notes: str | None = item.get("notes")  # type: ignore[assignment]
            asset_class_str: str = item.get("asset_class", "equity")  # type: ignore[assignment]

            try:
                asset_class = AssetClass(asset_class_str)
            except ValueError:
                asset_class = AssetClass.EQUITY

            existing = await self._session.execute(
                select(WatchlistEntry).where(WatchlistEntry.symbol == ticker)
            )
            entry = existing.scalar_one_or_none()
            if entry is None:
                entry = WatchlistEntry(
                    symbol=ticker,
                    asset_class=asset_class.value,
                    group_tags=group_tags,
                    notes=notes,
                    is_active=True,
                )
                self._session.add(entry)
                imported += 1
            else:
                entry.is_active = True
                entry.group_tags = list(set((entry.group_tags or []) + group_tags))
                if notes:
                    entry.notes = notes

        await self._session.flush()
        logger.info("watchlist.import", imported=imported)
        return imported
