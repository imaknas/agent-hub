# The git config fence: nothing in a sandboxed project writes what makes git run code

Status: plan, 11-14, not built. Measured the same day in scratch repos (git 2.50.1, macOS on a case-insensitive APFS
volume), with the platform's real Seatbelt profile (`seatbelt.service_profile`) standing in for a sandboxed writer and a
candidate fence appended to it. No model calls; Claude's and Codex's own sandboxes were not run (their part is the
conformance canary, below). This is D9 of `runtime-codex.md` as the owner redefined it on 11-14.

## The problem and the owner's decision

A sandboxed project's sessions and services may write their own `.git` today, by design (`runtime-versions.md` r3;
`projects-and-repos.md` section 4, "Platform git in project repos"). Git runs what its configuration names: filters,
fsmonitor, hooks, diff and merge drivers, signing programs, ssh and proxy commands, pagers and editors, credential
helpers, aliases. It also reads other data where configuration points it (includes, alternates, `core.worktree`,
`commondir`). So anything the owner runs git with in that folder, outside the sandbox, can run planted code as the owner:
their terminal, their editor's git integration, git apps, and the coding agents' own git (Codex runs `git status` as the
user in the session's folder; it turns fsmonitor and hooks off but not filters: `runtime-codex.md` Step 4 r2, B1).

The platform's rule so far is about itself: it runs git in a project's repo only as the project (`project_git`). That
can't cover the owner's own tools.

**The owner's principle (11-14):** a user never has to remember a special way to use their agent. Opening their own tool
normally (the Codex app, `codex`, Claude, an editor, a terminal) gets full brunna.

**The owner's decision (11-14, D9 redefined):** fix it at the source. In a sandboxed project, nothing inside the sandbox
may write git configuration that can make git execute code. What is legitimately needed, the platform writes, outside the
sandbox. D9's option (i) (`brunna session` with Codex's `--no-daemon`) stays only as an optional extra layer.

## What git runs or reads, by file (measured 11-14)

"Owner" = git run unsandboxed in the folder, as the owner's terminal or editor would. "Codex-style" = git with Codex's
flags (`-c core.hooksPath=/dev/null -c core.fsmonitor=false`, `GIT_OPTIONAL_LOCKS=0`).

**Executes** (each with a canary program; measured unless marked):

| source | what ran, from which ordinary commands |
|---|---|
| `.git/config`: `filter.<d>.clean` / `process` (with an attribute selecting `<d>`) | `status`, `diff`, `add`, `commit`, `checkout`; **Codex-style status too** |
| `.git/config`: `filter.<d>.smudge` | `worktree add`, checkouts |
| `.git/config`: `core.fsmonitor` | almost every command (`status`, `diff`, `add`, `commit`, `fetch`, `pull`, `gc`…); not Codex-style |
| `.git/config`: `diff.external`, `diff.<d>.textconv`, `diff.<d>.command` | `diff` |
| `.git/config`: `merge.<d>.driver` | `merge` with a 3-way file merge |
| `.git/config`: `gpg.program` | `commit -S` / `commit.gpgSign`; `log` with `log.showSignature` on a signed commit |
| `.git/config`: `core.pager`, `pager.<cmd>` | `log`, `status` in a terminal (pseudo-terminal) |
| `.git/config`: `core.editor` | `commit` without `-m` |
| `.git/config`: `alias.<x>` | overrides the owner's global alias of the same name (`git st` ran it) |
| `.git/config`: `core.hooksPath` | hooks from any folder, a tracked one included |
| `.git/config`: `remote.<r>.uploadpack`, `core.sshCommand`, `core.gitProxy` | `fetch`, `pull` |
| `.git/config`: `credential.helper` | `fetch` from a server that asks for credentials |
| `.git/config`: `protocol.ext.allow=always` + an `ext::` URL | `fetch` (without the setting, `ext::` is refused: measured) |
| `.git/config`: `submodule.<n>.update=!cmd` | `submodule update` when an update is due (the same key in `.gitmodules` is refused by git: measured) |
| `.git/config`: `include.path`, `includeIf.*.path` (a tracked file works) | whatever the included file names |
| `.git/config`: `extensions.worktreeConfig` + `config.worktree` | `config.worktree` is ignored without the extension, read with it |
| `.git/worktrees/<n>/config.worktree` | as above, in that worktree |
| `.git/modules/<x>/config` | the **parent's** `status`, Codex-style included (git recurses into submodules) |
| `.git/hooks/*` | `post-index-change` on a plain `status`, `diff`, `add`; `reference-transaction` on `commit`, `checkout`, `pull`, `gc`; the usual commit, checkout, merge and push hooks |
| `.git/commondir` (main git dir) | git reads config, hooks and objects from wherever it points: filters ran |
| `.git/worktrees/<n>/commondir`, a worktree's `.git` file | the same, for git run in that worktree; a `.git` file anywhere makes its folder a checkout of any git dir |
| `.git/info/attributes`, tracked `.gitattributes` | select drivers: inert with an undefined driver (measured), but they **select drivers defined in the owner's global config** (ran, Codex-style included) |
| a nested repo (`sub/.git`) | everything above, for git run in `sub/` (an editor scanning nested repos, a terminal there) |
| a git-dir-shaped folder (HEAD, objects, refs, config) under any name | owner's git with its cwd inside it: with `core.bare=false` and `core.worktree=..` its fsmonitor ran; `safe.bareRepository=explicit` refused it |
| `.git/rebase-merge/git-rebase-todo` with an `exec` line | not by `status`; **by the owner's `rebase --continue`** |
| `uploadpack.packObjectsHook` in repo config | not honored (git reads it only from protected config): not measured from a fetch out of the repo |
| `.lfsconfig` (tracked) | **not measured**: git-lfs isn't installed on this machine |

