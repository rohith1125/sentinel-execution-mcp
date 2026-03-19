import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { createServer } from './server.js';
import { EngineClient } from './engine-client.js';
import { HealthTracker } from './health.js';
import { config } from './config.js';
import { logger } from './logger.js';

const client = new EngineClient(config.ENGINE_BASE_URL, config.ENGINE_TIMEOUT_MS);
const healthTracker = new HealthTracker();
const server = createServer(client, healthTracker);

async function probeEngine(maxAttempts = 5, delayMs = 2000): Promise<void> {
  for (let i = 1; i <= maxAttempts; i++) {
    try {
      const h = await client.healthCheck();
      healthTracker.recordSuccess(h.env ?? 'unknown');
      logger.info({ env: h.env }, 'engine connected');
      return;
    } catch {
      if (i < maxAttempts) {
        logger.warn({ attempt: i, maxAttempts }, 'engine not reachable, retrying');
        await new Promise(r => setTimeout(r, delayMs));
      }
    }
  }
  logger.warn('engine unreachable at startup — continuing anyway, will retry on first tool call');
}
await probeEngine();

const healthInterval = setInterval(async () => {
  try {
    const h = await client.healthCheck();
    healthTracker.recordSuccess(h.env ?? 'unknown');
  } catch {
    healthTracker.recordFailure();
    logger.warn({ circuitState: client.getCircuitState() }, 'engine health ping failed');
  }
}, 30_000);

async function shutdown(signal: string): Promise<void> {
  logger.info({ signal }, 'shutting down');
  clearInterval(healthInterval);
  await server.close();
  process.exit(0);
}
process.on('SIGTERM', () => void shutdown('SIGTERM'));
process.on('SIGINT', () => void shutdown('SIGINT'));

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
