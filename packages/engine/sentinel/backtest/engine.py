"""
Event-driven backtest engine. Processes bars chronologically, runs strategies,
applies the risk firewall, simulates fills with configurable slippage.

Design: conservative and realistic
- Uses close price for signal generation (no lookahead bias)
- Fills at next bar's open + slippage (avoids filling at signal bar)
- Applies the real risk checks (drawdown, concentration, etc.)
- Tracks all trades with full details for audit
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sentinel.market.provider import Bar
from sentinel.regime.classifier import RegimeClassifier
from sentinel.strategy.base import StrategyBase

if TYPE_CHECKING:
    from sentinel.backtest.stats import BacktestStats


@dataclass
class BacktestConfig:
    strategy_name: str
    symbol: str
    start_date: date
    end_date: date
    initial_capital: Decimal = field(default_factory=lambda: Decimal("100000"))
    risk_per_trade_pct: float = 0.01  # 1% per trade
    max_concurrent_positions: int = 1  # single-symbol test = 1
    slippage_bps: int = 5
    commission_per_share: Decimal = field(default_factory=lambda: Decimal("0.005"))
    max_daily_drawdown_pct: float = 0.02


@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    entry_date: date
    exit_date: date | None
    side: str
    entry_price: Decimal
    exit_price: Decimal | None
    shares: int
    realized_pnl: Decimal
    pnl_pct: float
    hold_bars: int
    exit_reason: str  # "target", "stop", "time_stop", "end_of_data"
    regime_at_entry: str
    signal_confidence: float
    r_multiple: float  # pnl / initial_risk


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[BacktestTrade]
    equity_curve: list[tuple[date, Decimal]]  # (date, equity)
    stats: BacktestStats
    ran_at: datetime


class BacktestEngine:
    """
    Runs a strategy against historical bar data.

    Process for each bar:
    1. Update open positions (check stop/target hits)
    2. If no open position and strategy eligible: evaluate strategy
    3. If signal: check risk rules, compute size
    4. If approved: open position at next bar open + slippage
    5. Record trade on close
    """

    def __init__(
        self,
        strategy: StrategyBase,
        regime_classifier: RegimeClassifier,
        config: BacktestConfig,
    ) -> None:
        self.strategy = strategy
        self.regime_classifier = regime_classifier
        self.config = config

    def run(self, bars: list[Bar]) -> BacktestResult:
        """Run backtest on provided bars. Returns complete result."""
        from sentinel.backtest.stats import compute_stats

        config = self.config
        equity = config.initial_capital
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[date, Decimal]] = []

        open_position: dict | None = None
        pending_entry: dict | None = None  # signal generated, waiting for next bar open

        # Track daily pnl for drawdown checks
        day_start_equity = equity

        min_bars = self.strategy.min_bars_required

        for i, bar in enumerate(bars):
            bar_date = bar.timestamp.date() if hasattr(bar.timestamp, "date") else bar.timestamp

            # Reset daily tracking on new day (simple approach: use bar index)
            if i == 0:
                day_start_equity = equity

            # --- Step 1: Fill pending entry at this bar's open ---
            if pending_entry is not None and open_position is None:
                fill_price = self._compute_fill_price(bar, pending_entry["side"])
                # Check daily drawdown before entering
                daily_loss_pct = float((day_start_equity - equity) / day_start_equity) if day_start_equity > 0 else 0.0
                if daily_loss_pct < config.max_daily_drawdown_pct:
                    open_position = {
                        "symbol": config.symbol,
                        "strategy": self.strategy.name,
                        "entry_date": bar_date,
                        "side": pending_entry["side"],
                        "entry_price": fill_price,
                        "stop_price": pending_entry["stop_price"],
                        "target_price": pending_entry["target_price"],
                        "shares": pending_entry["shares"],
                        "bar_index": i,
                        "regime_at_entry": pending_entry["regime_at_entry"],
                        "signal_confidence": pending_entry["signal_confidence"],
                        "max_hold_bars": pending_entry.get("max_hold_bars"),
                        "initial_risk": pending_entry["initial_risk"],
                    }
                    # Deduct commission on entry
                    commission = config.commission_per_share * open_position["shares"]
                    equity -= commission
                pending_entry = None

            # --- Step 2: Update open position (check stop/target) ---
            if open_position is not None:
                closed = self._update_open_position(open_position, bar)
                if closed is not None:
                    pnl = closed["realized_pnl"]
                    # Deduct exit commission
                    commission = config.commission_per_share * closed["shares"]
                    pnl -= commission
                    equity += pnl

                    initial_risk = open_position["initial_risk"]
                    r_multiple = float(pnl / initial_risk) if initial_risk != 0 else 0.0

                    entry_price = open_position["entry_price"]
                    entry_notional = entry_price * open_position["shares"]
                    pnl_pct = float(pnl / entry_notional) if entry_notional != 0 else 0.0

                    trades.append(
                        BacktestTrade(
                            symbol=config.symbol,
                            strategy=self.strategy.name,
                            entry_date=open_position["entry_date"],
                            exit_date=bar_date,
                            side=open_position["side"],
                            entry_price=open_position["entry_price"],
                            exit_price=closed["exit_price"],
                            shares=open_position["shares"],
                            realized_pnl=pnl,
                            pnl_pct=pnl_pct,
                            hold_bars=i - open_position["bar_index"],
                            exit_reason=closed["exit_reason"],
                            regime_at_entry=open_position["regime_at_entry"],
                            signal_confidence=open_position["signal_confidence"],
                            r_multiple=r_multiple,
                        )
                    )
                    open_position = None
                    day_start_equity = equity  # reset after trade close

            # --- Step 3: Generate signal if no open position ---
            if open_position is None and pending_entry is None and i >= min_bars:
                bar_window = bars[max(0, i - 500) : i + 1]  # last 500 bars for context
                regime = self.regime_classifier.classify(bar_window, config.symbol)
                result = self.strategy.evaluate(config.symbol, bar_window, regime)

                if result.signal is not None:
                    sig = result.signal
                    # Compute position size based on risk
                    entry_est = sig.entry_price if sig.entry_price is not None else bar.close
                    risk_per_share = abs(float(entry_est) - float(sig.stop_price))
                    if risk_per_share > 0:
                        risk_amount = equity * Decimal(str(config.risk_per_trade_pct))
                        shares = max(1, int(float(risk_amount) / risk_per_share))

                        # Check concentration: notional <= 25% of equity
                        notional = entry_est * shares
                        if notional > equity * Decimal("0.25"):
                            shares = max(1, int(float(equity * Decimal("0.25")) / float(entry_est)))

                        pending_entry = {
                            "side": sig.side.value if hasattr(sig.side, "value") else str(sig.side),
                            "stop_price": sig.stop_price,
                            "target_price": sig.target_price,
                            "shares": shares,
                            "regime_at_entry": regime.label.value
                            if hasattr(regime.label, "value")
                            else str(regime.label),
                            "signal_confidence": sig.confidence,
                            "max_hold_bars": sig.max_hold_bars,
                            "initial_risk": Decimal(str(risk_per_share)) * shares,
                        }

            # --- Step 4: Mark-to-market equity and record equity curve ---
            mark_equity = equity
            if open_position is not None:
                # Unrealized PnL at close
                if open_position["side"] == "buy":
                    unrealized = (bar.close - open_position["entry_price"]) * open_position["shares"]
                else:
                    unrealized = (open_position["entry_price"] - bar.close) * open_position["shares"]
                mark_equity = equity + unrealized

            equity_curve.append((bar_date, mark_equity))

        # --- Close any remaining open position at end of data ---
        if open_position is not None:
            last_bar = bars[-1]
            last_date = last_bar.timestamp.date() if hasattr(last_bar.timestamp, "date") else last_bar.timestamp
            exit_price = last_bar.close

            if open_position["side"] == "buy":
                pnl = (exit_price - open_position["entry_price"]) * open_position["shares"]
            else:
                pnl = (open_position["entry_price"] - exit_price) * open_position["shares"]

            commission = config.commission_per_share * open_position["shares"]
            pnl -= commission
            equity += pnl

            initial_risk = open_position["initial_risk"]
            r_multiple = float(pnl / initial_risk) if initial_risk != 0 else 0.0
            entry_notional = open_position["entry_price"] * open_position["shares"]
            pnl_pct = float(pnl / entry_notional) if entry_notional != 0 else 0.0

            trades.append(
                BacktestTrade(
                    symbol=config.symbol,
                    strategy=self.strategy.name,
                    entry_date=open_position["entry_date"],
                    exit_date=last_date,
                    side=open_position["side"],
                    entry_price=open_position["entry_price"],
                    exit_price=exit_price,
                    shares=open_position["shares"],
                    realized_pnl=pnl,
                    pnl_pct=pnl_pct,
                    hold_bars=len(bars) - 1 - open_position["bar_index"],
                    exit_reason="end_of_data",
                    regime_at_entry=open_position["regime_at_entry"],
                    signal_confidence=open_position["signal_confidence"],
                    r_multiple=r_multiple,
                )
            )
            if equity_curve:
                equity_curve[-1] = (equity_curve[-1][0], equity)

        stats = compute_stats(trades, equity_curve, config)

        return BacktestResult(
            config=config,
            trades=trades,
            equity_curve=equity_curve,
            stats=stats,
            ran_at=datetime.now(tz=UTC),
        )

    def _update_open_position(self, position: dict, bar: Bar) -> dict | None:
        """Check if stop or target was hit. Returns closed position dict or None."""
        side = position["side"]
        stop = position["stop_price"]
        target = position["target_price"]
        shares = position["shares"]
        entry_price = position["entry_price"]
        _max_hold_bars = position.get("max_hold_bars")
        _bars_held = bar  # placeholder — actual hold_bars tracked in run()

        if side == "buy":
            # Stop hit: low touched stop
            if bar.low <= stop:
                exit_price = stop  # fill at stop (conservative)
                pnl = (exit_price - entry_price) * shares
                return {"exit_price": exit_price, "realized_pnl": pnl, "exit_reason": "stop", "shares": shares}
            # Target hit: high touched target
            if bar.high >= target:
                exit_price = target
                pnl = (exit_price - entry_price) * shares
                return {"exit_price": exit_price, "realized_pnl": pnl, "exit_reason": "target", "shares": shares}
        else:  # sell/short
            if bar.high >= stop:
                exit_price = stop
                pnl = (entry_price - exit_price) * shares
                return {"exit_price": exit_price, "realized_pnl": pnl, "exit_reason": "stop", "shares": shares}
            if bar.low <= target:
                exit_price = target
                pnl = (entry_price - exit_price) * shares
                return {"exit_price": exit_price, "realized_pnl": pnl, "exit_reason": "target", "shares": shares}

        return None

    def _compute_fill_price(self, bar: Bar, side: str) -> Decimal:
        """Fill at bar open + slippage. Conservative: buy at open*(1+slip), sell at open*(1-slip)."""
        slip = Decimal(str(self.config.slippage_bps)) / Decimal("10000")
        if side == "buy":
            return bar.open * (Decimal("1") + slip)
        else:
            return bar.open * (Decimal("1") - slip)
