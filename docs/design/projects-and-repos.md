# Projects and repos: one general model, many combinations

> agent-hub session · 2026-10-31, r11 2026-11-01. Owner's ask: a general architecture in which "one project = one git repo" is only
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
> r5: after a third blind review: memory clones join the sandbox's paths (own clone writable, others' denied); the
> worktree marker for inner projects; identity from the session-start hook; exclude before link; neutral remote name;
> folder-only and multi-repo projects' security fields; backup and deployment per repo at step 3.
> r6 (11-01): after a fourth blind review, with `test-waiting.md` r7: **the platform never runs git in a
> project-writable git dir with more than the project's authority** (section 4, "Platform git in project repos") —
> found because a memory clone's `.git` is project-writable while the hourly sync runs git there unsandboxed, a hole
> that exists today in the worktree form; other projects' memory bare repos denied; a nested scope's tests also
> depend on its ancestors' own files; deploy scripts run in the main checkout; the worktree marker's writer; one
> remote name; a deterministic rule for cross-file conflicts.
> r7 (11-01): after a fifth review: the platform-git rule restated by who runs git (the platform only in repos it
> owns; in a project's repo only as the project, handing back bundles and facts), after tests showed read-only
> commands (`git log`) running project-named programs and a fetch serving another project's objects; every call site
> listed; memory copies without symlinks and denied whole. Several of these holes exist today; their fix is a
> separate change, proposed 11-01, to be done before this design's steps.
> r8 (11-01): a sixth review refuted parts of r7's rule (a bundle handed over as a file can be a symlink to another
> project's bundle; the resolver itself would run git in project repos; "Inert" and the call-site list were wrong) and
> found more holes in today's code. Marked open in section 4 and listed in section 9; not settled.
> r9 (11-01, after the sandbox fix landed): the security claim scoped to "adds no path beyond the sandbox"; the
> resolver starts no git (pointer files, by path); hand-over on stdout, as `project_git.py` built it; a project in
> several repos needs the same security fields in all of them.
> r10 (11-01): after a seventh review: the sandbox decision's fallback reads repo files and fails closed (section 3);
> pointer files need git's back-pointer and must agree with the path answer (2.4); step 3's inventory refreshed.
> r11 (11-01): after an eighth review: a pointer never takes the user's authority back — the path answer comes first,
> "none" only outside every registered folder (2.4); the index keeps unloaded projects' folders and `enforce` (3);
> backups take `.env` files as data (4); scope collisions compared lexically, symlinked scope folders invalid (3).

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
sandboxed-run, seatbelt, sync-local-setup, upgrade-idea, worktrees; `hubconf.roster()` hands the same folder to eight
more (overview, onboard-project, recover-project, rename-project, sync-all-memory, gen-index-roster,
check-memory-gaps, hublib); 26 files plus five templates (`agent-instructions.md` and four mailbox prompts) touch
`.agent-memory` as a worktree of the code repo; 11 derive the project from git (`--show-toplevel`, `--git-common-dir`).
Counts exclude `bin/evals/`. `hubbus`'s
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
   `registry.dirs()["workspace"]`, `roster()`'s folder column (whose readers take it as a repo root; `roster()` gains a
   repo column from the resolver) or `hubbus`'s own project table.
