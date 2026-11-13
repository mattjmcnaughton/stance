# Architecture

## Overview

Stance is a Python CLI for configurable agent sessions and policies through Omnigent,
using typer with a commands/services layered architecture.

## Project Structure

```
src/stance/
  cli.py               # Typer app, registers subcommands
  config.py            # Pydantic-settings config from env vars
  logging.py           # structlog setup
  commands/            # Subcommand modules (thin I/O layer)
  services/            # Business logic
tests/
  unit/                # Fast, isolated unit tests
  integration/         # Tests with real dependencies
  e2e/                 # End-to-end tests
```

## Layering

```
CLI (typer commands)
  -> Commands (parse args into COMMANDInput, format COMMANDOutput)
    -> Services (business logic)
```

## Toolchain

| Tool | Purpose |
| ---- | ------- |
| uv | Package management, virtual environments |
| hatchling | Build backend |
| ruff | Formatting and linting |
| ty | Type checking |
| pytest | Testing |
| typer | CLI framework |
| structlog | Structured logging |
| pydantic-settings | Configuration |

## Conventions

- All configuration is in `pyproject.toml`.
- Version is the single source of truth in `pyproject.toml`, read via `importlib.metadata`.
- `py.typed` marker enables downstream type checking (PEP 561).
- Commands define `COMMANDInput` and `COMMANDOutput` dataclasses.
- Commands are thin — they parse CLI args and delegate to services.
