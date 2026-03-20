"""
Unit tests for individual risk check functions.

Each check is exercised for:
  - Pass case (nominal conditions)
  - Fail case (limit exceeded)
  - Boundary value (at the exact limit)
  - Edge cases (zero account value, empty inputs, etc.)

All functions are pure — no I/O, no database, fully synchronous.
"""
from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from sentinel.domain.types import OrderSide
from sentinel.risk.checks import (
    check_consecutive_losses_cooldown,
    check_correlated_exposure,
    check_daily_drawdown,
    check_gross_exposure,
    check_kill_switch,
    check_liquidity_threshold,
    check_max_concurrent_positions,
    check_no_trade_window,
    check_per_trade_risk,
    check_sector_concentration,
    check_slippage_estimate,
    check_spread_threshold,
    check_symbol_concentration,
    check_weekly_drawdown,
)
from sentinel.risk.models import KillSwitchState

# ---------------------------------------------------------------------------
# check_kill_switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_clean_state_passes(self):
        state = KillSwitchState()
        result = check_kill_switch(state, "momentum_breakout", "AAPL")
        assert result.passed
        assert result.is_hard_block

    def test_global_halt_blocks_all(self):
        state = KillSwitchState(global_halt=True, halt_reason="Risk event")
        result = check_kill_switch(state, "momentum_breakout", "AAPL")
        assert not result.passed
        assert result.is_hard_block
        assert "Risk event" in result.message

    def test_global_halt_message_includes_reason(self):
        state = KillSwitchState(global_halt=True, halt_reason="Circuit breaker")
        result = check_kill_switch(state, "any_strat", "ANY")
        assert "Circuit breaker" in result.message

    def test_global_halt_message_includes_halted_by(self):
        state = KillSwitchState(global_halt=True, halted_by="operator@firm.com")
        result = check_kill_switch(state, "any_strat", "ANY")
        assert "operator@firm.com" in result.message

    def test_global_halt_with_timestamp(self):
        ts = datetime(2024, 1, 15, 9, 45, 0, tzinfo=UTC)
        state = KillSwitchState(global_halt=True, halted_at=ts)
        result = check_kill_switch(state, "any_strat", "ANY")
        assert not result.passed
        assert "2024" in result.message

    def test_strategy_halt_blocks_that_strategy(self):
        state = KillSwitchState(halted_strategies={"momentum_breakout"})
        result = check_kill_switch(state, "momentum_breakout", "AAPL")
        assert not result.passed
        assert "momentum_breakout" in result.message

    def test_strategy_halt_does_not_block_other_strategy(self):
        state = KillSwitchState(halted_strategies={"momentum_breakout"})
        result = check_kill_switch(state, "vwap_reclaim", "AAPL")
        assert result.passed

    def test_multiple_strategy_halts(self):
        state = KillSwitchState(halted_strategies={"strat_a", "strat_b"})
        assert not check_kill_switch(state, "strat_a", "AAPL").passed
        assert not check_kill_switch(state, "strat_b", "AAPL").passed
        assert check_kill_switch(state, "strat_c", "AAPL").passed

    def test_symbol_halt_blocks_that_symbol(self):
        state = KillSwitchState(halted_symbols={"AAPL"})
        result = check_kill_switch(state, "any_strategy", "AAPL")
        assert not result.passed
        assert "AAPL" in result.message

    def test_symbol_halt_does_not_block_other_symbol(self):
        state = KillSwitchState(halted_symbols={"AAPL"})
        result = check_kill_switch(state, "any_strategy", "MSFT")
        assert result.passed

    def test_global_halt_overrides_clean_symbol_and_strategy(self):
        """Even if strategy/symbol not halted individually, global halt wins."""
        state = KillSwitchState(global_halt=True, halted_strategies=set(), halted_symbols=set())
        assert not check_kill_switch(state, "clean_strat", "CLEAN").passed

    def test_result_metrics_present_on_pass(self):
        state = KillSwitchState()
        result = check_kill_switch(state, "any", "ANY")
        assert "global_halt" in result.metrics
        assert result.metrics["global_halt"] == 0.0


