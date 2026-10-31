# Projects and repos: one general model, many combinations

> agent-hub session · 2026-10-31 · r4. Owner's ask: a general architecture in which "one project = one git repo" is only
> one case, so that a monorepo holding many projects, a project spread over several repos, and a project with no repo
> at all follow from the same logic, not from special cases. Owner's rule for building it: **the final architecture
> first, no workarounds** — nothing built in a form a later step replaces. **Design only; nothing is implemented.**
> The tests that build on this model are in `test-waiting.md` (its section 4, "the test system").
>
> History: r1 the model. r2 after a first blind review (four reviewers). r3 after a second blind review (three
> reviewers: code facts, architecture, a hunt for workarounds) and four owner decisions (the owner had no preference
> and left them to the proposals, 10-31): a shared repo names a root owner; **a repo is one trust domain**, so security
> settings live in the repo; memory is one clone per project outside any repo, linked into its folders; one deployment
> clone per repo. Section 9 lists both reviews' findings and what each changed.
> r4 (same day): the owner's decisions on tests, relayed from the agent-hub i18n session — project v2 with a top-level
> `tests` list, one result cache per repo, tests in an exported tree (`test-waiting.md` B1, B3, B4). Section 3 follows
> them.

## 1. The problem

Today three things are one: a **project** (name, mailbox, memory, STATE.md, privacy, secrets, services), a **git repo**
(main, worktrees, commit hooks, origin) and a **folder** (`paths.workspace`, which is the repo's root). Memory is an
orphan branch `agent-memory` inside that repo, checked out as the `.agent-memory` worktree (agent-hub itself is the
exception: `memory: false`, a plain local folder).

What that rules out:

- **A monorepo with several projects.** A second project in the same repo cannot be onboarded: the `agent-memory`
  branch name is per repo. `merge-branch` finds its project by the repo root, so a project in a subfolder is never
  found.
- **A project spread over several repos**: two registry entries, two mailboxes, two STATE.md for one thing.
- **A project with no code**: onboarding runs `git init` anyway, because memory needs a repo to branch from.

How deep the assumption goes (counted in `bin/` on 10-31): 26 files read a project's folder (`hubconf.workspace()`,
`paths.workspace`, `registry.dirs()["workspace"]`) and mostly treat it as the repo root — agenthub, backup, cap-runner,
check-memory-gaps, check-secrets, contracts-check, guard, hub-run, hubbus, hubconf, hublib, landlock, lease-runner,
mcp_server, merge-branch, policy, project-rename, propose_apply, registry, render-project-settings, render-services,
sandboxed-run, seatbelt, sync-local-setup, upgrade-idea, worktrees; 26 files plus three templates touch `.agent-memory`
as a worktree of the code repo; 11 derive the project from git (`--show-toplevel`, `--git-common-dir`). `hubbus`'s
`project_of()` already walks parent folders but tries the repo root first, so a root project would win over a nested one.

## 2. The model

### 2.1 Three concepts

- **Repo**: a git repository on the hub machine, identified by its main checkout. It owns what git owns — main, the
  merge lock, worktrees, git hooks, the origin, pushing, mirroring — and, because everything in it shares one `.git`
  (hooks, config) that runs code in every session working in it, **it is one trust domain**: the sandbox, network and
  privacy settings are the repo's.
- **Project**: an identity: name, mailbox, memory, STATE.md and to-dos, owner-tagged tasks, secrets, services and jobs,
  statistics, its tests.
- **Scope**: what connects them. A project has one or more scopes; a scope is a **(repo, path)** pair or, for a project
  without a repo, a plain **folder**.

"One project = one repo" is the case of exactly one scope, `(R, /)`.

### 2.2 Rules

1. **Every path in a repo belongs to exactly one project: the one whose scope is the longest prefix of it.** Scopes may
   nest; two projects may not have the same scope. A repo with more than one project names a **root owner** whose
   scope is `/`, so no path is unowned.
2. **Every platform feature lives at exactly one level** — repo, project or scope (2.3).
3. **Memory belongs to the project.** It is its own repository (`<name>-memory.git`, as today) with one working clone
   outside any code repo, linked into the project's scope folders (section 4).
4. **Only the resolver knows the mapping** (2.4); a ratchet test keeps other code from reading `paths.workspace`,
   `registry.dirs()["workspace"]` or `hubbus`'s own project table.
