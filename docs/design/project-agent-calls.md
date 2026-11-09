# A project's own confined agent calls, per-job model tiers, declared secrets kept from confined runs

> **Status**: design, 11-09, second draft after the first cold review (a security reading and a feasibility reading,
> both blind; what each found and what changed is under "Review, round 1"). Not built.

Source: a project's request (11-09). Its research jobs (a Python runner) ran the coding agent themselves with
allow/deny flags, and failed from 10-26 because the agent's binary is not on a command job's PATH. It asked the
scheduler to export the binary's path; that was refused (a core file names no tool; a project's code doesn't call the
agent with the platform's login: the rules file's "Model calls"). It also asked the real question: **is there a
judgment point a project's script can call, where every tool not listed is refused, and how does the script find it
without a path written into its repo?** (It has since split those jobs into command → `kind: agent` + `allow:` →
command, which works today; this design is for the general case.)

What exists, and why it is not enough:

- `kind: agent` + `allow:` in a registry job → `agent-job-confined`: dontAsk, only the job's rules plus the platform's
  settings for the project, unnamed tool servers denied. Right boundary, wrong shape: a whole scheduled job. A pipeline
  has to be cut into jobs chained with `after:`, and the prompt has to live in the config repo
  (`projects/<name>/jobs/*.md`), which a project can't write (manifest.update carries only the YAML).
- `brunna complete <point>` (registry `models:`) is callable from a script, but answer-only.
- An agent job can't say which model strength it needs (`agent-job-confined` is `model_group: default`).
- A confined run's settings keep the platform's credentials out of reach, not the project's own: `allow:` can only
  allow.

How a script finds the platform: by `brunna` on PATH, the only interface; never `llm.py` or a path in its repo. A
command job gets it already: the scheduler runs each job through the login shell (`scheduler.run_bounded`,
`login_shell() -lc`), which reads the user's profile, where `install-cli`'s folder is. A service does not: its PATH is
`$PLATFORM_PATH` (`hubconf.LAUNCHD_PATH`), which has no such folder (decision D).

## Decisions proposed

### A. `brunna agent <name>`: a confined agent call a project declares

Registry (project v1 and v2), next to `models:`:

```yaml
agents:
  research:                       # lowercase letters, digits, -
    tier: strong                  # fast | standard | strong, as models:
    allow:                        # the whole boundary; the same rule strings as a job's allow:, with the limits below
      - Read(./data/**)
      - Read(./tools/**)
      - Edit(./data/research-log.md)
      - Bash(.venv/bin/python tools/research_context.py*)
      - WebFetch(domain:example.org)
    timeout: 45m                  # optional; default 30m, at most {settings} cap_runner.agent_max_timeout_s (1h)
```

Validation (registry): every rule is `Tool` or `Tool(./…)` / `Tool(<pattern>)` with no `~/`, `//` or absolute path and
no `..`; no `mcp__` rule (the run loads no tool servers); for a project with `enforce.sandbox: true`, `WebFetch` only
as `WebFetch(domain:d)` with `d` in its `network:` or the platform's defaults, and no `WebSearch` (the owner-only
`network:` list is what bounds a sandboxed project's reach; a bare web tool would get around it). The same limits are
checked on a job's `allow:` from now on (decision A6).

Call, from the project's workspace or runtime folder (a session, a command job; a service once D is in):

```
brunna agent research [--schema F] < prompt
```

stdout = llm.py's JSON result; exit 0 done, 1 the run failed (reason in `llm_error`), 126 cap-runner refused the
request (reason on stderr), 124 timed out, 125 the runner isn't there or lost it; the same codes as `cap-run` and
`complete`.

**A1. The path.** `brunna complete`'s, with a new request kind:

