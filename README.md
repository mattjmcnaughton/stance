# stance

Budget-protected Claude Code and Codex sessions, powered by
[Omnigent](https://github.com/omnigent-ai/omnigent).

Stance starts a native Omnigent session for Claude Code or Codex, attaches
Omnigent's stock session cost-budget policy, reads it back from the server, and
only then hands your terminal to the harness. The goal is protection against
accidental unattended overspend, not an unbreakable spending cap.

## Requirements

- macOS or Linux, Python 3.12 or 3.13 (uv installs one if needed)
- [uv](https://docs.astral.sh/uv/) and `tmux`
- `claude` (Claude Code) and/or `codex` on `PATH`, already authenticated and
  configured the way you want. Stance does not store or manage credentials. If
  `omnigent claude` / `omnigent codex` already work for you, Stance uses the
  same configuration.

The harness runs under Omnigent's host daemon, which forwards only an
allowlist of environment variables from your shell. What reaches the harness
depends on the server mode:

- **Stance's local server (default):** provider keys such as
  `OPENAI_API_KEY` and `AWS_BEARER_TOKEN_BEDROCK` are forwarded;
  `AWS_PROFILE`, `AWS_REGION`, and AWS access-key variables are not.
- **An existing server (`--server URL`):** no provider key exported in your
  shell is forwarded unless a saved Omnigent provider entry references it or
  you name it in `OMNIGENT_RUNNER_ENV_PASSTHROUGH` (comma-separated; Stance
  keeps your entries). A harness's own stored login is unaffected.

So keep settings where the harness reads them itself, or name them:

- Claude Code through AWS Bedrock: the `env` block of Claude Code's
  `settings.json` (`CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`, `AWS_PROFILE`,
  plus `~/.aws` files), or an Omnigent Bedrock provider entry. With an existing
  server and `AWS_BEARER_TOKEN_BEDROCK` in your shell, also set
  `OMNIGENT_RUNNER_ENV_PASSTHROUGH=AWS_BEARER_TOKEN_BEDROCK`.
- Codex through an OpenAI API key: Codex's own login, or `OPENAI_API_KEY`.
  With an existing server and only the shell variable, also set
  `OMNIGENT_RUNNER_ENV_PASSTHROUGH=OPENAI_API_KEY`.

Stance pins `omnigent==0.20.0` and installs it alongside itself.

## Install

```sh
uv tool install git+https://github.com/mattjmcnaughton/stance
```

## Usage

```sh
# Start Claude Code in a protected session and attach this terminal.
stance claude --budget-usd 5

# Codex, with an explicit model.
stance codex --budget-usd 5 --model gpt-6.1-sol

# Pass arguments to the harness after --.
stance claude --budget-usd 5 -- --permission-mode plan

# Prepare a protected session without starting the harness or sending a
# prompt, then attach later (from the same directory).
stance claude --budget-usd 5 --prepare
stance attach <session-id> --server local   # the printed attach command

# Use an existing Omnigent server instead of Stance's local one.
stance codex --budget-usd 5 --server https://omnigent.example.com

# Stance's local server.
stance server start | status | stop
```

Every launch prints the session id, budget, model, server URL and port,
conversation URL, and an attach command that names its server. No secrets are
printed.

Choose the model with Stance's `--model`, not with harness arguments: Stance
rejects `--model` (and Codex `-m` or `-c model=...`) after `--`, because that
model would skip the price check.

`stance attach` re-verifies that the session still has its enabled Stance
budget and then resumes the same session, so the budget and accumulated spend
carry over. Codex sessions resume on the session's current model, not
Omnigent's configured default. It refuses sessions without an intact Stance budget rather than
creating a new, unprotected one.

### Model check

Before creating a session, Stance resolves the launch model and requires an
exact, priced entry in Omnigent's model catalog (MLflow release catalogs
downloaded from GitHub). Model sources, in order: `--model`, the config file,
then the harness's own settings (`ANTHROPIC_MODEL` or Claude Code
`settings.json`; Codex `config.toml`). Family aliases such as `sonnet` and
unrecognized ids that Omnigent would only price by family estimate are
rejected; pass a full id such as `claude-sonnet-5-5`,
`us.anthropic.claude-sonnet-5-5-v1:0`, or `gpt-6.1-sol`. Catalog prices are not
your exact AWS or OpenAI billing rates.

## Configuration

`$XDG_CONFIG_HOME/stance/config.toml` (default `~/.config/stance/config.toml`).
Every key is optional, and CLI flags override it.

