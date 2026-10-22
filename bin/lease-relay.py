#!/usr/bin/env python3
"""通道租借的權限轉接:Claude Code 的 `--permission-prompt-tool`(stdio MCP,一個工具 `approve`)。

規則(--allowedTools、deny)決定得了的不會到這裡(2026-10-22 試驗 T3);到這裡的,discuss 模式照這個政策:
  AskUserQuestion        → 送到 owner 的助理(Muninn)問 Knas;回 allow + updatedInput.answers = {問題: 選項}
  WebFetch(不在登錄網域) → 送到手機,顯示完整網址;allow / allow_session(這個租約都允許這個網域)/ deny
  其他任何工具           → deny(discuss 唯讀;工具清單本來就沒有,這是第二道防線)
沒人回答 5 分鐘 → deny,告訴 session 不要重試。每次請求跟決定都回報給 runner(稽核、等待中不算閒置)。

環境變數(runner 給):LEASE_ID、LEASE_PROJECT、LEASE_RUNNER(runner 的 base URL)。token 從檔案讀,不放環境變數。
助理的 API(Muninn 定義;contracts/vendor 之後補):
  POST <base>/api/permission-requests {v, id, lease, project, tool, input_summary, risk, question?}
       → 200 {decision: allow|deny|allow_session, answers?} 或 202 {id, pending: true}
  GET  <base>/api/permission-requests/<id>?wait=25 → 同上(長輪詢)
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

LEASE, PROJECT = os.environ.get("LEASE_ID", ""), os.environ.get("LEASE_PROJECT", "")
RUNNER = os.environ.get("LEASE_RUNNER", "http://127.0.0.1:18797")
TIMEOUT_S = float(os.environ.get("LEASE_RELAY_TIMEOUT_S", "300"))
_out = threading.Lock()
_allowed_domains: set[str] = set()


def secret(path: pathlib.Path | None) -> str:
    try:
        return path.read_text().strip() if path else ""
    except OSError:
        return ""


def assistant_base() -> str:
    api = hubconf.get("assistant.api") or ""
    return api.rsplit("/api/", 1)[0] if "/api/" in api else api.rstrip("/")


def http(method: str, url: str, token: str, body: dict | None = None, timeout: float = 35) -> tuple[int, dict]:
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def tell_runner(phase: str, **kw) -> None:
    http("POST", f"{RUNNER}/v1/internal/{LEASE}/relay", secret(hubconf.ROOT / ".secrets" / "runner-token"),
         {"phase": phase, **kw}, timeout=5)


def ask_owner(rid: str, tool: str, summary: str, risk: str, question: dict | None) -> dict:
    """送到助理,等到有答案或逾時。回傳 {decision, answers?} 或 {} (逾時/連不上)。"""
    base, token = assistant_base(), secret(hubconf.p("assistant.ingest_token"))
    body = {"v": 1, "id": rid, "lease": LEASE, "project": PROJECT, "tool": tool, "input_summary": summary, "risk": risk}
    if question is not None:
        body["question"] = question
    deadline = time.time() + TIMEOUT_S
    code, res = http("POST", f"{base}/api/permission-requests", token, body)
    while time.time() < deadline:
        if res.get("decision"):
            return res
        if code not in (200, 202) and code != 0:
            return {}
        if code == 0:
            time.sleep(5)  # 連不上:稍等再試(等的時間算在 5 分鐘裡)
        wait = max(1, min(25, int(deadline - time.time())))
        code, res = http("GET", f"{base}/api/permission-requests/{urllib.parse.quote(rid)}?wait={wait}", token,
                         timeout=wait + 10)
    return {}


def no_session(host: str) -> bool:
    """hub.json lease.no_session_domains(含子網域)。"""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in hubconf.get("lease.no_session_domains", []))


def decide(args: dict) -> dict:
    tool, inp = args.get("tool_name", ""), args.get("input") or {}
    rid = f"{LEASE}:{args.get('tool_use_id') or int(time.time() * 1000)}"
    if tool == "AskUserQuestion":
        qs = inp.get("questions") or []
        summary = " / ".join(q.get("question", "") for q in qs)[:500]
        tell_runner("start", tool=tool, summary=summary, kind="question")
        res = ask_owner(rid, tool, summary, "question", inp)
        if res.get("decision") in ("allow", "allow_session") and res.get("answers"):
            tell_runner("end", tool=tool, decision="answered")
            return {"behavior": "allow", "updatedInput": {**inp, "answers": res["answers"]}}
        tell_runner("end", tool=tool, decision="no_answer")
        return {"behavior": "deny", "message": "Knas 現在沒有回答這個問題。用一般文字問他,不要再用提問工具重試。"}
    if tool == "WebFetch":
        url = str(inp.get("url", ""))
        host = urllib.parse.urlparse(url).hostname or ""
        if host and host in _allowed_domains:
            return {"behavior": "allow", "updatedInput": inp}
        tell_runner("start", tool=tool, summary=url, kind="permission")
        res = ask_owner(rid, tool, url, "external_fetch", None)
        d = res.get("decision")
        if d == "allow_session" and no_session(host):
            d = "allow"  # 護欄:任何人都能架/看請求的網域,只准這一次(Muninn 那邊也會藏按鈕;兩邊都擋)
        if d in ("allow", "allow_session"):
            if d == "allow_session" and host:
                _allowed_domains.add(host)
            tell_runner("end", tool=tool, decision=d, summary=url)
            return {"behavior": "allow", "updatedInput": inp}
        tell_runner("end", tool=tool, decision="deny" if d == "deny" else "timeout", summary=url)
        return {"behavior": "deny", "message": ("Knas 拒絕了這個網址。" if d == "deny" else "沒有人核准這個網址(5 分鐘內沒回)。")
                + "不要重試同一個網址;需要的話用文字跟 Knas 說明為什麼要讀它。"}
    tell_runner("end", tool=tool, decision="deny_readonly")
    return {"behavior": "deny", "message": f"這是唯讀的深談(discuss)租約,不能用 {tool}。需要改東西就寫進交接,讓 Knas 決定。"}


def send(obj: dict) -> None:
    with _out:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def handle_call(mid, args: dict) -> None:
    try:
        decision = decide(args)
    except Exception as e:  # 轉接壞掉:拒絕(安全的那邊),不要讓 session 卡住
        decision = {"behavior": "deny", "message": f"權限轉接出錯({e!r}),先不要用這個工具。"}
    send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(decision, ensure_ascii=False)}]}})


def main() -> None:
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "lease_relay", "version": "1"}}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{
                "name": "approve", "description": "Relay a permission request or question to the lease owner",
                "inputSchema": {"type": "object", "properties": {"tool_name": {"type": "string"}, "input": {"type": "object"},
                                                                 "tool_use_id": {"type": "string"}}}}]}})
        elif method == "tools/call":
            threading.Thread(target=handle_call, args=(mid, (msg.get("params") or {}).get("arguments") or {}),
                             daemon=True).start()
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
