# Direction and what is left

The one place that says where brunna is going and what is still missing. Each design doc holds its own details; this file
holds the decisions that span them, every plan's phases, and what each one still lacks. **When a step lands, a plan
changes or the owner decides a direction, update this file in the same change.** A direction agreed only in a
conversation is lost when that conversation ends.

Checked against the code on 2026-11-13 (main at `c39867ff`). Several design docs' own status lines were behind the code
that day; they are listed at the end.

## The direction (decided)

| Since | Decision | Where it is written |
|---|---|---|
| 10-24 | Tools stay current and never fall behind silently; no unified update flow is wanted | `runtime-versions.md` DV2 |
| 10-25 | Every layer is an interface; Claude Code is one implementation | `runtime-abstraction.md` |
| 10-26 | Code, config, data and secrets are separate; a newcomer's install is the reference | `new-user.md` |
| 10-26 | Specs, open source and articles are pushed together; Principal Handoff is the first spec | `specs-and-open-source.md` |
| 10-26 | An evaluator at every point where a model is called, so models can be swapped on evidence | `evaluators.md` |
| 10-29 | Any coding agent, any LLM, any key: the platform guarantees the result, a tool's features only reinforce it | `tool-agnostic.md`, AGENTS.md "Tool neutrality" |
| 10-29 | The user picks the model and the provider and brings the key | `llm-providers.md` |
| 10-29 | The personal assistant is optional; a minimal owner side is built in | `tool-agnostic.md` "Confirmed" |
| 10-29 | No privacy routing or enforcement until the evaluators can measure it | `llm-providers.md` step 5 |
| 10-29 | Evals first, then translate everything | `i18n.md` |
| 10-31 | One project = one git repo is a special case of a general model; build the final architecture, no workarounds | `projects-and-repos.md` §8 |
| 10-31 | Take what competitors do better | `competitive-gaps.md` |
| 11-01 | Three tiers for agents: the CLI and MCP for all, a per-tool adapter only where it adds something | `mailbox-runtimes.md` |
| 11-01 | Memory across projects comes first in how brunna is described; an always-on machine is optional | `newcomer-path.md` "Positioning" |
| 11-01 | The owner pushes main; users upgrade only to release tags | CONTRIBUTING.md |
| 11-02 | The name is brunna; machines that already exist keep what can't be renamed | `rename-brunna.md` |
| 11-03 | Conversation statistics come from a neutral record of every agent's sessions, not from adapters | AGENTS.md (`bin/sessions.py`) |
| 11-06 | A letter goes first to a session that already has the context | `mailbox-session-first.md` |
| 11-08 | The platform runs its own checked copy of each coding agent; the tool's own auto-update stays on | `runtime-versions.md` DV1–DV5 |
| 11-09 | A project's code asks the platform for model calls and confined agent calls; it never calls a provider itself | `project-agent-calls.md` |
| 11-13 | Every platform run of Claude uses the headless token; the subscription login is only for registry jobs with `connectors: true`, and the hourly login check looks at it only while one exists | `known-issues.md` "Three credentials", `bin/check-claude-login.py` header |

**Order of the coding-agent adapters** (decided across 10-29, 11-01 and 11-08; no other doc has it whole):

1. Claude Code, native: all ports.
2. Any CLI agent through `runtime_command`, and ACP agents through `runtime_acp`: in the throwaway copy only (propose,
   then the platform applies). Proven live with Antigravity CLI and Codex (10-29), and with two ACP agents (11-01).
3. **Codex, a dedicated adapter**: designed and cold-reviewed 11-08. Step 2 (sessions the owner opens) built 11-14; the
   rest after `runtime-versions.md` steps 1–4.
4. Antigravity CLI: a later candidate (11-08). Check what it supports before designing.
5. Every other agent: MCP (the mailbox) and ACP, with no adapter of its own.

**Working rules decided in conversation:**
- Big changes get a blind review of the design before building, and of the code before `merge-branch`.
- The README stays lean; new features go to `docs/` and the release notes.
- Wording never makes the personal assistant sound required.

## What is left

Ordered by what unblocks the most. "Owner" means the step waits for a decision only the owner can make.

### 1. Coding-agent runtimes (the critical path)

