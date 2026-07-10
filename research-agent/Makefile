.PHONY: install test lint clean docker-build docker-test docker-run eval

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --tb=short

test-cov:
	pytest tests/ -v --tb=short --cov=src/research_agent --cov-report=term

lint:
	ruff check src/ tests/

format:
	ruff format src/ tests/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache

docker-build:
	docker compose build

docker-test:
	docker compose run --rm test

docker-run:
	docker compose run --rm agent

eval:
	research-agent eval --domain attention_mechanism

check-creds:
	! grep -rn "sk-.*\|api_key\s*=\s*['\"]\w\|password\s*=\s*['\"]\w\|secret\s*=\s*['\"]\w" src/ --include="*.py" || (echo "ERROR: Hardcoded credentials found" && exit 1)