# Projects and repos: one general model, many combinations

> agent-hub session · 2026-10-31 · r1. Owner's ask: a general architecture in which "one project = one git repo" is only
> one case, so that a monorepo holding many projects, a project spread over several repos, and a project with no repo
> at all all follow from the same logic, not from special cases. **Design only; nothing is implemented.**
> Related: `test-waiting.md` (its result cache and test tiers should be built on this model from the start, section 7).

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
  STATE.md and its to-dos, 〔owner〕 tasks, privacy labels, secrets, services and jobs, reflection statistics.
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
| project | memory, STATE.md, the librarian, to-dos, the registrar; mailbox; 〔owner〕 tasks and questions; privacy; secrets; services and jobs; reflection and memory-gap statistics; merge reports (`merge.done`, `session.report`) |
| scope | which tests a change needs; which project a session or a commit counts for; the sandbox's write paths; where the rules file and the memory mount go |

### 2.4 The resolver

In `hubconf`, the only code that reads scopes:

- `scopes(project)` → list of `(repo, path)` / folder
- `project_at(path)` → the project owning that path (rule 1), or None
- `projects_in(repo)` → projects with a scope in that repo
- `projects_touched(repo, changed_paths)` → owners of the changed paths, plus projects that declared `depends_on` one
  of them
- `repo_of(project)` → its repo **only when it has exactly one**; otherwise an error, so callers that assumed one find
  out

`roster()` must keep working without PyYAML (new machine, the laptop). It reads only the `paths:` block, line by line,
so `paths.workspace` stays **required for every project** and means "the folder where its memory is mounted" (its
first scope's folder, or its folder). `scopes` refines it and is read only by the validated registry; recovery on a
new machine needs no more than `paths.workspace` and the memory repo.

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

Contracts: `scopes` and `depends_on` are new fields of project v1 (adding fields keeps the version; `paths.workspace`
keeps its meaning as the shorthand). `repos/` is a new contract, `repo.v1`. `scopes` is a security-relevant field (it
sets the sandbox's write paths), so `manifest.update` cannot change it, like `allow_write`.

## 4. How each part of the platform works under the model

**Memory.** Each project's memory is a clone of its own `<name>-memory.git`, mounted at `<first scope's folder>/
.agent-memory` (a folder-only project: inside its folder). A clone instead of a worktree of the code repo, so any
number of projects fit in one repo and a project needs no code repo at all. The code repo ignores the mount through its
`.git/info/exclude` (one file per repo, one line per mount). The librarian is unaffected (it reads
`~/.memory-copies/<name>/`, already per project). `git-sync-memory.sh` and `sync-all-memory.sh` commit and push in a git
repo either way; today they switch off the project's git hooks for memory commits because the worktree shares the code
repo's `core.hooksPath` — a separate clone has no such sharing, so that workaround becomes unnecessary (kept, harmless).
In `memory-nudge.py` the comparison becomes the memory's age against the latest commit **touching
the project's scopes**, not the repo's latest commit. `worktree-memory.sh` links a worktree's scope folders to the main
checkout's mounts.

Existing projects move from worktree to clone with a migration script (dry-run, then `--apply`): sync and push the
memory branch, remove the worktree, clone from the bare repo into the same place. Content and history are identical.

**Rules files and hooks.** Written in each scope's folder. Whether the coding agent reads a rules file in a subfolder
of where the session started, and where it reads hook settings from, differs by tool: new capabilities in
`runtime_ports` (e.g. `nested_rules`), answered by each runtime adapter. Where a tool cannot do it, the platform says
so (doctor's feature table) and writes at the repo root, one marked section per project. Hooks that deliver mail ask
`project_at(cwd)`, so they need no per-project copy.

**Which project a session belongs to.** `project_at(the session's folder)`. A session opened at the root of a shared
repo is the root owner's (`root:` in the repo file); in a repo without one it counts for the repo and shows the mail of
all its projects, each labelled.

**Mailbox, STATE.md, to-dos, registrar, librarian.** Per project, unchanged.

**merge-branch** (a repo operation): lock the repo → rebase → commit titles (the repo's setting) → the projects touched
by the branch's changed files (`projects_touched`, which includes every project in the repo when a root-owned file
changed, rule 5) → each one's test tiers, run in the worktree with that project's
scope folder as the working directory, each with its own cache key (below) → fast-forward → one `merge.done` /
`session.report` per touched project, plus one for the repo. Files nobody owns: the repo file may declare a test of its
own; otherwise nothing runs for them, and the report says so. A change across two repos is two merges, as today; there
is no atomic cross-repo merge.

**Commit gate.** Per repo; the fast tier of the touched projects (`test-waiting.md` A2/B2).

**Test cache key.** The hash of each of the project's scopes in the tested commit — git gives every folder one
(`git rev-parse <commit>:<path>`) — plus its `depends_on` projects' scope hashes, plus the declared outside inputs. For a
scope at `/` that is the whole tree: exactly today's key. A change in `pkg/b` no longer invalidates `pkg/a`'s pass.

**Sandbox.** Write paths = the project's scope folders (instead of the workspace) + what it writes today (runtime, bus,
caches, `allow_write`). Files at a shared repo's root belong to the root owner; another project that has to change
them (a lockfile) lists them in `allow_write`, which only the owner can grant (security field).

**Services and runtime.** `<services_root>/<repo>` becomes one deployment clone per repo; each project's services run
with their `cwd` in its scope, and `deploy.sh` lives in the scope (or the repo's root for the root owner). Projects that
are their own repo see no difference.

**Onboarding, recovery, renaming.**

- `onboard-project.sh <folder>`:
  - a repo root not yet registered → today's behaviour (repo + project with one scope);
  - a folder inside a registered repo → a new project with that scope: no `git init`, no new GitHub repo, a new memory
    repo mounted in the folder;
  - a folder outside any repo with `--no-repo` → a folder-only project.
- `recover-project.sh` restores the repo (if missing) and every project's memory mount.
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
- **Ambiguity has to be decided once:** sessions opened at a shared root, files nobody owns, root files several
  projects need. The rules above decide them; the registry validation rejects a shared repo without a root owner.
- **Tool-dependent parts** (rules files in subfolders, hook settings location) can only be as good as the coding
  agent allows; where it cannot, the platform falls back and says so.
- **The laptop and new machines** read the registry without PyYAML, so `paths.workspace` stays required (2.4) even when `scopes` says more.

## 7. Order of work

Following "tests before rewrite": pin today's behaviour first, then change it.

1. Characterization tests for today's single-repo behaviour: onboarding, `merge-branch`, `project_of` in hubbus,
   sync-local-setup, worktree-memory, the sandbox policy's write paths.
2. Resolver + registry fields (`scopes`, `depends_on`, `repos/`), with every existing registry file producing the same
   answers as today (test). Ratchet test: no new direct reads of `paths.workspace`.
3. Move the inventory in section 1 onto the resolver, file by file, the tests from 1 staying green.
4. `merge-branch` and the commit gate per touched project, with the scope-hash cache. **The test-waiting work (B1 cache,
   B2 tiers) is built here, directly on scopes**, instead of on whole trees and then again.
5. Memory as a clone; the migration script; migrate every project.
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
- ~~Order against test-waiting.md~~ — **decided 10-31: B1/B2 are built on scopes** (step 4 here), after steps 1–3.
