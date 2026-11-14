# Git through the platform: a sandboxed project never writes a git dir

Status: design, 11-14, on measurements made the same day; nothing built. It takes over the prevention half of
`git-config-fence.md` (the per-name fence, the narrow `brunna git` runner and the one-time install). That doc's scan stays
as a second layer, and so do its config allowlist and its presets. Measured in scratch repos (git 2.50.1, macOS on a
case-insensitive APFS volume). A sandboxed writer was stood in for by the platform's real Seatbelt profile
(`seatbelt.service_profile`), with the candidate rules appended. Codex 0.167's own sandbox was run through
`codex sandbox` (no model). Claude Code was read from its installed binary and not run. There were no model calls.

## The problem and the owner's decision

A sandboxed project's sessions, services and jobs may write their project's `.git` today. Whatever is written there,
the owner's unsandboxed git later reads and runs in that folder: their terminal, their editor, git apps, and the coding
agents' own git. Codex and Claude Code both run git as the user, outside their sandboxes (`runtime-codex.md` B1;
"Claude Code" below). `git-config-fence.md` tried to fence the dangerous names one by one and to detect the rest.
Two blind reviews (its r2 and r3) showed the same thing: detection that re-implements git's parsing always misses
something. So the guarantee has to come from preventing the writes. The index can't simply be fenced, because the
sandbox must commit.

**The owner's principle (11-14):** a user never has to remember a special way to use their agent. Any tool opened
normally in a sandboxed project must be safe and get full brunna: the Codex app or CLI, Claude Code, their editor, a
terminal, a git GUI.

**The owner's decision (11-14):** git goes through the platform. In a sandboxed project, the sandbox can't write any git
dir at all. The `git` an agent runs inside the sandbox is a platform wrapper on its PATH. The wrapper forwards the
command to a platform service outside the sandbox, which runs the real git with safe settings and an allowlist. So a git
dir is only ever written by real git under the platform's control, or by the owner. Agents and the owner keep typing
ordinary git commands.

## What was measured (11-14)

### 1. How agents call git

**Claude Code** (read from the binary; needs a run to confirm):
- Its own git calls look the program up on PATH. There is a check that refuses a `git` found inside the working folder,
  so a wrapper in the platform's own folder would be accepted.
- They run with `-c core.hooksPath=/dev/null -c core.fsmonitor=` and `--no-optional-locks` (status, worktree add).
- Some calls also set `GIT_NO_REPLACE_OBJECTS`, `GIT_TERMINAL_PROMPT=0`, `GIT_SSH_COMMAND=false` and
  `GIT_LFS_SKIP_SMUDGE=1`, plus `safe.bareRepository=explicit` and `protocol.file.allow=never`, and they strip
  `GIT_DIR` and its kin.
- **Nothing in them turns filters off.** A clean filter in the repo's config would run from Claude's own status call,
  as the user, which is B1 again (Codex's case). Not run.
- Claude's worktree mode creates worktrees with its own git, as the user, outside the sandbox.
- The Bash tool's shell gets `GIT_EDITOR=true`.
- The sandbox's `allowUnixSockets` takes paths on macOS only ("ignored on Linux", says its own schema). Unix sockets are
  denied unless listed.
- The binary carries a list of git-dir names (`config.lock`, `config.worktree`, `commondir`, `remotes`, `branches`,
  `worktrees`, `alternates`) and a flag-gated "allow git config" switch for its sandbox. So Claude may already protect
  some of these itself. The platform doesn't rely on it.

**Codex** (`codex sandbox`, workspace-write, 0.167):
- The top repo's `.git` is read-only throughout, `.git/brunna/` included.
- `sub/.git` (a folder), a `.git` gitfile elsewhere and a file named `HEAD` were all created.
- A unix socket in the workspace was refused with the network off and **reachable with it on**, with no path rule.
- Codex's own git (the daemon's `status`, `rev-parse`, `remote`, `/diff`) runs as the user outside the sandbox
  (`runtime-codex.md` B1). It never goes through a wrapper on the tool shell's PATH, and the platform doesn't own the
  daemon's PATH.

**Tools that bundle a git library** (libgit2/pygit2, dulwich, isomorphic-git, go-git, JGit) write `.git` themselves.
Under the fence below they fail with "Operation not permitted", and their reads still work. **None was tried.** The
tools that shell out to `git` (pip and npm git dependencies, `gh`, pre-commit, uv) reach the wrapper. That too is
unmeasured, beyond the fence doc's uv and pip results.

### 2. Denying every git dir to a sandboxed writer (Seatbelt)

These rules were appended to the service profile:
- no write to any entry named `.git` or below it, matched anywhere: a regex on `/\.git(/|$)`;
- no entry named `HEAD`, anywhere;
- the platform's folder `<repo>/.git/brunna/` allowed again, with its own entry pinned and no `.git` below it.

**Refused** (`Operation not permitted`):
- writing `.git/config`, `git config`, a hook, `.git/info/attributes`;
- `git init sub`, `mkdir sub/.git`, `mkdir sub/.GIT`, a gitfile `sub/.git`;
- `git init` in the temp area, `git init --bare x.git` there (by the `HEAD` rule), a git-dir shape `shape/HEAD`;
- `mv .git g`;
- a hard link to `.git/config`, in the workspace and inside `.git/brunna`;
- a symlink in `.git/brunna` aimed at `../config`, written through;
- renaming `.git/brunna` (pinned);
- a linked worktree rewriting its own `.git` file;
- `git commit`, `git stash`, `git worktree add` with real git;
- reading another project's file.

**Worked:**
- real git's `status`, `log` and `diff` (exit 0; status just skips its opportunistic index refresh);
- status in a linked worktree;
- writing the outbox under `.git/brunna`;
- a test-runner style export with the index and the object folder both in temp (`GIT_INDEX_FILE` and
  `GIT_OBJECT_DIRECTORY` in temp, the repo's objects as an alternate: `read-tree`, `add -A`, `write-tree`,
  `checkout-index`). It writes nothing in a git dir.

