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
import i18n  # noqa: E402

_ = i18n.domain(__file__)

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


def apply(lines: list[str], rec: dict) -> bool:
    """第 1 步,一筆 record:只有 principal 端的 settled(刪掉)跟帶 restore_text 的 reopened(加回)會改文件。"""
    if rec.get("origin") != "principal":
        return False
    if rec.get("type") == "settled":
        return strike(lines, rec["key"])
    if rec.get("type") == "reopened" and rec.get("restore_text"):
        return readd(lines, rec["key"], rec["restore_text"])
    return False


def relink(lines: list[str], vanished: dict[str, str]) -> list[str]:
    """第 2 步。vanished:送過、沒結案、沒撤回、文件裡找不到的 key → 最新的 terms 文字。文字一模一樣(正規化後)的
    沒 key owner 行蓋回那個 key;回傳補回的 key。"""
    by_text, done = {norm(t): k for k, t in sorted(vanished.items())}, []
    for t in owner_lines(lines):
        k = by_text.pop(norm(t["text"]), None) if t["key"] is None else None
        if k:
            lines[t["i"]] = lines[t["i"]].rstrip() + stamp(k)
            done.append(k)
    return done


def candidates(lines: list[str], project: str) -> list[tuple[int, str, str]]:
    """第 3 步:沒 key 的 owner 行 → (行號, key, 文字)。蓋章(stamp)要等 raised 送出去之後。"""
    return [(t["i"], task_key(project, t["text"]), t["text"]) for t in owner_lines(lines) if t["key"] is None]


def amendments(lines: list[str], latest: dict[str, str]) -> list[dict]:
    """第 4 步。latest:還開著的 key → 最新的 terms 文字。文字不一樣的帶 key 行:[{key, text}]。"""
    return [{"key": t["key"], "text": t["text"]} for t in owner_lines(lines)
            if t["key"] in latest and norm(t["text"]) != norm(latest[t["key"]])]


def reconcile(lines: list[str], open_keys: dict[str, str]) -> list[dict]:
    """第 5 步。open_keys:送過、沒結案、沒撤回的 key → 最新的 terms 文字。文件裡找不到的:有很像的行 → warn,
    否則 claim。"""
    present = {t["key"] for t in owner_lines(lines) if t["key"]}
    texts, out = line_texts(lines), []
    for k, text in sorted(open_keys.items()):
        if k in present:
            continue
        a = norm(text)
        similar = any(a == t or difflib.SequenceMatcher(None, a, t).ratio() >= SIMILAR for t in texts)
        out.append({"key": k, "action": "warn" if similar else "claim"})
    return out


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
            raise SystemExit(_("cannot read hubbus records (exit {code}): {stderr}", code=r.returncode,
                               stderr=r.stderr.strip()[-200:]))
        self.need, self.settle, self.pending = {}, {}, []
        for e in events:
            rec = e["record"]
            if e["direction"] == "out" and rec["type"] in ("raised", "amended", "withdrawn"):
                self.need.setdefault(rec["key"], []).append(rec)
            elif e["direction"] == "in" and rec["type"] in ("settled", "reopened"):
                self.settle.setdefault(rec["key"], []).append(rec)
                if not e["done"]:
                    self.pending.append(e)
        self.v1_posted, self.v1_closed, self.v1_pending, self.v1_claimed = v1_compat(project)

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


def v1_compat(project: str) -> tuple[dict, set, list, set]:
    """過渡期:v1 寄過的 task(key → 文字)、v1 收過結案轉交的 key、還沒套用的 v1 帶 key 轉交、v1 已經寄過推斷結案
    (id done-<key>)的 key——換成 v2 那晚不要用新的 id 再寄一次。v1 退役時刪掉。"""
    db = hubconf.bus_db()
    if not db.exists():
        return {}, set(), [], set()
    names = [project, *hubconf.former_names(project)]   # 改名前寄的 v1 信也算(例:idea-1 → garden-app)
    marks, pm = ",".join("?" * len(OWNERS)), ",".join("?" * len(names))
    c = sqlite3.connect(db, timeout=30)
    posted = {r[0]: json.loads(r[1]).get("text", "") for r in c.execute(
        f"SELECT id, payload FROM events WHERE type='message' AND to_project IN ({marks}) AND source IN ({pm})"
        " AND id LIKE 'task-%'", (*OWNERS, *names))}
    closed = set()
    for (pl,) in c.execute(f"SELECT payload FROM events WHERE type='message' AND source IN ({marks})"
                           f" AND to_project IN ({pm}) AND payload LIKE '%reply_to%'", (*OWNERS, *names)):
        h = json.loads(pl).get("handoff") or {}
        if h.get("effect") in ("done", "dropped", "already_done") and h.get("reply_to"):
            closed.add(h["reply_to"])
    claimed = {r[0][len("done-"):] for r in c.execute(
        f"SELECT id FROM events WHERE type='message' AND to_project IN ({marks}) AND source IN ({pm})"
        " AND id LIKE 'done-task-%'", (*OWNERS, *names))}
    r = hubbus("open-handoffs")
    try:
        pending = [h for h in json.loads(r.stdout or "[]") if h.get("project") in names and h.get("reply_to")
                   and h.get("effect") in ("done", "dropped", "already_done", "reopened")]
    except ValueError:
        pending = []
    return posted, closed, pending, claimed


