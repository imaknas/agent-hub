# Projects and repos: one general model, many combinations

> agent-hub session · 2026-10-31 · r2. Owner's ask: a general architecture in which "one project = one git repo" is only
> one case, so that a monorepo holding many projects, a project spread over several repos, and a project with no repo
> at all all follow from the same logic, not from special cases. **Design only; nothing is implemented.**
> Related: `test-waiting.md`.
>
> r2: a blind review (four independent reviewers, Claude models of different sizes; findings checked against the
> source) found the r1 model sound in shape but wrong or silent on sandboxing nested scopes, the cache key, memory
> backups and migration, worktree sessions, recovery and contract work. Corrections are made in place; section 9 lists
> every finding and what it changed. Whether to start this refactor before the test-waiting work is reopened (8).

## 1. The problem

Today three things are one: a **project** (name, mailbox, memory, STATE.md, privacy, secrets, services), a **git repo**
(main, worktrees, commit hooks, origin) and a **folder** (`paths.workspace`, which is the repo's root). Memory is an
orphan branch `agent-memory` inside that repo, checked out as the `.agent-memory` worktree.

What that rules out:

- **A monorepo with several projects.** A second project in the same repo cannot be onboarded: the `agent-memory`
  branch name is per repo, so onboarding stops at "the agent-memory branch exists". `merge-branch` finds its project by
  the repo root, so a project in a subfolder is never found. The whole monorepo can only be one project.
- **A project spread over several repos** (e.g. a frontend repo and a backend repo): two registry entries, two
  mailboxes, two STATE.md for what the owner thinks of as one thing.
- **A project with no code** (research, operations, an idea): onboarding runs `git init` anyway, because memory needs a
  repo to branch from.

How deep the assumption goes (counted in `bin/`, 10-31):

- 24 files read a project's folder (`hubconf.workspace()`, `paths.workspace`) and mostly treat it as the repo root:
  agenthub, backup, cap-runner, check-memory-gaps, check-secrets, contracts-check, guard, hub-run, hubbus, hubconf,
  hublib, lease-runner, mcp_server, merge-branch, policy, project-rename, propose_apply, registry,
  render-project-settings, sandboxed-run, seatbelt, sync-local-setup, upgrade-idea, worktrees.
- 26 files (plus three templates) touch `.agent-memory` as a worktree of the code repo: backup, check-memory-gaps,
  check-secrets, create-idea-sandbox, evals/e3, git-sync-memory, hub-event, hubbus, lease-runner, mcp_server,
  memory-nudge, merge-hook-settings, migrate-to-mini, minilib, onboard-project, openclaw-session-backup, overview,
  propose_apply, recover-project, rename-project, run-agent-job, runtime_ports, sync-all-memory, sync-local-setup,
  worktree-memory, worktrees.
- 10 files derive the project from git (`--show-toplevel`, `--git-common-dir`): cap-runner, guard, hub-event, hubbus,
  lease-runner, merge-branch, migrate-to-mini, sync-local-setup, test-gate, worktree-memory.

There is a start: `hubbus`'s `project_of()` already walks up parent folders. But it tries the repo root first and the
session's folder second, so with a project at the root and another in a subfolder, the root wins — the opposite of
what this model needs.

## 2. The model

### 2.1 Three concepts

- **Repo**: a git repository on the hub machine, identified by its main checkout. It owns what git owns: main, the
  merge lock, worktrees, commit hooks, the origin and pushing, mirroring.
- **Project**: an identity. It owns what a person or the assistant talks about: name, mailbox address, memory,
  STATE.md and its to-dos, owner-tagged tasks, privacy labels, secrets, services and jobs, reflection statistics.
- **Scope**: what connects them. A project has zero or more scopes; a scope is a **(repo, path)** pair — "the files
  under `path` in that repo" — or, for a project without a repo, a plain **folder**.

"One project = one repo" is the case of exactly one scope, `(R, /)`.

### 2.2 Rules

1. **A path belongs to at most one project: the one whose scope is the longest prefix of it.** Scopes in the same repo
   may nest (a project at `/` and one at `pkg/a`): `pkg/a/x.py` is the second's, `README.md` the first's. Two projects
   may not have the same scope. Paths no scope covers belong to nobody.
2. **Every platform feature lives at exactly one level** — repo, project or scope (table below). A feature never asks
   "what is this project's repo" without handling zero or several.
3. **Memory belongs to the project, never to a repo.** It is its own repository (it already is: `<name>-memory.git` on
   the hub machine); where it shows up in a folder is only a mount point.
4. **Only `hubconf` knows the mapping.** Everything else asks it (2.4); a ratchet test keeps new code from reading
   `paths.workspace` directly.
5. **A shared repo's root owner is a dependency of every project in that repo.** Files at the root (a lockfile, root
   config) are owned by one project but used by all, so a change there runs every project's tests in the repo, not
   only the owner's. Without this rule such a change would be tested only by the owner and the nightly full run.

