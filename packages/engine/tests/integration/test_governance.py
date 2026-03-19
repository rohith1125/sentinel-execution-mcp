"""Integration tests for strategy governance lifecycle."""
from __future__ import annotations

import pytest
import pytest_asyncio

from sentinel.domain.types import StrategyState
from sentinel.governance.service import GovernanceError, GovernanceService


# ---------------------------------------------------------------------------
# Test 1: register_strategy creates DRAFT record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_strategy_creates_draft(db_session):
    svc = GovernanceService(db_session)
    record = await svc.register_strategy(
        name="test_strat_1",
        description="Unit test strategy",
        config={"symbols": ["AAPL"], "max_position": 1000},
    )

    assert record is not None
    assert record.name == "test_strat_1"
    assert record.state == StrategyState.DRAFT.value


# ---------------------------------------------------------------------------
# Test 2: evaluate_promotion returns not_eligible for fresh strategy (no trades)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evaluate_promotion_not_eligible_no_trades(db_session):
    svc = GovernanceService(db_session)
    await svc.register_strategy(
        name="test_strat_2",
        description="Fresh strategy with no trades",
        config={},
    )

    eligible, evaluation = await svc.evaluate_promotion(
        "test_strat_2", StrategyState.BACKTEST_APPROVED
    )

    assert not eligible
    assert "gaps" in evaluation
    assert len(evaluation["gaps"]) > 0  # missing trades/metrics


# ---------------------------------------------------------------------------
# Test 3: promote_strategy raises GovernanceError if criteria not met
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_promote_raises_if_criteria_not_met(db_session):
    svc = GovernanceService(db_session)
    await svc.register_strategy(
        name="test_strat_3",
        description="Criteria not met strategy",
        config={},
    )

    with pytest.raises(GovernanceError):
        await svc.promote_strategy(
            strategy_name="test_strat_3",
            target_state=StrategyState.BACKTEST_APPROVED,
            approved_by="human_trader",
        )


# ---------------------------------------------------------------------------
# Test 4: live promotion requires human approved_by (not 'system')
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_promotion_requires_human_approver(db_session):
    svc = GovernanceService(db_session)
    await svc.register_strategy(
        name="test_strat_4",
        description="Live promotion test",
        config={},
    )

    with pytest.raises(GovernanceError, match="human approver"):
        await svc.promote_strategy(
            strategy_name="test_strat_4",
            target_state=StrategyState.LIVE_APPROVED,
            approved_by="system",
        )


# ---------------------------------------------------------------------------
# Test 5: suspend_strategy changes state to SUSPENDED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_suspend_strategy(db_session):
    svc = GovernanceService(db_session)
    await svc.register_strategy(
        name="test_strat_5",
        description="Suspendable strategy",
        config={},
    )

    record = await svc.suspend_strategy(
        strategy_name="test_strat_5",
        reason="Drawdown limit breached",
        operator="risk_manager",
    )

    assert record.state == StrategyState.SUSPENDED.value


# ---------------------------------------------------------------------------
# Test 6: strategy starts in DRAFT, can be fetched by name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_strategy_starts_in_draft_and_is_fetchable(db_session):
    svc = GovernanceService(db_session)
    await svc.register_strategy(
        name="test_strat_6",
        description="Fetch test",
        config={"param": 42},
    )

    fetched = await svc.get_strategy("test_strat_6")
    assert fetched is not None
    assert fetched.state == StrategyState.DRAFT.value

    # Also confirm duplicates are rejected
    with pytest.raises(GovernanceError, match="already exists"):
        await svc.register_strategy("test_strat_6", "duplicate", {})