1. `hub-run --agent <name>` writes `{"v": 1, "id", "kind": "agent", "name", "prompt", "schema"?}` into the project's
   own `.hub-run/requests/` (the folder a request is found in is the project it belongs to). There is no `cwd` in the
   request: **the run's working directory is the registry folder the request was found in** (workspace or runtime),
   so nothing the project can swap between the check and the start moves it.
2. cap-runner (not sandboxed) serves projects that declare `agents:` too (`eligible()`), checks that `name` is in
   `agents:`, the prompt is non-empty, the schema is an object, writes `{prompt, schema}` into platform state (never
   read back from the project's folder) and runs `llm.py agent <project> <name> --request F --cwd <that folder>`.
3. `llm.py agent` reads the declaration **from the registry, never the request** (allow, tier, timeout), and runs the
   new judgment point `project-agent`: `shape: agent`, `permission_mode: dontAsk`, `prompts: none`,
   `settings: platform`, `allow_from_env` + `project_env` (set by llm.py from the registry entry),
   `tier_from_env` (B), and sealed from the environment's tool servers (`extra_args: ["--strict-mcp-config"]`, as the
   librarian's points; no per-call `mcp list`, no committed `.mcp.json` loaded). `Task.timeout_s` = the declared
   timeout; cap-runner's own deadline is that plus a margin, so the run reports its own timeout. The platform
   settings file is written per run (today one per project, shared with its scheduled confined runs).
4. stdout and the exit code come back through `.hub-run/jobs/<id>/` as for `complete`.

**A2. Links can't move the boundary.** A `settings: platform` run turns each `./` rule whose fixed part resolves
outside the working directory into an extra working directory plus an absolute rule (`runtime_claude._linked_rules`,
written for a worktree's `.agent-memory` link). Today that has no containment check: a project that plants
`data -> ~` makes `Edit(./data/.zshenv)` an edit of the user's shell startup file, from a run whose file tools are
outside the OS sandbox. For every `settings: platform` run (this point, `agent-job-confined`, `agent-job-event`, the
mailbox handlers): a `./` rule that resolves outside the project's workspace, its runtime folder and its notes folder
refuses the run (job.failed, the reason names the rule and where it leads). This also closes the hole for today's
confined jobs, so it lands first, on its own.

**A3. The platform's login stays out of the agent's tools.** The run's environment carries the runtime's login
(`auth_env`: the headless token). Whether the runtime keeps it out of the processes its tools start (a `Bash` rule's
script) can't be read from the repo. The runtime adapter declares it (`login_reaches_tools: False | True | None`),
`update-claude.py` probes it for each new version (a confined run whose one allowed command reports only whether the
variable is set, never its value) and alerts on a change, and:
- `None` (not probed) or `True`: a `project-agent` run (and an `agent-job-confined` / `agent-job-event` run) of a
  sandboxed project whose rules include `Bash(...)` is refused, saying why.
- Unsandboxed projects: their code runs as the user and can already read the user's files; nothing new is reachable.

And the token file (`~/.claude/headless-oauth-token`) joins the runtime's `credential_paths()` files, so every
sandbox profile and confined run's deny list keeps it, like `.credentials.json`.

**A4. The prompt never becomes a flag.** The agent interface passes every run's prompt on stdin (today it is an
argument after `-p`, so a prompt that starts with `--` would be read as one). For all agent points, with the golden
(`tests/fixtures/llm-golden.json`) updated in the same commit.

**A5. Edits in place, and what a runtime without confinement gets.** A changes the real workspace (`Edit(./data/…)`)
under the runtime's `confine` port, as `agent-job-confined` already does: the data an agent edits are often untracked
(fetched inputs, logs), and the throwaway worktree `propose_apply` builds holds only tracked files. The neutrality rule
says agent work the platform starts that changes files runs in a throwaway worktree with only allowed paths applied;
for a runtime **with** `confine` this design takes the in-place run as `agent-job-confined` does, with A2–A4 as the
platform's own checks around it. A runtime **without** `confine` runs it through `propose_apply.run` (tracked files
only, the allowed `Edit` paths applied back) and says so in the result; a call whose declared rules write nothing runs
there unchanged. **Owner's decision** (the one place this design leans on a runtime's own enforcement for writes).

