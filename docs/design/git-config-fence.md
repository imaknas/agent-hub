# The git config fence: nothing in a sandboxed project writes what makes git run code

Status: plan, 11-14; r2 after a blind review, and r3 on what the scan can claim (both below; they hold where the text
above disagrees). Built: r2 B3
(the commit gate's tiers in the project's sandbox), cap-runner's FIFO fix (S5) and step 1, the scan (report only;
"Step 1 as built" at the end); the rest not built. Measured the same day in scratch repos (git 2.50.1, macOS on a case-insensitive APFS
volume), with the platform's real Seatbelt profile (`seatbelt.service_profile`) standing in for a sandboxed writer and a
candidate fence appended to it. No model calls; Claude's and Codex's own sandboxes were not run (their part is the
conformance canary, below). This is D9 of `runtime-codex.md` as the owner redefined it on 11-14.

**Superseded in part (11-14):** the owner chose git through the platform (`git-broker.md`): a sandboxed project writes
no git dir at all, and a platform broker runs its git. That design replaces this one's per-name fence, its narrow
`brunna git` runner and its one-time install; this doc's scan, config allowlist, URL rule and presets stay, as that
doc's table says.

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

So putting the fence on starts with the one-time install (every fenced file rewritten as a fresh inode, links
replaced: below); the scan reports what it finds but proves nothing (r3).

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
sessions of each runtime it has, each canary passed) and whose one-time install is done. Before that, and always on Linux, nothing
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

**Status and doctor:** a row per sandboxed project (no known problem found, a best-effort check, at <time> /
problems found: <finding> / not supported on this OS), a row for the owner's global drivers that tracked attributes can select, and the uv check (the
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

1. the project is sandboxed and its one-time install is done; a finding of the scan keeps it unconfined, but the
   scan finding nothing proves nothing and is not a condition (r3);
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

Rewritten in r2 with the blind review's recommendation for each; the owner decides.

1. **Scratch repos and git dependencies.** Recommended: (b), limited to the test runner's per-run TMPDIR (r2 S3).
   Only there may a `.git` be made; the runner removes the run folder afterwards. What it costs: a repo made there can
   be moved into the workspace before that (pinning its folders would stop the tests' own clean-up, measured: a
   rename's source and `rmdir` are one operation), so for these the fence is detection by the scan, not prevention.
   Elsewhere the anywhere rule stays: `git+` dependencies use archive URLs. The other options were (a) none at all and
   (c) a separate volume.
2. **`safe.bareRepository=explicit` in your global git config.** Recommended: yes, as a precondition of "fence holds"
   (r2 S1); the platform asks, since it is your user-level setting, after moving its own uses of bare repos to
   `--git-dir`.
3. **Your global git drivers.** Recommended: listed and accepted (they are your programs); `.lfsconfig` is measured
   once git-lfs is available on a machine.
4. **Neutralising.** Settled by r2 S2: automatic only at the one-time install; afterwards reported, never stripped.
5. **Hook frameworks you set up yourself.** Your choice, but "fence holds" is false while `core.hooksPath` points into a
   project-writable folder.
6. **Submodules.** Recommended: fence the git-dir names inside each `modules/**` (not the whole tree, so work inside an
   existing submodule goes on); the runner never fetches; no new submodules at first.

## Risks: what this still doesn't cover

- **The owner's own drivers and hook frameworks** run as the owner on content the project chooses (question 3, 5).
- **Git-dir-shaped folders** under other names, until question 2 is answered; the scan reports them.
- **Rebase and sequencer state**: a planted `exec` line runs if the owner continues an operation a session left
  (measured); it can't be fenced, since sessions rebase. The scan reports it.
- **The platform's own commit gate**: closed by r2 B3 (built and reviewed 11-14): a sandboxed project's tiers run in its sandbox
  whoever commits.
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

## r2 (11-14), after the blind review

A cold review of the plan above found four blocking points (B1–B4; B3 a hole already open in the platform), seven
should-fix items (S1–S7) and nits. Where r2 and the text above disagree, r2 holds.

**B1, an index the project writes.** An index the project writes can carry entries that git's checkout of the index
writes into the git dir; details kept out of this public doc pending the owner's decision on reporting them to git's
security list. Folded in:
- The scan parses a copy of every index of the repo (the main one, each `worktrees/<n>/index`, `modules/**/index`):
  versions 2 to 4, split index (`sharedindex.*`) and sparse entries. Any entry whose path has a `.git` component,
  matched the way git's own path check does (case-insensitively, ignoring the characters HFS+ ignores, and the
  8.3 short name `git~1`), is a "not holding" finding.
