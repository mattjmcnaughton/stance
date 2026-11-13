"""Privacy bootstrap: overrides opt-ins and runs before Omnigent initializes."""

import json
import os
import subprocess
import sys
from pathlib import Path

from omnigent.telemetry.environment import CI_ENV_VARS, is_disabled_by_env

from stance.privacy import apply_privacy_env


def test_overrides_inherited_opt_ins_and_keeps_existing_passthrough(
    tmp_path: Path,
) -> None:
    env = {
        "XDG_STATE_HOME": str(tmp_path),
        "OMNIGENT_ANALYTICS": "1",
        "OMNIGENT_TELEMETRY_ENABLED": "1",
        "OMNIGENT_OTEL_CAPTURE_CONTENT": "true",
        "OMNIGENT_RUNNER_ENV_PASSTHROUGH": "AWS_PROFILE, OMNIGENT_ANALYTICS",
        "OMNIGENT_DATA_DIR": "/somewhere/else",
    }
    assert not is_disabled_by_env(env)  # the control: these opt in

    apply_privacy_env(env)

    assert is_disabled_by_env(env)
    assert env["OMNIGENT_TELEMETRY_ENABLED"] == "0"
    assert env["OMNIGENT_OTEL_CAPTURE_CONTENT"] == "0"
    assert env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"] == (
        "AWS_PROFILE,OMNIGENT_ANALYTICS,OMNIGENT_DISABLE_TELEMETRY"
    )
    assert env["OMNIGENT_DATA_DIR"] == str(tmp_path / "stance" / "omnigent")


def test_importing_stance_disables_omnigent_telemetry_before_initialization(
    tmp_path: Path,
) -> None:
    """A fresh interpreter, opted in, with no CI/pytest markers to mask a miss."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in CI_ENV_VARS and not key.startswith("OMNIGENT_")
    }
    env.update(
        HOME=str(tmp_path),
        XDG_STATE_HOME=str(tmp_path / "state"),
        OMNIGENT_ANALYTICS="1",
        OMNIGENT_TELEMETRY_ENABLED="1",
    )
    script = (
        "import json, stance.cli\n"
        "from omnigent.telemetry import client\n"
        "from omnigent.runtime import telemetry\n"
        "from omnigent.host import local_server\n"
        "print(json.dumps([client.is_disabled(), telemetry.telemetry_enabled(),\n"
        "                  str(local_server._LOCAL_SERVER_PID_PATH)]))\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    disabled, tracing, pid_path = json.loads(out.stdout.strip().splitlines()[-1])
    assert disabled is True
    assert tracing is False
    assert pid_path.startswith(str(tmp_path / "state" / "stance" / "omnigent"))

    # Control: the same interpreter without Stance reports telemetry enabled,
    # so the assertion above is not satisfied by the environment alone.
    control = subprocess.run(
        [
            sys.executable,
            "-c",
            "from omnigent.telemetry import client; print(client.is_disabled())",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert control.stdout.strip().splitlines()[-1] == "False"


def test_codex_spawned_bridge_has_no_analytics_emitter(tmp_path: Path) -> None:
    """Codex starts Omnigent's MCP bridge with a scrubbed env (only
    OMNIGENT_DATA_DIR), which Stance cannot extend without patching Omnigent.
    That is safe only while the bridge never loads an analytics emitter; this
    guards the pinned upgrade path."""
    script = (
        "import sys, omnigent.harnesses.claude_native.bridge\n"
        "print(sorted(m for m in sys.modules if m in {'omnigent.telemetry.client',\n"
        "  'omnigent.host.connect', 'omnigent.runner.transports.ws_tunnel.serve'}))\n"
    )
    out = subprocess.run(
        [sys.executable, "-I", "-c", script],
        env={"HOME": str(tmp_path), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip().splitlines()[-1] == "[]"
