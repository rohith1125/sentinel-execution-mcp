# MCP Tool Reference

This document lists all available MCP tools, their parameters, and usage notes.

## Watchlist Tools

### `watchlist.add`
Add symbols to the trading watchlist.

**Parameters:**
- `symbols` (string[], required) — Ticker symbols to add. Max 100 per call.
- `group` (string, optional) — Group tag to assign. Max 64 chars.
- `notes` (string, optional) — Free-text notes. Max 500 chars.

**Example:**
```json
{ "symbols": ["AAPL", "MSFT"], "group": "tech", "notes": "Q1 earnings plays" }
```

---

### `watchlist.remove`
Mark symbols as inactive. Removed symbols no longer appear in strategy scans.

**Parameters:**
- `symbols` (string[], required) — Symbols to remove.

---

### `watchlist.list`
List active watchlist entries with full metadata.

**Parameters:**
- `group` (string, optional) — Filter by group tag.
- `active_only` (boolean, optional, default: true) — Include inactive entries if false.

---

### `watchlist.groups`
List all distinct group tags currently in use.

**Parameters:** None.

---

### `watchlist.tag`
Assign a group tag to existing symbols. Additive — does not remove existing tags.

**Parameters:**
- `symbols` (string[], required) — Symbols to tag.
- `group` (string, required) — Group tag to assign.

---

### `watchlist.export`
Export the watchlist as JSON. Suitable for backup or external analysis.

**Parameters:**
- `group` (string, optional) — Filter to a specific group.

---

## Market Tools

### `market.snapshot`
Get current price, quote, and volume snapshot for a symbol.

**Parameters:**
- `symbol` (string, required) — Ticker symbol.

---

### `market.snapshots`
Get snapshots for multiple symbols in one call.

**Parameters:**
- `symbols` (string[], required) — Ticker symbols.

---

### `market.bars`
Get historical OHLCV bars.

**Parameters:**
- `symbol` (string, required)
- `timeframe` (string, optional, default: "1Min") — e.g., "1Min", "5Min", "1Hour", "1Day"
- `limit` (integer, optional, default: 100) — Max 500 bars.

---

### `market.health`
Check market data provider status and latency.

**Parameters:** None.

---

## Regime Tools

### `regime.evaluate`
Classify the current market regime for a symbol based on recent bar history.

**Parameters:**
- `symbol` (string, required)
- `timeframe` (string, optional, default: "1Min")

**Returns:** `label`, `confidence`, `tradeability_score`, `strategy_compatibility`, `reasoning`, `supporting_metrics`

---

### `regime.evaluate_bulk`
Evaluate regime for multiple symbols concurrently.

**Parameters:**
- `symbols` (string[], required)

---

## Strategy Tools

### `strategy.scan_watchlist`
Run registered strategies against the entire watchlist. Returns signals for all symbol/strategy combinations that meet thresholds.

**Parameters:**
- `group` (string, optional) — Limit to a watchlist group.
- `strategies` (string[], optional) — Limit to specific strategy names.

---

### `strategy.scan_symbol`
Run strategies against a single symbol.

**Parameters:**
- `symbol` (string, required)
- `strategies` (string[], optional)

---

### `strategy.list`
List all registered strategy names.

**Parameters:** None.

---

## Risk Tools

### `risk.validate_trade`
Run the full risk firewall against a proposed trade without submitting it. Useful for pre-flight checks.

**Parameters:**
- `symbol` (string, required)
- `side` (string, required) — "buy" or "sell"
- `quantity` (integer, required)
- `order_type` (string, required) — "market", "limit", "stop", "stop_limit"
- `limit_price` (number, optional)

---

### `risk.kill_switch_status`
Get the current kill switch state.

**Parameters:** None.

---

### `risk.engage_halt`
Engage global trading halt. All new orders will be blocked until disengaged.

**Parameters:**
- `reason` (string, required, min 10 chars) — Document why the halt is being engaged.

**Note:** This is a serious action. The halt persists across restarts and does not auto-expire.

---

