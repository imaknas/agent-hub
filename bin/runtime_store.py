#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The store of the coding agents' checked copies (docs/design/runtime-versions.md section 3; neutral: the tool's own
getting and installing is its adapter's distribution port).

  <hubconf.runtimes_dir()>/<runtime>/
    <version>/            one copy, installed by the distribution port into a folder of its own (an npm prefix for
                          Claude Code), plus .lock (runs hold it shared) and, once promoted, promoted.json
    current, previous     links to a version folder (relative names), each moved by os.replace of a fresh link
    .writer               writers (install, promote, rollback, remove) hold it exclusively, one at a time
    .staging-<random>/    an install in progress: renamed into <version>/ only once it is complete

Readers: hold(runtime) resolves `current` once and keeps a shared flock on its .lock for the run, so the folder isn't
removed under it (the descriptor is read-only and, when asked, inheritable, so a process that execs keeps it);
verify(entry) compares the executable with what promotion recorded (size, mtime and real path every run, the full
sha256 once a day) and says why not. The store is outside every sandbox's writes (the instance directory, or the hub's
.state on an old layout) and out of the backup (rebuilt from the vendor).
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import pathlib
import re
import secrets
import shutil
import sys
from typing import NamedTuple

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
LINKS = ("current", "previous")
PROMOTED = "promoted.json"
KEEP_DAYS = 14


class Entry(NamedTuple):
    runtime: str
    version: str
    folder: pathlib.Path
    exe: pathlib.Path
    promoted: dict


def root(runtime: str) -> pathlib.Path:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", runtime or ""):
        raise ValueError(_("not a runtime name: {name!r}", name=runtime))
    return hubconf.runtimes_dir() / runtime


