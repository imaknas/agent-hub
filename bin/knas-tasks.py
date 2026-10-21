#!/usr/bin/env python3
"""〔Knas〕待辦 ↔ Muninn 的任務登錄(librarian 每晚呼叫,機械處理、零 LLM)。

只有 Knas 能做的事只在 Muninn 登錄一次:各專案 STATE.md(跟 openclaw-migration-status.md,
算 agent-hub 的)`## 待辦` 裡的〔Knas〕行,寄成 Muninn 的任務,行尾蓋上「(等 Knas:<key>)」;
Knas 在 Muninn 結案/取消/重開時,Muninn 送回帶 key 的轉交,這裡照 key 改那一行。

  1. 帶 key 的轉交(hubbus open-handoffs 裡 effect 是 done/dropped/already_done/reopened 的):
     reply_to 就是行尾的 key。done/dropped/already_done → 刪掉那一行;reopened → 那一行不在就加回
     `## 待辦`(用轉交的 item,也就是當初寄出去的文字)。
  2. 還沒有 key 的〔Knas〕行(`- [ ] 〔Knas〕…`,或 `### 〔Knas〕` 小標題底下的 `- [ ] …`):
     key = task-<專案>-<sha1(正規化的文字)[:10]>,固定、可重算:先寄(hubbus 跟 Muninn 都照 id 去重)
     再蓋章,蓋章或 commit 失敗,隔晚重寄也只是 no-op。第一次跑時 context=migration。
  3. 有改就 commit + push(memory 副本走 git-safe-push;agent-hub 的檔走 commit-hub-files.sh),
     push 成功之後才把那些轉交 `hubbus done`(bridge 再回報 Muninn);失敗就留到隔晚。

不碰其他待辦,也不改行文字(librarian 的 prompt 要求模型保留行尾的「(等 Knas:…)」;
模型真的改了文字,key 就變了,Muninn 的去重會當成同一件)。

用法:knas-tasks.py [--dry-run]
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import shlex
import subprocess
import sys

HOME = pathlib.Path.home()
HUB = HOME / "agent-hub"
COPIES = HOME / ".memory-copies"
HUBBUS = ["/usr/bin/python3", str(HUB / "bin" / "hubbus")]
MIGRATED = HUB / ".state" / "knas-tasks-migrated"
KEYED = ("done", "dropped", "already_done", "reopened")
STRIKE = ("done", "dropped", "already_done")

ITEM = re.compile(r"^- \[ \] 〔Knas〕\s*(.+)$")
GROUP = re.compile(r"^#{3,4}\s*〔(Knas|agent|待定)〕\s*$")
BARE = re.compile(r"^- \[ \] (?!〔)(.+)$")
KEY = re.compile(r"[((]等 Knas[::]\s*([^))\s]+)\s*[))]")
DRY = "--dry-run" in sys.argv


def log(msg: str) -> None:
    print(f"knas-tasks: {msg}")


def norm(text: str) -> str:
    return " ".join(text.split())


def task_key(project: str, text: str) -> str:
    return f"task-{project}-{hashlib.sha1(norm(text).encode()).hexdigest()[:10]}"


def stamp(key: str) -> str:
    return f"(等 Knas:{key})"


def todo_bounds(lines: list[str]) -> tuple[int, int] | None:
    """`## 待辦` 段落的 [開始, 結束) 行號(不含標題)。"""
    start = next((i for i, l in enumerate(lines) if l.strip() == "## 待辦"), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start + 1, end


def unkeyed(lines: list[str]) -> list[tuple[int, str]]:
    """還沒蓋章的〔Knas〕行:(行號, 文字)。"""
    b = todo_bounds(lines)
    if b is None:
        return []
    out, group = [], None
    for i in range(*b):
        l = lines[i].rstrip()
        if m := GROUP.match(l):
            group = m.group(1)
            continue
        if KEY.search(l):
            continue
        if m := ITEM.match(l):
            out.append((i, m.group(1).strip()))
        elif group == "Knas" and (m := BARE.match(l)):
            out.append((i, m.group(1).strip()))
    return out


def apply_keyed(lines: list[str], h: dict) -> bool:
    """照 key 改那一行;回傳有沒有改。"""
    key = h.get("reply_to")
    hits = [i for i, l in enumerate(lines) if (m := KEY.search(l)) and m.group(1) == key]
    if h["effect"] in STRIKE:
        for i in reversed(hits):
            del lines[i]
        b = todo_bounds(lines)
        if hits and b and not any(lines[i].strip() for i in range(*b)):
            lines.insert(b[0], "- (無)")  # 段落空了照格式寫「- (無)」(gen-todo / 格式檢查要的)
        return bool(hits)
    if hits:  # reopened,而那一行本來就還在
        return False
    b = todo_bounds(lines)
    new = f"- [ ] 〔Knas〕{norm(h.get('item') or '(Muninn 重開的任務)')}{stamp(key)}"
    if b is None:
        lines += ["", "## 待辦", "", new]
        return True
    body = [i for i in range(*b) if lines[i].strip()]
    if len(body) == 1 and lines[body[0]].strip() == "- (無)":
        lines[body[0]] = new
    else:
        lines.insert((body[-1] + 1) if body else b[0], new)
    return True


def hubbus(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([*HUBBUS, *args], capture_output=True, text=True, timeout=60)


def targets() -> list[tuple[str, pathlib.Path, str]]:
    """(專案, 檔案, 怎麼 commit)"""
    out = [(d.name, d / "STATE.md", "copy") for d in sorted(COPIES.iterdir())
           if (d / ".git").exists() and (d / "STATE.md").exists()]
    out.append(("agent-hub", HUB / "openclaw-migration-status.md", "hub"))
    return out


def commit(project: str, path: pathlib.Path, how: str, msg: str) -> bool:
    if how == "hub":
        r = subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), msg, path.name], cwd=HUB,
                           capture_output=True, text=True)
    else:
        r = subprocess.run(f"git add STATE.md && git commit -qm {shlex.quote(msg)} && {HUB}/bin/git-safe-push.sh",
                           shell=True, cwd=path.parent, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"{project}: commit/push 失敗,轉交留到隔晚:{(r.stderr or r.stdout).strip()[-200:]}")
    return r.returncode == 0


def main() -> int:
    r = hubbus("open-handoffs")
    try:
        handoffs = [h for h in json.loads(r.stdout) if h.get("effect") in KEYED and h.get("reply_to")]
    except ValueError:
        log(f"讀不到 hubbus open-handoffs(exit {r.returncode}),這次只寄新任務")
        handoffs = []
    context = None if MIGRATED.exists() else "migration"
    posted = failed = 0
    for project, path, how in targets():
        lines = path.read_text().splitlines()
        changed, closing = False, []
        for h in (h for h in handoffs if h["project"] == project):
            if apply_keyed(lines, h):
                changed = True
                log(f"{project}: {h['effect']} {h['reply_to']}")
            closing.append(h["id"])
        for i, text in unkeyed(lines):
            key = task_key(project, text)
            args = ["send", "--to", "knas", "--type", "task", "--from", project, "--id", key, "--text", text]
            if context:
                args += ["--context", context]
            if DRY:
                log(f"{project}: (dry-run) 會寄 {key}:{text[:60]}")
                continue
            res = hubbus(*args)
            if res.returncode != 0:
                failed += 1
                log(f"{project}: 寄不出去,這行先不蓋章:{res.stderr.strip()[-160:]}")
                continue
            lines[i] = lines[i].rstrip() + stamp(key)
            changed, posted = True, posted + 1
        if DRY or not changed:
            if closing and not DRY:  # 沒改檔(那一行早就不在)也算套用完成
                hubbus("done", *closing, "--note", "librarian:STATE.md 已經沒有這一行")
            continue
        path.write_text("\n".join(lines) + "\n")
        if commit(project, path, how, "librarian: Knas 任務登錄(Muninn)"):
            if closing:
                hubbus("done", *closing, "--note", "librarian 照 key 更新了 STATE.md 的待辦")
        else:
            failed += 1
    log(f"寄出 {posted} 件任務、{len(handoffs)} 筆帶 key 的轉交、{failed} 個失敗")
    if not DRY and failed == 0 and context:
        MIGRATED.write_text("first full pass ok\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
