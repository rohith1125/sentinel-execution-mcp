import { z } from 'zod';

const ConfigSchema = z.object({
  ENGINE_BASE_URL: z.string().url().default('http://localhost:8100'),
  ENGINE_TIMEOUT_MS: z.coerce.number().default(10_000),
  MCP_TRANSPORT: z.enum(['stdio', 'sse']).default('stdio'),
  MCP_SSE_PORT: z.coerce.number().default(3100),
  LOG_LEVEL: z.enum(['trace', 'debug', 'info', 'warn', 'error']).default('info'),
  NODE_ENV: z.enum(['development', 'paper', 'live', 'test']).default('paper'),
});

export type Config = z.infer<typeof ConfigSchema>;
export const config = ConfigSchema.parse(process.env);