**Found:** uv's cache marker (`.git/brunna/cache/uv/sdists-v<N>/.git`) is refused by the `.git` rule. The fence doc's
pinned exception for it carries over unchanged.

### 3. The prototype broker (scratch, not committed)

The pieces:
- **The wrapper**, `git`: a small Python program put first on the sandboxed shell's PATH. It sends argv, cwd, selected
  environment and stdin over a unix socket and streams stdout, stderr and the exit code back.
- **The service**, outside the sandbox. Local operations run under a **writer profile**: the project's own profile,
  plus write access to this repo's git dir, minus the names git runs or reads elsewhere (`config*`, `commondir`,
  `gitdir`, `hooks`, `info`, `modules`, `remotes`, `branches`, the alternates files, worktree admin pointers). The
  entries `objects`, `objects/info`, `refs`, `logs` and `worktrees` are pinned. Network operations to the registry's
  remote run as the owner.
- The hardened invocation: `-c core.hooksPath=/dev/null -c core.fsmonitor=false -c core.pager=cat
  -c protocol.ext.allow=never -c credential.helper= -c safe.bareRepository=explicit -c submodule.recurse=false`,
  `GIT_CONFIG_NOSYSTEM=1`, a platform-written global config, `GIT_TERMINAL_PROMPT=0` and `GIT_EDITOR=:`.

Through the wrapper, from inside the fenced sandbox:
- **Worked:**
  - `status`, `diff`, `add`, `commit -m`, `commit -F -` (stdin);
  - `log`, `show`, `branch`, `switch -c`, `checkout`;
  - a 3-way `merge`, `rebase`, `stash`/`pop`, `reset --hard`, `restore`, `cherry-pick`, `tag`, `blame`;
  - `git -C sub`;
  - `worktree add` inside the project, a commit in that worktree, and `worktree remove`. Remove needed `unlink` on the
    admin pointers; a first version that denied them failed;
  - `fetch` and `ls-remote` of the registry's remote (as the owner); `push origin main`;
  - `pull` (the fetch as the owner, the merge under the writer profile);
  - `init` in the temp area followed by a commit (the repo made by the platform with an empty template);
  - `hash-object -w --stdin` with 300 KB of binary data; `git apply` of a binary patch from stdin;
  - `gc` (one warning: `info/refs` sits under the denied `info`, so only `info/attributes` should be denied there);
  - a 20,000-commit `git log`, streamed in 0.12 s (direct: 0.08 s).
- **With the editor set to `:`** (Claude's `true` does the same):
  - a non-fast-forward merge without `--no-edit`, a conflicted `rebase --continue` and a conflicted
    `cherry-pick --continue` all worked;
  - `commit` without `-m` and `tag -a` without `-m` stopped on an empty message, as they do under `GIT_EDITOR=true`.
- **Broken or incomplete in the prototype:**
  - `push -u`: the push lands, the upstream isn't recorded (config is the config op's);
  - `clone`: the fetch worked; the checkout tried to write tracking config and failed. It needs the
    `branch.autoSetupMerge=false` preset;
  - a clone of another project's repo was refused by the project's own read rules, as it should be.
- **Not tried:** `add -p` / `-i` and anything else needing a TTY; credentials against a real remote (the prototype
  cleared the credential helper); `submodule` (refused); merge-branch and the memory sync through the wrapper.

**Latency** (30 calls each, small repo):

| path | `rev-parse HEAD` | `status --porcelain` |
|---|---|---|
| direct, unsandboxed | 9.4 ms | 10.2 ms |
| real git inside the fenced sandbox | 9.6 ms | 10.8 ms |
| through the wrapper and the broker | 36.8 ms | 38.1 ms |

Of the broker path's extra ~27 ms, the wrapper's Python start is 9 ms and applying the writer profile with
`sandbox-exec` is 8 ms.

### 4. Arguments and environment

- **The writer profile carries the guarantee; the argument filter is a second layer.** With the filter turned off and
  only an **exec guard** in the writer profile (`(deny process-exec)`, then allow only `/usr/bin/git`, the Command Line
  Tools' `git` and git's exec-path):
  - `rebase --exe '<cmd>'` (an abbreviation git accepts) and `rebase -x'<cmd>'` stopped the rebase without running the
    command;
  - `grep -O'<cmd>'` failed with "cannot exec";
  - every canary stayed absent.

  The same guard let commit, merge, `rebase --continue`, cherry-pick, stash, gc and worktree add/commit/remove work.
  For gc, the one warning came from the prototype denying all of `info/`.
- **The guard stops a program from running under the broker. It doesn't stop git from writing the request into its
  own state.** A `rebase --exe '<cmd>'` over two commits, run with the filter off:
  - stopped at its first `exec` line, without running it;
  - left `exec <cmd>` in `rebase-merge/done` and in `git-rebase-todo`.

  The owner's own unsandboxed `rebase --continue` then **ran the canary**. So keeping `exec` lines out of state the owner
  might continue rests on the allowlist (exact spellings, abbreviations off) and on the broker's check after a stop
  (below), not on the guard.
