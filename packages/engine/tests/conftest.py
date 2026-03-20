"""
Shared test fixtures for Sentinel Engine test suite.

All database tests use a fresh schema per test function — tables are created
at the start of each test and dropped at the end. This ensures full isolation
at the cost of some setup overhead (acceptable for correctness).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from sentinel.config import Settings
from sentinel.db.base import Base
from sentinel.market.mock import MockProvider
from sentinel.market.provider import Bar

TEST_DB_URL = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel_test"


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        app_env="development",
        database_url=TEST_DB_URL,
        redis_url="redis://localhost:6379/0",
        market_data_provider="mock",
        max_position_pct=0.05,
        max_daily_drawdown_pct=0.02,
        max_gross_exposure_pct=0.80,
        max_concurrent_positions=10,
    )


@pytest_asyncio.fixture(scope="function")
async def db_session(settings: Settings):
    """
    Creates a fresh database schema before each test, yields an AsyncSession,
    and tears down the schema afterwards. Guarantees complete test isolation.
    """
    engine = create_async_engine(settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def mock_provider() -> MockProvider:
    """Mock market data provider with deterministic output (seed=42, scenario=choppy)."""
    return MockProvider(scenario="choppy", seed=42)


@pytest.fixture
def bull_provider() -> MockProvider:
    """Mock provider configured for a bull trend scenario."""
    return MockProvider(scenario="bull_trend", seed=42)


@pytest.fixture
def bear_provider() -> MockProvider:
    """Mock provider configured for a bear trend scenario."""
    return MockProvider(scenario="bear_trend", seed=42)


def _make_bars(
    symbol: str,
    count: int,
    base_price: float,
    drift: float,
    volatility: float,
    volume_mult: float = 1.0,
    start_time: datetime | None = None,
) -> list[Bar]:
    """Generate a deterministic list of Bar objects for testing."""
    if start_time is None:
        # Default to market hours, well after open to avoid opening noise checks
        start_time = datetime(2024, 1, 15, 11, 0, 0)

    bars = []
    price = base_price
    for i in range(count):
        # Deterministic "random" using simple formula
        noise = (((i * 7919 + 13) % 100) - 50) / 1000  # small noise ±5%
        price = price * (1 + drift + noise * volatility)
        price = max(price, 0.01)

        high = price * (1 + abs(noise) * 0.5 + 0.002)
        low = price * (1 - abs(noise) * 0.5 - 0.002)
        open_p = price * (1 + noise * 0.1)

        vol = int(500_000 * volume_mult * (1 + abs(noise)))

        bars.append(
            Bar(
                symbol=symbol,
                timestamp=start_time + timedelta(minutes=i),
                open=Decimal(str(round(open_p, 2))),
                high=Decimal(str(round(high, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(price, 2))),
                volume=max(100, vol),
                vwap=Decimal(str(round((high + low + price) / 3, 4))),
            )
        )
    return bars


@pytest.fixture
def trending_bull_bars() -> list[Bar]:
    """
    50 bars in a strong uptrend: consistent positive drift with low noise.
    EMA alignment should be bullish; ADX will be elevated.
    """
    return _make_bars(
        symbol="AAPL",
        count=50,
        base_price=180.0,
        drift=0.0015,
        volatility=0.3,
        volume_mult=1.5,
        start_time=datetime(2024, 1, 15, 11, 0, 0),
    )


@pytest.fixture
def choppy_bars() -> list[Bar]:
    """
    50 bars with mean-reverting characteristics: near-zero drift, higher noise.
    Designed so Hurst exponent will tend toward < 0.5.
    """
    # Alternating positive/negative pattern for anti-persistence
    bars = []
    price = 180.0
    start_time = datetime(2024, 1, 15, 11, 0, 0)
    for i in range(50):
        # Alternating directions
        direction = 1 if i % 2 == 0 else -1
        move = direction * 0.003 + (((i * 3) % 7) - 3) * 0.001
        price = price * (1 + move)
        price = max(price, 0.01)
        high = price * 1.004
        low = price * 0.996
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=start_time + timedelta(minutes=i),
                open=Decimal(str(round(price * 0.999, 2))),
                high=Decimal(str(round(high, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(price, 2))),
                volume=max(100, 400_000),
                vwap=Decimal(str(round((high + low + price) / 3, 4))),
            )
        )
    return bars


@pytest.fixture
def high_vol_bars() -> list[Bar]:
    """
    50 bars with elevated volatility — ATR% will exceed the 4% threshold,
    triggering HIGH_VOL_UNSTABLE classification.
    """
    bars = []
    price = 180.0
    start_time = datetime(2024, 1, 15, 11, 0, 0)
    for i in range(50):
        noise = (((i * 11 + 7) % 100) - 50) / 100  # large swings
        price = price * (1 + noise * 0.10)  # ±10% moves
        price = max(price, 0.01)
        high = price * 1.06
        low = price * 0.94
        bars.append(
            Bar(
                symbol="AAPL",
                timestamp=start_time + timedelta(minutes=i),
                open=Decimal(str(round(price * 1.01, 2))),
                high=Decimal(str(round(high, 2))),
                low=Decimal(str(round(low, 2))),
                close=Decimal(str(round(price, 2))),
                volume=max(100, 800_000),
                vwap=Decimal(str(round((high + low + price) / 3, 4))),
            )
        )
    return bars
