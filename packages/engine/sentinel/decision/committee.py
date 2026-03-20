"""DecisionCommittee — combines independent filter votes into a trade decision."""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from sentinel.decision.filters import (
    vote_beta_context,
    vote_liquidity,
    vote_portfolio_concentration,
    vote_regime_compatibility,
    vote_risk_reward,
    vote_signal_confidence,
    vote_time_of_day,
    vote_volatility_sanity,
)
from sentinel.decision.models import DecisionRequest, DecisionResult, VoteRecord
from sentinel.domain.types import DecisionOutcome
from sentinel.market.provider import Bar
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategySignal
from sentinel.strategy.registry import registry as global_registry

logger = structlog.get_logger(__name__)

_APPROVE_THRESHOLD = 0.65
_HUMAN_THRESHOLD = 0.45

# Hard-reject voters — a single reject from these immediately kills the trade
_HARD_REJECT_VOTERS = frozenset(
    [
        "regime_gate",
        "concentration_filter",
        "time_of_day_filter",
    ]
)

# Anomaly conditions that escalate APPROVED → REQUIRES_HUMAN_APPROVAL
_LARGE_POSITION_PCT = 0.05  # position would be > 5% of account
_HIGH_VOL_TRADEABILITY_THRESHOLD = 0.35


class DecisionCommittee:
    """Multi-signal voting committee for trade decisions.

    Voting logic:
    - Any HARD REJECT voter = immediate REJECTED
    - Weighted average >= 0.65 → APPROVED
    - Weighted average 0.45-0.65 → REQUIRES_HUMAN_APPROVAL
    - Weighted average < 0.45 → REJECTED
    - If approved but anomalous (new position, high vol, large size) → REQUIRES_HUMAN_APPROVAL
    """

    def __init__(
        self,
        strategy_registry: StrategyRegistry | None = None,  # type: ignore[name-defined]  # noqa: F821
        min_rr: float = 1.5,
        min_confidence: float = 0.55,
        min_spread_bps: float = 20.0,
        min_volume: int = 100_000,
        max_concentration_pct: float = 0.10,
    ) -> None:
        self._registry = strategy_registry or global_registry
        self._min_rr = min_rr
        self._min_confidence = min_confidence
        self._min_spread_bps = min_spread_bps
        self._min_volume = min_volume
        self._max_concentration_pct = max_concentration_pct

    def deliberate(
        self,
        request: DecisionRequest,
        bars: list[Bar],
    ) -> DecisionResult:
        """Run all filters and produce a final decision."""
        signal = request.signal
        regime = request.regime
        now = datetime.now(tz=UTC)

        # Resolve strategy for regime compatibility check
        strategy_name = getattr(signal, "strategy_name", None)
        strategy: StrategyBase | None = None
        if strategy_name:
            strategy = self._registry.get(strategy_name)

        votes: list[VoteRecord] = []

        # --- Collect all votes ---

        # 1. Regime compatibility (hard reject possible)
        if strategy is not None:
            votes.append(vote_regime_compatibility(signal, regime, strategy))

        # 2. Liquidity
        votes.append(
            vote_liquidity(
                request.snapshot,
                min_spread_bps=self._min_spread_bps,
                min_volume=self._min_volume,
            )
        )

        # 3. Volatility sanity
        votes.append(vote_volatility_sanity(bars, signal))

        # 4. Risk/reward
        votes.append(vote_risk_reward(signal, min_rr=self._min_rr))

        # 5. Concentration
        votes.append(
            vote_portfolio_concentration(
                symbol=request.symbol,
                side=signal.side,
                portfolio_context={
                    **request.portfolio_context,
                    "account_value": float(request.account_value),
                },
                max_pct=self._max_concentration_pct,
            )
        )

        # 6. Beta / macro context
        votes.append(vote_beta_context(regime, signal))

        # 7. Signal confidence
        votes.append(vote_signal_confidence(signal, min_confidence=self._min_confidence))

        # 8. Time of day
        votes.append(vote_time_of_day(now))

        # --- Tally votes ---
        approve_count = sum(1 for v in votes if v.vote == "approve")
        reject_count = sum(1 for v in votes if v.vote == "reject")
        abstain_count = sum(1 for v in votes if v.vote == "abstain")

        # Check for hard rejects first
        hard_rejects = [v for v in votes if v.vote == "reject" and v.voter in _HARD_REJECT_VOTERS]
        if hard_rejects:
            reasons = "; ".join(v.reason for v in hard_rejects)
            explanation = f"HARD REJECT by: {', '.join(v.voter for v in hard_rejects)}. {reasons}"
            logger.info(
                "committee.hard_reject",
                symbol=request.symbol,
                voters=[v.voter for v in hard_rejects],
            )
            return DecisionResult(
                outcome=DecisionOutcome.REJECTED,
                confidence=0.0,
                votes=votes,
                approve_count=approve_count,
                reject_count=reject_count,
                abstain_count=abstain_count,
                weighted_score=0.0,
                explanation=explanation,
                requires_human_reason=None,
                decided_at=now,
            )

        # Compute weighted score
        total_weight = sum(v.weight for v in votes)
        if total_weight == 0:
            weighted_score = 0.5
        else:
            weighted_score = sum(v.numeric_score * v.weight for v in votes) / total_weight

        # Map score to outcome
        if weighted_score >= _APPROVE_THRESHOLD:
            raw_outcome = DecisionOutcome.APPROVED
        elif weighted_score >= _HUMAN_THRESHOLD:
            raw_outcome = DecisionOutcome.REQUIRES_HUMAN_APPROVAL
        else:
            raw_outcome = DecisionOutcome.REJECTED

        # Escalation check: approved but anomalous?
        human_reason: str | None = None
        if raw_outcome == DecisionOutcome.APPROVED:
            anomalies = self._detect_anomalies(request, regime, signal, weighted_score)
            if anomalies:
                raw_outcome = DecisionOutcome.REQUIRES_HUMAN_APPROVAL
                human_reason = " | ".join(anomalies)

        if raw_outcome == DecisionOutcome.REQUIRES_HUMAN_APPROVAL and human_reason is None:
            human_reason = (
                f"Weighted committee score {weighted_score:.3f} is in the gray zone "
                f"[{_HUMAN_THRESHOLD:.2f}, {_APPROVE_THRESHOLD:.2f}). "
                "Human review required before execution."
            )

        # Build explanation
        vote_summary = ", ".join(
            f"{v.voter}={'✓' if v.vote == 'approve' else ('?' if v.vote == 'abstain' else '✗')}" for v in votes
        )
        explanation = (
            f"Outcome: {raw_outcome.value}. "
            f"Score: {weighted_score:.3f} (approve>={_APPROVE_THRESHOLD}, human>={_HUMAN_THRESHOLD}). "
            f"Votes: approve={approve_count}, reject={reject_count}, abstain={abstain_count}. "
            f"[{vote_summary}]"
        )

        logger.info(
            "committee.decision",
            symbol=request.symbol,
            outcome=raw_outcome.value,
            score=weighted_score,
            approve=approve_count,
            reject=reject_count,
        )

        return DecisionResult(
            outcome=raw_outcome,
            confidence=round(weighted_score, 4),
            votes=votes,
            approve_count=approve_count,
            reject_count=reject_count,
            abstain_count=abstain_count,
            weighted_score=round(weighted_score, 4),
            explanation=explanation,
            requires_human_reason=human_reason,
            decided_at=now,
        )

    def _detect_anomalies(
        self,
        request: DecisionRequest,
        regime: RegimeSnapshot,
        signal: StrategySignal,
        score: float,
    ) -> list[str]:
        """Return list of anomaly descriptions that warrant human review."""
        anomalies: list[str] = []

        # Large position relative to account
        positions = request.portfolio_context.get("positions", {})
        account_value = float(request.account_value)
        if account_value > 0:
            existing_notional = float(positions.get(request.symbol, {}).get("notional", 0.0))
            pct = existing_notional / account_value
            if pct > _LARGE_POSITION_PCT:
                anomalies.append(f"Large existing position: {pct:.1%} of account in {request.symbol}.")

        # High volatility regime
        if regime.tradeability_score < _HIGH_VOL_TRADEABILITY_THRESHOLD:
            anomalies.append(f"Low tradeability score: {regime.tradeability_score:.2f} (regime: {regime.label.value}).")

        # New strategy (not yet in live state) — check notes
        _strategy = self._registry.get(signal.side.value)  # loose check
        # We check strategy_name if available via supporting_indicators
        _strategy_name = signal.supporting_indicators.get("strategy_name_flag", 0.0)

        # Score close to approval boundary (uncertain)
        if _APPROVE_THRESHOLD <= score < _APPROVE_THRESHOLD + 0.05:
            anomalies.append(
                f"Decision score {score:.3f} is close to approval boundary ({_APPROVE_THRESHOLD}). Low conviction."
            )

        return anomalies
