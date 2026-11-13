"""Omnigent server selection: Stance-owned local server or an existing server."""

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import psutil
import structlog

from stance.privacy import (
    PRIVACY_ENV,
    missing_privacy_settings,
    stance_state_dir,
    use_user_data_dir,
)

log = structlog.get_logger(__name__)

COST_BUDGET_HANDLER = "omnigent.policies.builtins.cost.cost_budget"
LOCAL_ALIASES = frozenset({"", "local"})


class ServerError(Exception):
    """The Omnigent server could not be started, verified, or reached."""


@dataclass(frozen=True)
class ServerTarget:
    """An Omnigent server Stance talks to.

    :param url: Concrete base URL, e.g. ``"http://127.0.0.1:6767"``.
    :param local: ``True`` for the Stance-owned local server.
    :param headers: Auth headers from existing Omnigent login state. Never
        printed.
    """

    url: str
    local: bool
    headers: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def cli_server_arg(self) -> str:
        """The ``--server`` value for the native Omnigent CLI.

        The Stance-owned server uses Omnigent's local mode (``""``), so the
        daemon gets the same credential environment as plain ``omnigent
        claude``/``omnigent codex``, inside Stance's data dir.
        """
        return "" if self.local else self.url

    def client(self) -> httpx.Client:
        """Return an HTTP client for this server.

        Loopback traffic never goes through inherited ``HTTP(S)_PROXY`` /
        ``ALL_PROXY`` settings, matching upstream's own local clients; remote
        servers keep the environment's proxy configuration.
        """
        from omnigent_client._http import is_loopback_url

        return httpx.Client(
            base_url=self.url,
            headers=self.headers,
            timeout=60.0,
            trust_env=not is_loopback_url(self.url),
        )


def omnigent_cli(*args: str) -> list[str]:
    """Return argv for the pinned Omnigent CLI in Stance's own environment."""
    return [sys.executable, "-P", "-m", "omnigent.cli", *args]


