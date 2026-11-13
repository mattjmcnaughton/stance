"""``stance server``: minimal lifecycle for Stance's own local Omnigent server."""

import typer

from stance.commands.launch import _user_errors
from stance.services.server import (
    ensure_local_server,
    local_server_status,
    stop_local_server,
)

server_app = typer.Typer(
    name="server",
    help="Start, inspect, or stop Stance's local Omnigent server.",
    no_args_is_help=True,
)


@server_app.command("start")
def start() -> None:
    """Start Stance's local server, or reuse it when already running."""
    with _user_errors():
        target = ensure_local_server()
    typer.echo(f"Stance local server: {target.url}")


@server_app.command("status")
def status() -> None:
    """Show whether Stance's local server is running."""
    with _user_errors():
        info = local_server_status()
    if not info.get("running"):
        typer.echo("Stance local server: not running.")
        return
    typer.echo(
        f"Stance local server: running at {info.get('url')} (pid {info.get('pid')})"
    )
    typer.echo(
        f"  host daemon attached: {'yes' if info.get('daemon_attached') else 'no'}"
    )


@server_app.command("stop")
def stop() -> None:
    """Stop Stance's local server and its host daemon."""
    with _user_errors():
        stopped = stop_local_server()
    typer.echo(
        "Stopped Stance's local server."
        if stopped
        else "Stance local server: not running."
    )
