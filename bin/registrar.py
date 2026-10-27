#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Principal Handoff Acts 0.3 的 registrar:各專案 STATE.md `## 待辦` 裡只有 owner 能做的事 ↔ 匯流排上的 0.3 record
(規範 acts/SPEC.md §4;每晚 librarian 之後的 job,零 LLM)。取代舊的任務登記。

每個專案(memory 副本的 STATE.md,加上平台自己的 openclaw-migration-status.md),照規範的順序:
  1. 套用:從 principal 端來、還沒 done 的 settled / reopened(hubbus records --direction in --pending)。這件的結案
     是 settled → 刪掉帶這個 key 的行(段落空了補「- (無)」);是 reopened → 那一行不在就用 restore_text 加回。
     note、related 不歸這裡,留給 librarian 用判斷處理。
  2. 補 key:送過、沒結案、沒撤回、文件裡找不到 key 的件,有一行沒 key 的 owner 行文字跟它最新的 terms 一模一樣
     (正規化後)→ 蓋回原本的 key(agent 改寫時把行尾拿掉了),不當成新的一件。
  3. 送出:剩下沒 key 的 owner 行 → key = task-<專案>-<sha1(正規化文字)[:10]>,先送 raised(id r:<key>)、
     收到 accepted/duplicate 才蓋章;送不出去就不蓋,隔晚再試(同一個 id,重送是 no-op)。
  4. 修改:帶 key 的行文字跟最新的 terms 不一樣 → amended(id a:<key>:<rev>,terms_rev +1,supersedes 現在的需求紀錄)。
  5. 對帳:送過、沒結案、沒撤回、還是找不到 key 的件:有很像的行 → 不送、警告一次;否則送一個 claimed
     (outcome done,evidence.basis 是 review-log 裡刪掉那一行的理由;id c:<key>:r<重開次數>)。撤回的件刪了行不 claim。
  6. 有改就 commit + push(memory 副本走 git-safe-push;平台的檔走 commit-hub-files.sh),成功之後才把套用過的
     record `hubbus done`;失敗就留到隔晚。

過渡期(v1 還沒退役):匯流排上 v1 寄過的 task 信跟個人助理的 v1 轉交也算進來(v1_compat),那些 key 在有 record 之前
就存在。v1 退役時連同 v1_compat 一起刪掉。

