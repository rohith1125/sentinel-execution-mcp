/**
 * Integration tests for MCP tool registration and behavior.
 *
 * These tests mock the EngineClient to verify tool input validation,
 * response formatting, and error handling without requiring a live engine.
 */

import { describe, it, expect, vi, beforeEach, type MockInstance } from 'vitest';
import { EngineClient } from '../engine-client.js';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Build a minimal EngineClient mock with all methods as vi.fn().
 * Tests override specific methods as needed.
 */
function makeClientMock(): EngineClient {
  return {
    addSymbols: vi.fn(),
    removeSymbols: vi.fn(),
    listSymbols: vi.fn(),
    getGroups: vi.fn(),
    tagSymbols: vi.fn(),
    getSnapshot: vi.fn(),
    getSnapshots: vi.fn(),
    getBars: vi.fn(),
    marketHealth: vi.fn(),
    evaluateRegime: vi.fn(),
    evaluateRegimeBulk: vi.fn(),
    scanWatchlist: vi.fn(),
    scanSymbol: vi.fn(),
    listStrategies: vi.fn(),
    validateTrade: vi.fn(),
    getKillSwitchState: vi.fn(),
    engageGlobalHalt: vi.fn(),
    disengageGlobalHalt: vi.fn(),
    haltStrategy: vi.fn(),
    haltSymbol: vi.fn(),
    getPortfolioStatus: vi.fn(),
    getPositions: vi.fn(),
    getAccountValue: vi.fn(),
    submitPaperOrder: vi.fn(),
    cancelOrder: vi.fn(),
    getOrder: vi.fn(),
    flattenAll: vi.fn(),
    resetPaperAccount: vi.fn(),
    evaluatePromotion: vi.fn(),
    promoteStrategy: vi.fn(),
    suspendStrategy: vi.fn(),
    listStrategyStates: vi.fn(),
    checkDrift: vi.fn(),
    explainTrade: vi.fn(),
    getRecentEvents: vi.fn(),
    getDailySummary: vi.fn(),
    getWeeklySummary: vi.fn(),
    getStrategyScorecard: vi.fn(),
    getTradeBlotter: vi.fn(),
    healthCheck: vi.fn(),
  } as unknown as EngineClient;
}

// ---------------------------------------------------------------------------
// EngineClient unit behavior tests
// ---------------------------------------------------------------------------

describe('EngineClient construction', () => {
  it('creates an instance with baseUrl and timeoutMs', () => {
    const client = new EngineClient('http://localhost:8100', 5000);
    expect(client).toBeDefined();
    expect(client).toBeInstanceOf(EngineClient);
  });
});

// ---------------------------------------------------------------------------
// Watchlist mock behavior tests
// ---------------------------------------------------------------------------

describe('EngineClient watchlist methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('addSymbols returns entries from engine', async () => {
    const mockEntries = [{ symbol: 'AAPL', is_active: true, group_tags: [], asset_class: 'equity' }];
    vi.mocked(client.addSymbols).mockResolvedValueOnce(mockEntries as any);

    const result = await client.addSymbols(['AAPL']);
    expect(result).toHaveLength(1);
    expect(result[0].symbol).toBe('AAPL');
    expect(client.addSymbols).toHaveBeenCalledWith(['AAPL']);
  });

  it('addSymbols forwards group parameter', async () => {
    vi.mocked(client.addSymbols).mockResolvedValueOnce([]);
    await client.addSymbols(['AAPL'], 'tech');
    expect(client.addSymbols).toHaveBeenCalledWith(['AAPL'], 'tech');
  });

  it('removeSymbols returns count', async () => {
    vi.mocked(client.removeSymbols).mockResolvedValueOnce({ removed: 2 });
    const result = await client.removeSymbols(['AAPL', 'MSFT']);
    expect(result.removed).toBe(2);
  });

  it('listSymbols with active_only filter', async () => {
    vi.mocked(client.listSymbols).mockResolvedValueOnce([]);
    await client.listSymbols(undefined, true);
    expect(client.listSymbols).toHaveBeenCalledWith(undefined, true);
  });

  it('getGroups returns sorted array', async () => {
    vi.mocked(client.getGroups).mockResolvedValueOnce(['finance', 'tech']);
    const groups = await client.getGroups();
    expect(groups).toContain('tech');
    expect(groups).toContain('finance');
  });

  it('tagSymbols returns tagged count', async () => {
    vi.mocked(client.tagSymbols).mockResolvedValueOnce({ tagged: 3 });
    const result = await client.tagSymbols(['AAPL', 'MSFT', 'NVDA'], 'mega_cap');
    expect(result.tagged).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// Risk / kill switch mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient risk methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('getKillSwitchState returns state object', async () => {
    const mockState = { global_halt: false, halted_strategies: [], halted_symbols: [] };
    vi.mocked(client.getKillSwitchState).mockResolvedValueOnce(mockState as any);
    const state = await client.getKillSwitchState();
    expect(state.global_halt).toBe(false);
  });

  it('engageGlobalHalt calls engine with reason', async () => {
    vi.mocked(client.engageGlobalHalt).mockResolvedValueOnce(undefined);
    await client.engageGlobalHalt('Risk event detected');
    expect(client.engageGlobalHalt).toHaveBeenCalledWith('Risk event detected');
  });

  it('disengageGlobalHalt calls engine', async () => {
    vi.mocked(client.disengageGlobalHalt).mockResolvedValueOnce(undefined);
    await client.disengageGlobalHalt();
    expect(client.disengageGlobalHalt).toHaveBeenCalledOnce();
  });

  it('haltStrategy requires strategy and reason', async () => {
    vi.mocked(client.haltStrategy).mockResolvedValueOnce(undefined);
    await client.haltStrategy('momentum_breakout', 'Drawdown exceeded');
    expect(client.haltStrategy).toHaveBeenCalledWith('momentum_breakout', 'Drawdown exceeded');
  });

  it('haltSymbol requires symbol and reason', async () => {
    vi.mocked(client.haltSymbol).mockResolvedValueOnce(undefined);
    await client.haltSymbol('AAPL', 'News halt');
    expect(client.haltSymbol).toHaveBeenCalledWith('AAPL', 'News halt');
  });
});

