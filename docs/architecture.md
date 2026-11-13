# Architecture

## Overview

Stance is a thin session/policy wrapper around stock Omnigent (pinned
`omnigent==0.20.0`). It creates a native Claude Code or Codex root session,
attaches Omnigent's built-in `cost_budget` policy, verifies it, and then hands
the terminal to `omnigent claude|codex --resume <session>`. Omnigent owns the
harness launch, hooks, usage accounting, and policy enforcement; Stance owns
input validation, server selection, privacy settings, and ordering.

## Project Structure

```
src/stance/
  __init__.py          # applies privacy env before anything imports Omnigent
  privacy.py           # opt-out env vars and Stance's Omnigent data dir (no Omnigent imports)
  cli.py               # Typer app: claude, codex, attach, server
  config.py            # logging settings, XDG config.toml, budget validation
  logging.py           # structlog setup (stderr)
  commands/
    launch.py          # claude / codex / attach: parse args, print connection details
    server.py          # server start / status / stop
  services/
    launch.py          # budget + model resolution, prepare/find session, native handover
    pricing.py         # exact-match catalog pricing preflight
    server.py          # Stance-owned local server reuse; existing-server checks
    sessions.py        # native session creation, policy attach + read-back
tests/
  unit/                # CLI validation, config precedence, privacy bootstrap
  integration/         # real local Omnigent server, real policy evaluation
  e2e/                 # full handover with a fake harness executable
```

## Launch sequence

1. Resolve budget (CLI, then config) and model (CLI, config, harness settings).
   Raw harness arguments that select a model (`--model`; Codex `-m`,
   `-c model=`) are rejected.
2. Require an exact priced catalog entry for the model.
3. Resolve the server: the Stance-owned local server (`omnigent server
   --background` with `OMNIGENT_DATA_DIR` in Stance's state dir), or an
   existing server checked for version and policy registry. For an existing
   server Stance switches back to the user's Omnigent data dir (login tokens,
   remote daemons), renews an expired login from its refresh grant, and refuses
   any live host daemon serving that URL (including a local-mode daemon via its
   `resolved_server_url`) whose environment lacks the opt-outs. HTTP clients
   ignore environment proxies for loopback URLs.
4. `POST /v1/sessions` with upstream's native wrapper bundle and labels, and no
   initial input or launch metadata.
5. `POST /v1/sessions/{id}/policies` (`cost_budget`, `max_cost_usd` only), then
   `GET` it back and require it enabled and unchanged. Any failure deletes the
   session.
6. Persist harness launch args (Claude: `--model`), record the workspace.
7. `--prepare` stops here. Otherwise run `omnigent <harness> --server ...
   --resume <id>` in the foreground. Codex also gets `--model <model>`;
   without it `omnigent codex` writes Omnigent's configured default model over
   the session's.

`stance attach` repeats step 5's read-back on the existing session before step
7, and for Codex passes the session's stored model (`model_override`, else the
spec's `llm_model`).

## Upstream coupling

Private Omnigent helpers used, all pinned to 0.20.0:
`harnesses.{claude,codex}_native.main._SESSION_LABELS`,
`_materialize_{claude,codex}_agent_spec`, `chat._bundle_agent`,
`chat._remote_headers`, `chat._REMOTE_AUTH_TOKEN_ENV`,
`onboarding.providers._catalog_lookup_targets`, and
`omnigent_client._http.is_loopback_url`.
Everything else goes through the public CLI and HTTP API.

## Conventions

- Commands are thin: parse args into an input dataclass, call services, print.
- Services hold the logic and raise typed errors; commands map them to exit 2.
- Version lives in `pyproject.toml`, read via `importlib.metadata`.
