"""
ReportGenerator — daily and weekly performance summaries.
"""
from __future__ import annotations

import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates daily, weekly, and strategy-level performance summaries."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Daily summary
    # ------------------------------------------------------------------

    async def daily_summary(self, report_date: date | None = None) -> dict:
        """
        Returns:
        - trades executed: count, win/loss, gross P&L
        - best trade, worst trade
        - regime distribution
        - strategies active
        - risk events (halts, rejections)
        - decisions by outcome (approved/rejected/deferred)
        """
        if report_date is None:
            report_date = datetime.now(tz=UTC).date()

        day_start = datetime.combine(report_date, datetime.min.time()).replace(tzinfo=UTC)
        day_end = day_start + timedelta(days=1)

        try:
            from sqlalchemy import select

            from sentinel.db.models import AuditEvent, TradeJournal

            # Trade stats
            stmt = select(TradeJournal).where(
                TradeJournal.exit_timestamp >= day_start,
                TradeJournal.exit_timestamp < day_end,
            )
            result = await self._db.execute(stmt)
            trades = list(result.scalars().all())

            pnls = [float(t.realized_pnl) for t in trades if t.realized_pnl is not None]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            gross_pnl = sum(pnls)
            best_trade = max(pnls) if pnls else None
            worst_trade = min(pnls) if pnls else None

            # Regime distribution
            regime_counts: dict[str, int] = {}
            for t in trades:
                label = t.regime_at_entry or "unknown"
                regime_counts[label] = regime_counts.get(label, 0) + 1

            # Active strategies
            active_strategies = list({t.strategy_id for t in trades if t.strategy_id})

            # Audit events for the day (risk events, decisions)
            audit_stmt = select(AuditEvent).where(
                AuditEvent.created_at >= day_start,
                AuditEvent.created_at < day_end,
            )
            audit_result = await self._db.execute(audit_stmt)
            audit_events = list(audit_result.scalars().all())

            risk_events = [
                {
                    "event_id": e.id,
                    "type": e.event_type,
                    "outcome": e.decision_outcome,
                    "explanation": e.decision_explanation,
                    "at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in audit_events
                if e.event_type in ("risk_halt", "kill_switch")
            ]

            decision_outcomes: dict[str, int] = {}
            for e in audit_events:
                if e.event_type == "trade_decision":
                    outcome_key = e.decision_outcome or "unknown"
                    decision_outcomes[outcome_key] = decision_outcomes.get(outcome_key, 0) + 1

            return {
                "date": report_date.isoformat(),
                "trade_count": len(trades),
                "win_count": len(wins),
                "loss_count": len(losses),
                "gross_pnl": gross_pnl,
                "best_trade": best_trade,
                "worst_trade": worst_trade,
                "win_rate": len(wins) / len(pnls) if pnls else None,
                "regime_distribution": regime_counts,
                "active_strategies": active_strategies,
                "risk_events": risk_events,
                "decision_outcomes": decision_outcomes,
            }

        except Exception:
            logger.exception("ReportGenerator.daily_summary: error for date %s", report_date)
            return {"date": report_date.isoformat(), "error": "Failed to generate daily summary"}

    # ------------------------------------------------------------------
    # Weekly summary
    # ------------------------------------------------------------------

    async def weekly_summary(self, week_ending: date | None = None) -> dict:
        """Aggregated weekly view with trend analysis."""
        if week_ending is None:
            week_ending = datetime.now(tz=UTC).date()

        # Week is Mon–Sun; compute week start
        days_since_monday = week_ending.weekday()
        week_start = week_ending - timedelta(days=days_since_monday)

        start_dt = datetime.combine(week_start, datetime.min.time()).replace(tzinfo=UTC)
        end_dt = datetime.combine(week_ending + timedelta(days=1), datetime.min.time()).replace(tzinfo=UTC)

        try:
            from sqlalchemy import select

            from sentinel.db.models import AuditEvent, TradeJournal

            stmt = select(TradeJournal).where(
                TradeJournal.closed_at >= start_dt,
                TradeJournal.closed_at < end_dt,
            )
            result = await self._db.execute(stmt)
            trades = list(result.scalars().all())

            pnls = [float(t.pnl) for t in trades if t.pnl is not None]
            r_multiples = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            gross_pnl = sum(pnls)
            gross_wins = sum(wins)
            gross_losses = abs(sum(losses))
            profit_factor = gross_wins / gross_losses if gross_losses > 0 else None
            expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else None

            # Max drawdown over the week
            cumulative = 0.0
            peak = 0.0
            max_drawdown = 0.0
            for p in sorted(pnls):
                cumulative += p
                peak = max(peak, cumulative)
                dd = (peak - cumulative) / peak if peak > 0 else 0.0
                max_drawdown = max(max_drawdown, dd)

            # Strategy breakdown
            strategy_pnl: dict[str, float] = {}
            for t in trades:
                name = t.strategy_name or "unknown"
                strategy_pnl[name] = strategy_pnl.get(name, 0.0) + float(t.pnl or 0)

            # Risk events count
            audit_stmt = select(AuditEvent).where(
                AuditEvent.created_at >= start_dt,
                AuditEvent.created_at < end_dt,
                AuditEvent.event_type == "risk_halt",
            )
            audit_result = await self._db.execute(audit_stmt)
            risk_halt_count = len(list(audit_result.scalars().all()))

            # Day-by-day P&L trend
            daily_pnl: dict[str, float] = {}
            for t in trades:
                if t.closed_at:
                    day_key = t.closed_at.date().isoformat()
                    daily_pnl[day_key] = daily_pnl.get(day_key, 0.0) + float(t.pnl or 0)

            return {
                "week_start": week_start.isoformat(),
                "week_ending": week_ending.isoformat(),
                "trade_count": len(trades),
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": len(wins) / len(pnls) if pnls else None,
                "gross_pnl": gross_pnl,
                "gross_wins": gross_wins,
                "gross_losses": gross_losses,
                "profit_factor": profit_factor,
                "expectancy_r": expectancy_r,
                "max_drawdown_pct": max_drawdown,
                "strategy_breakdown": strategy_pnl,
                "risk_halt_count": risk_halt_count,
                "daily_pnl_trend": daily_pnl,
            }

        except Exception:
            logger.exception("ReportGenerator.weekly_summary: error for week ending %s", week_ending)
            return {
                "week_ending": week_ending.isoformat(),
                "error": "Failed to generate weekly summary",
            }

    # ------------------------------------------------------------------
    # Strategy scorecard
    # ------------------------------------------------------------------

    async def strategy_scorecard(self, strategy_name: str, days: int = 30) -> dict:
        """
        Complete strategy scorecard:
        - trade statistics (count, win rate, profit factor)
        - risk metrics (max drawdown, sharpe, sortino)
        - expectancy in R-multiples
        - fill quality (avg slippage, fill rate)
        - regime performance breakdown
        - recent drift signals
        - promotion eligibility status
        """
        from sqlalchemy import desc, select

        from sentinel.db.models import TradeJournal

        cutoff = datetime.now(tz=UTC) - timedelta(days=days)

        try:
            stmt = (
                select(TradeJournal)
                .where(
                    TradeJournal.strategy_name == strategy_name,
                    TradeJournal.closed_at >= cutoff,
                )
                .order_by(desc(TradeJournal.closed_at))
            )
            result = await self._db.execute(stmt)
            trades = list(result.scalars().all())
        except Exception:
            logger.exception(
                "ReportGenerator.strategy_scorecard: DB error for '%s'", strategy_name
            )
            return {"strategy": strategy_name, "error": "Failed to load trades"}

        if not trades:
            return {
                "strategy": strategy_name,
                "days": days,
                "trade_count": 0,
                "message": "No trades in evaluation period",
            }

        pnls = [float(t.pnl) for t in trades if t.pnl is not None]
        r_multiples = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
        slippages = [float(t.slippage_bps) for t in trades if t.slippage_bps is not None]
        hold_hours = []
        for t in trades:
            if t.opened_at and t.closed_at:
                hold_hours.append((t.closed_at - t.opened_at).total_seconds() / 3600)

        wins = [p for p in pnls if p > 0]
        losses_neg = [p for p in pnls if p < 0]
        trade_count = len(pnls)
        win_rate = len(wins) / trade_count if trade_count else 0.0
        gross_wins = sum(wins)
        gross_losses = abs(sum(losses_neg))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf") if gross_wins > 0 else 0.0
        expectancy_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0.0
        avg_slippage_bps = sum(slippages) / len(slippages) if slippages else 0.0
        avg_hold_hours = sum(hold_hours) / len(hold_hours) if hold_hours else 0.0

        # Sharpe
        if len(pnls) > 1:
            avg_p = sum(pnls) / len(pnls)
            variance = sum((p - avg_p) ** 2 for p in pnls) / (len(pnls) - 1)
            std_p = math.sqrt(variance) if variance > 0 else 0.0
            sharpe = (avg_p / std_p) * math.sqrt(252) if std_p > 0 else 0.0
        else:
            sharpe = 0.0

        # Sortino (downside deviation only)
        downside = [p - 0 for p in pnls if p < 0]
        if len(downside) > 1:
            avg_d = sum(downside) / len(downside)
            down_var = sum((p - avg_d) ** 2 for p in downside) / (len(downside) - 1)
            down_std = math.sqrt(down_var) if down_var > 0 else 0.0
            avg_p_all = sum(pnls) / len(pnls)
            sortino = (avg_p_all / down_std) * math.sqrt(252) if down_std > 0 else 0.0
        else:
            sortino = 0.0

        # Max drawdown
        cumulative = 0.0
        peak = 0.0
        max_drawdown = 0.0
        for p in pnls:
            cumulative += p
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0.0
            max_drawdown = max(max_drawdown, dd)

        # Fill rate
        total_orders = sum(t.order_count or 0 for t in trades)
        filled_orders = sum(t.fill_count or 0 for t in trades)
        fill_rate = filled_orders / total_orders if total_orders > 0 else 1.0

        # Regime performance breakdown
        regime_perf: dict[str, dict] = {}
        for t in trades:
            label = t.regime_label or "unknown"
            pnl_val = float(t.pnl or 0)
            if label not in regime_perf:
                regime_perf[label] = {"count": 0, "total_pnl": 0.0, "wins": 0}
            regime_perf[label]["count"] += 1
            regime_perf[label]["total_pnl"] += pnl_val
            if pnl_val > 0:
                regime_perf[label]["wins"] += 1

        for _, data in regime_perf.items():
            count = data["count"]
            data["win_rate"] = data["wins"] / count if count > 0 else 0.0
            data["avg_pnl"] = data["total_pnl"] / count if count > 0 else 0.0

        # Promotion eligibility
        from sentinel.governance.criteria import CRITERIA
        promotion_eligibility: dict[str, bool] = {}
        _metrics = {
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "sharpe_ratio": sharpe,
            "expectancy_r": expectancy_r,
            "avg_slippage_bps": avg_slippage_bps,
            "fill_rate": fill_rate,
        }
        for state, criteria in CRITERIA.items():
            eligible = (
                trade_count >= criteria.min_trades
                and win_rate >= criteria.min_win_rate
                and profit_factor >= criteria.min_profit_factor
                and max_drawdown <= criteria.max_drawdown_pct
                and sharpe >= criteria.min_sharpe_ratio
                and expectancy_r >= criteria.min_expectancy_r
                and (criteria.max_avg_slippage_bps >= 999 or avg_slippage_bps <= criteria.max_avg_slippage_bps)
                and fill_rate >= criteria.min_fill_rate
            )
            promotion_eligibility[state.value] = eligible

        # Drift signals (reuse governance logic inline)
        _recent_r = r_multiples[:5] if len(r_multiples) >= 5 else r_multiples
        drift_signals: list[str] = []
        if len(r_multiples) >= 10:
            recent_5 = sum(r_multiples[:5]) / 5
            hist = sum(r_multiples[5:]) / len(r_multiples[5:])
            if hist > 0 and (hist - recent_5) / hist >= 0.30:
                drift_signals.append(f"Expectancy degradation: recent R={recent_5:.2f} vs historical R={hist:.2f}")

        consecutive_losses = 0
        for t in trades:
            if (t.pnl or 0) < 0:
                consecutive_losses += 1
            else:
                break
        if consecutive_losses >= 4:
            drift_signals.append(f"{consecutive_losses} consecutive losses")

        return {
            "strategy": strategy_name,
            "days": days,
            "trade_count": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_drawdown,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "expectancy_r": expectancy_r,
            "avg_slippage_bps": avg_slippage_bps,
            "fill_rate": fill_rate,
            "avg_hold_hours": avg_hold_hours,
            "gross_pnl": sum(pnls),
            "gross_wins": gross_wins,
            "gross_losses": gross_losses,
            "regime_breakdown": regime_perf,
            "promotion_eligibility": promotion_eligibility,
            "drift_signals": drift_signals,
            "consecutive_losses": consecutive_losses,
        }

    # ------------------------------------------------------------------
    # Trade blotter
    # ------------------------------------------------------------------

    async def trade_blotter(
        self,
        start: datetime,
        end: datetime,
        strategy: str | None = None,
    ) -> list[dict]:
        """Tabular trade history for a time window."""
        try:
            from sqlalchemy import asc, select

            from sentinel.db.models import TradeJournal

            stmt = (
                select(TradeJournal)
                .where(
                    TradeJournal.closed_at >= start,
                    TradeJournal.closed_at <= end,
                )
                .order_by(asc(TradeJournal.closed_at))
            )
            if strategy:
                stmt = stmt.where(TradeJournal.strategy_name == strategy)

            result = await self._db.execute(stmt)
            trades = list(result.scalars().all())

            return [
                {
                    "journal_id": t.journal_id,
                    "strategy": t.strategy_name,
                    "symbol": t.symbol,
                    "side": t.side,
                    "quantity": t.quantity,
                    "filled_qty": t.filled_qty,
                    "entry_price": float(t.entry_price) if t.entry_price else None,
                    "pnl": float(t.pnl) if t.pnl is not None else None,
                    "r_multiple": float(t.r_multiple) if t.r_multiple is not None else None,
                    "slippage_bps": float(t.slippage_bps) if t.slippage_bps is not None else None,
                    "regime": t.regime_label,
                    "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                    "closed_at": t.closed_at.isoformat() if t.closed_at else None,
                    "hold_hours": (
                        (t.closed_at - t.opened_at).total_seconds() / 3600
                        if t.closed_at and t.opened_at
                        else None
                    ),
                }
                for t in trades
            ]
        except Exception:
            logger.exception("ReportGenerator.trade_blotter: error for window %s-%s", start, end)
            return []