- It is removed by a job run as the project (`project_git`: the index rewritten without those entries), never by the
  platform writing the project's index as the owner.
- This is **detection with a window, not prevention**: the index is the project's to write (every commit writes it),
  so between a write and the next scan the owner's restore or checkout of the index can still be reached. The scan runs
  every 15 minutes and before every runner op; the status row says so.

**B2, links at names git writes.** `logs/HEAD`, `FETCH_HEAD`, `COMMIT_EDITMSG` and the like, replaced by a symlink to
any file, make the owner's git write that file (the name is writable by design, so the deny list doesn't cover it).
Folded in:
- A Seatbelt rule: no symlink is created anywhere under a `.git/` folder (`file-write-create` with `vnode-type SYMLINK`),
  except under `.git/brunna/` (the platform's folder: uv's cache makes links).
- `logs`, `refs` and `objects` are pinned as entries too (added to the fenced names), so a folder holding links can't be
  swapped in for them.
- The scan walks every entry of every git dir and reports any symlink and any file with `nlink > 1`.
- A per-runtime canary: a symlink created at `.git/FETCH_HEAD` and `.git/logs/HEAD`, expected refused. A runtime whose
  rules can't express "no symlinks under `.git`" can't hold the fence, and its sessions keep the project unfenced.
- Residual: a folder holding a link, made elsewhere and moved in deeper than the pinned entries (into a folder git writes
  in, below `logs/refs/heads/`, say). The scan finds it; nothing prevents it.

**B3, the commit gate ran the project's commands as the owner. Built 11-14, revised after its code review.**
`commit_gate` and `tests` aren't owner-only registry fields, so a sandboxed project could set them by manifest.update,
and the platform's pre-commit hook then ran `brunna test --index`, whose tiers are `/bin/sh -c <run>`, as whoever
committed: the owner's next commit from a terminal or editor ran the project's commands as the owner. As built:
- **Which project, by folder.** `bin/test-runner.py` decides the project from the folder it starts in, before any git:
  the sandboxed project whose workspace, runtime folder or `allow_write` holds it (`claimant`). A sandboxed project
  writes its repo's pointers, and the first build took the project from git's common dir, so a `.git/commondir` aimed at
  another project's git dir (or the hub's) made the runner take that project and skip the wrap (review F1,
  reproduced). After git has named the repo, the run is refused when git's common dir is outside that project's
  folders or names another project, and a sandboxed project's checkout outside its folders (a tool's own worktree
  folder, which its sandbox can't read) is refused with that said, rather than failing inside the sandbox.
- **The wrap.** For that project, unless the process already runs in a sandbox that holds, `isolation.run_as_project`
  runs the same command through `sandboxed-run` as the project. Everything after (the export, which runs git on the
  repo, `cmd:` inputs, `uv sync` / `npm ci`, the tiers) runs in the project's sandbox. The commit gate, `merge-branch`
  and a person running `brunna test` all go through it.
- **A fixed environment** (review F2, reproduced: a token in the owner's shell reached the tier): the wrapped run gets
  only `PATH`, the locale, `HOME`, `TERM`, git's hook variables (`GIT_INDEX_FILE`, `GIT_DIR`, `GIT_WORK_TREE`, the
  `GIT_CONFIG_*` two), the platform's own settings variables and the mark. A sandboxed project's `env:` inputs pass
  nothing (their names are the project's to choose and no owner-only field names allowed ones), and the run says so.
- **"Already in a sandbox that holds"** (review F4, reproduced: a permissive `sandbox-exec` profile skipped the wrap):
  `isolation.confined_now` needs the adapter's own check (Seatbelt's `sandbox_check`; on Linux `no_new_privs` plus a
  seccomp filter, and the mark, since Flatpak, bubblewrap and hardened units show the same) **and** a probe of the
  REQUIRED isolated-reads guarantee: the platform's state and secrets folder can't be opened from here. Inside some
  other sandbox the run is wrapped; on macOS a profile can't be applied inside another one (`sandbox-exec` exits 71,
  measured), which the runner reports as its own refusal. Landlock stacks, so on Linux nesting is safe.
- **Refusals** say "… can't be done here (why); nothing was run", exit 2, so a commit stops; never sandboxed-run's 78
  or its "service or job" wording. The mark only stops a loop: outside a sandbox it is a refusal, never a reason to skip
  the wrap.
