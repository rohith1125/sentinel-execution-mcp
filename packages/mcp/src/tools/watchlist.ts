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

export function registerWatchlistTools(server: McpServer, client: EngineClient): void {
  server.tool(
    'watchlist.add',
    'Add one or more symbols to the trading watchlist. Symbols are validated against the market data provider before being added. Optionally assign them to a named group for strategy filtering.',
    {
      symbols: z.array(z.string().min(1).max(10)).min(1).max(100),
      group: z.string().min(1).max(64).optional(),
      notes: z.string().max(500).optional(),
    },
    async ({ symbols, group, notes }) => {
      try {
        const entries = await client.addSymbols(symbols, group, notes);
        return {
          content: [{ type: 'text', text: JSON.stringify(entries, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'watchlist.remove',
    'Remove one or more symbols from the watchlist. Symbols are marked inactive and will no longer appear in strategy scans.',
    {
      symbols: z.array(z.string().min(1).max(10)).min(1).max(100),
    },
    async ({ symbols }) => {
      try {
        const result = await client.removeSymbols(symbols);
        return {
          content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'watchlist.list',
    'List all symbols currently on the watchlist. Optionally filter by group tag or active status. Returns full metadata for each entry including asset class, group tags, and notes.',
    {
      group: z.string().min(1).max(64).optional(),
      active_only: z.boolean().optional(),
    },
    async ({ group, active_only }) => {
      try {
        const entries = await client.listSymbols(group, active_only);
        return {
          content: [{ type: 'text', text: JSON.stringify(entries, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'watchlist.groups',
    'List all distinct group tags currently in use on the watchlist. Useful for discovering available filter categories before running strategy scans.',
    {},
    async () => {
      try {
        const groups = await client.getGroups();
        return {
          content: [{ type: 'text', text: JSON.stringify(groups, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'watchlist.tag',
    'Assign a group tag to one or more watchlist symbols. Tags are additive — existing tags are preserved. Use groups to organize symbols by sector, strategy type, or trading universe.',
    {
      symbols: z.array(z.string().min(1).max(10)).min(1).max(100),
      group: z.string().min(1).max(64),
    },
    async ({ symbols, group }) => {
      try {
        const result = await client.tagSymbols(symbols, group);
        return {
          content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );

  server.tool(
    'watchlist.export',
    'Export the watchlist as a structured JSON document suitable for backup, analysis, or import into another system. Optionally filter by group.',
    {
      group: z.string().min(1).max(64).optional(),
    },
    async ({ group }) => {
      try {
        const entries = await client.listSymbols(group, false);
        const exported = {
          exported_at: new Date().toISOString(),
          group_filter: group ?? null,
          count: entries.length,
          entries,
        };
        return {
          content: [{ type: 'text', text: JSON.stringify(exported, null, 2) }],
        };
      } catch (err) {
        return { content: [{ type: 'text', text: engineError(err) }], isError: true };
      }
    },
  );
}
