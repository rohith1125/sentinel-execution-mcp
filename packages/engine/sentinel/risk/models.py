from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from sentinel.domain.types import OrderSide

if TYPE_CHECKING:
    pass


@dataclass
class RiskCheckResult:
    """Result of a single risk check."""

    check_name: str
    passed: bool
    is_hard_block: bool  # if True, immediately reject regardless of other checks
    message: str
    metrics: dict[str, float]  # relevant numbers that were checked


@dataclass
class RiskAssessment:
    """Complete risk assessment for a proposed trade."""

    symbol: str
    proposed_shares: int
    proposed_side: OrderSide
    results: list[RiskCheckResult]
    passed: bool  # True only if ALL hard_block checks pass
    blocking_checks: list[str]  # names of failed hard-block checks
    warning_checks: list[str]  # names of failed soft checks
    assessed_at: datetime

    def to_explanation(self) -> str:
        """Human-readable summary of what passed and what blocked."""
        lines: list[str] = [
            f"Risk Assessment for {self.proposed_side.value} {self.proposed_shares} shares of {self.symbol}",
            f"Overall result: {'APPROVED' if self.passed else 'REJECTED'}",
            f"Assessed at: {self.assessed_at.isoformat()}",
            "",
        ]

        if self.blocking_checks:
            lines.append(f"BLOCKING failures ({len(self.blocking_checks)}):")
            for name in self.blocking_checks:
                result = next((r for r in self.results if r.check_name == name), None)
                if result:
                    lines.append(f"  [BLOCK] {name}: {result.message}")
            lines.append("")

        if self.warning_checks:
            lines.append(f"Warnings ({len(self.warning_checks)}):")
            for name in self.warning_checks:
                result = next((r for r in self.results if r.check_name == name), None)
                if result:
                    lines.append(f"  [WARN]  {name}: {result.message}")
            lines.append("")

        passed_checks = [r for r in self.results if r.passed]
        lines.append(f"Passed checks ({len(passed_checks)}):")
        for r in passed_checks:
            lines.append(f"  [OK]    {r.check_name}: {r.message}")

        return "\n".join(lines)


@dataclass
class KillSwitchState:
    """Persistent kill switch state stored in Redis."""

    global_halt: bool = False
    halted_strategies: set[str] = field(default_factory=set)
    halted_symbols: set[str] = field(default_factory=set)
    halt_reason: str = ""
    halted_at: datetime | None = None
    halted_by: str = ""
