"""FastAPI router for backtest endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime

import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["backtest"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class BacktestRequest(BaseModel):
    strategy_name: str
    symbol: str
    start_date: date
    end_date: date
    initial_capital: float = 100_000.0
    risk_per_trade_pct: float = 0.01
    slippage_bps: int = 5


class BacktestResponse(BaseModel):
    strategy_name: str
    symbol: str
    total_trades: int
    win_rate: float
    profit_factor: float
    sharpe_ratio: float
    max_drawdown_pct: float
    net_profit: float
    avg_r_multiple: float
    ran_at: str  # ISO datetime
    result_id: str  # UUID key for fetching full result


class WalkForwardRequest(BaseModel):
    strategy_name: str
    symbol: str
    start_date: date
    end_date: date
    n_windows: int = 5


class WalkForwardResponse(BaseModel):
    strategy_name: str
    symbol: str
    n_windows: int
    consistency_ratio: float
    oos_win_rate: float
    oos_profit_factor: float
    is_robust: bool
    verdict: str
    recommendation: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_mock_bars(symbol: str, start_date: date, end_date: date):  # type: ignore[return]
    """Load bars from fixture file for mock/dev environments."""
    import json
    from decimal import Decimal
    from pathlib import Path

    from sentinel.market.provider import Bar

    fixture_path = Path(__file__).parent.parent.parent / "tests" / "fixtures" / "sample_bars_daily.json"
    if not fixture_path.exists():
        return []

    data = json.loads(fixture_path.read_text())
    bars = []
    for item in data.get("bars", []):
        ts = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        bar_date = ts.date()
        if bar_date < start_date or bar_date > end_date:
            continue
        bars.append(
            Bar(
                symbol=item["symbol"],
                timestamp=ts,
                open=Decimal(str(item["open"])),
                high=Decimal(str(item["high"])),
                low=Decimal(str(item["low"])),
                close=Decimal(str(item["close"])),
                volume=int(item["volume"]),
                vwap=Decimal(str(item["vwap"])) if item.get("vwap") is not None else None,
            )
        )
    return sorted(bars, key=lambda b: b.timestamp)


def _result_to_dict(result) -> dict:  # type: ignore[type-arg]
    """Serialize BacktestResult to a JSON-safe dict."""
    return {
        "strategy_name": result.config.strategy_name,
        "symbol": result.config.symbol,
        "total_trades": result.stats.total_trades,
        "win_rate": result.stats.win_rate,
        "profit_factor": result.stats.profit_factor if result.stats.profit_factor != float("inf") else 9999.0,
        "sharpe_ratio": result.stats.sharpe_ratio,
        "max_drawdown_pct": result.stats.max_drawdown_pct,
        "net_profit": float(result.stats.net_profit),
        "avg_r_multiple": result.stats.avg_r_multiple,
        "ran_at": result.ran_at.isoformat(),
        "trades": [
            {
                "symbol": t.symbol,
                "strategy": t.strategy,
                "entry_date": t.entry_date.isoformat(),
                "exit_date": t.exit_date.isoformat() if t.exit_date else None,
                "side": t.side,
                "entry_price": str(t.entry_price),
                "exit_price": str(t.exit_price) if t.exit_price else None,
                "shares": t.shares,
                "realized_pnl": str(t.realized_pnl),
                "pnl_pct": t.pnl_pct,
                "hold_bars": t.hold_bars,
                "exit_reason": t.exit_reason,
                "r_multiple": t.r_multiple,
            }
            for t in result.trades
        ],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/run", response_model=BacktestResponse)
async def run_backtest(body: BacktestRequest, request: Request) -> BacktestResponse:
    from decimal import Decimal

    from sentinel.backtest.engine import BacktestConfig, BacktestEngine
    from sentinel.regime.classifier import RegimeClassifier
    from sentinel.strategy.registry import registry

    strategy = registry.get(body.strategy_name)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{body.strategy_name}' not found in registry",
        )

    bars = _get_mock_bars(body.symbol, body.start_date, body.end_date)
    if not bars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No bars available for the requested symbol/date range",
        )

    config = BacktestConfig(
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        start_date=body.start_date,
        end_date=body.end_date,
        initial_capital=Decimal(str(body.initial_capital)),
        risk_per_trade_pct=body.risk_per_trade_pct,
        slippage_bps=body.slippage_bps,
    )

    engine = BacktestEngine(
        strategy=strategy,
        regime_classifier=RegimeClassifier(),
        config=config,
    )
    result = engine.run(bars)

    result_id = str(uuid.uuid4())

    # Store in Redis if available
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            payload = _result_to_dict(result)
            payload["result_id"] = result_id
            await redis.setex(
                f"sentinel:backtest:{result_id}",
                3600,
                json.dumps(payload),
            )
            # Maintain recent list
            await redis.lpush("sentinel:backtest:recent", result_id)
            await redis.ltrim("sentinel:backtest:recent", 0, 99)
        except Exception as exc:
            logger.warning("backtest.redis_store_failed", error=str(exc))

    pf = result.stats.profit_factor
    if pf == float("inf"):
        pf = 9999.0

    return BacktestResponse(
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        total_trades=result.stats.total_trades,
        win_rate=result.stats.win_rate,
        profit_factor=pf,
        sharpe_ratio=result.stats.sharpe_ratio,
        max_drawdown_pct=result.stats.max_drawdown_pct,
        net_profit=float(result.stats.net_profit),
        avg_r_multiple=result.stats.avg_r_multiple,
        ran_at=result.ran_at.isoformat(),
        result_id=result_id,
    )


@router.post("/walk-forward", response_model=WalkForwardResponse)
async def run_walk_forward(body: WalkForwardRequest, request: Request) -> WalkForwardResponse:
    from sentinel.backtest.walk_forward import WalkForwardValidator
    from sentinel.strategy.registry import registry

    strategy = registry.get(body.strategy_name)
    if strategy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy '{body.strategy_name}' not found in registry",
        )

    bars = _get_mock_bars(body.symbol, body.start_date, body.end_date)
    if not bars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No bars available for the requested symbol/date range",
        )

    validator = WalkForwardValidator()
    wf_result = validator.validate(strategy=strategy, bars=bars, n_windows=body.n_windows)

    return WalkForwardResponse(
        strategy_name=wf_result.strategy_name,
        symbol=wf_result.symbol,
        n_windows=len(wf_result.windows),
        consistency_ratio=wf_result.consistency_ratio,
        oos_win_rate=wf_result.oos_win_rate,
        oos_profit_factor=wf_result.oos_profit_factor if wf_result.oos_profit_factor != float("inf") else 9999.0,
        is_robust=wf_result.is_robust,
        verdict=wf_result.verdict,
        recommendation=wf_result.recommendation,
    )


@router.get("/results")
async def list_recent_results(request: Request) -> list[dict]:
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return []
    try:
        result_ids = await redis.lrange("sentinel:backtest:recent", 0, 9)
        summaries = []
        for rid in result_ids:
            raw = await redis.get(f"sentinel:backtest:{rid}")
            if raw:
                data = json.loads(raw)
                summaries.append(
                    {
                        "result_id": rid,
                        "strategy_name": data.get("strategy_name"),
                        "symbol": data.get("symbol"),
                        "total_trades": data.get("total_trades"),
                        "win_rate": data.get("win_rate"),
                        "net_profit": data.get("net_profit"),
                        "ran_at": data.get("ran_at"),
                    }
                )
        return summaries
    except Exception as exc:
        logger.warning("backtest.results_fetch_failed", error=str(exc))
        return []


@router.get("/strategies")
async def list_strategies() -> list[str]:
    from sentinel.strategy.registry import registry

    return registry.list_strategies()
