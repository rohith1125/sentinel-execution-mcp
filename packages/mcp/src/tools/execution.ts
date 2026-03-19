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

export function registerExecutionTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'execution.paper_order',
    'Submit a paper trading order for simulated execution. Supports market, limit, stop, and stop-limit order types. Always call risk.validate_trade first to ensure the order passes all risk checks. Returns the order record with status and fill details.',
    {
      symbol: z.string().min(1).max(10),
      side: z.enum(['buy', 'sell']),
      order_type: z.enum(['market', 'limit', 'stop', 'stop_limit']),
      quantity: z.number().positive(),
      limit_price: z.number().positive().optional(),
      stop_price: z.number().positive().optional(),
      strategy_id: z.string().min(1).max(64).optional(),
    },
    async ({ symbol, side, order_type, quantity, limit_price, stop_price, strategy_id }) => {
      try {
        const params = {
          symbol: symbol.toUpperCase(),
          side,
          order_type,
          quantity,
          ...(limit_price !== undefined ? { limit_price } : {}),
          ...(stop_price !== undefined ? { stop_price } : {}),
          ...(strategy_id !== undefined ? { strategy_id } : {}),
        };
        const order = await client.submitPaperOrder(params);
        return {
          content: [{ type: 'text', text: JSON.stringify(order, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'execution.cancel_order',
    'Cancel a pending or partially filled order by order ID. A reason is required for audit trail purposes. Returns the updated order record reflecting the cancellation.',
    {
      order_id: z.string().min(1).max(64),
      reason: z.string().min(5).max(500),
    },
    async ({ order_id, reason }) => {
      try {
        const order = await client.cancelOrder(order_id, reason);
        return {
          content: [{ type: 'text', text: JSON.stringify(order, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'execution.get_order',
    'Retrieve the current status and details of an order by its order ID. Returns fill information, timestamps, and any rejection reason if applicable.',
    {
      order_id: z.string().min(1).max(64),
    },
    async ({ order_id }) => {
      try {
        const order = await client.getOrder(order_id);
        return {
          content: [{ type: 'text', text: JSON.stringify(order, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'execution.flatten_all',
    'Emergency action: submit market sell orders for all open positions simultaneously. This is a destructive, irreversible action. Requires the exact confirmation string "FLATTEN_ALL" to prevent accidental execution. Provide a substantive reason for the audit log.',
    {
      reason: z.string().min(10).max(500),
      confirmation: z.literal('FLATTEN_ALL'),
    },
    async ({ reason, confirmation: _confirmation }) => {
      try {
        const result = await client.flattenAll(reason);
        return {
          content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'execution.reset_paper_account',
    'Reset the paper trading account to a clean state with the specified starting cash balance (defaults to the engine-configured default). All positions, orders, and P&L history will be cleared. Use at the start of a new testing period.',
    {
      starting_cash: z.number().positive().max(10_000_000).optional(),
    },
    async ({ starting_cash }) => {
      try {
        await client.resetPaperAccount(starting_cash);
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  status: 'paper_account_reset',
                  starting_cash: starting_cash ?? 'engine_default',
                  reset_at: new Date().toISOString(),
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