# ---------------------------------------------------------------------------
# check_daily_drawdown
# ---------------------------------------------------------------------------


class TestDailyDrawdown:
    def test_positive_pnl_passes(self):
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("500"),
            unrealized_pnl=Decimal("200"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert result.passed
        assert result.is_hard_block

    def test_zero_pnl_passes(self):
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert result.passed

    def test_within_limit_passes(self):
        # 1.5% loss < 2% limit
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-1500"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert result.passed

    def test_exceeds_limit_blocks(self):
        # 2.1% loss > 2% limit
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-2100"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_unrealized_loss_combined_with_realized(self):
        # Realized -1200, unrealized -900 = combined -2100 = 2.1%
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-1200"),
            unrealized_pnl=Decimal("-900"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert not result.passed

    def test_unrealized_gain_offsets_realized_loss(self):
        # Realized -2500 but unrealized +600 = net -1900 = 1.9% < 2%
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-2500"),
            unrealized_pnl=Decimal("600"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert result.passed

    def test_boundary_exactly_at_limit(self):
        # Exactly 2.0% = passes (check is strict less-than)
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-2000"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        # At boundary: 2000/100000 = 0.02, check is 0.02 < 0.02 = False → blocks
        assert not result.passed

    def test_boundary_one_cent_below_limit(self):
        # -1999.99 / 100000 = 1.99999% < 2% → passes
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-1999.99"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert result.passed

    def test_zero_account_value_blocks(self):
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("0"),
            max_daily_drawdown_pct=0.02,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_negative_account_value_blocks(self):
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            account_value=Decimal("-1000"),
            max_daily_drawdown_pct=0.02,
        )
        assert not result.passed

    def test_metrics_populated(self):
        result = check_daily_drawdown(
            realized_pnl_today=Decimal("-1000"),
            unrealized_pnl=Decimal("-500"),
            account_value=Decimal("100000"),
            max_daily_drawdown_pct=0.02,
        )
        assert "drawdown_pct" in result.metrics
        assert "max_daily_drawdown_pct" in result.metrics
        assert abs(result.metrics["drawdown_pct"] - 0.015) < 1e-6


# ---------------------------------------------------------------------------
# check_weekly_drawdown
# ---------------------------------------------------------------------------


class TestWeeklyDrawdown:
    def test_no_loss_passes(self):
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("1000"),
            account_value=Decimal("100000"),
            max_weekly_drawdown_pct=0.04,
        )
        assert result.passed

    def test_within_limit_passes(self):
        # 3% loss < 4% limit
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("-3000"),
            account_value=Decimal("100000"),
            max_weekly_drawdown_pct=0.04,
        )
        assert result.passed

    def test_exceeds_limit_blocks(self):
        # 4.5% loss > 4% limit
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("-4500"),
            account_value=Decimal("100000"),
            max_weekly_drawdown_pct=0.04,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_zero_account_value_blocks(self):
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("-100"),
            account_value=Decimal("0"),
            max_weekly_drawdown_pct=0.04,
        )
        assert not result.passed

    def test_positive_week_passes(self):
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("5000"),
            account_value=Decimal("100000"),
            max_weekly_drawdown_pct=0.04,
        )
        assert result.passed

    def test_metrics_populated(self):
        result = check_weekly_drawdown(
            realized_pnl_week=Decimal("-2000"),
            account_value=Decimal("100000"),
        )
        assert "weekly_loss_pct" in result.metrics
        assert abs(result.metrics["weekly_loss_pct"] - 0.02) < 1e-6


# ---------------------------------------------------------------------------
# check_max_concurrent_positions
# ---------------------------------------------------------------------------


