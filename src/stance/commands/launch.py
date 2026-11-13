"""``stance claude``, ``stance codex``, and ``stance attach``."""

from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import typer

from stance.config import ConfigError, load_user_config
from stance.services.launch import (
    LaunchError,
    SessionTarget,
    find_session,
    prepare_session,
    run_native,
)
from stance.services.pricing import PricingError
from stance.services.server import ServerError
from stance.services.sessions import Harness, SessionError

_HARNESS_NAMES = {Harness.CLAUDE: "Claude Code", Harness.CODEX: "Codex"}

BudgetOption = typer.Option(
    None,
    "--budget-usd",
    help="Session budget in USD. Defaults to budget_usd in config.",
)
ModelOption = typer.Option(
    None,
    "--model",
    help="Model id to price and launch. Defaults to config or harness settings.",
)
ServerOption = typer.Option(
    None,
    "--server",
    help="Existing Omnigent server URL, or 'local' for Stance's own local server.",
)
PrepareOption = typer.Option(
    False,
    "--prepare",
    help="Create the protected session and print attach details only.",
)
HarnessArgs = typer.Argument(None, help="Arguments after -- are passed to the harness.")


@dataclass(frozen=True)
class LaunchInput:
    """Parsed arguments for ``stance claude`` / ``stance codex``."""

    harness: Harness
    budget_usd: float | None
    model: str | None
    server: str | None
    prepare: bool
    harness_args: tuple[str, ...]


@contextmanager
def _user_errors() -> Generator[None]:
    try:
        yield
    except (ConfigError, LaunchError, PricingError, ServerError, SessionError) as exc:
        typer.echo(f"stance: {exc}", err=True)
        raise typer.Exit(2) from exc


def _details(target: SessionTarget) -> list[str]:
    port = urlsplit(target.server.url).port
    owner = "Stance-owned local server" if target.server.local else "existing server"
    lines = [
        f"  session:      {target.session_id}",
        f"  budget:       ${target.budget_usd:.2f} USD (Omnigent cost_budget policy)",
    ]
    if target.model:
        lines.append(f"  model:        {target.model}")
    return lines + [
        f"  server:       {target.server.url} (port {port}, {owner})",
        f"  conversation: {target.conversation_url}",
        f"  attach:       {target.attach_command}",
    ]


def _launch(request: LaunchInput) -> None:
    with _user_errors():
        target = prepare_session(
            request.harness,
            cli_budget=request.budget_usd,
            cli_model=request.model,
            cli_server=request.server,
            harness_args=request.harness_args,
            config=load_user_config(),
            workspace=Path.cwd(),
        )
    name = _HARNESS_NAMES[request.harness]
    if request.prepare:
        typer.echo(
            f"Prepared a budget-protected {name} session. No harness was started."
        )
        typer.echo("\n".join(_details(target)))
        return
    typer.echo(f"Starting a budget-protected {name} session.", err=True)
    typer.echo("\n".join(_details(target)), err=True)
    _handover(target)


def _handover(target: SessionTarget) -> None:
    code = run_native(target)
    typer.echo(f"Re-attach with Stance: {target.attach_command}", err=True)
    raise typer.Exit(code)


def claude(
    budget_usd: float | None = BudgetOption,
    model: str | None = ModelOption,
    server: str | None = ServerOption,
    prepare: bool = PrepareOption,
    harness_args: list[str] | None = HarnessArgs,
) -> None:
    """Start Claude Code in a budget-protected Omnigent session."""
    _launch(
        LaunchInput(
            Harness.CLAUDE,
            budget_usd,
            model,
            server,
            prepare,
            tuple(harness_args or ()),
        )
    )


def codex(
    budget_usd: float | None = BudgetOption,
    model: str | None = ModelOption,
    server: str | None = ServerOption,
    prepare: bool = PrepareOption,
    harness_args: list[str] | None = HarnessArgs,
) -> None:
    """Start Codex in a budget-protected Omnigent session."""
    _launch(
        LaunchInput(
            Harness.CODEX, budget_usd, model, server, prepare, tuple(harness_args or ())
        )
    )


def attach(
    session_id: str = typer.Argument(help="Session id printed by --prepare."),
    server: str | None = ServerOption,
) -> None:
    """Attach to a prepared or earlier Stance session, keeping its budget and spend."""
    with _user_errors():
        target = find_session(session_id, cli_server=server, config=load_user_config())
    typer.echo(
        f"Attaching to {_HARNESS_NAMES[target.harness]} session {session_id} "
        f"(budget ${target.budget_usd:.2f} USD).",
        err=True,
    )
    _handover(target)
