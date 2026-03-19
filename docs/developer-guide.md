# Developer Guide

## Project Structure

```
sentinel-execution-mcp/
├── packages/
│   ├── engine/                     # Python FastAPI service
│   │   ├── sentinel/
│   │   │   ├── api.py              # FastAPI app, router assembly
│   │   │   ├── config.py           # Settings (pydantic-settings)
│   │   │   ├── db/                 # SQLAlchemy models, migrations
│   │   │   ├── domain/             # Types, enums, value objects
│   │   │   ├── execution/          # BrokerAdapter protocol + implementations
│   │   │   ├── market/             # Market data provider protocol + implementations
│   │   │   ├── regime/             # Regime classifier + indicators
│   │   │   ├── risk/               # Risk check functions + firewall
│   │   │   ├── strategy/           # Strategy base + implementations
│   │   │   └── watchlist/          # Watchlist service + router
│   │   └── tests/
│   │       ├── unit/               # Pure function tests (no DB required)
│   │       ├── integration/        # DB-backed service tests
│   │       └── fixtures/           # JSON fixture data
│   └── mcp/                        # TypeScript MCP server
│       └── src/
│           ├── index.ts            # Entry point, server initialization
│           ├── server.ts           # MCP server creation
│           ├── engine-client.ts    # HTTP client for engine API
│           ├── config.ts           # Configuration (env vars)
│           ├── logger.ts           # Pino logger setup
│           ├── tools/              # Tool registrations by category
│           └── types/              # TypeScript types mirroring engine models
```

## Running Tests

### Python tests (unit only — no database required)

```bash
cd packages/engine
pytest tests/unit/ -v
```

### Python tests (all — requires PostgreSQL and Redis)

```bash
# Start test infrastructure
docker-compose -f docker/docker-compose.yml up -d db redis

# Run all tests
pytest tests/ -v --cov=sentinel --cov-report=term-missing
```

### TypeScript tests

```bash
cd packages/mcp
pnpm test
```

With coverage:
```bash
pnpm test -- --coverage
```

## Adding a New Strategy

### 1. Create the strategy file

```python
# packages/engine/sentinel/strategy/implementations/my_signal.py

from sentinel.strategy.base import StrategyBase, StrategyResult

class MySignalStrategy(StrategyBase):
    name = "my_signal"
    description = "Describe the hypothesis clearly."

    def evaluate(self, bars, regime, symbol) -> StrategyResult:
        # Your signal logic here
        # Return StrategyResult with signal, confidence, details
        ...
```

### 2. Register it

```python
# packages/engine/sentinel/strategy/implementations/__init__.py
from .my_signal import MySignalStrategy
```

The strategy registry picks it up automatically on startup.

### 3. Write unit tests

```python
# packages/engine/tests/unit/test_decision.py
# Test signal fires for correct conditions
# Test no signal in wrong regime
# Test confidence scaling
```

### 4. Promote through lifecycle

See `docs/strategy-lifecycle.md` for the full promotion workflow.

## Adding a New Broker Adapter

### 1. Implement the protocol

```python
# packages/engine/sentinel/execution/my_broker.py

from sentinel.execution.broker import BrokerAdapter, OrderRequest, OrderUpdate

class MyBroker:
    async def submit_order(self, request: OrderRequest) -> OrderUpdate: ...
    async def cancel_order(self, broker_order_id: str) -> OrderUpdate: ...
    async def get_order(self, broker_order_id: str) -> OrderUpdate: ...
    async def get_positions(self) -> list[dict]: ...
    async def get_account(self) -> dict: ...
    async def is_market_open(self) -> bool: ...
```

`MyBroker` satisfies `BrokerAdapter` via structural subtyping (Protocol). No explicit inheritance required.

### 2. Add configuration

```python
# packages/engine/sentinel/config.py
broker: Literal["paper", "alpaca", "my_broker"] = "paper"
```

### 3. Wire it in the API

```python
# packages/engine/sentinel/api.py
# Add to broker factory function
```

## Code Conventions

### Python
- **Formatting**: `ruff format` (Black-compatible)
- **Linting**: `ruff check` — see `ruff.toml` for enabled rules
- **Type hints**: Required on all public functions. `from __future__ import annotations` for forward refs.
- **Imports**: `from __future__ import annotations` at the top of every module
- **Async**: All I/O functions are `async`. No blocking calls in async context.
- **Decimals**: Use `Decimal` for all monetary values. Never float for money.
- **Error handling**: Raise specific exceptions from `sentinel/market/provider.py`. Catch at API layer.

### TypeScript
- **Formatting**: `prettier` (run via `pnpm exec prettier --write src/`)
- **Linting**: `eslint` with TypeScript rules
- **Types**: Strict mode enabled. No `any` except at the API boundary.
- **Validation**: All tool inputs validated with Zod before touching the engine client.
- **Error handling**: Catch `AxiosError`, extract detail message, return `{ isError: true }` to MCP.

## Architecture of the Decision Pipeline

When a strategy generates a signal, the following sequence runs before any order is placed:

1. `RiskFirewall.assess()` — Runs all configured risk checks. Returns `RiskAssessment`.
2. If any hard-block check fails → return `DecisionOutcome.REJECTED` immediately.
3. Soft checks are accumulated as warnings.
4. Position sizing is computed based on account value, volatility, and confidence.
5. `AuditEvent` is written to the database regardless of outcome.
6. If approved → `BrokerAdapter.submit_order()`.
7. Fill confirmation or rejection updates the order record and triggers position update.

Every step is observable via the audit journal. Nothing happens silently.

## Database Schema Notes

- `audit_events` — Append-only. Never update or delete rows. This is the system of record.
- `orders` — Use `client_order_id` as the idempotency key. Same client ID = same order.
- `positions` — Maintained by the engine, not the broker. The engine is the source of truth for paper trading.
- `strategy_records` — Governance state lives here. `promotions` tracks every state transition.

## Environment Modes

| `APP_ENV` | Market Data | Broker | Orders |
|---|---|---|---|
| `development` | mock | paper | simulated |
| `paper` | alpaca (or mock) | paper | simulated via Alpaca paper |
| `live` | alpaca | live | real money |

The `development` mode uses the mock provider with deterministic output, which makes tests reproducible without external API calls.
