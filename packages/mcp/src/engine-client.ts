import axios, { AxiosInstance, AxiosError } from 'axios';
import { logger } from './logger.js';
import { CircuitBreaker, withRetry } from './resilience.js';
import type { CircuitState } from './resilience.js';
import type {
  WatchlistEntry,
  MarketSnapshot,
  Bar,
  RegimeSnapshot,
  StrategyResult,
  TradeValidationParams,
  RiskAssessment,
  KillSwitchState,
  PortfolioState,
  Position,
  OrderParams,
  OrderResult,
  FlattenResult,
  StrategyRecord,
  PromotionEvaluation,
  DriftReport,
  AuditEvent,
  TradeExplanation,
  TradeRecord,
  DailySummary,
  WeeklySummary,
  StrategyScorecard,
} from './types/engine.js';

export class EngineClient {
  private readonly http: AxiosInstance;
  private readonly circuitBreaker = new CircuitBreaker(5, 30_000);

  private async request<T>(fn: () => Promise<T>): Promise<T> {
    return this.circuitBreaker.execute(() => withRetry(fn, 2, 300));
  }

  getCircuitState(): CircuitState {
    return this.circuitBreaker.getState();
  }

  constructor(baseUrl: string, timeoutMs: number) {
    this.http = axios.create({
      baseURL: baseUrl,
      timeout: timeoutMs,
      headers: { 'Content-Type': 'application/json' },
    });

    this.http.interceptors.request.use((req) => {
      logger.debug({ method: req.method, url: req.url }, 'engine request');
      return req;
    });

    this.http.interceptors.response.use(
      (res) => {
        logger.debug({ status: res.status, url: res.config.url }, 'engine response');
        return res;
      },
      (err: AxiosError) => {
        logger.warn(
          { status: err.response?.status, url: err.config?.url, message: err.message },
          'engine error',
        );
        return Promise.reject(err);
      },
    );
  }

  // ─── Watchlist ─────────────────────────────────────────────────────────────

  async addSymbols(symbols: string[], group?: string, notes?: string): Promise<WatchlistEntry[]> {
    return this.request(() =>
      this.http.post<WatchlistEntry[]>('/watchlist/add', { symbols, group, notes }).then((r) => r.data),
    );
  }

  async removeSymbols(symbols: string[]): Promise<{ removed: number }> {
    return this.request(() =>
      this.http.post<{ removed: number }>('/watchlist/remove', { symbols }).then((r) => r.data),
    );
  }

  async listSymbols(group?: string, activeOnly?: boolean): Promise<WatchlistEntry[]> {
    return this.request(() =>
      this.http
        .get<WatchlistEntry[]>('/watchlist', { params: { group, active_only: activeOnly } })
        .then((r) => r.data),
    );
  }

  async getGroups(): Promise<string[]> {
    return this.request(() =>
      this.http.get<string[]>('/watchlist/groups').then((r) => r.data),
    );
  }

  async tagSymbols(symbols: string[], group: string): Promise<{ tagged: number }> {
    return this.request(() =>
      this.http.post<{ tagged: number }>('/watchlist/tag', { symbols, group }).then((r) => r.data),
    );
  }

  // ─── Market ────────────────────────────────────────────────────────────────

  async getSnapshot(symbol: string): Promise<MarketSnapshot> {
    return this.request(() =>
      this.http.get<MarketSnapshot>(`/market/snapshot/${symbol}`).then((r) => r.data),
    );
  }

  async getSnapshots(symbols: string[]): Promise<Record<string, MarketSnapshot>> {
    return this.request(() =>
      this.http
        .post<Record<string, MarketSnapshot>>('/market/snapshots', { symbols })
        .then((r) => r.data),
    );
  }

  async getBars(symbol: string, timeframe: string, limit: number): Promise<Bar[]> {
    return this.request(() =>
      this.http
        .get<Bar[]>(`/market/bars/${symbol}`, { params: { timeframe, limit } })
        .then((r) => r.data),
    );
  }

