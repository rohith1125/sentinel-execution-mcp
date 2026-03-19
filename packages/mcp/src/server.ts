import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { EngineClient } from './engine-client.js';
import { HealthTracker } from './health.js';
import { formatToolError } from './errors.js';
import { registerWatchlistTools } from './tools/watchlist.js';
import { registerMarketTools } from './tools/market.js';
import { registerRegimeTools } from './tools/regime.js';
import { registerStrategyTools } from './tools/strategy.js';
import { registerRiskTools } from './tools/risk.js';
import { registerPortfolioTools } from './tools/portfolio.js';
import { registerExecutionTools } from './tools/execution.js';
import { registerGovernanceTools } from './tools/governance.js';
import { registerAuditTools } from './tools/audit.js';

export function createServer(engineClient: EngineClient, healthTracker: HealthTracker): McpServer {
  const server = new McpServer({
    name: 'sentinel-execution-mcp',
    version: '0.1.0',
  });

  registerWatchlistTools(server, engineClient);
  registerMarketTools(server, engineClient);
  registerRegimeTools(server, engineClient);
  registerStrategyTools(server, engineClient);
  registerRiskTools(server, engineClient);
  registerPortfolioTools(server, engineClient);
  registerExecutionTools(server, engineClient);
  registerGovernanceTools(server, engineClient);
  registerAuditTools(server, engineClient);

  server.tool(
    'system.health',
    'Check the health of the Sentinel MCP server and its connection to the trading engine. Returns circuit breaker state, last contact time, and engine environment. Use this to diagnose connectivity issues before running other tools.',
    {},
    async () => {
      const localHealth = healthTracker.getHealth();
      // Attempt a live probe
      try {
        const engineHealth = await engineClient.healthCheck();
        healthTracker.recordSuccess(engineHealth.env ?? 'unknown');
        return {
          content: [{
            type: 'text' as const,
            text: JSON.stringify({
              mcp_status: 'healthy',
              engine_status: 'reachable',
              engine_env: engineHealth.env,
              circuit_state: engineClient.getCircuitState(),
              last_success_at: localHealth.lastSuccessAt,
            }, null, 2),
          }],
        };
      } catch (err) {
        healthTracker.recordFailure();
        return {
          content: [{
            type: 'text' as const,
            text: JSON.stringify({
              mcp_status: 'healthy',
              engine_status: 'unreachable',
              circuit_state: engineClient.getCircuitState(),
              last_success_at: localHealth.lastSuccessAt,
              error: formatToolError(err),
            }, null, 2),
          }],
          isError: true,
        };
      }
    }
  );

  return server;
}