用法:registrar.py [--dry-run]
"""
from __future__ import annotations

import datetime
import difflib
import hashlib
import json
import pathlib
import re
import shlex
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

HUB = hubconf.ROOT
COPIES = hubconf.p("memory_copies")
HUBBUS = [sys.executable, str(HUB / "bin" / "hubbus")]
WARNED = hubconf.STATE / "registrar-warned.json"   # 警告過的 key(很像的行):只警告一次
REVIEW_LOG = hubconf.data_file("review-log.md")
SIMILAR = 0.6   # 對帳:跟某一行這麼像就當成改寫(實作自己定,規範 §4 第 5 步)
EXT = "acts/0.3"
TAG = hubconf.owner_tag()
REGISTRAR = hubconf.party("registrar")
OWNERS = sorted(hubconf.owner_addresses())
ITEM = re.compile(rf"^- \[ \] 〔{re.escape(TAG)}〕\s*(.+)$")
GROUP = re.compile(r"^#{3,4}\s*〔([^〕]+)〕\s*$")
BARE = re.compile(r"^- \[ \] (?!〔)(.+)$")
LABELLED = re.compile(r"^- \[ \] 〔[^〕]+〕\s*(.+)$")
# zh-hant profile 的 marker_pattern:半形/全形括號跟冒號、「等」後面有沒有空格都認,要在行尾
KEY = re.compile(rf"[(（]等 ?{re.escape(TAG)}[:：]\s*([^()（）]+?)\s*[)）]\s*$")
DRY = "--dry-run" in sys.argv


def log(msg: str) -> None:
    print(f"[registrar] {msg}", flush=True)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(text: str) -> str:
    return " ".join(text.split())


def task_key(project: str, text: str) -> str:
    return f"task-{project}-{hashlib.sha1(norm(text).encode()).hexdigest()[:10]}"


def stamp(key: str) -> str:
    return f"(等 {TAG}:{key})"


def hubbus(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([*HUBBUS, *args], input=stdin, capture_output=True, text=True, timeout=60)


# --- 文件 ---

def todo_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = next((i for i, l in enumerate(lines) if l.strip() == "## 待辦"), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start + 1, end


def owner_lines(lines: list[str]) -> list[dict]:
    """段落裡 owner 的行:{i, text(去掉 key 標記), key}。"""
    b, out, group = todo_bounds(lines), [], None
    for i in range(*(b or (0, 0))):
        l = lines[i].rstrip()
        if m := GROUP.match(l):
            group = m.group(1)
            continue
        m = ITEM.match(l) or (BARE.match(l) if group == TAG else None)
        if m:
            body = m.group(1)
            k = KEY.search(body)
            out.append({"i": i, "text": (body[:k.start()] if k else body).strip(), "key": k.group(1) if k else None})
    return out


def line_texts(lines: list[str]) -> list[str]:
    """段落裡所有待辦行的文字(去掉標籤跟 key 標記),給對帳比像不像。"""
    b, out = todo_bounds(lines), []
    for i in range(*(b or (0, 0))):
        l = lines[i].rstrip()
        m = LABELLED.match(l) or BARE.match(l)
        if m:
            out.append(norm(KEY.sub("", m.group(1))))
    return out


def strike(lines: list[str], key: str) -> bool:
    hits = [i for i, l in enumerate(lines) if (m := KEY.search(l.rstrip())) and m.group(1) == key]
    for i in reversed(hits):
        del lines[i]
    b = todo_bounds(lines)
    if hits and b and not any(lines[i].strip() for i in range(*b)):
        lines.insert(b[0], "- (無)")
    return bool(hits)


def readd(lines: list[str], key: str, text: str) -> bool:
    if any((m := KEY.search(l.rstrip())) and m.group(1) == key for l in lines):
        return False
    new = f"- [ ] 〔{TAG}〕{norm(text)}{stamp(key)}"
    b = todo_bounds(lines)
    if b is None:
        lines += ["", "## 待辦", "", new]
        return True
    body = [i for i in range(*b) if lines[i].strip()]
    if len(body) == 1 and lines[body[0]].strip() == "- (無)":
        lines[body[0]] = new
    else:
        lines.insert((body[-1] + 1) if body else b[0], new)
    return True


# --- 匯流排上的紀錄 ---

def _when(r: dict) -> tuple:
    return (r.get("ts", ""), r.get("id", ""))


def current(recs: list[dict]) -> dict | None:
    """core §6:沒被別筆 supersedes 的裡面最新的(ts 一樣比 id)。"""
    if not recs:
        return None
    replaced = {s for r in recs for s in r.get("supersedes", [])}
    heads = [r for r in recs if r["id"] not in replaced] or recs
    return max(heads, key=_when)


class Ledger:
    """一個專案在匯流排上的 Acts 紀錄,照 key 整理。"""

    def __init__(self, project: str):
        r = hubbus("records", "--project", project, "--ext", EXT)
        try:
            events = json.loads(r.stdout or "[]")
        except ValueError:
            raise SystemExit(f"讀不到 hubbus records(exit {r.returncode}):{r.stderr.strip()[-200:]}")
        self.need, self.settle, self.pending = {}, {}, []
        for e in events:
            rec = e["record"]
            if e["direction"] == "out" and rec["type"] in ("raised", "amended", "withdrawn"):
                self.need.setdefault(rec["key"], []).append(rec)
            elif e["direction"] == "in" and rec["type"] in ("settled", "reopened"):
                self.settle.setdefault(rec["key"], []).append(rec)
                if not e["done"]:
                    self.pending.append(e)
        self.v1_posted, self.v1_closed, self.v1_pending = v1_compat(project)

    def terms(self, key: str) -> tuple[str | None, int, str | None]:
        """(最新的 terms 文字, 最新的 terms_rev, 現在的需求紀錄 id)。"""
        recs = self.need.get(key, [])
        with_terms = [r for r in recs if r["type"] in ("raised", "amended")]
        latest = max(with_terms, key=lambda r: (r.get("terms_rev", 0), *_when(r))) if with_terms else None
        cur = current(recs)
        if latest is None:
            return self.v1_posted.get(key), 1 if key in self.v1_posted else 0, None
        return latest["terms"]["text"], latest.get("terms_rev", 1), cur["id"] if cur else None

    def known(self) -> set[str]:
        return set(self.need) | set(self.v1_posted)

    def settled(self, key: str) -> bool:
        cur = current(self.settle.get(key, []))
        return (cur is not None and cur["type"] == "settled") or (cur is None and key in self.v1_closed)

    def withdrawn(self, key: str) -> bool:
        cur = current(self.need.get(key, []))
        return cur is not None and cur["type"] == "withdrawn"

    def reopens(self, key: str) -> int:
        return sum(1 for r in self.settle.get(key, []) if r["type"] == "reopened")

    def open(self) -> list[str]:
        return sorted(k for k in self.known() if not self.settled(k) and not self.withdrawn(k))


def v1_compat(project: str) -> tuple[dict, set, list]:
    """過渡期:v1 寄過的 task(key → 文字)、v1 收過結案轉交的 key、還沒套用的 v1 帶 key 轉交。v1 退役時刪掉。"""
    db = hubconf.bus_db()
    if not db.exists():
        return {}, set(), []
    marks = ",".join("?" * len(OWNERS))
    c = sqlite3.connect(db, timeout=30)
    posted = {r[0]: json.loads(r[1]).get("text", "") for r in c.execute(
        f"SELECT id, payload FROM events WHERE type='message' AND to_project IN ({marks}) AND source=? AND id LIKE 'task-%'",
        (*OWNERS, project))}
    closed = set()
    for (pl,) in c.execute(f"SELECT payload FROM events WHERE type='message' AND source IN ({marks}) AND to_project=?"
                           " AND payload LIKE '%reply_to%'", (*OWNERS, project)):
        h = json.loads(pl).get("handoff") or {}
        if h.get("effect") in ("done", "dropped", "already_done") and h.get("reply_to"):
            closed.add(h["reply_to"])
    r = hubbus("open-handoffs")
    try:
        pending = [h for h in json.loads(r.stdout or "[]") if h.get("project") == project and h.get("reply_to")
                   and h.get("effect") in ("done", "dropped", "already_done", "reopened")]
    except ValueError:
        pending = []
    return posted, closed, pending


def publish(recs: list[dict]) -> dict[str, str]:
    """送出,回傳 {record id: status}。"""
    if not recs:
        return {}
    if DRY:
        for rec in recs:
            log(f"(dry-run) 會送 {rec['type']} {rec['key']}")
        return {rec["id"]: "dry-run" for rec in recs}
    r = hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
    try:
        res = {x["id"]: x["status"] for x in json.loads(r.stdout or "[]")}   # 每筆 {id: record id, event, status}
    except ValueError:
        res = {}
    for rec in recs:
        st = res.get(rec["id"], "failed")
        if st not in ("accepted", "duplicate"):
            log(f"{rec['project']}: {rec['type']} {rec['key']} 送不出去({st}):{r.stderr.strip()[-160:]}")
    return res


def record(project: str, type_: str, key: str, rid: str, **kw) -> dict:
    return {"ph": "0.3", "ext": EXT, "id": rid, "type": type_, "origin": kw.pop("origin", "requester"),
            "by": REGISTRAR, "key": key, "project": project, "ts": now(), **kw}


def removal_reason(key: str) -> str | None:
    """review-log 裡刪掉帶這個 key 那一行的那一次:「<標題>:<理由>」(最後一次)。"""
    if not REVIEW_LOG.exists():
        return None
    head = why = found = None
    for line in REVIEW_LOG.read_text().splitlines():
        if line.startswith("## "):
            head, why = line[3:].strip(), None
        elif line.startswith("**理由**"):
            why = re.sub(r"^\*\*理由\*\*\s*[::]\s*", "", line).strip()
        elif line.startswith("-") and not line.startswith("---") and key in line:
            found = f"{head}:{why}" if why else head
    return found


def targets() -> list[tuple[str, pathlib.Path, str]]:
    """(專案, 檔案, 怎麼 commit)"""
    out = [(d.name, d / "STATE.md", "copy") for d in sorted(COPIES.iterdir())
           if (d / ".git").exists() and (d / "STATE.md").exists()]
    mig = hubconf.data_file("openclaw-migration-status.md")
    if mig.exists():
        out.append((hubconf.hub_project(), mig, "hub"))
    return out


def commit(project: str, path: pathlib.Path, how: str, msg: str) -> bool:
    if how == "hub":
        r = subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), msg, path.name], cwd=HUB,
                           capture_output=True, text=True)
    else:
        r = subprocess.run([str(HUB / "bin" / "with-lock.sh"), "bash", "-c",
                            f"git add STATE.md && git commit -qm {shlex.quote(msg)} && {HUB}/bin/git-safe-push.sh"],
                           cwd=path.parent, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"{project}: commit/push 失敗,留到隔晚:{(r.stderr or r.stdout).strip()[-200:]}")
    return r.returncode == 0


# --- 一個專案 ---

def run_project(project: str, path: pathlib.Path, how: str, warned: set[str]) -> tuple[bool, int]:
    """回傳 (全部順利, 送出的 record 數)。"""
    led, lines = Ledger(project), path.read_text().splitlines()
    changed, ok, sent = False, True, 0
    # 1. 套用
    applied = []
    for key in sorted({e["record"]["key"] for e in led.pending}):
        cur = current(led.settle[key])
        if cur["type"] == "settled":
            changed |= strike(lines, key)
        elif cur.get("restore_text"):
            changed |= readd(lines, key, cur["restore_text"])
        log(f"{project}: 套用 {cur['type']} {key}")
    applied += [e["id"] for e in led.pending]
    for h in led.v1_pending:
        changed |= strike(lines, h["reply_to"]) if h["effect"] != "reopened" else \
            readd(lines, h["reply_to"], h.get("item") or "(重開的任務)")
        applied.append(h["id"])
    # 2. 補 key
    present = {t["key"] for t in owner_lines(lines) if t["key"]}
    vanished = [k for k in led.open() if k not in present]
    by_text = {norm(led.terms(k)[0] or ""): k for k in vanished}
    for t in owner_lines(lines):
        k = by_text.pop(norm(t["text"]), None) if t["key"] is None else None
        if k:
            lines[t["i"]] = lines[t["i"]].rstrip() + stamp(k)
            changed = True
            log(f"{project}: 補回 key {k}(行尾的 key 被拿掉了)")
    # 3. 送出
    fresh = []
    for t in owner_lines(lines):
        if t["key"] is None:
            k = task_key(project, t["text"])
            if led.settled(k):
                log(f"{project}: 警告——{k} 的文字跟已經結案的一件一樣,重新宣告會被當成重複(規範 acts §11)")
            fresh.append((t["i"], k, record(project, "raised", k, f"r:{k}", terms={"text": norm(t["text"])}, terms_rev=1)))
        elif t["key"] not in led.known():   # 過渡期:有 key 但匯流排上沒紀錄(很早以前寄的)
            fresh.append((None, t["key"], record(project, "raised", t["key"], f"r:{t['key']}",
                                                 terms={"text": norm(t["text"])}, terms_rev=1, context="migration-v1")))
    res = publish([r for _, _, r in fresh])
    for i, k, r in fresh:
        if res.get(r["id"]) in ("accepted", "duplicate", "dry-run"):
            sent += res.get(r["id"]) == "accepted"
            if i is not None and not DRY:
                lines[i] = lines[i].rstrip() + stamp(k)
                changed = True
        else:
            ok = False
    # 4. 修改
    amends = []
    for t in owner_lines(lines):
        k = t["key"]
        if not k or k not in led.need:
            continue
        text, rev, cur = led.terms(k)
        if text is not None and norm(t["text"]) != norm(text) and not led.settled(k) and not led.withdrawn(k):
            amends.append(record(project, "amended", k, f"a:{k}:{rev + 1}", terms={"text": norm(t["text"])},
                                 terms_rev=rev + 1, **({"supersedes": [cur]} if cur else {})))
    res = publish(amends)
    sent += sum(1 for r in amends if res.get(r["id"]) == "accepted")
    ok &= all(res.get(r["id"]) in ("accepted", "duplicate", "dry-run") for r in amends)
    # 5. 對帳
    present = {t["key"] for t in owner_lines(lines) if t["key"]}
    texts = line_texts(lines)
    claims = []
    for k in led.open():
        if k in present or k in warned:
            continue
        a = norm(led.terms(k)[0] or "")
        if any(a == t or difflib.SequenceMatcher(None, a, t).ratio() >= SIMILAR for t in texts):
            log(f"{project}: 警告——{k} 還沒結案,找不到 key 但有很像的行(改寫時拿掉了行尾?),不送 claim")
            warned.add(k)
            continue
        why = removal_reason(k)
        basis = ("librarian 推斷已完成:STATE.md 的待辦裡已經沒有這一件。"
                 + (f"刪掉的那次({why[:300]})" if why else "review-log 沒有記錄是哪一次刪掉的"))
        claims.append(record(project, "claimed", k, f"c:{k}:r{led.reopens(k)}", origin="registrar", outcome="done",
                             evidence={"basis": basis}))
    res = publish(claims)
    sent += sum(1 for r in claims if res.get(r["id"]) == "accepted")
    ok &= all(res.get(r["id"]) in ("accepted", "duplicate", "dry-run") for r in claims)
    # 6. 寫檔、commit,之後才 done
    if DRY:
        return ok, sent
    if changed:
        path.write_text("\n".join(lines) + "\n")
        if not commit(project, path, how, f"registrar: owner 的待辦對齊 {hubconf.assistant_name()}"):
            return False, sent
    if applied:
        hubbus("done", *applied, "--note", "registrar 照 key 更新了 STATE.md 的待辦" if changed else
               "registrar:STATE.md 已經是這個狀態")
    return ok, sent


def main() -> int:
    try:
        warned = set(json.loads(WARNED.read_text()))
    except (OSError, ValueError):
        warned = set()
    failed = total = 0
    for project, path, how in targets():
        ok, sent = run_project(project, path, how, warned)
        failed += not ok
        total += sent
    if not DRY:
        WARNED.write_text(json.dumps(sorted(warned)))
    log(f"送出 {total} 筆 record、{failed} 個專案有失敗")
    return 0


if __name__ == "__main__":
    sys.exit(main())