### 2.3 Which level each feature lives at

| level | features |
|---|---|
| repo | worktrees and their nightly cleanup; `merge-branch`'s lock, rebase, commit-title check and fast-forward; the commit gate; origin, `push_on_merge`, mirror backup; backup of uncommitted/unpushed work; the status page's worktree list; the `gate: strict` setting |
| project | memory, STATE.md, the librarian, to-dos, the registrar; mailbox; owner-tagged tasks and questions; privacy; secrets; services and jobs; reflection and memory-gap statistics; merge reports (`merge.done`, `session.report`) |
| scope | which tests a change needs; which project a session or a commit counts for; the sandbox's write paths; where the rules file and the memory mount go |

### 2.4 The resolver

In `hubconf`, the only code that reads scopes:

- `scopes(project)` → list of `(repo, path)` / folder
- `project_at(path)` → the project owning that path (rule 1), or None; resolved through git (repo + path relative to
  its root), so it works in worktrees (section 4)
- `projects_in(repo)` → projects with a scope in that repo
- `projects_touched(repo, changed_paths)` → owners of the changed paths, plus every project whose `depends_on` closure
  reaches one of them (transitively), plus every project in the repo when a root-owned file changed (rule 5)
- `repo_of(project)` → its repo **only when it has exactly one**; otherwise an error, so callers that assumed one find
  out

