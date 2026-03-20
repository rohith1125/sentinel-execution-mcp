"""Integration tests for audit journal."""

from __future__ import annotations

import pytest

from sentinel.audit.journal import AuditJournal
from sentinel.domain.types import DecisionOutcome


def _trade_decision_kwargs(symbol: str = "AAPL", strategy_id: str = "strat-001") -> dict:
    return dict(
        symbol=symbol,
        strategy_id=strategy_id,
        regime_snapshot={"label": "trending_bull", "confidence": 0.85},
        signal_details={"signal": "breakout", "strength": 0.7},
        risk_check_results=[
            {"name": "position_size", "passed": True},
            {"name": "drawdown_limit", "passed": True},
        ],
        decision_outcome=DecisionOutcome.APPROVED,
        decision_explanation="All risk checks passed; regime aligns.",
        sizing_details={"shares": 100, "notional": 15000.0},
        execution_details={"order_type": "market"},
    )


# ---------------------------------------------------------------------------
# Test 1: record_trade_decision creates AuditEvent record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_trade_decision_creates_record(db_session):
    journal = AuditJournal(db_session)
    event = await journal.record_trade_decision(**_trade_decision_kwargs())

    assert event is not None
    assert event.event_id is not None
    assert event.symbol == "AAPL"
    assert event.event_type == "trade_decision"
    assert event.decision_outcome == DecisionOutcome.APPROVED.value


# ---------------------------------------------------------------------------
# Test 2: AuditEvent records have unique IDs (append-only pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_events_have_unique_ids(db_session):
    journal = AuditJournal(db_session)

    event1 = await journal.record_trade_decision(**_trade_decision_kwargs(symbol="AAPL"))
    event2 = await journal.record_trade_decision(**_trade_decision_kwargs(symbol="TSLA"))

    assert event1 is not None
    assert event2 is not None
    assert event1.event_id != event2.event_id


# ---------------------------------------------------------------------------
# Test 3: explain_trade returns dict with expected keys
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_trade_returns_expected_keys(db_session):
    journal = AuditJournal(db_session)
    event = await journal.record_trade_decision(**_trade_decision_kwargs())
    assert event is not None

    explanation = await journal.explain_trade(event.event_id)

    required_keys = {
        "event_id",
        "event_type",
        "symbol",
        "strategy",
        "outcome",
        "signal",
        "regime",
        "risk_assessment",
        "sizing",
        "execution",
    }
    for key in required_keys:
        assert key in explanation, f"Missing key in explain_trade result: {key}"

    assert explanation["symbol"] == "AAPL"
    assert explanation["event_id"] == event.event_id
    assert explanation["risk_assessment"]["total_checks"] == 2


# ---------------------------------------------------------------------------
# Test 4: get_recent_events returns records in reverse chronological order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_events_reverse_chronological(db_session):
    journal = AuditJournal(db_session)

    # Write 3 events
    e1 = await journal.record_trade_decision(**_trade_decision_kwargs(symbol="AAPL"))
    e2 = await journal.record_trade_decision(**_trade_decision_kwargs(symbol="TSLA"))
    e3 = await journal.record_trade_decision(**_trade_decision_kwargs(symbol="MSFT"))

    assert e1 and e2 and e3

    events = await journal.get_recent_events(limit=10)

    assert len(events) >= 3
    # Verify reverse chronological: most recent first
    timestamps = [e.created_at for e in events]
    assert timestamps == sorted(timestamps, reverse=True)

    # Confirm our events are present
    event_ids = {e.event_id for e in events}
    assert e1.event_id in event_ids
    assert e2.event_id in event_ids
    assert e3.event_id in event_ids
