"""FastAPI endpoints for monitoring: health, reconciliation, alerts, metrics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["monitoring"])


def _get_redis(request: Request) -> Any:
    return getattr(request.app.state, "redis", None)


# ---------------------------------------------------------------------------
# GET /monitoring/health/full
# ---------------------------------------------------------------------------

@router.get("/health/full")
async def full_health(request: Request) -> JSONResponse:
    """Full SystemHealth JSON. Requires authenticated context (API key via header)."""
    from sentinel.config import get_settings
    from sentinel.db.base import get_session_factory
    from sentinel.monitoring.health import HealthMonitor

    settings = get_settings()
    redis = _get_redis(request)

    factory = get_session_factory(settings)
    async with factory() as session:
        monitor = HealthMonitor(
            db_session=session,
            redis=redis,
            settings=settings,
        )
        health = await monitor.check_all()

    return JSONResponse(
        status_code=status.HTTP_200_OK if health.status != "unhealthy" else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=_serialize_health(health),
    )


# ---------------------------------------------------------------------------
# GET /monitoring/reconciliation/latest
# ---------------------------------------------------------------------------

@router.get("/reconciliation/latest")
async def reconciliation_latest(request: Request) -> JSONResponse:
    """Return the last reconciliation result from cache."""
    recon = getattr(request.app.state, "reconciler", None)
    if recon is None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "reconciler not initialised", "result": None},
        )
    result = await recon.get_last_result()
    if result is None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "no reconciliation run yet", "result": None},
        )
    return JSONResponse(content=_serialize_recon(result))


# ---------------------------------------------------------------------------
# POST /monitoring/reconciliation/run
# ---------------------------------------------------------------------------

@router.post("/reconciliation/run")
async def reconciliation_run(request: Request) -> JSONResponse:
    """Trigger an immediate reconciliation pass."""
    recon = getattr(request.app.state, "reconciler", None)
    if recon is None:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"message": "reconciler not configured", "is_clean": None, "discrepancies": []},
        )
    result = await recon.reconcile()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=_serialize_recon(result),
    )


# ---------------------------------------------------------------------------
# GET /monitoring/alerts/recent
# ---------------------------------------------------------------------------

@router.get("/alerts/recent")
async def alerts_recent(request: Request, limit: int = 20) -> JSONResponse:
    """Return recent alerts from Redis cache."""
    from sentinel.config import get_settings
    from sentinel.monitoring.alerts import AlertService

    settings = get_settings()
    redis = _get_redis(request)
    svc = AlertService(settings=settings, redis=redis)
    alerts = await svc.get_recent_alerts(limit=limit)
    return JSONResponse(
        content={
            "alerts": [
                {
                    "alert_id": a.alert_id,
                    "level": a.level.value,
                    "title": a.title,
                    "message": a.message,
                    "source": a.source,
                    "fired_at": a.fired_at.isoformat(),
                    "context": a.context,
                }
                for a in alerts
            ],
            "count": len(alerts),
        }
    )


# ---------------------------------------------------------------------------
# GET /monitoring/metrics
# ---------------------------------------------------------------------------

@router.get("/metrics")
async def metrics(request: Request) -> dict[str, Any]:
    """Simple metrics dict for dashboards."""
    from sentinel.config import get_settings
    from sentinel.db.base import get_session_factory
    from sentinel.monitoring.health import HealthMonitor

    settings = get_settings()
    redis = _get_redis(request)

    factory = get_session_factory(settings)
    async with factory() as session:
        monitor = HealthMonitor(db_session=session, redis=redis, settings=settings)
        health = await monitor.check_all()

    return {
        "status": health.status,
        "open_positions": health.open_positions,
        "pending_orders": health.pending_orders,
        "daily_pnl": health.daily_pnl,
        "kill_switch_active": health.kill_switch_active,
        "uptime_seconds": round(health.uptime_seconds, 1),
        "checks": {
            name: {"status": c.status, "latency_ms": c.latency_ms}
            for name, c in health.checks.items()
        },
        "ts": datetime.now(tz=timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_health(health: Any) -> dict[str, Any]:
    from sentinel.monitoring.health import SystemHealth
    return {
        "status": health.status,
        "uptime_seconds": round(health.uptime_seconds, 1),
        "last_trade_at": health.last_trade_at.isoformat() if health.last_trade_at else None,
        "open_positions": health.open_positions,
        "pending_orders": health.pending_orders,
        "daily_pnl": health.daily_pnl,
        "kill_switch_active": health.kill_switch_active,
        "checked_at": health.checked_at.isoformat(),
        "checks": {
            name: {
                "name": c.name,
                "status": c.status,
                "latency_ms": c.latency_ms,
                "message": c.message,
            }
            for name, c in health.checks.items()
        },
    }


def _serialize_recon(result: Any) -> dict[str, Any]:
    return {
        "reconciled_at": result.reconciled_at.isoformat(),
        "total_positions_checked": result.total_positions_checked,
        "is_clean": result.is_clean,
        "summary": result.summary,
        "discrepancies": [
            {
                "symbol": d.symbol,
                "discrepancy_type": d.discrepancy_type,
                "db_quantity": d.db_quantity,
                "broker_quantity": d.broker_quantity,
                "db_side": d.db_side,
                "broker_side": d.broker_side,
                "detected_at": d.detected_at.isoformat(),
                "severity": d.severity,
            }
            for d in result.discrepancies
        ],
    }
