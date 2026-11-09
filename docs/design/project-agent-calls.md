# A project's own confined agent calls, per-job model tiers, declared secrets kept from confined runs

> **Status**: design, fourth draft (11-09). Not built.
> - Rounds 1–3 were blind cold reviews, each with a security reading and a feasibility reading. What they changed is
>   at the end.
> - The holes round 2 found in today's confined runs were fixed on their own first, in the three `fix(confine)` commits
>   of 11-09 (merged).
> - The owner decided on 11-09: a project's agent edits the workspace in place, under the runtime's confinement, as
>   `agent-job-confined` does. There is no throwaway-copy fallback.

Source: a project's request (11-09). Its research jobs (a Python runner) ran the coding agent themselves with allow/deny
flags, and failed from 10-26 because the agent's binary is not on a command job's PATH. It asked the scheduler to export
the binary's path, and that was refused:
- a core file names no tool;
- a project's code doesn't call the agent with the platform's login (the rules file's "Model calls").

It also asked the real question: **is there a judgment point a project's script can call, where every tool not listed
is refused, and how does the script find it without a path written into its repo?**

What exists, and why it is not enough:

- **Scheduled agent jobs.** `kind: agent` + `allow:` → `agent-job-confined`: dontAsk, only the job's rules and the
  platform's settings, unnamed tool servers denied. The boundary is right, but the shape is a whole scheduled job: a
  pipeline has to be cut into jobs chained with `after:`, and the prompt lives in the owner's config repo.
- **`brunna complete`** can be called from a script, but it only answers.
- **No model strength.** An agent job can't say which strength it needs.
- **The project's own secrets.** A confined run keeps the platform's credentials out of reach, but not the project's
  own.

## A. `brunna agent <name>`: a confined agent call a project declares

### A1. Declaration, call, path

The registry entry (project v1 and v2) declares it next to `models:`:

```yaml
agents:
  research:                       # lowercase letters, digits, -
    tier: strong                  # fast | standard | strong, as models:
    allow:                        # the whole boundary (A2); the same rule strings as a job's allow:
      - Edit(./data/research-log.md)
      - WebFetch(domain:example.org)
    timeout: 45m                  # optional; default 30m, at most {settings} cap_runner.agent_max_timeout_s (1h)
```

A script calls it from the project's workspace, its runtime folder, or a folder below them (a session, a command job, a
service):

```
brunna agent research [--schema F] < prompt
```

- **The id first.** It prints the run's id on stderr first, as `cap-run` does. A caller that is cut off reattaches with
  `brunna cap-run --wait <id>`.
- **stdout:** llm.py's JSON result: `result`, plus `structured_output` with `--schema`, or `llm_error`.
- **Exit codes**, split by who refused or failed:
  - 0: done.
  - 1: the run itself refused or failed, with `llm_error` saying why. This covers its rules being refused (A2), a
    runtime without `confine`, the billing gate, and its own timeout.
  - 126: cap-runner refused the request: undeclared name, an empty prompt, a limit reached, one already running. The
    reason is on stderr.
  - 124: cap-runner's deadline killed a hung run. The deadline is the declared timeout plus a minute.
  - 125: the runner isn't there, or lost the run.
  - 2: usage.
- **Where it runs.** The run's folder is the registry folder the request was found in, never a subfolder the request
  names. A call from a session in a linked worktree under the workspace therefore runs in the main checkout; the rules
  file says so.
- **Long calls.** A command job that calls `brunna agent` needs `background: true` and a `timeout:` of at least the
  agent's timeout plus queue time, since the scheduler stops a foreground job at 30 minutes. The rules file says so. A
  session's tool call times out sooner; it takes the id and `--wait`s.

The path is `brunna complete`'s, with a new request kind:

1. `hub-run --agent <name>` writes `{"v": 1, "id", "kind": "agent", "name", "prompt", "schema"?}` into the project's own
   `.hub-run/requests/`. The folder a request is found in is the project it belongs to.
