# stance

Configurable agent sessions and policies, powered by Omnigent.

Stance will wrap Claude Code and Codex sessions with configurable Omnigent
policies. Session budgets are the first use case, not the limit of its scope.

This repository currently contains the Python CLI scaffold. Native session launch
and policy configuration are not implemented yet.

## Getting Started

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- [just](https://just.systems/)

### Installation

```sh
uv sync --dev
```

### Usage

```sh
# Run the CLI
uv run stance --help

# Run the example command
uv run stance example World
```

## Development

```sh
# Check formatting and linting
just fmt
just lint

# Fix formatting and linting
just fmt-fix
just lint-fix

# Run type checker
just typecheck
```

## Testing

```sh
# Run unit tests
just test-unit

# Run all tests
just test-all

# Full pre-push check
just gate
```

## Architecture

See [docs/architecture.md](docs/architecture.md) for project structure and design decisions.

## Contributing

Contributions are welcome! Please open an issue or pull request.

## License

[MIT](LICENSE)
