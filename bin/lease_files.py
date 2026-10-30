"""深談裡把專案的檔案給使用者看(docs/design/lease-show-file.md):檢查、複製一份、讀回來。跟執行環境無關。

lease-runner 收到「給他看這個」(`POST /v1/internal/<lease>/show`,session 那邊由各 runtime 的工具接上來)就呼叫 take():
  1. 在專案資料夾裡:相對專案資料夾解析,解析成真實路徑後要在資料夾裡(`../`、symlink 指出去都擋),而且是一般檔案
  2. 類型:副檔名在 TYPES 裡,**內容開頭也要對**(圖片/pdf/glb 看 magic bytes,文字要是 UTF-8);不收 svg、html
  3. 大小:每個 ≤ MAX_FILE;每個租約**最近 WINDOW_S 內**最多 MAX_COUNT 個、合計 MAX_TOTAL(10-25 m10252240:同一段深談
     會被接回好幾天,一直迭代算圖,一天幾十張很正常;算整個租約的話長壽租約會被卡死。總量由 prune()/KEEP_DAYS 管)
  4. 看起來像憑證的檔名、專案登錄 secrets: 宣告的路徑、內容像憑證的文字檔(私鑰、API key)一律不送
以「打開的那個 fd」為準,不是以字串路徑為準(10-25 review):runner 不在 sandbox 裡,session 可以借它的手讀檔(confused deputy)——
  hard link(st_nlink > 1)拒絕;打開之後拿實際打開的檔的路徑(macOS F_GETPATH、Linux /proc/self/fd;都沒有就不送),
  再檢查一次在資料夾裡、不是 secrets。
檢查完把內容複製到租約自己的目錄(session 寫不到),之後只從這份送:檢查跟送出之間被換掉沒用,原檔改了刪了也還看得到。
保留 KEEP_DAYS(prune())。標準函式庫。

  take(ws, path, summary, store, secret_paths=()) -> (meta, "") 或 (None, 拒絕的原因)
  read(store, file_id) -> (meta, bytes) 或 None
  prune(store, older_than_s)                     刪掉超過期限的
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
MAX_COUNT = 100                  # 最近 WINDOW_S 內
MAX_TOTAL = 500 * 1024 * 1024    # 最近 WINDOW_S 內
WINDOW_S = 24 * 3600
KEEP_DAYS = 30
TEXT = "text"
# 副檔名 → (mime, 內容開頭要符合的檢查)
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
# 文字檔的內容像憑證就不送(檔名看不出來的,例:service-account.json 裡的私鑰)
SECRET_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\"private_key\"\s*:|sk-ant-[A-Za-z0-9_-]{10}|"
                         r"\bghp_[A-Za-z0-9]{20}|\bgithub_pat_[A-Za-z0-9_]{20}|\bAKIA[0-9A-Z]{16}\b|\bxox[bpas]-[A-Za-z0-9-]{10}")
F_GETPATH = getattr(fcntl, "F_GETPATH", 50)  # macOS:fd 實際對應的路徑
ID = re.compile(r"^[0-9a-f]{16}$")


def _fd_path(fd: int) -> pathlib.Path | None:
    """fd 實際打開的檔在哪(不是當初給的字串路徑)。這個系統問不到就是 None(呼叫端拒絕送)。"""
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
        if st.st_nlink > 1:  # hard link 可能指著資料夾外的檔(resolve、O_NOFOLLOW 都看不出來)
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
    # 上限是先讀再寫:同一個租約一次只跑一個回合、工具一次一個,不會兩個 show 同時算
    since = time.time() - WINDOW_S
    kept = [k for k in (json.loads(m.read_text()) for m in store.glob("*.json")) if k.get("at", 0) >= since] \
        if store.is_dir() else []
    hours = WINDOW_S // 3600
    if len(kept) >= MAX_COUNT:
        return None, _("This lease already showed {n} files in the last {hours} hours, the limit; show it later, or say in "
                        "text where the file is for now", hours=hours, n=MAX_COUNT)
    if sum(k.get("size", 0) for k in kept) + len(data) > MAX_TOTAL:
        return None, _("The files this lease showed in the last {hours} hours are over {mb} MB in total; show it later, or "
                        "say in text where the file is for now", hours=hours, mb=MAX_TOTAL // 1024 // 1024)
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
    for f in store.iterdir():  # 寫到一半就掛掉、沒有 meta 的內容
        if ID.match(f.name) and not (store / f"{f.name}.json").exists() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)
            n += 1
    return n
