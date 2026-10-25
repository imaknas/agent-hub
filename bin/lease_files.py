"""深談裡把專案的檔案給使用者看(docs/design/lease-show-file.md):檢查、複製一份、讀回來。跟執行環境無關。

lease-runner 收到「給他看這個」(`POST /v1/internal/<lease>/show`,session 那邊由各 runtime 的工具接上來)就呼叫 take():
  1. 在專案資料夾裡:相對專案資料夾解析,解析成真實路徑後要在資料夾裡(`../`、symlink 指出去都擋),而且是一般檔案
  2. 類型:副檔名在 TYPES 裡,**內容開頭也要對**(圖片/pdf/glb 看 magic bytes,文字要是 UTF-8);不收 svg、html
  3. 大小:每個 ≤ MAX_FILE;每個租約最多 MAX_COUNT 個、合計 MAX_TOTAL
  4. 看起來像憑證的檔名、專案登錄 secrets: 宣告的路徑一律不送
檢查完把內容複製到租約自己的目錄(session 寫不到),之後只從這份送:檢查跟送出之間被換掉沒用,原檔改了刪了也還看得到。
保留 KEEP_DAYS(prune())。標準函式庫。

  take(ws, path, summary, store, secret_paths=()) -> (meta, "") 或 (None, 拒絕的原因)
  read(store, file_id) -> (meta, bytes) 或 None
  prune(store, older_than_s)                     刪掉超過期限的
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import secrets
import stat
import time

MAX_FILE = 20 * 1024 * 1024
MAX_COUNT = 50
MAX_TOTAL = 200 * 1024 * 1024
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
ID = re.compile(r"^[0-9a-f]{16}$")


def _inside(ws: pathlib.Path, p: pathlib.Path) -> bool:
    return ws in p.parents


def take(ws, path: str, summary: str | None, store, secret_paths=()) -> tuple:
    ws, store = pathlib.Path(ws).resolve(), pathlib.Path(store)
    if not isinstance(path, str) or not path.strip():
        return None, "要給專案資料夾裡的檔案路徑"
    p = pathlib.Path(path)
    real = (p if p.is_absolute() else ws / p).resolve()
    if not _inside(ws, real):
        return None, f"{path} 不在這個專案資料夾裡(只能給專案資料夾裡的檔案)"
    rel = str(real.relative_to(ws))
    ext = real.suffix.lower()
    if ext not in TYPES:
        return None, f"{real.name} 的類型不能送(可以:{' '.join(sorted(e[1:] for e in TYPES))})"
    if SECRET_NAME.search(real.name) or any(real == pathlib.Path(s).resolve() for s in secret_paths):
        return None, f"{real.name} 看起來是憑證或機密,不送"
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as e:
        return None, f"{rel} 打不開:{e.strerror}"
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None, f"{rel} 不是一般檔案"
        if st.st_size > MAX_FILE:
            return None, f"{rel} 太大({st.st_size // 1024 // 1024} MB;上限 {MAX_FILE // 1024 // 1024} MB)"
        with os.fdopen(fd, "rb", closefd=False) as f:
            data = f.read(MAX_FILE + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_FILE:
        return None, f"{rel} 太大(上限 {MAX_FILE // 1024 // 1024} MB)"
    mime, ok = TYPES[ext]
    if ok == TEXT:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return None, f"{rel} 不是 UTF-8 文字"
    elif not ok(data):
        return None, f"{rel} 的內容不是 {ext[1:]}(副檔名跟內容對不上)"
    kept = [json.loads(m.read_text()) for m in store.glob("*.json")] if store.is_dir() else []
    if len(kept) >= MAX_COUNT:
        return None, f"這個深談已經給了 {MAX_COUNT} 個檔案,到上限了"
    if sum(k.get("size", 0) for k in kept) + len(data) > MAX_TOTAL:
        return None, f"這個深談給的檔案合計超過 {MAX_TOTAL // 1024 // 1024} MB 了"
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
    return n
