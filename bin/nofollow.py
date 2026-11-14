"""Reading and replacing a file inside a project's folder with the platform's authority, without following a link the
project planted or blocking on a FIFO it put there (round 9, 11-01: render-project-settings wrote a sandboxed project's
settings file through a link to the user-level one).

Every folder below the base is opened with O_NOFOLLOW from the one above it (directory handles, so nothing can be
swapped in between); the file is read only when it is a regular file, opened without blocking; a write creates a new
file next to it and renames it into place, replacing a link instead of writing through it. The base itself (a project's
workspace) is opened normally: its parent is not the project's to change. Standard library only.

  read(base, rel, cap) → bytes | None        None: the file or a folder on the way is missing
  replace(base, rel, data)                   creates missing folders
  append(base, rel, data)                    adds to the end; creates the file and only the folder it is in
  entries(base, rel) → [name] | None         the names in a folder (None: missing); what each is isn't looked at
  remove(base, rel)                          unlink one entry (a link is removed, never what it points to); missing
                                             is fine
  is_dir(base, rel) → bool                   this entry itself is a folder (a link to one isn't); missing: False
  remove_dir(base, rel)                      remove one empty folder; OSError if it isn't empty
  make_dirs(base, rel)                       the folder and the missing ones above it (a link on the way: OSError)
All raise OSError for a link, a FIFO or anything else that isn't a regular file / folder on the way (read: the file
itself too), and read for a file over cap.
"""
from __future__ import annotations

import errno
import os
import pathlib
import stat

FLAGS = os.O_NOFOLLOW | os.O_CLOEXEC
DIR = os.O_RDONLY | os.O_DIRECTORY


def _parts(rel) -> tuple[str, ...]:
    parts = pathlib.PurePosixPath(rel).parts
    if not parts or pathlib.PurePosixPath(rel).is_absolute() or any(p in ("..", ".") for p in parts):
        raise OSError(errno.EINVAL, f"not a plain relative path: {rel}")
    return parts


def _folder(base, parts, create: bool) -> int:
    fd = os.open(base, DIR | os.O_CLOEXEC)
    try:
        for p in parts:
            try:
                nfd = os.open(p, DIR | FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(p, 0o755, dir_fd=fd)
                nfd = os.open(p, DIR | FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nfd
    except BaseException:
        os.close(fd)
        raise
    return fd


def read(base, rel, cap: int = 1 << 20) -> bytes | None:
    parts = _parts(rel)
    try:
        d = _folder(base, parts[:-1], create=False)
    except FileNotFoundError:
        return None
    try:
        try:
            f = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | FLAGS, dir_fd=d)
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(f).st_mode):
                raise OSError(errno.EINVAL, f"{rel} is not a regular file")
            chunks, n = [], 0
            while chunk := os.read(f, 1 << 16):
                n += len(chunk)
                if n > cap:
                    raise OSError(errno.EFBIG, f"{rel} is over {cap} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            os.close(f)
    finally:
        os.close(d)


def replace(base, rel, data: bytes) -> None:
    parts = _parts(rel)
    d = _folder(base, parts[:-1], create=True)
    tmp = f".{parts[-1]}.{os.getpid()}.agent-hub-new"
    try:
        f = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | FLAGS, 0o644, dir_fd=d)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(f, view):]
        finally:
            os.close(f)
        os.replace(tmp, parts[-1], src_dir_fd=d, dst_dir_fd=d)
    except BaseException:
        try:
            os.unlink(tmp, dir_fd=d)
        except OSError:
            pass
        raise
    finally:
        os.close(d)


def append(base, rel, data: bytes) -> None:
    """Add data to the end of a file, creating it and only its own folder when missing (every folder above that must be
    there already: a project's notes go into its .agent-memory, never one this makes). The file must be a regular file
    with one link: a hard link to a file outside the project would otherwise take the write (lease_files.py checks the
    same)."""
    parts = _parts(rel)
    d = _folder(base, parts[:-2], create=False)
    try:
        if len(parts) > 1:
            try:
                nd = os.open(parts[-2], DIR | FLAGS, dir_fd=d)
            except FileNotFoundError:
                os.mkdir(parts[-2], 0o755, dir_fd=d)
                nd = os.open(parts[-2], DIR | FLAGS, dir_fd=d)
            os.close(d)
            d = nd
        f = os.open(parts[-1], os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | FLAGS, 0o644, dir_fd=d)
        try:
            st = os.fstat(f)
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise OSError(errno.EINVAL, f"{rel} is not a regular file with one link")
            view = memoryview(data)
            while view:
                view = view[os.write(f, view):]
        finally:
            os.close(f)
    finally:
        os.close(d)


def entries(base, rel) -> list[str] | None:
    try:
        d = _folder(base, _parts(rel), create=False)
    except FileNotFoundError:
        return None
    try:
        return os.listdir(d)
    finally:
        os.close(d)


def is_dir(base, rel) -> bool:
    parts = _parts(rel)
    try:
        d = _folder(base, parts[:-1], create=False)
    except FileNotFoundError:
        return False
    try:
        return stat.S_ISDIR(os.stat(parts[-1], dir_fd=d, follow_symlinks=False).st_mode)
    except FileNotFoundError:
        return False
    finally:
        os.close(d)


def make_dirs(base, rel) -> None:
    os.close(_folder(base, _parts(rel), create=True))


def remove_dir(base, rel) -> None:
    parts = _parts(rel)
    d = _folder(base, parts[:-1], create=False)
    try:
        os.rmdir(parts[-1], dir_fd=d)
    finally:
        os.close(d)


def remove(base, rel) -> None:
    parts = _parts(rel)
    try:
        d = _folder(base, parts[:-1], create=False)
    except FileNotFoundError:
        return
    try:
        os.unlink(parts[-1], dir_fd=d)
    except FileNotFoundError:
        pass
    finally:
        os.close(d)
