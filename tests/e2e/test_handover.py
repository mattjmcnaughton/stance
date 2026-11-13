"""Interactive and prepare/attach handover through the real Omnigent stack.

Fake ``claude``/``codex`` executables stand in for the harnesses: they record
their argv and environment, then idle. Nothing calls a model provider. This proves the
handover path (Stance -> ``omnigent claude --resume`` -> host daemon ->
runner -> tmux -> harness), not provider billing.
"""

import contextlib
import os
import pty
import shlex
import shutil
import signal
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest

from tests.support import (
    PRIVACY,
    STANCE,
    clean_env,
    server_url_from,
    session_id_from,
    stance,
)

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")

FAKE_CLAUDE = """#!/bin/sh
LOG={log}
mkdir -p "$LOG"
n=$(date +%s%N)
printf '%s\\n' "$@" > "$LOG/args-$n"
env > "$LOG/env-$n"
case "$1" in
  --version|-v) echo "2.9.0 (Claude Code)"; exit 0;;
  -p|auth) exit 1;;  # upstream's model/auth probes: report nothing, call nothing
esac
echo "FAKE CLAUDE READY"
sleep 60
"""
# Codex's app-server protocol is not emulated; the model check below happens
# before the runner starts the terminal.
FAKE_CODEX = """#!/bin/sh
case "$1" in --version) echo "codex-cli 0.200.0"; exit 0;; esac
exit 1
"""


@pytest.fixture
def env(tmp_path: Path) -> Iterator[dict[str, str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, script in (
        ("claude", FAKE_CLAUDE.format(log=tmp_path / "harness")),
        ("codex", FAKE_CODEX),
    ):
        (bin_dir / name).write_text(script)
        (bin_dir / name).chmod(0o755)
    environment = clean_env(tmp_path)
    environment["PATH"] = f"{bin_dir}:{environment['PATH']}"
    yield environment
    stance(environment, "server", "stop")


def _harness_launched(log_dir: Path) -> Callable[[], Path | None]:
    """The first interactive harness launch (upstream probes pass no --model)."""

    def check() -> Path | None:
        for path in sorted(log_dir.glob("args-*")):
            if "--model" in path.read_text().splitlines():
                return path
        return None

    return check


def _run_in_pty[T](
    argv: list[str], env: dict[str, str], until: Callable[[], T | None]
) -> T:
    """Run *argv* on a pseudo-terminal until *until* returns a truthy value."""
    controller, terminal = pty.openpty()
    proc = subprocess.Popen(
        argv,
        env=env,
        cwd=env["HOME"],
        stdin=terminal,
        stdout=terminal,
        stderr=terminal,
        start_new_session=True,
    )
    os.close(terminal)
    output = b""
    try:
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            if result := until():
                return result
            if proc.poll() is not None:
                break
            try:
                output += os.read(controller, 65536)
            except OSError:
                time.sleep(0.2)
        raise AssertionError(
            f"condition never met; output:\n{output.decode(errors='replace')}"
        )
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=30)
        os.close(controller)


def _assert_harness_env(args_file: Path) -> None:
    env_file = args_file.with_name(args_file.name.replace("args-", "env-"))
    harness_env = dict(
        line.split("=", 1) for line in env_file.read_text().splitlines() if "=" in line
    )
    for name, value in PRIVACY.items():
        assert harness_env.get(name) == value, name
    assert "EXISTING_PASSTHROUGH" in harness_env["OMNIGENT_RUNNER_ENV_PASSTHROUGH"]


def test_interactive_launch_attaches_policy_before_harness(env: dict[str, str]) -> None:
    log_dir = Path(env["HOME"]).parent / "harness"
    args_file = _run_in_pty(
        [STANCE, "claude", "--budget-usd", "5", "--model", "claude-sonnet-5-5"],
        env,
        _harness_launched(log_dir),
    )
    args = args_file.read_text().splitlines()
    assert args[:2] == ["--model", "claude-sonnet-5-5"]
    _assert_harness_env(args_file)

    url = stance(env, "server", "status").stdout.split("running at ")[1].split()[0]
    with httpx.Client(base_url=url) as client:
        sessions = client.get("/v1/sessions").json()["data"]
        assert len(sessions) == 1
        policies = client.get(f"/v1/sessions/{sessions[0]['id']}/policies").json()[
            "data"
        ]
        assert [(p["enabled"], p["factory_params"]) for p in policies] == [
            (True, {"max_cost_usd": 5.0})
        ]


def _printed_attach(stdout: str) -> list[str]:
    line = next(line for line in stdout.splitlines() if "attach:" in line)
    argv = shlex.split(line.split("attach:", 1)[1])
    assert argv[0] == "stance"
    return [STANCE, *argv[1:]]


def _write_stance_config(env: dict[str, str], text: str) -> None:
    path = Path(env["XDG_CONFIG_HOME"]) / "stance" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_prepare_then_printed_attach_reuses_the_protected_session(
    env: dict[str, str],
) -> None:
    log_dir = Path(env["HOME"]).parent / "harness"
    # A configured default server that is not the one --server local picks.
    _write_stance_config(env, '[server]\nurl = "http://127.0.0.1:1"\n')
    prepared = stance(
        env,
        "claude",
        "--budget-usd",
        "2",
        "--model",
        "claude-sonnet-5-5",
        "--server",
        "local",
        "--prepare",
        cwd=Path(env["HOME"]),
    )
    assert prepared.returncode == 0, prepared.stderr
    sid = session_id_from(prepared.stdout)
    assert not log_dir.exists()  # prepare starts no harness

    args_file = _run_in_pty(
        _printed_attach(prepared.stdout), env, _harness_launched(log_dir)
    )
    _assert_harness_env(args_file)
    url = server_url_from(prepared.stdout)
    with httpx.Client(base_url=url) as client:
        assert [s["id"] for s in client.get("/v1/sessions").json()["data"]] == [sid]
        policy = client.get(f"/v1/sessions/{sid}/policies").json()["data"][0]
        assert policy["factory_params"] == {"max_cost_usd": 2.0}


def test_codex_attach_keeps_session_model_over_omnigent_default(
    env: dict[str, str],
) -> None:
    """`omnigent codex` writes Omnigent's configured default model into a
    resumed session unless given --model; Stance must pass the session's."""
    config_home = Path(env["OMNIGENT_CONFIG_HOME"])
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / "config.yaml").write_text("model: gpt-6.1-terra\n")
    prepared = stance(
        env,
        "codex",
        "--budget-usd",
        "2",
        "--model",
        "gpt-6.1-sol",
        "--prepare",
        cwd=Path(env["HOME"]),
    )
    assert prepared.returncode == 0, prepared.stderr
    sid = session_id_from(prepared.stdout)
    client = httpx.Client(base_url=server_url_from(prepared.stdout))

    def stored_override() -> str | None:
        return client.get(f"/v1/sessions/{sid}").json().get("model_override")

    with client:
        assert stored_override() is None
        override = _run_in_pty(_printed_attach(prepared.stdout), env, stored_override)
    assert override == "gpt-6.1-sol"