  async marketHealth(): Promise<{ status: string; provider: string; latency_ms: number }> {
    return this.request(() =>
      this.http
        .get<{ status: string; provider: string; latency_ms: number }>('/market/health')
        .then((r) => r.data),
    );
  }

  // ─── Regime ────────────────────────────────────────────────────────────────

  async evaluateRegime(symbol: string, timeframe?: string): Promise<RegimeSnapshot> {
    return this.request(() =>
      this.http
        .get<RegimeSnapshot>(`/regime/evaluate/${symbol}`, { params: { timeframe } })
        .then((r) => r.data),
    );
  }

  async evaluateRegimeBulk(symbols: string[]): Promise<Record<string, RegimeSnapshot>> {
    return this.request(() =>
      this.http
        .post<Record<string, RegimeSnapshot>>('/regime/evaluate/bulk', { symbols })
        .then((r) => r.data),
    );
  }

  // ─── Strategy ──────────────────────────────────────────────────────────────

  async scanWatchlist(group?: string, strategies?: string[]): Promise<StrategyResult[]> {
    return this.request(() =>
      this.http
        .post<StrategyResult[]>('/strategy/scan/watchlist', { group, strategies })
        .then((r) => r.data),
    );
  }

  async scanSymbol(symbol: string, strategies?: string[]): Promise<StrategyResult[]> {
    return this.request(() =>
      this.http
        .post<StrategyResult[]>(`/strategy/scan/${symbol}`, { strategies })
        .then((r) => r.data),
    );
  }

  async listStrategies(): Promise<string[]> {
    return this.request(() =>
      this.http.get<string[]>('/strategy/list').then((r) => r.data),
    );
  }

  // ─── Risk ──────────────────────────────────────────────────────────────────

  async validateTrade(params: TradeValidationParams): Promise<RiskAssessment> {
    return this.request(() =>
      this.http.post<RiskAssessment>('/risk/validate', params).then((r) => r.data),
    );
  }

  async getKillSwitchState(): Promise<KillSwitchState> {
    return this.request(() =>
      this.http.get<KillSwitchState>('/risk/kill-switch').then((r) => r.data),
    );
  }

  async engageGlobalHalt(reason: string): Promise<void> {
    return this.request(() =>
      this.http.post('/risk/halt/engage', { reason }).then(() => undefined),
    );
  }

  async disengageGlobalHalt(): Promise<void> {
    return this.request(() =>
      this.http.post('/risk/halt/disengage').then(() => undefined),
    );
  }

  async haltStrategy(strategy: string, reason: string): Promise<void> {
    return this.request(() =>
      this.http.post('/risk/halt/strategy', { strategy, reason }).then(() => undefined),
    );
  }

  async haltSymbol(symbol: string, reason: string): Promise<void> {
    return this.request(() =>
      this.http.post('/risk/halt/symbol', { symbol, reason }).then(() => undefined),
    );
  }

  // ─── Portfolio ─────────────────────────────────────────────────────────────

  async getPortfolioStatus(): Promise<PortfolioState> {
    return this.request(() =>
      this.http.get<PortfolioState>('/portfolio/status').then((r) => r.data),
    );
  }

  async getPositions(): Promise<Position[]> {
    return this.request(() =>
      this.http.get<Position[]>('/portfolio/positions').then((r) => r.data),
    );
  }

  async getAccountValue(): Promise<{ value: number; cash: number; equity: number }> {
    return this.request(() =>
      this.http
        .get<{ value: number; cash: number; equity: number }>('/portfolio/account')
        .then((r) => r.data),
    );
  }

  // ─── Execution ─────────────────────────────────────────────────────────────

  async submitPaperOrder(params: OrderParams): Promise<OrderResult> {
    return this.request(() =>
      this.http.post<OrderResult>('/execution/paper/order', params).then((r) => r.data),
    );
  }

  async cancelOrder(orderId: string, reason: string): Promise<OrderResult> {
    return this.request(() =>
      this.http
        .post<OrderResult>(`/execution/order/${orderId}/cancel`, { reason })
        .then((r) => r.data),
    );
  }

