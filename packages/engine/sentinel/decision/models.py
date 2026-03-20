"""Decision committee data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from sentinel.domain.types import DecisionOutcome
from sentinel.market.provider import Snapshot
from sentinel.strategy.base import StrategySignal


@dataclass
class VoteRecord:
    """A single vote cast by one filter in the committee."""

    voter: str  # e.g., "liquidity_filter", "regime_gate"
    vote: Literal["approve", "reject", "abstain"]
    weight: float  # 0-1, how much this vote matters
    reason: str
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def numeric_score(self) -> float:
        """Approve=1.0, abstain=0.5, reject=0.0 (weighted by self.weight)."""
        if self.vote == "approve":
            return 1.0
        if self.vote == "abstain":
            return 0.5
        return 0.0


@dataclass
class DecisionRequest:
    """Input bundle for a committee deliberation."""

    symbol: str
    signal: StrategySignal
    regime: RegimeSnapshot  # type: ignore[name-defined]  # noqa: F821
    snapshot: Snapshot
    portfolio_context: dict  # current positions, exposure, P&L, etc.
    account_value: Decimal


@dataclass
class DecisionResult:
    """Full output of committee deliberation."""

    outcome: DecisionOutcome
    confidence: float  # overall committee confidence
    votes: list[VoteRecord]
    approve_count: int
    reject_count: int
    abstain_count: int
    weighted_score: float  # weighted average of all votes
    explanation: str  # human-readable summary
    requires_human_reason: str | None  # populated if REQUIRES_HUMAN_APPROVAL
    decided_at: datetime
