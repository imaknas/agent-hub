# What to take from the competition (11-01)

A competitor survey on 11-01 put about 37 products against agent-hub's 12 feature areas: vendor-native features,
parallel-session orchestrators, personal-agent platforms, integrated control planes, memory layers and single-layer
infrastructure. No product covers all 12. This file decides, item by item, what agent-hub takes from them, what it
leaves to them, and what it rejects, with the facts each decision rests on.

The survey's claims about other products were checked again against a primary source before an item was decided here
(npm, the GitHub API, the installed CLI's own help). Claims still unchecked are marked as such.

## What stays agent-hub's own

These areas have no counterpart in any product surveyed, or no counterpart that works the same way. Keep them; they
are why the platform exists:

- the owner loop: Principal Handoff, where only the owner side closes;
- a mailbox addressed to projects, with platform dispatch when nobody picks a letter up;
- the nightly cross-project curation into STATE.md and the digest;
- services, sandbox, credentials and backups driven by the registry;
- the rule against silent degradation, applied to models.

The survey also found that supporting many coding agents is now table stakes. Paseo and Emdash wrap the same agents
agent-hub does. Tool neutrality stays a hard rule (AGENTS.md) but is not what sets agent-hub apart. What sets it apart
is that the platform guarantees the result: what has to hold does not depend on a tool's features.

## Build (each is a task of its own, with its own design and review)

1. **Event-triggered jobs.** The model is Cursor Automations: cloud agents started by CI results, reviews, Sentry,
   PagerDuty, Linear or a webhook. agent-hub's jobs run on a clock or `after:` another job. The bus already records
   `merge.done`, `merge.failed`, `config.applied` and letters, so the first step is a registry `jobs:` field that
   starts a job on bus events. External sources (CI finished, a webhook) come in as bus events from one receiver
   later, so jobs only ever react to bus events. Open design questions:
   - debounce and rate limits;
   - loops (a job whose own events trigger it again);
   - auth for the receiver;
   - what the status page shows.
2. **An ACP runtime adapter (`runtime_acp.py`).** OpenHands Agent Canvas, Goose, OpenClaw and Hermes all speak the
   Agent Client Protocol. Claude has `@agentclientprotocol/claude-agent-acp` (0.99.1, Apache-2.0, published
   2026-10-30, checked on npm). One adapter turns "any coding agent" from a claim into a test against several real
   agents. Open design questions:
   - which `runtime_ports` ports ACP can fill: task, streaming, resume/fork, permission relay through ACP
     elicitation;
   - which features turn off explicitly (`Unsupported`) for an ACP agent;
   - how the lease runner maps onto ACP sessions.
3. **Staleness anchors in STATE.md.** The model is Copilot Memory, which checks each memory's code citation before it
   uses the memory. A STATE.md statement about code can carry an anchor: a path, or a path plus a symbol. A zero-LLM
   nightly check, shaped like `check-memory-gaps.py`, lists statements whose anchor is gone, in `todo.md` and the
   digest. The librarian's prompt then writes the anchors. That prompt change goes through the e2 evals like any
   other. Before building:
   - check that the anchor's syntax can't be mistaken for a key marker or a label in either token set
     (`bin/todo_tokens.py`);
   - check that the anchor stays out of the to-do section's format, or else bump the contract.
4. **A sandbox backend interface under `isolation.py`.** Today `seatbelt.py` and `landlock.py` are the only
   backends. Make them one implementation of an explicit backend interface, with characterization tests on the
   current behavior first. A second backend can then plug in behind the same tests. The policy (`policy.py`),
   registry-driven settings, tamper detection, service sandboxing and the capability runner stay agent-hub's.
   Security-critical code we maintain ourselves is a liability, and the survey cites a week in July 2026 with sandbox
   escapes in several vendors' tools. Whether a second backend is adopted is an owner decision (below).
   **Done 11-01**: `guarantees()` per adapter, `isolation.REQUIRED`, and the `Conformance` tests (one probe through
   `sandboxed-run`, whatever the adapter).

## Owner decisions

- **Adopt an external sandbox backend?** The candidates, checked 11-01:
  - nono (`nolabs-ai/nono`, Apache-2.0, about 4.8k stars, v0.81.0 released 2026-10-29) uses the same mechanisms we
    do, Landlock and Seatbelt, wraps any agent, and has a proxy mode that keeps keys outside the sandbox. Its kernel
    requirements are still unchecked (sources disagree).
  - `@anthropic-ai/sandbox-runtime` (0.0.81, Apache-2.0) is what Claude Code uses, but it uses bubblewrap on Linux,
    not Landlock, and is labelled a research preview.

  Recommendation: build item 4 first. Then run nono behind the interface against our own tests. Switch the default
  only if it passes and still blocks what ours blocks (the Keychain on macOS, other projects' folders).

  **Tried 11-01 (owner: "try it"), nono 0.81.0 on macOS and in the Linux test container (kernel 6.8), the Conformance
  probe run by hand** (fixture outside the temp area, which nono allows by default):

  | guarantee | macOS | Linux 6.8 |
  |---|---|---|
  | own-writes-only | yes | yes |
  | isolated-reads (it allows only what is granted) | yes | yes |
  | declared-port-only | **no**: `--listen-port`/`--open-port` don't restrict; any port binds and listens, `0.0.0.0` too. `--block-net` blocks every bind, the declared port included | **no**, the same |
  | keychain-blocked | yes | (n/a) |
  | signals-blocked | yes | no (ABI < 6, like ours) |
  | unix-sockets-blocked | no | no |
  | outbound domain allowlist | **yes** (`--allow-domain`: direct connects refused, HTTP(S) through its proxy filtered by host) | (not tried) |

  **Decision: not adopted as a backend.** It lacks `declared-port-only`, which is in `isolation.REQUIRED`, so
  `isolation.can_sandbox()` would refuse it. No adapter is written for it, because one would only ever be refused.
  What it has and we don't is the outbound domain allowlist (`policy.network` is enforced by neither of our
  adapters). It can't be layered on top of ours: on macOS both use Seatbelt, and Seatbelt profiles can't be stacked.
  Look again when a release restricts listening to the declared ports. Reporting the gap upstream is an outward
  action, so it is the owner's call.
- **Publish Principal Handoff outward?** The Acts profiles already live in agent-ops-specs. No standard covers
  cross-session owner questions that persist, carry a key and close with done/drop: ACP's elicitation (stable
  2026-07-24) works only inside one live session. The choices are to publish the spec as it is, to propose an RFD to
  ACP, or to wait. Whoever defines this first sets the terms. Going outward is the owner's call.
