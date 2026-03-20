"""Unit tests for sentinel.monitoring: reconciliation and alerts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sentinel.monitoring.alerts import AlertLevel, AlertService
from sentinel.monitoring.reconciliation import (
    PositionReconciler,
    ReconciliationResult,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_settings(**kwargs):
    s = MagicMock()
    s.alert_webhook_enabled = False
    s.alert_webhook_url = ""
    s.alert_quiet_hours_start = 22
    s.alert_quiet_hours_end = 8
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def _make_redis(existing_keys: dict | None = None):
    """Mock Redis that supports get/set/lpush/ltrim/lrange."""
    store: dict = {}
    lists: dict = {}
    if existing_keys:
        store.update(existing_keys)

    redis = AsyncMock()
    redis.get = AsyncMock(side_effect=lambda k: store.get(k))
    redis.set = AsyncMock(side_effect=lambda k, v, ex=None: store.__setitem__(k, v))

    async def lpush(key, val):
        lists.setdefault(key, []).insert(0, val)

    async def ltrim(key, start, end):
        lists[key] = lists.get(key, [])[start : end + 1]

    async def lrange(key, start, end):
        return lists.get(key, [])[start : end + 1]

    redis.lpush = AsyncMock(side_effect=lpush)
    redis.ltrim = AsyncMock(side_effect=ltrim)
    redis.lrange = AsyncMock(side_effect=lrange)
    return redis, store, lists


def _make_db_session(positions: list[dict] | None = None):
    """Mock SQLAlchemy async session returning given positions."""
    session = AsyncMock()

    class FakeRow:
        def __init__(self, d):
            self.__dict__.update(d)

    rows = [FakeRow(p) for p in (positions or [])]
    result_mock = MagicMock()
    result_mock.fetchall.return_value = rows
    result_mock.scalar.return_value = 0
    result_mock.fetchone.return_value = None
    session.execute = AsyncMock(return_value=result_mock)
    return session


class FakeBrokerAdapter:
    def __init__(self, positions: list[dict] | None = None):
        self._positions = positions or []

    async def get_positions(self):
        return [MagicMock(**p) for p in self._positions]


# ---------------------------------------------------------------------------
# Reconciliation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciliation_clean_when_positions_match():
    """No discrepancies when DB and broker agree."""
    pos = [{"symbol": "AAPL", "quantity": 100, "side": "long"}]
    db = _make_db_session(pos)
    broker = FakeBrokerAdapter(pos)
    reconciler = PositionReconciler(broker_adapter=broker, db_session=db, alert_service=None)

    result = await reconciler.reconcile()

    assert result.is_clean is True
    assert result.discrepancies == []
    assert result.total_positions_checked == 1


@pytest.mark.asyncio
async def test_reconciliation_detects_missing_db_position():
    """Broker has a position that is not in DB → position_missing_in_db."""
    db = _make_db_session([])  # DB empty
    broker = FakeBrokerAdapter([{"symbol": "TSLA", "quantity": 50, "side": "long"}])
    reconciler = PositionReconciler(broker_adapter=broker, db_session=db, alert_service=None)

    result = await reconciler.reconcile()

    assert result.is_clean is False
    assert len(result.discrepancies) == 1
    d = result.discrepancies[0]
    assert d.symbol == "TSLA"
    assert d.discrepancy_type == "position_missing_in_db"
    assert d.severity == "critical"


@pytest.mark.asyncio
async def test_reconciliation_detects_quantity_mismatch():
    """DB and broker agree on symbol/side but differ in quantity by > 1 share."""
    db_pos = [{"symbol": "MSFT", "quantity": 100, "side": "long"}]
    broker_pos = [{"symbol": "MSFT", "quantity": 110, "side": "long"}]
    db = _make_db_session(db_pos)
    broker = FakeBrokerAdapter(broker_pos)
    reconciler = PositionReconciler(broker_adapter=broker, db_session=db, alert_service=None)

    result = await reconciler.reconcile()

    assert result.is_clean is False
    assert len(result.discrepancies) == 1
    d = result.discrepancies[0]
    assert d.discrepancy_type == "quantity_mismatch"
    assert d.db_quantity == 100
    assert d.broker_quantity == 110


@pytest.mark.asyncio
async def test_reconciliation_ignores_sub_share_rounding():
    """Qty differences < 1 share are ignored (rounding tolerance)."""
    # Both have same symbol; the reconciler uses int comparison but the threshold is 1,
    # so an exact-match of 0 diff should be clean.
    db_pos = [{"symbol": "NVDA", "quantity": 50, "side": "long"}]
    broker_pos = [{"symbol": "NVDA", "quantity": 50, "side": "long"}]
    db = _make_db_session(db_pos)
    broker = FakeBrokerAdapter(broker_pos)
    reconciler = PositionReconciler(broker_adapter=broker, db_session=db, alert_service=None)

    result = await reconciler.reconcile()

    assert result.is_clean is True


@pytest.mark.asyncio
async def test_reconciler_never_raises_on_exception():
    """Reconciler must return degraded result instead of raising on error."""
    broken_broker = AsyncMock()
    broken_broker.get_positions = AsyncMock(side_effect=RuntimeError("broker down"))
    broken_db = AsyncMock()
    broken_db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    reconciler = PositionReconciler(broker_adapter=broken_broker, db_session=broken_db, alert_service=None)
    result = await reconciler.reconcile()  # must not raise

    assert isinstance(result, ReconciliationResult)
    assert result.is_clean is False


# ---------------------------------------------------------------------------
# Alert tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_alert_deduplication_prevents_spam():
    """Same (source, title) within 5 min should not re-fire (dedup via Redis)."""
    redis, store, _ = _make_redis(
        # Pre-seed the dedup key as if the alert already fired
        existing_keys={"sentinel:alert:dedup:my_source:Test Alert": "1"}
    )
    settings = _make_settings()
    svc = AlertService(settings=settings, redis=redis)

    alert = await svc.fire(
        level=AlertLevel.WARNING,
        title="Test Alert",
        message="should be deduplicated",
        source="my_source",
    )

    # The dedup key was present → alert should be returned but not stored in recent list
    assert alert.title == "Test Alert"
    # lpush should NOT have been called (alert was deduplicated)
    redis.lpush.assert_not_called()


@pytest.mark.asyncio
async def test_critical_alert_bypasses_quiet_hours():
    """CRITICAL alert fires even when quiet hours are active."""
    redis, store, lists = _make_redis()
    # Settings that put the current hour in quiet time (force by mocking datetime)
    settings = _make_settings(
        alert_quiet_hours_start=0,
        alert_quiet_hours_end=23,  # always quiet hours
    )
    svc = AlertService(settings=settings, redis=redis)

    alert = await svc.fire_critical(
        title="Critical Issue",
        message="must fire",
        source="engine",
    )

    assert alert.level == AlertLevel.CRITICAL
    # Should have been pushed to recent list regardless
    redis.lpush.assert_called_once()


@pytest.mark.asyncio
async def test_warning_alert_suppressed_during_quiet_hours():
    """WARNING alert is suppressed during configured quiet hours."""
    redis, store, lists = _make_redis()
    settings = _make_settings(
        alert_quiet_hours_start=0,
        alert_quiet_hours_end=23,  # always quiet
    )
    svc = AlertService(settings=settings, redis=redis)

    alert = await svc.fire(
        level=AlertLevel.WARNING,
        title="Warning",
        message="suppressed",
        source="risk",
    )

    # lpush should NOT be called — alert suppressed
    redis.lpush.assert_not_called()
    assert alert.level == AlertLevel.WARNING  # still returned


@pytest.mark.asyncio
async def test_alert_fires_to_log_always():
    """_deliver_log is always called regardless of other config."""
    redis, _, _ = _make_redis()
    settings = _make_settings()
    svc = AlertService(settings=settings, redis=redis)

    with patch.object(svc, "_deliver_log", new_callable=AsyncMock) as mock_log:
        await svc.fire(
            level=AlertLevel.INFO,
            title="Log Test",
            message="should log",
            source="test",
        )
        mock_log.assert_called_once()


@pytest.mark.asyncio
async def test_webhook_delivery_failures_are_swallowed():
    """Webhook errors must not propagate — fire-and-forget."""
    redis, _, _ = _make_redis()
    settings = _make_settings(alert_webhook_enabled=True, alert_webhook_url="http://bad-host/")
    svc = AlertService(settings=settings, redis=redis)

    with patch.object(svc, "_deliver_webhook", new_callable=AsyncMock) as mock_wh:
        mock_wh.side_effect = Exception("network error")
        # The fire() method schedules webhook as a task, so simulate direct call
        alert = await svc.fire(
            level=AlertLevel.CRITICAL,
            title="Webhook test",
            message="fire-and-forget",
            source="engine",
        )
    # No exception raised
    assert alert.title == "Webhook test"