**Reads across** (no code runs, but another place's data moves where the project can read it):

| source | measured |
|---|---|
| `core.worktree` pointing at another folder | the owner's `status` listed its files, `add -A` and `commit` put a secret file into the project's repo |
| `objects/info/alternates` | after the owner's `repack -a -d`, another repo's objects were copied in and stayed readable once the alternates file was gone; plain `gc` didn't copy |
| `.git/remotes/<r>` (legacy remote files) | `fetch <r>` fetched from the path it names |
| `commit.template`, `core.excludesFile`, `http.cookieFile` and similar | not measured (`commit.template`: the probe's commit aborted) |

**Inert** (data only: they decide what git does with the repo's own content, nothing runs): `info/exclude`,
`packed-refs` and refs, `HEAD`, `FETCH_HEAD`, `ORIG_HEAD`, logs, `shallow`, `info/sparse-checkout`, `description`,
`COMMIT_EDITMSG`. Reasoned from their formats, not probed one by one. Crafted objects, packs or an index can only matter
through git's own parser bugs (a risk below).

**Hangs:** `include.path` pointing at a FIFO hangs every git command that reads the repo's config (plain `status`,
`worktree add`), as `projects-and-repos.md` found. `git config --file <f> --list` doesn't follow includes and returned at
once; with `--includes` it hung.

## The fence

### What is denied, to every writer inside a sandboxed project's sandbox

Matched on any git dir, **anywhere the writer may write** (not only under the workspace: Seatbelt checks a rename at its
two ends only, so a repo built in the temp area and moved in would land; measured, below):

- **Any entry named `.git`**, as the entry itself: it can't be created (directory, file, link), removed, renamed or
  replaced. This stops `git init`, `git clone`, gitfiles, nested repos and `--separate-git-dir` pointers. Writing *inside*
  an existing `.git` folder is not affected by this name.
- Inside a git dir (as `<gitdir>/…`):
  - `config`, `config.lock`, `config.worktree`, `config.worktree.lock`, `commondir`, `gitdir`;
  - `hooks` (the whole tree);
  - `info` (the entry) and `info/attributes`;
  - `objects` and `objects/info` (the entries), `objects/info/alternates`, `objects/info/http-alternates`;
  - `modules` (the whole tree: every submodule's git dir);
  - `worktrees` and each `worktrees/<n>` (the entries), `worktrees/<n>/commondir`, `gitdir`, `config.worktree` (+ `.lock`);
  - `remotes`, `branches` (the whole trees).

Every folder on the way to a fenced file is denied as an entry too, so a prepared folder can't be swapped in (a
`mv stage/info .git/info` lands *inside* the existing folder as `.git/info/info`, inert: measured).

What stays writable in a git dir: the index, objects, refs, logs, `HEAD`, `FETCH_HEAD`, rebase and merge state, the stash,
`info/exclude`, files inside `worktrees/<n>/` other than the three named, and `.git/brunna/` (the outbox, the test
runner's store, the tool caches).

### Measured under the fence (Seatbelt, the platform's service profile + the deny list above)

Each probe ran once without the fence (all succeeded) and once with it.

**Refused** (`Operation not permitted`): appending to `.git/config`; `git config`; `config.lock` then rename; a hard link
to `.git/config` (in the workspace and in the temp area); replacing `.git/config` with a symlink; writing a hook;
swapping `.git/hooks`; renaming `.git`; `.git/info/attributes` (also as `attributeſ`); `.git/commondir`;
`worktrees/<n>/commondir` and `config.worktree`; a worktree's `.git` file; `.git/modules/x/config`; `alternates`;
`.git/remotes/x`; `git init sub`; `git clone … sub`; `mkdir sub/.GIT`; a `sub/.git` file; `git init` in the temp area;
a bare repo renamed to `x/.git`.

**Folded spellings:** the volume opens `.GIT`, `CONFIG`, `conﬁg` (U+FB01), `hooKs` (U+212A, Kelvin sign) and `hookſ` /
`attributeſ` (U+017F) as the ASCII names (found by trying, against the volume, upper case and the 11 characters Python's
case folding maps to ASCII letters, in each fenced name; not every code point). Seatbelt refused every one of them with plain ASCII rules, for existing and new entries alike: it matches what
the volume resolves. Claude's and Codex's own sandboxes must be shown to do the same (canary). Side finding:
`runtime_claude._folds` says nothing but the long s folds to a letter its names use; the Kelvin sign folds to `k`, which
`.claude/hooks` and `.claude/skills` contain. Seatbelt is unaffected; Claude's own rule matching for a *missing*
`.claude/skills` should get a canary.

**The anywhere rule is needed:** with the same denies limited to the workspace, a repo `git init`-ed in the temp area,
given a hostile config and moved in arrived whole, config included (measured). A rename's source is checked as
`file-write-unlink`, the same operation `rmdir` needs (measured), so "can't be moved out" can't be told apart from "can't
be removed".

**Ordinary operations:**

| works under the fence | breaks |
|---|---|
| `status`, `add`, `commit`, `checkout -b` (local start), `fetch`, `pull`, `merge`, `rebase`, `stash`, `gc`, `maintenance run`, commit and rebase in an existing linked worktree, Codex-style status, `info/exclude` | `switch -c --track` / `checkout -b x origin/x`: exit 1, "unable to write upstream branch configuration" |
| `push origin <branch>` | `push -u`: **the push lands, the upstream isn't recorded, exit 0** (error text only) |
| `branch -D` (no config section): exit 0 with a warning | `branch -m`: **the branch is renamed, exit 128** ("update of config-file failed") |
| brunna's `project-git-job` ops: `head`, `worktrees`, `exclude`, `memory-sync` | `branch --set-upstream-to`, `remote add`, `remote set-url`, `config user.*`, `sparse-checkout set` |
| | `worktree add` (anywhere: the admin dir can't be made), `worktree remove`, `prune` |
| | `submodule add` (and so `update --init` of a new one) |
| | brunna's `hook-write`, `set-origin`, `worktree-add`, `worktree-remove`; so `render-project-settings`' hook installer, `upgrade-idea`'s origin, `propose_apply`'s worktree, `merge-branch --dry-run`'s worktree |
| | anything that installs hooks (`git lfs install`, pre-commit's installer) |

Every break is loud (an error naming the file), except `push -u` (exit 0) and `branch -D` (warning only). Not
measured: `git maintenance start` / `register` (they write the owner's global config, which every sandbox already
denies, and schedule jobs), `git lfs install` itself (no git-lfs here; its hook writes are refused as above).

**Package managers:** uv makes an empty `sdists-v9/.git` file in its cache at first use, opens it for writing on every
run and removes it in `cache clean`; under the fence uv fails at once, fresh or warm cache, even for a registry package
(measured). pip with a `git+` dependency clones into the temp area and fails (measured). npm's and cargo's git
dependencies clone the same way (not measured). Test suites that `git init` scratch repos fail.

**uv's marker, the one exception (measured):** allowing writes to exactly `<uv cache>/sdists-v<N>/.git`, where the uv
cache is the project's own (`<repo>/.git/brunna/cache/uv`, `policy.cache_env`), and pinning every folder on the way
(`.git/brunna`, `cache`, `uv`, each `sdists-v<N>`: no `file-write-unlink` on them as directories) let uv run, while moving
the bucket or the cache out, hard-linking the marker as a `.git` elsewhere, a new `.git` elsewhere in the cache and a
bucket-named folder outside the cache were all refused. The marker itself can be given content (`gitdir: …`, measured),
but it can't leave the git dir, and only git run from inside that bucket would read it. The bucket name is uv's; a uv
that renames it fails loudly until the platform's list catches up (a conformance check, below).

An "empty `.git` file only" rule doesn't work: Seatbelt checks a new file's creation, not the bytes written through the
same descriptor, and a rename, `cp` or `cp -c` onto the name all carried content in (measured).

### State from before the fence

The fence is by path, so it binds only what is still at the fenced names. Measured with the fence on, all of these
**still wrote**:

- a hard link to `.git/config` made earlier (written through its other name: `.git/config` changed);
- `.git/hooks` already a symlink to a tracked folder (both through the link and directly);
- `.git/config` already a symlink to a tracked file;
- a main checkout whose `.git` is a gitfile pointing at a git dir not named `.git` (`git config` succeeded);
- `core.hooksPath` pointing at a tracked folder (a husky-style setup): a hook written there.

So putting the fence on starts with the scan (below), and a project counts as fenced only once the scan has passed.

### The policy port and each adapter

`policy.py` gains `git_fence`, in neutral terms: the list above as patterns relative to a git dir (`config`,
`worktrees/*/commondir`, …), each a file or a tree, plus the entry rule for `.git`, plus `git_fence_pinned` (the uv
marker and the folders pinned on its way, as absolute paths computed from the workspace). Every sandbox translates it:

- **Seatbelt** (`seatbelt.service_profile` for services and command jobs, `seatbelt.profile` for cap-run jobs): regexes
  matched anywhere, as `protect_denies` does, written after the allows; the pinned exception after them. Built and
  measured above as a prototype.
- **Claude sessions** (`runtime_claude.settings`): Edit denies on each pattern under the workspace, the write roots and
  the temp roots (as `_protect_rules`), which the sandbox takes in for Bash too. Unmeasured: whether a rule naming
  `**/.git` denies only the entry (sessions must keep writing inside `.git`), whether `**/.git/worktrees/*/commondir`
  is expressed, and the folded spellings. Confined runs keep M7 of `confined-mail-tools.md` (every `.git` denied whole),
  which is stricter.
- **Codex sessions** (the layer-1 profile, `runtime-codex.md` Step 4): `deny` globs, which r2 measured to work for
  nested names (`read` entries can't be globs). The renderer r2 plans (not built) makes `:tmpdir` read-only for Codex's
  tools.
- **Landlock** (Linux): can't express it (below).

### Nested repos and worktrees

No sandboxed writer creates a `.git` anywhere, so a nested repo or a worktree is always made outside the sandbox: by the
owner, by the agent tool itself (Claude Code's worktree mode, Codex's worktrees; both run git as the user, not inside the
tool's sandbox: to be confirmed for each, see the canary), or by the platform's command below. Once made, the sandbox
works in it normally (commit and rebase in a linked worktree were measured to work) and can't repoint it.

The tools' own worktree creation runs git as the user in the project's repo. Before the fence that is B1 again, for
Claude too (a `post-checkout` hook and a smudge filter run); with the fence the config sources are the platform's.

## What the platform pre-sets, and when

Written by the platform outside the sandbox, into a sandboxed project's `.git/config`, at onboarding and by
`render-project-settings` (every 15 minutes, and on `brunna config apply`), only when a key is absent:

- `push.default=current`, `remote.pushDefault=origin`: a plain `git push` on a new branch pushes it, no config written
  (measured). `push.autoSetupRemote` stays off: on, the push lands and the config write fails with exit 0 (measured).
- `branch.autoSetupMerge=false`: `checkout -b x origin/x` and `git switch x` for a remote-only branch work without
  writing tracking config (measured). A branch has no upstream then; `git pull` with no arguments fails ("no tracking
  information"), `git pull origin <branch>` works (both measured). The rules text says so.
- `pull.rebase=false` (explicit, so git doesn't ask).
- The platform's own hooks (commit gate, commit-msg) and `info/` (so `exclude` can be written), as today, but written by
  the platform rather than by a job of the project's.

What can't be pre-set: renaming a branch (`branch -m` always rewrites config), upstream tracking, remotes, worktrees,
submodules, sparse checkout, hook installers. Those go through the command below.

## The narrow platform command: `brunna git`

`brunna git <op> …`, from a session, service or job of a sandboxed project. The request goes into the project's own
folder like cap-run's (`.hub-run/requests/`, kind `git`; the folder it is found in is the project, `cap-runner.py`'s
checks apply), and the runner does it outside the sandbox. Ops:

- `remote add|set-url|remove <name> <url>`;
- `upstream <branch> <remote>/<branch>` (sets `branch.<b>.remote` / `merge`), `branch-rename <old> <new>` (the ref is
  renamed in the sandbox; this moves the config section);
- `config user.name|user.email <value>`;
- `worktree add <path> [<branch>]`, `worktree remove <path>`, `worktree prune`;
- `submodule init|update [<path>]` (the project's `.gitmodules`), if the owner wants submodules at all (question below).

**When the runner may write at all:** only for a project whose every writer is fenced (its services, jobs and the
sessions of each runtime it has, each canary passed) and whose scan passed. Before that, and always on Linux, nothing
keeps `.git` still between the runner's check and its write, and a write as the owner would go wherever a planted link
leads: measured, `git config` wrote through a `.git/config` that was a symlink to a tracked file, and
`projects-and-repos.md` already records the hook installer writing through a planted `.git/hooks`. So until then
brunna's own ops stay what they are today, jobs of the project, and `brunna git` says the project isn't fenced yet.

**How the runner writes**, so that running git as the owner in a project's repo stays safe:

- the scan first (below); a repo that doesn't pass is refused, with the reason;
- config edits never in place: the runner copies the file (opened without following links, a regular file with one
  link), edits the copy with `git config --file <copy> <key> <value>` from a neutral folder, with
  `GIT_CONFIG_NOSYSTEM=1` and `GIT_CONFIG_GLOBAL=/dev/null` (measured on the file itself: runs nothing with a hostile
  config present, doesn't follow includes, doesn't hang on a FIFO include), and installs it as a fresh inode through a
  directory descriptor for `.git`, opened without following links (the neutraliser's path, below); hooks the same way;
- worktrees with `git --git-dir=<common dir> -c core.hooksPath=/dev/null -c core.fsmonitor=false worktree add
  --no-checkout` (measured: with hostile filters, fsmonitor and hooks present, nothing ran), then the checkout itself
  (`reset --hard`) as the project, inside its sandbox, where its own filters run as itself; `worktree remove --force` and
  `worktree prune` the same way (nothing ran). A FIFO include **hung** `worktree add --no-checkout`, so every runner op
  has a timeout and kills its process group, as `project_git` does;
- everything checked against **one allowlist table** shared with the scan: the key, and the value (a remote URL is
  `https://`, `ssh://`, `git@host:` or a path the platform owns; no `ext::`, no `<helper>::`, no local path into another
  project). A key not in the table is refused; the error names the table.

**The allowlist** (the first version; a key is added only with a reason it can't make git run or read anything else).
Keys are matched case-insensitively in the section and key parts, as git does; `<n>`, `<b>` are any name:

| key | value rule | why it is there |
|---|---|---|
| `core.repositoryformatversion` | `0` or `1` | written by `git init` |
| `core.filemode`, `core.logallrefupdates`, `core.ignorecase`, `core.precomposeunicode`, `core.symlinks` | `true` / `false` | written by `git init` / `clone` |
| `core.bare` | `false` | written by `git init`; a non-bare repo only |
| `extensions.objectformat` | `sha1` / `sha256` | repository format |
| `remote.<n>.url`, `remote.<n>.pushurl` | `https://…`, `ssh://…` or `user@host:path` with no option-looking host; or a path the platform owns (the memory bare repo); never `ext::`, `<helper>::`, `file://` or a path elsewhere | remotes |
| `remote.<n>.fetch`, `remote.<n>.push` | a refspec: optional `+`, `refs/…:refs/…`, at most one `*` each side | remotes |
| `remote.<n>.prune`, `remote.<n>.tagopt` | `true` / `false`; `--tags` / `--no-tags` | remotes |
| `branch.<b>.remote`, `branch.<b>.pushremote` | the name of a remote in this file | upstream |
| `branch.<b>.merge` | `refs/heads/…` | upstream |
| `branch.<b>.rebase` | `true` / `false` / `merges` | upstream |
| `user.name`, `user.email` | one line, printable, at most 200 characters | identity |
| `push.default` | `current` / `simple` / `upstream` / `nothing` | preset |
| `remote.pushdefault` | the name of a remote in this file | preset |
| `branch.autosetupmerge` | `false` | preset |
| `pull.rebase`, `pull.ff` | `true` / `false` / `only` | preset |
| `submodule.<n>.url`, `submodule.<n>.active` | as `remote.<n>.url`; `true` / `false` | only if submodules are allowed (question 6) |

Not in it, on purpose: every `include*`, `core.worktree`, `core.hooksPath`, `core.fsmonitor`, `core.sshCommand`,
`core.pager`, `core.editor`, `alias.*`, `filter.*`, `diff.*`, `merge.*`, `gpg.*`, `credential.*`, `url.*`,
`protocol.*`, `uploadpack.*`, `extensions.worktreeconfig` (set only by the platform, when a sparse checkout is asked
through the runner), `lfs.*`. `push.autoSetupRemote` and `branch.autoSetupMerge=true` aren't either: with them git tries
to write config the sandbox can't.

**The rules text agents get** (in `templates/agent-instructions.md`, for sandboxed projects only):

> This project's git configuration is written by the platform. Commit, branch, fetch, pull, merge, rebase and push work as
> usual; a new branch has no upstream, so push with `git push` and pull with `git pull origin <branch>`. For a remote,
> an upstream, a branch rename, your git identity, a worktree or a submodule, use `brunna git …` (`brunna git --help`).
> `git init` and `git clone` don't work here, nor git dependencies (use the package's archive URL, e.g. a GitHub
> tarball, instead of `git+…`). Don't try to write `.git/config`, `.git/hooks` or `.git/info/attributes`.

## The scan, and neutralising what is already there

`brunna git-scan <project>` (and a platform job, `brunna/git-fence`, every 15 minutes with `render-project-settings`,
plus once before every runner op). The platform reads project-writable files only by opening them without following
links, as regular files, and parses a **copy** in its own temp folder with `git config --file <copy> --null --list`
(so includes aren't followed, and a link can't redirect the read). It never runs git in the project's repo to scan it.

Checks, for each repo in the workspace, the memory clone and the project's runtime folder:

- every config source (`config`, `config.worktree`, `worktrees/*/config.worktree`, `modules/**/config`): every key in
  the allowlist with a valid value; no `include.*` / `includeIf.*`; no `core.worktree`; `core.bare=false`;
  `extensions.worktreeConfig` only if the platform set it;
- `hooks/`: only git's samples and the hooks the platform recorded (by hash);
- `info/attributes` absent or empty; `objects/info/alternates` absent; no `commondir` in a main git dir; each
  `worktrees/<n>/commondir` is `../..` and `gitdir` points at a worktree folder that points back;
- every fenced name a regular file or folder, **not a symlink, with one link** (`nlink == 1`);
- every `.git` entry anywhere in the workspace (a walk that follows no links and enters `node_modules` too) is either the
  repo's own git dir, a gitfile into its `worktrees/<n>` or `modules/**`, or one the platform made and recorded; anything
  else is a nested repo;
- git-dir-shaped folders (`HEAD`, `objects/`, `refs/`) outside any `.git`;
- rebase or sequencer state holding `exec` lines (reported only: it is a session's own work in progress);
- informational: the drivers the owner's global and system git config define (`filter.*`, `diff.*.textconv|command`,
  `merge.*.driver`), which a tracked `.gitattributes` can select.

**Neutralising**, outside the sandbox: a fenced file is rewritten as a fresh inode (a new file next to it, opened
`O_EXCL|O_NOFOLLOW`, then renamed over it), with only the allowlisted keys of the old one; unknown hooks are moved out;
a planted `alternates`, `info/attributes` or `commondir` is removed; a link at a fenced name is replaced by a real file
or folder. The old content is kept in the platform's state (`.state/git-fence/<project>/`), never in the project. The
one-time install does this for every fenced file whether or not it looks hostile (it is how a hard link made earlier is
cut). Whether neutralising is automatic or waits for the owner is a question below; until then, a finding marks the
project "fence not holding" and says what was found.

**Status and doctor:** a row per sandboxed project (fenced and scanned clean at <time> / not holding: <finding> / not
supported on this OS), a row for the owner's global drivers that tracked attributes can select, and the uv check (the
marker's bucket name matches the installed uv's).

## Linux

Landlock only allows: a write right on a folder covers everything below it, and it can't deny a name inside a writable
tree (`landlock.py`'s header, and why it doesn't declare `runtime-files-locked`). So on Linux nothing stops a sandboxed
service or job from writing `.git/config` or making a nested repo. The fence isn't offered there: the scan still runs
and neutralises on its schedule (detection with a window, not prevention), the status row says "not supported on this
OS", and a sandboxed project's sessions of any runtime whose git runs as the user count as **not confined** on Linux
(as r2's nits plan for Codex's confined check, `runtime-codex.md`). On Linux brunna's own ops stay jobs of the project
(below), since nothing keeps `.git` still between a check and a write as the owner.

## What this does for Codex layer 1

`runtime-codex.md` Step 4 r2's confined check returns "not confined" for B1 today. With the fence it gains a condition
instead: **the fence holds**, which is

1. the project is sandboxed and its scan passed (recently, and after the last finding was resolved);
2. every writer the project has fences: its services and jobs (Seatbelt), its Claude sessions (settings), its Codex
   sessions (the rendered profile), each runtime's canary passed for the version running;
3. macOS (Linux: not confined);
4. the owner's global drivers are listed and accepted (question 3 below).

Codex's own `git status` then reads only configuration the platform wrote, and B1's filter canary changes meaning: it is
no longer "a planted filter runs" but "the sandbox can't plant one".

**The conformance canary**, per runtime and version (`conformance_<runtime>.py`, run by `brunna/update-runtimes` and for
each version a session runs), from inside that runtime's sandbox in a throwaway project: append to `.git/config`;
`git config`; write `.git/hooks/x` and `.git/info/attributes`; create `sub/.git` as a folder and as a file; `git init` in
the temp area; write `.git/worktrees/<n>/commondir`; a hard link to `.git/config`; rename `.git/info`; the folded
spellings (`.GIT/config`, `conﬁg`, `hooKs`, `attributeſ`); each expected refused. Positive controls, expected to work:
a commit, a write to `.git/brunna/probe`, `info/exclude`, the uv marker. For Claude and Codex also: who creates a
worktree in the tool's worktree mode (expected: the tool, as the user, outside its sandbox). A version where any canary
lands counts as not confined, and `update-runtimes` holds it back for sandboxed projects.

## brunna's own flows that change

- `project_git`'s `hook-write`, `hook-remove`, `set-origin`, `worktree-add`, `worktree-remove` move from "a job as the
  project" to the runner when the project's switch flips, not before (they fail under the fence: measured). Callers: `render-project-settings` (hooks),
  `upgrade-idea` (origin), `propose_apply` (worktree), `prune-worktrees` (removal).
- `merge-branch --dry-run` asks the runner for its temporary worktree (today it runs `worktree add` from the session:
  fails under the fence, measured) or tests the rebased commit without one (`brunna test --rev` already exports a tree).
- `memory-sync`, `head`, `worktrees`, `exclude`, `merge-branch`'s rebase and fast-forward, the outbox, the test runner's
  store and the tool caches keep working (measured or unchanged paths).
- `projects-and-repos.md` section 4's rule gains one sentence: in a fenced repo, the runner may run the measured
  non-executing commands above as the owner, after a passing scan, with a timeout; everything else stays as the project.

## Tests

- `policy.git_fence` and each adapter's rendering (Seatbelt regexes, Claude rules under every root, Codex globs, Landlock
  reporting `Unsupported`), and the boundary ratchet (no runtime syntax in `policy.py`).
- Seatbelt integration, macOS only, as `tests/test_control_files.py`: every hostile probe above refused, every ordinary
  operation above working, the uv marker's pinned exception (works; can't leave), each with an unfenced positive control.
- The scan: allowlist parsing from a copy, no hang on a FIFO include, `nlink`, links at fenced names, gitfiles to unknown
  git dirs, nested repos, git-dir-shaped folders, hooks by hash; neutralising keeps allowlisted keys, cuts hard links,
  never writes through a link.
- The runner: the allowlist refuses unknown keys and bad URLs; every op refused on a failing scan; timeouts kill the group.
- Golden output for the rules text and the status rows.

## Order of work

Nothing writes as the owner into a project's `.git` before every writer of that project is fenced; a project becomes
"fenced" as a whole, by a per-project switch the platform flips only when the last writer's fence is rendered and its
canary passed.

1. The scan, report only, with the status and doctor rows: no behaviour changes, and it shows what is already out there.
2. The runner's `git` kind and the allowlist, built and tested, but answering "not fenced yet" for every project.
3. The fence rendered for each writer, behind the per-project switch: Seatbelt (services, command jobs, cap-run) with
   the uv exception; Claude sessions' settings with its canary in `conformance_claude.py`; Codex's rendered profile with
   its canary. A writer that can't fence keeps the project unfenced.
4. Flipping the switch for a project: the one-time install (every fenced file rewritten as a fresh inode, links
   replaced, unknown hooks moved out, as question 4 decides), then the presets, then brunna's own ops (hooks, origin,
   worktrees, `merge-branch --dry-run`) move from jobs of the project to the runner, and the rules text for that project
   changes. The scan then runs on its schedule and before every runner op.
5. The confined check's "fence holds" for Codex (Step 4 r2 steps 3–5).
6. Linux: the scan and the rows only; ops stay jobs of the project.

## Open questions for the owner

1. **Scratch repos and git dependencies.** The anywhere rule means no `git init` or `git clone` inside the sandbox:
   test suites that build scratch repos and `git+` dependencies fail (measured for pip; uv works through its exception).
   Options: (a) accept it, with archive URLs and `git --git-dir=<x>.git --work-tree=<y>` for tests, and the runner for
   real clones (recommended: no window); (b) a scratch folder where `.git` may be made, its folders pinned so nothing
   leaves it, cleaned by the platform (tests can't remove their own temp folders there); (c) scratch on a separate
   volume, so a move into the workspace becomes a copy the fence refuses (heavy; unmeasured).
2. **`safe.bareRepository=explicit` in your global git config.** It is the only measured stop for a git-dir-shaped
   folder planted under another name. It is your user-level setting, so the platform asks; the platform's own use of
   bare repos by `-C` would move to `--git-dir` first.
3. **Your global git drivers.** A tracked `.gitattributes` selects any driver your global config defines (measured).
   If you use git-lfs, its clean and smudge run on the project's
   files and it reads the project's `.lfsconfig` (unmeasured). Should a defined driver block "confined", or be listed and
   accepted?
4. **Neutralising**: automatic (backed up in the platform's state, then a notice), or report and wait for you?
5. **Hook frameworks you set up yourself** (`core.hooksPath` into a tracked folder, pre-commit, husky): they run the
   project's tracked content as you on every commit. Remove them in sandboxed projects, or keep them as your choice with
   a doctor warning?
6. **Submodules** in sandboxed projects: through the runner, or not supported? With `modules` fenced as a whole tree,
   work *inside* an existing submodule (a commit, a fetch, its index) also fails in the sandbox (follows from the rule;
   not measured). The alternative is fencing the same names inside each `modules/**` git dir instead of the whole tree.

## Risks: what this still doesn't cover

- **The owner's own drivers and hook frameworks** run as the owner on content the project chooses (question 3, 5).
- **Git-dir-shaped folders** under other names, until question 2 is answered; the scan reports them.
- **Rebase and sequencer state**: a planted `exec` line runs if the owner continues an operation a session left
  (measured); it can't be fenced, since sessions rebase. The scan reports it.
- **The platform's own commit gate**: `brunna test` "runs as whoever runs it" (`bin/test-runner.py`), so the owner's
  commit in a sandboxed project with `commit_gate` runs the project's tests as the owner. Out of this design's scope;
  to be checked separately.
- **Other tools' project files**: editor settings and tasks (`.vscode/`, `.idea/`) and other agents' control files are
  the same class as `.codex` and `.claude`; this fence is git's part only.
- **Git's parser** on crafted objects, packs or an index the project wrote: git's own robustness.
- **Linux**: no fence (above).
- **The window before the first scan**, and repos stashed in a writable place before the fence and moved in afterwards:
  the periodic scan finds a `.git` the platform didn't record; before that, the owner's tools could open it.
- **uv's marker** can hold content and stays a writable `.git` file inside the git dir; uv's bucket name is uv's.
- **The runner runs git as the owner** in the project's repo, in the narrow measured forms, behind the scan and a timeout.
- **Writers outside the sandbox that run project code**: anything the owner runs in the folder (a build, a test run) is
  outside this design, as it always was.
