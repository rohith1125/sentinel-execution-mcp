# Risk Model

## Philosophy

The core principle of Sentinel's risk system is: **refusal is a feature**.

Every risk check function is designed to err on the side of blocking. When a check cannot determine whether a trade is safe (missing data, boundary conditions, unexpected inputs), it defaults to blocking rather than allowing. This conservatism is intentional and documented.

Risk checks are **pure functions** — they take typed inputs and return a `RiskCheckResult`. No I/O, no side effects, no database access. This makes them trivially testable in complete isolation.

## Hard Blocks vs. Soft Warnings

**Hard blocks** (`is_hard_block=True`) cause immediate, unconditional trade rejection. A single hard block failure rejects the entire trade regardless of what other checks say.

**Soft warnings** (`is_hard_block=False`) appear in the risk assessment output and are surfaced to the agent, but do not by themselves prevent execution. They exist to inform decision-making.

Most checks in Sentinel are hard blocks. Soft checks exist for information that is useful but unavailable in all environments (e.g., sector classification, correlation matrices).

## Risk Check Reference

### Hard Blocks

#### `check_kill_switch`
Checks three halt conditions in order:
1. **Global halt** — Stops all trading system-wide. Set via `risk.engage_halt`.
2. **Strategy halt** — Stops a specific strategy. Other strategies are unaffected.
3. **Symbol halt** — Stops trading in a specific ticker. Other tickers are unaffected.

If any halt is engaged, this is a hard block. The message includes the halt reason, who engaged it, and when.

#### `check_daily_drawdown`
Computes `(realized_pnl_today + unrealized_pnl) / account_value` as a negative percentage. If this exceeds `max_daily_drawdown_pct`, trading halts for the day.

Note: unrealized losses are included. This prevents strategies from accumulating large floating losses by avoiding realizing them.

Default limit: 2% of account value.

#### `check_weekly_drawdown`
Same logic as daily, but computed over the trading week using realized P&L only (unrealized excluded for weekly calculation to avoid over-counting intraday noise).

Default limit: 4% of account value.

#### `check_max_concurrent_positions`
Rejects new entries when the count of open positions equals or exceeds `max_concurrent`. This limits portfolio fragmentation and ensures adequate attention can be paid to each position.

Default: 10 positions.

#### `check_per_trade_risk`
Validates that the dollar risk on a single trade (entry to stop) does not exceed `max_trade_risk_pct` of account value.

Default: 2% per trade.

#### `check_symbol_concentration`
Validates that adding a new position in a symbol does not push that symbol's total notional exposure above `max_symbol_pct` of account value. Existing position in the same symbol is included in the calculation.

Default: 10% per symbol.

#### `check_gross_exposure`
Validates that total notional exposure across all positions (long + short) does not exceed `max_gross_pct` after adding the proposed trade.

Default: 80% of account value.

#### `check_spread_threshold`
Validates that the current bid-ask spread is within acceptable bounds. Wide spreads indicate poor liquidity or a volatility event. Trading through a wide spread guarantees bad fills.

Default: 30 bps maximum spread.

#### `check_liquidity_threshold`
Two-part check:
1. Average Daily Volume (ADV) must exceed `min_adv` (default: 500,000 shares).
2. Order size must not exceed `max_adv_pct` of ADV (default: 1%) to avoid unacceptable market impact.

#### `check_no_trade_window`
Blocks trading:
- Before market open (09:30 ET)
- After market close (16:00 ET)
- In the final 5 minutes before close (15:55 - 16:00 ET)

The final 5-minute blackout prevents forced position exits in illiquid conditions at close.

### Soft Checks (Warnings)

#### `check_consecutive_losses_cooldown`
Counts consecutive losses from the most recent trade backward. If the count reaches `max_consecutive_losses` (default: 3) and a cooldown period has not elapsed (default: 30 minutes), this check fails with a warning.

This check can be promoted to a hard block by setting `hard_block=True` in configuration.

#### `check_sector_concentration`
Warns when a symbol's sector would exceed `max_sector_pct` (default: 25%) of account value after the proposed trade. Requires sector data from the market provider — silently passes if sector data is unavailable.

#### `check_correlated_exposure`
Warns when a proposed position is highly correlated (r ≥ 0.70) with an existing position in the same direction. Silently passes if correlation data is unavailable (common in paper/mock mode).

#### `check_slippage_estimate`
Estimates total slippage in basis points: spread cost (half spread for limits, full for market/stop) plus a market impact component based on order size relative to ADV. Warns if estimated slippage exceeds 10 bps.

## Kill Switch Operation

The kill switch state is stored in Redis for persistence across restarts and fast read access.

```
Engage global halt:  POST /risk/halt/engage  { "reason": "..." }
Disengage halt:      POST /risk/halt/disengage
Halt strategy:       POST /risk/halt/strategy { "strategy": "...", "reason": "..." }
Halt symbol:         POST /risk/halt/symbol   { "symbol": "...", "reason": "..." }
```

Via MCP tools:
- `risk.engage_halt` — Requires a non-empty reason string
- `risk.disengage_halt` — Requires the confirmation string `"CONFIRM"` as a safety guard
- `risk.halt_strategy`, `risk.halt_symbol` — Available for targeted halts

A global halt does not automatically clear at the start of the next trading day. It requires explicit disengage via the MCP tool or API. This is intentional.

## Drawdown Mechanics

Daily drawdown resets at the start of each trading session (midnight UTC). The engine computes this as:

```
drawdown_pct = max(0, -(realized_pnl_today + unrealized_pnl) / account_value)
```

If `drawdown_pct >= max_daily_drawdown_pct`, the `check_daily_drawdown` hard block fires and all new orders are rejected. Existing positions are not automatically closed — you must use `execution.flatten_all` if you want to close them.

Weekly drawdown uses realized P&L only (accumulated from Monday open to current time) and resets each week.

## Position Sizing

Position sizing is computed separately from risk checks. The engine uses a volatility-adjusted sizing model:

1. **Base size** — `account_value * max_position_pct` (default: 5% of account)
2. **Volatility scalar** — Reduce size when ATR% is elevated
3. **Confidence scalar** — Reduce size proportionally to strategy confidence score
4. **Liquidity cap** — Hard cap at 1% of ADV regardless of other factors
5. **Concentration cap** — Clipped at `max_symbol_pct` of account

The binding constraint (whichever produces the smallest position) is reported in the sizing output so you can understand why a position was sized smaller than expected.

## Recommended Parameter Values

These defaults are conservative and appropriate for a small retail account. Adjust carefully.

| Parameter | Default | Notes |
|---|---|---|
| `max_position_pct` | 5% | 5% per position, 20 positions maximum before gross exposure cap |
| `max_daily_drawdown_pct` | 2% | Tight. Consider 3% if you have high-confidence strategies |
| `max_gross_exposure_pct` | 80% | Leave 20% cash buffer |
| `max_concurrent_positions` | 10 | Fewer positions = more focus = better execution |
| `max_spread_bps` | 30 | Generous for most liquid names; reduce to 15 for large-cap only |
| `min_adv` | 500,000 | Appropriate for US equities; increase for safety |

Do not increase `max_daily_drawdown_pct` above 5% without understanding the compounding effects of consecutive losing days.
