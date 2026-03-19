import type { CircuitState } from './resilience.js';

export interface EngineHealth {
  reachable: boolean;
  circuitState: CircuitState;
  lastSuccessAt: Date | null;
  lastErrorAt: Date | null;
  consecutiveFailures: number;
  engineEnv: string | null;
}

export class HealthTracker {
  private health: EngineHealth = {
    reachable: false,
    circuitState: 'closed',
    lastSuccessAt: null,
    lastErrorAt: null,
    consecutiveFailures: 0,
    engineEnv: null,
  };

  recordSuccess(env: string): void {
    this.health.reachable = true;
    this.health.lastSuccessAt = new Date();
    this.health.consecutiveFailures = 0;
    this.health.engineEnv = env;
  }

  recordFailure(): void {
    this.health.reachable = false;
    this.health.lastErrorAt = new Date();
    this.health.consecutiveFailures += 1;
  }

  updateCircuitState(state: CircuitState): void {
    this.health.circuitState = state;
  }

  getHealth(): EngineHealth {
    return { ...this.health };
  }
}