def _run_omnigent(*args: str, timeout: float = 180.0) -> str:
    result = subprocess.run(
        omnigent_cli(*args),
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ServerError(f"`omnigent {' '.join(args)}` failed: {detail}")
    return result.stdout


def _record_path() -> Path:
    return stance_state_dir() / "local-server.json"


def _read_record() -> dict[str, object] | None:
    try:
        data = json.loads(_record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_record(pid: int, url: str) -> None:
    path = _record_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"pid": pid, "url": url, "privacy": PRIVACY_ENV}
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


def process_privacy_ok(pid: int) -> bool | None:
    """Whether a live process's environment carries Stance's opt-outs.

    :returns: ``None`` when the environment cannot be read on this platform.
    """
    try:
        environ = psutil.Process(pid).environ()
    except (psutil.Error, OSError):
        return None
    return not missing_privacy_settings(environ)


def _daemon_pids() -> list[int]:
    from omnigent.host.daemon_lifecycle import read_daemon_records

    return [
        record.pid for record in read_daemon_records() if psutil.pid_exists(record.pid)
    ]


def local_server_status() -> dict[str, object]:
    """Return ``omnigent server status --json`` for Stance's data dir."""
    return json.loads(_run_omnigent("server", "status", "--json"))


def _reusable(status: dict[str, object]) -> bool:
    pid = status.get("pid")
    if not status.get("running") or not isinstance(pid, int):
        return False
    record = _read_record()
    recorded = bool(
        record and record.get("pid") == pid and record.get("privacy") == PRIVACY_ENV
    )
    for check_pid in (pid, *_daemon_pids()):
        live = process_privacy_ok(check_pid)
        if live is False or (live is None and check_pid == pid and not recorded):
            log.warning("stance.local_server.untrusted", pid=check_pid)
            return False
    return True


def ensure_local_server() -> ServerTarget:
    """Start or reuse the Stance-owned local Omnigent server.

    A running server (or host daemon) in Stance's data dir is reused only when
    Stance recorded starting it with the current privacy settings or its live
    environment shows them; otherwise everything in the namespace is stopped
    and a fresh server is started with Stance's environment.
    """
    status = local_server_status()
    if status.get("running") and not _reusable(status):
        _run_omnigent("stop", "--force")
        status = {"running": False}
    if not status.get("running"):
        _run_omnigent("server", "--background")
        status = local_server_status()
        pid, url = status.get("pid"), status.get("url")
        if (
            not status.get("running")
            or not isinstance(pid, int)
            or not isinstance(url, str)
        ):
            raise ServerError(f"Stance's local Omnigent server did not start: {status}")
        _write_record(pid, url)
    url = status.get("url")
    if not isinstance(url, str):
        raise ServerError(f"Stance's local Omnigent server reported no URL: {status}")
    return ServerTarget(url=url.rstrip("/"), local=True)


def stop_local_server() -> bool:
    """Stop the Stance-owned server and host daemons. Returns whether one ran."""
    was_running = bool(local_server_status().get("running"))
    _run_omnigent("stop", "--force")
    _record_path().unlink(missing_ok=True)
    return was_running


def _major_minor(value: str) -> tuple[str, ...]:
    return tuple(value.split(".")[:2])


def _check_existing_daemons(url: str) -> None:
    """Refuse to hand over to a live host daemon for *url* without opt-outs.

    Upstream reuses a running daemon for a server as-is, and its runners
    inherit that daemon's environment. A daemon Stance starts carries the
    opt-outs; one started some other way may not, and Stance will not adopt
    it.
    """
    from omnigent.host.daemon_lifecycle import read_daemon_records

    netloc = urlsplit(url).netloc.lower()
    for record in read_daemon_records():
        # A local-mode daemon (target "local") also serves its own server's
        # concrete URL, recorded as resolved_server_url; upstream reuses it for
        # that URL. Without a recorded URL it cannot be ruled out.
        urls = {record.server_url, record.target, record.resolved_server_url}
        local_unknown = record.target == "local" and not record.resolved_server_url
        if not local_unknown and netloc not in {
            urlsplit(u).netloc.lower() for u in urls if u
        }:
            continue
        if not psutil.pid_exists(record.pid) or process_privacy_ok(record.pid):
            continue
        stop = (
            "omnigent stop"
            if record.target == "local"
            else f"omnigent host stop --server {url}"
        )
        raise ServerError(
            f"An Omnigent host daemon serving {url} (pid {record.pid}) is running "
            "without Stance's telemetry opt-outs, or its settings cannot be read, "
            "so Stance will not launch through it. Stopping it also stops the "
            "sessions and subagents it is running: finish or deliberately end "
            f"that work, then run `{stop}` and retry. Stance then starts a daemon "
            "with its privacy settings."
        )


def connect_existing_server(url: str) -> ServerTarget:
    """Verify an operator-managed server's version and policy APIs.

    Switches this process (and the native CLI it starts) to the user's own
    Omnigent data dir, where existing ``omnigent login`` state and host
    daemons for remote servers live. Stance does not manage this server's
    privacy or configuration.
    """
    from omnigent.chat import _REMOTE_AUTH_TOKEN_ENV, _remote_headers
    from omnigent.cli_auth import (
        REFRESH_MIN_REMAINING_SECONDS,
        load_token,
        refresh_stored_token,
    )

    use_user_data_dir(os.environ)
    url = url.rstrip("/")
    _check_existing_daemons(url)
    if not os.environ.get(_REMOTE_AUTH_TOKEN_ENV, "").strip() and (
        load_token(url, min_remaining_seconds=REFRESH_MIN_REMAINING_SECONDS) is None
    ):
        # Renew an expired or near-expiry `omnigent login` from its refresh
        # grant, as upstream's runner does. A no-op without refresh material.
        refresh_stored_token(url)
    target = ServerTarget(
        url=url, local=False, headers=_remote_headers(url, host_id=None)
    )
    try:
        with target.client() as client:
            info = client.get("/v1/info")
            info.raise_for_status()
            registry = client.get("/v1/policy-registry")
            registry.raise_for_status()
    except httpx.HTTPError as exc:
        raise ServerError(f"Could not use the Omnigent server at {url}: {exc}") from exc
    server_version = str(info.json().get("server_version") or "")
    client_version = version("omnigent")
    if _major_minor(server_version) != _major_minor(client_version):
        raise ServerError(
            f"Omnigent server at {url} runs version {server_version or 'unknown'}; "
            f"Stance is pinned to omnigent {client_version}. Use a matching server."
        )
    entries = registry.json().get("data", [])
    if not any(
        isinstance(e, dict) and e.get("handler") == COST_BUDGET_HANDLER for e in entries
    ):
        raise ServerError(
            f"Omnigent server at {url} does not offer {COST_BUDGET_HANDLER}."
        )
    return target


def resolve_server(explicit: str | None, configured: str | None) -> ServerTarget:
    """Pick the server: CLI flag, then config, then the Stance-owned local one."""
    choice = explicit if explicit is not None else configured
    if choice is None or choice.strip().casefold() in LOCAL_ALIASES:
        return ensure_local_server()
    record = _read_record()
    if record and record.get("url") == choice.rstrip("/"):
        # The printed URL of Stance's own server still means local mode.
        return ensure_local_server()
    return connect_existing_server(choice)