class TestMaxConcurrentPositions:
    def test_zero_positions_passes(self):
        result = check_max_concurrent_positions(open_position_count=0, max_concurrent=10)
        assert result.passed

    def test_below_limit_passes(self):
        result = check_max_concurrent_positions(open_position_count=5, max_concurrent=10)
        assert result.passed

    def test_one_below_limit_passes(self):
        result = check_max_concurrent_positions(open_position_count=9, max_concurrent=10)
        assert result.passed

    def test_at_limit_blocks(self):
        # check is open_count < max → at limit means not passed
        result = check_max_concurrent_positions(open_position_count=10, max_concurrent=10)
        assert not result.passed
        assert result.is_hard_block

    def test_exceeds_limit_blocks(self):
        result = check_max_concurrent_positions(open_position_count=15, max_concurrent=10)
        assert not result.passed

    def test_max_concurrent_one(self):
        """Edge: only 1 position allowed at a time."""
        assert check_max_concurrent_positions(0, 1).passed
        assert not check_max_concurrent_positions(1, 1).passed

    def test_utilization_metric_is_correct(self):
        result = check_max_concurrent_positions(open_position_count=5, max_concurrent=10)
        assert abs(result.metrics["utilization_pct"] - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# check_per_trade_risk
# ---------------------------------------------------------------------------


class TestPerTradeRisk:
    def test_small_risk_passes(self):
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("500"),
            account_value=Decimal("100000"),
            max_trade_risk_pct=0.02,
        )
        assert result.passed

    def test_exact_limit_passes(self):
        # 2.0% risk == 2.0% limit → passed (check is <=)
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("2000"),
            account_value=Decimal("100000"),
            max_trade_risk_pct=0.02,
        )
        assert result.passed  # <= not <

    def test_exceeds_limit_blocks(self):
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("2001"),
            account_value=Decimal("100000"),
            max_trade_risk_pct=0.02,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_zero_account_value_blocks(self):
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("100"),
            account_value=Decimal("0"),
            max_trade_risk_pct=0.02,
        )
        assert not result.passed

    def test_zero_risk_passes(self):
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("0"),
            account_value=Decimal("100000"),
            max_trade_risk_pct=0.02,
        )
        assert result.passed

    def test_metrics_populated(self):
        result = check_per_trade_risk(
            proposed_risk_amount=Decimal("1000"),
            account_value=Decimal("100000"),
            max_trade_risk_pct=0.02,
        )
        assert "risk_pct" in result.metrics
        assert abs(result.metrics["risk_pct"] - 0.01) < 1e-6


# ---------------------------------------------------------------------------
# check_symbol_concentration
# ---------------------------------------------------------------------------


class TestSymbolConcentration:
    def test_small_position_passes(self):
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("5000"),
            current_positions={},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert result.passed

    def test_exceeds_concentration_blocks(self):
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("11000"),
            current_positions={},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_existing_plus_new_exceeds_limit(self):
        # Already have 8k, adding 3k = 11k = 11% > 10%
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("3000"),
            current_positions={"AAPL": Decimal("8000")},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert not result.passed

    def test_existing_plus_new_within_limit(self):
        # Already have 6k, adding 3k = 9k = 9% < 10%
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("3000"),
            current_positions={"AAPL": Decimal("6000")},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert result.passed

    def test_other_symbols_dont_count(self):
        # Large MSFT position shouldn't affect AAPL check
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("5000"),
            current_positions={"MSFT": Decimal("50000")},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert result.passed

    def test_zero_account_value_blocks(self):
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("1000"),
            current_positions={},
            account_value=Decimal("0"),
            max_symbol_pct=0.10,
        )
        assert not result.passed

    def test_exact_limit_passes(self):
        # Exactly 10% = passes (check is <=)
        result = check_symbol_concentration(
            symbol="AAPL",
            proposed_notional=Decimal("10000"),
            current_positions={},
            account_value=Decimal("100000"),
            max_symbol_pct=0.10,
        )
        assert result.passed


# ---------------------------------------------------------------------------
# check_gross_exposure
# ---------------------------------------------------------------------------