5. **What a project's tests depend on is computed, not assumed** (section 4, "Which tests a change needs"): the files
   in its scopes **including nested scopes** (test runners collect recursively), the root owner's files, and the scopes
   of the projects in its `depends_on` closure.

### 2.3 Which level each feature lives at

| level | features |
|---|---|
| repo | worktrees and their cleanup; `merge-branch`'s lock, rebase, commit-title check and fast-forward; git hooks (`gate: strict`, the opt-in commit gate); origin, `push_on_merge`, mirroring; backup of unpushed and uncommitted work; the deployment clone; **sandbox (`enforce`), `network`, `allow_write`, `privacy`** |
| project | memory, STATE.md, librarian, to-dos, registrar; mailbox; owner-tagged tasks and questions; secrets; services and jobs; statistics; merge reports; its test declaration |
| scope | which tests a change needs; which project a session or commit counts for; where the memory link and the rules file go; a service's working folder |

### 2.4 The resolver

In `hubconf` (shell scripts get the same answers through `hubconf.py` subcommands, as `roster` does today):

- `scopes(project)` → its `(repo, path)` pairs / folder
- `project_at(path)` → the owning project: through git (the repo from `--git-common-dir`, the path relative to
  `--show-toplevel`), so it works in worktrees; a path inside a memory clone or a folder-only project resolves by its
  folder, as `hubbus` does today
- `projects_in(repo)`, `repo_of(project)` (only when it has exactly one; otherwise an error)
- `affected(repo, base, head)` → the (project, tier) pairs a change needs, defined in `test-waiting.md` section 4 and
  used by every caller that decides what to test

`roster()` keeps working without PyYAML (new machine, the laptop): `paths.workspace` stays **required** and means the
project's primary folder (its first scope's folder); a project inside a shared repo also carries `repo: <name>` in its
`paths:` block, and `roster()` reads `repos/*.yaml` the same line-by-line way, so recovery finds the origin.

## 3. Registry format

Today's files stay valid and mean what they mean now.

