"""WatchlistScanner — runs strategies across the symbol universe."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import structlog

from sentinel.market.provider import Bar
from sentinel.market.service import MarketDataService
from sentinel.regime.classifier import RegimeClassifier
from sentinel.regime.models import RegimeSnapshot
from sentinel.strategy.base import StrategyResult
from sentinel.strategy.registry import StrategyRegistry, registry as global_registry

logger = structlog.get_logger(__name__)

_DEFAULT_BAR_LIMIT = 200
_DEFAULT_TIMEFRAME = "5Min"


class WatchlistScanner:
    """Runs strategies across a watchlist universe.

    For each symbol:
    1. Fetch recent bars from MarketDataService
    2. Classify regime with RegimeClassifier
    3. Run requested (or all) strategies
    4. Return only results with signals, sorted by confidence desc
    """

    def __init__(
        self,
        market_service: MarketDataService,
        classifier: RegimeClassifier | None = None,
        strategy_registry: StrategyRegistry | None = None,
        bar_limit: int = _DEFAULT_BAR_LIMIT,
        timeframe: str = _DEFAULT_TIMEFRAME,
        context_symbol: str = "SPY",
        max_concurrency: int = 10,
    ) -> None:
        self._market = market_service
        self._classifier = classifier or RegimeClassifier()
        self._registry = strategy_registry or global_registry
        self._bar_limit = bar_limit
        self._timeframe = timeframe
        self._context_symbol = context_symbol
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def scan(
        self,
        symbols: list[str],
        strategy_names: list[str] | None = None,
        regime_override: RegimeSnapshot | None = None,
    ) -> list[StrategyResult]:
        """Scan all symbols concurrently.

        Returns only results with signals, sorted by confidence descending.
        """
        # Fetch context bars (SPY) once for risk-off detection
        context_bars: list[Bar] | None = None
        if self._context_symbol and regime_override is None:
            try:
                now = datetime.now(tz=timezone.utc)
                context_bars = await self._market.get_bars(
                    self._context_symbol,
                    self._timeframe,
                    start=now - timedelta(hours=8),
                    end=now,
                    limit=self._bar_limit,
                )
            except Exception as exc:
                logger.warning(
                    "scanner.context_fetch_failed",
                    symbol=self._context_symbol,
                    error=str(exc),
                )

        tasks = [
            self._scan_with_semaphore(symbol, strategy_names, regime_override, context_bars)
            for symbol in symbols
        ]
        nested_results = await asyncio.gather(*tasks, return_exceptions=True)

        all_results: list[StrategyResult] = []
        for symbol, result in zip(symbols, nested_results):
            if isinstance(result, Exception):
                logger.error("scanner.symbol_failed", symbol=symbol, error=str(result))
                continue
            all_results.extend(result)  # type: ignore[arg-type]

        # Filter to only results with signals, sort by confidence desc
        with_signals = [r for r in all_results if r.signal is not None]
        with_signals.sort(
            key=lambda r: r.signal.confidence if r.signal else 0.0,
            reverse=True,
        )
        return with_signals

    async def scan_symbol(
        self,
        symbol: str,
        strategy_names: list[str] | None = None,
        regime_override: RegimeSnapshot | None = None,
        context_bars: list[Bar] | None = None,
    ) -> list[StrategyResult]:
        """Scan a single symbol. Returns all results (including no-signal)."""
        try:
            now = datetime.now(tz=timezone.utc)
            bars = await self._market.get_bars(
                symbol,
                self._timeframe,
                start=now - timedelta(hours=8),
                end=now,
                limit=self._bar_limit,
            )
        except Exception as exc:
            logger.error("scanner.bars_fetch_failed", symbol=symbol, error=str(exc))
            return []

        if not bars:
            logger.warning("scanner.no_bars", symbol=symbol)
            return []

        # Classify regime
        if regime_override is not None:
            regime = regime_override
        else:
            try:
                regime = self._classifier.classify(bars, symbol, context_bars)
            except Exception as exc:
                logger.error("scanner.classify_failed", symbol=symbol, error=str(exc))
                from sentinel.domain.types import RegimeLabel
                from sentinel.regime.models import StrategyCompatibility

                regime = RegimeSnapshot(
                    label=RegimeLabel.UNKNOWN,
                    confidence=0.0,
                    tradeability_score=0.0,
                    supporting_metrics={},
                    strategy_compatibility=StrategyCompatibility(
                        momentum_breakout=0.3,
                        vwap_reclaim=0.3,
                        ema_trend=0.3,
                        rsi_mean_reversion=0.3,
                        atr_swing=0.3,
                        orb=0.3,
                    ),
                    classified_at=datetime.now(tz=timezone.utc),
                    bars_analyzed=len(bars),
                    reasoning="Classification failed — using safe defaults.",
                )

        if not regime.is_tradeable():
            logger.info(
                "scanner.regime_not_tradeable",
                symbol=symbol,
                regime=regime.label.value,
                tradeability=regime.tradeability_score,
            )
            # Still run strategies but they'll self-reject on anti-regime

        # Run strategies
        if strategy_names is not None:
            strategies = [
                s for name in strategy_names
                if (s := self._registry.get(name)) is not None
            ]
            results = []
            for strategy in strategies:
                try:
                    results.append(strategy.evaluate(symbol, bars, regime))
                except Exception as exc:
                    logger.error(
                        "scanner.strategy_failed",
                        symbol=symbol,
                        strategy=strategy.name,
                        error=str(exc),
                    )
        else:
            results = self._registry.evaluate_all(symbol, bars, regime)

        return results

    async def _scan_with_semaphore(
        self,
        symbol: str,
        strategy_names: list[str] | None,
        regime_override: RegimeSnapshot | None,
        context_bars: list[Bar] | None,
    ) -> list[StrategyResult]:
        async with self._semaphore:
            return await self.scan_symbol(symbol, strategy_names, regime_override, context_bars)