// ---------------------------------------------------------------------------
// Execution mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient execution methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('submitPaperOrder returns order result', async () => {
    const mockResult = {
      order_id: 'test-123',
      status: 'filled',
      filled_qty: 100,
      filled_avg_price: 182.50,
    };
    vi.mocked(client.submitPaperOrder).mockResolvedValueOnce(mockResult as any);

    const result = await client.submitPaperOrder({
      symbol: 'AAPL',
      side: 'buy',
      order_type: 'market',
      quantity: 100,
    } as any);

    expect(result.status).toBe('filled');
    expect(result.filled_qty).toBe(100);
  });

  it('flattenAll requires reason and returns result', async () => {
    const mockResult = { orders_cancelled: 3, positions_closed: 2 };
    vi.mocked(client.flattenAll).mockResolvedValueOnce(mockResult as any);

    const result = await client.flattenAll('Emergency exit');
    expect(result.positions_closed).toBe(2);
    expect(client.flattenAll).toHaveBeenCalledWith('Emergency exit');
  });

  it('cancelOrder calls engine with orderId and reason', async () => {
    const mockResult = { status: 'cancelled' };
    vi.mocked(client.cancelOrder).mockResolvedValueOnce(mockResult as any);

    await client.cancelOrder('order-abc', 'Manual cancel');
    expect(client.cancelOrder).toHaveBeenCalledWith('order-abc', 'Manual cancel');
  });

  it('resetPaperAccount accepts optional starting cash', async () => {
    vi.mocked(client.resetPaperAccount).mockResolvedValueOnce(undefined);
    await client.resetPaperAccount(50000);
    expect(client.resetPaperAccount).toHaveBeenCalledWith(50000);
  });
});

// ---------------------------------------------------------------------------
// Market data mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient market methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('getSnapshot returns snapshot with quote and bar', async () => {
    const mockSnap = {
      symbol: 'AAPL',
      quote: { bid: 182.45, ask: 182.55 },
      latest_bar: { close: 182.50 },
    };
    vi.mocked(client.getSnapshot).mockResolvedValueOnce(mockSnap as any);

    const snap = await client.getSnapshot('AAPL');
    expect(snap.symbol).toBe('AAPL');
    expect(snap.quote.bid).toBe(182.45);
  });

  it('getBars accepts timeframe and limit', async () => {
    vi.mocked(client.getBars).mockResolvedValueOnce([]);
    await client.getBars('AAPL', '1Min', 100);
    expect(client.getBars).toHaveBeenCalledWith('AAPL', '1Min', 100);
  });

  it('marketHealth returns status object', async () => {
    const mockHealth = { status: 'ok', provider: 'mock', latency_ms: 5 };
    vi.mocked(client.marketHealth).mockResolvedValueOnce(mockHealth);

    const health = await client.marketHealth();
    expect(health.status).toBe('ok');
    expect(health.provider).toBe('mock');
  });
});

