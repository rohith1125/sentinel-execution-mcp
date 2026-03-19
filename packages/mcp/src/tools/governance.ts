import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { z } from 'zod';
import type { EngineClient } from '../engine-client.js';
import { formatToolError as engineError } from '../errors.js';

const VALID_STATES = ['research', 'paper', 'shadow', 'live', 'suspended', 'retired'] as const;

export function registerGovernanceTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'governance.evaluate_promotion',
    'Evaluate whether a strategy meets the quantitative and qualitative criteria required to be promoted to a higher governance state (e.g., paper → shadow, shadow → live). Returns an eligibility verdict, per-criterion results, and a list of gaps that must be resolved before promotion.',
    {
      strategy: z.string().min(1).max(64),
      target_state: z.enum(VALID_STATES),
    },
    async ({ strategy, target_state }) => {
      try {
        const evaluation = await client.evaluatePromotion(strategy, target_state);
        return {
          content: [{ type: 'text', text: JSON.stringify(evaluation, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'governance.promote_strategy',
    'Promote a strategy to a higher governance state after evaluation criteria have been met. Requires the approver identity for the audit record. Should only be called after governance.evaluate_promotion confirms eligibility.',
    {
      strategy: z.string().min(1).max(64),
      target_state: z.enum(VALID_STATES),
      approved_by: z.string().min(1).max(128),
      notes: z.string().max(1000).optional(),
    },
    async ({ strategy, target_state, approved_by, notes }) => {
      try {
        const record = await client.promoteStrategy(strategy, target_state, approved_by, notes);
        return {
          content: [{ type: 'text', text: JSON.stringify(record, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'governance.suspend_strategy',
    'Suspend a strategy, stopping it from generating new signals or executing orders. The strategy remains in the registry but is marked inactive. A reason is required and will appear in the audit log and strategy record.',
    {
      strategy: z.string().min(1).max(64),
      reason: z.string().min(10).max(500),
    },
    async ({ strategy, reason }) => {
      try {
        const record = await client.suspendStrategy(strategy, reason);
        return {
          content: [{ type: 'text', text: JSON.stringify(record, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'governance.list_strategies',
    'List all strategies registered in the governance system with their current state, promotion history, suspension details, and days spent in paper and live modes. Use for a full overview of the strategy lifecycle.',
    {},
    async () => {
      try {
        const records = await client.listStrategyStates();
        return {
          content: [{ type: 'text', text: JSON.stringify(records, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'governance.check_drift',
    'Analyze a strategy for performance drift relative to its historical baseline. Returns drift metrics across key performance dimensions (win rate, avg win/loss, profit factor) and a recommendation on whether intervention is needed.',
    {
      strategy: z.string().min(1).max(64),
    },
    async ({ strategy }) => {
      try {
        const report = await client.checkDrift(strategy);
        return {
          content: [{ type: 'text', text: JSON.stringify(report, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
