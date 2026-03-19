# Architecture

## Overview

Sentinel Execution MCP is a production-grade trading control plane split into two packages:

- **`packages/engine`** — A Python FastAPI service that owns all trading logic: market data ingestion, regime classification, risk checks, strategy execution, order management, and audit logging.
- **`packages/mcp`** — A TypeScript MCP (Model Context Protocol) server that exposes the engine's capabilities as tools that an AI agent can call.

The split is intentional. The engine is the single source of truth for all trading state. The MCP layer is a thin translation layer — it validates inputs with Zod schemas, calls the engine over HTTP, and formats responses. No trading logic lives in the MCP server.

## Component Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        AI Agent                              │
│              (Claude, GPT, local LLM, etc.)                  │
└─────────────────────┬───────────────────────────────────────┘
                      │ MCP protocol (stdio or SSE)
┌─────────────────────▼───────────────────────────────────────┐
│                   MCP Server (TypeScript)                     │
│                  packages/mcp/src/                           │
│                                                              │
│  Tool categories: watchlist, market, regime, strategy,       │
│  risk, portfolio, execution, governance, audit               │
│                                                              │
│  Each tool: Zod validation → EngineClient call → format      │
└─────────────────────┬───────────────────────────────────────┘
                      │ HTTP (JSON REST)
┌─────────────────────▼───────────────────────────────────────┐
│                  Engine Service (Python)                      │
│                packages/engine/sentinel/                     │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Watchlist│  │  Market  │  │  Regime  │  │  Risk    │   │
│  │ Service  │  │ Provider │  │Classifier│  │ Firewall │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │ Strategy │  │Execution │  │Governance│  │  Audit   │   │
│  │ Registry │  │  Broker  │  │ Service  │  │ Journal  │   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────┬────────────────────────────────────┬─────────────┘
          │                                    │
┌─────────▼────────┐                ┌──────────▼────────┐
│   PostgreSQL      │                │      Redis         │
│  (primary store)  │                │  (kill switch,     │
│  orders, positions│                │   cache, pubsub)   │
│  strategies, audit│                └───────────────────┘
└───────────────────┘
```

## Data Flow: Watchlist Scan to Order Execution

1. **Watchlist scan** — Agent calls `strategy.scan_watchlist`. Engine queries active symbols from PostgreSQL.
2. **Market data** — For each symbol, engine fetches latest bars from the configured provider (Alpaca or Mock).
3. **Regime classification** — `RegimeClassifier` runs a suite of technical indicators (ATR, ADX, RSI, Bollinger Width, Hurst Exponent, VWAP) on the bars and classifies the current market regime.
4. **Strategy evaluation** — Each registered strategy evaluates whether it sees a signal for each symbol/regime pair. Returns a `StrategyResult` with signal details and confidence.
5. **Risk firewall** — Before any order can proceed, the `RiskFirewall` runs all applicable checks (kill switch, drawdown limits, position sizing, liquidity, spread, timing). Any single hard-block check failure rejects the trade immediately.
6. **Decision committee** — A committee of checks votes on the trade. The outcome is `APPROVED`, `REJECTED`, `DEFERRED`, or `REQUIRES_HUMAN_APPROVAL`.
7. **Order submission** — Approved trades go to the `BrokerAdapter`. In paper mode, the `PaperBroker` simulates fills with configurable latency and slippage. In live mode, orders go to Alpaca.
8. **Audit journal** — Every decision, risk check result, fill, and rejection is written to the `audit_events` table. Nothing is silently dropped.

## Technology Choices

| Component | Choice | Rationale |
|---|---|---|
| Engine language | Python 3.12 | NumPy/Pandas for indicators; best-in-class quant library ecosystem |
| Web framework | FastAPI | Async-native, Pydantic integration, auto OpenAPI docs |
| ORM | SQLAlchemy 2.0 async | Type-safe, async sessions, Alembic migrations |
| Database | PostgreSQL 16 | JSONB for flexible event storage, ARRAY for tags |
| Cache/State | Redis 7 | Kill switch state, rate limiting, pub/sub for fills |
| MCP runtime | TypeScript / Node 20 | MCP SDK is TypeScript-first; type safety for tool schemas |
| MCP validation | Zod | Schema validation at the tool boundary |
| Testing (Python) | pytest + pytest-asyncio | Async test support; pytest ecosystem |
| Testing (TS) | Vitest | Fast, ESM-compatible, excellent TS support |

## Module Responsibilities

### `sentinel/risk/`
Pure functions with no side effects. Every risk check takes typed inputs and returns a `RiskCheckResult`. The `RiskFirewall` orchestrates all checks and produces a `RiskAssessment`. Tests cover each check function in complete isolation.

### `sentinel/regime/`
Technical indicator computation (`indicators.py`) and regime classification (`classifier.py`). The classifier uses a priority waterfall: opening noise → high volatility → risk-off → liquidity → trend → mean reversion → fallback. Each branch documents its threshold and reasoning.

### `sentinel/execution/`
`BrokerAdapter` protocol defines the interface. `PaperBroker` implements deterministic simulation with configurable fill latency and slippage. `AlpacaBroker` implements the live/paper trading connection.

### `sentinel/strategy/`
`StrategyBase` defines the interface. Each implementation evaluates one strategy's signal logic given bars and a regime snapshot. Strategies are registered in `StrategyRegistry` and can be enabled/disabled without code changes.

### `sentinel/watchlist/`
CRUD service for the symbol watchlist. Supports group tagging, import/export, and symbol validation against the configured market data provider.

## Extension Points

### Adding a new broker
1. Implement the `BrokerAdapter` protocol in `sentinel/execution/`.
2. Add configuration in `Settings` to select the broker.
3. Wire it into the router factory in `sentinel/api.py`.

### Adding a new strategy
1. Subclass `StrategyBase` in `sentinel/strategy/implementations/`.
2. Implement `evaluate(bars, regime, symbol) -> StrategyResult`.
3. Register it in `StrategyRegistry`.
4. Write unit tests in `tests/unit/test_decision.py`.

### Adding a new risk check
1. Add a pure function to `sentinel/risk/checks.py` following the existing signature pattern.
2. Wire it into `RiskFirewall.run_checks()`.
3. Add comprehensive unit tests in `tests/unit/test_risk_checks.py`.

## Key Design Decisions

**Conservative by default.** The risk system is designed to refuse trades when uncertain. Hard blocks are not overridable. This is a feature, not a limitation.

**Audit everything.** Every trade decision — approved or rejected — is written to `audit_events`. The audit journal is append-only and supports full explanation of why any given trade was made or refused.

**No implicit state.** Risk check functions are pure. Kill switch state is explicit in Redis. There are no hidden global variables that affect trading behavior.

**Paper-first.** The default configuration runs in paper mode with mock market data. Live trading requires explicit environment variable changes and human sign-off on strategy promotion.

**Separation of concerns.** The MCP server contains zero trading logic. If the engine is down, the MCP layer fails loudly and returns `isError: true` on every tool call.