2. cap-runner (not sandboxed) serves projects that declare `agents:` (`eligible()`, both folders). It checks the name is
   declared, the prompt is non-empty, the schema is an object, and the A5 limits. It writes `{prompt, schema}` into
   platform state and runs `llm.py agent <project> <name> --request F --cwd <that registry folder>`.
3. `llm.py agent` is a thin front on `run()`. It:
   - reads allow, tier and timeout from the registry, never the request;
   - sets `AGENT_JOB_ALLOW` and `AGENT_JOB_PROJECT`;
   - names `<project>/<name>` as the key for `model_for` (as `project_call` does), so an owner's pin can be per call;
   - sets `model_group` from the tier;
   - passes the request's schema and the declared timeout, which `run()` gains as parameters (today it takes only the
     point's).

   It then runs the judgment point `project-agent`: `shape: agent`, `permission_mode: dontAsk`, `prompts: none`,
   `settings: platform`, `project_env`, `allow_from_env`, `extra_args: ["--strict-mcp-config"]` and `mail: none`.
4. stdout and the exit code come back through `.hub-run/jobs/<id>/`.

The run gets everything the hardening gave confined runs:
- no rule that isn't a rule;
- no link or starting folder outside the project's folders;
- per-step link checks;
- the headless token and the shell's files denied.

### A2. What a confined run's rules may be: checked when it runs

The adapter gets a new optional port, `rule_problems(task, *, sandboxed, network, untrusted) -> [neutral sentences]`
(the adapter gathers the run's rules itself; a second function takes a job's `allow:` list for listings;
`docs/design/confined-mail-tools.md`, "Depends on"). **It runs when a run starts**, for every `settings: platform`
point (`agent-job-confined`, `agent-job-event`, `project-agent`, and the mail handlers), in `llm.py run`. A run whose rules it refuses fails with the sentences (job.failed, status page).

It is never a registry validation error. The registry checks only the shape (`rule_shaped`) and keeps every entry
valid, so an owner who tightens `enforce.sandbox` or `network:`, or changes runtimes, is never held back by a project's
rules. `registry.py check` and the status page list the rules each run would refuse. A manifest.update may also get the
list back as a note.

A runtime without the port refuses every confined run that has rules, saying why.