- The hook installer takes nothing from the project: the hooks' texts are the platform's fixed text chosen by exact
  field values, and they exec only the platform's own scripts (tested with hostile values in every field a project
  sets).
- Tests: a sandboxed project's tier can't read the platform's state whether `brunna test` or an owner's `git commit`
  through the hook starts it (an unsandboxed project's can, the positive control); an owner's token doesn't reach it;
  a planted `commondir` is refused and the other project's tier doesn't run; a checkout outside the folders is refused;
  under a permissive profile the tier never runs as the starter; a forged mark is refused; `run_as_project`'s cases;
  Seatbelt's own check inside and outside a profile. The wrap tests skip where the test process is itself sandboxed.
- A project in `service_sandbox_exempt` still runs its tiers as the user, at the owner's commit too: the owner's
  exemption, as for its jobs (said in AGENTS.md).
- "Fence holds" gains: no platform hook runs project code unsandboxed (the commit gate as built; any later hook the
  same way).

**B4, the URL rule.** The runner's remote and submodule URLs come only from an owner-set registry field (the origin and
declared remotes, owner-only like `paths`), never from the request. The runner never fetches, and submodule update is
not one of its ops.

**S1, a git-dir-shaped folder in the temp area.** The owner's git run anywhere below such a folder finds it. Folded in:
- A Seatbelt rule on the entry name `HEAD` (non-directories only) outside git dirs, with exceptions only at the exact
  positions git uses: `/.git/HEAD`, `/.git/worktrees/<n>/HEAD`, `/.git/modules/**/HEAD` (and the per-run TMPDIR of S3).
- The scan extends to the temp roots (git-dir-shaped folders there are reported too).
- `safe.bareRepository=explicit` in the owner's global git config becomes a precondition of "fence holds" (question 2).

**S2, neutralising.** Automatic only at the one-time install. After that the scan reports and never strips; it never
strips `extensions.*` or the repository-format keys whatever happens (a repo with them removed can be misread). The
allowlist gains the inert `extensions.refstorage`, `maintenance.*` and `extensions.worktreeconfig`.

**S3, scratch repos.** Question 1, recommended (b) limited to the test runner's per-run TMPDIR: the fence's `.git` and
`HEAD` rules get one exception, below `<run folder>/tmp/` of a test run, which the runner removes afterwards. A repo
made there can be moved into the workspace before that (a rename's source can't be pinned without stopping the tests'
own `rmdir`, measured), so for it the scan is the control.

**S4.** The runner's and the scan's copies of project files go in `hubconf.private_tmp()`, never the system's temp
folder (every sandboxed project writes that).

**S5, reading project-writable files.** The scan opens every file with `O_NONBLOCK` and `O_NOFOLLOW`, checks with
`fstat` that it is a regular file, reads at most a size cap, and runs under a timeout. Found while checking: cap-runner
read a request file, which the project can swap in its own folder between the check and the read, with a blocking open:
a FIFO there would have held the shared service. **Built 11-14** as its own fix, completed after its code review
(F3: three more opens, all reproduced): every read in a job folder goes through the folder's descriptor without
blocking and reads only a regular file, checked before any read (the request; `state.json` in `recover()` at start-up,
which a FIFO had kept from ever coming up, and in `prune()`, whose age is now taken without following a link); and
`write_state` makes `.state.tmp` fresh and exclusively instead of opening whatever is there for writing. Tested: a FIFO
with and without a writer, a folder renamed into place, a link at `state.json`.

**S6, `worktree add`.** The runner chooses the location; the request names only a worktree name. The runner walks the
path component by component without following links (directory descriptors) and requires that the target doesn't exist.

**S7, a way back.** Un-flipping a project's switch removes the fence from every writer's rendering and moves brunna's
ops back to jobs of the project; `brunna git-fence restore <project> [--at <time>]` puts back what the install or a
neutralising changed, from `.state/git-fence/<project>/`, and reports what it restored.

**Nits, each taken:**
- The runner installs a config through git's own `config.lock`, created exclusively (`O_EXCL|O_NOFOLLOW`) in `.git`,
  then renamed over `config`: the lock git itself honours, so a concurrent git of the owner's waits or fails.
- The neutraliser never follows a link at any level (directory descriptors all the way down).
- The fence is a guarantee in each adapter's `guarantees()` (`git-config-fenced`), so `isolation` and doctor say which
  machine's adapter holds it; Landlock doesn't declare it.
- The cap-runner `git` kind serves every fenced project, not only those declaring capabilities.

