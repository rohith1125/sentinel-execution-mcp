import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import type { EngineClient } from '../engine-client.js';
import { formatToolError as engineError } from '../errors.js';

export function registerMarketTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'market.snapshot',
    'Get a real-time market snapshot for a single symbol, including the latest quote (bid/ask/spread), most recent OHLCV bar, and daily summary bar. Use this before submitting any order to confirm current pricing.',
    {
      symbol: z.string().min(1).max(10).toUpperCase(),
    },
    async ({ symbol }) => {
      try {
        const snapshot = await client.getSnapshot(symbol);
        return {
          content: [{ type: 'text', text: JSON.stringify(snapshot, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'market.snapshots',
    'Get real-time market snapshots for multiple symbols in a single request. Returns a map of symbol → snapshot. Efficient for scanning multiple positions or watchlist symbols at once.',
    {
      symbols: z.array(z.string().min(1).max(10)).min(1).max(200),
    },
    async ({ symbols }) => {
      try {
        const snapshots = await client.getSnapshots(symbols.map((s) => s.toUpperCase()));
        return {
          content: [{ type: 'text', text: JSON.stringify(snapshots, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'market.bars',
    'Retrieve historical OHLCV bars for a symbol at a given timeframe. Timeframe examples: "1min", "5min", "15min", "1hour", "1day". Use for chart analysis, regime evaluation inputs, or strategy backtesting context.',
    {
      symbol: z.string().min(1).max(10),
      timeframe: z.string().min(1).max(10),
      limit: z.number().int().min(1).max(1000).optional().default(100),
    },
    async ({ symbol, timeframe, limit }) => {
      try {
        const bars = await client.getBars(symbol.toUpperCase(), timeframe, limit);
        return {
          content: [{ type: 'text', text: JSON.stringify(bars, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'market.health',
    'Check the health and connectivity status of the market data provider. Returns provider name, status, and current latency. Use to diagnose data feed issues before trading sessions.',
    {},
    async () => {
      try {
        const health = await client.marketHealth();
        return {
          content: [{ type: 'text', text: JSON.stringify(health, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
