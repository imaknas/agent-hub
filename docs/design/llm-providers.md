# Users choose the model, the LLM vendor, and bring their own API key (before open source)

2026-10-29 (second version: revised after blind review). Goal: once agent-hub is open source, users need neither a
Claude subscription nor Claude Code; one API key from any vendor (or a local model) is enough to run the platform's
nightly core; people with Claude Code keep using it. The owner's machine's current behaviour (everything through
Claude Code + the headless token) is one of the possible settings, and **no step changes it when it goes live**.

Continues `runtime-abstraction.md` (the runtime interfaces) and `capabilities.md` (the neutral permission policy).

## Where things stand

Every model call the platform makes goes through `bin/llm.py` and is a judgment point declared in
`contracts/llm-points.yaml` (12 of them); `llm.py` hands it to `runtime_ports`' agent interface, and Claude Code
commands are built only in `runtime_claude.py`. Four things are tied to Claude:

1. All 12 points are "run a coding agent once": the model reads and changes files with its own tools, and permissions
   are enforced by Claude's `allowed_tools`.
2. Model names are Claude's (`model_default: haiku|sonnet|opus`, `models.env`).
3. Credentials, checks and installation assume Claude Code (`bin/setup` lists claude as required, `claude-login`
   hourly, `update-claude` nightly).
4. The platform's **inputs** are Claude's too: memory notes are written by project sessions following
   `CLAUDE.local.md`, reminders rely on Claude's hooks, memory-gaps and the weekly report read Claude's conversation
   logs (the interfaces of layers 3–5 exist, but Claude is the only implementation).

## The fundamental change

### Judgment points come in two shapes

| Shape (`shape`) | What it needs | Who can run it |
|---|---|---|
| **completion** | prompt in, JSON matching a schema out; **the model has no tools and touches no files** | anything implementing the completion interface (API providers, local models, Claude Code with tools off) |
| **agent** | tools, working directory, permissions, continuing a conversation | only a coding agent runtime |

Whatever can be completion becomes completion: the platform puts what the model should see into the prompt, the model
answers "how to change it", and the platform checks that and writes the files itself.
The benefit is not only switching providers: **permissions go from "telling the model not to change things" to "the
platform only writes what it allows"** (today librarian-state holds haiku back with three layers, `--allowedTools` +
`dontAsk` + `--strict-mcp-config`), and input and output are both data, so testable.

**The model returns a list of edits, not a full rewrite**: `{"edits": [{"old": "…", "new": "…"}], …}`; each `old` must
occur exactly once in the original, the platform applies them in order, then checks mechanically that markers like
`(等 owner:<key>)` are neither missing nor added. That way "every other line unchanged, word for word" is guaranteed by
construction; a small model will not rewrite an 11KB STATE.md to change one line, dropping sections or breaking keys.

### Where each point goes