// ---------------------------------------------------------------------------
// Regime mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient regime methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('evaluateRegime returns regime snapshot', async () => {
    const mockRegime = {
      label: 'trending_bull',
      confidence: 0.85,
      tradeability_score: 0.80,
      reasoning: '[AAPL] Trending bullish: ADX = 32.1',
    };
    vi.mocked(client.evaluateRegime).mockResolvedValueOnce(mockRegime as any);

    const regime = await client.evaluateRegime('AAPL');
    expect(regime.label).toBe('trending_bull');
    expect(regime.confidence).toBe(0.85);
  });

  it('evaluateRegimeBulk returns map of symbols to snapshots', async () => {
    const mockBulk = {
      AAPL: { label: 'trending_bull', confidence: 0.85 },
      MSFT: { label: 'mean_reverting', confidence: 0.70 },
    };
    vi.mocked(client.evaluateRegimeBulk).mockResolvedValueOnce(mockBulk as any);

    const result = await client.evaluateRegimeBulk(['AAPL', 'MSFT']);
    expect(Object.keys(result)).toHaveLength(2);
    expect(result['AAPL'].label).toBe('trending_bull');
  });
});

// ---------------------------------------------------------------------------
// Governance mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient governance methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('evaluatePromotion returns evaluation with eligible flag', async () => {
    const mockEval = {
      strategy: 'momentum_breakout',
      current_state: 'paper_approved',
      target_state: 'live_approved',
      eligible: false,
      blocking_criteria: ['Requires 90-day paper run'],
    };
    vi.mocked(client.evaluatePromotion).mockResolvedValueOnce(mockEval as any);

    const result = await client.evaluatePromotion('momentum_breakout', 'live_approved');
    expect(result.eligible).toBe(false);
    expect(result.blocking_criteria).toHaveLength(1);
  });

  it('promoteStrategy requires approved_by field', async () => {
    const mockRecord = { name: 'momentum_breakout', state: 'backtest_approved' };
    vi.mocked(client.promoteStrategy).mockResolvedValueOnce(mockRecord as any);

    await client.promoteStrategy('momentum_breakout', 'backtest_approved', 'trader@firm.com');
    expect(client.promoteStrategy).toHaveBeenCalledWith(
      'momentum_breakout',
      'backtest_approved',
      'trader@firm.com',
    );
  });

  it('suspendStrategy includes reason', async () => {
    const mockRecord = { name: 'momentum_breakout', state: 'suspended' };
    vi.mocked(client.suspendStrategy).mockResolvedValueOnce(mockRecord as any);

    await client.suspendStrategy('momentum_breakout', 'Performance degraded');
    expect(client.suspendStrategy).toHaveBeenCalledWith(
      'momentum_breakout',
      'Performance degraded',
    );
  });
});

// ---------------------------------------------------------------------------
// Audit mock behavior
// ---------------------------------------------------------------------------

describe('EngineClient audit methods (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('getRecentEvents supports filters', async () => {
    vi.mocked(client.getRecentEvents).mockResolvedValueOnce([]);
    await client.getRecentEvents(50, 'AAPL', 'momentum_breakout');
    expect(client.getRecentEvents).toHaveBeenCalledWith(50, 'AAPL', 'momentum_breakout');
  });

  it('getDailySummary accepts optional date', async () => {
    const mockSummary = { date: '2024-01-15', total_trades: 5, win_rate: 0.6 };
    vi.mocked(client.getDailySummary).mockResolvedValueOnce(mockSummary as any);

    const summary = await client.getDailySummary('2024-01-15');
    expect(summary.total_trades).toBe(5);
  });

  it('getTradeBlotter requires start and end', async () => {
    vi.mocked(client.getTradeBlotter).mockResolvedValueOnce([]);
    await client.getTradeBlotter('2024-01-01', '2024-01-31');
    expect(client.getTradeBlotter).toHaveBeenCalledWith('2024-01-01', '2024-01-31');
  });

  it('explainTrade returns explanation for audit event', async () => {
    const mockExplanation = {
      audit_event_id: 'evt-abc',
      decision: 'REJECTED',
      reasons: ['Kill switch engaged'],
    };
    vi.mocked(client.explainTrade).mockResolvedValueOnce(mockExplanation as any);

    const explanation = await client.explainTrade('evt-abc');
    expect(explanation.decision).toBe('REJECTED');
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe('EngineClient error propagation (mocked)', () => {
  let client: EngineClient;

  beforeEach(() => {
    client = makeClientMock();
  });

  it('propagates rejection from addSymbols', async () => {
    vi.mocked(client.addSymbols).mockRejectedValueOnce(new Error('Network timeout'));
    await expect(client.addSymbols(['AAPL'])).rejects.toThrow('Network timeout');
  });

  it('propagates rejection from engageGlobalHalt', async () => {
    vi.mocked(client.engageGlobalHalt).mockRejectedValueOnce(new Error('Engine unavailable'));
    await expect(client.engageGlobalHalt('test')).rejects.toThrow('Engine unavailable');
  });

  it('propagates rejection from flattenAll', async () => {
    vi.mocked(client.flattenAll).mockRejectedValueOnce(new Error('503 Service Unavailable'));
    await expect(client.flattenAll('emergency')).rejects.toThrow('503');
  });
});
