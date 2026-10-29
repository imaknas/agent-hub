#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""agent 提議,平台套用(docs/design/tool-agnostic.md「核心」):平台啟動、要動到檔案的 agent 工作在丟棄式 worktree 裡跑。

任何 agent(Claude Code、`runtime: command` 接的 Gemini CLI / Codex / opencode…)都一樣:它在 worktree 裡做什麼都碰不到
真的 checkout;跑完平台看它改了哪些檔,**只把准許的路徑**複製回真的資料夾,其他的記成「不採用」、跟 worktree 一起丟掉。
要寄的信、要結案的事 agent 寫在結構化輸出裡,平台代寄(寄件人是平台給的,不是 agent 自己說的)。
這套保證不靠工具的權限設定,在沒有沙盒的系統(Linux)上一樣成立。

  propose_apply.py mailbox <專案> <信 id>…     信箱交辦(hub.json mailbox.handler: propose 時由 hubbus dispatch 叫)

  run(project, ws, point, prompt, allow)        給其他程式用:回 {ok, output, applied, refused, error}

worktree:專案的 HEAD(detached)+ 它的 .agent-memory 的 HEAD;agent 的工作目錄是專案那份。
"""
from __future__ import annotations

import datetime
import fnmatch
import importlib.machinery
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402

PY = sys.executable


def git(repo, *args, check=True) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}:{r.stderr.strip()[-200:]}")
    return r.stdout


def changed(repo: pathlib.Path) -> list[tuple[str, bool]]:
    """(相對 repo 的路徑, 還在不在)——改過、新增(含沒追蹤的)、刪掉的檔案。"""
    out = []
    for line in git(repo, "status", "--porcelain", "-uall", "--no-renames").splitlines():
        path = line[3:].strip().strip('"')
        out.append((path, (repo / path).exists()))
    return out


def run(project: str, ws: pathlib.Path, point: str, prompt: str, allow: list[str], timeout_s: int = 1800) -> dict:
    """allow:相對 ws 的 glob(例 `.agent-memory/memory/**`)。"""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"hub-propose-{project}-"))
    wt, mem = tmp / "ws", ws / ".agent-memory"
    added = []
    try:
        git(ws, "worktree", "add", "--detach", str(wt), "HEAD")
        added.append((ws, wt))
        if (mem / ".git").exists():
            git(mem, "worktree", "add", "--detach", str(wt / ".agent-memory"), "HEAD")
            added.append((mem, wt / ".agent-memory"))
        r = subprocess.run([PY, str(BIN / "llm.py"), "run", point, "--cwd", str(wt)], input=prompt, text=True,
                           capture_output=True, timeout=timeout_s + 60)
        try:
            res = json.loads(r.stdout) if r.stdout.strip() else {}
        except ValueError:
            res = {}
        applied, refused = [], []
        for repo, prefix in ((wt, ""), *([(wt / ".agent-memory", ".agent-memory/")] if len(added) > 1 else [])):
            for path, exists in changed(repo):
                rel = prefix + path
                if prefix == "" and rel.startswith(".agent-memory/"):
                    continue
                if not any(fnmatch.fnmatch(rel, g) for g in allow):
                    refused.append(rel)
                    continue
                dst = ws / rel
                if exists:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(wt / rel, dst)
                elif dst.exists():
                    dst.unlink()
                applied.append(rel)
        ok = r.returncode == 0 and not res.get("is_error")
        return {"ok": ok, "output": res.get("structured_output"), "text": res.get("result"), "applied": applied,
                "refused": refused, "error": "" if ok else (res.get("llm_error") or r.stderr.strip()[-300:])}
    finally:
        for repo, path in reversed(added):
            git(repo, "worktree", "remove", "--force", str(path), check=False)
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- 信箱交辦 ----------

def _hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_pa", str(BIN / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_pa", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def letters_text(hb, c, rows) -> str:
    out = []
    for r in rows:
        p = hb.payload(r)
        thread = ""
        if r["ref"]:
            prev = c.execute("SELECT * FROM events WHERE id=?", (r["ref"],)).fetchone()
            if prev is not None:
                thread = f"\n  (回覆 {r['ref']}:{hb.payload(prev).get('text', '')[:600]})"
        out.append(f"- id {r['id']},來自 {r['source']},類型 {p.get('type')}:\n  {p.get('text', '')}{thread}")
    return "\n".join(out)


def _status(project: str, state: str, **extra) -> None:
    f = hubconf.STATE / "agent-jobs" / f"mailbox-{project}" / "status.json"   # hubbus dispatch 看 running 就不重派
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"state": state, "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                             "via": "propose", **extra}, ensure_ascii=False))


def mailbox(project: str, ids: list[str]) -> int:
    _status(project, "running", ids=ids)
    try:
        return _mailbox(project, ids)
    finally:
        _status(project, "done", ids=ids)


def _mailbox(project: str, ids: list[str]) -> int:
    hb = _hubbus()
    c = hb.db()
    rows = [c.execute("SELECT * FROM events WHERE id=? AND type='message'", (i,)).fetchone() for i in ids]
    rows = [r for r in rows if r is not None and r["to_project"] == project]
    ws = hubconf.workspace(project)
    if not rows or not ws:
        print(f"{project}:沒有要處理的信", file=sys.stderr)
        return 1
    tpl = (BIN.parent / "templates" / "prompts" / "mailbox-propose.md").read_text()
    prompt = hubconf.fill_names(tpl).replace("{{letters}}", letters_text(hb, c, rows)).replace(
        "{{today}}", datetime.date.today().isoformat())
    res = run(project, ws, "mailbox-propose", prompt, [".agent-memory/memory/**"])
    sent = []
    out = res["output"] if isinstance(res.get("output"), dict) else {}
    handled = {r["id"]: r for r in rows}
    c.execute("BEGIN IMMEDIATE")
    for rep in out.get("replies") or []:
        orig = handled.get(rep.get("reply_to"))
        if orig is None or rep.get("type") not in ("info", "done", "question") or not rep.get("text"):
            continue   # 只准回這次交辦的信、回給原寄件人
        mid = hb.new_id()
        hb.publish(c, mid, "message", project, hb.canon(orig["source"]), orig["id"],
                   {"type": rep["type"], "text": rep["text"], "from_session": None, "via": "propose"})
        sent.append(mid)
    for q in out.get("ask_owner") or []:
        if q.get("text"):
            mid = hb.new_id()
            hb.publish(c, mid, "message", project, hb.OWNER_ADDRESS, None,
                       {"type": "question", "text": q["text"], "from_session": None, "via": "propose"})
            sent.append(mid)
    done = []
    for d in out.get("done") or []:
        if d.get("id") in handled:
            hb.publish(c, f"{d['id']}:done", "message.done", project, project, d["id"],
                       {"note": d.get("note") or "平台派的 agent 處理了(propose)"})
            done.append(d["id"])
    c.execute("COMMIT")
    if res["applied"] and (ws / ".agent-memory").is_dir():   # 筆記 commit + push 到 memory repo(跟 hook 同一條路)
        subprocess.run([str(BIN / "with-lock.sh"), str(BIN / "git-sync-memory.sh")], cwd=ws / ".agent-memory",
                       capture_output=True)
    print(json.dumps({"project": project, "ok": res["ok"], "applied": res["applied"], "refused": res["refused"],
                      "sent": sent, "done": done, "error": res["error"]}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "mailbox":
        sys.exit(mailbox(sys.argv[2], sys.argv[3:]))
    print(__doc__, file=sys.stderr)
    sys.exit(2)
