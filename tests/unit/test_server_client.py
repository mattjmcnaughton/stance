"""HTTP behavior of Stance's server clients: proxies, existing login, env."""

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path

import pytest
from omnigent.cli_auth import load_token, store_token

from stance.privacy import apply_privacy_env
from stance.services.server import (
    COST_BUDGET_HANDLER,
    ServerTarget,
    connect_existing_server,
)


class _Recorder(BaseHTTPRequestHandler):
    """A tiny Omnigent stand-in that also works as a forward proxy."""

    seen: list[tuple[str, str | None]]
    valid_tokens: set[str]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def _send(self, status: int, body: object) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        self.seen.append((self.path, self.headers.get("authorization")))
        auth = self.headers.get("authorization", "")
        if self.valid_tokens and auth.removeprefix("Bearer ") not in self.valid_tokens:
            return self._send(401, {"error": "unauthorized"})
        if self.path.endswith("/v1/info"):
            return self._send(200, {"server_version": version("omnigent")})
        if self.path.endswith("/v1/policy-registry"):
            return self._send(200, {"data": [{"handler": COST_BUDGET_HANDLER}]})
        return self._send(200, {"ok": True})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", 0))
        body = self.rfile.read(length).decode()
        self.seen.append((self.path, body))
        if self.path.endswith("/oauth/token") and "refresh_token=refresh-1" in body:
            return self._send(200, {"access_token": "fresh-1", "expires_in": 3600})
        return self._send(400, {"error": "bad grant"})


@pytest.fixture
def fake() -> Iterator[tuple[str, type[_Recorder]]]:
    handler = type("Handler", (_Recorder,), {"seen": [], "valid_tokens": set()})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}", handler
    server.shutdown()


def test_loopback_bypasses_inherited_proxy_but_remote_uses_it(
    fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    proxy_url, proxy = fake
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "all_proxy"):
        monkeypatch.setenv(name, proxy_url)

    # Loopback: a direct connection to a closed port fails; it must not be
    # answered by the proxy.
    with (
        ServerTarget("http://127.0.0.1:1", local=True).client() as client,
        pytest.raises(Exception, match="(?i)connect"),
    ):
        client.get("/v1/sessions")
    assert proxy.seen == []

    with ServerTarget("http://omnigent.example.invalid", local=False).client() as c:
        assert c.get("/v1/sessions").status_code == 200
    assert proxy.seen[0][0] == "http://omnigent.example.invalid/v1/sessions"


@pytest.fixture
def user_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The user's own Omnigent data dir, where `omnigent login` stores tokens."""
    user = tmp_path / "user-omnigent"
    monkeypatch.setenv("STANCE_USER_OMNIGENT_DATA_DIR", str(user))
    monkeypatch.setenv("OMNIGENT_DATA_DIR", str(user))
    monkeypatch.delenv("OMNIGENT_REMOTE_AUTH_TOKEN", raising=False)
    return user


def test_existing_server_renews_an_expired_refreshable_login(fake, user_dir) -> None:
    url, server = fake
    server.valid_tokens = {"fresh-1"}
    store_token(
        url, "stale-1", "me@example.com", time.time() - 60, refresh_token="refresh-1"
    )

    target = connect_existing_server(url)

    assert target.headers["Authorization"] == "Bearer fresh-1"
    assert load_token(url) == "fresh-1"
    assert any(path.endswith("/oauth/token") for path, _ in server.seen)


def test_explicit_token_wins_over_stored_login(
    fake, user_dir, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, server = fake
    server.valid_tokens = {"explicit-1"}
    store_token(
        url, "stale-1", "me@example.com", time.time() - 60, refresh_token="refresh-1"
    )
    monkeypatch.setenv("OMNIGENT_REMOTE_AUTH_TOKEN", "explicit-1")

    target = connect_existing_server(url)

    assert target.headers["Authorization"] == "Bearer explicit-1"
    assert not any(path.endswith("/oauth/token") for path, _ in server.seen)


def test_existing_server_daemon_forwards_only_named_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Documented contract: for an existing server, Omnigent's host daemon
    drops ambient provider keys unless named in the passthrough list, and
    Stance's own passthrough entries must not displace the user's."""
    from omnigent.cli import _build_host_daemon_env

    env = {
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "OMNIGENT_RUNNER_ENV_PASSTHROUGH": "OPENAI_API_KEY",
    }
    apply_privacy_env(env)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OMNIGENT_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic")
    monkeypatch.setenv("AWS_BEARER_TOKEN_BEDROCK", "synthetic")

    daemon_env = _build_host_daemon_env(server_url="https://omnigent.example.invalid")

    assert daemon_env.get("OPENAI_API_KEY") == "sk-synthetic"
    assert "AWS_BEARER_TOKEN_BEDROCK" not in daemon_env
    assert daemon_env["OMNIGENT_DISABLE_TELEMETRY"] == "1"
    assert daemon_env["OMNIGENT_ANALYTICS"] == "0"
