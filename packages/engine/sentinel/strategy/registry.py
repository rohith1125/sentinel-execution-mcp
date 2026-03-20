"""StrategyRegistry — plugin loader for strategy implementations."""

from __future__ import annotations

from datetime import UTC

from sentinel.market.provider import Bar
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyBase, StrategyResult


class StrategyRegistry:
    """Central registry for strategy plugins.

    Strategies call registry.register(instance) at module load time.
    The registry is the single source of truth for active strategies.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, StrategyBase] = {}

    def register(self, strategy: StrategyBase) -> None:
        """Register a strategy instance. Overwrites if same name exists."""
        self._strategies[strategy.name] = strategy

    def get(self, name: str) -> StrategyBase | None:
        """Retrieve a strategy by name."""
        return self._strategies.get(name)

    def list_strategies(self) -> list[str]:
        """Return sorted list of registered strategy names."""
        return sorted(self._strategies.keys())

    def evaluate_all(
        self,
        symbol: str,
        bars: list[Bar],
        regime: RegimeSnapshot,
    ) -> list[StrategyResult]:
        """Run all registered strategies. Returns all results (including no-signal)."""
        results: list[StrategyResult] = []
        for strategy in self._strategies.values():
            try:
                result = strategy.evaluate(symbol, bars, regime)
                results.append(result)
            except Exception as exc:
                # Gracefully degrade — record failure as no-signal result
                from datetime import datetime

                results.append(
                    StrategyResult(
                        strategy_name=strategy.name,
                        symbol=symbol,
                        signal=None,
                        evaluated_at=datetime.now(tz=UTC),
                        bars_used=len(bars),
                        regime_compatibility=0.0,
                        rejection_reason=f"Strategy raised exception: {exc!r}",
                    )
                )
        return results


# Global singleton registry
registry = StrategyRegistry()


# Auto-register all built-in strategy implementations
def _load_builtin_strategies() -> None:
    from sentinel.strategy.implementations import (  # noqa: F401
        atr_swing,
        ema_trend,
        momentum_breakout,
        orb,
        rsi_mean_reversion,
        vwap_reclaim,
    )


_load_builtin_strategies()
