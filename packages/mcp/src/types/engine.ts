// TypeScript interfaces mirroring Python engine Pydantic models

export type RegimeLabel =
  | 'trending_bull'
  | 'trending_bear'
  | 'mean_reverting'
  | 'high_vol_unstable'
  | 'low_liquidity'
  | 'risk_on'
  | 'risk_off'
  | 'opening_noise'
  | 'event_distorted'
  | 'unknown';

export type AssetClass = 'equity' | 'crypto' | 'etf';

export type OrderSide = 'buy' | 'sell';

export type OrderType = 'market' | 'limit' | 'stop' | 'stop_limit';

export type OrderStatus =
  | 'pending'
  | 'accepted'
  | 'filled'
  | 'partially_filled'
  | 'cancelled'
  | 'rejected';

export type StrategyState =
  | 'research'
  | 'paper'
  | 'shadow'
  | 'live'
  | 'suspended'
  | 'retired';

// ─── Watchlist ────────────────────────────────────────────────────────────────

export interface WatchlistEntry {
  id: string;
  symbol: string;
  asset_class: AssetClass;
  group_tags: string[];
  notes: string | null;
  added_at: string;
  is_active: boolean;
}

// ─── Market ───────────────────────────────────────────────────────────────────

export interface Bar {
  symbol: string;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  vwap: number | null;
}

export interface Quote {
  symbol: string;
  timestamp: string;
  bid: number;
  ask: number;
  bid_size: number;
  ask_size: number;
  spread_bps: number;
  mid: number;
}

export interface MarketSnapshot {
  symbol: string;
  quote: Quote;
  latest_bar: Bar;
  daily_bar: Bar | null;
}

// ─── Regime ───────────────────────────────────────────────────────────────────

export interface RegimeSnapshot {
  label: RegimeLabel;
  confidence: number;
  tradeability_score: number;
  supporting_metrics: Record<string, number>;
  strategy_compatibility: Record<string, number>;
  classified_at: string;
  bars_analyzed: number;
  reasoning: string;
}

// ─── Strategy ─────────────────────────────────────────────────────────────────

export interface StrategySignal {
  direction: 'long' | 'short' | 'flat';
  confidence: number;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  reasoning: string;
}

export interface StrategyResult {
  symbol: string;
  strategy: string;
  signal: StrategySignal;
  regime_compatibility: number;
  scanned_at: string;
}

// ─── Risk ─────────────────────────────────────────────────────────────────────

export interface RiskCheckResult {
  check_name: string;
  passed: boolean;
  message: string;
  value: number | null;
  threshold: number | null;
}

export interface RiskAssessment {
  approved: boolean;
  checks: RiskCheckResult[];
  position_size_shares: number | null;
  position_size_dollars: number | null;
  risk_reward_ratio: number | null;
  max_loss_dollars: number | null;
  notes: string;
}

export interface KillSwitchState {
  global_halt: boolean;
  halted_strategies: string[];
  halted_symbols: string[];
  halt_reason: string | null;
  halted_at: string | null;
  halted_by: string | null;
}

export interface TradeValidationParams {
  symbol: string;
  side: OrderSide;
  shares: number;
  entry_price: number;
  stop_price: number;
  strategy_name: string;
}

// ─── Portfolio ────────────────────────────────────────────────────────────────

export interface Position {
  symbol: string;
  side: OrderSide;
  quantity: number;
  avg_entry_price: number;
  current_price: number;
  market_value: number;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  strategy_id: string | null;
  opened_at: string;
}

export interface PortfolioState {
  account_value: number;
  cash: number;
  equity: number;
  daily_pnl: number;
  daily_pnl_pct: number;
  total_pnl: number;
  open_positions: Position[];
  position_count: number;
  buying_power: number;
  updated_at: string;
}

// ─── Execution ────────────────────────────────────────────────────────────────

export interface OrderParams {
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: number;
  limit_price?: number;
  stop_price?: number;
  strategy_id?: string;
}

export interface OrderResult {
  order_id: string;
  symbol: string;
  side: OrderSide;
  order_type: OrderType;
  quantity: number;
  filled_quantity: number;
  limit_price: number | null;
  stop_price: number | null;
  avg_fill_price: number | null;
  status: OrderStatus;
  strategy_id: string | null;
  submitted_at: string;
  filled_at: string | null;
  cancelled_at: string | null;
  reject_reason: string | null;
}

export interface FlattenResult {
  orders_submitted: number;
  symbols_flattened: string[];
  errors: string[];
  initiated_at: string;
}

// ─── Governance ───────────────────────────────────────────────────────────────

export interface StrategyRecord {
  strategy: string;
  state: StrategyState;
  promoted_at: string | null;
  promoted_by: string | null;
  suspended_at: string | null;
  suspend_reason: string | null;
  notes: string | null;
  paper_days: number;
  live_days: number;
}

export interface PromotionCriterion {
  name: string;
  met: boolean;
  required_value: number | null;
  actual_value: number | null;
  description: string;
}

export interface PromotionEvaluation {
  strategy: string;
  target_state: StrategyState;
  eligible: boolean;
  criteria: PromotionCriterion[];
  overall_score: number;
  gaps: string[];
  evaluated_at: string;
}

export interface DriftMetric {
  metric: string;
  baseline_value: number;
  recent_value: number;
  drift_pct: number;
  is_significant: boolean;
}

export interface DriftReport {
  strategy: string;
  has_drift: boolean;
  drift_metrics: DriftMetric[];
  recommendation: string;
  analyzed_at: string;
}

// ─── Audit ────────────────────────────────────────────────────────────────────

export interface AuditEvent {
  event_id: string;
  event_type: string;
  symbol: string | null;
  strategy: string | null;
  description: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface TradeExplanation {
  event_id: string;
  symbol: string;
  strategy: string;
  narrative: string;
  regime_at_decision: RegimeSnapshot | null;
  risk_assessment: RiskAssessment | null;
  signal: StrategySignal | null;
  order: OrderResult | null;
  created_at: string;
}

export interface TradeRecord {
  trade_id: string;
  symbol: string;
  strategy: string | null;
  side: OrderSide;
  quantity: number;
  entry_price: number;
  exit_price: number | null;
  realized_pnl: number | null;
  opened_at: string;
  closed_at: string | null;
}

export interface DailySummary {
  date: string;
  trades_taken: number;
  trades_won: number;
  trades_lost: number;
  gross_pnl: number;
  net_pnl: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  strategies_active: string[];
  notable_events: string[];
}

export interface WeeklySummary {
  week_ending: string;
  trading_days: number;
  total_trades: number;
  gross_pnl: number;
  net_pnl: number;
  win_rate: number;
  sharpe_ratio: number | null;
  max_drawdown: number;
  best_day_pnl: number;
  worst_day_pnl: number;
  strategies_summary: Record<string, DailySummary>;
}

export interface StrategyScorecard {
  strategy: string;
  days_analyzed: number;
  total_trades: number;
  win_rate: number;
  avg_win_loss_ratio: number;
  gross_pnl: number;
  net_pnl: number;
  sharpe_ratio: number | null;
  max_drawdown: number;
  profit_factor: number;
  avg_holding_period_hours: number;
  regime_performance: Record<string, number>;
  generated_at: string;
}