1. **`runtime-versions.md` steps 1–4: built and merged (11-14).** The conformance runner and Claude's checks, the store
   of checked copies and `brunna/update-runtimes`, platform runs on the store's copy (this machine: 2.1.323), the
   tool folder locked for services and sessions with its control checks, and every version a session runs checked
   when first seen (`brunna/session-versions`) with a narrow gate. Left: the three pty-driven checks (V-M6). Their
   original outline:
   1. The distribution port, conformance checks per port, the results store, `brunna runtimes`.
   2. The store and `update-runtimes` (promotion, hold-back, rollback); this retires `update-claude`.
   3. Each session's agent version recorded and gated.
   4. One control rule over the tool's own folder.
2. **Codex adapter (`runtime-codex.md`):** step 0 (fail closed) and **step 2** (`bin/runtime_codex.py`: user-level hooks,
   rules by injection, session state; merged 11-14) are built. Order now: Step 2 → Step 4 layer 1 → Step 1 → Step 4
   layer 2 → Step 3. Step 1 is `runtime-versions.md` step 5.
   - **Step 1:** planned and revised after a cold review (r2), then **built 11-14 on its branch** through r2's step 5:
     the platform's own runs on a checked Codex copy in the store, a fresh Codex home per run (updater, hooks, plugins
     and the shell snapshot off; the run's own `HOME`), the tool list checked before and after every run; completions
     and the judgment points that work in a memory copy; nothing starts in a project's folder (B1); no `confine` port.
     The model-free checks pass on 0.169 (B1's probe fails, which holds only sessions). Left: the login list with the
     owner, then a first promoted copy. With Codex only in `runtime.installed` nothing new happens (tested). D11–D14
     are provisional as recommended, the owner's.
   - **Step 3:** wake, status, resume and fork. Wake also waits on M11 (is a client attached), under D4.
   - **Step 4 layer 1** (Codex's own sandbox, rendered by the platform): planned and revised after a cold review (r2,
     11-14, on measurements with the owner's login). **It can't ship as "confined"**: Codex runs `git status` as the user
     in the session's folder, and a filter in the repo's config ran from it. r2's steps 1–2 are built (11-14): `.codex`
     and `.agents` are protected in every sandbox, and a per-version probe (`conformance_codex.py`) runs, whose B1 check
     fails on 0.167. The path to "confined" is D9.
   - **D9, the git config fence** (`git-config-fence.md`, planned 11-14 on measurements): nothing in a sandboxed
     project's sandbox writes git configuration; the platform writes what is needed, and "the fence holds" becomes
     Codex's confined condition.
   - **Git through the platform** (`git-broker.md`, designed 11-14 on measurements, the owner's decision): in a
     sandboxed project the sandbox writes no git dir at all; the `git` on its PATH forwards to a platform broker that
     runs real git under the project's profile (writes to the git dir, no program but git's own) or, for the registry's
     remotes, as the owner. It replaces the fence's per-name list and its narrow runner; the scan stays as a second
     layer. r2 after a blind review (11-14): no process writes both the worktree and the git dir (a symlink race
     otherwise plants files in `.git`), and `safe.bareRepository=explicit` in the owner's git config is a checked
     precondition (set 11-14). Not built; open for the owner: push policy, signing, Codex app worktrees, submodules,
     who switches a project on.
   - **Step 4 layer 2:** `brunna session <project> --agent` under the platform's OS sandbox (with `--no-daemon` for
     Codex, D9's option i). It waits on M10 and Step 1's platform `CODEX_HOME`.

   Measurements still open: `--output-schema` with the platform's own schemas (Step 1's login list; the V-M1 model pin
   is answered: `[agents] enabled = false`),
   M10 and M11, the network proxy, the daemon's socket from a tool shell with the network on, worktree trust,
   `turn_context` timing at UserPromptSubmit, and a sandboxed project's `brunna send` end to end. The Step 2 follow-ups (`TELLS_ON` widening, `current_session` inside Codex's sandbox) are in
   `runtime-codex.md`.
