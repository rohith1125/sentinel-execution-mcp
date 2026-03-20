# Sentinel Execution MCP

![CI](https://github.com/rohith1125/sentinel-execution-mcp/actions/workflows/ci.yml/badge.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A production-grade algorithmic trading control plane exposed as an MCP server — so Claude can manage watchlists, classify market regimes, validate risk, and submit paper orders through natural language.**

---

## What It Is

Sentinel is a two-package monorepo:

| Package | Language | Role |
|---|---|---|
| `packages/engine` | Python 3.12 / FastAPI | All trading logic: risk checks, regime classification, order lifecycle, audit journal, strategy governance |
| `packages/mcp` | TypeScript / Node 20 | Thin MCP server that routes 40+ tools to the engine via HTTP. Zero trading logic lives here. |

Claude (or any MCP-compatible agent) talks to the MCP server. The MCP server talks to the engine. The engine owns the database and cache.

---

## Architecture

```
  Claude Desktop (or any MCP agent)
           │
           │  MCP protocol (stdio or SSE)
           ▼
  ┌─────────────────────────┐
  │   MCP Server            │  TypeScript · Zod validation · tool routing
  │   (packages/mcp)        │
  └────────────┬────────────┘
               │  HTTP REST (localhost:8100)
               ▼
  ┌─────────────────────────┐
  │   Engine API            │  Python · FastAPI · all trading logic
  │   (packages/engine)     │
  └──────────┬──────────────┘
             │
     ┌───────┴────────┐
     ▼                ▼
 PostgreSQL          Redis
 (orders,           (kill switch,
  positions,         rate limits,
  strategies,        cache)
  audit log)
```

If the engine is unavailable, every MCP tool call returns an error immediately. There is no fallback or partial execution.

---

## Prerequisites

| Dependency | Minimum version | Notes |
|---|---|---|
| Python | 3.12 | Engine runtime |
| Node.js | 20 | MCP server runtime |
| pnpm | latest | MCP package manager (`npm i -g pnpm`) |
| PostgreSQL | 15+ | Primary data store |
| Redis | 7+ | Kill switch and cache |

For local development, Docker is the easiest way to run Postgres and Redis:

```bash
docker compose -f docker/docker-compose.yml up -d db redis
```

---

## Quick Start

### 1. Clone and configure

```bash
git clone https://github.com/rohith1125/sentinel-execution-mcp.git
cd sentinel-execution-mcp
cp .env.example .env
# Default values work for local paper-trading development — no edits required
```

### 2. Set up the engine

```bash
cd packages/engine
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Run database migrations

```bash
# From packages/engine with the venv active
alembic upgrade head
```

### 4. Start the engine

```bash
uvicorn sentinel.api:app --reload --port 8100
```

Verify it is running:

```bash
curl http://localhost:8100/health
# {"status": "ok", "env": "development"}
```

### 5. Build and start the MCP server

Open a second terminal:

```bash
cd packages/mcp
pnpm install
pnpm build
pnpm dev        # stdio transport — for direct Claude Desktop integration
```

---

## Connect Claude Desktop

Add the following to your Claude Desktop configuration file.

**macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "sentinel": {
      "command": "node",
      "args": ["/absolute/path/to/sentinel-execution-mcp/packages/mcp/dist/index.js"],
      "env": {
        "ENGINE_BASE_URL": "http://localhost:8100",
        "APP_ENV": "paper"
      }
    }
  }
}
```

Replace `/absolute/path/to/sentinel-execution-mcp` with the actual path on your machine. Restart Claude Desktop after saving.

---

## MCP Tools Reference

Sentinel exposes **40+ tools** across nine categories. The MCP server name is `sentinel`.

| Category | Tool | Description |
|---|---|---|
| **Watchlist** | `watchlist.add` | Add symbols to the trading watchlist, optionally assigned to a group |
| | `watchlist.remove` | Remove symbols; they will no longer appear in strategy scans |
| | `watchlist.list` | List active symbols, optionally filtered by group |
| | `watchlist.get` | Get details for a single symbol |
| | `watchlist.groups` | List all named watchlist groups |
| | `watchlist.update` | Update notes or group assignment for a symbol |
| **Market Data** | `market.snapshot` | Latest quote and trade data for one or more symbols |
| | `market.bars` | OHLCV bar history with configurable timeframe |
| | `market.quote` | Real-time bid/ask spread for a symbol |
| | `market.health` | Check market data provider connectivity |
| **Regime** | `regime.evaluate` | Classify current market regime using ATR, ADX, RSI, Bollinger Width, Hurst Exponent, VWAP, and Price Efficiency |
| | `regime.history` | Retrieve historical regime snapshots for a symbol |
| **Strategy** | `strategy.scan` | Scan the watchlist for signals across one or more strategies |
| | `strategy.signal` | Evaluate a single symbol against a specific strategy |
| | `strategy.list` | List all registered strategies and their current state |
| **Risk / Kill Switch** | `risk.validate_trade` | Run all 13+ risk checks against a proposed trade before submission |
| | `risk.kill_switch_status` | Get the current state of all kill switches |
| | `risk.kill_switch_enable` | Enable a kill switch globally, per-strategy, or per-symbol |
| | `risk.kill_switch_disable` | Disable a kill switch (requires explicit reason) |
| | `risk.exposure` | Current gross and net exposure summary |
| | `risk.drawdown` | Current daily drawdown against configured limits |
| **Portfolio** | `portfolio.status` | Full account overview: value, cash, equity, P&L, buying power |
| | `portfolio.positions` | All open positions with unrealized P&L |
| | `portfolio.history` | Closed position history with realized P&L |
| **Execution** | `execution.paper_order` | Submit a paper trading order (market, limit, stop, stop-limit) |
| | `execution.cancel_order` | Cancel a pending or partially filled order by ID |
| | `execution.get_order` | Get the current state of a specific order |
| | `execution.list_orders` | List orders filtered by status, symbol, or date range |
| | `execution.reconcile` | Trigger a manual reconciliation between engine state and broker |
| **Governance** | `governance.create_strategy` | Register a new strategy in `draft` state |
| | `governance.promote_strategy` | Advance a strategy: Draft → Research → Backtest → Paper → Live |
| | `governance.suspend_strategy` | Suspend a live or paper strategy immediately |
| | `governance.list_strategies` | List all strategies with their current lifecycle state |
| | `governance.evaluate_promotion` | Check whether a strategy meets criteria for promotion |
| **Audit** | `audit.explain_trade` | Full human-readable explanation for a trade decision by audit event ID |
| | `audit.recent_events` | Most recent audit events, filterable by symbol or strategy |
| | `audit.trade_history` | Completed trade history with outcomes |
| | `audit.decision_log` | Raw decision log entries for a time window |
| | `audit.stats` | Aggregate statistics: win rate, average P&L, Sharpe proxy |
| | `audit.export` | Export audit records as CSV for a date range |

Full tool documentation with parameter schemas: [docs/mcp-tools.md](docs/mcp-tools.md)

---

## Environment Variables

### Engine (`packages/engine/.env`)

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `paper` | `development`, `paper`, or `live` |
| `DATABASE_URL` | `postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel` | PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `MARKET_DATA_PROVIDER` | `mock` | `mock` (no credentials needed) or `alpaca` |
| `ALPACA_API_KEY` | _(empty)_ | Required when `MARKET_DATA_PROVIDER=alpaca` |
| `ALPACA_API_SECRET` | _(empty)_ | Required when `MARKET_DATA_PROVIDER=alpaca` |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | Use `https://api.alpaca.markets` for live trading |
| `MAX_POSITION_PCT` | `0.05` | Maximum position size as a fraction of account equity (5%) |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.02` | Hard daily loss limit (2%); trading halts if breached |
| `MAX_GROSS_EXPOSURE_PCT` | `0.80` | Maximum gross exposure across all positions (80%) |
| `MAX_CONCURRENT_POSITIONS` | `10` | Maximum number of simultaneously open positions |
| `MAX_TRADE_RISK_PCT` | `0.01` | Maximum risk per individual trade (1%) |
| `PAPER_FILL_LATENCY_MS` | `50` | Simulated fill latency in paper trading mode |
| `SLIPPAGE_BPS` | `5` | Simulated slippage in basis points |
| `SENTINEL_AUTH_ENABLED` | `true` | Set to `false` for local development only |
| `SENTINEL_MASTER_KEY` | _(empty)_ | Generate with `python -m sentinel.auth.cli generate --name master --scopes admin` |
| `SENTINEL_API_KEYS_JSON` | _(empty)_ | JSON array of additional client key records |

### MCP Server (`packages/mcp/.env`)

| Variable | Default | Description |
|---|---|---|
| `ENGINE_BASE_URL` | `http://localhost:8100` | Base URL of the running engine service |

See `.env.example` at the repo root for the complete annotated reference.

---

## Example Workflow (Paper Trading)

```
# 1. Add symbols
watchlist.add(symbols=["NVDA", "MSFT", "AAPL"], group="tech")

# 2. Classify regime
regime.evaluate(symbol="NVDA", timeframe="1Day")

# 3. Scan for signals
strategy.scan(group="tech", strategy="momentum_v1")

# 4. Validate before submitting
risk.validate_trade(symbol="NVDA", side="buy", qty=10, order_type="market")

# 5. Submit paper order
execution.paper_order(symbol="NVDA", side="buy", qty=10, order_type="market")

# 6. Review portfolio
portfolio.status()

# 7. Inspect the audit trail
audit.recent_events(symbol="NVDA", limit=1)
audit.explain_trade(audit_event_id="evt-...")
```

---

## Running Tests

### Engine (Python)

```bash
cd packages/engine
source .venv/bin/activate
pytest tests/ -v
```

### MCP Server (TypeScript)

```bash
cd packages/mcp
pnpm test
```

### Full CI (lint + type check + test)

```bash
# From repo root
make check
```

---

## Repository Structure

```
sentinel-execution-mcp/
├── packages/
│   ├── engine/          # Python FastAPI trading engine
│   │   ├── sentinel/    # Application source
│   │   ├── tests/       # Pytest test suite
│   │   └── alembic/     # Database migrations
│   └── mcp/             # TypeScript MCP server
│       └── src/
│           └── tools/   # One file per tool category
├── docker/              # Dockerfiles and docker-compose
├── docs/                # Architecture, tool reference, risk model
├── scripts/             # Setup and reset helpers
└── .env.example         # Annotated environment variable reference
```

---

## Safety Disclaimer

This software is for **paper trading and research only** unless you fully understand every component. Setting `APP_ENV=live` with real Alpaca credentials will place real orders with real money. The hard-coded risk limits are conservative defaults — verify they match your own risk tolerance before use. The authors accept no liability for financial losses.

---

## License

MIT. See [LICENSE](LICENSE).
