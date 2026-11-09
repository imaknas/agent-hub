# A project's own confined agent calls, per-job model tiers, declared secrets kept from confined runs

> **Status**: design, 11-09. Not built. Cold review before building (the owner's rule for this change).

Source: a project's request (11-09). Its research jobs (a Python runner) run `claude -p`
themselves with `--allowedTools` / `--disallowedTools`, and have failed since 10-26 because a command job's PATH has no
`claude`. It asked the scheduler to export `CLAUDE_BIN`; that was refused (a core file names no tool; a project's code
doesn't call the agent with the platform's login: the rules file's "Model calls"). It also asked the real question:
**is there a judgment point a project's script can call, where every tool not listed is refused, and how does the
script find it without a path written into its repo?**

What exists today, and why it is not enough:

- `kind: agent` + `allow:` in a registry job → `agent-job-confined`: dontAsk, only the job's rules plus the platform's
  settings for the project, unnamed tool servers denied. Right boundary, wrong shape: it is a whole scheduled job. A
  pipeline (fetch inputs → build a digest → the model edits three data files → file the result) has to be cut into
  three jobs chained with `after:`, and the prompt has to live in the config repo (`projects/<name>/jobs/*.md`), which
  a project can't write (manifest.update carries only the YAML).
- `brunna complete <point>` (registry `models:`) is callable from a script, but answer-only: no tools.
- An agent job can't say which model strength it needs: `agent-job-confined` is `model_group: default`.
- The platform's settings for a confined run keep the platform's credentials out of reach, not the project's own:
  `allow:` can only allow, so a job can't keep its agent out of `secrets/` except by never allowing a bare `Read`.

Found while checking: **a command job's PATH can't find `brunna` either.** The scheduler runs under launchd with no
`EnvironmentVariables`, so a job inherits `/usr/bin:/bin:/usr/sbin:/sbin`; `brunna install-cli` writes the wrapper to
`~/.local/bin`, and services' `$PLATFORM_PATH` (`hubconf.LAUNCHD_PATH`: nvm's bin, Homebrew, /usr/local/bin, /usr/bin,
/bin) doesn't hold that folder either. The rules file says `brunna complete` "works from sessions, services and jobs";
from a job or a service it doesn't, by name. No project calls it from either today, so nothing has failed yet.

## Decisions proposed

### A. `brunna agent <name>`: a confined agent call a project declares

Registry (project v1 and v2), next to `models:`:

```yaml
agents:
  research:                       # lowercase letters, digits, -
    tier: strong                  # fast | standard | strong, as models:
    allow:                        # the whole boundary, the same rule strings as a job's allow:
      - Read(./data/**)
      - Read(./tools/**)
      - WebSearch
      - WebFetch
      - Edit(./data/research-log.md)
      - Bash(.venv/bin/python tools/research_context.py*)
    timeout: 45m                  # optional; default 30m, at most {settings} cap_runner.agent_max_timeout_s (1h)
```

Call, from anywhere in the project's workspace or runtime folder (a session, a service, a command job):

```
brunna agent research [--schema F] < prompt      # stdout: llm.py's JSON result; exit 1 refused or failed
```

The path is `brunna complete`'s, with a new request kind:

1. `hub-run --agent <name>` writes `{"v": 1, "id", "kind": "agent", "name", "prompt", "cwd", "schema"?}` into the
   project's own `.hub-run/requests/` (the folder a request is found in is the project it belongs to: nothing in the
   request names a project).