**A6. Job `allow:` gets A's validation.** The same rule limits (A) apply to every agent job's `allow:`. Before the
check goes in, `registry.py check` lists each current job it would refuse; none is refused silently.

**A7. Cost and fairness.**
- cap-runner runs agent calls in their own slots: `cap_runner.agent_concurrency` (default 1), apart from GPU jobs and
  `complete` (`concurrency`, default 2). One agent call per project at a time.
- A per-project daily limit, `cap_runner.agent_calls_per_day` (default 20), a registry `agents_per_day` lowering it
  (never raising it); past it a request is refused with the count.
- A call made from inside an agent run (`HUB_LLM_POINT` set) is refused by `hub-run` at once, not queued behind
  itself.
- One row per call in `.state/llm-usage.jsonl`: `llm.py agent` writes it itself (provider = the runtime, billing as
  the runtime's login is billed, the project, the point `<project>/<name>`, the reported cost), since only
  `completion.run` writes rows today. So it shows per project on the status page.
- `metered_models`: when the runtime's login for this run is billed per call (`completion.metered` of the runtime
  target), the call is refused unless the owner set `metered_models: true`; when that can't be told, refused.

What it is not: no claude.ai connectors (they need the subscription login and the machine's tool servers; a scheduled
job keeps that path), no long-running streaming, no resuming a session.

Neutrality: the registry and `brunna agent` say nothing about any tool; rule strings are the same as a job's `allow:`
today (Claude Code's syntax, already in the registry; making them neutral is the policy layer's open work). A runtime
without the `agent` port refuses the call and says so (`runtime_ports.Unsupported`); doctor's feature table gets the
row.

### B. `tier:` on agent jobs

A registry agent job may say `tier: fast | standard | strong`. `registry.scheduler_jobs` puts `AGENT_JOB_TIER=<tier>`
on the command (as it does `AGENT_JOB_ALLOW`), and `llm.py run` uses it as the run's `model_group` for points that
declare `tier_from_env: AGENT_JOB_TIER` (`agent-job`, `agent-job-confined`, `agent-job-event`; `project-agent` gets its
own from the registry). Without it, as today. `llm.model_for` stays the only place a group becomes a model, and an
owner's `llm.points.<point>.model` pin still wins (per point); `schedulectl models` shows a job's tier beside its
point.

### C. The project's declared secrets are kept from every confined run

`policy.secret_paths(project, doc)` (neutral) resolves each registry `secrets:` path: relative ones under **both** the
workspace and the runtime folder (either may hold it), `~/` and absolute as given; no globs (the registry has none).
`platform_settings` (the only settings file a `settings: platform` run loads) adds them to the run's `deny_read`, so
they land in the sandbox's `denyRead` when the project is sandboxed and in the deny rules always: a folder as
`Read(<dir>/**)`, a file as `Read(<file>)`, and `Edit` denied on both. A `.env` with `env_key` is denied whole.

Not in `policy.policy()` itself: that also builds the project's own sessions' settings and its services' sandbox, and a
service reads its own credentials.

What it does not do: in an unsandboxed project a `Bash(...)` rule runs a script as the user, and that script can open
them (the rule says what the script is; what it reads is its own). In a sandboxed project an allowed script that reads
a declared secret now fails; the run's result says which path was denied.

### D. Services find `brunna` by name

A platform-owned folder holding only a `brunna` wrapper (in the shared platform state the sandbox already reads,
`hubconf.shared()`; written by `brunna install-cli` and `render-services`, never a symlink, mode 755) is **appended**
to render-services' default service PATH, after the system folders. Not `~/.local/bin` (it holds uv's `python3` and
other tools, which would change every service's interpreter), and not `hubconf.LAUNCHD_PATH` itself (cap-runner,
lease-runner, update-claude and the test runner use it too). Command jobs need nothing (above).

