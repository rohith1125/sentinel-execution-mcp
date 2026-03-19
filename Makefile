.PHONY: install dev test lint format build docker-up docker-down clean help

# Default target
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	cd packages/engine && pip install -e ".[dev]"
	cd packages/mcp && pnpm install

dev: ## Start development environment (requires Docker for DB/Redis)
	docker-compose -f docker/docker-compose.yml up -d db redis
	cd packages/engine && uvicorn sentinel.api:app --reload --host 0.0.0.0 --port 8100 &
	cd packages/mcp && pnpm dev

test: ## Run all tests
	cd packages/engine && pytest tests/ -v --cov=sentinel --cov-report=term-missing
	cd packages/mcp && pnpm test

test-engine: ## Run Python tests only
	cd packages/engine && pytest tests/ -v --cov=sentinel --cov-report=term-missing

test-mcp: ## Run TypeScript tests only
	cd packages/mcp && pnpm test

lint: ## Lint all code
	cd packages/engine && ruff check sentinel/ tests/
	cd packages/mcp && pnpm lint

format: ## Format all code
	cd packages/engine && ruff format sentinel/ tests/
	cd packages/mcp && pnpm exec prettier --write src/

typecheck: ## Type check TypeScript
	cd packages/mcp && pnpm typecheck

build: ## Build for production
	cd packages/mcp && pnpm build

docker-up: ## Start full stack with Docker
	docker-compose -f docker/docker-compose.yml up -d

docker-down: ## Stop Docker services
	docker-compose -f docker/docker-compose.yml down

docker-build: ## Build Docker images
	docker-compose -f docker/docker-compose.yml build

migrate: ## Run database migrations
	cd packages/engine && alembic upgrade head

migrate-new: ## Create new migration (usage: make migrate-new MSG="description")
	cd packages/engine && alembic revision --autogenerate -m "$(MSG)"

clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -name "*.pyc" -delete
	rm -rf packages/mcp/dist packages/mcp/node_modules/.cache
