"""Stance-owned local server: privacy in real process environments and reuse."""

from pathlib import Path

import psutil

from tests.support import OMNIGENT, PRIVACY, clean_env, run, stance


def _server_pid(env: dict[str, str]) -> int:
    result = stance(env, "server", "status")
    assert "running at" in result.stdout, result.stdout
    return int(result.stdout.split("(pid ")[1].split(")")[0])


def _privacy_of(pid: int) -> dict[str, str | None]:
    environ = psutil.Process(pid).environ()
    return {name: environ.get(name) for name in PRIVACY}


def test_reuses_only_a_server_started_with_stance_privacy(tmp_path: Path) -> None:
    env = clean_env(tmp_path)  # inherits OMNIGENT_ANALYTICS=1 etc.
    try:
        assert stance(env, "server", "start").returncode == 0
        first = _server_pid(env)
        assert _privacy_of(first) == PRIVACY
        data_dir = Path(env["XDG_STATE_HOME"]) / "stance" / "omnigent"
        assert psutil.Process(first).environ()["OMNIGENT_DATA_DIR"] == str(data_dir)

        assert stance(env, "server", "start").returncode == 0
        assert _server_pid(env) == first  # reused

        # A server started in Stance's namespace without the opt-outs (for
        # example by hand) must not be adopted.
        assert stance(env, "server", "stop").returncode == 0
        foreign_env = {**env, "OMNIGENT_DATA_DIR": str(data_dir)}
        assert run([OMNIGENT, "server", "--background"], foreign_env).returncode == 0
        foreign = _server_pid(env)
        assert _privacy_of(foreign)["OMNIGENT_DISABLE_TELEMETRY"] is None

        assert stance(env, "server", "start").returncode == 0
        replacement = _server_pid(env)
        assert replacement != foreign
        assert (
            not psutil.pid_exists(foreign)
            or psutil.Process(foreign).status() == "zombie"
        )
        assert _privacy_of(replacement) == PRIVACY
    finally:
        stance(env, "server", "stop")
