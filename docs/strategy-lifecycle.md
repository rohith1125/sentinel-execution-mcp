# Strategy Lifecycle

## States

Strategies move through a defined promotion path. State transitions are one-way (forward) except for suspension and retirement which can happen from any active state.

```
draft → research → backtest_approved → paper_approved → live_approved
  ↓         ↓              ↓                  ↓               ↓
suspended  suspended     suspended          suspended       suspended
                                                              ↓
                                                           retired
```

### `draft`
The initial state. A strategy exists in the registry but has no performance record. The engine will not route live or paper orders to a draft strategy.

### `research`
Strategy logic is written and passing basic tests. The developer has reviewed the signal logic and documented the hypothesis. Still not eligible for backtesting production capital allocation.

### `backtest_approved`
A formal backtest has been completed and reviewed. Key metrics have been recorded in `performance_metrics`. The strategy can now run in paper trading.

### `paper_approved`
The strategy has accumulated sufficient paper trading history with acceptable results. It is eligible for promotion to live trading — but only with explicit human sign-off.

### `live_approved`
The strategy can submit real orders via the live broker. This state requires the account to be configured for live trading (`APP_ENV=live`).

### `suspended`
Manually or automatically suspended due to performance degradation, drift detection, or operator action. No orders are routed. The strategy can be re-promoted after investigation.

### `retired`
Terminal state. The strategy is decommissioned and will not be re-promoted.

## Promotion Criteria

### `draft` → `research`
- Strategy class exists and is importable
- Basic unit tests pass
- Hypothesis documented in strategy `description` field

### `research` → `backtest_approved`
- Formal backtest completed (external tool, results imported via API)
- Sharpe ratio ≥ 1.0 on out-of-sample data
- Maximum drawdown < 20% in backtest
- Results reviewed by at least one person other than the author
- `performance_metrics` populated with backtest summary

### `backtest_approved` → `paper_approved`
- Minimum 30 calendar days of paper trading
- Minimum 20 paper trades
- Paper win rate ≥ 40%
- Paper Sharpe ratio ≥ 0.8
- No single paper trade loss > 2% of paper capital
- No parameter changes since paper trading began (drift check)

### `paper_approved` → `live_approved`
**This transition requires explicit human sign-off.**

The engine will never automatically promote a strategy to live. The promotion API requires:
- `approved_by` — a non-empty identifier of the human approving the promotion
- `notes` — documentation of why the promotion is approved
- The caller must also have `APP_ENV=live` configured

This is a hard technical requirement, not a soft guideline.

## Why `paper → live` Requires Human Sign-Off

Paper trading and live trading are fundamentally different environments:
- **Fills** — Paper fills assume you can always execute at the quoted price. Live fills have slippage, partial fills, and queue position.
- **Liquidity** — A paper order for 10,000 shares of a thinly-traded stock will fill at the simulated price. A live order may move the market.
- **Psychology** — Watching real money change hands produces different decision-making than watching paper P&L.
- **Systemic risk** — A bug that causes losses in paper is a learning opportunity. The same bug in live trading is a financial event.

Human review of the promotion decision provides an accountability checkpoint that automated systems cannot replicate.

## Drift Detection

Drift detection checks whether a live or paper strategy's current behavior matches the parameters recorded at promotion time. Drift is flagged when:

- Strategy configuration has changed since the last promotion
- Key performance metrics have deviated more than 2 standard deviations from the backtest baseline
- Win rate or Sharpe ratio has declined significantly over the last 30 days

Drift does not automatically suspend a strategy. It generates a warning that is surfaced via the `governance.check_drift` tool. However, significant drift is a blocking criterion for re-promotion.

## Example Promotion Workflow

### Step 1: Write and register the strategy
```python
# packages/engine/sentinel/strategy/implementations/my_strategy.py
class MyStrategy(StrategyBase):
    name = "my_strategy"
    ...
```

### Step 2: Promote to research
```
governance.promote_strategy(
  strategy="my_strategy",
  target_state="research",
  approved_by="developer@firm.com",
  notes="Signal hypothesis documented. Unit tests passing."
)
```

### Step 3: Run paper trading for 30+ days, then check metrics
```
governance.evaluate_promotion(
  strategy="my_strategy",
  target_state="paper_approved"
)
# Returns: eligible=true/false, blocking_criteria=[...]
```

### Step 4: Promote to paper_approved
```
governance.promote_strategy(
  strategy="my_strategy",
  target_state="paper_approved",
  approved_by="trader@firm.com",
  notes="30 days paper. 52% win rate, 1.2 Sharpe. Approved."
)
```

### Step 5: Live promotion (requires APP_ENV=live)
```
governance.promote_strategy(
  strategy="my_strategy",
  target_state="live_approved",
  approved_by="risk-officer@firm.com",
  notes="Reviewed paper results. Position sizing conservative. Approved for live."
)
```

## Strategy Health Metrics

The engine tracks the following per-strategy metrics for governance decisions:

- `total_trades` — Total number of closed trades
- `win_rate` — Fraction of trades that closed with positive P&L
- `avg_pnl` — Average P&L per trade in dollars
- `sharpe_ratio` — Annualized Sharpe ratio (risk-adjusted return)
- `max_drawdown_pct` — Maximum peak-to-trough drawdown since activation
- `avg_hold_minutes` — Average holding period in minutes
- `consecutive_losses` — Current streak of losing trades

These are available via `audit.strategy_scorecard`.