## Security, as a whole

- `agents:` and a job's `allow:` are not owner-only: a project grants its own agent its own rules by manifest.update.
  The ceiling is what the platform loads with them and what A's validation refuses: the sandbox when enforced, the
  deny lists (other projects, the platform's state and credentials, the login token, and with C its own secrets), no
  links out (A2), no web reach past `network:` (A), no tool servers, no login in a sandboxed project's scripts (A3).
- A sandboxed project's code gains a model with tools it couldn't run before. That is the point, bounded as above, and
  visible: cap-runner's log, llm-usage per project, the daily limit.
- The prompt is the project's text, under dontAsk: it can't widen the rules (A4 keeps it from being a flag).
- A confined run can't change its own grants: `manifest.update` is refused for confined senders (hubbus
  `CONFINED_TYPES`).

## Tests

- registry: `agents:` (name, tier, rules: the A limits, sandboxed web rules, `mcp__`, paths), `tier:` on jobs, the A6
  report; v1 and v2 schemas.
- cap-runner: undeclared name, empty prompt, over the daily limit → 126 with the reason; a declared call runs
  `llm.py agent` with the prompt from platform state and the registry folder as cwd; agent slots apart from others;
  `eligible()` serves `agents:`-only projects.
- hub-run: a nested call (`HUB_LLM_POINT`) refused; exit codes.
- llm.py agent: allow/tier/timeout from the registry; dry-run: dontAsk, restricted, sealed, prompt on stdin; usage row;
  metered refusal; no `confine` → propose_apply; no `agent` port → refused, said so (`test_runtime_none_flow`).
- A2: a `./` rule through a link out of the project → refused, for `agent-job-confined` too; the `.agent-memory` link
  still works.
- A3: token file in `credential_paths()`; `login_reaches_tools` None/True + sandboxed + Bash → refused.
- C: each declared secret (file, `.env`, `~/`, folder, workspace and runtime) in a confined run's denies, Read and Edit;
  sessions' settings and service profiles unchanged.
- D: the service PATH ends with the wrapper's folder; nothing else on it changes.
- Contracts and lints: `llm-points.v1.schema.json` gains `tier_from_env`; `runtime_ports.features()` and
  `test_runtime_ports_features`; i18n goldens for cap-runner and hub-run; `test_runtime_boundary`, `test_tool_agnostic`.

## Steps

1. A2 + the token file in `credential_paths()` (fixes today's confined jobs; its own commit).
2. A4 (prompt on stdin, all agent points).
3. B and C.
4. A3 (the probe, the declaration, the refusal).
5. A, A5–A7: registry `agents:`, `hub-run --agent`, cap-runner `kind: agent`, `llm.py agent`, the point, `brunna
   agent` in `SESSION_COMMANDS` and `policy.SESSION_SUBCOMMANDS`, the rules file's "Model calls", docs/mcp.md if the
   MCP server should offer it (not in this change).
6. D.
7. Tell the project that asked.

## Review, round 1 (11-09, blind; security and feasibility)

Taken: D's first form was wrong (command jobs already find `brunna` through the login shell; the PATH change would
have done nothing for them and, put first, changed every service's `python3`); usage rows don't exist for agent runs
(now written by `llm.py agent`); the run was not sealed from tool servers; two timers; one shared queue and a call that
waits on itself; the neutrality rule on file-changing agent work (A5); `WebFetch` past `network:`; C's rule forms,
neutral home and limits; links out of the project through `_linked_rules` (A2, an existing hole); the login reaching a
tool's scripts (A3, unresolved from the repo, so probed); the headless token file missing from the deny lists; the
prompt as an argument (A4); metered logins; the per-run settings file name; the missing steps (schemas, features
table, `eligible()`, session subcommands, goldens).
