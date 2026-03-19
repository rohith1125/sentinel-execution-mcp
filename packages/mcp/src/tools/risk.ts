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

export function registerRiskTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'risk.validate_trade',
    'Run a proposed trade through the full risk firewall before submission. Checks include position sizing limits, daily loss limits, symbol concentration, sector exposure, kill-switch state, and strategy-specific guardrails. Always call this before executing any order.',
    {
      symbol: z.string().min(1).max(10),
      side: z.enum(['buy', 'sell']),
      shares: z.number().positive(),
      entry_price: z.number().positive(),
      stop_price: z.number().positive(),
      strategy_name: z.string().min(1).max(64),
    },
    async ({ symbol, side, shares, entry_price, stop_price, strategy_name }) => {
      try {
        const assessment = await client.validateTrade({
          symbol: symbol.toUpperCase(),
          side,
          shares,
          entry_price,
          stop_price,
          strategy_name,
        });
        return {
          content: [{ type: 'text', text: JSON.stringify(assessment, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'risk.kill_switch_status',
    'Get the current state of all kill switches: global halt, halted strategies, and halted symbols. Check this before any trading activity to confirm execution is permitted.',
    {},
    async () => {
      try {
        const state = await client.getKillSwitchState();
        return {
          content: [{ type: 'text', text: JSON.stringify(state, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'risk.engage_halt',
    'Immediately engage a global trading halt. All new order submissions will be rejected until the halt is explicitly disengaged. This is a safety mechanism — use when unusual market conditions, system anomalies, or portfolio risk thresholds require immediate cessation of all trading activity. A meaningful reason of at least 10 characters is required for audit purposes.',
    {
      reason: z.string().min(10).max(500),
    },
    async ({ reason }) => {
      try {
        await client.engageGlobalHalt(reason);
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                { status: 'halt_engaged', reason, engaged_at: new Date().toISOString() },
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

  server.tool(
    'risk.disengage_halt',
    'Disengage the global trading halt and resume normal order submission. Requires explicit confirmation string "CONFIRM" to prevent accidental reactivation. Verify that the underlying issue has been resolved before disengaging.',
    {
      confirmation: z.literal('CONFIRM'),
    },
    async ({ confirmation: _confirmation }) => {
      try {
        await client.disengageGlobalHalt();
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                { status: 'halt_disengaged', disengaged_at: new Date().toISOString() },
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

  server.tool(
    'risk.halt_strategy',
    'Halt a specific strategy, preventing it from generating or executing new orders. Other strategies remain unaffected. Useful for isolating a misbehaving strategy without engaging a global halt.',
    {
      strategy: z.string().min(1).max(64),
      reason: z.string().min(10).max(500),
    },
    async ({ strategy, reason }) => {
      try {
        await client.haltStrategy(strategy, reason);
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                { status: 'strategy_halted', strategy, reason, halted_at: new Date().toISOString() },
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

  server.tool(
    'risk.halt_symbol',
    'Halt trading on a specific symbol across all strategies. No new orders will be accepted for the halted symbol until the halt is lifted. Use when a symbol exhibits anomalous behavior such as a trading halt, news embargo, or data feed issue.',
    {
      symbol: z.string().min(1).max(10),
      reason: z.string().min(10).max(500),
    },
    async ({ symbol, reason }) => {
      try {
        await client.haltSymbol(symbol.toUpperCase(), reason);
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify(
                {
                  status: 'symbol_halted',
                  symbol: symbol.toUpperCase(),
                  reason,
                  halted_at: new Date().toISOString(),
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
