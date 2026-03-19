import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import type { EngineClient } from '../engine-client.js';
import { formatToolError as engineError } from '../errors.js';

const ISO_DATE = z.string().regex(/^\d{4}-\d{2}-\d{2}$/, 'Must be YYYY-MM-DD format');

export function registerAuditTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'audit.explain_trade',
    'Retrieve a full, human-readable explanation for a specific trade decision identified by its audit event ID. Returns the complete decision narrative including the regime state, risk assessment, strategy signal, and resulting order at the time the decision was made.',
    {
      audit_event_id: z.string().min(1).max(128),
    },
    async ({ audit_event_id }) => {
      try {
        const explanation = await client.explainTrade(audit_event_id);
        return {
          content: [{ type: 'text', text: JSON.stringify(explanation, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'audit.recent_events',
    'Retrieve the most recent audit events from the event log. Optionally filter by symbol or strategy to narrow results. Returns event type, description, metadata, and timestamps. Useful for reviewing recent system activity.',
    {
      limit: z.number().int().min(1).max(500).optional().default(50),
      symbol: z.string().min(1).max(10).optional(),
      strategy: z.string().min(1).max(64).optional(),
    },
    async ({ limit, symbol, strategy }) => {
      try {
        const events = await client.getRecentEvents(
          limit,
          symbol?.toUpperCase(),
          strategy,
        );
        return {
          content: [{ type: 'text', text: JSON.stringify(events, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'audit.daily_summary',
    'Get a structured summary of all trading activity for a given date (defaults to today). Includes trade counts, win/loss breakdown, gross and net P&L, win rate, average win/loss, profit factor, and notable events.',
    {
      date: ISO_DATE.optional(),
    },
    async ({ date }) => {
      try {
        const summary = await client.getDailySummary(date);
        return {
          content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'audit.weekly_summary',
    'Get a week-over-week trading summary ending on the specified date (defaults to the most recent completed week). Includes aggregate P&L, win rate, Sharpe ratio, max drawdown, best/worst day, and a per-strategy breakdown.',
    {
      week_ending: ISO_DATE.optional(),
    },
    async ({ week_ending }) => {
      try {
        const summary = await client.getWeeklySummary(week_ending);
        return {
          content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'audit.strategy_scorecard',
    'Generate a performance scorecard for a specific strategy over a configurable lookback window (default 30 days). Includes win rate, average win/loss ratio, P&L, Sharpe ratio, max drawdown, profit factor, average holding period, and regime-segmented performance.',
    {
      strategy: z.string().min(1).max(64),
      days: z.number().int().min(1).max(365).optional().default(30),
    },
    async ({ strategy, days }) => {
      try {
        const scorecard = await client.getStrategyScorecard(strategy, days);
        return {
          content: [{ type: 'text', text: JSON.stringify(scorecard, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'audit.trade_blotter',
    'Retrieve a chronological record of all trades within a date range. Optionally filter by strategy. Returns entry/exit prices, P&L, and timestamps for each trade. Use for detailed performance review and reconciliation.',
    {
      start: ISO_DATE,
      end: ISO_DATE,
      strategy: z.string().min(1).max(64).optional(),
    },
    async ({ start, end, strategy }) => {
      try {
        const blotter = await client.getTradeBlotter(start, end, strategy);
        return {
          content: [{ type: 'text', text: JSON.stringify(blotter, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
