"""Budget-protected native Omnigent sessions for Claude Code and Codex.

Session creation reuses upstream's native-wrapper spec, bundle, and labels
(private helpers pinned to omnigent==0.20.0) so the server treats the session
exactly like one ``omnigent claude``/``omnigent codex`` creates. The session is
created without initial input or launch metadata; the budget policy is attached
and read back before anything can start a harness.
"""

import json
import math
import tempfile
from enum import StrEnum
from pathlib import Path

import httpx
import structlog

from stance.services.server import COST_BUDGET_HANDLER

log = structlog.get_logger(__name__)

POLICY_NAME = "stance-budget"
WRAPPER_LABEL_KEY = "omnigent.wrapper"


class Harness(StrEnum):
    """Native harnesses Stance can wrap."""

    CLAUDE = "claude"
    CODEX = "codex"


class SessionError(Exception):
    """A protected session could not be created, verified, or found."""


def _native(harness: Harness):  # noqa: ANN202 - returns an upstream module
    if harness is Harness.CLAUDE:
        from omnigent.harnesses.claude_native import main
    else:
        from omnigent.harnesses.codex_native import main
    return main


def _wrapper_label(harness: Harness) -> str:
    return _native(harness)._SESSION_LABELS[WRAPPER_LABEL_KEY]


def _bundle(harness: Harness, model: str) -> bytes:
    from omnigent.chat import _bundle_agent

    native = _native(harness)
    with tempfile.TemporaryDirectory(prefix="stance-") as tmpdir:
        if harness is Harness.CLAUDE:
            spec = native._materialize_claude_agent_spec(Path(tmpdir))
        else:
            spec = native._materialize_codex_agent_spec(Path(tmpdir), model=model)
        return _bundle_agent(spec)


def _check(resp: httpx.Response, action: str) -> dict[str, object]:
    if resp.status_code >= 400:
        raise SessionError(f"{action} failed ({resp.status_code}): {resp.text.strip()}")
    body = resp.json()
    if not isinstance(body, dict):
        raise SessionError(f"{action} returned an unexpected response: {body!r}")
    return body


def verify_budget_policy(client: httpx.Client, session_id: str) -> float:
    """Return the enabled Stance budget on *session_id*, or raise.

    Reads the policy back from the server rather than trusting the create
    response, so a server that dropped or disabled it is caught.
    """
    body = _check(
        client.get(f"/v1/sessions/{session_id}/policies"), "Reading session policies"
    )
    data = body.get("data")
    for policy in data if isinstance(data, list) else []:
        if not isinstance(policy, dict) or policy.get("name") != POLICY_NAME:
            continue
        params = policy.get("factory_params") or {}
        budget = params.get("max_cost_usd") if isinstance(params, dict) else None
        if (
            policy.get("enabled") is True
            and policy.get("type") == "python"
            and policy.get("handler") == COST_BUDGET_HANDLER
            and set(params) == {"max_cost_usd"}
            and isinstance(budget, int | float)
            and math.isfinite(budget)
            and budget > 0
        ):
            return float(budget)
        raise SessionError(
            f"Session {session_id} has a {POLICY_NAME!r} policy that is disabled or "
            f"altered: {policy}"
        )
    raise SessionError(f"Session {session_id} has no Stance budget policy attached.")


def _discard(client: httpx.Client, session_id: str) -> None:
    try:
        client.delete(f"/v1/sessions/{session_id}")
    except httpx.HTTPError:
        log.warning("stance.session.discard_failed", session_id=session_id)


def create_protected_session(
    client: httpx.Client,
    harness: Harness,
    *,
    budget_usd: float,
    model: str,
    harness_args: tuple[str, ...] = (),
    workspace: Path,
) -> str:
    """Create a native root session with a verified budget policy.

    Order matters: create (no input, no launch args) -> attach policy ->
    read it back -> persist launch args. On any failure after creation the
    session is deleted so no unprotected session is left behind.
    """
    metadata = {"labels": dict(_native(harness)._SESSION_LABELS)}
    body = _check(
        client.post(
            "/v1/sessions",
            data={"metadata": json.dumps(metadata)},
            files={
                "bundle": (
                    f"{harness}-native-ui.tar.gz",
                    _bundle(harness, model),
                    "application/gzip",
                )
            },
            timeout=120.0,
        ),
        "Creating the Omnigent session",
    )
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise SessionError(f"Session creation returned no session_id: {body}")
    try:
        _check(
            client.post(
                f"/v1/sessions/{session_id}/policies",
                json={
                    "name": POLICY_NAME,
                    "type": "python",
                    "handler": COST_BUDGET_HANDLER,
                    "factory_params": {"max_cost_usd": budget_usd},
                },
            ),
            "Attaching the budget policy",
        )
        attached = verify_budget_policy(client, session_id)
        if attached != budget_usd:
            raise SessionError(
                f"Server recorded budget {attached}, expected {budget_usd}."
            )
        launch_args = list(harness_args)
        if harness is Harness.CLAUDE:
            # Pin Claude Code to the model Stance priced. Codex gets it in the spec.
            launch_args = ["--model", model, *launch_args]
        if launch_args:
            _check(
                client.patch(
                    f"/v1/sessions/{session_id}",
                    json={"terminal_launch_args": launch_args},
                ),
                "Saving harness launch arguments",
            )
    except BaseException:
        _discard(client, session_id)
        raise
    _record_workspace(harness, session_id, workspace)
    return session_id


def _record_workspace(harness: Harness, session_id: str, workspace: Path) -> None:
    """Record the launch cwd the way upstream does for fresh sessions.

    Attaching from another directory then gets upstream's switch-directory
    prompt instead of starting the harness in the wrong workspace.
    """
    if harness is Harness.CLAUDE:
        from omnigent.harnesses.claude_native.state import write_launch_state
    else:
        from omnigent.harnesses.codex_native.state import write_launch_state
    try:
        write_launch_state(session_id, str(workspace.resolve()))
    except OSError:
        log.warning("stance.session.workspace_not_recorded", session_id=session_id)


def session_harness(client: httpx.Client, session_id: str) -> Harness:
    """Return which native harness wraps *session_id*."""
    resp = client.get(f"/v1/sessions/{session_id}")
    if resp.status_code == 404:
        raise SessionError(f"Session {session_id} was not found on this server.")
    body = _check(resp, "Reading the session")
    labels = body.get("labels") or {}
    wrapper = labels.get(WRAPPER_LABEL_KEY) if isinstance(labels, dict) else None
    for harness in Harness:
        if wrapper == _wrapper_label(harness):
            return harness
    raise SessionError(
        f"Session {session_id} is not a Claude Code or Codex native root session "
        f"(wrapper label {wrapper!r})."
    )


def session_model(client: httpx.Client, session_id: str) -> str | None:
    """Return the session's current model: its override, else its spec model."""
    body = _check(client.get(f"/v1/sessions/{session_id}"), "Reading the session")
    for key in ("model_override", "llm_model"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None
