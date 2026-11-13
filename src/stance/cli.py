"""CLI entry point for stance."""

import typer

from stance.commands.launch import attach, claude, codex
from stance.commands.server import server_app
from stance.logging import configure_logging

app = typer.Typer(
    name="stance",
    help="Budget-protected Claude Code and Codex sessions, powered by Omnigent.",
    no_args_is_help=True,
)

app.command()(claude)
app.command()(codex)
app.command()(attach)
app.add_typer(server_app)


@app.callback()
def main() -> None:
    """Configure logging before running a command."""
    configure_logging()
