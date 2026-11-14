# Git through the platform: a sandboxed project never writes a git dir

Status: design, 11-14, on measurements made the same day; r2 after a blind review the same day (at the end; it holds
where the text above disagrees: chiefly, no process writes both the worktree and the git dir, and
`safe.bareRepository=explicit` is a precondition again); r3 after a second blind review (at the very end; it holds
over both: an unsandboxed checkout can be raced into writing anywhere the owner can write, and how far to go against
that is the owner's choice; B-1's mitigations 2 and 3 built 11-14, "B-1 as built" at the very end); the broker
itself is not built. It takes over the prevention half of
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
terminal, a git GUI. **Caveat (r3):** this design doesn't yet deliver that for checkouts. While a project process runs,
an unsandboxed checkout (the owner's, or an agent tool's worktree mode) can be raced into writing anywhere the owner can
write. That was true before this design too. The owner's options are in r3, "B-1: the options".

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
    by git, not by the project. If any line is an `exec` or `x` command, the broker aborts the operation (composed:
    P1 puts the refs and the index back and removes the state folder, P2 restores the worktree; r3 S-f) and says
    why.

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

- **Arbitrary writes as the owner during any unsandboxed checkout (r3, B-1).** This covers the owner's terminal, editor
  or git app, and an agent tool's own worktree mode. While any of the project's processes runs (a session's command, a
  service, a job), a checkout can be raced into writing the project's files into any folder the owner can write:
  `~`, `~/Library/LaunchAgents`, another project. Existing files there get overwritten. This holds today, with or
  without the broker. The options are in r3.
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

**Caveat (r3, until the owner decides B-1):** these conditions don't cover the owner's checkout race. While any of the
project's processes runs, an owner's or an agent tool's unsandboxed checkout can be made to write the project's files
anywhere the owner can write. Which conditions "confined" adds for that is the owner's choice (r3, "B-1: the options").

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

## r2 (11-14), after the blind review

A cold review found two blocking points (B1, B2), both reproduced end to end, eight should-fix items (S1–S8), nits
and a rework of the owner questions. Each fix was measured in scratch the same way as above: git 2.50.1, the platform's
real `service_profile` plus the candidate rules, the reviewer's probes re-run against the changed prototype. Where r2
and the text above disagree, r2 holds.

### B1: a symlink swap during a broker checkout. Fixed by splitting every operation into two processes.

**The finding.** The project loops, replacing a real folder `d` with a link `ln -s .git d`. Meanwhile the writer
profile runs `checkout -f` of a tree holding `d/…`, including `d/rebase-merge/` with an `exec` line in its todo.
- Git caches its leading-path check, so some of the tree's files land inside `.git/`.
- The writer profile allows them there: one process may write both the worktree and the git dir, and Seatbelt checks
  the resolved path.

Re-run here, the first round put 2,931 of the tree's files and a `rebase-merge/` into `.git`. The owner's `git status`
then reported a rebase in progress. The reviewer's run went on to the owner's `rebase --continue` running the canary;
here the follow-up probe stopped earlier, at "merge conflicts". Every operation that writes the worktree can plant
this way.

**The fix: no single process writes both the worktree and the git dir.**
- **P1, the git-dir writer.**
  - It may write the request's git dir (minus the names git runs or reads elsewhere, as before) and nothing else: no
    worktree, no temp area, only `/dev/null` and the terminal devices.
  - The exec guard applies.
  - A symlink in the worktree gives it nothing, since it writes no path through the worktree.
- **P2, the materialiser.** It is the project's own fenced profile: the profile its sessions and services already
  have. It writes the worktree from a **temporary copy of the index** outside every git dir:
  - `read-tree -m -u <old> <new>`, or `read-tree -u --reset <new>`, with `GIT_INDEX_FILE` set to the copy;
  - `checkout-index` for single paths.

  A swapped link reaches no git dir, since the fence refuses that write.