class TestGrossExposure:
    def test_small_trade_passes(self):
        result = check_gross_exposure(
            proposed_notional=Decimal("5000"),
            current_gross_exposure=Decimal("20000"),
            account_value=Decimal("100000"),
            max_gross_pct=0.80,
        )
        assert result.passed

    def test_pushes_over_limit_blocks(self):
        result = check_gross_exposure(
            proposed_notional=Decimal("10000"),
            current_gross_exposure=Decimal("75000"),
            account_value=Decimal("100000"),
            max_gross_pct=0.80,
        )
        assert not result.passed
        assert result.is_hard_block

    def test_exactly_at_limit_passes(self):
        # 80000 / 100000 = 80% == 80% → passes (<=)
        result = check_gross_exposure(
            proposed_notional=Decimal("5000"),
            current_gross_exposure=Decimal("75000"),
            account_value=Decimal("100000"),
            max_gross_pct=0.80,
        )
        assert result.passed

    def test_zero_account_value_blocks(self):
        result = check_gross_exposure(
            proposed_notional=Decimal("1000"),
            current_gross_exposure=Decimal("0"),
            account_value=Decimal("0"),
            max_gross_pct=0.80,
        )
        assert not result.passed

    def test_metrics_include_projected_pct(self):
        result = check_gross_exposure(
            proposed_notional=Decimal("10000"),
            current_gross_exposure=Decimal("30000"),
            account_value=Decimal("100000"),
            max_gross_pct=0.80,
        )
        assert "projected_pct" in result.metrics
        assert abs(result.metrics["projected_pct"] - 0.40) < 1e-6


# ---------------------------------------------------------------------------
# check_spread_threshold
# ---------------------------------------------------------------------------


class TestSpreadThreshold:
    def test_narrow_spread_passes(self):
        result = check_spread_threshold(spread_bps=5.0, max_spread_bps=30.0)
        assert result.passed
        assert result.is_hard_block

    def test_wide_spread_blocks(self):
        result = check_spread_threshold(spread_bps=50.0, max_spread_bps=30.0)
        assert not result.passed

    def test_exact_limit_passes(self):
        result = check_spread_threshold(spread_bps=30.0, max_spread_bps=30.0)
        assert result.passed  # <=

    def test_just_over_limit_blocks(self):
        result = check_spread_threshold(spread_bps=30.1, max_spread_bps=30.0)
        assert not result.passed

    def test_zero_spread_passes(self):
        result = check_spread_threshold(spread_bps=0.0, max_spread_bps=30.0)
        assert result.passed

    def test_metrics_include_spread_bps(self):
        result = check_spread_threshold(spread_bps=15.0, max_spread_bps=30.0)
        assert result.metrics["spread_bps"] == 15.0
        assert result.metrics["max_spread_bps"] == 30.0

    def test_wide_spread_message_mentions_liquidity(self):
        result = check_spread_threshold(spread_bps=100.0, max_spread_bps=30.0)
        assert not result.passed
        assert "TOO WIDE" in result.message or "wide" in result.message.lower()


# ---------------------------------------------------------------------------
# check_liquidity_threshold
# ---------------------------------------------------------------------------


