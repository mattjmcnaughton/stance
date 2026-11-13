"""Stance against a real local Omnigent server: budgets, attach, server choice.

No harness is started and no model is called. Usage is recorded through the
same ``external_session_usage`` event the native forwarders post, and verdicts
come from the server's real ``/policies/evaluate`` path.
"""

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from omnigent.harnesses.claude_native.main import _SESSION_LABELS as CLAUDE_LABELS
from omnigent.llms.context_window import fetch_model_pricing
from typer.testing import CliRunner

from stance.cli import app
from tests.support import (
    OMNIGENT,
    clean_env,
    evaluate,
    post_usage,
    run,
    server_url_from,
    session_id_from,
    stance,
)


@pytest.fixture(scope="module")
def runtime(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[dict[str, str], str]]:
    root = tmp_path_factory.mktemp("runtime")
    env = clean_env(root)
    started = stance(env, "server", "start")
    assert started.returncode == 0, started.stderr
    url = started.stdout.strip().rsplit(" ", 1)[-1]
    yield env, url
    stance(env, "server", "stop")


def _prepare(env: dict[str, str], *args: str) -> str:
    result = stance(env, *args, "--prepare")
    assert result.returncode == 0, result.stderr
    return session_id_from(result.stdout)


def test_prepared_claude_session_is_protected_and_enforced(runtime) -> None:
    env, url = runtime
    sid = _prepare(env, "claude", "--budget-usd", "5", "--model", "claude-sonnet-5-5")
    with httpx.Client(base_url=url) as client:
        session = client.get(f"/v1/sessions/{sid}").json()
        assert (
            session["labels"]["omnigent.wrapper"] == CLAUDE_LABELS["omnigent.wrapper"]
        )
        policies = client.get(f"/v1/sessions/{sid}/policies").json()["data"]
        assert [
            (p["handler"], p["enabled"], p["factory_params"]) for p in policies
        ] == [
            ("omnigent.policies.builtins.cost.cost_budget", True, {"max_cost_usd": 5.0})
        ]
        # No prompt was submitted: the session has no items.
        assert client.get(f"/v1/sessions/{sid}/items").json()["data"] == []

        assert evaluate(client, sid, "PreToolUse") == "ALLOW"
        for cost, verdict in [
            (2.0, "ALLOW"),
            (4.99, "ALLOW"),
            (5.0, "DENY"),
            (7.5, "DENY"),
        ]:
            post_usage(
                client,
                sid,
                cumulative_cost_usd=cost,
                cumulative_input_tokens=1000,
                cumulative_output_tokens=100,
                model="claude-sonnet-5-5",
            )
            assert evaluate(client, sid, "PreToolUse") == verdict, cost
            assert evaluate(client, sid, "UserPromptSubmit") == verdict, cost


def test_tracked_child_session_counts_against_root_budget(runtime) -> None:
    env, url = runtime
    root = _prepare(env, "claude", "--budget-usd", "5", "--model", "claude-sonnet-5-5")
    with httpx.Client(base_url=url) as client:
        bundle_session = client.get(f"/v1/sessions/{root}").json()
        child = client.post(
            "/v1/sessions",
            json={"agent_id": bundle_session["agent_id"], "parent_session_id": root},
        )
        assert child.status_code < 300, child.text
        child_id = child.json()["id"]

        post_usage(client, root, cumulative_cost_usd=3.0, model="claude-sonnet-5-5")
        post_usage(client, child_id, cumulative_cost_usd=1.5, model="claude-sonnet-5-5")
        assert evaluate(client, child_id, "PreToolUse") == "ALLOW"
        post_usage(client, child_id, cumulative_cost_usd=2.5, model="claude-sonnet-5-5")
        # Root $3.00 + child $2.50 crosses the root's $5.00 budget for both.
        assert evaluate(client, child_id, "PreToolUse") == "DENY"
        assert evaluate(client, root, "PreToolUse") == "DENY"


