# Deployment Guide

## Prerequisites

- Python 3.12+
- Node.js 20+
- pnpm 9+
- Docker and Docker Compose (for database/Redis)
- PostgreSQL 16 (or use Docker)
- Redis 7 (or use Docker)

## Local Development Setup

### 1. Clone and configure

```bash
git clone <repo>
cd sentinel-execution-mcp
cp .env.example .env
# Edit .env — most defaults are fine for local development
```

The defaults use `MARKET_DATA_PROVIDER=mock`, which requires no external credentials.

### 2. Start infrastructure

```bash
make docker-up
# Or just the dependencies:
docker-compose -f docker/docker-compose.yml up -d db redis
```

Wait for both services to be healthy:
```bash
docker-compose -f docker/docker-compose.yml ps
```

### 3. Install dependencies

```bash
make install
```

This runs:
- `pip install -e ".[dev]"` in `packages/engine`
- `pnpm install` in `packages/mcp`

### 4. Run database migrations

```bash
make migrate
```

### 5. Start the engine

```bash
cd packages/engine
uvicorn sentinel.api:app --reload --host 0.0.0.0 --port 8100
```

Verify: `curl http://localhost:8100/health`

### 6. Start the MCP server (development)

```bash
cd packages/mcp
pnpm dev
```

The MCP server starts in stdio mode by default (`MCP_TRANSPORT=stdio`). To use SSE mode, set `MCP_TRANSPORT=sse` and `MCP_SSE_PORT=3100`.

## Docker Deployment

Build and start all services:

```bash
# Build images
make docker-build

# Start everything
make docker-up
```

Services:
- PostgreSQL on port 5432
- Redis on port 6379
- Engine on port 8100
- MCP server on port 3100

To stop:
```bash
make docker-down
```

Data volumes are stored in `docker/volumes/` (excluded from git).

## Environment Variable Reference

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `paper` | Application environment: `development`, `paper`, or `live` |
| `LOG_LEVEL` | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `DATABASE_URL` | `postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel` | PostgreSQL connection string |
| `DATABASE_POOL_SIZE` | `10` | SQLAlchemy connection pool size |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis connection string |
| `REDIS_TTL_SECONDS` | `300` | Default cache TTL |
| `MARKET_DATA_PROVIDER` | `mock` | Data provider: `mock` or `alpaca` |
| `ALPACA_API_KEY` | `` | Alpaca API key (required when provider=alpaca) |
| `ALPACA_API_SECRET` | `` | Alpaca API secret |
| `ALPACA_BASE_URL` | `https://paper-api.alpaca.markets` | Alpaca endpoint |
| `MAX_POSITION_PCT` | `0.05` | Maximum position size as fraction of account |
| `MAX_DAILY_DRAWDOWN_PCT` | `0.02` | Daily loss limit (2%) |
| `MAX_GROSS_EXPOSURE_PCT` | `0.80` | Maximum gross exposure |
| `MAX_CONCURRENT_POSITIONS` | `10` | Maximum open positions |
| `PAPER_FILL_LATENCY_MS` | `50` | Simulated fill latency in paper mode |
| `SLIPPAGE_BPS` | `5` | Simulated slippage in basis points |
| `ENGINE_HOST` | `0.0.0.0` | Engine bind address |
| `ENGINE_PORT` | `8100` | Engine HTTP port |
| `MCP_TRANSPORT` | `stdio` | MCP transport: `stdio` or `sse` |
| `MCP_SSE_PORT` | `3100` | SSE server port (when transport=sse) |
| `ENGINE_BASE_URL` | `http://localhost:8100` | Engine URL from MCP server's perspective |
| `ENGINE_TIMEOUT_MS` | `10000` | Engine request timeout |

## Database Migrations

Create a new migration after changing models:
```bash
make migrate-new MSG="add column to positions"
```

Apply pending migrations:
```bash
make migrate
```

The migration history is in `packages/engine/sentinel/db/migrations/versions/`.

## Production Considerations

**Never run `APP_ENV=live` unless you understand the implications.** Live mode routes orders to a real broker with real money. Mistakes are financially consequential.

**Secure your environment variables.** Do not commit `.env` files. Use a secrets manager (AWS Secrets Manager, HashiCorp Vault) in production.

**Database backups.** The audit journal is your primary record of all trading decisions. Back up PostgreSQL regularly.

**Redis persistence.** Kill switch state is stored in Redis. If Redis loses data (and you haven't configured RDB/AOF persistence), the kill switch state resets on restart. The Docker configuration enables AOF persistence (`--appendonly yes`).

**Health checks.** The engine exposes `GET /health`. Wire this into your load balancer or orchestrator health check.

**Log aggregation.** In production mode (`APP_ENV=paper` or `live`), the engine outputs structured JSON logs. Aggregate these with your preferred logging stack (Datadog, Splunk, Loki).

**Uvicorn workers.** The Docker CMD uses `--workers 2`. Adjust based on your CPU count. For most trading workloads, 2-4 workers is sufficient; the bottleneck is I/O, not CPU.
