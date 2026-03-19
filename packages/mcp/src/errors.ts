import type { CircuitState } from './resilience.js';

export class EngineUnavailableError extends Error {
  constructor(public readonly circuitState: CircuitState) {
    super(`Trading engine unavailable (circuit: ${circuitState})`);
    this.name = 'EngineUnavailableError';
  }
}

export class EngineError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly detail: string,
  ) {
    super(`Engine error ${statusCode}: ${detail}`);
    this.name = 'EngineError';
  }
}

export function formatToolError(err: unknown): string {
  if (err instanceof EngineUnavailableError) {
    return `The trading engine is currently unavailable (circuit breaker ${err.circuitState}). Check that the engine service is running.`;
  }
  if (err instanceof EngineError) {
    return `Engine returned an error: ${err.detail}`;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}
