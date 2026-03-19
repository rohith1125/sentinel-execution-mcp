import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import type { EngineClient } from '../engine-client.js';
import { formatToolError as engineError } from '../errors.js';

export function registerPortfolioTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'portfolio.status',
    'Get a full portfolio overview including account value, cash balance, equity, daily and total P&L, buying power, and all open positions with unrealized P&L. Use for a comprehensive health check of the current trading state.',
    {},
    async () => {
      try {
        const status = await client.getPortfolioStatus();
        return {
          content: [{ type: 'text', text: JSON.stringify(status, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'portfolio.positions',
    'List all open positions with detailed metrics: symbol, side, quantity, average entry price, current price, market value, and unrealized P&L in both dollar and percentage terms. Optionally includes the originating strategy ID.',
    {},
    async () => {
      try {
        const positions = await client.getPositions();
        return {
          content: [{ type: 'text', text: JSON.stringify(positions, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'portfolio.account',
    'Get the current account value, available cash, equity, and daily P&L metrics. A lightweight alternative to portfolio.status when you only need financial figures rather than full position detail.',
    {},
    async () => {
      try {
        const account = await client.getAccountValue();
        return {
          content: [{ type: 'text', text: JSON.stringify(account, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
