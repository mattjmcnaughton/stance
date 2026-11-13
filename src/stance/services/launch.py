"""Launch orchestration: budget, model, server, protected session, handover."""

import json
import os
import shlex
import signal
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from stance.config import ConfigError, UserConfig, validate_budget
from stance.services.pricing import check_model_pricing
from stance.services.server import ServerTarget, omnigent_cli, resolve_server
from stance.services.sessions import (
    Harness,
    create_protected_session,
    session_harness,
    session_model,
    verify_budget_policy,
)


class LaunchError(Exception):
    """A launch input could not be resolved."""


@dataclass(frozen=True)
class SessionTarget:
    """A verified, budget-protected session ready for the native CLI."""

    session_id: str
    harness: Harness
    budget_usd: float
    server: ServerTarget
    model: str | None = None

    @property
    def conversation_url(self) -> str:
        from omnigent.conversation_browser import conversation_url

        return conversation_url(self.server.url, self.session_id)

    @property
    def attach_command(self) -> str:
        server = "local" if self.server.local else self.server.url
        return shlex.join(["stance", "attach", self.session_id, "--server", server])

    @property
    def native_argv(self) -> list[str]:
        argv = [self.harness.value, "--server", self.server.cli_server_arg]
        if self.harness is Harness.CODEX and self.model:
            # Without --model, `omnigent codex` falls back to Omnigent's
            # configured default model and writes it over the session's.
            argv += ["--model", self.model]
        return omnigent_cli(*argv, "--resume", self.session_id)


def resolve_budget(cli_budget: float | None, config: UserConfig) -> float:
    """CLI budget, else the configured default; one of them is required."""
    if cli_budget is not None:
        return validate_budget(cli_budget, "--budget-usd")
    if config.budget_usd is not None:
        return config.budget_usd
    raise ConfigError(
        "A budget is required: pass --budget-usd AMOUNT or set budget_usd in the "
        "Stance config file."
    )


def _claude_ambient_model(environ: Mapping[str, str]) -> str | None:
    if model := environ.get("ANTHROPIC_MODEL", "").strip():
        return model
    config_dir = environ.get("CLAUDE_CONFIG_DIR") or str(
        Path(environ.get("HOME", "~")) / ".claude"
    )
    try:
        settings = json.loads(
            (Path(config_dir).expanduser() / "settings.json").read_text()
        )
    except (OSError, ValueError):
        return None
    if not isinstance(settings, dict):
        return None
    env = settings.get("env")
    for candidate in (
        env.get("ANTHROPIC_MODEL") if isinstance(env, dict) else None,
        settings.get("model"),
    ):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _codex_ambient_model(environ: Mapping[str, str]) -> str | None:
    from omnigent.harnesses.codex_native.bridge import read_codex_home_config_model

    codex_home = environ.get("CODEX_HOME") or str(
        Path(environ.get("HOME", "~")) / ".codex"
    )
    return read_codex_home_config_model(Path(codex_home).expanduser())


def resolve_model(
    harness: Harness,
    cli_model: str | None,
    config: UserConfig,
    environ: Mapping[str, str] | None = None,
) -> str:
    """CLI model, then config, then the harness's own configured default."""
    env = os.environ if environ is None else environ
    configured = (config.claude if harness is Harness.CLAUDE else config.codex).model
    ambient = (
        _claude_ambient_model(env)
        if harness is Harness.CLAUDE
        else _codex_ambient_model(env)
    )
    model = cli_model or configured or ambient
    if model:
        return model
    where = (
        "ANTHROPIC_MODEL or Claude Code settings.json"
        if harness is Harness.CLAUDE
        else "~/.codex/config.toml"
    )
    raise LaunchError(
        f"Could not determine the {harness} model before launch (checked config and "
        f"{where}). Pass --model MODEL_ID or set [{harness}] model in the Stance "
        "config."
    )


def _codex_config_key(harness_args: tuple[str, ...], index: int) -> str | None:
    """The key of a Codex ``-c/--config key=value`` override at *index*."""
    arg = harness_args[index]
    if arg in ("-c", "--config"):
        value = harness_args[index + 1] if index + 1 < len(harness_args) else ""
    elif arg.startswith("--config="):
        value = arg.removeprefix("--config=")
    elif arg.startswith("-c"):
        value = arg[2:].removeprefix("=")
    else:
        return None
    return value.partition("=")[0].strip()


def reject_model_args(harness: Harness, harness_args: tuple[str, ...]) -> None:
    """Refuse raw harness arguments that select the launch model.

    Stance prices and pins the model itself; a second selection passed through
    to the harness could launch an unchecked model.
    """
    for index, arg in enumerate(harness_args):
        selects = arg.partition("=")[0] == "--model"
        if harness is Harness.CODEX:
            selects = (
                selects
                or arg.startswith("-m")
                or _codex_config_key(harness_args, index) == "model"
            )
        if selects:
            raise LaunchError(
                f"Harness argument {arg!r} selects a model. Use Stance's --model "
                "instead, so the model is price-checked and pinned for the session."
            )


def prepare_session(
    harness: Harness,
    *,
    cli_budget: float | None,
    cli_model: str | None,
    cli_server: str | None,
    harness_args: tuple[str, ...],
    config: UserConfig,
    workspace: Path,
) -> SessionTarget:
    """Validate inputs, then create a protected session. Starts no harness."""
    budget = resolve_budget(cli_budget, config)
    reject_model_args(harness, harness_args)
    model = resolve_model(harness, cli_model, config)
    check_model_pricing(model)
    server = resolve_server(cli_server, config.server.url)
    with server.client() as client:
        session_id = create_protected_session(
            client,
            harness,
            budget_usd=budget,
            model=model,
            harness_args=harness_args,
            workspace=workspace,
        )
    return SessionTarget(session_id, harness, budget, server, model)


def find_session(
    session_id: str, *, cli_server: str | None, config: UserConfig
) -> SessionTarget:
    """Look up an existing session and re-verify its budget before attaching."""
    server = resolve_server(cli_server, config.server.url)
    with server.client() as client:
        harness = session_harness(client, session_id)
        budget = verify_budget_policy(client, session_id)
        model = session_model(client, session_id)
    return SessionTarget(session_id, harness, budget, server, model)


def run_native(target: SessionTarget) -> int:
    """Hand the terminal to ``omnigent <harness> --resume`` and wait for it."""
    previous = signal.signal(signal.SIGINT, lambda *_: None)
    try:
        return subprocess.run(
            target.native_argv, env=os.environ.copy(), check=False
        ).returncode
    finally:
        signal.signal(signal.SIGINT, previous)