`roster()` must keep working without PyYAML (new machine, the laptop). It reads only the `paths:` block, line by line,
so `paths.workspace` stays **required for every project** and means "the folder where its memory is mounted" (its
first scope's folder, or its folder). `scopes` refines it and is read only by the validated registry. For a project inside a
shared repo, recovery also needs the repo's origin: its `paths:` block carries `repo: <name>`, and `roster()` reads
`repos/` the same line-by-line way (section 4).

## 3. Registry format

Backward compatible: today's files stay valid and mean exactly what they mean now.

**One project, one repo (today's form, unchanged):** `paths.workspace` = a repo root is shorthand for one scope
`(that repo, /)`; the repo-level fields in the project file (`paths.origin`, `push_on_merge`, `commits`, `gate`) are that
repo's settings.

**Several projects in one repo:** the repo gets its own file, `<repos_dir>/<name>.yaml` (a new hub.json key, default
a `repos/` folder next to `registry_dir`), holding the repo-level fields; projects point into it.

```yaml
# repos/acme.yaml
v: 1
name: acme
path: ~/workspace/acme          # main checkout on the hub machine
origin: git@github.com:example/acme.git
push_on_merge: false
commits: conventional
root: acme-platform             # who owns paths no other scope covers, and sessions opened at the root
```

```yaml
# projects/acme-billing.yaml
v: 1
name: acme-billing
paths:
  workspace: ~/workspace/acme/services/billing   # where its memory is mounted (required, see 2.4)
scopes:
- repo: acme
  path: services/billing
depends_on: [acme-shared]       # changes there also run this project's tests
runtime:
  test: {full: "uv run pytest -q", e2e: {cmd: "...", paths: ["web/**"]}}   # cwd = the scope's folder
```

**One project over several repos:**

```yaml
scopes:
- repo: storefront-web          # repos/storefront-web.yaml, or a project-style shorthand
- repo: storefront-api
```

**A project with no repo:**

```yaml
paths:
  workspace: ~/workspace/field-notes
scopes:
- folder: ~/workspace/field-notes
```

Contracts (corrected r2; more work than r1 said): `scopes` and `depends_on` are new fields, so they must be added to
the registry's known top-level fields (`registry.py` rejects unknown ones) and to `contracts/project.v1.schema.json`
(`additionalProperties: false`); additive, so project stays v1. `repos/` is a new contract `repo.v1` (listed in
`contracts/README.md`); `repos_dir` goes into `contracts/hub.v1.schema.json` first (config-check rejects unknown hub.json
keys), and `repos/` joins the config pipeline beside `registry_dir` (config-check, `commit-hub-files.sh`,
`bin/appliers.py`, backup). `scopes` sets the sandbox's paths, so it joins the security fields `manifest.update` cannot
change (`PLATFORM_OWNED` in `registry.py` and its header) — and so must `paths.workspace`, which already decides the
sandbox's folder today but is not protected (a gap that exists now, independent of this design).

## 4. How each part of the platform works under the model

**Memory.** Each project's memory is a clone of its own `<name>-memory.git`, mounted at `<first scope's folder>/
.agent-memory` (a folder-only project: inside its folder). A clone instead of a worktree of the code repo, so any
number of projects fit in one repo and a project needs no code repo at all. The code repo ignores the mount through its
`.git/info/exclude` (one file per repo, one line per mount). The librarian is unaffected (it reads
`~/.memory-copies/<name>/`, already per project). `git-sync-memory.sh` and `sync-all-memory.sh` commit and push in a git
repo either way; today they switch off the project's git hooks for memory commits because the worktree shares the code
repo's `core.hooksPath` — a separate clone has no such sharing, so that workaround becomes unnecessary (kept, harmless).
`memory-nudge.py` compares the latest note with the project-work commits since it (`git log -- .` from the project's
root); under the model the pathspec becomes the project's scopes. `worktree-memory.sh` links a worktree's scope folders to the main
checkout's mounts.

**Backups (r2).** Today the nightly backup bundles each workspace's unpushed branches, which includes the
`agent-memory` branch, so notes committed but not yet pushed are backed up. A separate clone would not be: the backup
looks only at the workspace's `.git` and skips `.agent-memory`. Before any project moves to a clone, the backup bundles
every memory mount's unpushed commits too.

**Migration** from worktree to clone (dry-run, then `--apply`), refusing unless: no session is open in the project
(`hubconf.cwd_users()`, as renaming does); the mount has no untracked files (never `--force`); the memory branch is
pushed. Then: remove the worktree, clone from the bare repo into the same place **with the remote named `mini`** (every
memory script addresses it by that name), delete the leftover `agent-memory` ref in the code repo (onboarding and the
worktree cleanup special-case it), and record the project as migrated. Recovery, renaming and onboarding must handle
both forms until every project is migrated. Content and history are identical.

**Alternative mount (r2, open):** keep each project's clone outside the code repo (a per-project directory beside the
memory copies) and symlink it into the scope folder. A refactor that moves or deletes the scope folder then cannot take
unpushed notes with it, and the backup covers one place.

**Rules files and hooks.** Written in each scope's folder. Whether the coding agent reads a rules file in a subfolder
of where the session started, and where it reads hook settings from, differs by tool: new capabilities in
`runtime_ports` (e.g. `nested_rules`), answered by each runtime adapter. Where a tool cannot do it, the platform says
so (doctor's feature table) and writes at the repo root, one marked section per project. Hooks that deliver mail ask
`project_at(cwd)`, so they need no per-project copy.

**Which project a session belongs to.** Resolved through git, not the disk path: the repo from `--git-common-dir`,
then the session folder's path relative to `--show-toplevel` matched against the repo's scopes (a worktree lives at
`<repo>/.claude/worktrees/<n>/…`, so a disk-prefix match would give every worktree session to the root owner). A plain
disk-path match is only for folder-only projects. A session opened at the root of a shared repo is the root owner's.
Session hooks pull and sync every memory mount in the repo (idempotent, locked per mount), not only the one under the
session's folder. Privacy cannot rely on this attribution: a root session can work in an inner project's files all
afternoon (see the sandbox paragraph for the rule that makes this harmless).

**Mailbox, STATE.md, to-dos, registrar, librarian.** Per project, unchanged.

**merge-branch** (a repo operation): lock the repo → rebase → commit titles (the repo's setting) → the projects touched
by the branch's changed files (`projects_touched`, which includes every project in the repo when a root-owned file
changed, rule 5) → each one's test tiers, run in the worktree with that project's
scope folder as the working directory, each with its own cache key (below) → fast-forward → one `merge.done` /
`session.report` per touched project, plus one for the repo. A shared repo always has a root owner, so every file has
an owner. When one touched project fails, the others' passes are still recorded under their keys. A change across two
repos is two merges, as today; there is no atomic cross-repo merge, and a test in one repo that depends on another repo
is caught only by the nightly run. `merge-branch --project` no longer chooses whose tests run (the changed paths do); it
only names the repo when that is ambiguous.

**Commit gate.** Only in repos that opt in (today: agent-hub). The platform does not install git hooks into project
repos — many have their own (husky, lint-staged), the very conflict the memory scripts work around. Elsewhere,
`merge-branch` is the gate.

**Test cache key** (corrected r2). For each scope, the hash of **its folder minus the scopes nested in it** (the
root owner's is the whole tree minus every other scope; computed from `git ls-tree`, since `git rev-parse <commit>:/`
alone would include everything) — over the project's own scopes, the **closure** of its `depends_on`, and the root
owner's residual (rule 5) — plus the resolved test command and the declared outside inputs. For a single scope at `/`
with no nested scopes that is exactly today's key. With residual hashes, a change in `pkg/b` does not invalidate
`pkg/a`'s pass; r1's version (the root owner's whole-tree hash in every key) would have. Not in any hash: symlink
targets outside the repo, untracked environments (`.venv`, `node_modules`) — run tests in an export or rebuild the
environment from in-tree lockfiles — and imports across scopes that no `depends_on` declares (the nightly run is the
only net for those).

**Sandbox** (corrected r2). Today each project's policy denies reading and writing every other project's folder.
Under nesting that breaks both ways: an inner project would deny itself its own repo root, and the root owner would be
unable to read the inner folders. So:

- **All projects in one repo share the same `enforce`, `network` and `privacy`**; validation rejects a repo where they
  differ. Then a session's attribution inside the repo cannot weaken anyone's enforcement or leak an `exclude`
  project's content, and `scopes` cannot be used to get around the security fields.
- Ancestor and descendant scopes in the same repo are left out of each other's "other projects" deny lists.
- Write paths = the project's scope folders + what it writes today. For the root owner, the inner scopes are denied for
  writing — which today's Claude sandbox cannot express (it has write allows and read denies; a write deny exists only
  as a permission rule). Each runtime adapter answers a capability for it; where it is missing, the platform says so
  (doctor's feature table) and the root owner writes the whole repo.
- Files at the root (a lockfile) belong to the root owner; another project that has to change them lists them in
  `allow_write`, which only the owner can grant.

**Services and runtime.** `<services_root>/<repo>` becomes one deployment clone per repo; each project's services run
with their `cwd` in its scope, and `deploy.sh` lives in the scope (or the repo's root for the root owner). Projects that
are their own repo see no difference.

**Onboarding, recovery, renaming.**

- `onboard-project.sh <folder>`:
  - a repo root not yet registered → today's behaviour (repo + project with one scope);
  - a folder inside a registered repo → a new project with that scope: no `git init`, no new GitHub repo, a new memory
    repo mounted in the folder;
  - a folder outside any repo with `--no-repo` → a folder-only project.
- `recover-project.sh` restores the repo (if missing) and every project's memory mount — after the code checkout, since
  the scope folder comes from it. For an inner project, the repo's origin is in the repo file, which today's PyYAML-free
  `roster()` does not read: the project's `paths:` block carries `repo: <name>`, and `roster()` also reads `repos/`
  line by line.
- Onboarding a second project into a repo registered in shorthand promotes it: the script writes the repo file, moves
  the repo-level fields out of the first project's file, sets `root:` to that project, and commits both with
  `commit-hub-files.sh` (refusing if the first file has uncommitted changes). Validation rejects a repo registered both in
  shorthand and as a repo file.
- Scopes whose folder is moved: a `scopes` change keeps `former_paths`, like renames do for statistics. A scope folder
  missing in a tested commit makes its tests "not applicable", not failed. Two scopes resolving to the same real path
  (symlinks) are rejected.
- Renaming a project no longer renames a repo unless the two are 1:1; renaming a repo is its own command.

**Idea sandboxes** could become folder-only projects, with a repo only when the idea is upgraded — an option, not part
of this change.

**Statistics.** A project's commits are the commits touching its scopes (`git log -- <paths>`); its sessions are those
`project_at` assigns to it.

## 5. The combinations, from the same rules

| situation | scopes | what follows |
|---|---|---|
| one repo, one project (every project today) | `(R, /)` | identical to today |
| monorepo, N projects | `(R, a)`, `(R, b)`, … + a root owner | own memory, mailbox, STATE, tests and cache per project; one merge lock and one main |
| monorepo as one project | `(R, /)` | today's behaviour; the repo's own tools (Nx, Bazel) can be its `full` test |
| one project, several repos | `(R1, /)`, `(R2, /)` | one memory and mailbox; a merge in either repo counts for it |
| project with no code | `folder` | memory, mailbox, to-dos, jobs; merge gate and tests "not supported", said explicitly |
| project nested in another's repo | `(R, /)` + `(R, tools/x)` | the inner one owns `tools/x`, the outer one the rest |

## 6. Costs and risks

- **One refactor of identity, not a feature.** About 50 files go through the resolver instead of reading the path; most
  changes are mechanical, the risky ones are merge-branch, the sandbox policy, sync-local-setup and the memory
  migration.
- **Memory migration** touches every project's `.agent-memory`. A mistake loses notes, so: sync and push before,
  dry-run first, the bare repo stays the source of truth, nightly backup.
- **Ambiguity has to be decided once:** sessions opened at a shared root, root files several projects need, nested
  sandboxes. The rules above decide them; validation rejects a shared repo without a root owner, or with different
  security settings.
- **Tool-dependent parts** (rules files in subfolders, hook settings location) can only be as good as the coding
  agent allows; where it cannot, the platform falls back and says so.
- **The laptop and new machines** read the registry without PyYAML, so `paths.workspace` stays required (2.4) even when `scopes` says more.

## 7. Order of work

Following "tests before rewrite": pin today's behaviour first, then change it.

0. **Prerequisite (r2):** agent-hub works in worktrees (`test-waiting.md` A1). This refactor changes `hubconf.py` and
   ~50 files of the running platform; from the shared checkout every half-saved edit would be live for every hook on
   the machine. Steps 2–3 are done in a worktree and merged only with the step 1 tests green; step 5 runs only from
   merged main.
1. Characterization tests for today's single-repo behaviour: onboarding, `merge-branch`, `project_of` in hubbus,
   sync-local-setup, worktree-memory, the sandbox policy's write paths.
2. Resolver + registry fields (`scopes`, `depends_on`, `repos/`, and the final test declaration from `test-waiting.md`
   B2) and the contract work in section 3, with every existing
   registry file producing the same answers as today (test). Ratchet test: no new direct reads of `paths.workspace`,
   `registry.dirs()["workspace"]` or `hubbus`'s own project table. Code that uses `workspace` both as the repo root and
   as the working folder (`propose_apply.py`, `lease-runner.py`) names the two.
3. Move the inventory in section 1 onto the resolver, file by file, the tests from 1 staying green.
4. `merge-branch` and the commit gate per touched project, with the scope-hash cache. **The test-waiting work (B1 cache,
   B2 tiers) is built here, directly on scopes**, instead of on whole trees and then again.
5. The backup bundles memory mounts; then memory as a clone, the migration script with its refusals, migrate every
   project.
6. Runtime capabilities for rules files in subfolders and hook settings.
7. Onboarding, recovery and renaming for subfolder and folder-only projects.
8. End to end: a throwaway monorepo with a root owner and two projects, one depending on the other, through onboarding,
   a session, a merge touching both, the nightly run (like `bin/evals/e3.py`, in its own config copy and bus).

## 8. To decide

- Must a shared repo name a root owner (the proposal), or may its root belong to nobody?
- Deployment clones per repo (the proposal) or still per project?
- Memory as a clone for every project, including 1:1 ones (one mechanism, the proposal), or keep worktrees there and
  use clones only in shared repos?
- Should idea sandboxes become folder-only projects?
- **Decided 10-31 (owner): final architecture first, no workarounds.** test-waiting's B1/B2 are built on this model
  (step 4), and the final test declaration (tiers, paths, no runtime directory implied) is part of step 2's registry
  fields. The reviewers' alternatives (whole trees now; a project-local path list) are not taken. Order of all steps:
  `test-waiting.md` section 7.
- Mount memory clones inside the scope folder, or outside the repo with a symlink in (section 4)?

## 9. Blind review (r2): findings and what each changed

Four reviewers, each with one angle and without seeing the others: facts against the code (A), measurements (B),
architecture (C), a contrarian (D). Union of findings; each was checked against the source before it changed the
design. Severity is the reviewer's.

| # | finding | by | severity | change |
|---|---|---|---|---|
| 1 | Nested scopes break the sandbox both ways; projects in one repo with different `enforce`/`privacy` let the weakest win; privacy by session folder leaks | C | blocking | Section 4 "Sandbox": same security settings across a repo, ancestor/descendant scopes out of the deny lists, write-deny as a runtime capability |
| 2 | r1's cache key put the root owner's whole-tree hash into every key, contradicting "a change in `pkg/b` does not invalidate `pkg/a`"; also missing the test command, the `depends_on` closure, untracked environments, symlinks | C | blocking | Section 4 "Test cache key": residual folder hashes, closure, command, stated limits |
| 3 | Memory as a clone drops unpushed notes from the nightly backup; migration lacked preconditions (open sessions, untracked files, remote name, leftover ref, half-migrated fleet) | C | blocking | Section 4 "Backups" and "Migration"; step 5 |
| 4 | Contract and config work much larger than "adding fields": registry known fields, schema `additionalProperties: false`, `PLATFORM_OWNED`, `repo.v1`, `repos_dir` in the hub schema, config pipeline; `paths.workspace` already unprotected | A | should fix | Section 3 "Contracts" |
| 5 | Session attribution by disk path sends every worktree session to the root owner; session hooks sync only one mount | C | should fix | Section 4 "Which project a session belongs to" |
| 6 | Onboarding a second project into a shorthand repo means rewriting another project's registry file | C | should fix | Section 4 onboarding: the script promotes the repo |
| 7 | Multi-repo projects and cross-repo `depends_on` undefined for tests | C | should fix | Section 4 merge-branch: nightly run only |
| 8 | A per-repo commit gate would install git hooks into projects that have their own | C | should fix | Section 4 "Commit gate": opt-in |
| 9 | Recovery without PyYAML cannot restore an inner project; mount before checkout | C | should fix | Section 4 recovery |
| 10 | The refactor would run from the live platform checkout | C | should fix | Step 0 |
| 11 | Scope folder moves, deletions, symlinked duplicates undecided; the mount inside the scope can be deleted by a refactor | C | should fix | Section 4 onboarding list; the outside-mount alternative (section 8) |
| 12 | "Files nobody owns" contradicts "a shared repo must have a root owner"; `--project` would choose tests; `workspace` used with two meanings; ratchet too narrow; `memory-nudge` misdescribed | A, C | minor | Fixed in place |
| 13 | The refactor buys nothing for the three measured projects (all one repo each; no registered project nested or shared) and delays measured savings | B, C, D | blocking (D) | Reopened as a decision (section 8; `test-waiting.md` 7) |
| 14 | Simpler model: a project-local path list driving tests and keys only, a subset of scopes | C | minor | Offered as the middle option (section 8) |

Checked and found sound by the reviewers: keeping `paths.workspace` required; the clone removing the shared-hooks
problem; per-git-dir locks for several mounts; the existing `.agent-memory/` exclude pattern matching nested mounts;
the hub never becoming origin of a project's main; the librarian's per-project memory copies; tool-dependent parts as
runtime capabilities; "tests before rewrite" ordering and the end-to-end step.