| Judgment point | Afterwards | Notes |
|---|---|---|
| librarian-state | **completion** | the prompt carries: the memory diff, the current STATE.md, AGENT.md, the list + README of `memory/reference/` (full text only for topic files to compare against), handoffs and records. Returns a list of edits + the metadata in today's JSON. A diff over budget is **split at commit boundaries**, one call per part, the checkpoint advancing to the end of that part (truncation has not triggered in recent nights, so nothing more complicated) |
| librarian-todo-format | **completion** | returns a list of edits to the `## 待辦` section (give it a schema; today it is text) |
| librarian-index | **completion** | returns a list of edits outside the roster block; the digest summaries it gets are filtered by privacy labels first |
| reflect-week | completion | already is (tools "") |
| consolidate | **agent (unchanged in the first version)** | has to read every existing dated file and the topic documents (a large project's raw records are 260KB+), which small and local models cannot hold. Could later be split into one completion per topic, separately |
| login-check | the completion interface's `health()` | the Claude-only one moves into the claude module |
| agent-job, mailbox-*, lease-* | agent | need tools, continuation, the permission relay |

Result: **with just one API key, the nightly librarian, todo, index and weekly report all run**. consolidate, mailbox
delegation, channel leasing and users' agent jobs need a coding agent runtime; without one they are **explicitly
off**, not silently degraded.

### A provider is just one interface of runtime_ports

No separate "provider kind" is invented: a **`completion` interface** is added to `runtime_ports.PORTS`:

- `complete(req) -> CompletionResult`: `req` = messages, output schema, model, `max_tokens`, timeout; the result = text
  / parsed JSON, stop reason (completed / cut off by length / refused), token counts.
- `health() -> (ok, reason)`: the smallest possible request.

Implementations:
- `runtime_claude.complete`: `claude -p` with every tool off, `--strict-mcp-config`, **the prompt passed on stdin**
  (today it is argv, and a large prompt exceeds ARG_MAX), **run in an empty neutral directory** (running in a project
  copy triggers the project's SessionEnd hook, a trap librarian.py has recorded).
- `provider_anthropic.py`, `provider_openai.py` (Chat Completions compatible + `base_url`: OpenAI, OpenRouter, Gemini's
  compatible endpoint, DeepSeek, LM Studio, vLLM) implement only the completion interface; `provider_ollama.py` uses
  the **native API** (the compatible endpoint cannot set `num_ctx`, and an over-long prompt gets silently cut from the
  front).
- `runtime_fake` gets completion, and the contract tests automatically run every implementation (`load()` already
  supports implementing only some interfaces).
- **`runtime_none`**: every interface explicitly answers "not supported + the reason". With no coding agent,
  `runtime.default: none`; a caller that gets "not supported" shows the reason (memory-gaps: "no source of conversation
  logs, not checked"; the weekly report: "no conversation logs, only commit and note statistics", sending no empty
  statistics to the model), **rather than running on with empty data**.

Standard library only (`urllib`); the platform's venv stays 3.13 + PyYAML.

### Structured output (the same procedure every vendor goes through)

1. If the provider supports a schema, use it, with the platform first converting the schema to the shape that vendor
   wants (OpenAI strict requires every field to be required + `additionalProperties: false`;
   `contracts/llm/*.schema.json` does not comply, and sending it as is gets a 400; if it cannot be converted, use
   non-strict). Anthropic uses a tool input, always with `max_tokens`.
2. On return, first strip reasoning models' `<think>…</think>`, then validate with `minischema` (however strict the
   provider claims to be).
3. If it does not comply, retry **once** with minischema's errors attached for the model; **a length cut-off is not
   retried** (it would be cut the same way again), it counts as a different failure (`truncated`).
4. If it still does not comply, the declared `on_failure` applies (today's mechanism, unchanged).
5. Measure the length before sending: if it exceeds that model's context it is not sent, with the failure reason
   "prompt too long", rather than letting the provider truncate silently.

## Settings

### 1. Judgment point declarations (the platform's; `contracts/llm-points.yaml`)

**Use fields that already exist in the shared format** (`contracts/llm-points.v1.schema.json`, the same file Muninn
vendors):
- `model_group` (exists): how strong a model is needed: `fast | standard | strong` (today's haiku / sonnet / opus).
- `material` (exists, `cloud_safe | personal | sensitive | local_only`): which category the content sent to the model
  belongs to.
- **New `shape: completion | agent`** (not `kind`: the shared format's `kind` is already `decide | generate`). Adding an
  optional field changes no meaning and needs no version bump, but the point level is `additionalProperties: false`,
  so **the schema has to change first, with Muninn's copy following the same day** (agree on the date by mail).

`model_env`/`model_default` are still read during the transition, removed later.

### 2. The user's choices (`hub.json`, validated by the config pipeline)

```jsonc
"runtime": {"default": "claude"},          // exists: the coding agent; "none" if there is none
"llm": {
  "providers": {
    "anthropic": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"},
                  "models": {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}},
    "local":     {"impl": "ollama", "base_url": "http://127.0.0.1:11434", "location": "local",
                  "models": {"fast": {"name": "qwen3:8b", "context": 32768}, "standard": {"name": "qwen3:32b", "context": 32768}}},
    "claude":    {"impl": "claude", "models": {"fast": "haiku", "standard": "sonnet", "strong": "opus"}}
  },
  "use": {"fast": "anthropic", "standard": "anthropic", "strong": "anthropic"},   // model_group → provider
  "points": {"reflect-week": {"provider": "local", "model": "qwen3:32b"}},       // per-point override (replaces models.env)
  "budget": {"nightly_usd": 2.0}
}
```

- **Model names are written inside the provider** (each vendor's names appear only under that vendor); `use` only says
  which group goes to which vendor; the models an agent runtime uses also come from its own provider's `models` (the
  `claude` one), with no separate table.
- A model can be a string or `{name, context}` (Ollama's names contain colons themselves, so no `provider:model`
  strings).
- **No `llm.providers` = handed to `runtime.default`**: models follow the runtime adapter's group table
  (`runtime_<name>.MODELS`), and `llm.points.<point>.model` can be written alone to override it. Across the whole
  platform, models are chosen only in `llm.model_for` (landed 10-29, `models.env` removed).
- `schedulectl set-model` rewrites `points` in `hub.json`.

### 3. API keys

- Values go only in `secrets_dir()` (`{"secret": "<name>"}`, chmod 600) or environment variables (`{"env": "…"}`). No
  values allowed in `hub.json`: config check blocks strings that look like keys (`sk-…`, long random strings).
- **Files are the default, env is an option**: under launchd there are no environment variables from the user's shell,
  so `hub doctor` passes in the terminal while the night schedule fails: doctor checks with the same environment as the
  scheduler, and says so directly when an env key cannot be read under launchd.
- `hub init` asks once and writes into `secrets_dir()`; `bin/hub llm set-key <provider>` reads from stdin (stays out of
  shell history).
- check-secrets automatically treats every `secret` in `llm.providers` as a platform credential (present, permissions,
  backed up).
- **Keys do not leak to child processes**: a provider reads it only when making the request; `auth_env` / `clean_env`
  explicitly remove every declared key variable (today `llm.py` hands the whole `os.environ` to child processes, so a
  key given by env would be inherited by every agent job that has Bash).
- **Uniform redaction before sending**: today only `reflect_llm.py` redacts credential-like strings; instead every
  send to any provider goes through the same redaction point in `llm.py`.

## Privacy: which content may go to which provider

> **⏸ This section is an old proposal, to be settled once evaluators mature** (see step 5 above). For the project-level
> field, `privacy.material` in `docs/design/evaluators.md` is authoritative; no separate `privacy.memory` is added.

- A provider's `location: cloud | local` (default cloud). **`local` only allows a `base_url` on loopback, the tailnet,
  or private ranges** (config check); it cannot simply be declared.
- A judgment point's `material` sets the minimum: `local_only` may only go to local.
- **New** at project level: `privacy.memory: cloud_ok | local_only` (default `cloud_ok`, a security field managed by
  the platform). **`privacy.reflection` is not reused**: it means "conversation content stays out of the weekly
  report", not "memory may not go to the cloud"; today 3 `stats_only` projects still send to cloud models every night
  to update STATE.md, and reusing it would stop their STATE.md from updating. A `local_only` project with no local
  provider does not run that point (the status page says why); it does not fall back to the cloud.
- The cross-project digest summaries librarian-index takes are filtered by the same labels.

## Cost and quota

- APIs only return token counts: `llm.py` accounts by token (a price table in the provider settings, optional; without
  it only tokens are recorded), shown on the status page.
- `budget.nightly_usd`: when exceeded, the remaining judgment points stop (`on_failure` as declared) and an alert is
  sent; unlimited if not set.
- 429 / 529 retried with backoff (capped), not counted towards the "retry once on non-compliance".

## Users with only an API key: every place must be explicit

| Where | Today | Afterwards |
|---|---|---|
| `bin/setup` | claude required | optional (none = `runtime.default: none`) |
| `claude-login` (hourly), `update-claude` (nightly), doctor's headless token | platform jobs | moved into `modules/claude.yaml`, on only when `runtime.default == "claude"` or a provider uses claude |
| memory-gaps, the weekly report's conversation statistics | read Claude's conversation logs | `runtime_none` answers "not supported" → says explicitly: not checked / only commit and note statistics |
| memory notes (the platform's input) | written by project sessions following `CLAUDE.local.md`, reminded by hooks | see point 3 of "For the owner to decide" below |
| consolidate, mailbox delegation, leasing, agent jobs | run | off, explained by the status page and doctor |

## Steps (each goes live on its own; the owner's machine unchanged)

1. ✅ ratchet (1186f65): Claude syntax and model names outside `runtime_claude.py` may only go down (baseline 30 files,
   139 places).
2. The `completion` interface + `runtime_claude.complete` (stdin, neutral directory) + `runtime_fake` + `runtime_none` +
   contract tests; `llm.py` understands `shape`/`model_group` and `hub.json llm` (none = today's behaviour). **At the
   same time** the Claude-only jobs move into `modules/claude.yaml` and claude becomes optional in `bin/setup`: people
   with only an API key are not flooded with alerts from day one.
3. `provider_anthropic`, `provider_openai`, `provider_ollama`; the structured-output procedure; key location,
   redaction, `clean_env`, check-secrets, config check; token accounting, budget, backoff. The shared schema gets
   `shape`, the same day as Muninn.
4. Judgment points switched to completion one by one, each first in **shadow mode**: the completion version runs in
   dry-run alongside today's agent version for a few nights, comparing the proposed results (STATE.md, to-do section,
   index notes), and switches when there is no problem. Order: todo-format → index → state. The golden files change to
   fix "the edit list the stand-in returns → what the platform writes" (today's stand-in writes files directly to
   imitate the agent, and has to change accordingly); the path in `librarian.py` "no structured_output, go by the actual
   files" is removed after the switch.
5. ⏸ Privacy routing: **to be settled once evaluators mature** (the owner on 10-29: no enforced privacy policy for now).
   A version was made on 10-29 (b8f359d: `privacy.memory`, `material` marked on each point, llm.py blocking the cloud,
   digest filtering) and then reverted: it pre-empted what evaluators.md is to decide (project level uses
   `privacy.material`, `evals.trust`). All that remains is the provider's `location` field and its config check (local
   only on private ranges).
6. ✅ `models.env` → `hub.json` (10-29: `llm.model_for` is the only algorithm; librarian / consolidate / the weekly
   report record the `llm_model` llm.py reports; `model_group: default` = not specified, use the tool's default;
   `schedulectl set-model` writes `llm.points`). ✅ `agenthub llm setup` (init calls it too): choose the coding agent's
   own login / Anthropic / OpenAI (or compatible) / local Ollama; the key is written into secrets with getpass (600),
   the model chosen from that vendor's `list_models` (the platform does not guess names); doctor says who judgment
   points go to, and shows red when there is nowhere to run them.
7. Run a full nightly flow on a local Ollama (isolated HOME, nothing sent to the cloud): proving that local models alone
   are enough.

## Out of scope (later)

- Neutral permissions for agent points (`tools`/`allowed_tools` → neutral vocabulary): the shared format has to change
  in sync with Muninn; separately.
- Runtimes for other coding agents (Codex, Gemini CLI, opencode): the interfaces and contract tests are there; the
  community can add them.
- Splitting consolidate into completions.
- A small agent the platform runs itself with function calling (so mailbox delegation also needs only one key).

## For the owner to decide

1. Two shapes, completion wherever possible, the model returns a list of edits; consolidate stays agent in the first
   version.
2. The first open-source version supports only Claude Code for agent points; without it those features are off.
3. **Where memory notes come from** (for people with only an API key and no Claude Code, the platform has no input):
   (a) add an `AGENTS.md` implementation to layer 4 (Codex, Cursor and Gemini CLI all read it): the rules are written
   in as before, asking the agent to write notes; with no hooks there are no reminders, doctor explains. Low cost,
   covers most coding agents.
   (b) The first version supports only Claude Code as the interactive agent; others write notes themselves.
   (a) proposed.
4. Privacy: add `privacy.memory` (default `cloud_ok`, unchanged on your machine), not reusing `privacy.reflection`.
5. The shared format gets `shape`; agree with Muninn to change it the same day.
