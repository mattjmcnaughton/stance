# Development

## Prerequisites

- Python 3.12 or 3.13 (Omnigent's `google-re2` dependency has no 3.15 wheels)
- [uv](https://docs.astral.sh/uv/)
- [just](https://just.systems/) (or `uvx --from rust-just just ...`)
- `tmux` for the e2e tests


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

Integration tests start real Omnigent servers in temporary directories and
evaluate policies through the server's `/policies/evaluate` endpoint with
recorded usage. E2e tests run the full `stance` -> `omnigent` -> host daemon ->
runner -> tmux handover with a fake `claude` executable. Neither calls a model
provider; both download the public model-pricing catalog from GitHub.

`tests/support.py` builds environments that strip CI/pytest markers and set
conflicting telemetry opt-ins, so only Stance's explicit settings can make the
privacy assertions pass.

Use `@pytest.mark.external` for tests that hit external services. These run via `just test-external`.

## Omnigent pin

`omnigent==0.22.0` is exempt from `tool.uv.exclude-newer` through
`exclude-newer-package` (with its lockstep `omnigent-client` and
`omnigent-ui-sdk`), because it was published inside the one-week window. Stance
uses a few private Omnigent helpers (see `docs/architecture.md`); re-run the
integration and e2e suites before changing the pin.

## Building

```sh
uv build
```

This produces a wheel and sdist in `dist/`.
