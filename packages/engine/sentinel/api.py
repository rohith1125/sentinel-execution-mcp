"""FastAPI application factory for the Sentinel engine."""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from sentinel.auth.middleware import get_current_client, require_scope
from sentinel.auth.models import APIClient
from sentinel.auth.rate_limiter import RateLimiter
from sentinel.auth.service import APIKeyService, get_key_service
from sentinel.config import Settings, configure_logging, get_settings
from sentinel.db.base import create_all_tables, dispose_engine, get_engine, get_session_factory
from sentinel.watchlist.router import router as watchlist_router
from sentinel.market.router import router as market_router
from sentinel.regime.router import router as regime_router
from sentinel.strategy.router import router as strategy_router
from sentinel.risk.router import router as risk_router
from sentinel.execution.router import router as execution_router
from sentinel.execution.portfolio_router import router as portfolio_router
from sentinel.governance.router import router as governance_router
from sentinel.audit.router import router as audit_router
from sentinel.monitoring.router import router as monitoring_router
from sentinel.backtest.router import router as backtest_router

logger = structlog.get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = get_settings()

    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]
        # startup
        logger.info(
            "engine.startup",
            env=settings.app_env,
            provider=settings.market_data_provider,
        )
        # initialise DB pool (touch the engine)
        get_engine(settings)
        get_session_factory(settings)

        # initialise Redis client and store on app.state
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            await redis_client.ping()
            app.state.redis = redis_client
            logger.info("engine.redis_connected", url=settings.redis_url)
        except Exception as exc:
            logger.warning("engine.redis_unavailable", error=str(exc))
            app.state.redis = None

        yield

        # shutdown
        if getattr(app.state, "redis", None) is not None:
            await app.state.redis.aclose()
        await dispose_engine()
        logger.info("engine.shutdown")

    app = FastAPI(
        title="Sentinel Execution Engine",
        version="0.1.0",
        description="Production-grade trading control plane",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ---------------------------------------------------------------------------
    # Rate-limiter instance (shared across requests)
    # ---------------------------------------------------------------------------
    _rate_limiter = RateLimiter()

    # ---------------------------------------------------------------------------
    # Middleware
    # ---------------------------------------------------------------------------

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next: Any) -> Any:
        # Skip rate limiting for unauthenticated ops endpoints
        if request.url.path in ("/health", "/ready", "/docs", "/redoc", "/openapi.json"):
            return await call_next(request)
        # Only rate-limit if a client_id is attached (set by auth dependency or bypass)
        client_id = request.headers.get("X-API-Key", "anonymous")
        redis = getattr(request.app.state, "redis", None)
        allowed, remaining = await _rate_limiter.check_and_increment(
            client_id, settings.rate_limit_per_minute, redis
        )
        if not allowed:
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": "rate_limit_exceeded"},
                headers={"X-RateLimit-Remaining": "0"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

    @app.middleware("http")
    async def structured_logging_middleware(request: Request, call_next: Any) -> Any:
        start = time.perf_counter()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            method=request.method,
            path=request.url.path,
        )
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "http.request",
            status_code=response.status_code,
            elapsed_ms=round(elapsed_ms, 2),
        )
        return response

    # ---------------------------------------------------------------------------
    # Exception handlers
    # ---------------------------------------------------------------------------

    @app.exception_handler(404)
    async def not_found_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "not_found", "path": request.url.path},
        )

    @app.exception_handler(422)
    async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"error": "validation_error", "detail": str(exc)},
        )

    @app.exception_handler(500)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("engine.unhandled_error", exc_info=exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_server_error"},
        )

    # ---------------------------------------------------------------------------
    # Core endpoints
    # ---------------------------------------------------------------------------

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, object]:
        return {
            "status": "healthy",
            "env": settings.app_env,
            "ts": datetime.utcnow().isoformat(),
        }

    @app.get("/ready", tags=["ops"])
    async def ready(request: Request) -> JSONResponse:
        checks: dict[str, str] = {}
        all_ok = True

        # DB check
        try:
            from sqlalchemy import text

            factory = get_session_factory(settings)
            async with factory() as session:
                await session.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as exc:
            checks["db"] = f"error: {exc}"
            all_ok = False

        # Redis check
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            try:
                await redis.ping()
                checks["redis"] = "ok"
            except Exception as exc:
                checks["redis"] = f"error: {exc}"
                all_ok = False
        else:
            checks["redis"] = "unavailable"

        http_status = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return JSONResponse(
            status_code=http_status,
            content={"ready": all_ok, "checks": checks},
        )

    # ---------------------------------------------------------------------------
    # Auth endpoints
    # ---------------------------------------------------------------------------

    @app.post("/auth/keys/generate", tags=["auth"])
    async def generate_api_key(
        name: str,
        scopes: str = "read",
        rate_limit: int = 60,
        _client: APIClient = Depends(require_scope("admin")),
        key_service: APIKeyService = Depends(get_key_service),
    ) -> dict[str, object]:
        """Generate a new API key. Admin scope required. Raw key shown only once."""
        raw_key, hashed_key = key_service.generate_key()
        scope_list = [s.strip() for s in scopes.split(",") if s.strip()]
        logger.info("auth.key_generated", name=name, scopes=scope_list)
        return {
            "raw_key": raw_key,
            "hashed_key": hashed_key,
            "name": name,
            "scopes": scope_list,
            "rate_limit_per_minute": rate_limit,
            "warning": "Store the raw_key securely. It will not be shown again.",
        }

    # ---------------------------------------------------------------------------
    # Routers — inject DB session dependency properly
    # ---------------------------------------------------------------------------

    from functools import partial

    from sqlalchemy.ext.asyncio import AsyncSession

    async def db_session() -> Any:  # type: ignore[return]
        factory = get_session_factory(settings)
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.include_router(watchlist_router)
    app.include_router(market_router)
    app.include_router(regime_router)
    app.include_router(strategy_router)
    app.include_router(risk_router)
    app.include_router(execution_router)
    app.include_router(portfolio_router)
    app.include_router(governance_router)
    app.include_router(audit_router)
    app.include_router(monitoring_router, prefix="/monitoring", tags=["monitoring"])
    app.include_router(backtest_router, prefix="/backtest", tags=["backtest"])

    # Override DB session dependency for all routers that use `db_session_placeholder`
    from sentinel.db.base import db_session_placeholder
    app.dependency_overrides[db_session_placeholder] = db_session

    logger.info("engine.routes_mounted", routers=10)

    return app


def main() -> None:
    import uvicorn

    settings = get_settings()
    app = create_app(settings)
    uvicorn.run(
        app,
        host=settings.engine_host,
        port=settings.engine_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()


# Module-level app instance for uvicorn (e.g. `uvicorn sentinel.api:app`)
app = create_app()