class TestLiquidityThreshold:
    def test_liquid_stock_passes(self):
        result = check_liquidity_threshold(
            avg_daily_volume=5_000_000,
            proposed_shares=1000,
            min_adv=500_000,
            max_adv_pct=0.01,
        )
        assert result.passed

    def test_adv_too_low_blocks(self):
        result = check_liquidity_threshold(
            avg_daily_volume=100_000,
            proposed_shares=100,
            min_adv=500_000,
            max_adv_pct=0.01,
        )
        assert not result.passed
        assert result.is_hard_block
        assert "illiquid" in result.message.lower() or "below minimum" in result.message.lower()

    def test_order_too_large_blocks(self):
        # 5_000_000 ADV, 1% = 50_000 max shares. Proposing 60_000.
        result = check_liquidity_threshold(
            avg_daily_volume=5_000_000,
            proposed_shares=60_000,
            min_adv=500_000,
            max_adv_pct=0.01,
        )
        assert not result.passed
        assert "market impact" in result.message.lower() or "exceeds" in result.message.lower()

    def test_exactly_at_max_shares_passes(self):
        # 5_000_000 * 0.01 = 50_000 → proposing exactly 50_000
        result = check_liquidity_threshold(
            avg_daily_volume=5_000_000,
            proposed_shares=50_000,
            min_adv=500_000,
            max_adv_pct=0.01,
        )
        assert result.passed

    def test_one_share_over_max_blocks(self):
        result = check_liquidity_threshold(
            avg_daily_volume=5_000_000,
            proposed_shares=50_001,
            min_adv=500_000,
            max_adv_pct=0.01,
        )
        assert not result.passed

    def test_metrics_populated(self):
        result = check_liquidity_threshold(
            avg_daily_volume=2_000_000,
            proposed_shares=5_000,
        )
        assert "avg_daily_volume" in result.metrics
        assert "adv_pct" in result.metrics


# ---------------------------------------------------------------------------
# check_no_trade_window
# ---------------------------------------------------------------------------


class TestNoTradeWindow:
    MARKET_OPEN = time(9, 30)
    MARKET_CLOSE = time(16, 0)

    def test_mid_session_passes(self):
        dt = datetime(2024, 1, 15, 13, 30, 0)  # 1:30 PM
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert result.passed
        assert result.is_hard_block

    def test_before_open_blocks(self):
        dt = datetime(2024, 1, 15, 8, 0, 0)  # 8:00 AM
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert not result.passed

    def test_after_close_blocks(self):
        dt = datetime(2024, 1, 15, 16, 30, 0)  # 4:30 PM
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert not result.passed

    def test_exactly_at_open_passes(self):
        dt = datetime(2024, 1, 15, 9, 30, 0)
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert result.passed

    def test_exactly_at_close_blocks(self):
        dt = datetime(2024, 1, 15, 16, 0, 0)
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert not result.passed

    def test_final_5_min_blocks(self):
        dt = datetime(2024, 1, 15, 15, 56, 0)  # 4 minutes before close
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert not result.passed
        assert "no-trade window" in result.message.lower() or "blackout" in result.message.lower()

    def test_exactly_5_min_before_close_blocks(self):
        dt = datetime(2024, 1, 15, 15, 55, 0)
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert not result.passed

    def test_6_min_before_close_passes(self):
        dt = datetime(2024, 1, 15, 15, 54, 0)
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert result.passed

    def test_minutes_to_close_metric(self):
        dt = datetime(2024, 1, 15, 15, 0, 0)  # 1 hour before close
        result = check_no_trade_window(dt, self.MARKET_OPEN, self.MARKET_CLOSE)
        assert result.passed
        assert "minutes_to_close" in result.metrics
        assert result.metrics["minutes_to_close"] > 55  # approximately 60 minutes


# ---------------------------------------------------------------------------
# check_consecutive_losses_cooldown
# ---------------------------------------------------------------------------