  async getOrder(orderId: string): Promise<OrderResult> {
    return this.request(() =>
      this.http.get<OrderResult>(`/execution/order/${orderId}`).then((r) => r.data),
    );
  }

  async flattenAll(reason: string): Promise<FlattenResult> {
    return this.request(() =>
      this.http.post<FlattenResult>('/execution/flatten-all', { reason }).then((r) => r.data),
    );
  }

  async resetPaperAccount(startingCash?: number): Promise<void> {
    return this.request(() =>
      this.http
        .post('/execution/paper/reset', { starting_cash: startingCash })
        .then(() => undefined),
    );
  }

  // ─── Governance ────────────────────────────────────────────────────────────

  async evaluatePromotion(strategy: string, targetState: string): Promise<PromotionEvaluation> {
    return this.request(() =>
      this.http
        .post<PromotionEvaluation>('/governance/evaluate-promotion', {
          strategy,
          target_state: targetState,
        })
        .then((r) => r.data),
    );
  }

  async promoteStrategy(
    strategy: string,
    targetState: string,
    approvedBy: string,
    notes?: string,
  ): Promise<StrategyRecord> {
    return this.request(() =>
      this.http
        .post<StrategyRecord>('/governance/promote', {
          strategy,
          target_state: targetState,
          approved_by: approvedBy,
          notes,
        })
        .then((r) => r.data),
    );
  }

  async suspendStrategy(strategy: string, reason: string): Promise<StrategyRecord> {
    return this.request(() =>
      this.http
        .post<StrategyRecord>('/governance/suspend', { strategy, reason })
        .then((r) => r.data),
    );
  }

  async listStrategyStates(): Promise<StrategyRecord[]> {
    return this.request(() =>
      this.http.get<StrategyRecord[]>('/governance/strategies').then((r) => r.data),
    );
  }

  async checkDrift(strategy: string): Promise<DriftReport> {
    return this.request(() =>
      this.http.get<DriftReport>(`/governance/drift/${strategy}`).then((r) => r.data),
    );
  }

  // ─── Audit ─────────────────────────────────────────────────────────────────

  async explainTrade(auditEventId: string): Promise<TradeExplanation> {
    return this.request(() =>
      this.http.get<TradeExplanation>(`/audit/explain/${auditEventId}`).then((r) => r.data),
    );
  }

  async getRecentEvents(
    limit?: number,
    symbol?: string,
    strategy?: string,
  ): Promise<AuditEvent[]> {
    return this.request(() =>
      this.http
        .get<AuditEvent[]>('/audit/events', { params: { limit, symbol, strategy } })
        .then((r) => r.data),
    );
  }

  async getDailySummary(date?: string): Promise<DailySummary> {
    return this.request(() =>
      this.http
        .get<DailySummary>('/audit/summary/daily', { params: { date } })
        .then((r) => r.data),
    );
  }

  async getWeeklySummary(weekEnding?: string): Promise<WeeklySummary> {
    return this.request(() =>
      this.http
        .get<WeeklySummary>('/audit/summary/weekly', { params: { week_ending: weekEnding } })
        .then((r) => r.data),
    );
  }

  async getStrategyScorecard(strategy: string, days?: number): Promise<StrategyScorecard> {
    return this.request(() =>
      this.http
        .get<StrategyScorecard>(`/audit/scorecard/${strategy}`, { params: { days } })
        .then((r) => r.data),
    );
  }

  async getTradeBlotter(start: string, end: string, strategy?: string): Promise<TradeRecord[]> {
    return this.request(() =>
      this.http
        .get<TradeRecord[]>('/audit/blotter', { params: { start, end, strategy } })
        .then((r) => r.data),
    );
  }

  // ─── Health ────────────────────────────────────────────────────────────────

  async healthCheck(): Promise<{ status: string; env: string }> {
    return this.request(() =>
      this.http.get<{ status: string; env: string }>('/health').then((r) => r.data),
    );
  }
}
