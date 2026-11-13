"""Helpers for tests that run the real ``stance`` CLI against real Omnigent."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from omnigent.native.native_policy_hook import hook_payload_to_evaluation_request
from omnigent.telemetry.environment import CI_ENV_VARS

STANCE = str(Path(sys.executable).parent / "stance")
OMNIGENT = str(Path(sys.executable).parent / "omnigent")
# Shared so the model-pricing catalog downloads once per test run.
CACHE_HOME = tempfile.mkdtemp(prefix="stance-test-cache-")
PRIVACY = {
    "OMNIGENT_DISABLE_TELEMETRY": "1",
    "OMNIGENT_ANALYTICS": "0",
    "OMNIGENT_TELEMETRY_ENABLED": "0",
    "OMNIGENT_OTEL_CAPTURE_CONTENT": "0",
}


def clean_env(root: Path) -> dict[str, str]:
    """An isolated environment that does NOT mask a missing opt-out.

    CI/pytest markers (which make Omnigent disable analytics on its own) are
    removed, and conflicting opt-ins are set, so only Stance's explicit
    settings can turn telemetry off.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in CI_ENV_VARS
        and not key.startswith(("OMNIGENT_", "XDG_", "STANCE_"))
        and key not in {"DO_NOT_TRACK", "DISABLE_TELEMETRY"}
    }
    env.update(
        HOME=str(root / "home"),
        XDG_CONFIG_HOME=str(root / "config"),
        XDG_STATE_HOME=str(root / "state"),
        XDG_CACHE_HOME=CACHE_HOME,
        OMNIGENT_CONFIG_HOME=str(root / "omnigent-config"),
        OMNIGENT_ANALYTICS="1",
        OMNIGENT_TELEMETRY_ENABLED="1",
        OMNIGENT_OTEL_CAPTURE_CONTENT="1",
        OMNIGENT_RUNNER_ENV_PASSTHROUGH="EXISTING_PASSTHROUGH",
        TERM="xterm-256color",
    )
    (root / "home").mkdir(parents=True, exist_ok=True)
    return env


def run(
    argv: list[str], env: dict[str, str], cwd: Path | None = None, timeout: float = 240
):
    """Run a command and return the completed process (never raises on exit code)."""
    return subprocess.run(
        argv,
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def stance(env: dict[str, str], *args: str, cwd: Path | None = None):
    return run([STANCE, *args], env, cwd=cwd)


def session_id_from(output: str) -> str:
    match = re.search(r"session:\s+(\S+)", output)
    assert match, output
    return match.group(1)


def server_url_from(output: str) -> str:
    match = re.search(r"server:\s+(\S+)", output)
    assert match, output
    return match.group(1)


def post_usage(client: httpx.Client, session_id: str, **data: object) -> None:
    """Record harness-reported cumulative usage, as the native forwarders do."""
    resp = client.post(
        f"/v1/sessions/{session_id}/events",
        json={"type": "external_session_usage", "data": data},
    )
    assert resp.status_code < 300, resp.text


def evaluate(client: httpx.Client, session_id: str, hook_event: str) -> str:
    """Evaluate a native hook event through the server's real policy path."""
    payload: dict[str, object] = {
        "hook_event_name": hook_event,
        "tool_name": "Bash",
        "tool_input": {"command": "ls"},
        "prompt": "continue",
    }
    request = hook_payload_to_evaluation_request(hook_event, payload)
    resp = client.post(f"/v1/sessions/{session_id}/policies/evaluate", json=request)
    assert resp.status_code == 200, resp.text
    return resp.json()["result"].removeprefix("POLICY_ACTION_")
