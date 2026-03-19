#!/usr/bin/env bash
set -euo pipefail

echo "Setting up Sentinel Execution MCP..."

# Check prerequisites
command -v python3 >/dev/null || { echo "Python 3.12+ required"; exit 1; }
command -v node >/dev/null || { echo "Node.js 20+ required"; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm required (npm install -g pnpm)"; exit 1; }
command -v docker >/dev/null || { echo "Docker required for database/redis"; exit 1; }

# Copy env file
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example — review and configure before running"
fi

# Python setup
cd packages/engine
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cd ../..

# Node setup
pnpm install

echo ""
echo "Setup complete. Next steps:"
echo "  1. Start infrastructure:  make docker-up"
echo "  2. Run migrations:        make migrate"
echo "  3. Start development:     make dev"
