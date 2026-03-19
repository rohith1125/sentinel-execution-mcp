import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import type { EngineClient } from '../engine-client.js';
import { formatToolError as engineError } from '../errors.js';

export function registerStrategyTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'strategy.scan_watchlist',
    'Run one or more strategies across all symbols in the watchlist (or a specific group) and return a ranked list of signals sorted by confidence. Use this to surface the highest-conviction trade opportunities at any moment.',
    {
      group: z.string().min(1).max(64).optional(),
      strategies: z.array(z.string().min(1)).min(1).max(20).optional(),
    },
    async ({ group, strategies }) => {
      try {
        const results = await client.scanWatchlist(group, strategies);
        return {
          content: [{ type: 'text', text: JSON.stringify(results, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'strategy.scan_symbol',
    'Run strategies against a single symbol and return all signals with confidence scores, entry/stop/target prices, and reasoning. Useful for deep analysis before committing to a position.',
    {
      symbol: z.string().min(1).max(10),
      strategies: z.array(z.string().min(1)).min(1).max(20).optional(),
    },
    async ({ symbol, strategies }) => {
      try {
        const results = await client.scanSymbol(symbol.toUpperCase(), strategies);
        return {
          content: [{ type: 'text', text: JSON.stringify(results, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'strategy.list',
    'List all available strategies registered in the engine along with their current governance state (research, paper, shadow, live, suspended). Use to understand which strategies are eligible to generate signals for live execution.',
    {},
    async () => {
      try {
        const strategies = await client.listStrategies();
        return {
          content: [{ type: 'text', text: JSON.stringify(strategies, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