### `risk.disengage_halt`
Disengage global trading halt.

**Parameters:**
- `confirm` (string, required) — Must equal the literal string `"CONFIRM"`.

---

### `risk.halt_strategy`
Halt a specific strategy without affecting others.

**Parameters:**
- `strategy` (string, required)
- `reason` (string, required)

---

### `risk.halt_symbol`
Halt trading in a specific symbol.

**Parameters:**
- `symbol` (string, required)
- `reason` (string, required)

---

## Portfolio Tools

### `portfolio.status`
Get full portfolio state: positions, P&L, exposure metrics.

**Parameters:** None.

---

### `portfolio.positions`
List all open positions.

**Parameters:** None.

---

### `portfolio.account`
Get account value, cash balance, and buying power.

**Parameters:** None.

---

## Execution Tools

### `execution.paper_order`
Submit a paper trading order.

**Parameters:**
- `symbol` (string, required)
- `side` (string, required) — "buy" or "sell"
- `order_type` (string, required) — "market" or "limit"
- `quantity` (integer, required, min: 1)
- `limit_price` (number, optional, required for limit orders)
- `strategy_id` (string, optional) — Associate order with a strategy.

---

### `execution.cancel_order`
Cancel a pending order.

**Parameters:**
- `order_id` (string, required)
- `reason` (string, required)

---

### `execution.get_order`
Get the current status of an order.

**Parameters:**
- `order_id` (string, required)

---

### `execution.flatten_all`
Cancel all open orders and close all open positions. Emergency action.

**Parameters:**
- `confirm` (string, required) — Must equal `"FLATTEN_ALL"`.
- `reason` (string, required)

**Note:** This is irreversible. All positions will be closed at market.

---

### `execution.reset_paper`
Reset the paper trading account to starting conditions.

**Parameters:**
- `starting_cash` (number, optional, default: 100000)

---

## Governance Tools

### `governance.evaluate_promotion`
Check whether a strategy meets the criteria for promotion to a target state.

**Parameters:**
- `strategy` (string, required)
- `target_state` (string, required)

**Returns:** `eligible`, `blocking_criteria`, `metrics_summary`

---

### `governance.promote_strategy`
Promote a strategy to a higher state. Requires approved_by for paper→live.

**Parameters:**
- `strategy` (string, required)
- `target_state` (string, required)
- `approved_by` (string, required)
- `notes` (string, optional)

---

### `governance.suspend_strategy`
Suspend a strategy from trading.

**Parameters:**
- `strategy` (string, required)
- `reason` (string, required)

---

### `governance.list_strategies`
List all registered strategies with their current governance state.

**Parameters:** None.

---

### `governance.check_drift`
Check whether a strategy's behavior has drifted from its promotion baseline.

**Parameters:**
- `strategy` (string, required)

---

## Audit Tools

### `audit.explain_trade`
Get a full human-readable explanation of a trade decision (approved or rejected).

**Parameters:**
- `audit_event_id` (string, required) — UUID of the audit event.

---

### `audit.recent_events`
Get recent audit events with optional filters.

**Parameters:**
- `limit` (integer, optional, default: 50, max: 200)
- `symbol` (string, optional) — Filter to a specific symbol.
- `strategy` (string, optional) — Filter to a specific strategy.

---

### `audit.daily_summary`
Get the daily trading summary (P&L, trades, win rate).

**Parameters:**
- `date` (string, optional, format: "YYYY-MM-DD", default: today)

---

### `audit.weekly_summary`
Get the weekly trading summary.

**Parameters:**
- `week_ending` (string, optional, format: "YYYY-MM-DD")

---

### `audit.strategy_scorecard`
Get performance metrics for a specific strategy.

**Parameters:**
- `strategy` (string, required)
- `days` (integer, optional, default: 30) — Lookback window.

---

### `audit.trade_blotter`
Get the trade blotter for a date range.

**Parameters:**
- `start` (string, required, format: "YYYY-MM-DD")
- `end` (string, required, format: "YYYY-MM-DD")
- `strategy` (string, optional)
