#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The git work the platform needs done in a project's repo, run **as that project**
(docs/design/projects-and-repos.md section 4, "Platform git in project repos").

A repo's `.git` (config, hooks, attributes, `objects/info/alternates`) is writable by its projects, and git runs or
reads what it names: `git log` runs the repo's `gpg.program`, `status`/`add` its fsmonitor and clean filters, `diff` its
`diff.external`, `worktree add`/`remove` its hooks and filters, and a fetch or push from the repo serves whatever its
`alternates` point at with the reader's authority. So the platform never runs git in a project's repo itself:
project_git.job() starts this script (isolation.as_project), through bin/sandboxed-run for a project with enforce.sandbox (a no-op for the
others: they have the user's authority already), and what the platform needs back comes as data on stdout (JSON facts,
or a bundle) that it parses or fetches into a repo it owns (project_git.py).

Standard library only, and every path is an argument: inside the sandbox the platform's state and config are not
readable. Nothing here decides anything for the platform; the caller treats the output as untrusted data.

  worktrees <repo> <days>                 JSON {base, worktrees: [{path, branch, last, merged, clean, exists, files}],
                                          main: [shas on the base branch in the last <days> days]}
  ignored <worktree>                      JSON [ignored entries] (git status --ignored)
  worktree-add <repo> <path>              a detached worktree of HEAD at <path>; prints its HEAD
  worktree-remove <repo> <path> [--force]
  branch-delete <repo> <branch>           git branch -d (merged branches only)
  remove-dep <worktree> <dep> <marker> <line>
                                          delete a dependency folder (never through a symlink) and append <line> to
                                          <marker> in the worktree's git dir
  checkout-sigs <repo> <base>             JSON {path: signature}: the files of <base> the checkout did not write as the
                                          blob byte for byte (taken before the agent starts; usually empty)
  changed <repo> <base>                   stdin: checkout-sigs' JSON; JSON [[path, exists]]: changed since <base> by
                                          their bytes, committed or not, untracked too
  copy-back <src> <dst> <rel>…            copy each rel from src to dst (deleted in dst when gone from src); a symlink
                                          in src is refused, not followed; JSON {copied, refused}
  hooks <repo> <hook>…                    JSON {hooks_path, hooks: {name: text, "-> target" for a symlink, or null}}
                                          for <repo>/.git/hooks
  hook-write <repo> <hook>                stdin → <repo>/.git/hooks/<hook>, replacing a symlink instead of writing
                                          through it
  hook-remove <repo> <hook>
  bundle-unpushed <ws>                    stdout: a bundle of the branches not on any remote (exit 3: nothing)
  diff-head <ws>                          stdout: git diff HEAD --binary
  exclude <repo> <pattern>                the pattern into the repo's .git/info/exclude
  save <ws> <folder> <name>               stdin → a new file <ws>/<folder>/<name> (never overwriting, never through a
                                          link); prints its path relative to ws
  head <ws>                               JSON {branch, dirty}
  bundle-head <ws>                        stdout: a bundle of the checked-out branch
  set-origin <ws> <url> <branch>          origin = url, the branch tracking it
  memory-sync <memdir> <bare> <branch> [--lock]
                                          commit memory/ and STATE.md, merge what <bare>'s <branch> has, then stdout: a
                                          bundle of HEAD that <bare> lacks (empty: nothing to push); --lock takes
                                          with-lock.sh's lock
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import time

# memory notes are not project code: the project's own git hooks don't run on them (git-sync-memory.sh says why)
NO_HOOKS = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null"}
MEMORY_PATHS = ("memory", "STATE.md")
NOTHING = 3
LOCK_WAIT_S = 30


def git(cwd, *args, check=False, env=None, text=True, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output="stdout" not in kw, text=text,
                       env={**os.environ, **(env or {})}, **kw)
    if check and r.returncode != 0:
        sys.stderr.write(f"git {' '.join(args)}: {(r.stderr or '').strip()[-300:]}\n")
        sys.exit(1)
    return r


def out(cwd, *args) -> str:
    r = git(cwd, *args)
    return r.stdout if r.returncode == 0 else ""