2. cap-runner (not sandboxed) checks: `name` is in the registry's `agents:`, `cwd` resolves inside the folder it was
   found in, the prompt is non-empty, the schema is an object. It writes the prompt into platform state (never read
   back from the project's folder) and runs `llm.py agent <project> <name> --request F --cwd <dir>`.
3. `llm.py agent` reads the declaration **from the registry, not from the request** (allow, tier, timeout), and runs
   the new judgment point `project-agent` with them: `shape: agent`, `permission_mode: dontAsk`, `prompts: none`,
   `settings: platform`, `tool_servers: allowed_only`, `allow_from_env` + `project_env` set by llm.py itself from the
   registry entry. So the run is exactly an `agent-job-confined` run (the project's saved permissions, the user's
   settings and the project's hooks are not loaded; its sandbox and deny lists come from the policy; tools not listed
   are refused without a prompt), started on demand instead of by the clock.
4. stdout and the exit code come back through `.hub-run/jobs/<id>/` as for `complete`; the client prints them as they
   come. `hub-run --wait <id>` reattaches.

Same runner, so the same guarantees: O_NOFOLLOW directory fds, ≤ 512 KB requests, 10 minutes to be picked up, one job
per project at a time, a restart marks running jobs lost. Cost: one row per call in `.state/llm-usage.jsonl` with the
project, as `complete` does (from the runtime's reported cost), so it shows per project on the status page.

What it is not: no `connectors: true` (claude.ai connectors need the subscription login and load the machine's tool
servers; a scheduled job keeps that path), no long-running streaming, no resuming a session.

Neutrality: the registry and `brunna agent` say nothing about any tool. `allow:` strings are the same as a job's
`allow:` today (Claude Code's rule syntax, already in the registry; making them neutral is the policy layer's open
work, `runtime-abstraction.md`, not this change). A runtime without the `agent` + `confine` ports refuses the call and
says so (`runtime_ports.Unsupported`), never runs it unconfined.

### B. `tier:` on agent jobs

A registry agent job may say `tier: fast | standard | strong`. The scheduler line carries it to `run-agent-job.sh`
as `AGENT_JOB_TIER`, and `llm.py run` uses it as the run's `model_group` for points that declare
`tier_from_env: AGENT_JOB_TIER` (`agent-job`, `agent-job-confined`, `agent-job-event`). Without it, as today
(`default`: the tool's own). `llm.model_for` stays the only place a group becomes a model name, and
`hub.json llm.points.<point>.model` still overrides (per point, not per job).

### C. The project's declared secrets are kept from every confined run

`runtime_claude.platform_settings(project, …)` (the only settings file a `settings: platform` run loads) adds to its
deny list every path in the project's registry `secrets:` (resolved as check-secrets resolves them: relative to the
runtime folder when there is one, else the workspace; `~/` and absolute as given). A `.env` with `env_key` is denied
whole. This covers `agent-job-confined`, `agent-job-event`, the mailbox handlers and the new `project-agent`.

Not in the general policy (`policy.policy`): that also builds the project's own sessions' settings and its services'
sandbox profile, and a service must read its own credentials. Interactive sessions are out of scope here (the owner
decides separately).

A secret declared with a glob or a folder is denied as that folder (`Read(<dir>/**)`), a file as itself.

### D. The platform CLI's folder on a command job's and a service's PATH

The scheduler gives command jobs `PATH = <the CLI folder>:/usr/bin:/bin:/usr/sbin:/sbin` (the CLI folder =
`policy.cli_dirs()`, where `install-cli` writes `brunna`), and `hubconf.LAUNCHD_PATH` (services' `$PLATFORM_PATH`)
gains the same folder. Not nvm's bin for command jobs: a job that wants node declares it like a service does; and the
agent's binary stays where a project's code doesn't find it by name, which keeps "model calls go through the
platform" the easy path. Without D, A works only from sessions.

If `install-cli` was never run there is no folder to add; `brunna doctor` already says the CLI is missing.

## What the asking project does with it

Keeps its runner as it is, replacing `run_claude()` with `brunna agent <name> < prompt` and declaring one
`agents:` entry per tool set (its four jobs use three). The prompt stays in its repo. No `after:` chain, no prompt in
the config repo.

## Security

- `agents:` and job `allow:` are not owner-only fields: a project grants its own agent its own rules by manifest.update,
  as it already can for a job. The ceiling is the same: the platform's settings for the project (sandbox when
  enforced, other projects, the platform's state and credentials, and with C its own secrets) are loaded with them and
  a runtime's deny wins over any allow.
- A sandboxed project's session can't start an agent itself (no credentials in its sandbox); through cap-runner it
  gets one confined to what its registry entry grants. A sandboxed project's code thus gains a model with tools it
  couldn't run before. That is the point of the feature, bounded by `allow:` and the policy, and visible: every call is
  in cap-runner's log and in llm-usage per project.
- Cost: a script can loop. cap-runner runs one job per project at a time and at most `cap_runner.concurrency` in all;
  a long agent call holds its project's slot (a GPU job of the same project waits). No per-project budget in this
  change; usage is visible per project, and the owner's `metered_models` still decides whether a metered provider is
  used (for an agent shape the runtime's own login is used, as for every agent point today).
- The prompt is the project's text. It runs with the project's grants, under dontAsk: nothing in it can widen them.

## Tests

- registry: `agents:` validates (name, tier, allow list of strings, timeout ≤ the setting); a job's `tier:`.
- cap-runner: an undeclared name, a cwd outside the folder, an empty prompt are rejected with the reason; a declared
  call runs `llm.py agent` with the prompt from platform state; a request naming another project changes nothing.
- llm.py agent: allow and tier come from the registry, never the request; the dry-run command is dontAsk with
  `--restricted` and the platform settings file; a runtime without confine → refused, said so.
- C: a confined run's settings file denies each declared secret (file, `.env`, `~/` path, folder); the project's
  session settings and service profile are unchanged.
- D: a command job's environment has the CLI folder and not nvm's bin; a service's `$PLATFORM_PATH` has the CLI folder.
- tool-neutrality tests stay green: no tool name in the scheduler, registry or the CLI.

## Steps

1. B and C (small; independent of A).
2. D.
3. A: registry `agents:`, `hub-run --agent`, cap-runner `kind: agent`, `llm.py agent`, the `project-agent` point,
   `brunna agent` in the CLI and in the session subcommands the policy lets through, the rules file's "Model calls"
   paragraph, docs.
4. Tell the project that asked.
