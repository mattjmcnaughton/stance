"""CLI entry point for stance."""

import typer

from stance.commands.example import example
from stance.logging import configure_logging

app = typer.Typer(
    name="stance",
    help="Configurable agent sessions and policies, powered by Omnigent.",
)

app.command()(example)


@app.callback()
def main() -> None:
    """Configure logging before running a command."""
    configure_logging()
