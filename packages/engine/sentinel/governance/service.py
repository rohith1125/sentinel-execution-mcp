"""
GovernanceService — manages the strategy lifecycle.

Progression: draft → research → backtest → paper → live.

This system is deliberately conservative. A strategy must earn each promotion
by demonstrating real edge in progressively more demanding conditions.
Live promotion always requires a human sign-off — no automated path to real money.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from sentinel.db.models import StrategyPromotion, StrategyRecord
from sentinel.domain.types import StrategyState
from sentinel.governance.criteria import CRITERIA, PromotionCriteria

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Valid promotion path — strategies may only advance forward (or be suspended/retired)
_PROMOTION_ORDER: list[StrategyState] = [
    StrategyState.DRAFT,
    StrategyState.RESEARCH,
    StrategyState.BACKTEST,
    StrategyState.BACKTEST_APPROVED,
    StrategyState.PAPER,
    StrategyState.PAPER_APPROVED,
    StrategyState.LIVE,
    StrategyState.LIVE_APPROVED,
]


class GovernanceError(Exception):
    """Raised when a governance action cannot be completed."""


class GovernanceService:
    """
    Manages strategy lifecycle transitions with full audit trail.

    All promotion decisions are persisted as StrategyPromotion records.
    Suspension and retirement are always permitted regardless of current state.
    """

    def __init__(self, db: "AsyncSession") -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Strategy registration
    # ------------------------------------------------------------------

    async def register_strategy(
        self, name: str, description: str, config: dict
    ) -> StrategyRecord:
        """Create a new strategy in DRAFT state."""
        import json

        now = datetime.now(tz=timezone.utc)
        existing = await self.get_strategy(name)
        if existing is not None:
            raise GovernanceError(f"Strategy '{name}' already exists.")

        record = StrategyRecord(
            name=name,
            description=description,
            config=json.dumps(config),
            state=StrategyState.DRAFT.value,
            created_at=now,
            updated_at=now,
        )
        self._db.add(record)
        await self._db.flush()
        logger.info("GovernanceService: registered strategy '%s'", name)
        return record

    # ------------------------------------------------------------------
    # Evaluation (read-only, no side effects)
    # ------------------------------------------------------------------

    async def evaluate_promotion(
        self, strategy_name: str, target_state: StrategyState
    ) -> tuple[bool, dict]:
        """
        Check if strategy meets criteria for promotion.
        Returns (eligible, {criteria_results, metrics, gaps}).
        Does NOT promote — only evaluates.
        """
        criteria = CRITERIA.get(target_state)
        if criteria is None:
            return False, {
                "error": f"No promotion criteria defined for state '{target_state.value}'",
                "metrics": {},
                "gaps": [],
            }

        strategy = await self.get_strategy(strategy_name)
        if strategy is None:
            return False, {
                "error": f"Strategy '{strategy_name}' not found",
                "metrics": {},
                "gaps": [],
            }

        metrics = await self.compute_strategy_metrics(
            strategy_name, days=criteria.evaluation_period_days
        )

        criteria_results: dict[str, dict] = {}
        gaps: list[str] = []

        def _check(field: str, value: float, threshold: float, is_max: bool = False) -> bool:
            if is_max:
                passed = value <= threshold
            else:
                passed = value >= threshold
            criteria_results[field] = {
                "value": value,
                "threshold": threshold,
                "passed": passed,
                "direction": "max" if is_max else "min",
            }
            if not passed:
                direction = "below" if not is_max else "above"
                gaps.append(
                    f"{field}: {value:.4f} is {direction} required {'max' if is_max else 'min'} of {threshold:.4f}"
                )
            return passed

        all_pass = True
        all_pass &= _check("trade_count", metrics.get("trade_count", 0), criteria.min_trades)
        all_pass &= _check("win_rate", metrics.get("win_rate", 0.0), criteria.min_win_rate)
        all_pass &= _check("profit_factor", metrics.get("profit_factor", 0.0), criteria.min_profit_factor)
        all_pass &= _check("max_drawdown_pct", metrics.get("max_drawdown_pct", 1.0), criteria.max_drawdown_pct, is_max=True)
        all_pass &= _check("sharpe_ratio", metrics.get("sharpe_ratio", 0.0), criteria.min_sharpe_ratio)
        all_pass &= _check("expectancy_r", metrics.get("expectancy_r", 0.0), criteria.min_expectancy_r)

        if criteria.max_avg_slippage_bps < 999:
            all_pass &= _check(
                "avg_slippage_bps",
                metrics.get("avg_slippage_bps", 999.0),
                criteria.max_avg_slippage_bps,
                is_max=True,
            )

        if criteria.min_fill_rate > 0:
            all_pass &= _check("fill_rate", metrics.get("fill_rate", 0.0), criteria.min_fill_rate)

        return all_pass, {
            "criteria_results": criteria_results,
            "metrics": metrics,
            "gaps": gaps,
            "requires_human_sign_off": criteria.requires_human_sign_off,
            "evaluation_period_days": criteria.evaluation_period_days,
        }

    # ------------------------------------------------------------------
    # Promotion
    # ------------------------------------------------------------------

    async def promote_strategy(
        self,
        strategy_name: str,
        target_state: StrategyState,
        approved_by: str,
        notes: str = "",
    ) -> StrategyRecord:
        """
        Promote strategy to target state.
        Raises GovernanceError if criteria not met.
        Live promotion requires approved_by != 'system'.
        """
        now = datetime.now(tz=timezone.utc)

        strategy = await self.get_strategy(strategy_name)
        if strategy is None:
            raise GovernanceError(f"Strategy '{strategy_name}' not found.")

        criteria = CRITERIA.get(target_state)
        if criteria and criteria.requires_human_sign_off:
            if not approved_by or approved_by.lower() in ("system", "auto", "automated"):
                raise GovernanceError(
                    f"Promotion to '{target_state.value}' requires a human approver. "
                    f"Automated promotion to live trading is not permitted."
                )

        eligible, evaluation = await self.evaluate_promotion(strategy_name, target_state)
        if not eligible and criteria is not None:
            gaps = evaluation.get("gaps", [])
            raise GovernanceError(
                f"Strategy '{strategy_name}' does not meet criteria for '{target_state.value}'. "
                f"Gaps: {'; '.join(gaps) if gaps else 'see evaluation'}"
            )

        from_state = strategy.state
        strategy.state = target_state.value
        strategy.updated_at = now

        promotion = StrategyPromotion(
            strategy_id=strategy.id,
            from_state=from_state,
            to_state=target_state.value,
            approved_by=approved_by,
            notes=notes,
        )
        self._db.add(promotion)
        await self._db.flush()

        logger.info(
            "GovernanceService: '%s' promoted from %s -> %s by %s",
            strategy_name,
            from_state,
            target_state.value,
            approved_by,
        )
        return strategy

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------

    async def suspend_strategy(
        self, strategy_name: str, reason: str, operator: str
    ) -> StrategyRecord:
        """Immediately suspend strategy. Can be triggered by drift detection."""
        return await self._transition_to(
            strategy_name, StrategyState.SUSPENDED, operator, reason
        )

    async def retire_strategy(
        self, strategy_name: str, reason: str, operator: str
    ) -> StrategyRecord:
        """Retire a strategy permanently."""
        return await self._transition_to(
            strategy_name, StrategyState.RETIRED, operator, reason
        )

    async def _transition_to(
        self,
        strategy_name: str,
        target: StrategyState,
        operator: str,
        reason: str,
    ) -> StrategyRecord:
        strategy = await self.get_strategy(strategy_name)
        if strategy is None:
            raise GovernanceError(f"Strategy '{strategy_name}' not found.")

        from_state = strategy.state
        now = datetime.now(tz=timezone.utc)
        strategy.state = target.value
        strategy.updated_at = now

        promotion = StrategyPromotion(
            strategy_id=strategy.id,
            from_state=from_state,
            to_state=target.value,
            approved_by=operator,
            notes=reason,
        )
        self._db.add(promotion)
        await self._db.flush()

        logger.warning(
            "GovernanceService: '%s' transitioned %s -> %s by %s. Reason: %s",
            strategy_name,
            from_state,
            target.value,
            operator,
            reason,
        )
        return strategy

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get_strategy(self, name: str) -> StrategyRecord | None:
        """Fetch strategy by name."""
        from sqlalchemy import select
        try:
            stmt = select(StrategyRecord).where(StrategyRecord.name == name)
            result = await self._db.execute(stmt)
            return result.scalar_one_or_none()
        except Exception:
            logger.exception("GovernanceService: failed to fetch strategy '%s'", name)
            return None

    async def list_strategies(
        self, state: StrategyState | None = None
    ) -> list[StrategyRecord]:
        """List all strategies, optionally filtered by state."""
        from sqlalchemy import select
        try:
            stmt = select(StrategyRecord)
            if state is not None:
                stmt = stmt.where(StrategyRecord.state == state.value)
            result = await self._db.execute(stmt)
            return list(result.scalars().all())
        except Exception:
            logger.exception("GovernanceService: failed to list strategies")
            return []

    # ------------------------------------------------------------------
    # Metrics computation
    # ------------------------------------------------------------------

    async def compute_strategy_metrics(
        self, strategy_name: str, days: int = 30
    ) -> dict:
        """
        Compute performance metrics from TradeJournal records.

        Returns: win_rate, profit_factor, sharpe, max_drawdown, expectancy_r,
        avg_slippage_bps, fill_rate, trade_count, avg_hold_hours.
        """
        from sqlalchemy import select
        from sentinel.db.models import TradeJournal

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        try:
            stmt = (
                select(TradeJournal)
                .where(
                    TradeJournal.strategy_name == strategy_name,
                    TradeJournal.closed_at >= cutoff,
                )
            )
            result = await self._db.execute(stmt)
            trades = list(result.scalars().all())
        except Exception:
            logger.exception(
                "GovernanceService: failed to fetch trades for '%s'", strategy_name
            )
            return self._empty_metrics()

        if not trades:
            return self._empty_metrics()

        pnls = [float(t.pnl) for t in trades if t.pnl is not None]
        r_multiples = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
        slippages = [float(t.slippage_bps) for t in trades if t.slippage_bps is not None]
        hold_hours = []
        for t in trades:
            if t.opened_at and t.closed_at:
                delta = t.closed_at - t.opened_at
                hold_hours.append(delta.total_seconds() / 3600)

        trade_count = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        win_rate = len(wins) / trade_count if trade_count > 0 else 0.0
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0

        # Sharpe ratio (annualised, assuming daily returns; simplified here as trade-level)
        if len(pnls) > 1:
            avg_pnl = sum(pnls) / len(pnls)
            variance = sum((p - avg_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
            std_pnl = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_ratio = (avg_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0.0
        else:
            sharpe_ratio = 0.0

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for p in pnls:
            cumulative += p
            if cumulative > peak:
                peak = cumulative
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

        expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
        avg_slippage_bps = sum(slippages) / len(slippages) if slippages else 0.0
        avg_hold_hours = sum(hold_hours) / len(hold_hours) if hold_hours else 0.0

        # Fill rate: filled orders / total orders submitted
        total_orders = sum(1 for t in trades if t.order_count is not None and t.order_count > 0)
        filled_orders = sum(1 for t in trades if t.fill_count is not None and t.fill_count > 0)
        fill_rate = filled_orders / total_orders if total_orders > 0 else 1.0

        return {
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "sharpe_ratio": sharpe_ratio,
            "expectancy_r": expectancy_r,
            "avg_slippage_bps": avg_slippage_bps,
            "fill_rate": fill_rate,
            "avg_hold_hours": avg_hold_hours,
            "gross_wins": gross_wins,
            "gross_losses": gross_losses,
        }

    def _empty_metrics(self) -> dict:
        return {
            "trade_count": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "expectancy_r": 0.0,
            "avg_slippage_bps": 0.0,
            "fill_rate": 0.0,
            "avg_hold_hours": 0.0,
            "gross_wins": 0.0,
            "gross_losses": 0.0,
        }

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    async def check_strategy_drift(self, strategy_name: str) -> dict:
        """
        Detect drift signals:
        - Rolling 5-trade expectancy vs historical expectancy (>30% degradation = warn)
        - Increasing consecutive losses
        - Slippage trending upward (fill quality degradation)
        - Regime mismatch rate increasing

        Returns: {drifting: bool, signals: list[str], severity: 'ok'|'warn'|'critical'}
        """
        from sqlalchemy import select, desc
        from sentinel.db.models import TradeJournal

        signals: list[str] = []

        try:
            # Fetch last 20 trades
            stmt = (
                select(TradeJournal)
                .where(TradeJournal.strategy_name == strategy_name)
                .order_by(desc(TradeJournal.closed_at))
                .limit(20)
            )
            result = await self._db.execute(stmt)
            recent_trades = list(result.scalars().all())
        except Exception:
            logger.exception("GovernanceService.check_strategy_drift: DB error for '%s'", strategy_name)
            return {"drifting": False, "signals": [], "severity": "ok"}

        if len(recent_trades) < 5:
            return {
                "drifting": False,
                "signals": ["Insufficient trade history for drift detection (< 5 trades)"],
                "severity": "ok",
            }

        # --- Signal 1: Rolling 5-trade expectancy degradation ---
        all_r = [float(t.r_multiple) for t in recent_trades if t.r_multiple is not None]
        if len(all_r) >= 5:
            recent_5_r = sum(all_r[:5]) / 5
            historical_r = sum(all_r[5:]) / len(all_r[5:]) if len(all_r) > 5 else None
            if historical_r is not None and historical_r > 0:
                degradation = (historical_r - recent_5_r) / historical_r
                if degradation >= 0.30:
                    signals.append(
                        f"Expectancy degraded {degradation:.0%}: recent 5-trade R={recent_5_r:.2f} "
                        f"vs historical R={historical_r:.2f}"
                    )

        # --- Signal 2: Consecutive losses ---
        consecutive_losses = 0
        for t in recent_trades:
            pnl = float(t.pnl) if t.pnl is not None else 0.0
            if pnl < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 4:
            signals.append(f"{consecutive_losses} consecutive losses detected.")

        # --- Signal 3: Slippage trend ---
        slippages = [float(t.slippage_bps) for t in recent_trades if t.slippage_bps is not None]
        if len(slippages) >= 6:
            recent_3_slip = sum(slippages[:3]) / 3
            prev_3_slip = sum(slippages[3:6]) / 3
            if prev_3_slip > 0 and recent_3_slip > prev_3_slip * 1.5:
                signals.append(
                    f"Slippage trending upward: recent avg {recent_3_slip:.1f} bps "
                    f"vs prior avg {prev_3_slip:.1f} bps (50%+ increase)."
                )

        # --- Signal 4: Regime mismatch rate ---
        regimes = [t.regime_label for t in recent_trades if t.regime_label]
        if regimes:
            mismatches = sum(1 for t in recent_trades if getattr(t, "regime_mismatch", False))
            mismatch_rate = mismatches / len(regimes)
            if mismatch_rate >= 0.40:
                signals.append(
                    f"Regime mismatch rate {mismatch_rate:.0%} — strategy may be "
                    f"trading in adverse conditions."
                )

        # Determine severity
        if len(signals) == 0:
            severity = "ok"
            drifting = False
        elif len(signals) == 1:
            severity = "warn"
            drifting = True
        else:
            severity = "critical"
            drifting = True

        return {
            "drifting": drifting,
            "signals": signals,
            "severity": severity,
            "consecutive_losses": consecutive_losses,
        }
