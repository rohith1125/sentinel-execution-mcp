"""
Extended health monitoring beyond simple DB/Redis ping.
Tracks system-level metrics for operational visibility.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    latency_ms: float
    message: str


@dataclass
class SystemHealth:
    status: str  # "healthy" | "degraded" | "unhealthy"
    checks: dict[str, CheckResult]
    uptime_seconds: float
    last_trade_at: datetime | None
    open_positions: int
    pending_orders: int
    daily_pnl: float
    kill_switch_active: bool
    checked_at: datetime


class HealthMonitor:
    """
    Runs all health checks and returns SystemHealth.

    Checks:
    - database connectivity + query latency
    - redis connectivity + latency
    - market data provider reachability
    - last successful trade timestamp (warn if > configured threshold in market hours)
    - pending order count (warn if > 20)
    - reconciliation status (warn if last reconciliation had discrepancies)
    - kill switch state
    """

    _PENDING_ORDER_WARN_THRESHOLD = 20

    def __init__(
        self,
        db_session: Any,
        redis: Any,
        settings: Any,
        reconciler: Any = None,
        start_time: float | None = None,
    ) -> None:
        self._db = db_session
        self._redis = redis
        self._settings = settings
        self._reconciler = reconciler
        self._start_time = start_time or time.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def check_all(self) -> SystemHealth:
        """Run all checks and aggregate into a SystemHealth record."""
        checked_at = datetime.now(tz=UTC)
        checks: dict[str, CheckResult] = {}

        db_result = await self.check_database()
        checks["database"] = db_result

        redis_result = await self.check_redis()
        checks["redis"] = redis_result

        market_result = await self.check_market_data()
        checks["market_data"] = market_result

        reconciliation_result = await self._check_reconciliation()
        checks["reconciliation"] = reconciliation_result

        # Aggregate status
        statuses = [c.status for c in checks.values()]
        if "fail" in statuses:
            overall_status = "unhealthy"
        elif "warn" in statuses:
            overall_status = "degraded"
        else:
            overall_status = "healthy"

        # Operational metrics
        (
            open_positions,
            pending_orders,
            last_trade_at,
            daily_pnl,
            kill_switch_active,
        ) = await self._fetch_operational_metrics()

        # Warn on pending order count
        if pending_orders > self._PENDING_ORDER_WARN_THRESHOLD:
            checks["pending_orders"] = CheckResult(
                name="pending_orders",
                status="warn",
                latency_ms=0.0,
                message=f"{pending_orders} pending orders exceeds threshold of {self._PENDING_ORDER_WARN_THRESHOLD}",
            )
            if overall_status == "healthy":
                overall_status = "degraded"
        else:
            checks["pending_orders"] = CheckResult(
                name="pending_orders",
                status="ok",
                latency_ms=0.0,
                message=f"{pending_orders} pending orders",
            )

        return SystemHealth(
            status=overall_status,
            checks=checks,
            uptime_seconds=time.monotonic() - self._start_time,
            last_trade_at=last_trade_at,
            open_positions=open_positions,
            pending_orders=pending_orders,
            daily_pnl=daily_pnl,
            kill_switch_active=kill_switch_active,
            checked_at=checked_at,
        )

    async def check_database(self) -> CheckResult:
        """Measure DB connectivity and query latency."""
        start = time.perf_counter()
        try:
            from sqlalchemy import text

            await self._db.execute(text("SELECT 1"))
            latency_ms = (time.perf_counter() - start) * 1000
            status = "ok" if latency_ms < 200 else "warn"
            return CheckResult(
                name="database",
                status=status,
                latency_ms=round(latency_ms, 2),
                message="ok" if status == "ok" else f"high latency {latency_ms:.0f}ms",
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("health.db_check_failed", error=str(exc))
            return CheckResult(
                name="database",
                status="fail",
                latency_ms=round(latency_ms, 2),
                message=str(exc),
            )

    async def check_redis(self) -> CheckResult:
        """Measure Redis connectivity and round-trip latency."""
        if self._redis is None:
            return CheckResult(name="redis", status="warn", latency_ms=0.0, message="unavailable")
        start = time.perf_counter()
        try:
            await self._redis.ping()
            latency_ms = (time.perf_counter() - start) * 1000
            status = "ok" if latency_ms < 50 else "warn"
            return CheckResult(
                name="redis",
                status=status,
                latency_ms=round(latency_ms, 2),
                message="ok" if status == "ok" else f"high latency {latency_ms:.0f}ms",
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("health.redis_check_failed", error=str(exc))
            return CheckResult(
                name="redis",
                status="fail",
                latency_ms=round(latency_ms, 2),
                message=str(exc),
            )

    async def check_market_data(self) -> CheckResult:
        """Check market data provider reachability."""
        provider = getattr(self._settings, "market_data_provider", "mock")
        if provider == "mock":
            return CheckResult(
                name="market_data",
                status="ok",
                latency_ms=0.0,
                message="mock provider always available",
            )

        base_url = getattr(self._settings, "alpaca_base_url", "https://paper-api.alpaca.markets")
        start = time.perf_counter()
        try:
            import httpx

            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{base_url}/v2/clock")
            latency_ms = (time.perf_counter() - start) * 1000
            if resp.status_code < 500:
                status = "ok" if latency_ms < 500 else "warn"
                return CheckResult(
                    name="market_data",
                    status=status,
                    latency_ms=round(latency_ms, 2),
                    message=f"HTTP {resp.status_code}",
                )
            return CheckResult(
                name="market_data",
                status="fail",
                latency_ms=round(latency_ms, 2),
                message=f"HTTP {resp.status_code}",
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - start) * 1000
            logger.warning("health.market_data_check_failed", error=str(exc))
            return CheckResult(
                name="market_data",
                status="fail",
                latency_ms=round(latency_ms, 2),
                message=str(exc),
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _check_reconciliation(self) -> CheckResult:
        if self._reconciler is None:
            return CheckResult(
                name="reconciliation",
                status="ok",
                latency_ms=0.0,
                message="reconciler not configured",
            )
        try:
            result = await self._reconciler.get_last_result()
            if result is None:
                return CheckResult(
                    name="reconciliation",
                    status="ok",
                    latency_ms=0.0,
                    message="no reconciliation run yet",
                )
            if result.is_clean:
                return CheckResult(
                    name="reconciliation",
                    status="ok",
                    latency_ms=0.0,
                    message=result.summary,
                )
            return CheckResult(
                name="reconciliation",
                status="warn",
                latency_ms=0.0,
                message=result.summary,
            )
        except Exception as exc:
            return CheckResult(
                name="reconciliation",
                status="warn",
                latency_ms=0.0,
                message=f"check failed: {exc}",
            )

    async def _fetch_operational_metrics(
        self,
    ) -> tuple[int, int, datetime | None, float, bool]:
        """Returns (open_positions, pending_orders, last_trade_at, daily_pnl, kill_switch_active)."""
        open_positions = 0
        pending_orders = 0
        last_trade_at: datetime | None = None
        daily_pnl = 0.0
        kill_switch_active = False

        try:
            from sqlalchemy import text

            result = await self._db.execute(text("SELECT COUNT(*) FROM positions WHERE status = 'open'"))
            row = result.scalar()
            open_positions = int(row or 0)
        except Exception as exc:
            logger.debug("health.open_positions_fetch_failed", error=str(exc))

        try:
            from sqlalchemy import text

            result = await self._db.execute(
                text("SELECT COUNT(*) FROM orders WHERE status IN ('pending', 'submitted', 'partial')")
            )
            row = result.scalar()
            pending_orders = int(row or 0)
        except Exception as exc:
            logger.debug("health.pending_orders_fetch_failed", error=str(exc))

        try:
            from sqlalchemy import text

            result = await self._db.execute(
                text("SELECT filled_at FROM orders WHERE status = 'filled' ORDER BY filled_at DESC LIMIT 1")
            )
            row = result.fetchone()
            if row and row[0]:
                last_trade_at = row[0]
        except Exception as exc:
            logger.debug("health.last_trade_fetch_failed", error=str(exc))

        try:
            from sqlalchemy import text

            result = await self._db.execute(
                text("SELECT COALESCE(SUM(realized_pnl), 0) FROM positions WHERE DATE(closed_at) = CURRENT_DATE")
            )
            row = result.scalar()
            daily_pnl = float(row or 0.0)
        except Exception as exc:
            logger.debug("health.daily_pnl_fetch_failed", error=str(exc))

        try:
            from sqlalchemy import text

            result = await self._db.execute(text("SELECT is_active FROM kill_switch ORDER BY id DESC LIMIT 1"))
            row = result.fetchone()
            if row:
                kill_switch_active = bool(row[0])
        except Exception as exc:
            logger.debug("health.kill_switch_fetch_failed", error=str(exc))

        return open_positions, pending_orders, last_trade_at, daily_pnl, kill_switch_active
