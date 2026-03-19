#!/usr/bin/env bash
set -euo pipefail
echo "Resetting paper trading account..."
curl -s -X POST http://localhost:8100/execution/paper/reset \
  -H "Content-Type: application/json" \
  -d '{"starting_cash": 100000}' | python3 -m json.tool
echo "Paper account reset to $100,000"
