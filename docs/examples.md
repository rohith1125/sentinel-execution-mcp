# Usage Examples

## Workflow: Morning Scan → Signal → Risk Check → Paper Order → Review

This is the standard daily workflow for discovering and executing paper trades.

### Step 1: Add symbols to the watchlist

```
watchlist.add(
  symbols=["AAPL", "MSFT", "NVDA", "META"],
  group="tech_large_cap"
)
```

### Step 2: Check market health and regime

```
market.health()
# → { "status": "ok", "provider": "alpaca", "latency_ms": 42 }

regime.evaluate(symbol="SPY")
# → { "label": "trending_bull", "confidence": 0.78, "tradeability_score": 0.80 }
```

If tradeability is below 0.4, consider waiting. High volatility and opening noise regimes are poor trading environments.

### Step 3: Scan for signals

```
strategy.scan_watchlist(group="tech_large_cap", strategies=["momentum_breakout"])
```

Returns a list of `StrategyResult` objects. Signals with `confidence >= 0.7` and a regime `tradeability_score >= 0.6` are worth investigating.

### Step 4: Pre-flight risk check

Before submitting an order, validate the trade explicitly:

```
risk.validate_trade(
  symbol="AAPL",
  side="buy",
  quantity=50,
  order_type="limit"
)
# → { "passed": true, "blocking_checks": [], "warning_checks": ["slippage_estimate"] }
```

If `passed=false`, check `blocking_checks` to understand why. If there are warnings, review them but you can proceed.

### Step 5: Submit paper order

```
execution.paper_order(
  symbol="AAPL",
  side="buy",
  order_type="limit",
  quantity=50,
  limit_price=182.45,
  strategy_id="momentum_breakout"
)
# → { "order_id": "ord-abc123", "status": "pending", ... }
```

### Step 6: Monitor and review

```
execution.get_order(order_id="ord-abc123")
# → { "status": "filled", "filled_qty": 50, "filled_avg_price": 182.47 }

portfolio.status()
# → positions, P&L, exposure summary

audit.recent_events(symbol="AAPL", limit=5)
# → list of recent decisions for AAPL
```

---

## Workflow: Strategy Promotion Process

### Check current state

```
governance.list_strategies()
# → [{ "name": "momentum_breakout", "state": "paper_approved", ... }]
```

### Evaluate promotion eligibility

```
governance.evaluate_promotion(
  strategy="momentum_breakout",
  target_state="live_approved"
)
# → {
#     "eligible": false,
#     "blocking_criteria": [
#       "Requires APP_ENV=live configuration",
#       "Requires approved_by from authorized personnel"
#     ]
#   }
```

### Check for drift before promotion

```
governance.check_drift(strategy="momentum_breakout")
# → { "drift_detected": false, "metrics_deviation": 0.12, "details": "..." }
```

### Promote (when eligible)

```
governance.promote_strategy(
  strategy="momentum_breakout",
  target_state="live_approved",
  approved_by="risk-officer@firm.com",
  notes="Reviewed 45 days paper history. Sharpe 1.4. Win rate 54%. Approved."
)
```

---

## Workflow: Investigating a Rejected Trade

When a trade you expected to go through was rejected, use the audit trail.

### Step 1: Find the rejection event

```
audit.recent_events(symbol="AAPL", limit=10)
# Look for events with decision_outcome = "REJECTED"
# Copy the audit_event_id
```

### Step 2: Get the full explanation

```
audit.explain_trade(audit_event_id="evt-xyz789")
# → {
#     "decision": "REJECTED",
#     "blocking_checks": ["daily_drawdown"],
#     "explanation": "Risk Assessment for buy 100 shares of AAPL
#                    REJECTED
#                    BLOCKING failures (1):
#                      [BLOCK] daily_drawdown: Daily drawdown 2.15% EXCEEDS limit of 2.00%..."
#   }
```

### Step 3: Understand and respond

In this case, the daily drawdown limit was hit. Options:
- Wait until tomorrow (limit resets at midnight)
- Engage the kill switch if you want to stop all trading: `risk.engage_halt(reason="Daily limit hit, halting for review")`
- Close existing positions to reduce unrealized losses: `portfolio.positions()` then targeted cancels

---

## Example Tool Calls by Category

### Watchlist management
```
watchlist.add(symbols=["COIN", "AMD"], group="speculative")
watchlist.remove(symbols=["COIN"])
watchlist.list(group="speculative")
watchlist.groups()
watchlist.tag(symbols=["AMD"], group="ai_play")
watchlist.export(group="tech")
```

### Market data
```
market.snapshot(symbol="NVDA")
market.snapshots(symbols=["AAPL", "MSFT", "NVDA"])
market.bars(symbol="AAPL", timeframe="5Min", limit=50)
market.health()
```

### Regime classification
```
regime.evaluate(symbol="AAPL", timeframe="1Min")
regime.evaluate_bulk(symbols=["AAPL", "MSFT", "NVDA", "SPY"])
```

### Risk management
```
risk.kill_switch_status()
risk.validate_trade(symbol="AAPL", side="buy", quantity=100, order_type="market")
risk.engage_halt(reason="Unusual market conditions detected")
risk.disengage_halt(confirm="CONFIRM")
risk.halt_strategy(strategy="momentum_breakout", reason="Reviewing signal quality")
risk.halt_symbol(symbol="AAPL", reason="Earnings announcement pending")
```

### Portfolio
```
portfolio.status()
portfolio.positions()
portfolio.account()
```

### Execution
```
execution.paper_order(symbol="AAPL", side="buy", order_type="market", quantity=10)
execution.paper_order(symbol="MSFT", side="buy", order_type="limit", quantity=20, limit_price=415.00)
execution.get_order(order_id="ord-abc")
execution.cancel_order(order_id="ord-abc", reason="Signal expired")
execution.flatten_all(confirm="FLATTEN_ALL", reason="End of day cleanup")
execution.reset_paper(starting_cash=100000)
```

### Governance
```
governance.list_strategies()
governance.evaluate_promotion(strategy="vwap_reclaim", target_state="paper_approved")
governance.promote_strategy(strategy="vwap_reclaim", target_state="backtest_approved", approved_by="dev@firm.com")
governance.suspend_strategy(strategy="momentum_breakout", reason="Sharp drawdown in last 3 days")
governance.check_drift(strategy="vwap_reclaim")
```

### Audit
```
audit.recent_events(limit=20, symbol="AAPL")
audit.explain_trade(audit_event_id="evt-abc123")
audit.daily_summary()
audit.daily_summary(date="2024-01-15")
audit.weekly_summary()
audit.strategy_scorecard(strategy="momentum_breakout", days=30)
audit.trade_blotter(start="2024-01-01", end="2024-01-31", strategy="momentum_breakout")
```
