"""Showing the user a project file during a deep conversation (docs/design/lease-show-file.md): check it, copy it,
read it back. Independent of the runtime.

When lease-runner receives "show them this" (`POST /v1/internal/<lease>/show`; on the session side each runtime's
tool is wired to it), it calls take():
  1. Inside the project folder: resolved relative to the project folder, and the real path must be inside the folder
     (`../` and symlinks pointing out are both blocked), and it must be a regular file
  2. Type: the extension is in TYPES, **and the start of the content must match** (images/pdf/glb by magic bytes,
     text must be UTF-8); no svg or html
  3. Size: each ≤ MAX_FILE; per lease at most MAX_COUNT files totalling MAX_TOTAL **within the last WINDOW_S**
     (10-25 m10252240: the same deep conversation gets resumed for several days with images rendered over and over;
     dozens a day is normal; counting over the whole lease would block long-lived leases. The overall volume is
     managed by prune()/KEEP_DAYS)
  4. Credential-like file names, paths declared under the registry's secrets:, and text files whose content looks
     like a credential (private key, API key) are never sent
What counts is the fd that was opened, not the string path (10-25 review): the runner is not in the sandbox, so a
session could use it to read files for it (confused deputy) --
  hard links (st_nlink > 1) are refused; after opening, take the path of the file actually opened (macOS F_GETPATH,
  Linux /proc/self/fd; neither: not sent) and check again that it is inside the folder and not a secret.
After the checks the content is copied into the lease's own directory (which the session cannot write), and only
that copy is sent: swapping the file between the check and the send achieves nothing, and the file can still be
seen after the original is changed or deleted. Kept for KEEP_DAYS (prune()). Standard library.

  take(ws, path, summary, store, secret_paths=()) -> (meta, "") or (None, reason for refusing)
  read(store, file_id) -> (meta, bytes) or None
  prune(store, older_than_s)                     delete what is past the limit
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import secrets
import stat
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402

_ = i18n.domain(__file__)

MAX_FILE = 20 * 1024 * 1024
MAX_COUNT = 100                  # within the last WINDOW_S
MAX_TOTAL = 500 * 1024 * 1024    # within the last WINDOW_S
WINDOW_S = 24 * 3600
KEEP_DAYS = 30
TEXT = "text"
# extension -> (mime, the check the start of the content must pass)
TYPES = {
    ".png": ("image/png", lambda b: b.startswith(b"\x89PNG\r\n\x1a\n")),
    ".jpg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".jpeg": ("image/jpeg", lambda b: b.startswith(b"\xff\xd8\xff")),
    ".gif": ("image/gif", lambda b: b[:6] in (b"GIF87a", b"GIF89a")),
    ".webp": ("image/webp", lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP"),
    ".pdf": ("application/pdf", lambda b: b.startswith(b"%PDF-")),
    ".glb": ("model/gltf-binary", lambda b: b.startswith(b"glTF")),
    ".txt": ("text/plain; charset=utf-8", TEXT),
    ".md": ("text/markdown; charset=utf-8", TEXT),
    ".json": ("application/json", TEXT),
    ".csv": ("text/csv; charset=utf-8", TEXT),
}
SECRET_NAME = re.compile(r"(^\.env|\.pem$|\.key$|\.p12$|\.pfx$|^id_(rsa|ed25519|ecdsa)|secret|token|credential|password)", re.I)
# a text file whose content looks like a credential is not sent (when the name does not show it, e.g. the private key
# in service-account.json)
SECRET_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\"private_key\"\s*:|sk-ant-[A-Za-z0-9_-]{10}|"
                         r"\bghp_[A-Za-z0-9]{20}|\bgithub_pat_[A-Za-z0-9_]{20}|\bAKIA[0-9A-Z]{16}\b|\bxox[bpas]-[A-Za-z0-9-]{10}")
F_GETPATH = getattr(fcntl, "F_GETPATH", 50)  # macOS: the path the fd actually refers to
ID = re.compile(r"^[0-9a-f]{16}$")


def _fd_path(fd: int) -> pathlib.Path | None:
    """Where the file the fd actually opened is (not the string path given). None when this system cannot tell (the
    caller then refuses to send)."""
    if hasattr(fcntl, "F_GETPATH") or os.uname().sysname == "Darwin":
        raw = fcntl.fcntl(fd, F_GETPATH, bytes(1024)).split(b"\0", 1)[0]
        return pathlib.Path(os.fsdecode(raw)).resolve()
    proc = pathlib.Path(f"/proc/self/fd/{fd}")
    return pathlib.Path(os.readlink(proc)).resolve() if proc.exists() else None


def _inside(ws: pathlib.Path, p: pathlib.Path) -> bool:
    return ws in p.parents


def take(ws, path: str, summary: str | None, store, secret_paths=()) -> tuple:
    ws, store = pathlib.Path(ws).resolve(), pathlib.Path(store)
    if not isinstance(path, str) or not path.strip():
        return None, _("give the path of a file in the project folder")
    p = pathlib.Path(path)
    real = (p if p.is_absolute() else ws / p).resolve()
    if not _inside(ws, real):
        return None, _("{path} is not in this project folder (only files in the project folder can be shown)", path=path)
    rel = str(real.relative_to(ws))
    ext = real.suffix.lower()
    if ext not in TYPES:
        return None, _("{name}: this type cannot be sent (allowed: {types})", name=real.name,
                       types=" ".join(sorted(e[1:] for e in TYPES)))
    if SECRET_NAME.search(real.name) or any(real == pathlib.Path(s).resolve() for s in secret_paths):
        return None, _("{name} looks like a credential or a secret; not sent", name=real.name)
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        return None, _("{path} cannot be opened: {err}", path=rel, err=e.strerror)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, _("{path} is not a regular file", path=rel)
        if st.st_nlink > 1:  # a hard link may point at a file outside the folder (resolve and O_NOFOLLOW can't tell)
            return None, _("{path} is a hard link (it may point outside the folder); not sent", path=rel)
        try:
            opened = _fd_path(fd)
        except OSError:
            opened = None
        if opened is None or not _inside(ws, opened) or any(opened == pathlib.Path(s).resolve() for s in secret_paths):
            return None, _("{path}: what was opened is not that file in the folder (was the path swapped?); not sent", path=rel)
        if st.st_size > MAX_FILE:
            return None, _("{path} is too big ({mb} MB; the limit is {limit} MB)", path=rel, mb=st.st_size // 1024 // 1024,
                               limit=MAX_FILE // 1024 // 1024)
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_FILE + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_FILE:
        return None, _("{path} is too big (the limit is {limit} MB)", path=rel, limit=MAX_FILE // 1024 // 1024)
    mime, ok = TYPES[ext]
    if ok == TEXT:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return None, _("{path} is not UTF-8 text", path=rel)
        if SECRET_TEXT.search(text):
            return None, _("{path}: the content looks like a credential (a private key or an API key); not sent", path=rel)
    elif not ok(data):
        return None, _("{path}: the content is not {type} (the extension does not match the content)", path=rel, type=ext[1:])
    # The limit is read-then-write: a lease runs one turn at a time and tools one at a time, so two shows are never
    # counted at once
    since = time.time() - WINDOW_S
    kept = [k for k in (json.loads(m.read_text()) for m in store.glob("*.json")) if k.get("at", 0) >= since] \
        if store.is_dir() else []
    hours = WINDOW_S // 3600
    if len(kept) >= MAX_COUNT:
        return None, _("This lease has already shown {n} {n:file|files} in the last {hours} {hours:hour|hours}, which "
                       "is the limit; show it later, or say in text where the file is for now", hours=hours, n=MAX_COUNT)
    if sum(k.get("size", 0) for k in kept) + len(data) > MAX_TOTAL:
        return None, _("The files this lease has shown in the last {hours} {hours:hour|hours} are over {mb} MB in "
                       "total; show it later, or say in text where the file is for now", hours=hours, mb=MAX_TOTAL // 1024 // 1024)
    store.mkdir(parents=True, exist_ok=True)
    fid = secrets.token_hex(8)
    (store / fid).write_bytes(data)
    meta = {"id": fid, "path": rel, "name": real.name, "mime": mime, "size": len(data)}
    if summary:
        meta["summary"] = str(summary)[:300]
    (store / f"{fid}.json").write_text(json.dumps({**meta, "at": time.time()}, ensure_ascii=False))
    return meta, ""


def read(store, file_id: str):
    store = pathlib.Path(store)
    if not isinstance(file_id, str) or not ID.match(file_id):
        return None
    try:
        meta = json.loads((store / f"{file_id}.json").read_text())
        return meta, (store / file_id).read_bytes()
    except (OSError, ValueError):
        return None


def prune(store, older_than_s: float) -> int:
    store, n = pathlib.Path(store), 0
    if not store.is_dir():
        return 0
    cutoff = time.time() - older_than_s
    for m in store.glob("*.json"):
        try:
            if json.loads(m.read_text()).get("at", 0) < cutoff:
                (store / m.stem).unlink(missing_ok=True)
                m.unlink()
                n += 1
        except (OSError, ValueError):
            continue
    for f in store.iterdir():  # content without meta (crashed halfway through writing)
        if ID.match(f.name) and not (store / f"{f.name}.json").exists() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            n += 1
    return n
