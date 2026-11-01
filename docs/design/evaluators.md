# Evaluating judgment points (evaluators): every place that touches a model can be used to evaluate switching models

> agent-hub session · 2026-10-27 · **r5**. r1–r4 each went through one blind review (each "not ready to start"), with
> two rounds of sealing experiments in between. r5 goes by the second (recorded) experiment: the raw output is in
> `.state/seal-check/2026-10-27/` (`FINDINGS.md` is the summary, `../seal_exp2.py` the program). Implementation starts
> after the last blind review.

## Goal and scope

The owner's requirement: **every place at the boundary that touches a model must have an evaluator**, the point being
to evaluate whether models can be switched (cheaper, local, another vendor's).

**The owner's decision (10-27)**: agent jobs (job-board work, video research, literature search…, registry jobs going
through the judgment point `agent-job`) get **no** evaluator in phase one: these projects carry less privacy risk and
less pressure to move to local models. Recorded as a known, accepted gap; they still need MCP sealing (done) and an
`eval` in their declaration (`scorer: none` + the reason "owner decided 10-27 not to evaluate for now").

**What phase one can answer**: every call leaves a re-runnable case; whether a candidate passes the mechanically
checkable parts; how consistent a candidate is with the incumbent in the same sealed environment, against how
consistent the incumbent is with its own reruns (the noise floor), with confidence intervals grouped by project.
**What it cannot answer yet**: for most points "which is more right" (librarian-state has no reliable right-answer
signal); reflect-week has too few samples. Phase two.
**The range of model switching**: in phase one, class B candidates are only other claude models; switching class A to
local/other vendors needs a plain-text runtime (the contract tests first change to follow the interfaces a runtime
declares), scheduled late in phase one.

The skeleton follows Muninn (`src/muninn/core/evals.py`, `cases.py`).

## Principles

1. The declaration is a contract; running it is a module that can stay off (`modules/evals.yaml`, off by default).
2. Evaluation goes through the runtime interface; the evaluation code writes no runtime-specific syntax.
3. **Sealing is measured**: every production call reads its own `system/init` and records what was actually loaded
   this time (`sealed` is measured per call, not assumed).
4. Replays may not touch anything real; **no call may connect to the MCP servers in the environment** (including the
   control groups of experiments and seal-check).
5. Cases live only in the platform's evaluation directory (0700, not under any judgment point's cwd, not in git).
6. Recording may only cost a little time; it may not change a judgment point's behaviour.

## Already done

- **dcc61b6**: every headless judgment point carries `--strict-mcp-config` (experiment: `--tools ""` does not block
  MCP; a call with all tools off still got 265 MCP tools, including `execute_sql` on the production database).
  Exception: jobs with `connectors: true` (those that need a claude.ai connector) still load every server; since
  b5c4836 + 734eec9 (v0.2.0) every one their allow list doesn't name is denied whole, computed per run (step 0, "The
  connectors jobs' production database", done).
- **ca3ad1d**: from 10-27 to 11-01 the connectors job never actually ran: `run-agent-job.sh`'s background run uses
  macOS's `/bin/bash` 3.2, where the empty argument list a connectors job gets is an unbound variable under `set -u`;
  it died before llm.py with its status left at "running" (known-issues.md).
- **c89c25c**: the judgment points' model settings (`models.env`) never took effect before (`librarian.sh` did not
  export them); `llm.py` now reads them itself.
- f2755ca (headless calls do not receive mail), 10c5fbb (delivery into a session only counts as seen).

## Conclusions of the sealing experiments (second round, recorded)

Every run carries `--strict-mcp-config --no-session-persistence`; the task = read STATE.md and add a line with Edit (a
real tool, `--allowedTools Edit(./STATE.md)`, dontAsk); the temporary cwd has project-level canary hooks
(SessionStart/PostToolUse/SessionEnd each write a marker file); a separate `--debug hooks` run shows what actually
executes.

