import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createServer } from './server.js';
import { EngineClient } from './engine-client.js';
import { config } from './config.js';
import { logger } from './logger.js';

const client = new EngineClient(config.ENGINE_BASE_URL, config.ENGINE_TIMEOUT_MS);
const server = createServer(client);

// Verify engine reachability at startup — non-fatal if down
try {
  const health = await client.healthCheck();
  logger.info({ env: health.env }, 'Engine connection established');
} catch (err) {
  logger.warn({ err }, 'Engine not reachable at startup — will retry on first tool call');
}

const transport = new StdioServerTransport();
await server.connect(transport);
logger.info(
  {
    transport: config.MCP_TRANSPORT,
    engine: config.ENGINE_BASE_URL,
    env: config.NODE_ENV,
  },
  'Sentinel Execution MCP server started',
);