5. **What a project's tests depend on is computed, not assumed** (`test-waiting.md` B2 and B3): the files
   in its scopes **including nested scopes** (test runners collect recursively), the own files of every ancestor scope
   (test runners load configuration from parent folders; the root owner's files are the case of `/`), and the scopes
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
- `project_at(path)` → the owning project, **without starting git** (round 6: a project's git config can make even
  `rev-parse` hang or run a program): by path against the registered folders, as `project_git.project_at` already
  does (11-01), and inside a linked worktree by reading its `.git` and `commondir` pointer files as plain files
  (`bin/gitdirs.py`, which the session hooks already use: non-blocking, regular files only), accepted only when they
  lead to a registered repo: `.git` itself is a real directory or a regular file (checked without following links),
  a `.git` file's gitdir resolves to `<a registered repo's common dir>/worktrees/<id>`, and **that** gitdir's
  back-pointer (`gitdir`) names this `.git`. **A pointer never takes the user's authority back (r11):** the path
  answer comes first — the owner of the longest registered folder containing the path; a pointer is used only to
  refine it within the same repo (a worktree inside the repo's folder, or the marker naming an inner project) or,
  where no registered folder contains the path, to find a worktree placed elsewhere; a pointer that fails a check or
  leads to another repo is ignored and the path answer stands. "None" — which means the user's authority — is the
  answer only where no registered folder contains the path and no valid pointer leads to a registered repo (round 8:
  answering "none" for a failed check let a sandboxed project un-sandbox its own folder by writing a bad `.git`
  file). Pointer files are opened without following links, non-blocking, regular files only, up to a size cap; every
  caller that has to find its project before it knows it (`worktree-memory.sh`, `hubbus`, `guard`,
  `sync-local-setup.sh`, the session hooks) uses this one reader. So it works in worktrees. A linked worktree may carry a
  marker naming its project (`<git-dir>/agent-hub-project`), which wins, since tools open worktrees at the repo root.
  The session-start hook writes it, as the project (`project_git.run`, so a planted symlink leads nowhere it could not
  already write), from the folder the session was opened in, which the runtime reports (a `runtime_ports` capability,
  step 6; without it doctor says inner projects' worktree sessions count for the root owner); a marker naming a
  project that has no scope in this repo is ignored and reported. A deployment clone resolves to the repo it deploys (services
  and jobs run there); a path inside a
  memory clone or a folder-only project resolves by its folder, as `hubbus` does today
- the hub repo is the main checkout of the running copy's git common dir, so a worktree of agent-hub is recognized
- `projects_in(repo)`, `repo_of(project)` (only when it has exactly one; otherwise an error)
- `affected(repo, base, head, moment)` → the (project, tier) pairs a change needs, defined in `test-waiting.md` section 4 and
  used by every caller that decides what to test

`roster()` keeps working without PyYAML (new machine, the laptop): `paths.workspace` stays **required** (except for the
platform's own entry, as today) and means the project's primary folder (its first scope's folder); a project in a
shared repo, or in several, also carries `repo: <name>[,<name>…]` in its `paths:` block, and `roster()` reads
`repos/*.yaml` the same line-by-line way, so recovery finds every origin. Validation checks that `paths.workspace` and
`paths.repo` agree with `scopes`.

**The sandbox decision without the index fails closed (r10).** `isolation.sandboxed` and `as_project` take `enforce`
from `hubconf.projects()`, whose stdlib fallback (no index yet, or one that cannot be read — `OSError` included)
reads `enforce` from the project files today (`roster_sandboxed`, hardened 11-01). Once a shared repo's security
fields live only in its repo file, that fallback also reads `repos/*.yaml`, and it fails closed: a project with
`paths.repo` whose repo file cannot be read counts as sandboxed, and so does a registered project whose file is
invalid or not loaded (a cross-file conflict, below), with platform work in its folders refused until it loads. The
same holds with an index: it records every registry file's folders and `enforce` as read line by line, loaded or
not, so `project_at` still finds an unloaded project's folders and `as_project` refuses there instead of running
with the user's authority. The index stores each loaded project's effective security fields, merged from the
current repo file when the index is built (never kept in a project's last-valid cache, which would hold stale repo
fields). An index built by older code is still read for these fields (v1 has them too); only readers that need v2
fields refuse it (`test-waiting.md` B1), and `registry.refresh` never overwrites an index built with a newer schema
version (a long-running process with old code, such as `lease-runner.py`, refreshes on requests). The ratchet (rule 4) also covers
readers of `enforce` outside the resolver and `isolation`.

## 3. Registry format

Today's files stay valid and mean what they mean now.

**One project, one repo (today's form):** `paths.workspace` = a repo root is shorthand for one scope `(that repo, /)`,
and the repo-level fields in the project file (`paths.origin`, `push_on_merge`, `commits`, `gate`, `commit_gate`,
`enforce`, `network`, `allow_write`, `privacy`) are that repo's.

**Several projects in one repo:** the repo has its own file in `<repos_dir>` (a new hub.json key, default `repos/` next
to `registry_dir`) holding every repo-level field, including `commit_gate` (test-waiting A1); project files in that
repo may not set them (one source, so no cross-file equality check — per-file validation could never change such a
value). `metered_models` stays per project (it is about the project's model calls). A **folder-only project** is its own
trust domain and carries the repo-level security fields in its own file; a **project in several repos** must have
the same security fields (`enforce`, `network`, `allow_write`, `privacy`) in all of them, every one of those repos
has its own repo file (shorthand cannot name two repos), and a change to the shared fields lands in one config
commit — validation rejects the
combination otherwise — because its one memory clone and its project-level jobs need a single authority (round 6:
with different `enforce`, a sandboxed repo's sessions could write the clone that the other repo's unsandboxed
sessions run git in).

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
inputs: ["cmd:python3 --version"]
tests:                            # project v2, test-waiting.md B1
- {tier: full, run: "uv run pytest -q -n {jobs}", when: [merge, nightly]}
```

**One project over several repos:** `scopes: [{repo: storefront-web}, {repo: storefront-api}]` (each repo in its own
file or in shorthand); each test tier names the repo it runs in (`repo:`, test-waiting B1), and its patterns are
relative to its scope folder there.

**A project with no repo:** `paths: {workspace: ~/workspace/field-notes}`, `scopes: [{folder: ~/workspace/field-notes}]`.

**Contracts and validation.** The test fields (`tests`, `code`, `inputs`; `runtime` keeps only `deploy`) come first, as
**project v2** (test-waiting B1, the owner's decision): renaming `runtime.test` bumps the version, and every registry
file is rewritten in one cutover sequence (test-waiting B1 "Contract"). `scopes`, `depends_on` and `paths.repo` are later additions to v2 (additive, no
further bump). `repos/` is a new contract `repo.v1` in `contracts/README.md`; `repos_dir`
and `memory_root` go into `contracts/hub.v1.schema.json` first; `repos` joins the config paths in `bin/appliers.py`,
`bin/config-check` (which the config repo's `githooks/config/pre-commit` runs).
The security fields (`PLATFORM_OWNED`) gain `paths` (whole, since the check compares top-level keys — it decides the
sandbox's folders; closed 11-01 by `16c7622`) and `scopes`; a project in a shared repo carrying any repo-level field is
invalid. Cross-file rules — two projects with the same scope, a repo both in shorthand and in a repo file, two scopes
naming one folder (compared lexically on the registered paths; a scope folder that is a symlink is invalid, since a
project could otherwise point its folder at another's and invalidate that project's file), a project pointing at a
missing repo — are checked on every load: **every** file involved
in a conflict is invalid and keeps its last valid version (none on a new machine or a rebuilt cache, so those projects
are not loaded), and the status page names the conflict. That rule needs no history, so it gives the same answer
whichever file changed last, or both in one commit. A `depends_on` never widens a sandbox: a dependency in another
repo is readable to a sandboxed dependent only if the owner grants it as a security field; otherwise validation
reports the dependency as not supported.

## 4. How each part of the platform works under the model

**Memory.** Each project's memory is one clone of `<name>-memory.git`, kept in a platform folder outside every code repo
(`<memory_root>/<name>`, a new hub.json key, default in the instance directory), with its remote named `memory`
(neutral). Each of the project's scope folders gets a symlink `.agent-memory` → that clone, so a session in
any of its repos writes notes to the same place, and a refactor that moves or deletes a folder removes only a link.
Code repos ignore the link with the pattern `.agent-memory` (no trailing slash: a symlink is not a directory to git;
today's `.agent-memory/` would not match it, while `worktree-memory.sh` already writes the slash-less form). The exclude
is written **before** a link is created and links are relative where possible, so a `git add -A` can never commit a
link carrying an absolute machine path into a public repo. The librarian keeps reading `~/.memory-copies/<name>/`. `git-sync-memory.sh`
(whose toplevel guard accepts a clone reached through a link, via `pwd -P`), `sync-all-memory.sh` and `memory-nudge.py`
(which checks `.agent-memory/.git`) work in the clone; the nudge's `git log -- .` runs from each scope folder. The
hooks-off setting in `git-sync-memory.sh` stays: a user-level `core.hooksPath` would still apply to a standalone clone.
Of the memory scripts only onboarding, recovery and renaming name the remote; until the migration they handle the
worktree form's `mini` too, and that form leaves them at the end of step 5 with the rest of the worktree form.
`worktree-memory.sh` stays: in a linked worktree it creates the link at each scope path of the worktree (not only its
root) and links the rules file and settings, as it does today. Session hooks
sync the project `project_at` names, in the session; the hourly job syncs every clone in `<memory_root>` as described
under "Platform git in project repos". One clone serves all of a project's repos, so **a project in several repos
joins their trust domains**: a session in any of them can change the clone the others' sessions read and commit.

**Backups.** The nightly backup bundles every clone's unpushed commits from `<memory_root>` (one place) and iterates
**repos** (repo files and shorthand) for code repos' unpushed and uncommitted work, instead of projects; the `.env`
search runs per scope folder (it looks three levels deep), so inner projects' files are found. Under the rule below
the platform does not walk a project's folder itself (round 8: today's walk follows symlinks, so another project's
`.env` could be copied into this project's backup and restored into its folder, and a FIFO hangs it): the `.env`
files come back as data on the stdout of the project's own backup job, which `project_git.py` already runs for its
unpushed and uncommitted work.

**Migration from today's worktree form** (one script, dry-run then `--apply`, per project), only after renaming and the
memory scripts handle both forms; onboarding and recovery only ever create the clone form (recovering an unmigrated
project is its migration). It refuses unless no session is open in the project (`hubconf.cwd_users()`),
the mount has no untracked files, and the memory branch is pushed. Then: clone into `<memory_root>/<name>.new`, verify
its HEAD equals `mini/agent-memory`, remove the worktree, move the clone into place, create the links, delete the code
repo's `agent-memory` ref, its `mini` remote and `refs/remotes/mini/*`, update the exclude pattern. agent-hub's own
plain-folder memory (no bare repo today) gets a bare repo created and its notes imported, then the same steps. When every project is migrated, the worktree-form branches are removed from renaming, the memory scripts and the
worktree cleanup.

**Rules files and hooks.** Written in each scope folder. Whether a coding agent reads a rules file in a subfolder of
where it started, where it reads hook settings from, and whether its worktree mode can open a worktree at a subfolder,
are runtime capabilities (`runtime_ports`), answered by each adapter; where one is missing, doctor's feature table says
so and the platform writes at the repo root, one marked section per project. A tool that also reads rules files in
parent folders (Claude Code does) would see two projects' blocks in an inner folder, so a session's identity is
delivered by the platform's session-start hook (`project_at`), and the static blocks name no project.

**Which project a session belongs to.** `project_at(the session's folder)`; at a shared repo's root, the root owner.
Since a repo is one trust domain with one privacy setting, a root session working in an inner project's files cannot
leak anything the repo's setting does not already allow.

**Sandbox.** A repo is one trust domain: a session of any project in a repo may read and write the whole repo, its
`.git` (common dir and worktree gitdirs, which `git commit`, `merge-branch`'s lock, `.git/info/exclude` and the result
cache need) and its deployment clone, plus **its own memory clone** in `<memory_root>` (by real path, since the link
resolves there) and what it may today (runtime, bus, caches, `allow_write`); every **other** repo, folder-only project
and **other project's memory clone, memory bare repo and memory copy** stays denied (closed for today's layout on
11-01: `policy.py` puts every memory repo that is not the project's own into `other_projects`). **What this design
claims** (decided after round 6): it adds no path by which a project exceeds its sandbox; whether the sandbox itself
holds is the sandbox's own scope (section 9, the 11-01 fix, `bus-sender.md`). Within a repo there is no write fence between projects — it would be cosmetic, since any
of them can install a git hook that runs in the others' sessions. The platform's own Seatbelt/Landlock layer
(`seatbelt.py`, `landlock.py`) derives the same set from the resolver, so the repo root of an inner project's service is
readable.

**Platform git in project repos (r7).** A repo's `.git` (config, hooks, attributes, `objects/info/alternates`) is
writable by its projects, and git runs or reads what it names. Tested 11-01 (git 2.50), in a repo with hostile config:
`git status` and `git add` ran its fsmonitor and clean filter; `git log -1 --format=%ct` ran its `gpg.program`
(`log.showSignature` plus one commit with a forged signature header); `git worktree add`/`remove` ran its hooks and
filters; `git diff HEAD` ran `diff.external`; and `git fetch` from it into a separate repo ran nothing but **served
another project's objects** through an `alternates` file, because the serving side ran with the fetcher's authority.
Inert: `git bundle create`, `rev-parse`, `for-each-ref`, `worktree list`. So the rule is stated by who runs git, not
by which command:

- **The platform runs git only in repos it owns** — the memory bare repos, the librarian's memory copies, its own
  checkout.
- **In a project-writable git dir, every git command runs as the project**: the agent's own commands in its session
  (the runtime's sandbox), or through `isolation.wrap` as one of its command jobs. **A session's hooks are not "in its
  session"**: the coding agent runs them outside its sandbox (Claude Code's sandboxing docs, read 11-01: "The sandbox
  covers shell commands only", and "command hooks, local MCP servers, … run with your full access"; each runtime
  adapter answers the same question), so the platform's hook scripts are the platform with the user's authority and
  run as the project like its jobs. What the platform needs back comes as data on that job's **stdout**
  (r9; never a file in the project's folder, which can be a symlink or a FIFO): a **bundle** (`git bundle create`,
  built with the project's read rights, so it cannot contain objects the project cannot read) or **facts** as JSON
  (worktrees, last-commit times, clean or not, commit counts), with types checked. The platform fetches only from such
  a bundle, into a repo it owns, with `transfer.fsckObjects` on.
- For a project without a sandbox the wrap is a no-op: it has the user's authority already, so there is nothing to
  cross. Cost: one wrapped job per sandboxed repo wherever the platform needs git facts (the status page, every tick).

What moves under it (none of these may run git in a project repo directly): the status page's worktree view
(`worktrees.overview()`: `git log`, `git status`, every tick), the worktree cleanup (`worktree remove`, `branch -d`),
`check-memory-gaps.py` and `weekly-reflect.py` (`git log`), the backup (`bundle create`, the diff of uncommitted work),
`propose_apply.py` (`worktree add`/`remove`), and memory: the hourly sync commits each clone and writes a bundle as a
command job of its project, the bundle on its stdout; the platform fetches it into `<name>-memory.git` and fast-forwards it; what the
bare repo has that the clone lacks (the librarian's STATE.md commits) is merged inside the clone by the project's own
fetch, in that job. The librarian's memory copies are platform-owned but hold project content, so they are checked out
with `core.symlinks=false` (a committed `STATE.md` symlink would otherwise hand the librarian, and the model, any file
it points at). Until 11-01 every one of these ran unsandboxed (the worktree form's hourly sync and push included), so a
sandboxed project could run code as the user within one scheduler tick; the separate change of 11-01 applies the rule
to today's code (`bin/project-git-job.py` runs as the project, `bin/project_git.py` reads back its JSON or fetches its
bundle), and this design inherits it.

**Built 11-01, by the sandbox fix:** `bin/project_git.py` is this rule for today's layout. A project's git work runs
as the project with a timeout and an output cap (its process group is killed: a `[include] path=<fifo>` hangs even
`rev-parse`), and what comes back is data on the job's **stdout**, never a file in the project's folder (round 6: a
bundle file can be a symlink to another project's bundle, and fetching it left that project's objects in the
platform's repo past `fsckObjects`); a bundle on stdout is fetched into a repo the platform owns. The call sites
round 6 added (`render-project-settings.py`, `cap-runner.py`, `lease-runner.py`, `upgrade-idea.py`, the session hooks)
moved with it (section 9). "Inert" above is withdrawn: no git command is assumed safe; the design's resolver starts
none (2.4). Under this design the same module serves repos and scopes: the memory clone's sync and the per-repo calls
go through it.

**merge-branch** (a repo operation; a `--question` goes to the owner once per merge, not once per project): lock → rebase → commit titles → `affected(repo, main, head, merge)` → for each pair, a
recorded pass for its key or a run (the test system, test-waiting section 4) → fast-forward → one `merge.done` /
`session.report` per affected project (the bus contract keeps one `project` per event). A failure blocks the merge; the
passes of the other pairs are still recorded. A branch that deletes or empties a registered scope fails until the
registry change lands first (otherwise the moved project's tests would be "not applicable" and the merge would pass
untested). A test in one repo that depends on another repo is caught only by the nightly run. `--project` no longer
chooses whose tests run; it only names the repo when ambiguous.

**Commit gate and git hooks.** `gate: strict` (a main-protection hook the platform already installs) is repo-level and
installed in the repo's `.git/hooks` once per repo. The test commit gate is opt-in per repo (`commit_gate`,
test-waiting A1: a `pre-commit` and a `reference-transaction` hook, installed like `gate: strict`'s and never under a
repo's own `core.hooksPath`); without it the platform installs no test hooks, and `merge-branch` is the gate.

**Services and deployment.** One deployment clone per repo, `<services_root>/<repo>`; a project's runtime folder is its
scope path inside it. Its `deploy.sh` is run from the main checkout, as today (AGENTS.md: from the development copy),
and updates the clone (the root owner's script at the root). One pull deploys the repo; each
project's services restart from their folders. When a repo is promoted, its name defaults to the root owner's, so the
existing `<services_root>/<name>` stays where it is. For a one-repo project the path is today's `<services_root>/<name>`.

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
`project_at` assigns to it, from the session folder the runtime's transcript port reports — an extension of the port
scheduled in `test-waiting.md` section 7.

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
   `sync-local-setup.sh`, `worktree-memory.sh`, the sandbox policy and both enforcement layers (including that a
   project's memory path is writable and other projects' are not), backup.
2. The resolver and the registry: `scopes`, `depends_on`, `paths.repo`, `repos/`, repo-level security fields, the
   cross-file rules, the contract work (section 3). Every existing registry file gives the same answers as today (test).
   Project v2's test fields land earlier (test-waiting section 7, step 2) and are unchanged by this step.
3. Move the files in section 1 onto the resolver, the step 1 tests staying green — the inventory refreshed first:
   since 10-31, `project_git.py`, `project-git-job.py`, `gitdirs.py` and `hub-event`'s as-project path also read a
   project's folder; `project_git.project_at` becomes the resolver's, and one pointer-file reader (`gitdirs.py`)
   serves `hubconf`'s worktree lookup and the resolver; the sandbox derives from repos (`policy.cache_env` takes the
   repo's git dir from the resolver, not `<workspace>/.git`, which an inner project does not have); the
   backup and deployment iterate repos (done here, not moved twice); the platform's git calls in project repos, moved to
   project jobs by the separate fix, keep doing so through the resolver.
4. The test system's selection and cache on the resolver (test-waiting section 7, step 5).
5. Memory: backups of `<memory_root>` and the sandbox's memory paths (own clone writable; others' clones and bare
   repos and copies denied); the hourly sync re-pointed from the worktree to the clone (it already runs as project
   jobs handing back bundles, since 11-01); renaming and the memory scripts handle both forms,
   onboarding and recovery create clones; the migration; agent-hub's memory; then removing the worktree-form branches.
6. Runtime capabilities for nested rules files, hook settings and subfolder worktrees; the worktree marker.
7. Onboarding and recovery of subfolder and folder-only projects; repo promotion.
8. End to end: a throwaway monorepo (root owner, two projects, one depending on the other) through onboarding, a
   session, a merge touching both, a deploy and the nightly run, in its own config copy and bus.

## 8. Decided

- 10-31, owner: final architecture first, no workarounds; the test system is built on this model.
- 10-31, owner had no preference; the proposals stand: a shared repo names a root owner; repo-level security settings
  (one trust domain); one memory clone per project outside the repos, linked in; one deployment clone per repo.
- 11-01, owner: a review round passes with no blocking findings (should-fix and minor go to each step's own review).
- 11-01, owner: design reviews pause until the separate fix of today's sandbox holes lands. Then the design's security
  claim is "adds no path by which a project exceeds its sandbox" (the sandbox's own soundness is that fix's scope),
  step 1 and step 2.3 are settled by running them in a throwaway hub, and round 7 reviews the result.
- Open, outside this change: whether idea sandboxes become folder-only projects.

## 9. Blind reviews: findings and what each changed

Five rounds; reviewers independent, each with one angle, all Claude models (no other provider is configured, so their
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

**Round 3 (r5), with `test-waiting.md` r6:**

| finding | by | change |
|---|---|---|
| Platform-started test runs would execute project-declared commands outside the project's sandbox | architecture, workarounds | test-waiting B4: every platform run goes through `isolation.wrap` |
| Memory clones outside the repo: a sandboxed project could not write its notes, and could read other projects' raw notes | architecture, workarounds | section 4 "Sandbox": own clone writable, others' denied; step 1 tests it |
| Moving `.state` reversed a documented decision and touched ~40 files and the running scheduler | facts, architecture, workarounds | test-waiting A1: `.state` stays, found through the git common dir |
| Inner projects' worktree sessions attributed to the root owner; rules files read from parents give two identities | architecture | worktree marker; identity from the session-start hook |
| `merge-branch` cannot recognize the hub from a worktree | architecture | 2.4: the hub repo via the git common dir |
| A link committed before its exclude would leak an absolute path | architecture | exclude first, relative links |
| `.env` search per repo misses inner projects | architecture | per scope folder |
| Folder-only and multi-repo projects had no place for security fields | architecture | section 3 |
| `worktree-memory.sh` is still needed; `memory-nudge` has no toplevel guard | facts | section 4 "Memory" |
| Onboarding "both forms" duplicated; backup/deploy moved twice (steps 3 and 6) | workarounds | clone form only; per repo at step 3 |
| Remote name `mini`, `memory_root` missing from the hub schema, `commit_gate` field, `metered_models`, roster for multi-repo | facts, workarounds, architecture | sections 3, 4 |

**Round 4 (r6), with `test-waiting.md` r7** (security, code facts, order and workarounds; the owner's bar: no blocking
findings before building). Blocking:

| finding | by | change |
|---|---|---|
| Runs inside a session cannot go through `isolation.wrap`: sandboxes cannot be nested, so a sandboxed project's every merge would be refused | security, order | test-waiting B4 "Authority": session-started runs carry the session's authority; platform-started runs are project command jobs, wrapped whole |
| Project-declared `inputs` (`cmd:`, `file:`, `config:`, `env:`) were evaluated by the platform outside any sandbox | security, order | evaluated by the runner with the run's authority; `file:` confined to the repo, `config:` to names the platform lists |
| Environments cached in the tool caches every sandbox writes let one project change what another runs | security, order | per repo, in the repo's git common dir, third-party dependencies only |
| A memory clone's `.git` is project-writable and the hourly sync runs git there unsandboxed (remote URL, fsmonitor, filters); present today in the worktree form | security | section 4 "Platform git in project repos" |
| From step 1, rebased trees would reach agent-hub's main untested (no `runtime.test`; rebase and fast-forward run no gate) | order | agent-hub declares `runtime: {test: bin/test-gate --all}` in step 1 |

Should-fix, also changed: the export location (git common dir); keying the exported tree instead of a clean-checkout
rule; the declared command, not the filled-in worker count, in the key; a fixed tier environment; ancestor scopes'
files in a nested scope's key and triggers; the environment layer installing third-party dependencies only; main moving
only to a tested tree (`reference-transaction`, replacing post-commit's bypass log, which a rebase would trip); the
two-repo cutover to v2 and lease-handover v2; `bin/run-tests`' overrides; the worktree refusal only for looked-up
state; step 2's internal order and the old step 3 folded into step 1; deploy in the main checkout; nightly exported
like every run; `commit_gate`'s meaning; worker slots by kernel locks with a bounded wait; a deterministic cross-file
conflict rule; one memory remote name; the marker's writer and validation; other projects' memory bare repos denied;
`roster()` readers in the ratchet; `depends_on` never widening a sandbox; step numbers, the `inputs` format,
signatures and counts across both documents.

**Round 5 (r7), with `test-waiting.md` r8** (security; order and facts). Blocking:

| finding | by | change |
|---|---|---|
| Read-only git the platform runs in project repos executes project-named programs (`git log` → `gpg.program`, tested), every scheduler tick | security | section 4 "Platform git in project repos": the platform runs git only in its own repos |
| "Fetch from the clone" served another project's objects through `alternates` (tested) | security | the project's job writes a bundle; the platform fetches only from bundles, with `fsckObjects` |
| The shared uv cache still let one project change another's packages (tested: an edited cached file reached a new environment) | security | test-waiting B4: per-repo package cache, fresh per-run environment (measured 0.07 s warm) |
| Step 2's order broke every agent-hub merge (the migrated `test-gate --all` cannot run in an export) and needed temporary tier code | order, security | test-waiting section 7: one merge switches readers and callers, agent-hub's tiers written by hand |
| The project's own code in a cached environment was unspecified (`uv run` syncs the project into it) | order | per-run environments, project installed from the export |
| Migration defaults (`code` absent, `runtime` emptied) were undefined | order | upgrade on read: absent `code` = every file, `runtime: {}` kept |
| `bin/run-tests` pointing `AGENT_HUB_ROOT` at a temporary folder fails the suite (reproduced) | order | only HOME and STATE temporary; ROOT the tree under test |

Should-fix, also changed: the runner's source (`--rev`, `--index`); the main guard's limits, fetch-only pulls in
`commit_gate` repos, and an absolute `core.hooksPath` so a branch cannot replace it; one `config_inputs` key; one
shared `pre-commit` for `gate: strict` and `commit_gate`; hook installs replacing symlinks; agent-hub's `code` covering
the registry so the config check still runs; cross-repo pairs left to the nightly run; the v1 upgrade kept permanently
instead of removed per instance; worker slots built once in step 2.2; `session.report` v2; muninn's lease-handover
request; `agenthub test` as a session command; the key carrying the runner's version and environment; memory copies
denied whole and checked out without symlinks.

**Round 6 (r8), with `test-waiting.md` r9:** 10 blocking points (marked open in r8, settled in r9). Three refute r7/r8 mechanisms written without a test; the rest are open design points and holes in
today's code. The count rose (5, 7, 10) because each round's untested fixes became the next round's findings, and
because reviewers kept finding holes in the existing sandbox. Not converged then; the owner paused reviews until the
sandbox fix landed (section 8), and r9 decides the open points.

**Holes that exist today, worst first** (round 6 added the first three):

- Every sandbox may write `~/.local/share/uv`, where the platform's own interpreter lives: a sandboxed project can
  change the stdlib the scheduler, the gate and services run (verified: the platform venv's interpreter is there).
  Closed 11-01 with the shared caches: `policy.write` has no tool cache left; each repo's are in its own git dir.
- `paths` is not a security field (`PLATFORM_OWNED`), so a `manifest.update` can set `paths.workspace: ~` and the
  sandbox then writes the whole home folder; `apply-updates` applies it unattended. Closed 11-01: `paths` is a
  security field.
- Session hooks run outside the sandbox and run git in the project's `.git`: `memory-nudge.py`'s `git log` runs a
  project-named `gpg.program` after every commit a sandboxed session makes (reproduced by the reviewer). Closed 11-01
  for the platform's hooks: their repo work runs as the project (`hub-event as-project`, `project_git.py run-as`);
  finding whose repo it is, before the hand-off, starts no git: `bin/gitdirs.py` reads the `.git`, `gitdir:` and
  `commondir` pointer files as plain files (non-blocking, regular files only), so a FIFO the repo's config includes
  hangs nothing (`hubbus.project_of`, `guard.main_checkout`, `worktree-memory.sh`, `sync-local-setup.sh`; closed
  11-01).
- `worktrees.auto_prune` deletes paths taken from project-written gitdir files; `propose_apply.py`, `cap-runner.py`
  and `lease-runner.py` write through project-planted symlinks; a FIFO in a repo's config hangs platform git with no
  timeout; `merge-branch --project X` runs another project's tests. Closed 11-01: deletion, copy-back and those
  writes run as the project, every project job has a timeout and an output cap, `--project` must own the repo;
  `upgrade-idea.py` (which pushed from the sandbox's repo with the user's GitHub credentials) pushes from a bare repo
  the platform fills from the project's bundle. Still open: the scheduler has no per-job timeout.
- Open (found 11-01): a sandboxed project's service or command job may write its whole workspace, including the
  runtime's local settings file (`.claude/settings.local.json`), and `render-project-settings.py` keeps hook entries
  it didn't write: a job can add a hook that the next session opened there runs as the user. Closing it means the
  adapters denying writes to the runtime's settings paths inside the workspace (a neutral list from `runtime_ports`).

Earlier, found by rounds 4 and 5, for a separate change rather than a step of this design (which inherits the
fixes). Closed on 11-01 except the sender, which waits for the owner (`bus-sender.md`):

- The status page, worktree cleanup, memory-gaps and weekly statistics, backup, `propose_apply.py` and the hourly
  memory sync run git unsandboxed in project repos whose config a sandboxed project controls (code execution as the
  user within one scheduler tick; tested with `git log` and `gpg.program`).
- The memory push from a project's repo can carry another project's objects (`alternates`).
- Every sandbox may write the shared package caches (`policy.py` `WRITE_CACHES`), so one project can change the
  packages another installs.
- A sandboxed project can read other projects' memory: their bare repos (`git_origin` is only write-denied) and their
  memory copies outside `memory/` (STATE.md, `.git`).
- A letter's sender is chosen freely (`hubbus send --from`) and the bus is writable in every sandbox, so a
  `manifest.update` can claim another project.
- The librarian reads memory copies' files through symlinks a project can commit.
- The hook installer writes through a symlink a project planted in `.git/hooks`.

**Round 7 (r10), with `test-waiting.md` r11** (soundness and order; workarounds and gaps). Four blocking, each a
one-sentence fix: the platform reading results from a project-writable store (now only through a `project_git` job,
test-waiting B4); the sandbox decision's fallback not reading repo files (section 3, fails closed); step 2.3 leaving
agent-hub's main checkout with no hooks (its landing installs them under the merge lock, test-waiting section 7);
worker slots next to the bus (a platform-owned folder, test-waiting B5). Should-fix, also changed: step 1's list
matching the settled decisions; the index refreshed by `registry.refresh`, never by `hubconf`; pointer files with a
back-pointer check; the runner found through one variable the tests set; only main's `merge-branch` runs; upgraded
tiers run once before the cutover; `is_code()` and the upgrade function in step 2.2; the key's format constant; the
nightly job synthesized, not in the project's file; npm's measured cost; multi-repo projects' repo files.

**Round 8 (r11), with `test-waiting.md` r12.** Three blocking, each from a sentence round 7 added: the resolver
answering "none" (the user's authority) when a pointer check failed; step 2.3's landing with no code to run it
(`merge-branch`'s permanent landing step for the hub repo, from step 2.2); backups walking project folders against
the rule that the platform opens no project-writable file. Should-fix, also changed: the validation precedent
(`test-gate`, not `config-check --staged`); one rule for an older index; unloaded projects fail closed in the index
too; old processes cannot overwrite a newer index; step 2.2's one-off runs as each project; the slots' read set;
`commit-msg` for every conventional repo; the fixed tier environment without `AGENT_HUB_*`; the detector's record
consumed after the fast-forward; `config:` files readable only where declared.