**Order, revised:** ship now B3 (done) and the report-only scan, extended with index entries (B1), links and
`nlink > 1` anywhere in git dirs (B2), git-dir-shaped folders in the temp roots (S1) and the commit gate's status
(B3). The rest as planned above.

### Step 1 as built (11-14): the scan, report only, a best-effort check

`bin/git_fence.py`, the platform job `brunna/git-fence`. Revised the same day after two blind code reviews, each of which
found places where it said "holds" while the owner's git ran a planted canary: the first a folder it couldn't list, a
gitfile into a pointer it didn't check, git-dir shapes it didn't recognise, an index read differently from git; the
second pointer files with whitespace git keeps, index entries outside the `.git` names, a split index under another
spelling, a worktree index read with the wrong hash length, a hard link under a branch named `objects`, credentials cut
before they were stripped. All are covered below and by tests. It changes no project file and neutralises nothing.

**What it can claim (r3).** The scan re-implements the parts of git it reads, and each review found more ways that
re-reading can differ from git's own. So it claims no guarantee: its rows say "no known problem found (a best-effort
check)" or "problems found", never "holds", and nothing (the Codex confined check included) takes "no known problem" as
proof. A finding is a known problem and is acted on; its absence proves nothing. The guarantee has to come from the
fence preventing the writes (the Seatbelt rules, each runtime's canary, the one-time install), not from detection.

- **When:** every 15 minutes (r2 B1's window: an index can be written between two scans), in the background; the temp
  area at most once a day, its last result kept in between. The status page section and doctor say the window ("a
  change can stand that long before it is seen"). `--apply` takes no narrowing option (`--project`, `--no-temp`,
  `--temp-root` are refused), so a partial scan never ends findings it didn't look at.
- **Where:** per sandboxed project, its workspace, runtime folder, `allow_write` and the memory clone its workspace
  links to, walked without following a link, each root itself tested too. **A folder it can't list is a finding**
  ("can't be listed"), in the walk and in every git dir, never skipped. The walk is breadth first; what it found
  is still inspected when it stops, with a budget of its own. The time limit (five minutes per project) and the
  entry count (400,000) are checked at every folder entry, a stop said as "incomplete"; at most 300 findings are kept per
  project, the rest counted.
- **`.git` entries:** the repo's own git dir at a folder's top; a gitfile is accepted **only when git reads it as a pointer
  (`gitdir: ` with its space, only trailing CR and LF trimmed, a NUL ending it; any other whitespace, a NUL, or no
  prefix is a finding) and it leads exactly to a git dir this scan inspected** (a worktree's admin dir, a module's git dir); anything else is a finding (a nested repo,
  a gitfile elsewhere, a link named `.git`). **Git-dir shapes** follow git's own test, loosened to what can be planted:
  HEAD (any kind of entry) with objects and refs of any kind (a link, an executable file), or HEAD with a commondir.
- **Each git dir** (a repo's own; each `worktrees/<n>`; every folder under `modules/`, a worktree's `modules/` too, a
  folder there being inspected when it has HEAD, a config, a commondir or an index, walked into otherwise, since a
  module's name can hold slashes):
  - config sources through the allowlist above with r2 S2's additions and these, each with its value rule:
    `core.autocrlf` (true, false, input), `remote.<n>.gh-resolved` and `branch.<b>.vscode-merge-base` (a ref-like
    token: tools' bookkeeping, read by no git command), `branch.<b>.description` (printable text, used only in a cover
    letter), `lfs.repositoryformatversion` (0) and `lfs.locksverify` (a bool). Every other `lfs.*` is reported: `lfs.url`
    and its kin send the owner's credentials and objects where they name. In a module's git dir, the `core.worktree` git
    writes there is allowed when it leads to a folder in the project whose `.git` file leads back, and its
    `remote.origin.url` follows the URL rule. A path URL passes only when it is **this project's own memory bare repo**
    (`policy.memory_bare`); any `include*` is a finding, never followed; `core.hooksPath` has its own;
  - hooks other than git's samples and the platform's own text **in any UI language** (a hook's message is in the
    language the platform had when it wrote it);
  - `info/attributes`, alternates, a `commondir` (in a repo's or a module's git dir: any; in a worktree's: anything but
    `../..` read as git reads it, a missing one included), a worktree `gitdir` that doesn't lead back (a relative one, from
    `worktree.useRelativePaths`, resolved against the admin dir), every entry of `remotes/` and `branches/` (files there
    are remotes git reads);
  - every symbolic link and every file with more than one name in the whole git dir (worktrees' and modules' included),
    except under the platform's own folder (`.git/brunna/`, its caches) and the files under the `objects/` of a repo's
    or a module's git dir (that folder itself, not any folder of that name), which git links between local clones and
    checks by their hash;
  - the index entries the scan reports (r2 B1): every index (a worktree's, a module's), each read once (by device and
    inode), read as git's own reader reads it (the name length as git reads it, the extended flags wherever set, the
    entries ending where the extensions begin, the checksum verified, with the hash length of the repo's common dir
    config, which a worktree's index shares; with an entry-offset table, each block read again as git's threads would
    and the union kept); a split index's shared part opened as git opens it (the name its `link` extension gives),
    and any `sharedindex.*` in any case; an entry git's own path check would refuse is reported, whatever its name;
    one git couldn't read is "can't be checked";
  - rebase state with exec lines, said as a note.
- **The commit gate** (B3): on or off, the platform's pre-commit in place, exempt or not, and whether this machine can
  sandbox, in each project's row.
- **The temp area** (S1): the user's own temp folder and the shared one (macOS: the user's folder under
  `/private/var/folders` and `/private/tmp`; Linux: `/tmp`, `/var/tmp`), breadth first to eight levels, a million
  entries and the time limit, each cut said. A `.git` at a root is a finding whatever it holds (it makes the whole area a
  checkout); every other repo there is inspected as a project's is (config, hooks, pointers, index), so a clean one left
  by a test isn't a finding and one that would run something is; folders shaped like a git dir; a folder of the owner's it
  can't list (another user's is skipped). The temp area is on the status page only, with no alert and no doctor row,
  until the fence's `HEAD` rule (S1) exists: nothing in a project can stop it yet.