**One project, one repo (today's form):** `paths.workspace` = a repo root is shorthand for one scope `(that repo, /)`,
and the repo-level fields in the project file (`paths.origin`, `push_on_merge`, `commits`, `gate`, `enforce`,
`network`, `allow_write`, `privacy`) are that repo's.

**Several projects in one repo:** the repo has its own file in `<repos_dir>` (a new hub.json key, default `repos/` next
to `registry_dir`) holding every repo-level field; project files in that repo may not set them (one source, so no
cross-file equality check — per-file validation could never change such a value).

```yaml
# repos/acme.yaml
v: 1
name: acme
path: ~/workspace/acme            # main checkout on the hub machine
origin: git@github.com:example/acme.git
root: acme-platform               # owns everything no other scope covers, and sessions opened at the root
push_on_merge: false
commits: conventional
gate: strict
enforce: {sandbox: true}
network: [pypi.org]
privacy: {reflection: include}
```

```yaml
# projects/acme-billing.yaml
v: 2
name: acme-billing
paths:
  workspace: ~/workspace/acme/services/billing   # primary folder (required, 2.4)
  repo: acme
scopes:
- repo: acme
  path: services/billing
depends_on: [acme-shared]         # its tests also run when acme-shared changes
code: ["**/*.py", "pyproject.toml", "uv.lock"]
inputs: ["uv.lock"]
tests:                            # project v2, test-waiting.md B1
- {tier: full, run: "uv run pytest -q -n auto", when: [merge, nightly]}
```

**One project over several repos:** `scopes: [{repo: storefront-web}, {repo: storefront-api}]` (each repo in its own
file or in shorthand); its test tiers name the scope they run in.

**A project with no repo:** `paths: {workspace: ~/workspace/field-notes}`, `scopes: [{folder: ~/workspace/field-notes}]`.

**Contracts and validation.** The test fields (`tests`, `code`, `inputs`; `runtime` keeps only `deploy`) come first, as
**project v2** (test-waiting B1, the owner's decision): renaming `runtime.test` bumps the version, and every registry
file is rewritten in one change. `scopes`, `depends_on` and `paths.repo` are later additions to v2 (additive, no
further bump). `repos/` is a new contract `repo.v1` in `contracts/README.md`; `repos_dir`
goes into `contracts/hub.v1.schema.json` first; `repos` joins the config paths in `bin/appliers.py` and `bin/config-check`.
The security fields (`PLATFORM_OWNED`) gain `paths` (whole, since the check compares top-level keys — it decides the
sandbox's folders and is unprotected today) and `scopes`; a project in a shared repo carrying any repo-level field is
invalid. Cross-file rules — two projects with the same scope, a repo both in shorthand and in a repo file, two scopes
resolving to one real path, a project pointing at a missing repo — are checked when a file changes: the file that
introduces the conflict is invalid (it keeps its last valid version), the others keep working.

## 4. How each part of the platform works under the model

**Memory.** Each project's memory is one clone of `<name>-memory.git`, kept in a platform folder outside every code repo
(`<memory_root>/<name>`, a new hub.json key, default in the instance directory), with its remote named `mini` as every
memory script expects. Each of the project's scope folders gets a symlink `.agent-memory` → that clone, so a session in
any of its repos writes notes to the same place, and a refactor that moves or deletes a folder removes only a link.
Code repos ignore the link with the pattern `.agent-memory` (no trailing slash: a symlink is not a directory to git;
today's `.agent-memory/` would not match it). The librarian keeps reading `~/.memory-copies/<name>/`. `git-sync-memory.sh`,
`sync-all-memory.sh` and `memory-nudge.py` work in the clone (their toplevel guard accepts it through `pwd -P`); the
nudge's `git log -- .` runs from each scope folder. The hooks-off setting in `git-sync-memory.sh` (memory commits must
not run a code repo's hooks) becomes unnecessary once no project uses the worktree form and is removed then. Session hooks
sync the project `project_at` names, and the hourly job syncs every clone in `<memory_root>`.

**Backups.** The nightly backup bundles every clone's unpushed commits from `<memory_root>` (one place) and iterates
**repos** (repo files and shorthand) for code repos' unpushed and uncommitted work, instead of projects.

**Migration from today's worktree form** (one script, dry-run then `--apply`, per project), only after recovery,
renaming and onboarding handle both forms. It refuses unless no session is open in the project (`hubconf.cwd_users()`),
the mount has no untracked files, and the memory branch is pushed. Then: clone into `<memory_root>/<name>.new`, verify
its HEAD equals `mini/agent-memory`, remove the worktree, move the clone into place, create the links, delete the code
repo's `agent-memory` ref, its `mini` remote and `refs/remotes/mini/*`, update the exclude pattern. agent-hub's own
plain-folder memory becomes a normal clone the same way. When every project is migrated, the worktree-form code is
removed from onboarding, recovery, renaming, `worktree-memory.sh` and the worktree cleanup.

**Rules files and hooks.** Written in each scope folder. Whether a coding agent reads a rules file in a subfolder of
where it started, where it reads hook settings from, and whether its worktree mode can open a worktree at a subfolder,
are runtime capabilities (`runtime_ports`), answered by each adapter; where one is missing, doctor's feature table says
so and the platform writes at the repo root, one marked section per project.

**Which project a session belongs to.** `project_at(the session's folder)`; at a shared repo's root, the root owner.
Since a repo is one trust domain with one privacy setting, a root session working in an inner project's files cannot
leak anything the repo's setting does not already allow.

**Sandbox.** A repo is one trust domain: a session of any project in a repo may read and write the whole repo, its
`.git` (common dir and worktree gitdirs, which `git commit`, `merge-branch`'s lock and `.git/info/exclude` need) and its
deployment clone, plus what it may today (runtime, bus, caches, `allow_write`); every **other** repo and folder-only
project stays denied, as today. Within a repo there is no write fence between projects — it would be cosmetic, since any
of them can install a git hook that runs in the others' sessions. The platform's own Seatbelt/Landlock layer
(`seatbelt.py`, `landlock.py`) derives the same set from the resolver, so the repo root of an inner project's service is
readable.

**merge-branch** (a repo operation): lock → rebase → commit titles → `affected(repo, main, head)` → for each pair, a
recorded pass for its key or a run (the test system, test-waiting section 4) → fast-forward → one `merge.done` /
`session.report` per affected project (the bus contract keeps one `project` per event). A failure blocks the merge; the
passes of the other pairs are still recorded. A branch that deletes or empties a registered scope fails until the
registry change lands first (otherwise the moved project's tests would be "not applicable" and the merge would pass
untested). A test in one repo that depends on another repo is caught only by the nightly run. `--project` no longer
chooses whose tests run; it only names the repo when ambiguous.

**Commit gate and git hooks.** `gate: strict` (a main-protection hook the platform already installs) is repo-level and
installed in the repo's `.git/hooks` once per repo. The test commit gate stays opt-in per repo (today: agent-hub); the
platform does not install test hooks into project repos, many of which have their own; for them `merge-branch` is the
gate.

**Services and deployment.** One deployment clone per repo, `<services_root>/<repo>`; a project's runtime folder is its
scope path inside it, and its `deploy.sh` lives there (the root owner's at the root). One pull deploys the repo; each
project's services restart from their folders. For a one-repo project the path is today's `<services_root>/<name>`.

**Onboarding, recovery, renaming.**

- `onboard-project.sh <folder> [--name N]`: a repo root not yet registered → today's behaviour (repo + project);
  a folder inside a registered repo → a new project with that scope (no `git init`, no new GitHub repo, its memory clone
  and link); a folder outside any repo with `--no-repo` → a folder-only project. Adding a second project to a repo
  registered in shorthand **promotes** it: the script writes the repo file, moves the repo-level fields out of the
  first project's file, makes it the root owner, and commits both with `commit-hub-files.sh` (refusing if that file
  has uncommitted changes).
- `recover-project.sh` restores the repo (from the repo file's origin) first, then the memory clone, then the links.
- Renaming a project renames its memory repo, clone and links; renaming a repo is its own command. A `scopes` change
  that moves a folder records the old path for statistics, as renames do today (`legacy-paths.tsv`).

**Statistics.** A project's commits are those touching its scopes (`git log -- <paths>`); its sessions are those
`project_at` assigns to it, from the session folder the runtime's transcript port reports.

**Idea sandboxes** could become folder-only projects, with a repo only when upgraded — not part of this change.

## 5. The combinations, from the same rules

| situation | scopes | what follows |
|---|---|---|
| one repo, one project (every project today) | `(R, /)` | identical to today, except memory as a linked clone |
| monorepo, N projects | root owner `(R, /)` + `(R, a)`, `(R, b)`, … | own memory, mailbox, STATE, tests per project; one lock, one main, one trust domain |
| monorepo as one project | `(R, /)` | today's behaviour; the repo's own tools (Nx, Bazel) can be its `full` tier |
| one project, several repos | `(R1, /)`, `(R2, /)` | one memory (linked in both), one mailbox; each repo merges on its own |
| project with no code | `folder` | memory, mailbox, to-dos, jobs; merge gate and tests "not supported", said explicitly |
| project nested in another's repo | `(R, /)` + `(R, tools/x)` | the inner one owns `tools/x`; the outer one's tests depend on it (rule 5) |

## 6. Costs and risks

- **One refactor of identity.** About 50 files move onto the resolver; the risky ones are `merge-branch`, the sandbox
  policy and its two enforcement layers, `sync-local-setup.sh`, the backup and the memory migration.
- **Memory migration** touches every project's notes: it refuses unless safe, clones beside before swapping, and runs
  only after backups cover clones and recovery/renaming handle both forms.
- **A repo is one trust domain.** Projects in one repo cannot be sandboxed from each other; that is a property of git
  (shared hooks and config), now stated instead of pretended otherwise.
- **Tool-dependent parts** (rules files in subfolders, hook settings, worktrees at a subfolder) are only as good as the
  coding agent allows; missing capabilities are reported.

## 7. Order of work

0. **Prerequisite:** agent-hub works in worktrees (`test-waiting.md` A1), so this refactor never edits the running
   platform's checkout. Steps 2–3 happen in a worktree and merge only with the step 1 tests green.
1. Characterization tests of today's single-repo behaviour: onboarding, recovery, renaming, `merge-branch`, `project_of`,
   `sync-local-setup.sh`, `worktree-memory.sh`, the sandbox policy and both enforcement layers, backup.
2. The resolver and the registry: `scopes`, `depends_on`, `paths.repo`, `repos/`, repo-level security fields, the
   cross-file rules, the contract work (section 3). Every existing registry file gives the same answers as today (test).
   Project v2's test fields land earlier (test-waiting section 7, step 1) and are unchanged by this step.
3. Move the files in section 1 onto the resolver, the step 1 tests staying green; the sandbox derives from repos.
4. The test system's selection and cache on the resolver (test-waiting section 7, step 4).
5. Memory: backups of `<memory_root>`; recovery, renaming and onboarding handle both forms; the migration; agent-hub's
   memory; then removing the worktree form.
6. Backups and deployment per repo; runtime capabilities for nested rules files, hook settings and subfolder worktrees.
7. Onboarding and recovery of subfolder and folder-only projects; repo promotion.
8. End to end: a throwaway monorepo (root owner, two projects, one depending on the other) through onboarding, a
   session, a merge touching both, a deploy and the nightly run, in its own config copy and bus.

## 8. Decided

- 10-31, owner: final architecture first, no workarounds; the test system is built on this model.
- 10-31, owner had no preference; the proposals stand: a shared repo names a root owner; repo-level security settings
  (one trust domain); one memory clone per project outside the repos, linked in; one deployment clone per repo.
- Open, outside this change: whether idea sandboxes become folder-only projects.

## 9. Blind reviews: findings and what each changed

Two rounds; reviewers independent, each with one angle, all Claude models (no other provider is configured, so their
errors may be correlated). Findings are the union; each was checked against the source before it changed the design.

**Round 1 (r2):** nested scopes broke the sandbox and let the weakest setting win → repo as one trust domain (r3);
r1's cache key contradicted itself → replaced in r3 (below); clones dropped unpushed notes from backups → backups of
`<memory_root>`; migration lacked preconditions → section 4; contract work larger than "adding fields" → section 3;
worktree sessions attributed to the root owner → resolution through git; promotion of a shorthand repo → onboarding;
multi-repo tests undefined → tiers name their scope, cross-repo only nightly; a commit gate in every repo → opt-in;
recovery without PyYAML → `paths.repo` and `roster()` reading `repos/`; refactoring the live checkout → step 0;
moved/deleted scope folders → merge fails until the registry changes; minor wording.

**Round 2 (r3):**

| finding | by | change |
|---|---|---|
| The test declaration was undefined and the two documents contradicted each other (a mapping under `runtime:`) | all three | a top-level `tests` list, independent of `runtime`, in project v2 (test-waiting B1; owner's decision) |
| Residual hashes let an ancestor reuse a pass although its runner collects nested tests | architecture | rule 5: an ancestor's tests depend on nested scopes |
| Two caches and three "what counts as code" rules | workarounds | one result store, one `affected()` (test-waiting section 4) |
| Open decisions blocked step 2 | workarounds | decided (section 8) |
| Inner projects could not read their repo root (Seatbelt) or write `.git` (commit, merge lock, exclude) | architecture | repo as one trust domain; both enforcement layers derive from repos |
| `depends_on` targets unreadable inside a sibling's sandbox | architecture | no fence inside a repo |
| Cross-file equality of security fields cannot change under per-file validation | architecture | security fields only in the repo |
| A moved scope folder let a merge pass untested | architecture | the merge fails until the registry changes |
| A multi-repo project had a memory mount in one repo only | architecture | links in every scope folder |
| Migration before recovery/renaming knew clones; remove-then-clone could leave no mount; leftover `mini` remote | architecture | step 5 order; clone beside, then swap; remotes removed |
| Backups iterate projects, missing inner projects and second repos | architecture | backups iterate repos |
| Per-repo deployment clones made a project deny its own runtime | architecture | runtime = scope path in the repo's clone, inside the trust domain |
| "The platform installs no git hooks in project repos" was wrong (`gate: strict`) | facts, architecture | section 4 "Commit gate and git hooks" |
| Inventory missed `landlock.py`, `render-services.py`, the 11th git-derived file | facts | section 1 |
| The exclude pattern `.agent-memory/` would not match a symlink | (found while revising) | section 4 "Memory" |
| Stale header and table, `former_paths`, `git rev-parse <commit>:/`, repo-level events | facts, architecture | fixed or removed |

Checked and found sound by the reviewers: keeping `paths.workspace` required; per-git-dir locks; `.agent-memory` toplevel
guards accepting a clone; `propose_apply.py` working unchanged on a clone; the hub never becoming origin of a project's
main; the librarian's memory copies untouched; "tests before rewrite"; the end-to-end step.