- **Git's own abbreviation knob works:**
  - `GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS=true` turned `rebase --exe` into "disallowed abbreviated or ambiguous
    option";
  - `log --outp=…` is "unrecognized" in any case (diff-style options don't abbreviate).
- **Git's own path checks hold for the ways tried:**
  - `update-index --cacheinfo` refused `.git/x`, `.GIT/x`, `sub/.git/config` and `git~1/config`;
  - `update-index --index-info` ignored `.git/x` and `sub/.GIT/config` ("Ignoring path");
  - `read-tree` of a hand-made tree holding `.git/config` or `.GIT/config` failed with "invalid path".

  `mktree` does accept a `.git` entry: the object can exist, but it doesn't reach the index.
- `update-ref CONFIG HEAD` doesn't reach `.git/config` on this case-insensitive volume. Unsandboxed, git refused it
  because the existing file isn't a ref. Through the broker, the writer profile refused `CONFIG.lock` first: its ASCII
  rule matched the folded name, as the fence doc found.
- **The environment:** a caller's `GIT_DIR`, `GIT_TRACE=<.git/config>` and `GIT_EXTERNAL_DIFF` were dropped by the
  prototype, and nothing ran. Dropping them silently changes what a command does, though, so the design refuses or
  runs in place instead (below).

### 5. Identity

- **The socket is not the identity.**
  - With no unix-socket rule, project A's sandbox connected to project B's socket. The service profile allows outbound
    unix sockets, and Seatbelt's read denial of B's folder doesn't cover `connect()`.
  - A forged header naming B's folder then got B's file back.
  - A socket in a read-denied platform folder was reachable too.
- **A per-sandbox socket rule works where the sandbox can say it:** `(deny network-outbound (remote unix-socket))` plus
  an allow for the project's own socket path refused B's socket with EPERM. Claude's `allowUnixSockets` is the same
  kind of rule. Codex can't say it: with its network on, any socket is reachable.
- **A nonce in the project's own folder works in every sandbox.**
  - The broker sent a random nonce. The caller had to create a file of that name in the project's request folder
    (`<workspace>/.hub-run/git/`), which the broker read through no-follow directory descriptors (cap-runner's
    rule).
  - A's sandbox couldn't prove B, either by writing the nonce in its own folder or by aiming at B's folder.
  - B's sandbox could.
- **The kernel's view of the peer** (`LOCAL_PEERPID`, then `sandbox_check` on that pid; a private API, called from a
  small C helper since the call is variadic):
  - it told A's sandbox apart from an unsandboxed process, and answered correctly for reading or writing existing
    files (A may write its own file, may not read B's);
  - it answered "denied" to `file-write-create` even for an unsandboxed process;
  - it is pid-based.

  Not relied on.

### 6. The owner's tools afterwards

Each run planted the same set:
- a clean filter in `.git/config` with `.gitattributes` selecting it;
- `core.fsmonitor`;
- a `post-index-change` hook;
- `info/attributes`;
- a nested repo with its own fsmonitor.

The owner's git then ran `status`, `add`, `commit` and `diff` in the repo, and `status` in the nested repo:

| who wrote the plants | canaries that ran |
|---|---|
| an unfenced sandbox (the positive control) | filter, fsmonitor, hook, nested |
| a fenced sandbox, with real git and the shell | none |
| a fenced sandbox, through the wrapper (`git config …`, `git -c core.fsmonitor=… status` included) | none |

## The design

### The pieces

1. **The fence:** nothing in a sandboxed project's sandbox writes a git dir, at any depth, anywhere it may write.
2. **The wrapper:** the `git` that sandboxed processes find on PATH. Outside a sandbox it execs the real git unchanged.
3. **The channel:** a unix socket per project, plus a nonce proof of identity.
4. **The broker:** a platform service (`local.brunna.git-broker`, in brunna's registry services like cap-runner). It
   checks a request, then runs real git:
   - local operations under the project's **writer profile**, as the project;
   - network operations to the registry's remotes as the owner, with fixed arguments;
   - config changes through one allowlisted op.
5. **The policy port:** `policy.py` gains neutral entries. Each sandbox adapter renders them.

### The fence (what every sandbox of a switched-on project denies)

In neutral terms, `policy.git_fence`:
- **any entry named `.git`**, as the entry and everything below it, wherever the project may write: its workspace, its
  runtime folder, `allow_write`, the temp area. Seatbelt checks a rename only at its two ends, so a tree built elsewhere
  and moved in must hit the rule wherever it was built (the fence doc's measurement);
- **any entry named `HEAD`**, so no git-dir shape can be made under another name (the fence doc's S1);
- **one writable folder per repo**, `<common dir>/brunna/`:
  - its own entry is pinned (it can't be renamed or removed);
  - no `.git` below it, except uv's pinned marker (`git_fence_pinned`, as the fence doc measured);
  - it holds the outbox, the test runner's store and the tool caches, as today.

Each adapter renders it:
- **Seatbelt** (services, command jobs, cap-run): the measured regexes. A guarantee `git-dirs-fenced` in
  `guarantees()`.
- **Claude sessions** (`runtime_claude.settings`):
  - Edit denies and `denyWrite` on `**/.git` and `**/.git/**` under every write root, and on `HEAD`;
  - the `.git/brunna/` exception;
  - the socket in `allowUnixSockets`.

  To measure before relying on it: whether a glob denies the entry and everything below it, at any depth; whether the
  exception wins; and folded spellings. A canary in `conformance_claude.py`.
- **Codex sessions** (the rendered profile, `runtime-codex.md` Step 4): `deny` globs on `**/.git`, `**/.git/**` and
  `**/HEAD`, which r2 measured to work for nested names. Codex already makes the top `.git` read-only (measured), but
  not nested ones, gitfiles or `HEAD`. Its `.git/brunna/` exception needs a measurement: today Codex refuses the
  outbox there too. A canary in `conformance_codex.py`.
- **Landlock (Linux):** it can't deny a name inside a writable tree. Unsupported (see "Linux").

### The wrapper

- A platform program in a folder that holds only `git`, which no sandbox can write (beside the CLI wrapper, under
  `policy.cli_dirs`). It is put first on PATH for sandboxed processes: `sandboxed-run` for services and jobs, the
  session environment for each runtime (below).
- **`brunna git <args>`** is the same program under the CLI, for any agent or tool whose PATH the platform can't set.
- **Outside a sandbox** (`isolation.confined_now()` false, as the owner's tools and Claude's and Codex's own git are), it
  execs the real git with nothing changed.
- **Inside a sandbox**, it chooses one of three paths:
  - **In place** (real git, inside the sandbox, no broker):
    - a fixed set of read-only forms: `status`, `diff`, `log`, `show`, `blame`, `grep`, `rev-parse`, `ls-files`,
      `ls-tree`, `cat-file`, `for-each-ref`, `show-ref`, `describe`, `merge-base`, `rev-list`, `name-rev`,
      `config --get*` / `--list`, `branch` / `tag` / `stash` / `worktree` / `remote` / `reflog` in their listing forms;
    - every invocation whose environment selects a repo, index or object folder (`GIT_DIR`, `GIT_WORK_TREE`,
      `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`, `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_COMMON_DIR`). Such a call runs
      unchanged, and if it tries to write a git dir it fails loudly with the sandbox's refusal. It is never forwarded
      with those variables dropped.

    Measured: real git's reads under the fence cost 10 ms against the broker's 37 ms, and they run as the project, so
    there is nothing to argue about.
  - **Forwarded:** everything else, to the broker.
  - **Refused loudly** (exit 2, "can't be done here (why); nothing was run"): a forwarded call carrying a variable that
    names a program or a file git would write (`GIT_EXTERNAL_DIFF`, `GIT_SSH*`, `GIT_ASKPASS`, `GIT_EXEC_PATH`,
    `GIT_TEMPLATE_DIR`, `GIT_CONFIG*`, `GIT_TRACE*` naming a file, `GIT_PROXY_COMMAND`).
- **When the broker is down**, writes say so and fail; reads still run in place.
- It is small and starts fast. The prototype's Python start was a third of the overhead, so a compiled helper or a
  shell front for the in-place forms is an implementation choice.

### Identity

The project a request is for is the project whose own folder the caller can write. Never a path or name the caller
sends. In order:

1. **The socket the caller can reach.** Each project's socket lives in a platform-owned folder
   (`<state>/git-broker/<project>/`). Every sandbox that can name socket paths may connect only to its own project's
   socket: Seatbelt's rule (measured); Claude's `allowUnixSockets` (unmeasured, but it is the same kind of rule).
2. **The nonce, which holds in every sandbox.**
   - On connect, the broker sends a random nonce. The caller creates `<workspace>/.hub-run/git/<nonce>`.
   - The broker opens the workspace, `.hub-run` and `git` through no-follow directory descriptors and checks for a
     regular file there. Only then does it read the request. The nonce file is removed.
   - This is cap-runner's rule ("the folder a request is found in is the project"), made per connection. It is what
     holds for Codex, whose sandbox reaches any socket when its network is on (measured).
3. **The kernel's view of the peer** (`sandbox_check`) is not used. It is a private API, pid-based, and some of its
   answers were unreliable (above).

An unsandboxed process can prove any project. It already has the owner's authority and could write that `.git` directly,
so this gives it nothing new. The broker then checks that the request's working folder, and any `-C`, resolve inside
that project's folders. It also checks that the repo's common dir is one the project owns: its workspace repo, its
worktrees, or a repo the broker made for it (recorded in `<state>/git-broker/<project>/repos.json`). Since no sandbox
writes a `.git`, the gitfiles and `commondir` pointers it reads to find that dir were written by git or the platform.

### What the broker runs, and as whom

- **Local operations** run under the **writer profile**: the project's own sandbox profile, applied by the broker from
  outside with `sandbox-exec` (cap-runner's way: a profile can't be nested inside an agent's sandbox), plus:
  - write to this request's common dir and, for a linked worktree, its admin dir;
  - minus the names git runs or reads elsewhere: `config*`, `commondir`, `gitdir`, `hooks/`, `info/attributes` (with
    `info` pinned), `modules/`, `remotes/`, `branches/`, `objects/info/alternates` and `http-alternates`, each
    `worktrees/<n>/{commondir,gitdir,config.worktree}`;
  - with `objects`, `objects/info`, `refs`, `logs` and `worktrees` pinned as entries;
  - **the exec guard:** no program runs but git's own binaries, and the platform's hook forwarder (below).

  So nothing project-chosen runs with write access to a git dir, whatever reaches git: a missed option, an alias or a
  todo line. **What git writes into its own state is a different matter**, and it isn't all inert: a rebase todo with
  `exec` lines runs on the owner's `--continue` (measured). Three things keep such lines out:
  - the allowlist (no `--exec` / `-x`, abbreviations off);
  - the editor callback's check of a returned todo;
  - **the check after a stop.** Whenever a broker-run rebase, cherry-pick or revert stops, the broker reads
    `rebase-merge/git-rebase-todo`, `rebase-merge/done` and `sequencer/todo`. These are git's own line format, written
    by git, not by the project. If any line is an `exec` or `x` command, the broker runs `--abort` under the writer
    profile and says why.

  Whether the sequencer of `cherry-pick` and `revert` accepts `exec` lines at all wasn't measured. Worktree content
  (`.gitattributes`, `.gitmodules`, symlinks) is read by git as the project, inside the project's own sandbox.
- **The broker's global config** (`GIT_CONFIG_GLOBAL`, a platform-written file; `GIT_CONFIG_NOSYSTEM=1`). It holds:
  - the owner's `user.name` and `user.email`, unless the project's own config has them;
  - `init.defaultBranch`;
  - `safe.bareRepository=explicit`, `protocol.ext.allow=never`, `protocol.file.allow=user`;
  - `core.hooksPath` (the forwarder's folder);
  - `core.fsmonitor=false`, `core.pager=cat`, `submodule.recurse=false`.

  It holds **no drivers** (`filter.*`, `diff.*.textconv|command`, `merge.*.driver`), **no aliases**, no
  `credential.helper` and no `include*`. A `.gitattributes` in the worktree can therefore select only drivers that
  don't exist, which git ignores (the fence doc measured this). An alias is a caller's shell habit: the wrapper expands
  none, and `git <alias>` is refused as an unknown subcommand.
- **Drivers defined in the owner's own global config** (git-lfs's filter, say) are the one place the two gits differ.
  The owner's git applies such a driver to the files the attributes select, and the broker's git doesn't. So a file the
  owner's git would store as an LFS pointer would be staged whole by an agent, and the two would show each other's
  copies as modified. **Unmeasured** (no git-lfs here). The choices are question 10: copy named drivers from the
  owner's global config into the broker's, where they run under the writer profile with the exec guard allowing that
  one program; or leave them out and say so in doctor.
- **Network operations** (`fetch`, `push`, `ls-remote`, and the fetch half of `pull`) to a remote the registry names run
  **as the owner, unsandboxed**:
  - fixed arguments: `GIT_DIR` set explicitly, the URL and refspec pinned with `-c` from the registry, no hooks,
    `--no-recurse-submodules`, `push --no-verify`;
  - the owner's credential helper and global config apply;
  - they touch only the git dir's objects and refs, never the worktree.

  `pull` = this fetch, then `merge` / `rebase` of `FETCH_HEAD` under the writer profile.
- **Fetch or clone from any other URL** runs under the writer profile: the project's own network, no credentials (the
  Keychain is blocked), its own read rules. `file://` and paths reach only what the project can read (measured: another
  project's repo refused). `ext::` and `--upload-pack` are refused, and the exec guard would stop them anyway.
- **Config changes** go only through the config op: the fence doc's allowlist table and value rules, installed through
  `config.lock` created exclusively, never in place. The broker's own writes use git's lock files, so the owner's
  concurrent git waits or fails as it would against another terminal.

### The allowlists

**Recommendation: an allowlist**, not a denylist. A denylist is the parser-re-implementation the fence doc's reviews
kept defeating. The exec guard and the denied names carry the guarantee. The allowlist keeps caller-chosen content out
of the files git reads back as instructions and leaves writable: the index (r2 B1), `rebase-merge/` and `sequencer/`
todos (exec lines run on the owner's `--continue`).

- **Subcommands.** Allowed: the in-place set above, plus `add`, `rm`, `mv`, `commit`, `restore`, `reset`, `switch`,
  `checkout`, `branch`, `tag`, `merge`, `rebase`, `cherry-pick`, `revert`, `stash`, `apply`, `am`, `notes`, `gc`,
  `fsck`, `count-objects`, `hash-object`, `update-index`, `read-tree`, `write-tree`, `symbolic-ref`, `update-ref`,
  `format-patch --stdout`, `range-diff`, `init`, `clone`, `worktree`, `fetch`, `pull`, `push`, `ls-remote`, `remote`,
  `config`, `version`, `help`. Everything else is refused, by name:
  - `filter-branch`, `difftool`, `mergetool`, `hook`, `bisect run`, `submodule` (question 5);
  - `daemon`, `instaweb`, `send-email`, `credential*`, `http-backend`, `upload-pack`, `receive-pack`, `shell`,
    `remote-*`;
  - `fast-import`, `archive --remote`, `bundle`, `replace`;
  - `maintenance start|register` (it writes the owner's global config and schedules jobs).
- **Options:** one table per subcommand of exact spellings and their arity.
  - `GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS=true` is always set, so git itself refuses abbreviations (measured).
  - Short clusters and sticky values are parsed against the table, never passed through.
  - `--` and `--end-of-options` end option parsing; pathspecs after them are free (git refuses paths outside the repo).
  - Absent on purpose:
    - every option that writes a caller-named file: `--output`, `-o` / `--output-directory`, `--index-output`, `--prefix`
      on `checkout-index`, `--export-marks`;
    - every option that runs a program: `--exec` / `-x`, `-O`, `--ext-diff`, `--textconv`, `--upload-pack`,
      `--receive-pack`, `--config`;
    - every option that selects another repo or work tree: `--git-dir`, `--work-tree`, `--separate-git-dir`,
      `--reference`, `--template`;
    - signing: `-S`, `--gpg-sign`, `-s` on tags (question 6).
- **Global options:** `-C <dir>` (resolved and checked), `--no-pager`, `-P`, `--no-optional-locks`,
  `--literal-pathspecs`, and `-c` for an inert-key allowlist only: `user.name`, `user.email`, `color.*`,
  `core.quotepath`, `advice.*`, `init.defaultBranch`, `rebase.updateRefs`, `merge.conflictStyle`, `diff.renames`,
  `commit.gpgSign=false`, `log.showSignature=false`. `merge-branch` uses `rebase.updateRefs`.
- **Composed operations**, where git would write config itself:
  - `push -u` and `branch -u` / `--set-upstream-to`: the ref part runs under the writer profile, the upstream through
    the config op;
  - `branch -m` / `-M`: the ref rename, then the config op moves the section;
  - `switch` / `checkout` of a remote branch, with the preset `branch.autoSetupMerge=false`; a requested `--track` goes
    through the config op;
  - `remote add` / `set-url`: only a URL the registry declares (the fence doc's B4);
  - `config <key> <value>`: the allowlist.
- **The environment** passed on: `GIT_AUTHOR_*`, `GIT_COMMITTER_*`, the pathspec variables, `GIT_MERGE_AUTOEDIT`, the
  locale, `TZ`, `NO_COLOR` and `TERM`. Everything else is either handled in place, or refused (the wrapper's list). It
  is never dropped silently.

### init, clone, worktrees and scratch repos

- **`init <dir>`:** the broker creates the repo itself, as the owner, with `git init --template=` (no hooks copied;
  `git init` runs nothing) and the presets, then records it under the project. `<dir>` must resolve inside the
  project's folders or its per-run temp folder (question 3). It must be new or empty.
- **`clone <url> <dir>`:**
  - the broker's `init`;
  - then `remote.origin.url` through the config op (a registry remote, or a URL that passes the value rule);
  - then the fetch: as the owner for a registry remote, otherwise under the writer profile;
  - then the checkout under the writer profile.
- **`worktree add <path> [-b <branch>]`:**
  - `<path>` must resolve inside the project's folders, outside every git dir, and not exist yet (the fence doc's S6:
    walked through directory descriptors);
  - the broker's writer for this op may create `worktrees/<n>/` with its `commondir` and `gitdir`, which git writes
    from the path the broker checked;
  - the checkout runs under the writer profile.
- **`worktree remove` and `prune`:** may unlink those files (measured: needed).
- **The tools' own worktree modes** (Claude's, the Codex app's) create worktrees with their own git, as the user, outside
  any sandbox. That reads a trustworthy git dir and writes what git writes. A worktree outside the project's folders
  (the Codex app's) is refused by the broker, as the test runner already refuses one.
- **Scratch repos** (test suites' `git init` in temp): the broker makes them, as for `init`, but only in the project's
  per-run temp folder. They are recorded, and dropped when that folder goes. The temp area is shared between projects,
  so a recorded repo of A's is never served to B.

### Hooks and the platform's gates

- `core.hooksPath` points to a platform-owned folder in which every hook name is one program, the **hook forwarder**.
  The exec guard allows it, and it does one thing: it sends the hook's name, arguments, stdin and `GIT_INDEX_FILE`
  back to the broker over the request's own channel, then waits for a verdict.
- The broker runs the actual check **outside the writer profile**:
  - the commit gate (`brunna test --index`, with that index) in the plain project sandbox;
  - the commit-msg title check in the broker itself;
  - `pre-push` for the hub only.

  So the gate keeps git's exact semantics (`commit -a` and pathspec commits included), and nothing the project's tiers
  run has write access to a git dir.
- **The project's own hooks** (pre-commit, husky): installers can't write `.git/hooks` any more. A project that wants
  them declares a tracked hooks folder in its registry entry. The broker runs those hooks through the same forwarder, in
  the plain project sandbox. The owner's own git outside doesn't run them, since the config is the platform's.
  Question 4.
- The platform's hook installer (`render-project-settings`, `project_git hook-write`) stops writing `.git/hooks` in a
  switched-on project. The folder is the broker's.

### Editors, pagers, TTY

- **The editor:** git runs with `GIT_EDITOR=:` and `GIT_SEQUENCE_EDITOR=:`.
  - Measured: a merge, `rebase --continue` and `cherry-pick --continue` go through.
  - `commit` and `tag -a` without a message stop on an empty message, exactly as with Claude's `GIT_EDITOR=true`.
  - A caller whose editor is anything else (`EDITOR`, `VISUAL`, `GIT_EDITOR`, `GIT_SEQUENCE_EDITOR` set to a program)
    gets it run **on the caller's side** through the same callback channel. The forwarder hands the file to the
    wrapper, which runs the editor inside the caller's own sandbox on a copy and sends the result back.
  - An interactive rebase's todo coming back is checked: no `exec` / `x` lines (`break`, `label`, `reset`, `merge`,
    `update-ref` are fine).
  - Until that is built, such a call is refused with the reason, never silently accepted unedited.
- **The pager:** off (`core.pager=cat`). Paging is the caller's terminal's business.
- **Colour:** `color.ui=always` only when the wrapper's stdout is a terminal.
- **`add -p` / `-i`, `commit -p`, `checkout -p`** need an interactive loop on a terminal. Refused at first, with the
  `git apply --cached` way said. A pty relay later, if wanted (question 7).

### Streaming, timeouts, concurrency

- Frames: stdout, stderr, exit status, and stdin as a stream (binary is fine; measured). Nothing is buffered whole.
- Every call has a timeout (10 minutes, longer for `gc`, `fetch` and `clone`; a registry setting). The process group is
  killed at the timeout, and on a FIFO include, as `project_git` does.
- At most N calls per project at once (8 by default), queued beyond that. Git's own lock files (`index.lock`, ref locks,
  `config.lock`) order the broker against the owner's concurrent git, as two terminals are ordered. No extra lock.
- Every call is logged in `<state>/git-broker/log.jsonl` (project, subcommand, the path taken, exit code, duration; no
  arguments' values). Refusals are counted for the status page.

## What each runtime needs

- **Services and command jobs:** the Seatbelt fence and socket rule in `service_profile`, and the wrapper first on PATH
  in `sandboxed-run`. Fully platform-controlled.
- **Claude Code sessions:** the fence in settings, the socket in `allowUnixSockets`, and the wrapper on the Bash tool's
  PATH. That last one must survive the shell snapshot re-reading the user's rc files: an `env` setting, or the
  session-start hook's environment file. Unmeasured. Claude's own git (the status at start, worktree mode) stays outside
  and reads a trustworthy git dir, so its missing filter protection stops mattering.
- **Codex sessions:**
  - the fence globs in the rendered profile;
  - the network on, which D8 already decided and which the socket needs (measured);
  - the nonce for identity;
  - the wrapper on the tool shell's PATH (`shell_environment_policy` in the rendered profile; unmeasured).

  **B1 changes meaning:** Codex's own git still runs outside the sandbox, as the user, through no wrapper, and that is
  fine once the git dir is written only by git under the platform's control. The filter canary turns from "a planted
  filter runs" into "the sandbox can't plant one".
- **Any other agent:**
  - if the platform can't set its PATH, `brunna git …` is the explicit form, and the rules text says so;
  - plain `git` there still reads in place, and its writes fail with "Operation not permitted";
  - that is a loud failure, not a hole.

## Platform flows that change

- **`project_git` ops that write** (`hook-write`, `hook-remove`, `set-origin`, `exclude`, `worktree-add`,
  `worktree-remove`, the commit in `sync-memory`) move from "a job as the project" to the broker's runner. The platform
  calls it in-process, with the same writer profile and config op. The read ops (`head`, `worktrees`, bundles, facts)
  stay jobs as the project. They only read, and the fence makes their opportunistic writes fail harmlessly.
- **Memory sync:** the `.agent-memory` worktree's admin dir (worktree form) or the memory clone's git dir (clone form) is
  a git dir like any other, fenced for the project. Its commit of `memory/` and `STATE.md` goes through the broker's
  runner. Its bundle is still made as the project.
- **The commit gate** (r2 B3 as built): it runs through the hook forwarder. The test runner's export stops writing objects
  into the repo: its index and its object folder go into the run folder, with the repo's objects as an alternate
  (measured to work under the fence).
- **`merge-branch`:**
  - its git goes through the wrapper (it calls `git` on PATH);
  - `-c rebase.updateRefs=false` is on the inert list;
  - its lock moves from `<common>/merge-branch.lock` to `<common>/brunna/`;
  - its dry run's temporary worktree moves out of the git dir, or the dry run tests the rebased commit with `--rev`
    and no worktree at all;
  - the fast-forward of main runs in the main checkout through the broker (the same project's repo).
- **`with-lock.sh`:** its lock folder moves from the git dir to `<git dir>/brunna/`.
- **`sync-local-setup`:** its `info/exclude` writes become the broker's `exclude` op.
- **The fence scan** (`git_fence.py`, built): it stays as a second layer, report only. In a switched-on project, any
  finding means a writer got past the fence, so it is an alert, not a note. B1's index check becomes a check that the
  index was written by git: nothing else can write it.

## What stays residual

- **Worktree content read by the owner's own git.** The owner's global config is theirs. A tracked `.gitattributes` can
  select a driver it defines (git-lfs's filter, say), which then runs as the owner on content the project chose. The same
  goes for `.lfsconfig` (not measured: no git-lfs here) and the URLs in `.gitmodules` on the owner's own
  `submodule update`. This is the fence doc's question 3, unchanged.
- **Git's own parsers and path checks**, run as the owner on objects, packs and an index git wrote from project content.
  Git's robustness, as before. The exploit classes that needed a planted index or config are closed by prevention.
- **Tools that bundle a git library** inside the sandbox fail their writes loudly (unmeasured per tool). The rules text
  points at archive URLs and the wrapper.
- **The broker runs git as the owner** for network operations, in fixed forms, with the owner's credentials (question 1
  limits where).
- **A tool that runs the real git by an absolute path** inside the sandbox gets reads, and writes refused. A nuisance,
  not a hole.
- **The temp area's shared folders:** another project can't write a recorded scratch repo's git dir (the fence), but can
  write its worktree files, as anything in the shared temp area can be written today.
- **Linux:** no prevention (below).
- **What runs outside every sandbox in the folder** (a build or test the owner starts) is outside this design, as always.

## How this changes the fence plan and Codex's confined check

| `git-config-fence.md` part | under git through the platform |
|---|---|
| the per-name deny list and "what stays writable" | replaced by the whole-git-dir rule |
| B2's symlink rule and `logs` / `refs` / `objects` pins | unnecessary for the sandbox (it writes nothing there); the pins stay in the writer profile |
| presets (`push.default`, `branch.autoSetupMerge=false`, …) | kept, written by the config op |
| the config allowlist table and URL rule (B4) | kept: the config op's rules |
| the narrow `brunna git` runner and its "when it may write" condition | subsumed by the broker. A switched-on project is fenced by construction. |
| the one-time install | becomes the rebuild (below) |
| B1 (index entries) | prevented, as far as tried: only git writes the index, and the four ways tried (`update-index --cacheinfo` / `--index-info`, `read-tree` of a hand-made tree) refused or ignored the entries; any other way is git's own path check |
| the scan | kept as a second layer; a finding is an alert |
| S1 (`HEAD` shapes), S3 (scratch repos), S6 (worktree paths) | taken into the fence and the broker |
| the uv marker exception | kept |
| Linux | still unsupported |

**Codex's confined check** (`runtime-codex.md` Step 4) gains one condition: **the project's git goes through the
platform.** That means:
- the switch is on and the rebuild is done;
- every writer's fence is rendered, with each runtime's canary passed for the version running (Seatbelt; Claude's
  settings; Codex's profile);
- the broker is up;
- the machine is macOS.

The scan finding nothing is not a condition (the fence doc's r3); a scan finding is a failure. D13 is re-decided then
(`runtime-codex.md`).

## Rollout per project, and the way back

- **The switch:** `enforce.git: platform` in the project's registry entry. It is an owner-only security field like the
  rest of `enforce` (manifest.update can't change it). Off by default.
- **Switching on**, refused while a session is open in the project (`hubconf.cwd_users()`) or while a rebase, merge,
  cherry-pick or bisect is in progress:
  1. **The rebuild.** Every git-dir file made before the switch could be a hard link or a symlink to somewhere else (the
     fence doc's "State from before the fence"), and that now matters for every file, the index and `HEAD` included.
     So the broker builds a **fresh git dir** with git:
     - `init --template=`, then a fetch of every ref from the old one (packs, not hard links);
     - the config from the allowlisted keys only;
     - the index from the old one's `ls-files --stage` through `update-index --index-info` (git checks every entry);
     - each stash entry through `stash store`, and `info/exclude` as text;
     - the platform's own folder, `brunna/` (unsent outbox letters, the test runner's store, the tool caches), each
       file copied as a fresh file, links not followed;
     - each linked worktree's admin dir through `worktree add --no-checkout` at its existing path, then
       `worktree repair`.

     The old git dir is moved into the platform's state (`.state/git-broker/<project>/before-<time>/`), and the new one
     is put in its place. Reflogs aren't carried (they are history, not state). **Unmeasured.** Building and checking it
     is step 2 of the order of work.
  2. Then the fence is rendered for each writer, and the wrapper put on each writer's PATH.
  3. Then brunna's own ops move to the broker, and the rules text changes for that project.
- **The way back:**
  - switching off removes the fence and the wrapper from every rendering, and the broker stops serving the project;
  - the git dir stays as it is, a plain git dir;
  - `brunna git-broker restore <project> --at <time>` puts the pre-switch git dir back, for the case where the rebuild
    lost something.
- **Doctor and the status page:**
  - per sandboxed project: "git through the platform: on (rebuilt <time>) / off / not supported on this OS";
  - each writer's fence canary for the running version, and the count of refusals in the last day with the most
    common one;
  - the broker service's health, as cap-runner's.

**The rules text** for a switched-on project (in `templates/agent-instructions.md`, sandboxed projects only):

> Git works as usual here. The platform runs it for you, so a few things are different:
> - Commit with `-m` or `-F`.
> - A new branch has no upstream: push with `git push`, and pull with `git pull origin <branch>`.
> - Hooks and git settings are the platform's. `git config` accepts only a short list of keys.
> - Some commands and options aren't run (the refusal says which, and why).
> - Git dependencies need an archive URL in place of `git+…`.
> - If plain `git` fails with "Operation not permitted", run `brunna git …` instead.

## Linux

Landlock only grants rights. A write right on the workspace covers its `.git` at every depth, and no name inside a
writable tree can be denied (`landlock.py`'s header). A mount namespace could bind `.git` read-only, but unprivileged
user namespaces are restricted on current Ubuntu (`capabilities.md`). Also, Claude's own sandbox can't filter unix
sockets by path there. So on Linux the switch is refused with "not supported on this OS". The scan runs as it does today,
and a sandboxed project's sessions of a runtime whose git runs as the user count as not confined (the fence doc's
Linux section, unchanged).

## Tests

- **`policy.git_fence`** and each rendering: Seatbelt regexes, Claude rules under every write root, Codex globs,
  Landlock reporting `Unsupported`; the boundary ratchet.
- **Seatbelt integration** (macOS, as `tests/test_control_files.py`):
  - every refused probe in section 2, each with an unfenced positive control;
  - the in-place reads and the temp-object export working;
  - the uv marker exception.
- **The broker**, with a stand-in project in a throwaway instance:
  - every row of section 3, through the wrapper from inside the fence;
  - identity: another project's socket refused by the socket rule; a forged header with no proof refused; a nonce
    aimed at another project's folder refused; a FIFO or a link at `.hub-run` or `git` refused without blocking;
  - the exec guard: `--exe`, `-x<cmd>`, `-O<cmd>`, `!` aliases and an `exec` todo line, each run with the argument
    filter turned off. No canary runs, either under the broker or in the owner's `rebase --continue` afterwards: the
    check after a stop aborts a rebase whose state holds an `exec` line.
  - the allowlists: unknown subcommands and options, abbreviations, sticky shorts, `-c` keys and environment variables
    are each refused with the reason. Forms with a selecting environment run in place.
  - the config op's allowlist and install (from the fence doc);
  - the composed operations, the hook forwarder (the commit gate's verdict, a project hook), the editor callback with
    an `exec` line refused;
  - timeouts killing the group; a FIFO include; concurrency against an owner's git holding `index.lock`.
- **The owner's canaries** (section 6) after every scenario, and the same plants written with no fence (positive
  control).
- **The rebuild:** hard links and symlinks from before are cut; staged changes, stashes and worktrees survive; an
  in-progress operation refuses; restore.
- **Conformance canaries** per runtime and version, run from inside that runtime's sandbox in a throwaway project:
  - the section 2 probes and the socket rule;
  - the positive controls (`.git/brunna/` written, a commit through the wrapper);
  - the wrapper being the `git` found on PATH.
- **Golden output** for refusals, the rules text and the status rows.

## Order of work

1. The fence in `policy.py` and the Seatbelt rendering, with its tests. The fence isn't applied anywhere yet.
2. The broker and the wrapper: the channel and the nonce, the writer profile with the exec guard, the allowlists, the
   config op, the composed operations, the network path. Plus the rebuild and its restore. Tested against throwaway
   projects only, answering "not switched on" for every real one.
3. The hook forwarder, with the commit gate on it. The test runner's export moved to its own object folder.
4. Each runtime: Claude's settings and PATH, Codex's profile and PATH, each with its canary. A runtime whose canary fails
   keeps the project's sessions of that runtime refused (as hub-event does today).
5. brunna's own flows: `project_git`'s write ops, memory sync, `merge-branch`, `with-lock.sh` and `sync-local-setup`
   onto the broker or into `.git/brunna/`.
6. Switching on one throwaway project end to end. Then the owner's first real project.
7. Codex's confined check gains its condition (`runtime-codex.md` Step 4 r2's steps 3–5).
8. The editor callback, then (if wanted) a pty relay.

A blind review of this design before step 1, and of the code before `merge-branch` (the working rule).

## Open questions for the owner

1. **Pushing with your credentials.** The broker pushes to the registry's remotes as you, for any process of the
   project, services and jobs included, which today can't reach your credentials.
   - **Recommended:** sessions and jobs alike, but never a forced push and never to the default branch, unless the
     registry says so (`push_on_merge` already exists for `merge-branch`). `--force-with-lease` is allowed to the
     project's own non-default branches.
2. **Fetch and clone from other URLs.** **Recommended:** yes, in the project's own sandbox (its network, no credentials,
   its read rules), https only.
3. **Scratch repos.** **Recommended:** made by the broker only in the test runner's per-run temp folder and the
   project's own folders, never elsewhere in the shared temp area.
4. **The project's own hooks.** **Recommended:** run through the forwarder in the project's sandbox when the registry
   declares a tracked hooks folder. Never installed into `.git/hooks`.
5. **Submodules.** **Recommended:** none at first (the fence doc's question 6).
6. **Signing.** Signed commits and tags would use your key from a project's request. **Recommended:** not through the
   broker.
7. **Interactive git** (`add -p`, editors on a terminal). **Recommended:** the editor callback in step 8. A pty relay
   only if you use these inside a sandboxed session.
8. **Your global drivers** (git-lfs and the like) still run as you, in your own git, on project content (the fence doc's
   question 3). **Recommended:** listed and accepted.
9. **Switching on.** **Recommended:** you flip it per project. The rebuild runs then, with nothing open.
10. **Drivers from your global config inside the broker** (git-lfs and the like). **Recommended:** copy only drivers you
    name, per project in the registry, so that an agent's staging matches yours. A named driver runs under the writer
    profile, with the exec guard allowing that one program. Without one, doctor says the two gits differ for that
    project.

## Risks

- **Friction:** a refusal for an option an agent uses legitimately. The allowlist grows from the refusal log, one
  reasoned entry at a time, as the fence doc's config allowlist does.
- **One more service on the critical path.** If it is down, sandboxed projects can read but not write git. It is
  watched like cap-runner.
- **Latency:** forwarded calls cost ~37 ms against 10 ms (measured). Reads run in place.
- **Seatbelt** is deprecated as a public interface. The platform already depends on it for every sandbox.
- **Each runtime's sandbox semantics can change** between versions: the per-version canaries hold a version back.
- **The rebuild** touches every switched-on project's git dir once. It is reversible from the kept copy, and unmeasured.
- **The nonce file** is one more write the project makes per call into its own `.hub-run/git/`. It is removed by the
  broker, and a project that floods it only slows itself.