class TestConsecutiveLossesCooldown:
    def test_no_trades_passes(self):
        result = check_consecutive_losses_cooldown(recent_trades=[])
        assert result.passed

    def test_all_winning_trades_passes(self):
        trades = [{"pnl": 100}, {"pnl": 200}, {"pnl": 50}]
        result = check_consecutive_losses_cooldown(trades)
        assert result.passed

    def test_two_losses_below_threshold_passes(self):
        trades = [{"pnl": 100}, {"pnl": -50}, {"pnl": -80}]
        result = check_consecutive_losses_cooldown(trades, max_consecutive_losses=3)
        assert result.passed

    def test_three_consecutive_losses_triggers_cooldown(self):
        now = datetime.utcnow()
        trades = [
            {"pnl": 100},
            {"pnl": -50, "closed_at": (now - timedelta(minutes=5)).isoformat()},
            {"pnl": -80, "closed_at": (now - timedelta(minutes=3)).isoformat()},
            {"pnl": -60, "closed_at": (now - timedelta(minutes=1)).isoformat()},
        ]
        result = check_consecutive_losses_cooldown(
            trades, max_consecutive_losses=3, cooldown_minutes=30
        )
        assert not result.passed

    def test_cooldown_expires_after_enough_time(self):
        old_time = datetime.utcnow() - timedelta(minutes=35)
        trades = [
            {"pnl": -50, "closed_at": (old_time - timedelta(minutes=2)).isoformat()},
            {"pnl": -80, "closed_at": (old_time - timedelta(minutes=1)).isoformat()},
            {"pnl": -60, "closed_at": old_time.isoformat()},
        ]
        result = check_consecutive_losses_cooldown(
            trades, max_consecutive_losses=3, cooldown_minutes=30
        )
        assert result.passed

    def test_win_resets_consecutive_count(self):
        now = datetime.utcnow()
        trades = [
            {"pnl": -50, "closed_at": (now - timedelta(minutes=20)).isoformat()},
            {"pnl": -80, "closed_at": (now - timedelta(minutes=15)).isoformat()},
            {"pnl": 100, "closed_at": (now - timedelta(minutes=10)).isoformat()},  # win resets
            {"pnl": -60, "closed_at": (now - timedelta(minutes=5)).isoformat()},
            {"pnl": -70, "closed_at": (now - timedelta(minutes=2)).isoformat()},
        ]
        result = check_consecutive_losses_cooldown(
            trades, max_consecutive_losses=3, cooldown_minutes=30
        )
        # Only 2 consecutive losses after the win
        assert result.passed

    def test_hard_block_configurable(self):
        now = datetime.utcnow()
        trades = [
            {"pnl": -50, "closed_at": (now - timedelta(minutes=5)).isoformat()},
            {"pnl": -80, "closed_at": (now - timedelta(minutes=3)).isoformat()},
            {"pnl": -60, "closed_at": (now - timedelta(minutes=1)).isoformat()},
        ]
        soft_result = check_consecutive_losses_cooldown(trades, hard_block=False)
        hard_result = check_consecutive_losses_cooldown(trades, hard_block=True)
        assert not soft_result.is_hard_block
        assert hard_result.is_hard_block

    def test_metrics_include_consecutive_losses(self):
        now = datetime.utcnow()
        trades = [
            {"pnl": -50, "closed_at": (now - timedelta(minutes=5)).isoformat()},
            {"pnl": -80, "closed_at": (now - timedelta(minutes=3)).isoformat()},
        ]
        result = check_consecutive_losses_cooldown(trades, max_consecutive_losses=5)
        assert result.metrics["consecutive_losses"] == 2.0


# ---------------------------------------------------------------------------
# check_sector_concentration
# ---------------------------------------------------------------------------


class TestSectorConcentration:
    def test_no_sector_data_passes_softly(self):
        result = check_sector_concentration(
            symbol_sector=None,
            proposed_notional=Decimal("10000"),
            sector_exposures={},
            account_value=Decimal("100000"),
        )
        assert result.passed
        assert not result.is_hard_block  # soft check

    def test_within_sector_limit_passes(self):
        result = check_sector_concentration(
            symbol_sector="Technology",
            proposed_notional=Decimal("10000"),
            sector_exposures={"Technology": Decimal("10000")},
            account_value=Decimal("100000"),
            max_sector_pct=0.25,
        )
        assert result.passed  # 20k / 100k = 20% < 25%

    def test_exceeds_sector_limit_warns(self):
        result = check_sector_concentration(
            symbol_sector="Technology",
            proposed_notional=Decimal("10000"),
            sector_exposures={"Technology": Decimal("20000")},
            account_value=Decimal("100000"),
            max_sector_pct=0.25,
        )
        assert not result.passed
        assert not result.is_hard_block  # soft check, not a hard block

    def test_zero_account_value_passes_softly(self):
        result = check_sector_concentration(
            symbol_sector="Technology",
            proposed_notional=Decimal("5000"),
            sector_exposures={},
            account_value=Decimal("0"),
        )
        assert result.passed  # skips check gracefully


