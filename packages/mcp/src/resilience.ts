/**
 * Resilience primitives for engine communication.
 *
 * Circuit breaker states:
 * - CLOSED: normal operation, requests flow through
 * - OPEN: engine unreachable, fail fast for configured duration
 * - HALF_OPEN: testing if engine recovered (one probe request)
 */

import { logger } from './logger.js';
import { AxiosError } from 'axios';

export type CircuitState = 'closed' | 'open' | 'half_open';

export class CircuitBreaker {
  private state: CircuitState = 'closed';
  private failureCount = 0;
  private lastFailureTime = 0;
  private readonly failureThreshold: number;
  private readonly recoveryTimeMs: number;

  constructor(failureThreshold = 5, recoveryTimeMs = 30_000) {
    this.failureThreshold = failureThreshold;
    this.recoveryTimeMs = recoveryTimeMs;
  }

  async execute<T>(fn: () => Promise<T>): Promise<T> {
    if (this.state === 'open') {
      if (Date.now() - this.lastFailureTime > this.recoveryTimeMs) {
        this.transition('half_open');
      } else {
        throw new Error('Circuit breaker OPEN — engine unavailable');
      }
    }
    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (err) {
      this.onFailure();
      throw err;
    }
  }

  getState(): CircuitState {
    return this.state;
  }

  private transition(next: CircuitState): void {
    if (next !== this.state) {
      logger.warn({ from: this.state, to: next }, 'Circuit breaker state transition');
      this.state = next;
    }
  }

  private onSuccess(): void {
    this.failureCount = 0;
    this.transition('closed');
  }

  private onFailure(): void {
    this.failureCount += 1;
    this.lastFailureTime = Date.now();
    if (this.failureCount >= this.failureThreshold) {
      this.transition('open');
    }
  }
}

function isClientError(err: unknown): boolean {
  if (err instanceof AxiosError && err.response) {
    return err.response.status >= 400 && err.response.status < 500;
  }
  return false;
}

export async function withRetry<T>(
  fn: () => Promise<T>,
  maxAttempts = 3,
  baseDelayMs = 500,
): Promise<T> {
  let lastError: unknown;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastError = err;
      // Do NOT retry on 4xx client errors
      if (isClientError(err)) {
        throw err;
      }
      if (attempt < maxAttempts) {
        const delay = baseDelayMs * Math.pow(2, attempt - 1);
        logger.debug({ attempt, delay, err }, 'withRetry: retrying after failure');
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }
  }
  throw lastError;
}
