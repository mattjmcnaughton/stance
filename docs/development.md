# Development

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://just.systems/)


## Setup

```sh
# Install dependencies
uv sync --dev

# Copy environment file
cp .env.example .env
```


## Common Tasks

```sh
# Format and lint
just fmt-fix
just lint-fix

# Type check
just typecheck

# Run tests
just test-unit
just test-all

# Full pre-push check
just gate

# Full check including integration and e2e
just gate-expensive
```

## Testing

Tests are organized by type:

- `tests/unit/` — fast, isolated tests
- `tests/integration/` — tests with real dependencies
- `tests/e2e/` — end-to-end tests

Use `@pytest.mark.external` for tests that hit external services. These run via `just test-external`.

## Building

```sh
uv build
```

This produces a wheel and sdist in `dist/`.