- **How it reads:** every file opened without following a link and without blocking, a regular file only (fstat), at
  most 1 MB (256 MB for an index); a config source is copied into a folder of its own in `hubconf.private_tmp()` and read
  with `git config --file <copy> --null --list` from there, with no system or global config and a timeout (git's error
  text shown without the copy's path). No git runs in a project's repo, so nothing of the project's runs, and no git
  facts are needed through `project_git`. Paths are opened by path, not through descriptors held from the walk (a git
  dir's `info`, its `rebase-merge`, the commit gate's `.git`): a link at one is itself a finding of the same scan, but
  one made between two steps can make the scan read a file the owner can read (only parsed, and named in the report).
- **Output:** `.state/git-fence/scan.json` (no URL credentials kept: a value's `user:token@` is left out); a status page
  section and a doctor row per project and one for the temp area; an alert through `alert.once` (one stable id per
  project finding; the temp area has none). Every value from a project's files is shown with control and invisible characters
  escaped and an `@` broken by a zero-width space (no terminal escape, no chat mention), in `--report`, doctor, the
  status page and alerts. `bin/git_fence.py --report [--project P] [--json] [--no-temp] [--temp-root D]` scans and
  prints without writing anything.
- **Tests** (`tests/test_git_fence.py`, a throwaway instance, the scan as its own process): a clean repo with a linked
  worktree has no known problem; a hostile one is reported in full and its planted canary doesn't run (the positive control: it runs on
  a plain `git status`); nothing in the project changes (every entry's mode, size, mtime and inode before and after);
  folders it can't list (a git dir's hooks, a folder holding a nested repo); `remotes/` and `branches/` files; gitfiles
  into a module whose commondir points away and into a folder deeper than a worktree's admin dir, and a folder under
  `modules/` that isn't shaped; shape variants (folders, files, links, commondir) and a root that is itself shaped; a
  the temp area (a `.git` at a root, a clean repo below it not a finding and one with a key the allowlist lacks one, a
  folder of the owner's it can't list, no alert); pointers git reads differently (a gitfile's and a worktree commondir's
  whitespace, a NUL, a missing prefix); a split index opened by its `link` under another spelling; a sha256 repo's
  worktree index; a hard link under a branch named `objects`; credentials stripped before a cut; the index entries the scan reports, an index git couldn't read, the name
  length as git reads it, extended flags, the checksum, extensions that don't end where they should, against what git
  itself writes in versions 2 to 4 (a long name included); a FIFO where a hook or config belongs; the deadline
  ("incomplete"); the commit gate with the platform's hook in each language and a changed one; rebase notes; the job's
  result, its once-a-day temp walk, its refusal of narrowing options, and its alert received by a stand-in channel; the
  allowlist (its additions, another project's memory repo refused) and the escaping of shown values.
