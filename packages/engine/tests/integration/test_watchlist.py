"""
Integration tests for WatchlistService.

All tests use a real database session (test DB) created fresh per test
via the db_session fixture in conftest.py.
"""
from __future__ import annotations

import pytest

from sentinel.domain.types import AssetClass
from sentinel.market.mock import MockProvider
from sentinel.watchlist.service import WatchlistService


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.mark.asyncio
class TestWatchlistAdd:
    async def test_add_single_symbol(self, db_session):
        svc = WatchlistService(db_session)
        entries = await svc.add_symbols(["AAPL"])
        await db_session.commit()
        assert len(entries) == 1
        assert entries[0].symbol == "AAPL"
        assert entries[0].is_active

    async def test_add_normalizes_to_uppercase(self, db_session):
        svc = WatchlistService(db_session)
        entries = await svc.add_symbols(["aapl", "msft"])
        await db_session.commit()
        symbols = [e.symbol for e in entries]
        assert "AAPL" in symbols
        assert "MSFT" in symbols

    async def test_add_multiple_symbols(self, db_session):
        svc = WatchlistService(db_session)
        entries = await svc.add_symbols(["AAPL", "MSFT", "NVDA"])
        await db_session.commit()
        assert len(entries) == 3

    async def test_add_with_group(self, db_session):
        svc = WatchlistService(db_session)
        entries = await svc.add_symbols(["AAPL"], group="tech_large_cap")
        await db_session.commit()
        assert "tech_large_cap" in (entries[0].group_tags or [])

    async def test_add_with_notes(self, db_session):
        svc = WatchlistService(db_session)
        entries = await svc.add_symbols(["AAPL"], notes="Earnings play")
        await db_session.commit()
        assert entries[0].notes == "Earnings play"

    async def test_add_duplicate_reactivates_existing(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"])
        await svc.remove_symbols(["AAPL"])
        await db_session.commit()

        # Verify it's inactive
        entries = await svc.list_symbols(active_only=True)
        assert not any(e.symbol == "AAPL" for e in entries)

        # Re-add should reactivate
        await svc.add_symbols(["AAPL"])
        await db_session.commit()
        entries = await svc.list_symbols(active_only=True)
        assert any(e.symbol == "AAPL" for e in entries)

    async def test_add_duplicate_adds_new_group_tag(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"], group="group_a")
        await svc.add_symbols(["AAPL"], group="group_b")
        await db_session.commit()
        entries = await svc.list_symbols()
        aapl = next(e for e in entries if e.symbol == "AAPL")
        tags = aapl.group_tags or []
        assert "group_a" in tags
        assert "group_b" in tags


@pytest.mark.asyncio
class TestWatchlistRemove:
    async def test_remove_marks_inactive(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT"])
        await db_session.commit()

        count = await svc.remove_symbols(["AAPL"])
        await db_session.commit()
        assert count == 1

        active = await svc.list_symbols(active_only=True)
        assert not any(e.symbol == "AAPL" for e in active)
        assert any(e.symbol == "MSFT" for e in active)

    async def test_remove_nonexistent_returns_zero(self, db_session):
        svc = WatchlistService(db_session)
        count = await svc.remove_symbols(["NONEXISTENT"])
        await db_session.commit()
        assert count == 0

    async def test_remove_already_inactive_returns_zero(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"])
        await svc.remove_symbols(["AAPL"])
        await db_session.commit()

        count = await svc.remove_symbols(["AAPL"])  # already inactive
        await db_session.commit()
        assert count == 0


@pytest.mark.asyncio
class TestWatchlistList:
    async def test_list_returns_all_active(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT", "NVDA"])
        await db_session.commit()

        entries = await svc.list_symbols(active_only=True)
        symbols = {e.symbol for e in entries}
        assert {"AAPL", "MSFT", "NVDA"}.issubset(symbols)

    async def test_list_excludes_inactive_by_default(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT"])
        await svc.remove_symbols(["AAPL"])
        await db_session.commit()

        entries = await svc.list_symbols()
        assert not any(e.symbol == "AAPL" for e in entries)

    async def test_list_includes_inactive_when_requested(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"])
        await svc.remove_symbols(["AAPL"])
        await db_session.commit()

        entries = await svc.list_symbols(active_only=False)
        assert any(e.symbol == "AAPL" for e in entries)

    async def test_list_filtered_by_group(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT"], group="tech")
        await svc.add_symbols(["JPM", "GS"], group="finance")
        await db_session.commit()

        tech = await svc.list_symbols(group="tech")
        symbols = {e.symbol for e in tech}
        assert "AAPL" in symbols
        assert "JPM" not in symbols

    async def test_list_sorted_alphabetically(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["NVDA", "AAPL", "MSFT"])
        await db_session.commit()

        entries = await svc.list_symbols()
        symbols = [e.symbol for e in entries]
        assert symbols == sorted(symbols)


@pytest.mark.asyncio
class TestWatchlistGroups:
    async def test_get_groups_returns_distinct_tags(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"], group="tech")
        await svc.add_symbols(["JPM"], group="finance")
        await svc.add_symbols(["MSFT"], group="tech")  # duplicate group
        await db_session.commit()

        groups = await svc.get_groups()
        assert "tech" in groups
        assert "finance" in groups
        # Should be deduplicated
        assert groups.count("tech") == 1

    async def test_get_groups_sorted(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"], group="z_group")
        await svc.add_symbols(["MSFT"], group="a_group")
        await db_session.commit()

        groups = await svc.get_groups()
        assert groups == sorted(groups)

    async def test_get_groups_empty_when_no_entries(self, db_session):
        svc = WatchlistService(db_session)
        groups = await svc.get_groups()
        assert groups == []


@pytest.mark.asyncio
class TestWatchlistTag:
    async def test_tag_adds_group(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"])
        await db_session.commit()

        count = await svc.tag_symbols(["AAPL"], "earnings_play")
        await db_session.commit()
        assert count == 1

        entries = await svc.list_symbols()
        aapl = next(e for e in entries if e.symbol == "AAPL")
        assert "earnings_play" in (aapl.group_tags or [])

    async def test_tag_is_idempotent(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"], group="existing_tag")
        await db_session.commit()

        count = await svc.tag_symbols(["AAPL"], "existing_tag")
        await db_session.commit()
        # Re-tagging the same group should not duplicate it
        entries = await svc.list_symbols()
        aapl = next(e for e in entries if e.symbol == "AAPL")
        tags = aapl.group_tags or []
        assert tags.count("existing_tag") == 1

    async def test_tag_does_not_remove_existing_tags(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"], group="original")
        await db_session.commit()

        await svc.tag_symbols(["AAPL"], "new_tag")
        await db_session.commit()

        entries = await svc.list_symbols()
        aapl = next(e for e in entries if e.symbol == "AAPL")
        tags = aapl.group_tags or []
        assert "original" in tags
        assert "new_tag" in tags


@pytest.mark.asyncio
class TestWatchlistExportImport:
    async def test_export_returns_valid_structure(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT"], group="tech")
        await db_session.commit()

        exported = await svc.export_watchlist()
        assert "version" in exported
        assert "symbols" in exported
        assert exported["count"] == 2

    async def test_import_adds_symbols(self, db_session):
        svc = WatchlistService(db_session)
        payload = {
            "version": "1.0",
            "symbols": [
                {"symbol": "AAPL", "asset_class": "equity", "group_tags": ["tech"], "notes": None},
                {"symbol": "JPM", "asset_class": "equity", "group_tags": ["finance"], "notes": "Bank"},
            ],
        }
        count = await svc.import_watchlist(payload)
        await db_session.commit()
        assert count == 2

        entries = await svc.list_symbols()
        symbols = {e.symbol for e in entries}
        assert {"AAPL", "JPM"}.issubset(symbols)

    async def test_import_skips_duplicates(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL"])
        await db_session.commit()

        payload = {
            "symbols": [{"symbol": "AAPL", "asset_class": "equity", "group_tags": [], "notes": None}]
        }
        count = await svc.import_watchlist(payload)
        await db_session.commit()
        assert count == 0  # already exists, not counted as new import

    async def test_export_group_filter(self, db_session):
        svc = WatchlistService(db_session)
        await svc.add_symbols(["AAPL", "MSFT"], group="tech")
        await svc.add_symbols(["JPM"], group="finance")
        await db_session.commit()

        exported = await svc.export_watchlist(group="tech")
        assert exported["count"] == 2
        exported_symbols = {s["symbol"] for s in exported["symbols"]}
        assert "JPM" not in exported_symbols
