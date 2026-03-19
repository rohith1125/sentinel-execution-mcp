import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { EngineClient } from './engine-client.js';
import { registerWatchlistTools } from './tools/watchlist.js';
import { registerMarketTools } from './tools/market.js';
import { registerRegimeTools } from './tools/regime.js';
import { registerStrategyTools } from './tools/strategy.js';
import { registerRiskTools } from './tools/risk.js';
import { registerPortfolioTools } from './tools/portfolio.js';
import { registerExecutionTools } from './tools/execution.js';
import { registerGovernanceTools } from './tools/governance.js';
import { registerAuditTools } from './tools/audit.js';

export function createServer(engineClient: EngineClient): McpServer {
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

  return server;
}
