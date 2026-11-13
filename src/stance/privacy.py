"""Privacy and runtime-namespace environment, applied before Omnigent is imported.

This module must not import Omnigent. ``stance/__init__.py`` applies it to
``os.environ`` so every later Omnigent import, and every child process Stance
starts (server, host daemon, runner, native CLI), inherits the same settings.
"""

import os
from collections.abc import MutableMapping
from pathlib import Path

# Verified against omnigent 0.20.0:
# - OMNIGENT_DISABLE_TELEMETRY / OMNIGENT_ANALYTICS=0: usage analytics off
#   (omnigent/telemetry/environment.py), overriding an inherited opt-in.
# - OMNIGENT_TELEMETRY_ENABLED=0: runtime OpenTelemetry off
#   (omnigent/runtime/telemetry.py).
# - OMNIGENT_OTEL_CAPTURE_CONTENT=0: trace content capture off.
# Claude Code and Codex privacy settings (DISABLE_TELEMETRY, DO_NOT_TRACK, ...)
# are deliberately left alone: the harnesses own their own privacy config.
PRIVACY_ENV: dict[str, str] = {
    "OMNIGENT_DISABLE_TELEMETRY": "1",
    "OMNIGENT_ANALYTICS": "0",
    "OMNIGENT_TELEMETRY_ENABLED": "0",
    "OMNIGENT_OTEL_CAPTURE_CONTENT": "0",
}

# Upstream's daemon/runner allowlists already forward OMNIGENT_TELEMETRY_ENABLED
# and the OMNIGENT_OTEL_* prefix. These two need an explicit passthrough entry
# to reach remote-mode daemons and runners.
RUNNER_ENV_PASSTHROUGH_VAR = "OMNIGENT_RUNNER_ENV_PASSTHROUGH"
_PASSTHROUGH_NAMES = ("OMNIGENT_DISABLE_TELEMETRY", "OMNIGENT_ANALYTICS")

DATA_DIR_VAR = "OMNIGENT_DATA_DIR"
# The user's own Omnigent data dir (holds `omnigent login` tokens), captured
# before Stance points OMNIGENT_DATA_DIR at its namespace.
USER_DATA_DIR_VAR = "STANCE_USER_OMNIGENT_DATA_DIR"


def stance_state_dir(environ: MutableMapping[str, str] | None = None) -> Path:
    """Return Stance's state directory (``$XDG_STATE_HOME/stance``)."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME") or str(Path(env.get("HOME", "~")) / ".local/state")
    return Path(base).expanduser() / "stance"


def omnigent_data_dir(environ: MutableMapping[str, str] | None = None) -> Path:
    """Return the Stance-owned Omnigent runtime data directory."""
    return stance_state_dir(environ) / "omnigent"


def apply_privacy_env(environ: MutableMapping[str, str]) -> None:
    """Force Stance's Omnigent opt-outs and runtime namespace into *environ*.

    Overrides inherited opt-ins, merges the passthrough names into any existing
    ``OMNIGENT_RUNNER_ENV_PASSTHROUGH`` list without dropping user entries, and
    points ``OMNIGENT_DATA_DIR`` at Stance's own namespace so Stance never
    adopts an unrelated Omnigent server or daemon. Provider and harness
    configuration (``OMNIGENT_CONFIG_HOME``, ``~/.claude``, ``~/.codex``) is
    untouched.
    """
    environ.update(PRIVACY_ENV)
    existing = [
        name.strip()
        for name in environ.get(RUNNER_ENV_PASSTHROUGH_VAR, "").split(",")
        if name.strip()
    ]
    merged = existing + [name for name in _PASSTHROUGH_NAMES if name not in existing]
    environ[RUNNER_ENV_PASSTHROUGH_VAR] = ",".join(merged)
    stance_dir = str(omnigent_data_dir(environ))
    if USER_DATA_DIR_VAR not in environ:
        inherited = environ.get(DATA_DIR_VAR)
        user_dir = inherited if inherited and inherited != stance_dir else None
        environ[USER_DATA_DIR_VAR] = user_dir or str(
            Path(environ.get("HOME", "~")).expanduser() / ".omnigent"
        )
    environ[DATA_DIR_VAR] = stance_dir


def use_user_data_dir(environ: MutableMapping[str, str]) -> None:
    """Switch to the user's own Omnigent data dir, for an existing server.

    That is where existing ``omnigent login`` state and host daemons for
    remote servers live. Only valid after a command has committed to an
    existing server: Omnigent's local-server paths were fixed at import.
    """
    environ[DATA_DIR_VAR] = environ[USER_DATA_DIR_VAR]


def missing_privacy_settings(
    environ: MutableMapping[str, str] | dict[str, str],
) -> list[str]:
    """Return the privacy settings *environ* lacks, as ``NAME=value`` strings."""
    return [
        f"{name}={value}"
        for name, value in PRIVACY_ENV.items()
        if environ.get(name) != value
    ]
