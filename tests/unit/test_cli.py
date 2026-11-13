"""CLI input validation and configuration precedence, without a server."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from stance.cli import app
from stance.config import UserConfig, config_path, load_user_config
from stance.services import launch
from stance.services.server import ServerTarget
from stance.services.sessions import Harness


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("ANTHROPIC_MODEL", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Stub the server side; record what a launch would have created."""
    calls: list[dict[str, object]] = []
    server = ServerTarget(url="http://127.0.0.1:6767", local=True)
    monkeypatch.setattr(launch, "check_model_pricing", lambda model: None)
    monkeypatch.setattr(launch, "resolve_server", lambda cli, configured: server)

    def create(client, harness, **kwargs):  # noqa: ANN001, ANN202
        calls.append({"harness": harness, **kwargs})
        return "sess123"

    monkeypatch.setattr(launch, "create_protected_session", create)
    return calls


def _write_config(xdg: Path, text: str) -> None:
    path = xdg / "xdg" / "stance" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(text)


def invoke(*args: str):
    return CliRunner().invoke(app, list(args))


def test_budget_is_required_without_a_config_default(xdg: Path, captured) -> None:
    result = invoke("claude", "--model", "claude-sonnet-5-5", "--prepare")
    assert result.exit_code == 2
    assert "A budget is required" in result.output
    assert captured == []


@pytest.mark.parametrize("budget", ["0", "-1", "nan", "inf", "-inf"])
def test_rejects_nonpositive_or_nonfinite_budget(
    xdg: Path, captured, budget: str
) -> None:
    result = invoke(
        "codex", "--budget-usd", budget, "--model", "gpt-6.1-sol", "--prepare"
    )
    assert result.exit_code == 2
    assert "positive, finite" in result.output
    assert captured == []


@pytest.mark.parametrize("value", ["0", "-2.5", "nan", "inf", '"5"', "true"])
def test_rejects_invalid_config_budget(xdg: Path, captured, value: str) -> None:
    _write_config(xdg, f"budget_usd = {value}\n")
    result = invoke("claude", "--model", "claude-sonnet-5-5", "--prepare")
    assert result.exit_code == 2
    assert "Invalid Stance config" in result.output
    assert captured == []


def test_rejects_unknown_config_keys(xdg: Path, captured) -> None:
    _write_config(xdg, "[server]\nadress = 'http://typo'\n")
    result = invoke("claude", "--budget-usd", "5", "--model", "x", "--prepare")
    assert result.exit_code == 2
    assert "adress" in result.output


def test_config_default_budget_and_cli_override(xdg: Path, captured) -> None:
    _write_config(xdg, "budget_usd = 3.25\n[claude]\nmodel = 'claude-opus-5-5'\n")
    result = invoke("claude", "--prepare")
    assert result.exit_code == 0, result.output
    assert captured[-1]["budget_usd"] == 3.25
    assert captured[-1]["model"] == "claude-opus-5-5"
    assert "budget:       $3.25 USD" in result.output

    result = invoke(
        "claude", "--budget-usd", "9", "--model", "claude-sonnet-5-5", "--prepare"
    )
    assert result.exit_code == 0, result.output
    assert captured[-1]["budget_usd"] == 9.0
    assert captured[-1]["model"] == "claude-sonnet-5-5"


def test_prepare_prints_attach_details_and_starts_nothing(
    xdg: Path, captured, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[object] = []
    monkeypatch.setattr(launch, "run_native", started.append)
    result = invoke(
        "codex",
        "--budget-usd",
        "5",
        "--model",
        "gpt-6.1-sol",
        "--prepare",
        "--",
        "--foo",
    )
    assert result.exit_code == 0, result.output
    assert started == []
    assert captured[-1]["harness"] is Harness.CODEX
    assert captured[-1]["harness_args"] == ("--foo",)
    for expected in (
        "session:      sess123",
        "server:       http://127.0.0.1:6767 (port 6767, Stance-owned local server)",
        "conversation: http://127.0.0.1:6767/c/sess123",
        "attach:       stance attach sess123 --server local",
    ):
        assert expected in result.output


@pytest.mark.parametrize(
    ("harness", "args"),
    [
        ("claude", ["--model", "claude-opus-5-5"]),
        ("claude", ["--verbose", "--model=claude-opus-5-5"]),
        ("codex", ["-m", "gpt-6.1-terra"]),
        ("codex", ["--model=gpt-6.1-terra"]),
        ("codex", ["-c", "model=gpt-6.1-terra"]),
        ("codex", ["--config", 'model="gpt-6.1-terra"']),
        ("codex", ["-cmodel=gpt-6.1-terra"]),
    ],
)
def test_rejects_model_selection_in_harness_args(
    xdg: Path, captured, harness: str, args: list[str]
) -> None:
    model = "claude-sonnet-5-5" if harness == "claude" else "gpt-6.1-sol"
    result = invoke(
        harness, "--budget-usd", "5", "--model", model, "--prepare", "--", *args
    )
    assert result.exit_code == 2
    assert "Use Stance's --model" in result.output
    assert captured == []


def test_allows_other_harness_args(xdg: Path, captured) -> None:
    result = invoke(
        "codex",
        "--budget-usd",
        "5",
        "--model",
        "gpt-6.1-sol",
        "--prepare",
        "--",
        "-c",
        "model_reasoning_effort=high",
        "--search",
    )
    assert result.exit_code == 0, result.output
    assert captured[-1]["harness_args"] == (
        "-c",
        "model_reasoning_effort=high",
        "--search",
    )


def test_xdg_config_path_falls_back_to_home(tmp_path: Path) -> None:
    assert (
        config_path({"XDG_CONFIG_HOME": str(tmp_path)})
        == tmp_path / "stance/config.toml"
    )
    assert (
        config_path({"HOME": str(tmp_path)}) == tmp_path / ".config/stance/config.toml"
    )
    assert load_user_config(tmp_path / "missing.toml") == UserConfig()


def test_model_precedence_and_actionable_error(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        '{"model": "opus", '
        '"env": {"ANTHROPIC_MODEL": "us.anthropic.claude-opus-5-5-v1:0"}}'
    )
    env = {"HOME": str(tmp_path)}
    config = UserConfig.model_validate({"claude": {"model": "claude-sonnet-5-5"}})
    assert launch.resolve_model(Harness.CLAUDE, None, UserConfig(), env) == (
        "us.anthropic.claude-opus-5-5-v1:0"
    )
    assert (
        launch.resolve_model(Harness.CLAUDE, None, config, env) == "claude-sonnet-5-5"
    )
    assert launch.resolve_model(Harness.CLAUDE, "cli-model", config, env) == "cli-model"
    assert (
        launch.resolve_model(
            Harness.CLAUDE, None, UserConfig(), {**env, "ANTHROPIC_MODEL": "from-env"}
        )
        == "from-env"
    )

    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('model = "gpt-6.1-sol"\n')
    assert launch.resolve_model(Harness.CODEX, None, UserConfig(), env) == "gpt-6.1-sol"
    with pytest.raises(launch.LaunchError, match="Pass --model"):
        launch.resolve_model(
            Harness.CODEX, None, UserConfig(), {"HOME": str(tmp_path / "x")}
        )
