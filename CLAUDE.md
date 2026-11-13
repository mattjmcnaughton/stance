# CLAUDE.md

Configurable agent sessions and policies, powered by Omnigent. Session budgets are the first use case.

Python CLI using typer, structlog, uv, ruff, ty, and pytest.

## Quick Reference

| Command | Purpose |
| ------- | ------- |
| `just fmt` | Check formatting |
| `just fmt-fix` | Fix formatting |
| `just lint` | Check linting |
| `just lint-fix` | Fix linting |
| `just typecheck` | Run type checker |
| `just test-unit` | Run unit tests |
| `just test-integration` | Run integration tests |
| `just test-e2e` | Run e2e tests |
| `just test-all` | Run all tests |
| `just test-external` | Run tests hitting external services |
| `just gate` | Fast pre-push check (fmt + lint + typecheck + test-unit) |
| `just gate-expensive` | Full check (gate + integration + e2e) |
| `just gate-external` | Everything (gate-expensive + external) |

## Project Structure

```
src/stance/
  cli.py                    # Typer app, registers subcommands
  config.py                 # Pydantic-settings based config
  logging.py                # structlog setup
  commands/                 # Subcommand modules
  services/                 # Business logic
tests/
  unit/                     # Unit tests
  integration/              # Integration tests
  e2e/                      # End-to-end tests
```

## Key Conventions

- Source code lives in `src/stance/` (src layout).
- **Commands** are thin I/O wrappers. Parse CLI args into `COMMANDInput`, call a service, format `COMMANDOutput`.
- **Services** contain business logic. Commands delegate to services.
- Tests are organized by type in `tests/unit/`, `tests/integration/`, `tests/e2e/`.
- Use `@pytest.mark.external` for tests that hit external services.
- All config is in `pyproject.toml` — no separate config files.
- Version is defined in `pyproject.toml` and read via `importlib.metadata`.

## More Information

- `docs/architecture.md` — read before adding new modules or changing project structure
- `docs/development.md` — read for environment setup, debugging, or common tasks