| Setting | Result |
|---|---|
| Not sealed (haiku, sonnet) | all three canaries fire; vercel plugin; skills 61, agents 9; auto-memory on: **pointing at agent-hub's own memory** (the cwd is inside agent-hub's git repo, and Claude treats the repo as the project) |
| `--settings`: hooks off, auto-memory off, user-enabled plugins off | 0 canaries, vercel gone, auto-memory gone, Edit works; in the sonnet run modelUsage also has only sonnet. **But the built-in plugins' hook modules still run** (`cc-plugin-agents-md` listens to `prompt.context` and injects AGENTS.md-like content; telemetry; plugin-authoring): `disableAllHooks` does not reach them |
| Also setting the three `@builtin` plugins to false | **complete**: plugins empty, 0 built-in modules, 0 canaries, auto-memory off, Edit succeeds |
| `--setting-sources ""` (± auto-memory off) | built-in modules still there, no better than the previous row |
| `--bare` | unusable (the headless token is not accepted) |
| Two `--settings` | the later one overrides the earlier entirely, no merge (first round) |
| Leftovers | no new `~/.claude/file-history` entries despite the Edit; no conversation log |

Not tested: whether the advisor appears in long tasks; whether there are injection sources besides the built-in
modules. Both are watched continuously through the per-call init records and modelUsage.

## Step 0: sealing the class A/B judgment points

Split into two things: **`seal`** (blocking what the environment brings in) and **`persist_transcript`** (whether a
conversation log is kept).

| Class | Points | seal | persist_transcript |
|---|---|---|---|
| A, B | reflect-week, librarian-state, librarian-todo-format, librarian-index, consolidate | all | no |
| R | lease-handover, mailbox-conversation | MCP only (done) | yes (continues the conversation) |
| C | agent-job, mailbox-escalation, lease-session, lease-work | MCP only (done) + the deny for connectors jobs (done) | yes |
| N | login-check | MCP (done) | no |

**The sealing arguments** (generated by runtime_claude; row 3 of the experiment): `--strict-mcp-config
--no-session-persistence` + **one** `--settings` (inline JSON, not a file: `--settings` takes file-or-json; this also
avoids paths differing between machines): `disableAllHooks:true`, `autoMemoryEnabled:false`, `enabledPlugins` =
**every** plugin this call can see (those enabled at user/project/local level + the built-in ones) all false,
`syncClaudeAiPlugins/Skills:false`, `advisorModel:""`, `autoDreamEnabled:false`. The list of built-in plugins is
**taken dynamically**: one discovery run per Claude version (`--strict-mcp-config` + everything else sealed but plugins
not turned off, reading `plugins` from init), cached in `.state/seal-check/<version>.json`.