Refused, for every project:
- any `~/`, `//`, absolute or `..` path in a rule (paths are relative to the run's folder);
- a rule whose fixed part enters `.hub-run/` (a run never writes the platform's request folder, A1);
- for `project-agent`, any `mcp__` rule: the run loads no tool servers.

Refused, for a project with `enforce.sandbox: true` (for every confined point, not only `project-agent`):
- **Only these tools may be named:** Read, Glob, Grep, Edit, Write, MultiEdit, NotebookEdit, and `WebFetch(domain:d)`
  with `d` in the project's own `network:`. Not the platform's default domains: those exist for package installs by
  the shell, and they include sites anyone can publish to.
- **So no `Bash` rule, no bare `WebFetch`, no `WebSearch`, and no tool the list doesn't know.** Claude Code's sandbox
  can't keep a shell from the user's Keychain (tested 10-25), and the platform's own Seatbelt profile can't be nested
  inside it. A sandboxed project runs its scripts as command jobs or `cap-run`, before and after the agent.
- **An agent job without `allow:` doesn't run**, since auto mode with its saved settings has a shell. It needs an
  `allow:` list.

No sandboxed project has agent jobs on this machine today.

Not refused, and said plainly:
- **Reading.** Read, Glob and Grep are always on in a confined run, and the runtime reads its working folders without a
  rule. Everything in the run's folder is readable except the deny lists (and with C, the declared secrets). A
  `Read(./x/**)` rule doesn't narrow it.
- **The mail.** No confined run has the mail commands any more: the other confined points get the mail through the
  platform's tools (`docs/design/confined-mail-tools.md`, decided 11-10 after the probes of 11-09).

`mail: none` (point key, `confined-mail-tools.md` M1): no mail tools, and Bash is not in `--tools` unless a `Bash(...)`
rule is declared.

**The run's folder.** `llm.py` makes `<state>/agent-jobs/.settings/<project>.<point>.<run id>/` for each confined run
(`Task.run_dir`), and `platform_settings` writes the settings file in it; it is removed on every way out of the run,
instead of today's one file per project. Runs with different contents never share one. `confined-mail-tools.md`
"Depends on" has the details.

Edit and Write on `<workspace|runtime>/.hub-run/**`, and on every `.git` (`confined-mail-tools.md` M7), are denied in
every confined run's settings.

### A3. Edits in place (owner's decision, 11-09)

The agent changes the real workspace under the runtime's `confine` port, as `agent-job-confined` does. The data an
agent edits are often untracked, and `propose_apply`'s throwaway worktree holds only tracked files. The tool-neutrality
rule's "throwaway worktree for agent work that changes files" is met here by confinement plus the platform's own checks
around it: rule shape, links, folders, the deny lists, and A2.

A runtime without `agent` and `confine` refuses (`runtime_ports.Unsupported`), as `llm.py run` already does for
`settings: platform` points. Doctor's feature table gets the row.

### A4. The prompt is never a flag

Every task run's prompt goes to the runtime on stdin (`subprocess` `input=`), not after `-p`. Changes that go with it:
- `exec_stream`'s `cmd[1:3] = ["-p"]` splice changes with it;
- `tests/fixtures/llm-golden.json` is updated;
- a test checks that `run()` sends the prompt on stdin;
- `hubbus`'s `--resume … --fork-session` extras are unaffected.

This lands first.

### A5. Cost and fairness

- **Pools.** cap-runner has two pools:
  - agent calls (`cap_runner.agent_concurrency`, default 1);
  - everything else (`concurrency`, default 2).

  One job per project per pool, and within a pool the project served least recently goes first.
- **One call at a time.** An agent request from a project that already has one running or queued is refused (126). A
  queued one older than its declared timeout is refused.
- **Daily minutes**, in platform state:
  - per project, `cap_runner.agent_minutes_per_project` (default 90);
  - across all projects, `cap_runner.agent_minutes_per_day` (default 480).

  A request reserves its declared timeout when accepted; when it ends, the reservation is replaced by the minutes it
  ran. A request that wouldn't fit is refused with the numbers. The registry's `agents_per_day` can lower a project's
  share, never raise it.
- **Usage row.** `llm.py agent` writes one row per call to `.state/llm-usage.jsonl`, with a writer that doesn't need
  token counts (`TaskResult` has none). The row holds:
  - provider = the runtime;
  - the billing;
  - the project;
  - the point `<project>/<name>`;
  - the reported cost.

  It shows per project on the status page.
- **Billing**, for every agent run with a tier the project chose (`project-agent`, and agent jobs with `tier:`). The
  adapter declares `billing(auth) -> subscription | metered | unknown`:
  - metered needs the owner's `metered_models: true` and counts toward `llm.budget.daily_usd`;
  - unknown is refused;
  - subscription runs are bounded by the minutes above.
- **Budget.** `completion.today_cost()` counts only metered rows, so a subscription agent's estimated cost never uses up
  the budget the librarian's completions need.

## B. `tier:` on agent jobs

An agent job may say `tier: fast | standard | strong`.
- **How it reaches the run.** `registry.scheduler_jobs` puts `AGENT_JOB_TIER` on the command for every agent job (not
  only those with `allow:`). Points that declare `tier_from_env: AGENT_JOB_TIER` (`agent-job`, `agent-job-confined`,
  `agent-job-event`) use it as the run's `model_group`, behind the billing gate (A5).
- **Models.** `llm.model_for` stays the only place a group becomes a model, and an owner's per-point pin still wins.
- **`schedulectl models`** shows a job's tier.
- **Schemas.** `contracts/llm-points.v1.schema.json` gains `tier_from_env` and `mail`, and Muninn, which vendors it, is
  told in that step. `registry.TOP`/`JOB` and both project schemas gain `agents`, `agents_per_day` and `tier`.

## C. The project's declared secrets are kept from every confined run

`policy.secret_paths(project, doc)` (neutral) resolves each registry `secrets:` path:
- relative ones under both the workspace and the runtime folder;
- `~/` and absolute as given;
- returned as folders and files apart.

`platform_settings` adds:
- folders to `deny_read`, which becomes `Read(<dir>/**)` and the sandbox's `denyRead`;
- files through the same per-file pass as today's hidden state files, which gives `Read(<file>)` and the sandbox's
  `denyRead`;
- `Edit` on both;
- a `.env` with `env_key`, denied whole.

This covers every confined point. It is not in `policy.policy()`, since that also builds sessions' settings and
services' sandboxes, and a service reads its own credentials.

Before it lands, `registry.py check --secrets-report` lists the sandboxed projects with declared secrets and confined
runs. In an unsandboxed project a `Bash(...)` rule's script runs as the user and can still open them; the rules file and
this design say so.

## D. Services and jobs find `brunna` by name

A platform-owned folder `<shared>/cli/` holds only a `brunna` wrapper:
- it is written by `brunna install-cli` and `render-services`;
- it is never a symlink, mode 755;
- it is under the hub's `deny_write`.

Services:
- **PATH.** render-services appends it to every service's PATH. A service that sets `env.PATH`, or a bare command, gets
  its PATH plus the folder. A path-started service, which today gets the service manager's default PATH, gets that
  default (launchd's, systemd's) plus the folder, so nothing it finds today changes.
- **Restart.** Every service's unit changes once, so each restarts once. cap-runner's restart marks running jobs lost,
  so render-services applies the change when cap-runner is idle.

Command jobs:
- **Prefix.** The scheduler prefixes each command with `PATH="$PATH:<folder>";`, after the login shell's profile.
- **Unchanged.** `hubconf.LAUNCHD_PATH` stays as it is.

## Security, as a whole

- **The ceiling.** `agents:` and a job's `allow:` are the project's to write; the ceiling is what the platform loads
  with them and what A2 refuses when the run starts:
  - the sandbox, when enforced;
  - the deny lists: other projects, the platform's state and credentials, the login token, the shell's files, the
    request folder, and with C its own secrets;
  - no links or starting folders outside its folders;
  - no tool servers (`project-agent`);
  - for a sandboxed project, only file tools and its own `network:` domains, in every confined run;
  - no mail (`project-agent`).
- **A sandboxed project's code gains a model with tools.** That is the point of the feature. It is visible in
  cap-runner's log, in llm-usage per project and in the daily minutes, and it is bounded by the billing gate.
- **Reading is wide within the folder**, as said in A2. With WebFetch to its own domains, that is the reach a run has
  for sending out what it read. C keeps declared secrets out of it.
- **The prompt** is the project's text, under dontAsk; it can't widen the rules (A4).
- **A confined run can't change its own grants:** `CONFINED_TYPES` refuses `manifest.update`.

## Tests

- **Registry:**
  - `agents:` shape and `tier:`;
  - a project with refused rules stays valid, and the report lists them;
  - v1 and v2 schemas;
  - `--secrets-report`.
- **Adapter:**
  - `rule_problems`: each refusal, sandboxed and not; an unknown tool in a sandboxed project; `.hub-run`; domains from
    `network:` only;
  - `mail: none`;
  - `billing()`;
  - the per-run settings file, and its removal.
- **llm.py:**
  - `run` refuses a confined run whose rules the port refuses (all three points), and a sandboxed agent job without
    `allow:`;
  - `agent`: allow, tier, timeout and schema from the registry and the request; the per-call `model_for` key; the usage
    row; the billing gate; no `confine` → refused (`test_runtime_none_flow`).
- **cap-runner:**
  - each 126;
  - the cwd is the registry folder, and the prompt comes from platform state;
  - pools and least-recently-served order;
  - minutes reserved, then settled;
  - a stale queued request;
  - `eligible()` for `agents:`-only projects;
  - the id printed first.
- **completion:** `today_cost()` counts metered rows only.
- **A4:**
  - the golden;
  - the prompt on stdin;
  - the lease `exec_stream` command.
- **C:** a file secret gets `Read(<file>)` and the sandbox's `denyRead`; a folder secret gets `/**`; workspace and
  runtime folder both; sessions and services unchanged.
- **D:**
  - every service kind's PATH ends with the folder;
  - a path-started service keeps the manager's default before it;
  - a command job's prefix.
- **Lints and tests:**
  - the `features()` row;
  - i18n catalogs and goldens (cap-runner, hub-run);
  - `test_runtime_boundary` and `test_tool_agnostic`.

## Steps

1. A4.
2. A2's run-time check for the confined points that exist today (with its report run against the live registry
   first), the per-run settings file, the `.hub-run` deny. Then B with its billing gate and the `today_cost()` change,
   and C with its report first. Tell Muninn of the schema fields.
3. D.
4. A: `project-agent` with `mail: none`; `llm.py agent`; `hub-run --agent`; cap-runner's kind, pools and minutes. Then
   `brunna agent` in `SESSION_COMMANDS` and `policy.SESSION_SUBCOMMANDS`, the rules file's "Model calls", and docs.
5. Tell the project that asked.

## Review history

- **Round 1** (blind, security and feasibility), taken:
  - **D's first form was wrong.**
  - **Agent runs wrote no usage rows.**
  - **The run wasn't sealed** from the environment's tool servers.
  - **Timers and queue:** two timers, one shared queue, and a call that waited on itself.
  - **File-changing agent work and tool neutrality** were not reconciled.
  - **`WebFetch` reached past `network:`.**
  - **C:** its rule forms, its neutral home and its limits.
  - **Links out of the project.**
  - **The login reaching a tool's scripts.**
  - **The token file** was missing from the deny lists.
  - **The prompt was passed as an argument.**
  - **Metered logins.**
  - **A per-run settings file.**
  - **Missing steps.**
- **Round 2** (blind). Holes in what runs today were fixed and merged on 11-09 as three `fix(confine)` commits (twice
  code-reviewed, nothing blocking). For this design:
  - **The mail Bash in every confined run:** settled by `mail: none` and no shell for sandboxed projects.
  - **The throwaway-copy fallback:** dropped.
  - Also: rule checks in a port, billing declared by the runtime, the prompt on stdin, D for every service and job,
    pools and limits, the global limit, C's report.
- **Round 3** (blind). Blocking:
  - **A sandboxed project could still get a shell through a scheduled job's `allow:`.** Now A2 applies to every
    confined point, and a sandboxed agent job needs `allow:`.
  - **Rule checks in validation would freeze an owner's tightening behind a stale cached entry.** Now they run when the
    run starts, and the registry keeps the entry valid.

  Should-fix and nits, taken:
  - **Request folder:** `.hub-run` writes denied.
  - **Tiers:** behind the billing gate.
  - **Fairness:** a per-project minutes share, reserved then settled.
  - **Settings:** a file per run.
  - **Domains:** `network:` domains only.
  - **Tool list:** an allowlist of tools for sandboxed projects.
  - **Wide reading:** said plainly.
  - **Budget:** counts metered rows only.
  - **Long calls:** the run's id printed first.
  - **C:** file rules done correctly.
  - **`llm.py agent`:** a thin front on `run()`.
  - **D:** services keep their PATH base, and restart once while cap-runner is idle.
  - **`AGENT_JOB_TIER`:** set for every agent job.
  - **Exit codes:** split by who refused.
  - **Pins:** keyed per call.
  - **Tests:** a test for the prompt on stdin.
  - **Muninn:** told in step 2.
