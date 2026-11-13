"""Example command demonstrating the command pattern."""

from dataclasses import dataclass

import typer


@dataclass
class ExampleInput:
    """Input for the example command."""

    name: str


@dataclass
class ExampleOutput:
    """Output from the example command."""

    message: str


def run_example(request: ExampleInput) -> ExampleOutput:
    """Business logic for the example command. Delegate to a service for real work."""
    return ExampleOutput(message=f"Hello, {request.name}!")


def example(
    name: str = typer.Argument(help="Name to greet"),
) -> None:
    """Run the example command."""
    result = run_example(ExampleInput(name=name))
    typer.echo(result.message)