# ---------------------------------------------------------------------------
# check_correlated_exposure
# ---------------------------------------------------------------------------


class TestCorrelatedExposure:
    def test_no_correlation_data_passes(self):
        result = check_correlated_exposure(
            symbol="AAPL",
            proposed_side=OrderSide.BUY,
            current_positions={},
            correlation_matrix=None,
        )
        assert result.passed
        assert not result.is_hard_block

    def test_no_correlated_positions_passes(self):
        matrix = {"AAPL": {"MSFT": 0.30, "AMZN": 0.25}}
        result = check_correlated_exposure(
            symbol="AAPL",
            proposed_side=OrderSide.BUY,
            current_positions={"MSFT": "buy", "AMZN": "buy"},
            correlation_matrix=matrix,
        )
        assert result.passed  # correlations < 0.70

    def test_highly_correlated_position_warns(self):
        matrix = {"AAPL": {"MSFT": 0.85}}
        result = check_correlated_exposure(
            symbol="AAPL",
            proposed_side=OrderSide.BUY,
            current_positions={"MSFT": "buy"},  # same side, high correlation
            correlation_matrix=matrix,
        )
        assert not result.passed
        assert not result.is_hard_block  # soft check

    def test_opposite_side_not_flagged(self):
        """High correlation in opposite direction is hedging, not amplification."""
        matrix = {"AAPL": {"MSFT": 0.90}}
        result = check_correlated_exposure(
            symbol="AAPL",
            proposed_side=OrderSide.BUY,
            current_positions={"MSFT": "sell"},  # opposite side
            correlation_matrix=matrix,
        )
        assert result.passed

    def test_symbol_not_in_matrix_passes(self):
        matrix = {"GOOGL": {"MSFT": 0.80}}
        result = check_correlated_exposure(
            symbol="AAPL",
            proposed_side=OrderSide.BUY,
            current_positions={"GOOGL": "buy"},
            correlation_matrix=matrix,
        )
        assert result.passed


# ---------------------------------------------------------------------------
# check_slippage_estimate
# ---------------------------------------------------------------------------


class TestSlippageEstimate:
    def test_small_order_liquid_stock_passes(self):
        result = check_slippage_estimate(
            order_type="limit",
            spread_bps=3.0,
            proposed_shares=500,
            avg_daily_volume=5_000_000,
        )
        assert result.passed
        assert not result.is_hard_block

    def test_market_order_pays_full_spread(self):
        """Market orders should show higher slippage than limit orders."""
        market_result = check_slippage_estimate(
            order_type="market", spread_bps=10.0, proposed_shares=100, avg_daily_volume=1_000_000
        )
        limit_result = check_slippage_estimate(
            order_type="limit", spread_bps=10.0, proposed_shares=100, avg_daily_volume=1_000_000
        )
        assert market_result.metrics["total_slippage_bps"] > limit_result.metrics["total_slippage_bps"]

    def test_large_order_warns(self):
        """5% of ADV triggers high market impact warning."""
        result = check_slippage_estimate(
            order_type="market",
            spread_bps=5.0,
            proposed_shares=50_000,
            avg_daily_volume=1_000_000,
        )
        assert not result.passed

    def test_metrics_populated(self):
        result = check_slippage_estimate(
            order_type="limit",
            spread_bps=5.0,
            proposed_shares=1000,
            avg_daily_volume=2_000_000,
        )
        assert "total_slippage_bps" in result.metrics
        assert "spread_cost_bps" in result.metrics
        assert "market_impact_bps" in result.metrics

    def test_stop_order_pays_full_spread(self):
        stop_result = check_slippage_estimate(
            order_type="stop", spread_bps=6.0, proposed_shares=100, avg_daily_volume=2_000_000
        )
        assert stop_result.metrics["spread_cost_bps"] == 6.0