def publish(recs: list[dict]) -> dict[str, str]:
    """送出,回傳 {record id: status}。"""
    if not recs:
        return {}
    if DRY:
        for rec in recs:
            log(_("(dry-run) would send {type} {key}", type=rec["type"], key=rec["key"]))
        return {rec["id"]: "dry-run" for rec in recs}
    r = hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
    try:
        res = {x["id"]: x["status"] for x in json.loads(r.stdout or "[]")}   # 每筆 {id: record id, event, status}
    except ValueError:
        res = {}
    for rec in recs:
        st = res.get(rec["id"], "failed")
        if st not in ("accepted", "duplicate"):
            log(_("{project}: {type} {key} could not be sent ({status}): {stderr}", project=rec["project"],
                  type=rec["type"], key=rec["key"], status=st, stderr=r.stderr.strip()[-160:]))
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
        log(_("{project}: commit/push failed, left for tomorrow night: {output}", project=project,
              output=(r.stderr or r.stdout).strip()[-200:]))
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
        log(_("{project}: applied {type} {key}", project=project, type=cur["type"], key=key))
    applied += [e["id"] for e in led.pending]
    for h in led.v1_pending:
        changed |= strike(lines, h["reply_to"]) if h["effect"] != "reopened" else \
            readd(lines, h["reply_to"], h.get("item") or "(重開的任務)")
        applied.append(h["id"])
    # 2. 補 key
    present = {t["key"] for t in owner_lines(lines) if t["key"]}
    for k in relink(lines, {k: led.terms(k)[0] or "" for k in led.open() if k not in present}):
        changed = True
        log(_("{project}: restored key {key} (the key at the end of the line had been removed)", project=project, key=k))
    # 3. 送出
    fresh = []
    for i, k, text in candidates(lines, project):
        if led.settled(k):
            log(_("{project}: warning — {key} has the same text as one already settled; raising it again counts as "
                  "a duplicate (acts spec §11)", project=project, key=k))
        fresh.append((i, k, record(project, "raised", k, f"r:{k}", terms={"text": norm(text)}, terms_rev=1)))
    for t in owner_lines(lines):
        if t["key"] and t["key"] not in led.known():   # 過渡期:有 key 但匯流排上沒紀錄(很早以前寄的)
            fresh.append((None, t["key"], record(project, "raised", t["key"], f"r:{t['key']}",
                                                 terms={"text": norm(t["text"])}, terms_rev=1, context="migration-v1")))
    res = publish([r for _i, _k, r in fresh])
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
    for a in amendments(lines, {k: led.terms(k)[0] for k in led.open() if k in led.need}):
        k, (_text, rev, cur) = a["key"], led.terms(a["key"])
        amends.append(record(project, "amended", k, f"a:{k}:{rev + 1}", terms={"text": norm(a["text"])},
                             terms_rev=rev + 1, **({"supersedes": [cur]} if cur else {})))
    res = publish(amends)
    sent += sum(1 for r in amends if res.get(r["id"]) == "accepted")
    ok &= all(res.get(r["id"]) in ("accepted", "duplicate", "dry-run") for r in amends)
    # 5. 對帳
    claims = []
    for x in reconcile(lines, {k: led.terms(k)[0] or "" for k in led.open() if k not in warned and k not in led.v1_claimed}):
        k = x["key"]
        if x["action"] == "warn":
            log(_("{project}: warning — {key} is not settled and its key is gone, but a very similar line exists "
                  "(the line end dropped in a rewrite?); no claim sent", project=project, key=k))
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
        if not commit(project, path, how, _("registrar: owner to-dos in step with {assistant}",
                                            assistant=hubconf.assistant_name())):
            return False, sent
    if applied:
        hubbus("done", *applied, "--note", _("registrar updated the STATE.md to-dos by key") if changed else
               _("registrar: STATE.md was already in this state"))
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
    log(_("sent {sent} records, {failed} projects had failures", sent=total, failed=failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