- **Credentials kept in a password manager?** Infisical Agent Vault and 1Password for Agents inject a value so the
  agent never sees it. agent-hub keeps an inventory of names, locations and expiry dates and audits it daily. The two
  complement each other: a `location` could point at a 1Password item, and the daily check would still cover
  existence and expiry without reading the value. `op` is not installed on this machine, so this only matters if the
  owner wants credentials in a password manager.
- **Positioning.** Describe agent-hub as "one person's agent operations platform, where the platform guarantees the
  result" rather than leading with "works with any tool". This changes the README and the open-source story, so it is
  the owner's decision.

## Not now, with the reason

- **Native cross-session messaging as a delivery path.** Claude Code can message a running session and wake an idle
  one, but only from inside another session. The installed CLI (2.1.316) has no command to send to a session, so the
  platform's dispatcher cannot use it. Revisit if the CLI exposes it. Even then it would only be a faster first try;
  the platform's own dispatch stays the guarantee.
- **An LLM gateway (LiteLLM, agentgateway).** A gateway only sees HTTP calls made with an API key. It cannot see
  subscription CLI calls such as `claude -p`, which is where this deployment's model use goes. `provider_openai`
  already takes a `base_url`, so pointing a metered provider at an OpenAI-compatible gateway is a settings change,
  not code. The judgment points declared in `llm.py`, the owner's `metered_models` opt-in and the usage record stay
  agent-hub's.
- **Importing Claude Code's auto memory into `.agent-memory`.** Auto memory is the tool's notes to itself, it is
  Claude-only, and it overlaps with what the project's agent already writes. The value is low.
- **`/insights` into the weekly reflection.** The installed CLI's help lists no non-interactive form of it (not tried with
  `-p`), and the weekly reflection already computes its own statistics with zero LLM.

## Rejected

- **A GUI with diff review, inline comments and previews.** This is what the orchestrators (Conductor, Paseo,
  Emdash, Paradev) and the vendors' own desktop apps compete on. agent-hub runs underneath any of them; the status page
  and the lease relay to the phone are enough.
- **Building semantic or vector search over memory.** Memory is plain Markdown in git, so a Markdown-compatible MCP
  server (for example Basic Memory) can index it without agent-hub owning a vector store.
- **Capturing every session automatically through hooks (claude-mem style).** A memory note is what the agent chose
  to record, and the memory-gaps job already reports projects whose notes fall behind. The weekly reflection
  deliberately sends only statistics, never conversation text, to a model; mining whole transcripts into notes would
  undo that.
- **Windows.** No deployment needs it, and both sandbox backends are POSIX.