@contextlib.contextmanager
def writer(runtime: str):
    """One writer of a runtime's store at a time (install, promote, rollback, remove)."""
    d = root(runtime)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(d / ".writer", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield d
    finally:
        os.close(fd)


def _linked(runtime: str, name: str) -> str | None:
    """The version a link names, or None (no link, or one that doesn't name a version folder beside it)."""
    try:
        target = os.readlink(root(runtime) / name)
    except OSError:
        return None
    return target if VERSION.match(target) and (root(runtime) / target).is_dir() else None


def entry(runtime: str, version: str) -> Entry | None:
    """A promoted version's entry, or None (not there, or never promoted)."""
    if not VERSION.match(version or ""):
        return None
    folder = root(runtime) / version
    try:
        promoted = json.loads((folder / PROMOTED).read_text())
    except (OSError, ValueError):
        return None
    rel = promoted.get("exe")
    if not isinstance(rel, str) or rel.startswith("/") or ".." in pathlib.PurePath(rel).parts:
        return None
    return Entry(runtime, version, folder, folder / rel, promoted)


def current(runtime: str) -> Entry | None:
    v = _linked(runtime, "current")
    return entry(runtime, v) if v else None


def previous(runtime: str) -> Entry | None:
    v = _linked(runtime, "previous")
    return entry(runtime, v) if v else None


def versions(runtime: str) -> list[str]:
    d = root(runtime)
    return sorted(p.name for p in d.iterdir() if p.is_dir() and VERSION.match(p.name)) if d.is_dir() else []


def install(runtime: str, version: str, dist) -> pathlib.Path:
    """Install a version with dist.install (the runtime's distribution port) into a staging folder, then rename it in.
    The executable it returns, under the version folder. A version already in place is kept (an unpromoted folder with
    no executable, left by a crash, is replaced). Raises RuntimeError."""
    if not VERSION.match(version or ""):
        raise RuntimeError(_("not a version: {version!r}", version=version))
    with writer(runtime) as d:
        final = d / version
        if final.exists():
            try:
                done = json.loads((final / "installed.json").read_text())
            except (OSError, ValueError):
                done = None
            if done and isinstance(done.get("exe"), str) and (final / done["exe"]).is_file():
                return final / done["exe"]
            shutil.rmtree(final)   # a folder no install finished
        staging = d / f".staging-{secrets.token_hex(4)}"
        try:
            exe = pathlib.Path(dist.install(version, staging))
            rel = exe.relative_to(staging)
            (staging / ".lock").touch(mode=0o600)
            (staging / "installed.json").write_text(json.dumps({"version": version, "exe": str(rel)}) + "\n")
            staging.rename(final)
            return final / rel
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def _relink(d: pathlib.Path, name: str, version: str) -> None:
    tmp = d / f".{name}-{secrets.token_hex(4)}"
    os.symlink(version, tmp)
    os.replace(tmp, d / name)


def _fsync_write(path: pathlib.Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}-{secrets.token_hex(4)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def digest(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def promote(runtime: str, version: str, result_file: str | None = None, expect: str | None = None) -> Entry:
    """Record what this version's executable is now (size, mtime, inode, real path, sha256), then make it current, the
    old current becoming previous. expect: the sha256 the checks ran on; anything else is refused (the checks ran the
    candidate's own code for minutes). Raises RuntimeError."""
    with writer(runtime) as d:
        folder = d / version
        try:
            done = json.loads((folder / "installed.json").read_text())
        except (OSError, ValueError):
            raise RuntimeError(_("{version} isn't installed in the store", version=version)) from None
        exe = folder / done["exe"]
        real = pathlib.Path(os.path.realpath(exe))
        if not real.is_file() or folder.resolve() not in real.parents:
            raise RuntimeError(_("{version}'s executable isn't a file inside its folder", version=version))
        st = real.stat()
        sha = digest(real)
        if expect is not None and sha != expect:
            raise RuntimeError(_("{version} changed while it was checked: not promoted", version=version))
        record = {"version": version, "exe": done["exe"], "real": str(real.relative_to(folder.resolve())),
                  "size": st.st_size, "mtime": st.st_mtime, "ino": st.st_ino, "sha256": sha, "result": result_file,
                  "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        _fsync_write(folder / PROMOTED, json.dumps(record, indent=1) + "\n")
        was = _linked(runtime, "current")
        if was and was != version:
            _relink(d, "previous", was)
        _relink(d, "current", version)
        (d / ".rolled-back").unlink(missing_ok=True)   # a promotion ends a rollback's hold on the version it left
    return current(runtime)


def rolled_back_from(runtime: str) -> str | None:
    """The version a rollback left, which update-runtimes doesn't install again until a newer one is published."""
    try:
        v = (root(runtime) / ".rolled-back").read_text().strip()
    except OSError:
        return None
    return v if VERSION.match(v) else None


def rollback(runtime: str) -> Entry:
    """previous becomes current and current previous. The caller has checked previous first. Raises RuntimeError."""
    with writer(runtime) as d:
        now, was = _linked(runtime, "current"), _linked(runtime, "previous")
        if not was or entry(runtime, was) is None:
            raise RuntimeError(_("no previous version of {runtime} to go back to", runtime=runtime))
        _relink(d, "current", was)
        if now:
            _relink(d, "previous", now)
            _fsync_write(d / ".rolled-back", now + "\n")
    return current(runtime)


@contextlib.contextmanager
def hold(runtime: str, inheritable: bool = False):
    """The current entry for one run, its folder held against removal while the run lasts (None when there is no
    current). inheritable: the descriptor survives exec (a lease that execs the tool keeps holding it)."""
    e = current(runtime)
    if e is None:
        yield None
        return
    fd = os.open(e.folder / ".lock", os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        if inheritable:
            os.set_inheritable(fd, True)
        yield e
    finally:
        os.close(fd)


def verify(e: Entry) -> str | None:
    """None when the executable is still what promotion recorded; else why not."""
    p = e.promoted
    real = pathlib.Path(os.path.realpath(e.exe))
    changed = _("the platform's {runtime} changed after it was checked ({what})", runtime=e.runtime, what="{what}")
    try:
        st = real.stat()
    except OSError:
        return changed.format(what=_("its executable is gone"))
    if str(real) != str(e.folder.resolve() / p.get("real", "")):
        return changed.format(what=_("its executable points elsewhere"))
    if st.st_size != p.get("size") or st.st_mtime != p.get("mtime") or st.st_ino != p.get("ino", st.st_ino):
        return changed.format(what=_("its executable's size or time"))
    stamp = e.folder / ".verified"
    today = datetime.date.today().isoformat()
    try:
        if stamp.read_text().strip() == today:
            return None
    except OSError:
        pass
    if digest(real) != p.get("sha256"):
        return changed.format(what=_("its executable's content"))
    try:
        stamp.write_text(today + "\n")
    except OSError:
        pass
    return None


def prune(runtime: str, keep_days: int = KEEP_DAYS, now: float | None = None) -> list[str]:
    """Remove version folders other than current and previous whose mtime is older than keep_days and that no run
    holds. The versions removed."""
    import time
    now = time.time() if now is None else now
    gone = []
    with writer(runtime) as d:
        keep = {_linked(runtime, n) for n in LINKS}   # read under the lock: a promotion can't move them meanwhile
        for v in versions(runtime):
            folder = d / v
            if v in keep or now - folder.stat().st_mtime < keep_days * 86400:
                continue
            try:
                fd = os.open(folder / ".lock", os.O_RDONLY)
            except OSError:
                fd = None
            try:
                if fd is not None:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        continue   # a run holds it
                shutil.rmtree(folder)
                gone.append(v)
            finally:
                if fd is not None:
                    os.close(fd)
        for st in d.glob(".staging-*"):
            if now - st.stat().st_mtime > 86400:
                shutil.rmtree(st, ignore_errors=True)
        for left in (*d.glob(".current-*"), *d.glob(".previous-*"), *d.glob("*/.promoted.json-*")):
            if now - left.lstat().st_mtime > 86400:   # a writer that crashed between writing and renaming
                left.unlink(missing_ok=True)
    return gone