3. **The long-running session port.** This is `runtime-abstraction.md` step 4, `runtime-acp.md` step 4 and
   `tool-agnostic.md`'s postponed "leasing over ACP". The lease runner still builds Claude's flags itself and reads
   Claude's folders. The sandbox and credential questions in `runtime-acp.md` §4 come first.
4. **Leftovers of the runtime abstraction:**
   - the jobs `claude-login` / `update-claude` renamed (the update half goes with `runtime-versions.md` step 2);
   - judgment points declared neutrally, including `platform_tools: [show_file]` (`lease-show-file.md` "later");
   - `lease-resume` moved onto the login port;
   - the runtime-boundary ratchet brought to zero.
5. **ACP follow-ups (round 13):**
   - the propose eval scores `deferred`;
   - doctor warns when the flag for "no configured MCP servers" is missing;
   - a login check for runtimes pinned to points.

### 2. Integrity and confinement

1. **The bus as a platform service with a socket** (`tool-agnostic.md` step 7). Sandboxed projects already write only
   their outbox (`bus-sender.md`), but unsandboxed ones still write the bus file directly, so their letters' senders and
   events are claims (`event-jobs.md` "authenticated bus writes").
2. **capabilities A2** (`capabilities.md` 11-08): a sandboxed session can still read other projects' transcripts and
   memory in the coding agent's folder until A2 lands. It needs a measurement and a port of the agent's folder naming
   first.
3. **Session-level gaps:**
   - The Keychain is blocked for services and capability runs, not for sessions. Loosening work mode's per-command
     prompts waits on that.
   - The git-filter protection in `mailbox-session-first.md` step 3 rests on one tool's environment variable.
4. **Not enforced on either OS:**
   - the outbound domain allowlist (`policy.network`);
   - on Linux, inbound connections limited to localhost, and signals on kernels older than 6.12.
5. **The evaluators** (`evaluators.md`):
   - Step 0 is partly built: MCP sealing and `--restricted` exist. Not built: the per-call init verdict, the seal check
     and going live.
   - Phase 1 (schema fields, `policy.for_point`, the shim, coverage, the evals module) and phase 2 are not built.
   - Recording, replay and scoring exist only through i18n's E2.
   - Privacy routing waits on this.

### 3. Projects and repos (`projects-and-repos.md`)

Steps 1–8 are not built; only step 0 (brunna works in worktrees) and git run as the project are.

1. Characterization tests of today's behavior.
2. The resolver and the registry fields (`scopes`, `depends_on`, `repos/`, `memory_root`).
3. The callers moved onto the resolver.
4. Test selection on the resolver.
5. Memory in `memory_root`.
6. Rules files in nested folders.
7. Onboarding subfolder and folder-only projects.
8. An end-to-end throwaway monorepo.

Waiting on these:
- `test-waiting.md` steps 3, 5 and 6;
- `newcomer-path.md`'s two remaining layouts (a monorepo as several projects, several repos as one);
- idea sandboxes as folder-only projects (owner).

### 4. Tests (`test-waiting.md`)