**Only the runtime may give `--settings`**: the experiment showed two `--settings` override each other. Policies (the
lease's deny and the like) become fields of `Task`, and the runtime combines them into one; a contract test blocks
`--settings` appearing in `task.extra`. Leases (lease-runner passes `--settings` through extra today) are left alone in
step 0 and switched when they are next changed; until then classes R/C get no sealing settings, so nothing clashes.

**The connectors jobs' production database** (**done**, v0.2.0: b5c4836, 734eec9): they cannot use strict-mcp, so
permission rules deny the MCP servers in the environment instead, allowing only the servers whose tools their registry
declares.
- *Neutral*: the points `agent-job`, `agent-job-event`, `mailbox-escalation` declare `tool_servers: allowed_only`.
  llm.py asks the runtime (interface `tool_servers`: `ambient_servers(task)`, `servers_named(task)`) which of the
  environment's servers this run would load, `policy.tool_server_denials` keeps those the allow list doesn't name, and
  they go to the runtime in `Task.deny_servers`. A sealed run (`ambient_servers` → None) denies nothing, so the
  non-connectors jobs keep `--strict-mcp-config` exactly as before (`tests/test_run_agent_job_args.py`).
- *Adapter* (runtime_claude): what the run would load = `claude mcp list` run in the job's folder with the job's
  environment (login, config directory; the only place the account's claude.ai connectors show up, plugins' servers
  too) ∪ the config files (`~/.claude.json` user scope and every project's local scope, `.mcp.json` in the folder and
  every folder above, the managed file). Names become ids as Claude Code makes them (`[^a-zA-Z0-9_-]` → `_`); each
  denied server is `--disallowedTools mcp__<id>` (a whole-server rule: its tools are not even offered), plus the three
  built-in MCP resource tools, which would reach a denied server's resources by name.
- *Fail closed*: the config unreadable, `claude mcp list` failing, timing out or printing a shape it can't read, a user
  server the list doesn't show (its output changed under us), or a runtime without the interface → the run doesn't
  happen: `job.failed`, an alert (`tool-servers:<point>`, recovery sent when it can tell again), exit 1, the reason in
  the job's status. A connectors job without `allow:` gets every server denied.
- *Live probe* (11-01, Claude Code 2.1.316, subscription login, through `run-agent-job.sh` with its own name, folder
  and state; allow = the Upwork connector's `get_tool_help` + `ToolSearch`): the deny listed 25 servers (the user's
  three including the production database's, two other folders' local ones, two of the account's connectors, 18 plugin
  servers); all 24 the run loaded still started and connected (deny rules don't stop that), but the init offered only
  the allowed connector's 33 tools: none of the production database's (nor the resource tools after 734eec9). The
  agent's `list_schemas` on the production database and a vercel plugin search both came back "No matching deferred
  tools found" from ToolSearch: never offered, never called; `get_tool_help` on the allowed connector was called and
  answered. Nothing was written anywhere.
- *Costs*: every connectors run first health-checks every configured server (`claude mcp list` connects to each,
  calls none; the run itself connects to them again).

**Environment variables**: sealed points use the passthrough list the runtime declares
(`runtime_claude.passthrough_env()`: PATH, HOME, LANG, TMPDIR, `CLAUDE_CONFIG_DIR` (the laptop's personal profile uses
`~/.claude-personal`), proxy/CA variables, `ANTHROPIC_*`, login variables, `HUB_LLM_POINT`).
**cwd**: reflect-week's cwd today is `~/agent-hub` (`weekly-reflect.sh` first `cd`s to the hub, and `reflect_llm.py`
gives no `--cwd`), so it loads agent-hub's CLAUDE.md and its project hooks: **changed to an empty temporary
directory** (a behaviour change, compared at go-live). For class B see "Replay fidelity".

**Recording init per call**: in production, A/B points switch to `--output-format stream-json --verbose` (parsed by
runtime_claude; the format output to llm.py is unchanged), and every call keeps a summary of init: `mcp_servers`,
`plugins`, `tools`, `memory_paths`, model, version. **Verdict**:
- `mcp_servers` not empty, or `tools` more than declared → **this call fails** (goes to on_failure; a safety property,
  not just an alert);
- plugins not empty, `memory_paths` set → alert + the case marked `sealed:false` (excluded from replay), the call
  proceeds.
This verdict is made even when the recorder is off (it is a safety check, not an evaluation feature).

**seal-check (before go-live, and when the version or settings change)**: following
`.state/seal-check/seal_exp2.py` (a real tool, one harmless Edit, canary hooks, `--debug hooks`), **with the control
group also carrying `--strict-mcp-config`**; the cheapest model, once per version (not per point). Triggers:
`claude --version` changed, or the hash changed of `~/.claude/settings*.json`, `~/.claude/remote-settings.json`,
`~/.claude/plugins/installed_plugins.json`, or the mcpServers in `~/.claude.json`; locked. It tells "could not run"
(login, quota) apart from "leaks".

**Go-live**: daytime; check librarian and consolidate that night; force a comparison run of reflect-week (Mondays) and
consolidate (once a week per project) with `schedulectl run`. Golden files: the inline settings content and the dynamic
plugin list must be normalised. Production runs `librarian.sh`; `librarian.py` also takes its arguments from the
declaration.

## Replay fidelity

- `--exclude-dynamic-system-prompt-sections` **only moves** the sections that differ per machine (cwd, environment,
  memory paths, git status) into the first message; it does not remove them: r4 was wrong to treat it as the fidelity
  switch. Not used.
- Class B's cwd is a git repo (the memory copy), and the system prompt carries its path, git status and recent commits.
  Approach: **production also runs in a fresh clone**, with the same path rule as replay
  (`<temp root>/agent-hub-eval/<point>/<project>`), using `git clone` at that commit (not `git archive`: history and
  status are needed); afterwards the changes are brought back to the copy through the existing flow. consolidate
  already runs in a temporary clone; it switches to the same fixed path rule.
- Cases record the cwd, `git status` and the date; the date cannot be fixed, so a drift check of "incumbent in
  production vs incumbent in replay" quantifies it.
- CLAUDE.md-like files: the memory copies have no CLAUDE.md today (only AGENT.md), and there is none at user level; the
  built-in agents-md module is already off. Cases record whether the cwd's ancestor directories have instruction files
  (hash), and replay puts them in place if so.

## Judgment point classes and sample sizes

| Class | Judgment point | How it is evaluated | Sample size |
|---|---|---|---|
| A | reflect-week | rerun with a different model/runtime | once a week. **Backfill** only for weeks in the current format (from 10-21 only statistics + digest are sent) whose digest still exists; marked synthetic. About 5 weeks: not enough for a conclusion; reflect-week relies mainly on phase two's judge and owner signals |
| B | librarian-state | snapshot rerun, scored in parts | about 6 per night; grouped by project |
| B | consolidate | snapshot rerun; existing mechanical checks | about once a week per project; opus, its own budget |
| B | librarian-todo-format | only the replay path is tested; cases synthesised from historical STATE.md | once in 37 days |
| B′ | librarian-index | becomes B once its input is narrowed to `digest/$TODAY.md` + `index.md` | once a night |
| R, C | see above | not replayed; class C not evaluated in phase one (owner's decision) | — |
| N | login-check | none | — |

## Declaration format (synced with Muninn the same day)

Additions only, no version bump: `class`, `seal`, `persist_transcript`, `writes: [paths]`, `runtime`, `eval` for
exempt items. A point's `material` and `eval` fill in the shared fields. Consistency test: class may not contradict
seal/persist (class B cannot have seal:false). Job names for `agent-job`: `run-agent-job.sh` passes `--job` to `llm.py`.

## Permissions

`policy.policy()` is the policy for a "project workspace", and its deny happens to block exactly the memory copies the
librarian has to read and write: it cannot be used to generate permissions for platform judgment points. **A new
point-scoped policy** `policy.for_point(point, cwd)`: generates write permissions only from the declared `writes`
(relative to cwd); platform judgment points do not get the project's deny; both production and replay are generated
from it (replay additionally blocks secrets and the evaluation directory). Non-write rules (hubbus's Bash, MCP tool
names) stay in `allowed_tools`. Contract test: production and replay permissions are the same.

## Cases, recording, replay, scoring, privacy

(Same as r4, summarised) allow-listed fields (no `Task.env`, no tool inputs of denials, no full results of class C);
`TaskResult` gains `duration_ms`, `resolved_model` (the main-model rule: the entry in modelUsage for the declared/chosen
model); `evals_dir()` = `~/.local/state/agent-hub-evals/`, 0700, SQLite; not in the cloud backup by default (and the
other copies are written down: review-log, agent-jobs reports, conversation logs from before sealing); `eval_case`
passed back up + an `Eval-Case` trailer; `Hub-Writer: platform-model | project-agent | mechanical`; only the owner's own
signals count as right answers; replay = the same sealing arguments + `HUB_EVAL_REPLAY=1` (recognised by
llm.py/hubbus/alert.py) + `policy.for_point` + a fresh clone as cwd, refusing R/C/N/`sealed:false`; candidate and
incumbent both run k times, bootstrap grouped by project, a run budget, avoiding the night, postponed while a lease is
open; scorers in `bin/evals/`, scored in parts; a case's sensitivity = the project's `privacy.material`
(`project.v1.schema.json` and `registry.py` change accordingly; not a shared format), with the point's `material` as
the ceiling; `evals.trust` by default lets other vendors' clouds see only cloud_safe, and judges are filtered the same
way.

## Guarantees at the boundary

- The lint (every runtime declares how it is launched, recursive scan of `bin/`) **only warns**: relying on strings
  always misses something (e.g. runtime_claude's own `[exe, "-p", …]`).
- **The guarantee at run time**: `hubconf.tool("claude")` is replaced by a shim: carrying `-p` without `HUB_LLM_POINT`
  is refused and recorded; in addition, hub-event's SessionStart records sessions that are "headless, without
  HUB_LLM_POINT" (`.logs/hub-event.log`), and the coverage report lists them.
- (10-30: `trajectory.py` moved out of the platform and became the instance's own tool; this item and point 3 to
  confirm no longer apply) `trajectory.py`: its llm-points exemption says "decided by the owner on 2026-10-22 to keep it
  as is"; moving it to llm.py needs his explicit agreement (to confirm, #3); until then it already carries strict-mcp.

## Decoupling for open source

| Layer | Open source | Default for new users | The owner |
|---|---|---|---|
| MCP sealing (done), A/B sealing, per-call init verdict, seal-check | ✓ | **on** (safety property; seal-check once per version, cheapest model) | on |
| Declaration fields, lint (warning), shim | ✓ | on | on |
| Scorers, replay, plain-text runtime | ✓ | present; run them yourself when switching models | ✓ |
| Recorder, feedback | ✓ | **off** | on |
| Real cases | ✗ | — | local |

## Steps

**Step 0**: runtime_claude's sealing argument generator (inline settings, dynamic plugin list) + per-call init verdict
→ A/B points switch to it → passthrough env, empty cwd for reflect-week, the fresh-clone path rule for class B → MCP
deny for connectors jobs (done first, v0.2.0; the rest of step 0 is still the plan) → seal-check (with version/settings hash triggers) → golden files → daytime go-live, check the
results that night, force a run of reflect-week and one consolidate.

**Phase one**: schema fields (Muninn the same day) → `policy.for_point` → shim + coverage → recorder → replay → scoring
(librarian-state, consolidate) → reflect-week backfill (synthetic, small) → contract tests by interface → plain-text
runtime → coverage report.

**Phase two**: collecting truth, the assistant adapter, pairwise judge, librarian-index to B, online evaluation of
class R, class C (to be discussed after the owner decides), other vendors' agent runtimes.

## To confirm

1. Which projects' data may be marked cloud_safe (decides which cases other vendors' models can be compared on).
2. Cases are not in the cloud backup by default.
3. `trajectory.py` moves to llm.py (overturning the 10-22 "keep as is" exemption).

## Review and experiment record

- **r1–r3 blind reviews**: see this document's git history; main points: replay isolation, case location, case id
  direction, continuing conversations, loops, tokens, input reproduction, splitting seal/persist, `--settings`
  overriding each other, advisor, sample sizes, permission sources, formats to change, lint. Also found and fixed:
  headless calls receiving mail (f2755ca), delivery into a session only counting as seen (10c5fbb).
- **First round of experiments**: `--tools ""` does not block MCP → dcc61b6; two `--settings` override each other.
- **r4 blind review**: experimental evidence insufficient and not kept; the control group would reopen the MCP leak; an
  MCP leak must count as failure; dynamic sections are moved, not removed; `--bare`/`--setting-sources` not tried; a
  run-time gate cannot guarantee it; reflect-week backfill not faithful; leases' settings have no path; class C has no
  evaluation; minimal_env breaks other settings; `policy.policy()` blocks exactly what the librarian needs; also found
  that `models.env` had no effect (c89c25c).
- **Second round of experiments** (recorded): built-in plugins' hook modules are not governed by `disableAllHooks` and
  must be turned off with `enabledPlugins`; `--bare` unusable; `--setting-sources ""` no better; auto-memory points to
  the memory of the git repo that contains the cwd.