def test_codex_session_is_token_priced_against_budget(runtime) -> None:
    env, url = runtime
    budget = 0.5
    sid = _prepare(env, "codex", "--budget-usd", str(budget), "--model", "gpt-6.1-sol")
    pricing = fetch_model_pricing("gpt-6.1-sol")
    assert pricing is not None
    below = int(budget * 0.5 / pricing.output_per_token)
    above = int(budget * 1.5 / pricing.output_per_token)
    with httpx.Client(base_url=url) as client:
        post_usage(
            client,
            sid,
            cumulative_input_tokens=0,
            cumulative_output_tokens=below,
            model="gpt-6.1-sol",
        )
        assert evaluate(client, sid, "PreToolUse") == "ALLOW"
        post_usage(
            client,
            sid,
            cumulative_input_tokens=0,
            cumulative_output_tokens=above,
            model="gpt-6.1-sol",
        )
        assert evaluate(client, sid, "PreToolUse") == "DENY"
        assert evaluate(client, sid, "UserPromptSubmit") == "DENY"


@pytest.mark.parametrize("model", ["claude-sonnet-5-5-UNRECOGNIZED", "sonnet"])
def test_unpriceable_launch_model_creates_no_session(runtime, model: str) -> None:
    env, url = runtime
    with httpx.Client(base_url=url) as client:
        before = len(client.get("/v1/sessions").json()["data"])
        result = stance(
            env, "claude", "--budget-usd", "5", "--model", model, "--prepare"
        )
        assert result.returncode == 2
        assert "no exact entry" in result.stderr
        assert len(client.get("/v1/sessions").json()["data"]) == before


def test_attach_refuses_session_without_intact_budget(runtime, tmp_path: Path) -> None:
    env, url = runtime
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    marker = tmp_path / "harness-started"
    (fake_bin / "claude").write_text(f"#!/bin/sh\ntouch {marker}\n")
    (fake_bin / "claude").chmod(0o755)
    env = {**env, "PATH": f"{fake_bin}:{env['PATH']}"}
    template = _prepare(
        env, "claude", "--budget-usd", "5", "--model", "claude-sonnet-5-5"
    )
    with httpx.Client(base_url=url) as client:
        bare = client.post(
            "/v1/sessions",
            json={
                "agent_id": client.get(f"/v1/sessions/{template}").json()["agent_id"],
                "metadata": {"labels": dict(CLAUDE_LABELS)},
            },
        ).json()["id"]
        result = stance(env, "attach", bare)
        assert result.returncode == 2
        assert "no Stance budget policy" in result.stderr

        sid = _prepare(
            env, "claude", "--budget-usd", "5", "--model", "claude-sonnet-5-5"
        )
        policy = client.get(f"/v1/sessions/{sid}/policies").json()["data"][0]
        client.patch(
            f"/v1/sessions/{sid}/policies/{policy['id']}", json={"enabled": False}
        )
        result = stance(env, "attach", sid)
        assert result.returncode == 2
        assert "disabled or altered" in result.stderr
    assert not marker.exists()


def test_failed_policy_attachment_never_starts_harness(runtime, monkeypatch) -> None:
    _, url = runtime
    started: list[object] = []
    monkeypatch.setattr(
        "stance.services.sessions.COST_BUDGET_HANDLER", "not.a.registered.policy"
    )
    monkeypatch.setattr("stance.services.launch.run_native", started.append)
    with httpx.Client(base_url=url) as client:
        before = {s["id"] for s in client.get("/v1/sessions").json()["data"]}
        result = CliRunner().invoke(
            app,
            [
                "claude",
                "--budget-usd",
                "5",
                "--model",
                "claude-sonnet-5-5",
                "--server",
                url,
            ],
        )
        after = {s["id"] for s in client.get("/v1/sessions").json()["data"]}
    assert result.exit_code == 2, result.output
    assert "Attaching the budget policy failed" in result.output
    assert started == []
    assert after == before  # the half-created session was deleted