Built: steps 0–2 and step 4, the waiting report. Left:
- from 2.3: `session.report` v2, lease-handover v2 (another project's request waits on it), and running a failing nightly
  test once more;
- the Spotlight check;
- step 5's `@en` tier and the rest of B6;
- **owner:** B8 (merge queue) and A2. The waiting report they were to be judged on has run since 11-02.

### 5. Smaller items

- **Event jobs:** a receiver for outside sources (CI, webhooks) with its auth; waking the scheduler when an event is
  published.
- **Idea sandboxes:** an archive endpoint for dropped ideas.
- **Mail handler:** one that may change code (`mailbox-routing.md` step 4).
- **Budgets:** per-project model budgets (today there is one platform-wide daily budget).
- **Config pipeline:** an applier for `sync-local-setup` (`new-user.md` 1.5).
- **Rename:** `brunna.v1.schema.json`, and step 4 (writers stop falling back to the old spellings).
- **Principal Handoff:** sending 0.6 waits on the principal side reading it, then on one settings change.
- **LLM providers:** a full nightly run on a local model in an isolated home (`llm-providers.md` step 7).
- **Newcomer path:** the dry run of story 2 in both desktop apps.
- **herdr:** phase 2 (herdr as a session host), only on demand.
- **Two designs never moved here:** the schedule-hardening phase 1 and phase 2 designs (10-21/22: the central registry,
  versioned contracts, enforcement) are still only in untracked files in the hub's state folder. `new-user.md` says they
  were to move to `docs/design/` (after a check for private material).
- **Versioning:** CONTRIBUTING.md says `!` raises the major version; releases before 1.0 have raised only the minor.
  One of the two should change.

### 6. Waiting on the owner

- Codex D3: which runtime is the default.
- Codex Step 1 (`runtime-codex.md`, the plan and its r2), provisional as recommended until the owner says otherwise:
  - D11: a Codex copy is installed, and the owner's ChatGPT login used, only once Codex runs platform work;
  - D12: the owner's npm `codex` raised to the promoted version, while Codex runs platform work;
  - D13: a sessions-only safety fail (B1's probe) doesn't hold back the platform's copy; re-decided before Step 3 or
    when the git-config fence lands;
  - D14: a login refresh due during an ordinary run is allowed under a grace kill, unless the login measurement says
    otherwise.
- Codex Step 4 (`runtime-codex.md`, the plan and its r2). D5–D8 were decided by the owner 11-14:
  - D5: trust entries through `brunna user-settings --trust`, and Codex's own prompt checked;
  - D6: an overridden session isn't served;
  - D7: `never`;
  - D8: network on (the project's domains where the proxy can name them, else full and said).

  Still open:
  - **D9**: the path to a confined Codex session (layer 2 with `--no-daemon`, a fence on git config, or wait for Codex).
  - **D10**: whether r2's steps 3–5 are built now as hardening. Provisionally: steps 1–2 built 11-14, steps 3–5 wait for
    D9.
- test-waiting B8 and A2.
- Positioning: no wording decision is recorded since the README was rewritten.
- Principal Handoff: whether to send the drafted ACP RFD.
- nono: whether to report its missing port restriction upstream.
- Credentials in a password manager: only if the owner wants one.
- Whether to rewrite the owner's name out of code comments. The name baseline isn't zero, and `specs-and-open-source.md`
  lists zero as an open-source prerequisite.
- llm-providers: the switch recommendations from shadow mode, and decision 3 (where memory notes come from for other
  agents).

### 7. Not verified on a real run

- Project agent calls from a sandboxed project, at the daily cap, and on timeout. The unsandboxed path ran 11-13.
- herdr in the Linux container: the rules block and letter delivery. Only the macOS hook was checked.
- A first real `upgrade-idea --apply`.
- The live test of `show_file` with an idea sandbox.
- capabilities' 11-08 A1 check in a session the owner opens.
- `mailbox-session-first.md` step 4's systemd `KillMode` change, which was read, not run.
- Whether a mailbox fork shows in the desktop app's session list.

## Design docs whose status line was behind the code (11-13)

All twelve were corrected the same day; the list stays as a record of how far behind they had fallen.

- **`newcomer-path.md`:** says "nothing implemented"; B0–B8 are built.
- **`new-user.md`:** says "design only"; steps 0–7 are built.
- **`rename-brunna.md`:** says "nothing built yet"; v0.6.0, v0.6.1 and v0.7.0 shipped it. Its open question to the
  assistant was answered 11-02.
- **`principal-handoff-0.3-migration.md`:** calls itself a proposal; all four phases are done.
- **`runtime-codex.md`:** says "not built"; step 0 is. Its "where it stands" still describes the preset as
  `runtime.default: command`.
- **`runtime-abstraction.md`:** the 10-25 progress note lists `templates/CLAUDE.md` as not renamed; it is. Layer 5 has
  since moved to `bin/sessions.py`.
- **`herdr.md`:** the real run in the app and the topic are done.
- **`idea-sandboxes.md`:** phase two (work mode) is built.
- **`project-agent-calls.md`:** step 5 (telling the project) is done.
- **`test-waiting.md`:** omits 2.4's container module and 2.6's rules, which are built. It marks 2.3 done, but parts are
  not.
- **`evaluators.md`:** promises building after a last review. Parts of step 0 were built another way since.
- **`tool-agnostic.md`:** "postponed: conversation-log parsers for other tools" is out of date (`sessions_codex.py`).
