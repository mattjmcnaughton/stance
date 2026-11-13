# Check formatting
fmt:
    uv run ruff format --check .

# Fix formatting
fmt-fix:
    uv run ruff format .

# Check linting
lint:
    uv run ruff check .

# Fix linting
lint-fix:
    uv run ruff check --fix .

# Run type checker
typecheck:
    uv run ty check

# Run all tests
test-all: test-unit test-integration test-e2e

# Run unit tests
test-unit:
    uv run pytest tests/unit

# Run integration tests
test-integration:
    uv run pytest tests/integration

# Run e2e tests
test-e2e:
    uv run pytest tests/e2e

# Run tests that hit external services
test-external:
    uv run pytest -m external

# Fast pre-push check
gate: fmt lint typecheck test-unit

# Full check
gate-expensive: gate test-integration test-e2e

# Everything including external
gate-external: gate-expensive test-external
