"""
Alert routing: delivers operational alerts via configured channels.

Supports:
- Webhook (Slack, Discord, custom HTTP POST)
- Log-only (always active, structured JSON)
- Email via SMTP (optional)

Alert levels: INFO, WARNING, CRITICAL
Critical alerts always fire regardless of quiet hours.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_DEDUP_TTL_SECONDS = 300  # 5 minutes


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    level: AlertLevel
    title: str
    message: str
    context: dict[str, Any]
    source: str
    fired_at: datetime
    alert_id: str  # UUID for deduplication


class AlertService:
    """
    Routes alerts to configured channels. Always logs alerts regardless of other config.

    Deduplication: same (source, title) within 5 minutes = same alert, don't re-fire.
    Quiet hours: WARNING alerts suppressed between configurable hours, CRITICAL always fires.
    """

    _RECENT_ALERTS_KEY = "sentinel:alerts:recent"
    _RECENT_ALERTS_LIMIT = 100

    def __init__(self, settings: Any, redis: Any = None) -> None:
        self._settings = settings
        self._redis = redis

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fire(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        source: str,
        context: dict[str, Any] | None = None,
    ) -> Alert:
        """Fire an alert. Returns the Alert record."""
        ctx = context or {}
        alert = Alert(
            level=level,
            title=title,
            message=message,
            context=ctx,
            source=source,
            fired_at=datetime.now(tz=UTC),
            alert_id=str(uuid.uuid4()),
        )

        # Always log
        await self._deliver_log(alert)

        # Dedup check (skip for CRITICAL)
        if level != AlertLevel.CRITICAL:
            if await self._is_duplicate(alert):
                logger.debug("alert.deduplicated", title=title, source=source)
                return alert

        # Quiet hours check (only suppresses WARNING)
        if level == AlertLevel.WARNING and self._in_quiet_hours():
            logger.debug("alert.suppressed_quiet_hours", title=title)
            return alert

        # Store in Redis recent list
        await self._store_recent(alert)

        # Mark dedup key
        await self._mark_dedup(alert)

        # Webhook delivery (fire-and-forget)
        if getattr(self._settings, "alert_webhook_enabled", False):
            import asyncio
            asyncio.create_task(self._deliver_webhook(alert))

        return alert

    async def fire_critical(
        self,
        title: str,
        message: str,
        source: str,
        context: dict[str, Any] | None = None,
    ) -> Alert:
        """Convenience: fire a CRITICAL alert."""
        return await self.fire(
            level=AlertLevel.CRITICAL,
            title=title,
            message=message,
            source=source,
            context=context,
        )

    async def _deliver_webhook(self, alert: Alert) -> None:
        """POST alert to configured webhook URL. Non-blocking — failures logged but not raised."""
        url = getattr(self._settings, "alert_webhook_url", "")
        if not url:
            return
        try:
            import httpx

            payload = {
                "alert_id": alert.alert_id,
                "level": alert.level.value,
                "title": alert.title,
                "message": alert.message,
                "source": alert.source,
                "fired_at": alert.fired_at.isoformat(),
                "context": alert.context,
            }
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code >= 400:
                    logger.warning(
                        "alert.webhook_error",
                        status=resp.status_code,
                        alert_id=alert.alert_id,
                    )
                else:
                    logger.debug("alert.webhook_delivered", alert_id=alert.alert_id)
        except Exception as exc:
            logger.warning("alert.webhook_failed", error=str(exc), alert_id=alert.alert_id)

    async def _deliver_log(self, alert: Alert) -> None:
        """Always fires. Structured JSON log at appropriate level."""
        log_fn = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.CRITICAL: logger.error,
        }.get(alert.level, logger.info)

        log_fn(
            "alert.fired",
            alert_id=alert.alert_id,
            level=alert.level.value,
            title=alert.title,
            message=alert.message,
            source=alert.source,
            context=alert.context,
        )

    async def get_recent_alerts(self, limit: int = 20) -> list[Alert]:
        """Get recent alerts from Redis cache."""
        if self._redis is None:
            return []
        try:
            raw_list = await self._redis.lrange(self._RECENT_ALERTS_KEY, 0, limit - 1)
            alerts: list[Alert] = []
            for raw in raw_list:
                try:
                    data = json.loads(raw)
                    alerts.append(
                        Alert(
                            level=AlertLevel(data["level"]),
                            title=data["title"],
                            message=data["message"],
                            context=data.get("context", {}),
                            source=data["source"],
                            fired_at=datetime.fromisoformat(data["fired_at"]),
                            alert_id=data["alert_id"],
                        )
                    )
                except Exception:
                    continue
            return alerts
        except Exception as exc:
            logger.warning("alert.redis_fetch_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dedup_key(self, alert: Alert) -> str:
        return f"sentinel:alert:dedup:{alert.source}:{alert.title}"

    async def _is_duplicate(self, alert: Alert) -> bool:
        if self._redis is None:
            return False
        try:
            key = self._dedup_key(alert)
            val = await self._redis.get(key)
            return val is not None
        except Exception:
            return False

    async def _mark_dedup(self, alert: Alert) -> None:
        if self._redis is None:
            return
        try:
            key = self._dedup_key(alert)
            await self._redis.set(key, "1", ex=_DEDUP_TTL_SECONDS)
        except Exception as exc:
            logger.debug("alert.dedup_mark_failed", error=str(exc))

    async def _store_recent(self, alert: Alert) -> None:
        if self._redis is None:
            return
        try:
            payload = json.dumps({
                "alert_id": alert.alert_id,
                "level": alert.level.value,
                "title": alert.title,
                "message": alert.message,
                "context": alert.context,
                "source": alert.source,
                "fired_at": alert.fired_at.isoformat(),
            })
            await self._redis.lpush(self._RECENT_ALERTS_KEY, payload)
            await self._redis.ltrim(self._RECENT_ALERTS_KEY, 0, self._RECENT_ALERTS_LIMIT - 1)
        except Exception as exc:
            logger.debug("alert.redis_store_failed", error=str(exc))

    def _in_quiet_hours(self) -> bool:
        start: int = getattr(self._settings, "alert_quiet_hours_start", 22)
        end: int = getattr(self._settings, "alert_quiet_hours_end", 8)
        now_hour = datetime.now().hour
        if start > end:
            # crosses midnight: quiet from start..23 and 0..end
            return now_hour >= start or now_hour < end
        return start <= now_hour < end


# ---------------------------------------------------------------------------
# Pre-defined alert templates
# ---------------------------------------------------------------------------

def alert_global_halt_engaged(reason: str, operator: str) -> dict[str, Any]:
    return {
        "title": "Global Halt Engaged",
        "message": f"Trading halted by {operator}. Reason: {reason}",
        "context": {"reason": reason, "operator": operator},
    }


def alert_daily_drawdown_approaching(current_pct: float, limit_pct: float) -> dict[str, Any]:
    return {
        "title": "Daily Drawdown Warning",
        "message": (
            f"Current drawdown {current_pct:.2%} is approaching the {limit_pct:.2%} hard limit."
        ),
        "context": {"current_pct": current_pct, "limit_pct": limit_pct},
    }


def alert_position_reconciliation_failed(discrepancies: list[Any]) -> dict[str, Any]:
    symbols = [getattr(d, "symbol", str(d)) for d in discrepancies]
    return {
        "title": "Position Reconciliation Failed",
        "message": f"{len(discrepancies)} critical discrepancy(ies) detected: {', '.join(symbols)}",
        "context": {"count": len(discrepancies), "symbols": symbols},
    }


def alert_strategy_drift_detected(strategy: str, signals: list[str]) -> dict[str, Any]:
    return {
        "title": f"Strategy Drift: {strategy}",
        "message": f"Unexpected signals detected for strategy {strategy}: {', '.join(signals)}",
        "context": {"strategy": strategy, "signals": signals},
    }


def alert_fill_quality_degraded(strategy: str, avg_slippage_bps: float) -> dict[str, Any]:
    return {
        "title": f"Fill Quality Degraded: {strategy}",
        "message": f"Average slippage for {strategy} is {avg_slippage_bps:.1f} bps.",
        "context": {"strategy": strategy, "avg_slippage_bps": avg_slippage_bps},
    }


def alert_engine_startup(env: str) -> dict[str, Any]:
    return {
        "title": "Engine Started",
        "message": f"Sentinel execution engine started in {env} environment.",
        "context": {"env": env},
    }
