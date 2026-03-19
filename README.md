# Sentinel Execution MCP

**A production-grade trading control plane exposed as an MCP server.**

[![CI](https://github.com/your-org/sentinel-execution-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/your-org/sentinel-execution-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## What This Is

Sentinel is a trading control plane designed to be operated by an AI agent via the Model Context Protocol. It handles the machinery of trading — market data, regime classification, risk checks, order management, and audit logging — and exposes everything as typed MCP tools that an agent can call.

The system is built around one core idea: **the engine decides, the agent directs**. The agent calls tools to express intent (`strategy.scan_watchlist`, `execution.paper_order`). The engine enforces constraints (`check_kill_switch`, `check_daily_drawdown`, `check_liquidity_threshold`). Neither side can bypass the other.

This is not a signals library or a backtesting framework. It's the operational layer that sits between a strategy signal and a live broker: risk firewall, position management, governance workflows, and a complete audit trail.

The default configuration runs entirely in paper trading mode with mock market data. You can evaluate the full system without any broker account or API keys.

---

## Key Capabilities

- **Risk-first architecture**: 13+ risk check functions, all pure and independently testable. Hard blocks cannot be bypassed. Kill switch with global, per-strategy, and per-symbol granularity.
- **Regime classification**: Technical indicator suite (ATR, ADX, RSI, Bollinger Width, Hurst Exponent, VWAP, Price Efficiency) classifies market conditions before strategy signals are evaluated.
- **Strategy governance**: Draft → Research → Backtest → Paper → Live promotion lifecycle with configurable criteria. Paper-to-live transition requires explicit human sign-off.
- **Full audit trail**: Every trade decision — approved or rejected — is written to an append-only audit journal with full explanation of which checks passed and which blocked.
- **Paper trading simulation**: Deterministic fill simulation with configurable latency and slippage. Full order lifecycle: pending → submitted → filled/cancelled/rejected.
- **MCP tool coverage**: 40+ tools across watchlist, market, regime, strategy, risk, portfolio, execution, governance, and audit categories.
- **Dual transport**: stdio (for direct Claude integration) and SSE (for HTTP-based agent frameworks).

---

## Architecture

```
AI Agent (Claude, etc.)
        │ MCP (stdio / SSE)
        ▼
MCP Server (TypeScript)        ← Zod validation, tool routing, formatting
        │ HTTP REST
        ▼
Engine Service (Python)        ← All trading logic, state management
        │                 │
  PostgreSQL            Redis
  (orders, positions,   (kill switch, cache)
   strategies, audit)
```

The MCP server is a thin routing layer. Zero trading logic lives there. If the engine is unavailable, every tool call returns an error immediately.

---

## Quick Start

### Prerequisites

- Python 3.12+, Node.js 20+, pnpm 9+
- Docker and Docker Compose

### Setup

```bash
# 1. Clone and configure
git clone <repo>
cd sentinel-execution-mcp
cp .env.example .env
# Defaults work for local development — no changes needed

# 2. Start PostgreSQL and Redis
docker-compose -f docker/docker-compose.yml up -d db redis

# 3. Install dependencies
make install

# 4. Run migrations
make migrate

# 5. Start the engine (in one terminal)
cd packages/engine
uvicorn sentinel.api:app --reload --port 8100

# 6. Start the MCP server (in another terminal)
cd packages/mcp
pnpm dev
```

Verify the engine is running:
```bash
curl http://localhost:8100/health
# → {"status": "ok", "env": "development"}
```

---

## Paper Trading Walkthrough

This walkthrough uses the default mock market data provider — no broker account needed.

### 1. Populate the watchlist

```
watchlist.add(symbols=["AAPL", "MSFT", "NVDA"], group="tech")
```

### 2. Check market regime

```
regime.evaluate(symbol="SPY")
# → label: "trending_bull", tradeability_score: 0.80
```

### 3. Scan for signals

```
strategy.scan_watchlist(group="tech", strategies=["momentum_breakout"])
```

### 4. Validate a trade before submitting

```
risk.validate_trade(symbol="NVDA", side="buy", quantity=10, order_type="market")
# → passed: true, blocking_checks: [], warning_checks: []
```

### 5. Submit a paper order

```
execution.paper_order(symbol="NVDA", side="buy", order_type="market", quantity=10)
# → order_id: "ord-...", status: "filled", filled_avg_price: 876.25
```

### 6. Review the portfolio

```
portfolio.status()
# → cash, positions, unrealized_pnl, gross_exposure
```

### 7. Audit the decision

```
audit.recent_events(symbol="NVDA", limit=1)
# → copy the audit_event_id

audit.explain_trade(audit_event_id="evt-...")
# → Full explanation of every risk check that ran
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `paper` | `development`, `paper`, or `live` |
| `MARKET_DATA_PROVIDER` | `mock` | `mock` or `alpaca` |
| `DATABASE_URL` | `postgresql+asyncpg://...` | PostgreSQL connection |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection |
| `ALPACA_API_KEY` | `` | Required when provider=alpaca |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | Alpaca endpoint |
| `MAX_POSITION_PCT` | `0.05` | Max position size (5% of account) |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.02` | Daily loss limit (2%) |
| `MAX_CONCURRENT_POSITIONS` | `10` | Position count limit |
| `ENGINE_BASE_URL` | `http://localhost:8100` | Engine URL (from MCP server) |

See `.env.example` for the complete list.

---

## MCP Tool Categories

| Category | Tools | Purpose |
|---|---|---|
| `watchlist.*` | 6 tools | Symbol management and organization |
| `market.*` | 4 tools | Price data, quotes, health check |
| `regime.*` | 2 tools | Market regime classification |
| `strategy.*` | 3 tools | Signal scanning across strategies |
| `risk.*` | 6 tools | Risk validation and kill switches |
| `portfolio.*` | 3 tools | Positions and account state |
| `execution.*` | 5 tools | Order submission and management |
| `governance.*` | 5 tools | Strategy promotion lifecycle |
| `audit.*` | 6 tools | Trade history and explanations |

Full tool reference: [docs/mcp-tools.md](docs/mcp-tools.md)

---

## Testing

```bash
# Run all tests (requires Docker for DB/Redis)
make test

# Python unit tests only (no database required)
cd packages/engine && pytest tests/unit/ -v

# Python integration tests (requires DB/Redis)
cd packages/engine && pytest tests/integration/ -v

# TypeScript tests
cd packages/mcp && pnpm test
```

Test coverage targets: engine >= 80%, MCP >= 70%.

See [docs/developer-guide.md](docs/developer-guide.md) for details on writing new tests.

---

## Safety Disclaimer

**This software can route real orders to a live brokerage account. Use it incorrectly, and you will lose real money.**

Specific things that can go wrong:

- Setting `APP_ENV=live` with a funded account and misconfigured risk limits can result in unexpected positions or losses before you notice.
- The `execution.flatten_all` tool closes all open positions at market. In a fast-moving or illiquid market, market orders may fill significantly away from the last quoted price.
- Paper trading results do not predict live trading results. Fill simulation is simplified. Real markets have queue position, partial fills, and impact.
- The system does not have a circuit breaker against bugs in strategy code. If a strategy generates erroneous signals at high frequency, risk checks may not catch all of them before significant exposure accumulates.
- Kill switch state persists in Redis. If Redis fails and you restart without restoring state, a previously-engaged global halt will be cleared.
- This software is provided as-is with no warranty. The authors are not responsible for trading losses incurred through use of this system.

Before using this with real money:
1. Run exclusively in paper mode for at least 30 days.
2. Review every risk parameter and understand why each limit exists.
3. Test the kill switch in paper mode so you know it works when you need it.
4. Have a manual process for closing positions that does not depend on this software.

---

## Roadmap

Honest assessment of what is missing for a complete production system:

- **Live broker integration**: Alpaca adapter exists; needs thorough testing against live API behavior (partial fills, order amendments, stream reconnect).
- **Position P&L streaming**: Current implementation polls. Real-time P&L via WebSocket would reduce latency.
- **Multi-account support**: The current model assumes a single account. Multi-account allocation is not implemented.
- **Strategy backtesting integration**: Backtest results must be imported externally. Native backtesting is out of scope but an import endpoint is planned.
- **Alerting**: No built-in alerting when drawdown limits are approached or positions are stuck. Needs integration with a notification system.
- **Web UI**: All interaction is via MCP tools or raw API. A monitoring dashboard would improve operational visibility.

---

## License

MIT. See [LICENSE](LICENSE).