```toml
budget_usd = 5            # default budget; positive and finite

[server]
url = "https://omnigent.example.com"   # default server; "local" or absent = Stance's own

[claude]
model = "us.anthropic.claude-sonnet-5-5-v1:0"

[codex]
model = "gpt-6.1-sol"
```

Without `budget_usd` in config, `--budget-usd` is required. Zero, negative,
NaN, and infinite budgets are rejected.

## Servers

By default Stance runs its own persistent local Omnigent server on loopback,
with its runtime state in `$XDG_STATE_HOME/stance/omnigent` (default
`~/.local/state/stance/omnigent`), separate from any other Omnigent server or
daemon you run. Your Omnigent provider configuration (`~/.omnigent` config or
`OMNIGENT_CONFIG_HOME`) and harness configuration (`~/.claude`, `~/.codex`)
are used as-is. Stance reuses its local server only when it started that
server with the current privacy settings, or the running processes' environment
shows them; otherwise it stops everything in its namespace and starts fresh.
In Omnigent 0.20.0, `omnigent stop` also stops any server on the default port
6767, so it can stop Stance's local server and the sessions it runs; Stance
starts a fresh one on its next launch.

With `--server URL` or `[server] url`, Stance uses that server with your
existing Omnigent login for it (renewing an expired login from its refresh
grant, as Omnigent does; `OMNIGENT_REMOTE_AUTH_TOKEN` still wins), after
checking that its version matches the pinned Omnigent minor version and that
it offers the cost-budget policy. You are responsible for that server's privacy
and configuration; Stance's opt-outs cannot change a server it did not start.

Omnigent reuses a running host daemon for that URL, including the local-mode
daemon of an ordinary `omnigent` server selected by its loopback URL, and the
harness inherits that daemon's environment. Stance refuses to launch through a
daemon that lacks its opt-outs and names the stop command. Stopping it also
stops the sessions and subagents it runs, so finish that work first.

Stance's own HTTP calls to a loopback server ignore `HTTP_PROXY`/`ALL_PROXY`;
calls to remote servers honor them.

## Privacy

Before Omnigent is imported, Stance sets, overriding inherited opt-ins:

| Variable | Value | Effect in Omnigent 0.20.0 |
| --- | --- | --- |
| `OMNIGENT_DISABLE_TELEMETRY` | `1` | usage analytics off |
| `OMNIGENT_ANALYTICS` | `0` | usage analytics off |
| `OMNIGENT_TELEMETRY_ENABLED` | `0` | OpenTelemetry instrumentation off |
| `OMNIGENT_OTEL_CAPTURE_CONTENT` | `0` | trace content capture off |

They propagate to Stance's local server, host daemon, runners, and the harness
process. `OMNIGENT_RUNNER_ENV_PASSTHROUGH` gains the two analytics names
(existing entries are kept). Codex starts Omnigent's MCP bridge with a scrubbed
environment that Stance cannot extend without patching Omnigent; in 0.20.0
that bridge loads no analytics emitter and tracing is off unless explicitly
enabled, and a test guards this.

These opt-outs do not stop:

- traffic to your model provider, or the provider's retention, training, and
  account policies;
- Claude Code or Codex's own telemetry (configure those harnesses yourself);
- local Omnigent transcripts, databases, and logs under Stance's state dir;
- downloads of the public model-pricing catalog from GitHub.

## Budget semantics and known limits

- The budget is Omnigent's stock `cost_budget` policy with `max_cost_usd` and
  no model exemptions, so it applies to every model. Once observed session
  spend reaches the budget, new prompts and tool calls are denied.
- It is an observed-spend cutoff, not an invoice cap. In-flight requests,
  concurrent work, and reporting lag can overshoot, with no guaranteed bound.
- Omnigent asks for approval when usage has tokens but no dollar total yet
  (unpriced usage), and when policy evaluation is temporarily unavailable for a
  tool call. You can approve these yourself. Once a dollar total exists, later
  unpriced usage can go uncounted without prompting.
- If the harness's native hooks are inactive (for example after a Codex
  version or hook-trust problem), Omnigent can warn and continue without
  enforcement. Stance does not monitor hook health.
- Child sessions Omnigent tracks under the root session count against the
  root budget. Processes outside that tree, deliberately removed or disabled
  policies, and every possible subagent combination are not covered.
- Changing the model mid-session is not policed beyond what the stock policy
  does.

## Development

```sh
uv sync --dev
just gate             # format, lint, typecheck, unit tests
just test-integration # real local Omnigent server, no model calls
just test-e2e         # full handover with a fake `claude`; needs tmux
```

See [docs/architecture.md](docs/architecture.md) and
[docs/development.md](docs/development.md).

## License

[MIT](LICENSE)
