import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import { AxiosError } from 'axios';
import type { EngineClient } from '../engine-client.js';

function engineError(err: unknown): string {
  if (err instanceof AxiosError) {
    const detail = (err.response?.data as { detail?: string } | undefined)?.detail;
    return `Engine error: ${detail ?? err.message}`;
  }
  return `Unexpected error: ${String(err)}`;
}

export function registerRegimeTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'regime.evaluate',
    'Classify the current market regime for a symbol using multi-factor analysis. Returns a regime label (e.g. trending_bull, mean_reverting, high_vol_unstable), a confidence score, a tradeability score, and per-strategy compatibility scores. Use this before initiating any position to confirm conditions are favorable.',
    {
      symbol: z.string().min(1).max(10),
      timeframe: z.string().min(1).max(10).optional(),
    },
    async ({ symbol, timeframe }) => {
      try {
        const regime = await client.evaluateRegime(symbol.toUpperCase(), timeframe);
        return {
          content: [{ type: 'text', text: JSON.stringify(regime, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'regime.evaluate_bulk',
    'Classify the market regime for multiple symbols in a single call. Returns a map of symbol → regime snapshot. Useful for a portfolio-level scan before a trading session to identify which symbols are in tradeable regimes.',
    {
      symbols: z.array(z.string().min(1).max(10)).min(1).max(100),
    },
    async ({ symbols }) => {
      try {
        const regimes = await client.evaluateRegimeBulk(symbols.map((s) => s.toUpperCase()));
        return {
          content: [{ type: 'text', text: JSON.stringify(regimes, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
