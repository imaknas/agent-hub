# A project's own confined agent calls, per-job model tiers, declared secrets kept from confined runs

> **Status**: design, third draft (11-09). Not built.
> - Rounds 1 and 2 were blind cold reviews, each with a security reading and a feasibility reading; what they changed is
>   at the end.
> - The holes round 2 found in today's confined runs were fixed on their own first: the three `fix(confine)` commits
>   of 11-09, merged.
> - The owner decided on 11-09: a project's agent edits the workspace in place, under the runtime's confinement, as
>   `agent-job-confined` does. There is no throwaway-copy fallback.

Source: a project's request (11-09). Its research jobs (a Python runner) ran the coding agent themselves, with allow
and deny flags. They failed from 10-26 because the agent's binary is not found on a command job's PATH. The project
asked the scheduler to export the binary's path, and that was refused: a core file names no tool, and a project's code
doesn't call the agent with the platform's login (the rules file's "Model calls"). It also asked the real question: **is
there a judgment point a project's script can call, where every tool not listed is refused, and how does the script find
it without a path written into its repo?** The project has since split those jobs into command → `kind: agent` +
`allow:` → command. That works today; this design is for the general case.

What exists, and why it is not enough:

- **`kind: agent` + `allow:` in a registry job → `agent-job-confined`.** The run is dontAsk, gets only the job's rules
  plus the platform's settings for the project, and unnamed tool servers are denied. The boundary is right, but the
  shape is a whole scheduled job:
  - a pipeline has to be cut into jobs chained with `after:`;
  - the prompt lives in the owner's config repo (`projects/<name>/jobs/*.md`).
- **`brunna complete <point>`** (registry `models:`) can be called from a script, but it only answers.
- **No model strength for agent jobs.** An agent job can't say which strength it needs (`model_group: default`).
- **The project's own secrets are reachable.** A confined run's settings keep the platform's credentials out of reach,
  but not the project's own.

## A. `brunna agent <name>`: a confined agent call a project declares

### A1. Declaration and call

Registry (project v1 and v2), next to `models:`:

```yaml
agents:
  research:                       # lowercase letters, digits, -
    tier: strong                  # fast | standard | strong, as models:
    allow:                        # the whole boundary (A2); the same rule strings as a job's allow:
      - Read(./data/**)
      - Read(./tools/**)
      - Edit(./data/research-log.md)
      - WebFetch(domain:example.org)
    timeout: 45m                  # optional; default 30m, at most {settings} cap_runner.agent_max_timeout_s (1h)
```

From the project's workspace, its runtime folder or a folder below them (a session, a command job, a service):

```
brunna agent research [--schema F] < prompt
```

- **stdout:** llm.py's JSON result: `result`, `structured_output` when a schema is given, `llm_error` on failure.
- **Exit codes:**
  - 0: done.
  - 1: the run failed or ran over its timeout; the reason is in `llm_error`. The run's own timeout says so; cap-runner's
    deadline is the declared timeout plus a minute and only catches a run that hangs.
  - 126: refused (the reason on stderr).
  - 125: the runner isn't there, or lost the run.
  - 2: usage.

  These are the same codes as `cap-run` and `complete`.
- **Where it runs:** in the registry folder the request is found in, the workspace or the runtime folder; never a
  subfolder the request names. A call from a session in a linked worktree under the workspace (a coding agent's
  `.claude/worktrees/…`) therefore runs in the main checkout (`hub-run` takes the longest registered prefix). The
  rules file says so.

The path is `brunna complete`'s, with a new request kind:

1. `hub-run --agent <name>` writes `{"v": 1, "id", "kind": "agent", "name", "prompt", "schema"?}` into the project's own
   `.hub-run/requests/`. The folder a request is found in is the project it belongs to.
2. cap-runner (not sandboxed) checks the request:
   - it serves projects that declare `agents:` too (`eligible()`, both their folders);
   - `name` must be in `agents:`, the prompt non-empty, the schema an object;
   - the A5 limits.

   It writes `{prompt, schema}` into platform state (never read back from the project's folder) and runs
   `llm.py agent <project> <name> --request F --cwd <that registry folder>`.
3. `llm.py agent` reads allow, tier and timeout from the registry, never from the request. It sets `model_group` from
   the tier before `model_for`, and runs the judgment point `project-agent`:
   - `shape: agent`, `permission_mode: dontAsk`, `prompts: none`;
   - `settings: platform`, with the project in `project_env`;
   - `allow_from_env`: llm.py itself puts the registry's rules there;
   - `extra_args: ["--strict-mcp-config"]`, sealed from the environment's tool servers as the librarian's points are;
   - `mail: none` (A2);
   - `Task.timeout_s` = the declared timeout.
4. stdout and the exit code come back through `.hub-run/jobs/<id>/`, as for `complete`.

The run gets everything the hardening gave confined runs:
- no rule that isn't a rule;
- no link and no starting folder outside the project's folders;
- per-step link checks;
- the headless token and the shell's files denied.

### A2. What the agent can do

`allow:` is the whole boundary. Unlike the other confined points it gets **no mail commands**: its answer goes back on
stdout, and it has no letters to handle. A new point flag, `mail: none`, keeps `platform_settings` and `_flags` from
adding the mail rules, and leaves Bash out of `--tools` unless a `Bash(...)` rule is declared.

The adapter checks the rules, through a new optional port, `rule_problems(rules, *, sandboxed, network)`. It returns
neutral sentences, so the registry calls the port and never parses tool syntax itself (`bin/registry.py` is under the
boundary ratchet). With no runtime, or a runtime without the port, `agents:` is refused, saying why. What it refuses:

- **Paths:** any `~/`, `//`, absolute or `..` path in a rule. Paths are relative to the run's folder.
- **Tool servers:** any `mcp__` rule. The run loads no tool servers, so connectors stay with owner-approved scheduled
  jobs.
- **For a project with `enforce.sandbox: true`:**
  - **any `Bash` rule.** Claude Code's sandbox can't keep a shell from the user's Keychain (tested 10-25,
    `runtime_claude.py` SECURITY_DENY notes); the platform's own Seatbelt profile can, but it can't be nested inside.
    A sandboxed project runs its scripts as command jobs or `cap-run`, before and after the agent call.
  - **`WebFetch` only as `WebFetch(domain:d)`,** with `d` in its `network:` or the platform's defaults; **no
    `WebSearch`.** The owner-only `network:` list is what bounds a sandboxed project's reach.

  Lifting the shell refusal is future work, needing a probe that the runtime's shell can reach neither the Keychain
  nor the parent's environment.

### A3. Edits in place (owner's decision, 11-09)

The agent changes the real workspace (`Edit(./data/…)`) under the runtime's `confine` port, as `agent-job-confined`
does. The data an agent edits are often untracked (fetched inputs, logs), and `propose_apply`'s throwaway worktree holds
only tracked files. The tool-neutrality rule's "throwaway worktree for agent work that changes files" is satisfied here
by confinement plus the platform's own checks around it: the rule shape, links, folders, deny lists and A2.

A runtime without the `agent` and `confine` ports refuses the call (`runtime_ports.Unsupported`), as `llm.py run`
already does for `settings: platform` points. Doctor's feature table gets the row. A `runtime: command` template has no
`confine`, so it refuses too.

### A4. The prompt is never a flag

A project's prompt goes to the runtime on stdin. Today it is the argument after `-p`, so a prompt starting with `--`
would be read as an option.

- **Scope:** the change covers `_flags` for every task run. `exec_stream` (leases) no longer needs its
  `cmd[1:3] = ["-p"]` splice and is changed with it.
- **Golden:** `tests/fixtures/llm-golden.json` is updated in the same commit.
- **Order:** this lands before A.

### A5. Cost and fairness

- **Slots:**
  - cap-runner keeps two pools: agent calls (`cap_runner.agent_concurrency`, default 1) and everything else
    (`concurrency`, default 2);
  - one job per project **per pool**, so an agent call doesn't hold up the same project's `complete` or GPU job;
  - within a pool, the project served least recently goes first.
- **One at a time, refused not queued:**
  - cap-runner refuses (126) an agent request from a project that already has one running or queued, so a script's
    agent call can't wait on itself;
  - a queued agent request older than its declared timeout is refused, so a bounded caller isn't left waiting.
- **Daily limits (brunna.json), counted in platform state when a request is accepted:**
  - `cap_runner.agent_calls_per_day` per project (default 20); a registry `agents_per_day` can lower it, never raise
    it;
  - `cap_runner.agent_minutes_per_day` across all projects (default 240).

  Past either limit a request is refused with the count.
- **Usage row:** `llm.py agent` writes one row per call to `.state/llm-usage.jsonl`, since only `completion.run` writes
  rows today. The row has:
  - provider = the runtime;
  - billing from the runtime's declaration (below);
  - the project, and the point `<project>/<name>`;
  - the reported cost; tokens are absent, since `TaskResult` has none.

  It shows per project on the status page, and it counts toward `llm.budget.daily_usd`, which `llm.py agent` checks
  before starting, as `completion.run` does.
- **Billing:** the runtime adapter declares how a login is billed, `billing(auth) -> subscription | metered | unknown`.
  A metered login needs the owner's `metered_models: true`; `unknown` is refused.

## B. `tier:` on agent jobs

A registry agent job may say `tier: fast | standard | strong`.
- **How it reaches the run:** `registry.scheduler_jobs` puts `AGENT_JOB_TIER=<tier>` on the command, as it does
  `AGENT_JOB_ALLOW`. `llm.py run` uses it as the run's `model_group` for points that declare
  `tier_from_env: AGENT_JOB_TIER` (`agent-job`, `agent-job-confined`, `agent-job-event`). Without it, the run is as
  today.
- **Models:** `llm.model_for` stays the only place a group becomes a model. An owner's `llm.points.<point>.model` pin
  still wins, per point.
- **Visibility:** `schedulectl models` shows a job's tier beside its point.
- **Contract:** `contracts/llm-points.v1.schema.json` gains `tier_from_env` and `mail`. Muninn vendors that schema and
  is told.

## C. The project's declared secrets are kept from every confined run

`policy.secret_paths(project, doc)` (neutral) resolves each registry `secrets:` path:
- relative ones under **both** the workspace and the runtime folder;
- `~/` and absolute paths as given.

`platform_settings` adds them to the run's `deny_read`, so they land in the sandbox's `denyRead` when the project is
sandboxed, and in the deny rules always:
- a folder as `Read(<dir>/**)`, a file as `Read(<file>)`;
- `Edit` denied on both;
- a `.env` with `env_key` denied whole.

This covers `agent-job-confined`, `agent-job-event`, the mailbox handlers and `project-agent`.

It is not in `policy.policy()`: that also builds the project's own sessions' settings and its services' sandbox, and a
service reads its own credentials.

**Before it lands:**
- `registry.py check --secrets-report` lists every sandboxed project that declares secrets and has confined jobs or
  mailbox handling. In a sandboxed project, an allowed script that read a declared secret now fails.
- In an unsandboxed project, a `Bash(...)` rule's script still runs as the user and can open them. That is said
  plainly in the rules file and in this design.

## D. Services and jobs find `brunna` by name

A platform-owned folder holding only a `brunna` wrapper, `<shared>/cli/` under `hubconf.shared()`:
- **Who writes it:** `brunna install-cli` and `render-services`. Never a symlink; mode 755; covered by the policy's
  `deny_write` of the hub.
- **Services:** render-services appends the folder to every service's PATH, whether the service sets `env.PATH` or not
  and however its command starts. It goes last, so nothing a service finds today changes.
- **Command jobs:** they run through the login shell (`scheduler.run_bounded`, `login_shell() -lc`). They find
  `brunna` today only if the user's profile puts the CLI folder on PATH. The scheduler prefixes each command with
  `PATH="$PATH:<folder>";`, after the profile has run.
- **Not changed:** `hubconf.LAUNCHD_PATH`, which cap-runner, lease-runner, update-claude and the test runner use too.

## Security, as a whole

- **The ceiling.** `agents:` is not owner-only: a project grants its own agent its own rules by manifest.update, as it
  can for a job today. The ceiling is what the platform loads with them and what A2 refuses:
  - the sandbox when enforced;
  - the deny lists: other projects, the platform's state and credentials, the login token, the shell's files, and with
    C its own secrets;
  - no links or starting folders outside its folders;
  - no tool servers;
  - for a sandboxed project, no shell and no web reach past `network:`;
  - no mail.
- **What a sandboxed project gains.** Its code can now start a model with tools it couldn't run before. That is the
  point of the feature, and it is visible: cap-runner's log, llm-usage per project, and the per-project and global
  daily limits.
- **The prompt** is the project's text, under dontAsk. It can't widen the rules, and A4 keeps it from becoming a flag.
- **A confined run can't change its own grants:** hubbus `CONFINED_TYPES` refuses `manifest.update`.

## Tests

- **registry:**
  - `agents:` (name, tier, timeout, the port's refusals, no port → refused) and `tier:` on jobs;
  - v1 and v2 schemas;
  - `--secrets-report`.
- **adapter:**
  - `rule_problems` for each refusal, sandboxed and not;
  - `mail: none` gives no mail rules and no Bash without a `Bash(...)` rule;
  - `billing()`.
- **cap-runner:**
  - undeclared name, empty prompt, over either daily limit, one already running → 126 with the reason;
  - a declared call runs `llm.py agent` with the prompt from platform state and the registry folder as cwd;
  - pools and least-recently-served order;
  - `eligible()` serves `agents:`-only projects;
  - a stale queued agent request is refused.
- **llm.py agent:**
  - allow, tier and timeout come from the registry;
  - dry-run: dontAsk, restricted, sealed, prompt on stdin, no mail;
  - usage row; budget and billing refusals;
  - no `confine` → refused, said so (`test_runtime_none_flow`).
- **A4:**
  - golden updated;
  - leases' `exec_stream` command unchanged in meaning (a test that the next flag after `-p` survives).
- **C:** each declared secret (file, `.env`, `~/`, folder, in the workspace and the runtime folder) is in a confined
  run's denies, Read and Edit; sessions' settings and service profiles are unchanged.
- **D:**
  - every service's PATH ends with the wrapper's folder, including `env.PATH` services and path-started commands;
  - a command job's command has the prefix.
- **Contracts, lints and catalogs:**
  - `runtime_ports.features()` row and `test_runtime_ports_features`;
  - i18n catalog entries (`test_i18n_catalog`) and goldens for cap-runner and hub-run;
  - `test_runtime_boundary` and `test_tool_agnostic`.

## Steps

1. A4: prompt on stdin, `exec_stream`, golden.
2. B and C (with `--secrets-report` run against the live registry first).
3. D.
4. A, with A2's port and A5: `rule_problems` and `billing` in the adapter; registry `agents:`; `hub-run --agent`;
   cap-runner's `kind: agent`, pools and limits; `llm.py agent`; the `project-agent` point and its schema fields; and
   `brunna agent` in:
   - `SESSION_COMMANDS` and `policy.SESSION_SUBCOMMANDS`;
   - the rules file's "Model calls";
   - docs.
5. Tell the project that asked, and Muninn (the llm-points schema).

## Review history

**Round 1** (blind, security and feasibility). Taken:
- **D's first form was wrong:** command jobs find `brunna` through the login shell when the profile has it.
- **Usage rows:** they don't exist for agent runs.
- **Tool servers:** the run wasn't sealed from them.
- **Limits:** there were two timers, a single shared queue, and a call could wait on itself.
- **Tool neutrality:** the rule on file-changing agent work wasn't addressed.
- **WebFetch:** it could get past `network:`.
- **C:** rule forms, a neutral home and limits.
- **Links:** they could lead out of the project.
- **The login:** it could reach a tool's scripts.
- **Credentials:** the headless token file wasn't on the deny lists.
- **The prompt:** it was passed as an argument.
- **Metered logins:** they weren't handled.
- **Settings file:** a per-run settings file was needed.
- **Missing steps.**

**Round 2** (blind, security and feasibility). Holes in what runs today, fixed and merged on 11-09 as three
`fix(confine)` commits, reviewed twice in code with nothing blocking:
- rules passed as options;
- prompts at any path;
- connectors and legacy_name writable by the project;
- links and starting folders out of the project;
- state folders shared between jobs;
- the token and shell files missing from the deny lists.

Blocking for this design, and settled in this draft:
- **Every confined run has Bash for the mail commands,** so a "declared Bash rule" condition never fired. Now: no mail
  for `project-agent`, and no shell at all for a sandboxed project's agent.
- **The throwaway-copy fallback couldn't run.** It is dropped: no `confine`, no run.

Also taken:
- **Rule checks** go in an adapter port.
- **Billing** is declared by the runtime.
- **Prompt on stdin:** `exec_stream` changes with it.
- **D:** it reaches every service and every job.
- **Pools:** per-pool limits, with nested calls refused at the server.
- **Global limit:** a daily cap across all projects, with fair order.
- **C:** a report before it lands.
- **Tier:** set before `model_for`.
- **Muninn** is told of the schema change.