def emit(obj) -> int:
    json.dump(obj, sys.stdout)
    return 0


def base_branch(repo) -> str | None:
    return next((b for b in ("main", "master") if out(repo, "rev-parse", "--verify", "-q", b)), None)


def op_worktrees(repo: str, days: str) -> int:
    base = base_branch(repo)
    if not base:
        return emit({"base": None, "worktrees": [], "main": []})
    rp = pathlib.Path(repo).resolve()
    wts, cur = [], {}
    for line in out(repo, "worktree", "list", "--porcelain").splitlines() + [""]:
        if line.startswith("worktree "):
            cur = {"path": line[9:]}
        elif line.startswith("branch "):
            cur["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "" and cur:
            p = pathlib.Path(cur["path"])
            if p.resolve() != rp and p.name != ".agent-memory" and cur.get("branch") not in (None, "agent-memory"):
                b = cur["branch"]
                last = out(repo, "log", "-1", "--format=%ct", b).strip()
                cur.update(merged=git(repo, "merge-base", "--is-ancestor", b, base).returncode == 0,
                           last=int(last) if last.isdigit() else None,
                           clean=p.exists() and not out(p, "status", "--porcelain").strip(),
                           exists=p.exists(),
                           files=out(repo, "diff", "--name-only", f"{base}...{b}").split())
                wts.append(cur)
            cur = {}
    main = out(repo, "log", f"--since={int(days)} days ago", "--format=%H", base).split()
    return emit({"base": base, "worktrees": wts, "main": main})


def op_ignored(wt: str) -> int:
    return emit([line[3:].rstrip("/") for line in out(wt, "status", "--porcelain", "--ignored").splitlines()
                 if line.startswith("!! ")])


def op_worktree_add(repo: str, path: str) -> int:
    git(repo, "worktree", "add", "--detach", path, "HEAD", check=True)
    print(out(path, "rev-parse", "HEAD").strip())
    return 0


def op_worktree_remove(repo: str, path: str, *flags: str) -> int:
    r = git(repo, "worktree", "remove", *(["--force"] if "--force" in flags else []), path)
    sys.stderr.write(r.stderr)
    return r.returncode


def op_branch_delete(repo: str, branch: str) -> int:
    return git(repo, "branch", "-d", branch).returncode


def op_remove_dep(wt: str, dep: str, marker: str, line: str) -> int:
    d = pathlib.Path(wt) / dep
    if "/" in dep or d.is_symlink() or not d.is_dir():
        return 1
    shutil.rmtree(d)
    gd = out(wt, "rev-parse", "--absolute-git-dir").strip()
    if gd and "/" not in marker:
        with open(pathlib.Path(gd) / marker, "a") as fh:
            fh.write(line + "\n")
    return 0


# What the agent changed in a throwaway worktree is decided by the files' bytes, never by git's view of them: git
# status and diff run the clean filters and line-ending conversions that attributes name, and a `.gitattributes` the
# agent writes (`*.md filter=lfs` on a machine whose git config defines filter.lfs, as git-lfs installs do) turns
# files it never touched into changes, which copy-back would then write over the real checkout's (an uncommitted edit
# lost). So each file is hashed as it is (a git blob id of its raw bytes, a link's target for a symlink, plus the
# executable bit) and compared with the base tree's entry (`ls-tree`, which reads objects only); where the checkout
# itself wrote other bytes than the blob (a project's own filter or line endings), checkout-sigs records what it
# wrote before the agent starts, and that is the reference instead. Plain git plumbing every supported git has.

def _z(r) -> list[str]:
    return [os.fsdecode(x) for x in r.stdout.split(b"\0") if x]


def _tree(repo: str, base: str) -> tuple[dict[str, str], set[str]]:
    """(path → the signature the checkout of <base> should have written, the submodules' paths, left out)"""
    sigs, gitlinks = {}, set()
    for entry in _z(git(repo, "ls-tree", "-r", "-z", "--full-tree", base, check=True, text=False)):
        meta, _, path = entry.partition("\t")
        mode, kind, oid = meta.split(" ")
        if kind == "blob":
            sigs[path] = f"l:{oid}" if mode == "120000" else f"f:{oid}:{'x' if mode == '100755' else '-'}"
        else:
            gitlinks.add(path)
    return sigs, gitlinks


def _sig(root: str, rel: str, algo: str) -> str | None:
    """the file as it is: f:<blob id>:<x|->, l:<blob id of the link's target>, other (a folder, a FIFO…), or None when
    missing or under a folder that is a symlink (then it is not the file git checked out)."""
    p = pathlib.Path(root)
    for part in pathlib.PurePath(rel).parts[:-1]:
        p = p / part
        if p.is_symlink() or not p.is_dir():
            return None
    p = p / pathlib.PurePath(rel).name
    try:
        st = os.lstat(p)
    except OSError:
        return None
    h = hashlib.new(algo)
    if stat.S_ISLNK(st.st_mode):
        target = os.readlink(os.fsencode(p))
        h.update(b"blob %d\0" % len(target) + target)
        return f"l:{h.hexdigest()}"
    if not stat.S_ISREG(st.st_mode):
        return "other"
    with open(p, "rb") as fh:
        h.update(b"blob %d\0" % os.fstat(fh.fileno()).st_size)
        while chunk := fh.read(1 << 20):
            h.update(chunk)
    return f"f:{h.hexdigest()}:{'x' if st.st_mode & 0o100 else '-'}"


def _algo(tree: dict[str, str]) -> str:
    oid = next(iter(tree.values()), "f:" + "0" * 40).split(":")[1]
    return "sha256" if len(oid) == 64 else "sha1"


def op_checkout_sigs(repo: str, base: str) -> int:
    """JSON {path: signature} for the files of <base> whose checkout in <repo> is not the blob byte for byte (null when
    missing); run right after the worktree is made, before anyone works in it. Usually empty."""
    tree = _tree(repo, base)[0]
    algo = _algo(tree)
    return emit({p: s for p, want in tree.items() if (s := _sig(repo, p, algo)) != want})


def op_changed(repo: str, base: str) -> int:
    """stdin: checkout-sigs' JSON for this worktree. The paths of <base> whose bytes (or link, or executable bit) are no
    longer what the checkout wrote, and the files that are not in <base> (tracked since, or untracked and not ignored).
    A git that fails here fails the op: an empty list would read as "the agent changed nothing"."""
    try:
        wrote = json.loads(sys.stdin.read())
    except ValueError:
        wrote = None
    if not isinstance(wrote, dict):
        sys.stderr.write("changed: stdin is not checkout-sigs' JSON\n")
        return 1
    tree, gitlinks = _tree(repo, base)
    algo = _algo(tree)
    paths = {p for p, want in tree.items() if _sig(repo, p, algo) != wrote.get(p, want)}
    for listing in (("ls-files", "-z"), ("ls-files", "-z", "--others", "--exclude-standard")):
        paths |= {p for p in _z(git(repo, *listing, check=True, text=False))
                  if p not in tree and p not in gitlinks}
    return emit([[p, os.path.lexists(pathlib.Path(repo) / p)] for p in sorted(paths) if p])


def op_copy_back(src: str, dst: str, *rels: str) -> int:
    copied, refused = [], []
    for rel in rels:
        s, d = pathlib.Path(src) / rel, pathlib.Path(dst) / rel
        if ".." in pathlib.PurePath(rel).parts or rel.startswith("/") or s.is_symlink():
            refused.append(rel)
            continue
        if s.exists():
            d.parent.mkdir(parents=True, exist_ok=True)
            if d.is_symlink():
                d.unlink()
            shutil.copy2(s, d)
        elif d.exists() or d.is_symlink():
            d.unlink()
        copied.append(rel)
    return emit({"copied": copied, "refused": refused})


def hook_file(repo: str, hook: str) -> pathlib.Path:
    if "/" in hook:
        sys.exit(2)
    return pathlib.Path(repo) / ".git" / "hooks" / hook


def op_hooks(repo: str, *hooks: str) -> int:
    texts = {}
    for h in hooks:
        f = hook_file(repo, h)
        texts[h] = f"-> {os.readlink(f)}" if f.is_symlink() else f.read_text(errors="replace") if f.is_file() else None
    return emit({"hooks_path": out(repo, "config", "core.hooksPath").strip(), "hooks": texts})


def op_hook_write(repo: str, hook: str) -> int:
    f = hook_file(repo, hook)
    tmp = f.with_name(f".{hook}.agent-hub-new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.write_text(sys.stdin.read())
    tmp.chmod(0o755)
    os.replace(tmp, f)   # replaces a symlink at f instead of writing to where it points
    return 0


def op_hook_remove(repo: str, hook: str) -> int:
    f = hook_file(repo, hook)
    if f.is_symlink() or f.exists():
        f.unlink()
    return 0


def op_bundle_unpushed(ws: str) -> int:
    r = git(ws, "bundle", "create", "-", "--branches", "--not", "--remotes", text=False, stdout=sys.stdout.buffer,
            stderr=subprocess.PIPE)
    if r.returncode and b"empty bundle" in (r.stderr or b""):
        return NOTHING
    sys.stderr.write((r.stderr or b"").decode(errors="replace"))
    return r.returncode


def op_diff_head(ws: str) -> int:
    return git(ws, "diff", "HEAD", "--binary", text=False, stdout=sys.stdout.buffer).returncode


def op_memory_sync(memdir: str, bare: str, branch: str, *flags: str) -> int:
    """git-sync-memory.sh's commit, then what git-safe-push.sh's pull did (merge the bare repo's branch), then a bundle
    of what the bare repo lacks. --lock: take with-lock.sh's lock (the same directory in the git dir); without it the
    caller holds it."""
    if out(memdir, "rev-parse", "--show-toplevel").strip() != str(pathlib.Path(memdir).resolve()):
        sys.stderr.write(f"{memdir} is not a checkout of its own\n")
        return 1
    if "--lock" not in flags:
        return memory_sync(memdir, bare, branch)
    lock = pathlib.Path(out(memdir, "rev-parse", "--absolute-git-dir").strip()) / "agent-hub-op.lock"
    for _ in range(LOCK_WAIT_S):
        try:
            lock.mkdir()
            break
        except FileExistsError:
            time.sleep(1)
    else:
        sys.stderr.write(f"still no lock after {LOCK_WAIT_S}s ({lock}); not synced this time\n")
        return 1
    try:
        return memory_sync(memdir, bare, branch)
    finally:
        lock.rmdir()


def memory_sync(memdir: str, bare: str, branch: str) -> int:
    for p in MEMORY_PATHS:
        if os.path.lexists(pathlib.Path(memdir) / p) or git(memdir, "ls-files", "--error-unmatch", "--", p).returncode == 0:
            git(memdir, "add", "-A", "--", p, env=NO_HOOKS)
    files = out(memdir, "diff", "--cached", "--name-only", "--", "memory/", "STATE.md").split("\n")
    files = [f for f in files if f]
    if files:
        git(memdir, "commit", "-q", "-m", "agent-hub: sync memory/STATE.md", "--", *files, env=NO_HOOKS, check=True)
    up = out(memdir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").strip()
    track = f"refs/remotes/{up}" if up and "/" in up else "refs/agent-hub/memory-remote"
    git(memdir, "fetch", "-q", bare, f"+refs/heads/{branch}:{track}", env=NO_HOOKS, check=True)
    head, remote = out(memdir, "rev-parse", "HEAD").strip(), out(memdir, "rev-parse", track).strip()
    if git(memdir, "merge-base", "--is-ancestor", remote, head).returncode != 0:
        r = git(memdir, "merge", "--no-edit", "-q", track, env=NO_HOOKS)
        if r.returncode != 0:
            git(memdir, "merge", "--abort", env=NO_HOOKS)
            sys.stderr.write(f"the memory branch could not merge {bare}'s {branch}, possibly a real content conflict; "
                             f"a person has to look: {r.stderr.strip()[-300:]}\n")
            return 1
        head = out(memdir, "rev-parse", "HEAD").strip()
    if head == remote:
        return 0
    sys.stdout.flush()
    return git(memdir, "bundle", "create", "-q", "-", "HEAD", f"^{remote}", text=False,
               stdout=sys.stdout.buffer).returncode


def op_exclude(repo: str, pattern: str) -> int:
    """pattern into the repo's .git/info/exclude (shared by its worktrees) when it isn't there yet."""
    common = out(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    if not common:
        return 1
    ex = pathlib.Path(common) / "info" / "exclude"
    lines = ex.read_text().splitlines() if ex.exists() else []
    if pattern not in lines:
        ex.parent.mkdir(parents=True, exist_ok=True)
        ex.write_text("\n".join(lines + [pattern]) + "\n")
    return 0


def op_save(ws: str, folder: str, name: str) -> int:
    """stdin's bytes into <ws>/<folder>/<name>, a new file (a number added when the name is taken); prints its path
    relative to ws."""
    if "/" in folder or "/" in name or folder.startswith(".") or not name:
        return 2
    d = pathlib.Path(ws) / folder
    d.mkdir(exist_ok=True)
    data, stem = sys.stdin.buffer.read(), name
    for i in range(1, 1000):
        try:
            fd = os.open(d / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        except FileExistsError:
            name = f"{i}-{stem}"
            continue
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        print(f"{folder}/{name}")
        return 0
    return 1


def op_head(ws: str) -> int:
    """JSON {branch, dirty}: the checked-out branch (null when detached) and whether tracked files have changes. A git
    that fails (exit 128: it can't read the repo, or the sandbox denied it something) is an error, not a detached HEAD
    or a clean tree: symbolic-ref -q exits 1 only for a detached HEAD."""
    b = git(ws, "symbolic-ref", "--short", "-q", "HEAD")
    if b.returncode not in (0, 1):
        sys.stderr.write(f"git symbolic-ref: {b.stderr.strip()[-300:]}\n")
        return 1
    s = git(ws, "status", "--porcelain", "--untracked-files=no", check=True)
    return emit({"branch": b.stdout.strip() if b.returncode == 0 else None, "dirty": bool(s.stdout.strip())})


def op_bundle_head(ws: str) -> int:
    """stdout: a bundle of the checked-out branch (its whole history)."""
    b = out(ws, "symbolic-ref", "--short", "-q", "HEAD").strip()
    if not b:
        return 1
    return git(ws, "bundle", "create", "-q", "-", f"refs/heads/{b}", text=False, stdout=sys.stdout.buffer).returncode


def op_set_origin(ws: str, url: str, branch: str) -> int:
    """origin = url, and the branch tracks origin's branch of the same name (the platform pushed it)."""
    has = git(ws, "remote", "get-url", "origin").returncode == 0
    for args in (["remote", "set-url" if has else "add", "origin", url],
                 ["config", f"branch.{branch}.remote", "origin"],
                 ["config", f"branch.{branch}.merge", f"refs/heads/{branch}"]):
        if git(ws, *args).returncode != 0:
            return 1
    return 0


OPS = {"worktrees": op_worktrees, "ignored": op_ignored, "worktree-add": op_worktree_add,
       "worktree-remove": op_worktree_remove, "branch-delete": op_branch_delete, "remove-dep": op_remove_dep,
       "checkout-sigs": op_checkout_sigs, "changed": op_changed, "copy-back": op_copy_back, "hooks": op_hooks,
       "hook-write": op_hook_write, "hook-remove": op_hook_remove, "bundle-unpushed": op_bundle_unpushed, "diff-head": op_diff_head,
       "memory-sync": op_memory_sync, "exclude": op_exclude, "save": op_save, "head": op_head,
       "bundle-head": op_bundle_head, "set-origin": op_set_origin}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in OPS:
        sys.stderr.write(f"usage: project-git-job.py {{{'|'.join(OPS)}}} …\n")
        sys.exit(2)
    sys.exit(OPS[sys.argv[1]](*sys.argv[2:]))
