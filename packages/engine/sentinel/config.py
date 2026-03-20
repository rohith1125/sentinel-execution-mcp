"""Application configuration via pydantic-settings."""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # app
    app_env: Literal["development", "paper", "live"] = "paper"
    log_level: str = "INFO"

    # database
    database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"
    database_pool_size: int = 10

    # redis
    redis_url: str = "redis://localhost:6379/0"
    redis_ttl_seconds: int = 300

    # market data
    market_data_provider: Literal["alpaca", "mock"] = "mock"
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_base_url: str = "https://paper-api.alpaca.markets"

    # risk limits (defaults are conservative)
    max_position_pct: float = 0.05  # 5% per position
    max_daily_drawdown_pct: float = 0.02  # 2% daily stop
    max_gross_exposure_pct: float = 0.80  # 80% max gross
    max_concurrent_positions: int = 10
    max_trade_risk_pct: float = 0.01  # 1% max risk per trade

    # execution
    paper_fill_latency_ms: int = 50
    slippage_bps: int = 5

    # server
    engine_host: str = "0.0.0.0"
    engine_port: int = 8100

    # Monitoring
    alert_webhook_url: str = ""  # Slack/Discord/custom webhook
    alert_webhook_enabled: bool = False
    reconciliation_interval_seconds: int = 60
    reconciliation_enabled: bool = True
    alert_quiet_hours_start: int = 22  # 10 PM local
    alert_quiet_hours_end: int = 8  # 8 AM local
    drawdown_warning_pct: float = 0.015  # warn at 1.5% (before 2% hard stop)

    # authentication
    sentinel_auth_enabled: bool = True
    sentinel_master_key: str = ""  # loaded from env, never logged
    sentinel_api_keys_json: str = ""  # JSON string of client configs
    rate_limit_per_minute: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


def configure_logging(settings: Settings) -> None:
    """Configure structlog based on environment."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.app_env == "development":
        processors = [
            *shared_processors,
            structlog.dev.ConsoleRenderer(),
        ]
    else:
        processors = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, settings.log_level)),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
