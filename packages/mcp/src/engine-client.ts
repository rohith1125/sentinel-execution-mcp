import axios, { AxiosInstance, AxiosError } from 'axios';
import { logger } from './logger.js';
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
    const res = await this.http.post<WatchlistEntry[]>('/watchlist/add', { symbols, group, notes });
    return res.data;
  }

  async removeSymbols(symbols: string[]): Promise<{ removed: number }> {
    const res = await this.http.post<{ removed: number }>('/watchlist/remove', { symbols });
    return res.data;
  }

  async listSymbols(group?: string, activeOnly?: boolean): Promise<WatchlistEntry[]> {
    const res = await this.http.get<WatchlistEntry[]>('/watchlist', {
      params: { group, active_only: activeOnly },
    });
    return res.data;
  }

  async getGroups(): Promise<string[]> {
    const res = await this.http.get<string[]>('/watchlist/groups');
    return res.data;
  }

  async tagSymbols(symbols: string[], group: string): Promise<{ tagged: number }> {
    const res = await this.http.post<{ tagged: number }>('/watchlist/tag', { symbols, group });
    return res.data;
  }

  // ─── Market ────────────────────────────────────────────────────────────────

  async getSnapshot(symbol: string): Promise<MarketSnapshot> {
    const res = await this.http.get<MarketSnapshot>(`/market/snapshot/${symbol}`);
    return res.data;
  }

  async getSnapshots(symbols: string[]): Promise<Record<string, MarketSnapshot>> {
    const res = await this.http.post<Record<string, MarketSnapshot>>('/market/snapshots', {
      symbols,
    });
    return res.data;
  }

  async getBars(symbol: string, timeframe: string, limit: number): Promise<Bar[]> {
    const res = await this.http.get<Bar[]>(`/market/bars/${symbol}`, {
      params: { timeframe, limit },
    });
    return res.data;
  }

  async marketHealth(): Promise<{ status: string; provider: string; latency_ms: number }> {
    const res = await this.http.get<{ status: string; provider: string; latency_ms: number }>(
      '/market/health',
    );
    return res.data;
  }

  // ─── Regime ────────────────────────────────────────────────────────────────

  async evaluateRegime(symbol: string, timeframe?: string): Promise<RegimeSnapshot> {
    const res = await this.http.get<RegimeSnapshot>(`/regime/evaluate/${symbol}`, {
      params: { timeframe },
    });
    return res.data;
  }

  async evaluateRegimeBulk(symbols: string[]): Promise<Record<string, RegimeSnapshot>> {
    const res = await this.http.post<Record<string, RegimeSnapshot>>('/regime/evaluate/bulk', {
      symbols,
    });
    return res.data;
  }

  // ─── Strategy ──────────────────────────────────────────────────────────────

  async scanWatchlist(group?: string, strategies?: string[]): Promise<StrategyResult[]> {
    const res = await this.http.post<StrategyResult[]>('/strategy/scan/watchlist', {
      group,
      strategies,
    });
    return res.data;
  }

  async scanSymbol(symbol: string, strategies?: string[]): Promise<StrategyResult[]> {
    const res = await this.http.post<StrategyResult[]>(`/strategy/scan/${symbol}`, { strategies });
    return res.data;
  }

  async listStrategies(): Promise<string[]> {
    const res = await this.http.get<string[]>('/strategy/list');
    return res.data;
  }

  // ─── Risk ──────────────────────────────────────────────────────────────────

  async validateTrade(params: TradeValidationParams): Promise<RiskAssessment> {
    const res = await this.http.post<RiskAssessment>('/risk/validate', params);
    return res.data;
  }

  async getKillSwitchState(): Promise<KillSwitchState> {
    const res = await this.http.get<KillSwitchState>('/risk/kill-switch');
    return res.data;
  }

  async engageGlobalHalt(reason: string): Promise<void> {
    await this.http.post('/risk/halt/engage', { reason });
  }

  async disengageGlobalHalt(): Promise<void> {
    await this.http.post('/risk/halt/disengage');
  }

  async haltStrategy(strategy: string, reason: string): Promise<void> {
    await this.http.post('/risk/halt/strategy', { strategy, reason });
  }

  async haltSymbol(symbol: string, reason: string): Promise<void> {
    await this.http.post('/risk/halt/symbol', { symbol, reason });
  }

  // ─── Portfolio ─────────────────────────────────────────────────────────────

  async getPortfolioStatus(): Promise<PortfolioState> {
    const res = await this.http.get<PortfolioState>('/portfolio/status');
    return res.data;
  }

  async getPositions(): Promise<Position[]> {
    const res = await this.http.get<Position[]>('/portfolio/positions');
    return res.data;
  }

  async getAccountValue(): Promise<{ value: number; cash: number; equity: number }> {
    const res = await this.http.get<{ value: number; cash: number; equity: number }>(
      '/portfolio/account',
    );
    return res.data;
  }

  // ─── Execution ─────────────────────────────────────────────────────────────

  async submitPaperOrder(params: OrderParams): Promise<OrderResult> {
    const res = await this.http.post<OrderResult>('/execution/paper/order', params);
    return res.data;
  }

  async cancelOrder(orderId: string, reason: string): Promise<OrderResult> {
    const res = await this.http.post<OrderResult>(`/execution/order/${orderId}/cancel`, { reason });
    return res.data;
  }

  async getOrder(orderId: string): Promise<OrderResult> {
    const res = await this.http.get<OrderResult>(`/execution/order/${orderId}`);
    return res.data;
  }

  async flattenAll(reason: string): Promise<FlattenResult> {
    const res = await this.http.post<FlattenResult>('/execution/flatten-all', { reason });
    return res.data;
  }

  async resetPaperAccount(startingCash?: number): Promise<void> {
    await this.http.post('/execution/paper/reset', { starting_cash: startingCash });
  }

  // ─── Governance ────────────────────────────────────────────────────────────

  async evaluatePromotion(strategy: string, targetState: string): Promise<PromotionEvaluation> {
    const res = await this.http.post<PromotionEvaluation>('/governance/evaluate-promotion', {
      strategy,
      target_state: targetState,
    });
    return res.data;
  }

  async promoteStrategy(
    strategy: string,
    targetState: string,
    approvedBy: string,
    notes?: string,
  ): Promise<StrategyRecord> {
    const res = await this.http.post<StrategyRecord>('/governance/promote', {
      strategy,
      target_state: targetState,
      approved_by: approvedBy,
      notes,
    });
    return res.data;
  }

  async suspendStrategy(strategy: string, reason: string): Promise<StrategyRecord> {
    const res = await this.http.post<StrategyRecord>('/governance/suspend', { strategy, reason });
    return res.data;
  }

  async listStrategyStates(): Promise<StrategyRecord[]> {
    const res = await this.http.get<StrategyRecord[]>('/governance/strategies');
    return res.data;
  }

  async checkDrift(strategy: string): Promise<DriftReport> {
    const res = await this.http.get<DriftReport>(`/governance/drift/${strategy}`);
    return res.data;
  }

  // ─── Audit ─────────────────────────────────────────────────────────────────

  async explainTrade(auditEventId: string): Promise<TradeExplanation> {
    const res = await this.http.get<TradeExplanation>(`/audit/explain/${auditEventId}`);
    return res.data;
  }

  async getRecentEvents(
    limit?: number,
    symbol?: string,
    strategy?: string,
  ): Promise<AuditEvent[]> {
    const res = await this.http.get<AuditEvent[]>('/audit/events', {
      params: { limit, symbol, strategy },
    });
    return res.data;
  }

  async getDailySummary(date?: string): Promise<DailySummary> {
    const res = await this.http.get<DailySummary>('/audit/summary/daily', { params: { date } });
    return res.data;
  }

  async getWeeklySummary(weekEnding?: string): Promise<WeeklySummary> {
    const res = await this.http.get<WeeklySummary>('/audit/summary/weekly', {
      params: { week_ending: weekEnding },
    });
    return res.data;
  }

  async getStrategyScorecard(strategy: string, days?: number): Promise<StrategyScorecard> {
    const res = await this.http.get<StrategyScorecard>(`/audit/scorecard/${strategy}`, {
      params: { days },
    });
    return res.data;
  }

  async getTradeBlotter(start: string, end: string, strategy?: string): Promise<TradeRecord[]> {
    const res = await this.http.get<TradeRecord[]>('/audit/blotter', {
      params: { start, end, strategy },
    });
    return res.data;
  }

  // ─── Health ────────────────────────────────────────────────────────────────

  async healthCheck(): Promise<{ status: string; env: string }> {
    const res = await this.http.get<{ status: string; env: string }>('/health');
    return res.data;
  }
}