- **The order:**
  1. P1 computes the next index beside the real one (`.git/index.broker`, while the worktree is still the old one;
     `read-tree -m` checks the worktree is up to date, so it can't run after P2).
  2. P2 materialises.
  3. **P2 checks its own work:** `diff-files --quiet` against the temp index, which is S8, below.
  4. Only then does the broker rename the next index into place and move the refs, and P1 refreshes the index's stat
     information.
  5. If P2 failed or the check failed, the next index is dropped and no ref moves. The call fails.
- **Operations that don't write the worktree run whole under P1**, with the worktree read-only: `commit` (`-a` too),
  `add`, `rm --cached`, `branch`, `tag`, `notes`, `update-ref`, `stash create` / `store` / `drop`, `fetch`, and the
  reads.
- **Operations that write the worktree are composed from plumbing:**

  | operation | P1 (git dir only) | P2 (worktree only) |
  |---|---|---|
  | `switch` / `checkout <branch>` | next index (`read-tree -m`), then `symbolic-ref HEAD` | `read-tree -m -u` on the temp index |
  | `reset --hard <rev>` | next index (`read-tree --reset`), `update-ref HEAD` | `read-tree -u --reset` |
  | `restore <path>` | refresh | `checkout-index -f -- <path>` |
  | `merge` | `merge-tree --write-tree` → `commit-tree` → next index → `update-ref`; on conflict, the stage entries through `update-index --index-info`, with `MERGE_HEAD` / `MERGE_MSG` written by the broker | the merged tree (with conflict markers) |
  | `rebase` (no conflicts) | `replay --onto` → `update-ref --stdin` | the new head's tree |
  | `stash push` / `pop` | `stash create` / `store`; `merge-tree --merge-base`; `stash drop` | reset to HEAD; the merged tree, index kept |
  | `pull` | the fetch; then as `merge` | as `merge` |
  | `clone` | `init` by the broker; fetch | `read-tree -u --reset` of the default branch |
  | `rm` / `mv` (in the worktree) | `rm --cached` / `add` | the file removed or renamed as the project |
  | `apply` (worktree only) | none | `git apply` itself |

- **Measured, split this way.** All of these worked:
  - clone, checkout both ways, `commit -a`;
  - a clean merge, and a conflicted merge (`UU f`, markers in the file), then resolved and committed with two parents;
  - a rebase through `replay`;
  - `stash push` / `pop` (contents back);
  - `reset --hard`, `restore` (worktree clean afterwards);
  - `pull`.

  Each operation took 3–6 sandboxed git processes. The ~8 ms of applying a profile each time dominates.

  | | ms |
  |---|---|
  | clone | 155 |
  | checkout | 83 |
  | commit -a | 21 |
  | merge, clean / conflicted | 116 / 93 |
  | rebase | 122 |
  | stash push / pop | 107 / 92 |
  | reset --hard | 91 |
  | restore | 29 |
  | pull | 165 |
  | the same nine commands directly, unsandboxed, in one process each | 166 in total |
- **The race, re-run against the split (probe B/C's attack, 40 rounds, 90 s of swapping):**
  - no file of the tree in `.git`, and no `rebase-merge/`;
  - every round's checkout was refused as partial, and HEAD never moved;
  - the control without the attacker completed 40 of 40;
  - the reviewer's follow-up probe on the result: an owner's `status` clean, `checkout -f HEAD` clean, no rebase in
    progress, no canary.

  So the attack becomes a refusal: the project can only stop its own checkouts.
- **A fetch from a path** can't run under P1. Git starts `upload-pack` through `/bin/sh`, and allowing a shell under the
  exec guard would void it, since a shell's builtins write files. So the source is read under the project's own
  profile into a bundle (`bundle create`), and P1 fetches the bundle (measured, for the clone and the pull above).
- **Not measured yet:**
  - a rebase with conflicts (`replay` stops; a stepwise `merge-tree` per commit with the `rebase-merge/` state written
    by P1 is the plan, so the replay's being experimental in git is no lock-in);
  - cherry-pick and revert (the same `merge-tree` shape);
  - `stash pop` with conflicts;
  - `switch -c` with local changes;
  - `pull --rebase`;
  - `worktree add`, where the broker makes the folder and writes its gitfile through no-follow directory descriptors,
    P1 the admin dir, and P2 the checkout.

  Each is in step 2 of the order of work, with the race probe run against it.
- **Residual, newly listed: the owner's own unsandboxed checkouts race the same way.** r3 measured this as worse than
  written here: the swap can point at any folder, not only `.git`. The result is arbitrary writes as the owner. See r3,
  B-1.

### B2: git-dir shapes despite the "no HEAD" rule. `safe.bareRepository=explicit` is a precondition again.

**The finding** (re-run here):
1. **A HEAD path committed through the broker.** A commit whose tree holds `x/HEAD` (made with
   `update-index --cacheinfo`) passed through the writer profile. A checkout under the writer refused to write
   `x/HEAD`, with exit 0 (S8). An unsandboxed `worktree add` (the owner's, or an agent tool's worktree mode) wrote it.
   The fenced project then added `objects/`, `refs/` and a config with `core.worktree=..` and `fsmonitor` around it,
   and the owner's plain `git status` in `x/` **ran the fsmonitor**.
2. **A folder holding an existing HEAD renamed in.** The sandbox moved a folder already holding a `HEAD` (a fixture)
   into place and planted the config. The owner's `status` inside it ran the fsmonitor under git's default;
   **`safe.bareRepository=explicit` refused it** ("cannot use bare repository").

**Folded in:**
- **The precondition.** `safe.bareRepository=explicit` in the owner's global git config is a precondition of
  "confined" (the fence doc's question 2, answered). **The owner set it on this machine on 11-14.**
  - Doctor checks it on every run.
  - Its absence turns the project's row and Codex's confined check to "not confined", with the one command that fixes
    it.
  - The platform never writes it: it is a user-level setting.
  - A git GUI or editor that reads repos through a library that ignores this setting is a residual: listed, not
    covered.
- **The HEAD rule, non-directories only** (the fence doc's S1 wording restored: a folder named `HEAD` is ordinary).
  Measured with `(deny file-write* (require-all (regex #"/HEAD$") (require-not (vnode-type DIRECTORY))))`:
  - refused: creating a file `HEAD`, a folded `head`, `x/HEAD`, a rename onto `r/HEAD` or onto `s/head`, a hard link
    `t/HEAD`, `cp` to `u/HEAD`, a symlink `y/HEAD`;
  - allowed: a folder `HEAD/` and a file inside it.
- **The broker refuses tracked paths whose last component folds to `HEAD`.** It checks every tree it is about to write
  (`write-tree`, `commit`, `merge-tree`'s result, `update-index` input, a fetched branch it is asked to check out),
  matching the way the volume folds names (case, and the characters git's own path check ignores). So a HEAD file
  can't enter the project's history through the platform. History that already holds one is listed, below.
- **Switch-on and the scan list every existing `HEAD` entry outside a `.git`** in every area the project may write:
  the workspace, the runtime folder, `allow_write`, its scratch roots, and tracked `HEAD` paths in the repo's history
  on its branches. Switch-on refuses until each is gone or the owner accepts it by path. The rule stops new ones; the
  rename case needs the precondition.

### Should-fix

- **S1, refs.**
  - `update-ref` and `symbolic-ref` (and `update-ref --stdin` lines) take only `refs/heads/`, `refs/tags/` and
    `refs/notes/` (and `HEAD` → `refs/heads/…` for `symbolic-ref`).
  - Root refs and pseudo-refs are refused. Measured: `update-ref SHALLOW <commit>` writes `.git/shallow` and makes the
    repo look shallow to the owner; `MERGE_HEAD` git itself refuses.
  - `refs/replace/` is refused. Measured: a replace ref made the owner's `git show main:f` and `checkout -f main` show
    and write content other than the commit's, and a self-replace broke the owner's `status` and `log` ("replace
    depth too high").
  - The broker's git runs with `GIT_NO_REPLACE_OBJECTS=1`.
  - Fetch refspecs are the broker's own, so a fetch can't write `refs/replace/` either.
- **S2, the rebuild.**
  - **The old git dir is read only under the project's own profile, with the exec guard.** Measured:
    - run as the owner, the old config's `fsmonitor` ran on `ls-files --stage`, and was quiet only with the hardened
      `-c`;
    - even with the hardened `-c`, the owner's fetch from the old dir **followed a planted
      `objects/info/alternates` into project B** and copied B's file in;
    - under A's own profile, the same read failed loudly ("unable to normalize alternate object path") and copied
      nothing.

    So the old dir is turned into a bundle under A's profile, and the fresh git dir fetches that bundle under P1, with
    `transfer.fsckObjects`. A planted alternates file stops the rebuild, which reports it. The owner removes it from
    the kept copy and runs again; it is never followed.
  - `info/exclude` and the files under `brunna/` are copied by opening without following links, regular files only,
    with a size cap.
  - **Linked worktrees.** `worktree add --no-checkout` at an existing path fails (measured). So the broker writes the
    admin dir (`HEAD`, `commondir` = `../..`, `gitdir`), then `worktree repair` checks it. Measured: repair, then
    `read-tree` of the branch, left the worktree with its uncommitted file intact.
  - **The order:** refuse while a session is open; stop the project's services and jobs; render the fence for every
    writer; rebuild; start them again.
  - `git-broker restore <project> --at <time>` implies switching the project off, and runs the scan afterwards.
- **S3, pushes as the owner.** A push to GitHub starts Actions with the repo's secrets, on any branch or tag push. So:
  - **Sessions only.** Services and jobs push only through `merge-branch`'s `push_on_merge`, as today.
  - **Branches only, never tags, never the default branch.** Unless the registry says otherwise; the default branch is
    `merge-branch`'s.
  - **A push that changes `.github/workflows/**`** (the diff between the destination's current commit and the pushed
    one) is refused unless the registry allows it.
  - **The broker computes the destination itself.**
    - It pushes to the registry's **URL**, not a remote name, with explicit `<src>:<dst>` refspecs. So `pushurl`,
      `remote.*.push` and `branch.*.merge` in the repo's config never apply.
    - Refused: `--delete`, `:<ref>`, `--tags`, `--follow-tags`, `--mirror`, `--prune`, `--all`, a `+` refspec.
      `--force-with-lease` is allowed only on the project's own non-default branches.
  - The config allowlist drops `remote.*.pushurl`, `remote.*.push` and `branch.*.merge` naming a protected ref.

  Question 1 below.
- **S4, the platform's hooks stay for the owner's own git.** The commit gate's and `commits: conventional`'s hooks in
  `.git/hooks` are kept, written by the platform as today, so the owner's terminal commits still go through them. The
  broker's `-c core.hooksPath=<forwarder folder>` overrides them only for broker git.
- **S5, the socket rule is scoped to the broker's folder.** Measured (probe F):
  - "deny every outbound unix socket, allow its own" also cut a service's unrelated socket (a local database);
  - `(deny network-outbound (remote unix-socket (subpath <broker folder>)))` plus the allow for its own socket kept
    the database socket, refused project B's socket, and allowed its own.
- **S6, nested projects and tool worktrees.**
  - **Nested or overlapping projects.** A project whose nonce folder (`<workspace>/.hub-run/git/`) lies inside
    another project's write roots isn't served. The deepest root claims a folder, as `test-runner.claimant` does,
    so the outer project can't prove the inner one.
  - **Codex app worktrees** (under the user's Codex home, outside the project's folders). By the principle, git in a
    normal Codex app worktree must work, not fail. The design: such a worktree is served when all of these hold:
    - its gitfile leads to an admin dir inside a common dir the project owns, and that admin dir's `gitdir` leads back;
    - no other project's write roots contain the worktree;
    - its nonce folder is the worktree's own `.hub-run/git/`, listed in the repo's `info/exclude` by the platform.

    Identity is then "can write that worktree", which only the session working in it can. The fence there comes from
    Codex's rendered profile, whose writable root that worktree is. Unmeasured: whether the Codex app's sandbox
    writes the worktree only, and where it puts the worktree. Question 3.
- **S7, hook tools inside a commit.**
  - **lint-staged and the pre-commit framework** run `git stash` and `git add` inside the commit's own hook, with
    `GIT_INDEX_FILE` set by git to the commit's in-flight index (`index.lock` or a `next-index-*.lock`).
    - The forwarder gives the hook's environment a token for that commit.
    - The wrapper forwards a call carrying the token, with its `GIT_INDEX_FILE`.
    - The broker accepts that variable only when it names exactly the in-flight index of the commit the token belongs
      to, and runs the call under P1 (`add`), or split (`stash`).
    - Any other `GIT_INDEX_FILE` stays "in place".

    Unmeasured.
  - **The pre-commit framework's own repos** (`git init`, `remote add`, `fetch`, `checkout` in its cache).
    - The platform sets `PRE_COMMIT_HOME` (with the other tool caches in `policy.cache_env`) to a per-project cache
      folder outside every `.git`.
    - The broker accepts `init` there as a recorded scratch root: https URLs only, fetched under P1, checked out by P2.
    - The owner never runs git in it.

    Unmeasured.
  - **The project's own hooks** run through the forwarder in the plain project sandbox when the registry declares a
    tracked hooks folder (the old question 4, settled).
- **S8, a partial checkout fails the call.** A checkout that couldn't write a file exits 0, with only an "unable to
  create file" message (measured: the writer profile's refused `x/HEAD`). The split's own check (`diff-files --quiet`
  on the temp index after P2) turns any such result into a failure, and no ref or index moves. Measured: 40 of 40
  partial checkouts under the race were refused, and 40 of 40 completed without it.

### Nits

- **N1.** Every broker fetch, bundle fetch included, runs with `transfer.fsckObjects=true`. Measured: a crafted tree
  with a `.git` entry was accepted by a plain fetch and refused with it ("hasDotgit"). `hash-object --literally` is
  refused.
- **N2.** The check after a stop reads only `rebase-merge/`. The sequencer that `cherry-pick` and `revert` use refuses
  a todo holding an `exec` line, measured twice: "cannot cherry-pick during a revert", nothing ran. `git am`'s
  `rebase-apply/` holds no commands.
- **N3.** `core.hooksPath` and `core.fsmonitor` stay on the command line (`-c`), where no config file can override
  them. They come off the list of what the broker's global config holds.
- **N4.** `.git/brunna/` stays writable by the project: the outbox, the test runner's store of passes, the locks. So a
  pass recorded there, and the commit gate's verdicts built on it, are the project's word about itself, **not a
  boundary**. The boundary is that the project's code never runs as the owner (r2 B3 of the fence doc) and that its
  git dir is written only by git. `merge-branch` re-runs the tiers when a recorded pass can't be trusted. A
  switched-on project's records count only for the project's own convenience.

### Decided in the design (were owner questions)

- **Other URLs** (old question 2). https only, fetched under P1 (the project's network, no credentials), checked out by
  P2. A path is read under the project's own profile into a bundle. `ext::`, `file://` outside the project's reach,
  and ssh are refused.
- **Scratch repos** (old question 3). Made only by the broker, in the test runner's per-run temp folder, the project's
  own folders, and its pre-commit cache. Recorded per project, and dropped with their folder. `init --bare`,
  `clone --bare`, `clone --mirror` and `--separate-git-dir` are refused (a bare repo is a git-dir shape the owner's git
  would enter).
- **Interactive git** (old question 7). No pty relay. The editor callback runs a caller's editor on its own side
  (order step 8). `add -p` and kin are refused, with the `git apply --cached` way said.
- **Drivers** (old question 10). A driver named per project in the registry runs **through the forwarder in the plain
  project sandbox**, never inside P1 or P2's git: the filter's content is streamed to the broker and back. git-lfs's
  long-running `process` protocol is relayed the same way. Unmeasured (no git-lfs here).
- **`safe.bareRepository`** (the fence doc's question 2). Answered: set by the owner 11-14, now a checked precondition.

### Open questions for the owner (r2: replaces the list above)

1. **Pushing with your credentials** (S3). **Recommended:**
   - sessions only, to the project's own non-default branches;
   - no tags;
   - no change to `.github/workflows/**` unless the registry allows it;
   - `--force-with-lease` allowed there, any other force refused;
   - services and jobs only through `push_on_merge`.
2. **Signing** (unchanged). **Recommended:** not through the broker. Your key isn't used on a project's request.
3. **Codex app worktrees** (S6). **Recommended:** serve them under the conditions in S6, once a measurement shows the
   app's sandbox writes only that worktree. Until then, git there says plainly that it can't run, and why.
4. **Submodules** (unchanged). **Recommended:** none at first.
5. **Switching on** (unchanged). **Recommended:** you flip it per project. The rebuild runs then, with nothing open and
   the project's services stopped.

### Order of work, revised

Step 2 now builds the split (P1/P2, the next-index rename, the check after materialising), with the composed operations
in the B1 table. It does not build a single writer profile. Every composed operation gets the race probe (B/C) and the
B2 probes (a HEAD path through the broker; a renamed folder holding HEAD, with and without the precondition) as
tests, plus the S1 ref probes, the S2 rebuild probes (fsmonitor, alternates, worktree repair) and S5's socket probe.
Doctor's precondition check lands with step 1.

## r3 (11-14), after the second blind review

A second cold review of r2 found that the split held against everything it tried. It found one blocking point (B-1),
eight should-fix items (S-a to S-h) and a nit. Its probes were re-run here, and the measurements below were added (git
2.50.1, the real `service_profile` plus the candidate rules). Where r3 and anything above disagree, r3 holds.

### B-1: the owner's checkout race (this exists today, with or without the broker)

**The finding** (re-run):
- A fenced project process loops, swapping a tracked folder `d/` for a symlink to a folder it can't write itself. The
  stand-in here is a fake home folder, and the project's direct write there was refused.
- The owner's plain `git switch` wrote **2,925** of the tree's files into that folder in the first round. That
  included overwriting a file already there (`a2990`, now "planted 2990").
- `git worktree add`, which is how an agent tool's worktree mode makes a checkout as the user, wrote **2,920** in the
  first round.
- The review also measured `checkout -f`, and found that `checkout.workers` and `core.symlinks=false` don't help.

Aimed at `~` or `~/Library/LaunchAgents`, this is arbitrary writes as the owner, so code runs as the owner at the next
login. No owner action is needed: an agent's own tool makes worktrees as the user while the session's commands run.

**The residual, plainly** (also in "What stays residual" above): during any unsandboxed checkout (the owner's
terminal, editor or git app, or an agent tool's worktree mode), while any process of the project runs (a session's
command, a service, a job), the project can make that checkout write its files anywhere the owner can write.

**A measured partial mitigation: no symlink creation in the workspace.**
- The Seatbelt rule is `(deny file-write-create (require-all (subpath <workspace>) (vnode-type SYMLINK)))`.
- **Refused:** `ln -s` in the workspace; a link made elsewhere and renamed into the workspace; a link renamed onto
  `d`. A folder can't be hard-linked, so the swap needs a link.
- **The race with the rule in the attacker's profile:** 40 rounds, nothing written into the fake home, and the
  existing file intact.
- **What it breaks:** `python3 -m venv` and `uv venv` (both link the interpreter), and npm's `node_modules/.bin` and
  `file:` dependencies (npm exited 255).
- **Carve-outs.** The rule allows link creation inside `<workspace>/.venv/` and inside any `node_modules/` folder; the
  folder entries themselves stay covered.
  - With them, `python3 -m venv .venv`, `uv venv .venv` and `npm install` of a `file:` package with a `bin` worked.
  - A venv elsewhere (`venv2`) was still refused.
  - **A link made inside a carve-out can't be renamed onto `d`** (refused). The rename's destination counts as a link
    creation outside the carve-out.
  - **The broker refuses tracked paths at or under a carve-out name** (`.venv`, `node_modules`), so no checkout ever
    writes into a folder where links may exist.
  - More carve-outs (other languages' tool folders) are added one at a time with that same refusal, from the refusal
    log.
- **P2 needs an allowance for tracked symlinks.** Under the rule, P2's checkout of a tracked link failed ("unable to
  create symlink"). The allowance is computed per request: exactly the paths where this operation writes a tracked
  link (`(literal <path>)` with the link type). Measured: with the allowance for `tl` only, `tl` was made and the other
  tracked link was still refused.
  - The allowance is P2's alone. P2 runs only git, under the exec guard (r3 S-f), and only while the broker holds the
    index lock (S-e).
  - The broker also refuses, at commit time, tracked links whose target is absolute or leaves the worktree.
- **Coverage: services and jobs only.** Only the platform renders their Seatbelt profile. The agents' own sandboxes
  can't say it, as far as their binaries show (read, not run):
  - **Claude Code:** its sandbox settings take paths only (`filesystem` allow and deny for read and write, `network`,
    unix sockets, `excludedCommands`). The Seatbelt profile it generates uses `vnode-type` only for directory metadata
    reads and `/dev/null`. There is no symlink rule and no setting that makes one.
  - **Codex 0.167:** its permission profiles take filesystem read, write and deny by path or glob, plus network. Its
    Seatbelt generator uses `vnode-type DIRECTORY` only, to pin folders against removal. No symlink rule.

    There was nothing to measure without a setting to try.

  So a session's own commands (the commonest project process) stay able to swap.
- **Claude Code has a `WorktreeCreate` hook** that replaces its git worktree creation: "for another version-control
  system"; the hook prints the path it made. So the platform can make Claude's worktrees itself, through the broker
  (P1 the admin dir, P2 the checkout under the symlink rule's allowance). The Codex CLI binary shows managed worktrees
  in a "configured pool" and no such hook. The Codex app wasn't inspected.

### B-1: the options (the owner's decision)

| option | what it gives | what it costs |
|---|---|---|
| **1. Accept and document** | nothing new; the status page and doctor say it plainly | while any process of a sandboxed project runs, the owner's and the agent tools' checkouts can be made to write anywhere as the owner; "confined" can't be claimed for checkouts; the principle isn't met for them |
| **2. Partial mitigation for services and jobs** (the symlink rule, the carve-outs, P2's allowance, the broker's refusals) | services and jobs can't swap at all (measured) | venvs and tool folders outside the carve-outs break for services and jobs, loudly; the carve-out list needs maintaining; **sessions stay open**, so it doesn't close B-1 alone |
| **3. The platform makes worktrees for sandboxed projects** (Claude's `WorktreeCreate` hook → the broker, with P2 under the rule's allowance), and doctor warns about the owner's own checkouts | an agent tool's worktree mode stops being the no-owner-action trigger, for Claude; the owner is told when their checkout is exposed (a project process is running) | Codex has no such hook found; the owner's own checkouts are only warned about, not protected; a hook to build and maintain |
| **4. Never "confined" while any process of the project can run during a checkout** | the confined check stops overclaiming | in practice a sandboxed project's sessions are never "confined" (their commands can always run during a checkout), so D9's goal of a confined Codex session isn't reached by this design |

**Recommended:** 2 and 3 now, and 4 for sessions until a tool's own sandbox can refuse link creation in the workspace.
The platform also asks Anthropic and OpenAI for that setting, which turns 4 off runtime by runtime as each canary
passes. Until the owner decides, the principle (the top of this doc) and Codex's confined check carry the caveat
written there.

### Should-fix

- **S-a, no owner-identity step resolves a path through a folder the project can write.**
  - **`init`.** Measured: the broker checked `<dir>` (inside the project, new) and ran `git init --template=` as the
    owner, while a fenced process flipped `<dir>` between absent and a link to the fake home. **The owner's git dir
    landed in the fake home after 3 calls** (`HEAD`, `config`, `objects`, `refs`). Now:
    - the broker makes `<dir>` through no-follow directory descriptors and takes its real path from the descriptor;
    - `init` runs under a profile whose only write is `<that real path>/.git`, and the project's read rules apply;
    - a swap afterwards makes git write elsewhere, which is refused.

    Not yet measured with the fix (step 2).
  - **Owner network operations** run only on the workspace's own `.git`. That entry is pinned by the fence, and no
    folder above it is project-writable. For any other repo (a worktree's admin dir, a scratch repo), the broker
    fetches into a broker-owned bare repo outside every project folder, and P1 fetches from there through a bundle.
  - **Gitfiles** (worktrees, the rebuild) are written by the broker through no-follow directory descriptors.
  - **P2 makes the temp index copy** into its own temp folder. The broker never writes a temp path a project could
    have pre-made as a link.
  - **The hook forwarder sends contents, not paths.** For the commit gate, it sends the in-flight index's bytes. The
    broker writes them into its own private folder (`hubconf.private_tmp()`) and runs the test from there.
  - **Scratch repos** are recorded by real path plus device and inode of their `.git`. A moved or replaced repo isn't
    served.
  - **The rebuild's worktree repair.** Measured: an old admin dir's `gitdir` aimed at the fake home made the owner's
    `worktree repair` write a gitfile into the fake home, and `worktree list` then listed it as a worktree. Now:
    - worktree paths come only from the registry or the platform's records (the tool worktree folders under the
      workspace, walked without following links), never from the old admin dir;
    - repair runs under a profile that may write only that worktree's `.git` and its admin dir.
- **S-b, P1's and P2's reads are exactly the project's own read rules.** Measured with the review's probe:
  - with P1 built on the project's profile, four reads were refused and nothing of project B's entered the objects:
    `hash-object -w <B's file>`, the same through a folder link, `commit -F <B's file>`, `apply --cached <B's patch>`;
  - with a git-dir writer that had no read denies, each of the four put B's secret into the objects;
  - `add` through a folder link was refused by git itself ("beyond a symbolic link") either way;
  - a hard link of B's file into the project's folder was refused;
  - an https `ls-remote` worked under the project's profile with the exec guard (fetches from https under P1 are
    fine).

  The four become tests. **There is never a profile with the git-dir write and no read rules.**
- **S-c, the self-check after P2.** Measured: with an unrelated local change in `a` (git's own `switch` carries it),
  `diff-files --quiet` on the temp index failed the call. By then P2 had already rewritten `b`, HEAD stayed, status
  showed both changed, and a retry was refused ("b not uptodate"). Now:
  - the check covers only the paths this operation changes (`diff-tree` old → new, plus conflict paths), compared with
    the dirty set taken before the operation;
  - on failure, P2 puts back what it wrote: `read-tree -m -u <new> <old>` on its temp index, for those paths only.

  Not yet measured with the fix.
- **S-d, pushes.**
  - **Services and jobs can't be told from sessions by the nonce**, since both can write the project's folder. So they
    get **their own socket**: their Seatbelt profile allows only it, and the broker refuses pushes on it. Sessions use
    the session socket. Codex sessions reach any socket, but a service can't reach the session socket.
  - **`push_on_merge: true`:** `merge-branch` runs in the session, so its push reaches the broker like any session's
    push. Allowing it means **any session of that project can push the default branch**: its checks run in the session
    and can be skipped.
  - **The CI rule is widened to `.github/**`.**
  - Question 1 says what a push means: **every push runs this project's code in CI, with whatever secrets your
    workflows give it.**
- **S-e, locking.** The broker takes `.git/index.lock` (created exclusively, as git does) at step 1 and holds it to
  step 4. P1 computes the next index into it, and step 4 renames it over `index`, which is how git commits an index.
  So the owner's concurrent git waits or fails as with two terminals. Worktree-writing operations run one at a time
  per worktree.
- **S-f, the composed set is the allowlist.**
  - There is never a combined profile: P1 writes only the git dir, and P2 only the worktree.
  - The allowlist of forwarded operations is exactly the composed set. Everything else is refused, with its reason.
  - **In place** (real git in the project's sandbox; they write no git dir): `clean`, `archive` (local, to stdout),
    `check-ignore`, `check-attr`, `shortlog`, `diff-tree`, beside the read forms above.
  - The check after a stop aborts by composition (fixed above).
  - **Compositions planned for the everyday operations:**
    - `--abort` (rebase, cherry-pick, revert, am): P1 puts the refs back and sets the index to that tree, removing the
      state folder; P2 resets the worktree.
    - `merge --abort`: like `reset --merge`; P2 does `read-tree -m -u` from the current index to HEAD, so unrelated
      local changes stay.
    - `--continue`: P1 commits the current index with the saved message, then the next step. The state is written in
      git's own format with no `exec` lines, so the owner's own git can continue it too.
    - `--skip`: P1 drops the step, P2 resets to HEAD.
    - `restore --source <rev> <path>` and `checkout <rev> -- <path>`: P1 sets the next index for those paths
      (checkout only); P2 runs `checkout-index` from a temp index holding `<rev>`'s entries.
    - `stash apply`: as pop, without the drop.
    - `merge --squash`: the merged tree into P1's next index, no commit, `SQUASH_MSG`; P2 materialises.
    - `am`: each patch through `apply --cached` into P1's next index plus `commit-tree`; P2 materialises. A 3-way `am`
      is refused at first.

    Each gets the race probe and the S-c check as tests.
- **S-g, Codex app worktrees.** They sit under the user's Codex home. Every sandbox denies that folder, because it
  holds every conversation and the login. Serving one means carving that worktree out of that folder for the project's
  sandboxes. That is the trade in question 3. The Codex CLI's managed worktrees have a "configured pool"; if it can be
  placed inside the project, the carve-out isn't needed. To check.
- **S-h, nested projects** aren't served, unless every rendering of the outer project (Seatbelt, Claude, Codex) denies
  writes into the inner one. The review measured the outer project writing the inner one's folder by default.

### Nit

The hook forwarder runs under `python -I`, with every `PYTHON*` variable stripped, or as a compiled helper. Nothing
from the project's environment shapes the program the exec guard lets run.

### Open questions for the owner (r3: replaces both lists above)

1. **B-1's options.** **Recommended:** 2 and 3 now; 4 for sessions until a tool's own sandbox can refuse link
   creation in the workspace, and the platform asks Anthropic and OpenAI for that setting.
2. **Pushing with your credentials.** Every push runs this project's code in CI, with whatever secrets your workflows
   give it. **Recommended:**
   - sessions only (a separate socket for services and jobs, where push is refused);
   - the project's own non-default branches; no tags;
   - nothing under `.github/**` unless the registry allows it;
   - `--force-with-lease` there, any other force refused.

   Keep `push_on_merge` off for sandboxed projects: with it on, any session of the project can push the default branch.
3. **Signing.** **Recommended:** not through the broker. Your key is never used on a project's request.
4. **Codex app worktrees.** Serving one means carving it out of your Codex home, which every sandbox denies because it
   holds every conversation and your login. **Recommended:** don't carve. Git in such a worktree of a sandboxed project
   says why it can't run, and points to a worktree inside the project (Claude's mode, or the Codex CLI's pool if it can
   be placed there). Revisit once Codex can keep worktrees in the project.
5. **Submodules.** **Recommended:** none at first.
6. **Switching on.** **Recommended:** you flip it per project. The rebuild runs then, with nothing open and the
   project's services stopped.

### Order of work, revised again

- **Step 1** gains the symlink rule for services and jobs, with the carve-outs, behind the per-project switch (if
  option 2 is chosen), and doctor's B-1 row.
- **Step 2** gains:
  - S-a's real-path and descriptor discipline, with the `init` race and the repair probe as tests;
  - S-b's four read probes; S-c's dirty-set check and its rollback; S-e's lock;
  - S-f's compositions;
  - P2's per-request link allowance.
- **Step 4** gains Claude's `WorktreeCreate` hook (if option 3 is chosen), with the owner race probe run against a
  worktree made through it.

### B-1 as built (11-14): mitigations 2 and 3

**The owner's decision (11-14):** "Yes, build 2+3", for B-1, independently of the broker (which isn't built). Both are
built for every sandboxed project, with no switch. Option 4 (sessions never "confined") and question 4 (Codex app
worktrees) are still open. Until they are decided, the caveats on the principle and on Codex's confined check stand.

**Mitigation 2: a sandboxed project's services and jobs make no symbolic link in their checkouts.**
- **The port.** `policy.links` is neutral:
  - the roots are the workspace and the runtime folder (a deploy's `git pull` there is a checkout too);
  - the tool folders `LINK_FOLDERS` may hold links: `.venv/` at a root's top, and `node_modules/` at any depth, inside
    the folder only, never the folder's own entry;
  - so may the platform's own test runs (`.brunna/test/`, both spellings), where the test runner checks out tracked
    links and tests make links in their temp folder;
  - `allow` lists exact paths one platform job may link.
- **The Seatbelt rendering.** `seatbelt.link_rules` sits in the isolation both profile kinds share. That covers
  `sandboxed-run` (services, command jobs, the test runner's wrap, the platform's git work run as the project) and
  cap-run. It is placed before the control-file rules, so those still win. An allowance outside the roots is dropped.
  The guarantee is `links-blocked`, probed by `tests/test_sandboxed_services.py`'s Conformance.
- **Linux** doesn't declare it, and doctor says so (a warning row). Landlock does have a make-symlink right, but it is
  granted per folder that exists when the rules are made, so a `node_modules` or `.venv` made later would get no
  links. Not built.
- **Exact allowances, for the platform's own links made as the project:**
  - `worktree-memory.sh` (`project_git.py run-as --allow-link`): a worktree's `.agent-memory` link and the rules-file
    links;
  - the worktree maker below: each tracked link of the checkout.
- **Carve-outs, measured before adding any:**
  - `uv venv` with uv's cache in `.git/brunna/cache/uv` left no link in the cache;
  - a pnpm store in the workspace held no links (its links are in `node_modules`);
  - cargo's `target/` couldn't be measured: in the sandbox rustup tries to download a toolchain and can't reach the
    Keychain to check certificates;
  - tox and pdm (`.tox`, `__pypackages__`) aren't installed here.

  None was added. A tool that needs one fails loudly, and gets a folder added after it is measured.
- **This machine's sandboxed projects** (read from the registry and their folders, read only):
  - five sandboxed projects; one declares a service (a shell sync script) and a job (`python3 <script>`) that run in
    its runtime folder;
  - neither script makes a link, uses a venv or runs node (searched for `ln -s`, `symlink`, `venv`, `uv`, `npm`,
    `pip install`, `git clone|checkout|pull|worktree`);
  - they weren't run under the new profile, because they call outside services with real credentials;
  - no other sandboxed project declares services or jobs, and none has a `.venv` or `node_modules` at its top.

  Nothing is expected to break.

**Mitigation 3: Claude Code's worktrees in a sandboxed project are made by the platform, as the project.**
- **The contract**, read from Claude Code's binary (2.1.x, not run):
  - a `WorktreeCreate` hook replaces its own git worktree creation;
  - its input is the common fields plus `name`;
  - the path is read from the last line of stdout (a relative one against the session's folder);
  - a non-zero exit or no path fails the worktree;
  - `WorktreeRemove` (`worktree_path`) replaces its removal.
- **What renders the hooks.** `render-project-settings` writes both hooks into every sandboxed project's settings
  (`runtime_claude.platform_hooks`). Each hook runs `hub-event --runtime claude worktree-make|worktree-drop`, ending in
  `exit 1` and never in `|| true`: if the platform can't make the worktree, it isn't made, and never as the user.
- **The new port.** `worktree_maker` is an optional port of `runtime_ports`: `worktree_request`, `worktree_location`,
  `worktree_reply`, `makes_worktrees`.
- **How the worktree is made.** `session_worktree.make` runs `project-git-job.py session-worktree` as the project,
  through `project_git`, inside its sandbox:
  - the place is `<workspace>/.claude/worktrees/<name>`, on branch `worktree-<name>` (new from HEAD, or the existing
    one);
  - the steps are `worktree add --no-checkout`, `reset`, then `checkout-index`;
  - the tracked links are allowed by exact path;
  - tracked files the sandbox may not write (control names, a runtime's own folder) are left out as skip-worktree, so
    git doesn't show them as deleted, and the hook says so on stderr.

  A swap during that checkout can only redirect writes to places the project could already write.
- **What changes on this machine:** one sandboxed project tracks 44 files under control names (`.claude/skills`,
  `.agents/skills`). Its Claude worktrees will now lack those skills (left out). Doctor already lists those files.
- **The existing worktree path, checked.** For a sandboxed project, `worktree-memory.sh` already runs its git and
  links as the project (`project_git.py run-as`), and `render-project-settings --worktree` writes the worktree's
  settings without following links. The remaining owner-run checkouts in a sandboxed project's folder are Claude's own
  (now handed over), the Codex app's and Codex CLI's managed worktrees (left as they are: question 4), and the owner's
  own tools. `propose` is off for sandboxed projects, and its copy is outside the workspace.

**Doctor:**
- one row: services and jobs make no links (macOS), or "this OS's sandbox can't refuse links per folder" (Linux,
  warning);
- per sandboxed project, a warning: while its programs run, a checkout the owner makes in its folder (terminal,
  editor, git apps, the Codex app's worktrees) can be made to write its files anywhere the owner can write; plus which
  agents' worktrees the platform makes there (read from the project's settings);
- per project, files tracked under a tool folder (a carve-out a checkout of the owner's could still be redirected
  through).

**Tests** (`tests/test_link_fence.py`, and the Conformance probe):
- the port and its rendering; the order before the control rules; Seatbelt declares the guarantee and Landlock
  doesn't;
- under Seatbelt:
  - refused: a link in the workspace or runtime folder, one moved in from another writable folder, one made in a tool
    folder and renamed onto a tracked folder's place, a tool folder that is itself a link;
  - allowed: links inside `node_modules` (nested too), `.venv` (top only) and a test run;
  - an exact allowance; the positive control without the rule;
  - venv, `uv venv` and npm's `.bin` links still work;
- **the owner race** (the review's `owner_race2`, the attacker under the rule): nothing written outside, the file there
  intact, the link never made;
- the hook contract (request, location, reply, `makes_worktrees`, the commands fail closed and are the platform's);
- `hub-event` exit codes and stdout;
- a worktree made through the hook under the real Seatbelt profile: made as the project, the tracked link present, a
  control file left out as skip-worktree, status clean, removed again, nothing run for an unsandboxed project or a bad
  name;
- a race against the hook path writing nothing outside.

**Needs a real Claude run** (the platform's own tests can't start Claude; listed for the owner or the coordinator):
1. In a sandboxed project, `claude --worktree <name>` (and the `EnterWorktree` tool, and a subagent with
   `isolation: worktree`) runs the `WorktreeCreate` hook from the project's `.claude/settings.local.json`, opens the
   session in the path it printed, and makes no worktree with its own git. To check: no `git worktree add` from
   Claude's process, the branch `worktree-<name>`, `hub-event.log`'s `worktree-make` line.
2. On exit, choosing to remove the worktree runs `WorktreeRemove`, and the folder is gone.
3. A hook that fails (a name the platform refuses, say) shows Claude's "WorktreeCreate hook failed" and makes no
   worktree.
4. Whether a project's local settings are trusted for this hook in a fresh folder (the binary mentions "workspace not
   trusted"), and that `CLAUDE_CODE_RESTRICT_PERSONAL_CONFIG` doesn't apply to project-level hooks.