def test_config_selects_existing_server_and_default_budget(
    runtime, tmp_path: Path
) -> None:
    env, local_url = runtime
    other_env = {
        **clean_env(tmp_path),
        "OMNIGENT_DATA_DIR": str(tmp_path / "other-data"),
    }
    started = run([OMNIGENT, "server", "--background"], other_env)
    assert started.returncode == 0, started.stderr
    status = json.loads(run([OMNIGENT, "server", "status", "--json"], other_env).stdout)
    other_url = status["url"]
    assert other_url != local_url
    config = Path(env["XDG_CONFIG_HOME"]) / "stance" / "config.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    try:
        config.write_text(f'budget_usd = 3\n[server]\nurl = "{other_url}"\n')
        result = stance(env, "claude", "--model", "claude-sonnet-5-5", "--prepare")
        assert result.returncode == 0, result.stderr
        assert server_url_from(result.stdout) == other_url
        assert "existing server" in result.stdout
        sid = session_id_from(result.stdout)
        assert f"stance attach {sid} --server {other_url}" in result.stdout
        with httpx.Client(base_url=other_url) as client:
            policy = client.get(f"/v1/sessions/{sid}/policies").json()["data"][0]
            assert policy["factory_params"] == {"max_cost_usd": 3.0}

        # A host daemon for that server started outside Stance (no opt-outs)
        # in the user's own Omnigent data dir is refused, not adopted.
        daemon = run(
            [
                OMNIGENT,
                "start",
                "--server",
                other_url,
                "--no-open",
                "--non-interactive",
            ],
            env,
        )
        assert daemon.returncode == 0, daemon.stderr
        try:
            result = stance(env, "claude", "--model", "claude-sonnet-5-5", "--prepare")
            assert result.returncode == 2
            assert "without Stance's telemetry opt-outs" in result.stderr
            assert "also stops the sessions and subagents" in result.stderr
        finally:
            run([OMNIGENT, "host", "stop", "--server", other_url], env)

        # CLI flags override the config file.
        result = stance(
            env,
            "claude",
            "--budget-usd",
            "4",
            "--server",
            "local",
            "--model",
            "claude-sonnet-5-5",
            "--prepare",
        )
        assert result.returncode == 0, result.stderr
        assert server_url_from(result.stdout) == local_url
        sid = session_id_from(result.stdout)
        with httpx.Client(base_url=local_url) as client:
            policy = client.get(f"/v1/sessions/{sid}/policies").json()["data"][0]
            assert policy["factory_params"] == {"max_cost_usd": 4.0}
    finally:
        config.unlink(missing_ok=True)
        run([OMNIGENT, "stop", "--force"], other_env)


def test_refuses_ordinary_local_daemon_selected_by_its_url(
    runtime, tmp_path: Path
) -> None:
    """Upstream reuses a local-mode daemon (target "local") for its own
    server's concrete URL, so selecting that URL must not bypass the check."""
    env, _ = runtime
    user_env = clean_env(tmp_path)  # opted in, no Stance settings
    # A user's `omnigent stop` also stops Stance's server on the default port,
    # so the user's server can take over the URL in Stance's stale record.
    run([OMNIGENT, "stop", "--force"], user_env)
    started = run([OMNIGENT, "start", "--no-open", "--non-interactive"], user_env)
    assert started.returncode == 0, started.stderr
    try:
        status = json.loads(
            run([OMNIGENT, "server", "status", "--json"], user_env).stdout
        )
        assert status["daemon_attached"], status
        stance_env = {
            **env,
            "HOME": user_env["HOME"],
            "OMNIGENT_CONFIG_HOME": user_env["OMNIGENT_CONFIG_HOME"],
        }
        result = stance(
            stance_env,
            "claude",
            "--budget-usd",
            "5",
            "--model",
            "claude-sonnet-5-5",
            "--server",
            status["url"],
            "--prepare",
        )
        assert result.returncode == 2, result.stdout
        assert "without Stance's telemetry opt-outs" in result.stderr
        assert "`omnigent stop`" in result.stderr
    finally:
        run([OMNIGENT, "stop", "--force"], user_env)


def test_existing_server_uses_existing_omnigent_login(tmp_path: Path) -> None:
    """`omnigent login` tokens live in the user's Omnigent data dir, which
    Stance switches back to for an existing server."""
    env = clean_env(tmp_path)
    url = "https://omnigent.example.invalid"
    store = (
        "from omnigent.cli_auth import store_token; import time\n"
        f"store_token({url!r}, 'tok-123', 'me@example.com', time.time() + 3600)\n"
    )
    user_dir = str(tmp_path / "home" / ".omnigent")
    stored = run([sys.executable, "-c", store], {**env, "OMNIGENT_DATA_DIR": user_dir})
    assert stored.returncode == 0, stored.stderr
    probe = (
        "import stance, os\n"
        "from omnigent.chat import _remote_headers\n"
        "from stance.privacy import use_user_data_dir\n"
        f"before = 'Authorization' in _remote_headers({url!r}, host_id=None)\n"
        "use_user_data_dir(os.environ)\n"
        f"print(before, 'Authorization' in _remote_headers({url!r}, host_id=None))\n"
    )
    result = run([sys.executable, "-c", probe], env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["False", "True"]
