#!/usr/bin/env python3
"""通道租借的權限轉接:Claude Code 的 `--permission-prompt-tool`(stdio MCP,一個工具 `approve`)。

規則(--allowedTools、deny)決定得了的不會到這裡(2026-10-22 試驗 T3);到這裡的照中立政策 `policy.approvals(專案, 模式)`
決定(規則的意思見 policy.py;docs/design/lease-contract-neutral.md)。工具是哪一種動作(kind)由 `runtime_claude.action()` 翻譯,
這支只負責 Claude Code 的權限協定(MCP、回 behavior/updatedInput)。目前的規則:
  ask_user   → 送到 owner 的助理(Muninn)問 Knas;回 allow + updatedInput.answers = {問題: 選項}
  open_web   → 登錄 network 的網域放行;其他送到手機,顯示完整網址;allow / allow_session(這個租約都允許這個網域)/ deny
  write_file → discuss 拒絕;work(只限想法沙盒)路徑解析成真實路徑後在專案資料夾裡就直接放行、不問(10-25 Knas 決定:
               「只有碰資料夾外才問」);資料夾外、../ 繞出去、symlink 指出去都直接 deny(比問更嚴)
  run_command→ discuss 拒絕;work 每個指令都送手機(ask_once:allow_session 只算這一次,試行期的保守做法):
               10-24 實測 OS sandbox 擋得住寫資料夾外、讀 ~/.ssh、讀 ~/.claude 的憑證,但其他放機密的位置
               (各服務的 .env 等)沒有逐一驗證過,而且擋不住 Keychain
  其他       → deny
另外提供平台工具 show_file(path, summary):把專案裡的檔案給使用者看(轉給 runner 的 /v1/internal/<lease>/show,檢查在 runner;
docs/design/lease-show-file.md)。在判斷點的自動放行清單裡,不經過上面的權限判斷。
沒人回答 5 分鐘 → deny,告訴 session 不要重試。每次請求跟決定都回報給 runner(稽核、等待中不算閒置)。

環境變數(runner 給):LEASE_ID、LEASE_PROJECT、LEASE_RUNNER(runner 的 base URL)。token 從檔案讀,不放環境變數。
助理的 API(Muninn 定義,contracts/vendor/muninn/permission-requests;版本看 hub.json lease.permission_requests_v):
  POST <base>/api/permission-requests v1 {v, id, lease, project, tool, input_summary, risk, question?}
                                      v2 {v: 2, id, lease, project, kind, summary, target?, question?(通用結構), detail: {tool}}
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
import policy  # noqa: E402
import runtime_claude  # noqa: E402  這支本身就是 Claude Code 的權限轉接(--permission-prompt-tool):工具 → kind 的翻譯在那裡

LEASE, PROJECT = os.environ.get("LEASE_ID", ""), os.environ.get("LEASE_PROJECT", "")
RUNNER = os.environ.get("LEASE_RUNNER", "http://127.0.0.1:18797")
TIMEOUT_S = float(os.environ.get("LEASE_RELAY_TIMEOUT_S", "300"))
_out = threading.Lock()
_allowed_domains: set[str] = set()
MODE = os.environ.get("LEASE_MODE", "discuss")
WS = os.path.realpath(os.environ.get("LEASE_WS") or os.getcwd())


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


def request_body(rid: str, tool: str, summary: str, risk: str, question: dict | None) -> dict:
    """送助理的權限請求。助理的契約(permission-requests)版本看 hub.json lease.permission_requests_v:
    v1 帶原生的工具名跟提問;v2 跟執行環境無關(kind、summary、target、通用的提問結構),原生名稱只在 detail。"""
    if hubconf.get("lease.permission_requests_v", 1) >= 2:
        kind = runtime_claude.action(tool, {})[0]
        body = {"v": 2, "id": rid, "lease": LEASE, "project": PROJECT, "kind": kind, "summary": summary,
                "detail": {"tool": tool}}
        if kind in ("open_web", "run_command", "write_file"):
            body["target"] = summary
        if question is not None:
            body["question"] = runtime_claude.neutral_question(question)
        return body
    body = {"v": 1, "id": rid, "lease": LEASE, "project": PROJECT, "tool": tool, "input_summary": summary, "risk": risk}
    if question is not None:
        body["question"] = question
    return body


def ask_owner(rid: str, tool: str, summary: str, risk: str, question: dict | None) -> dict:
    """送到助理,等到有答案或逾時。回傳 {decision, answers?} 或 {} (逾時/連不上)。"""
    base, token = assistant_base(), secret(hubconf.p("assistant.ingest_token"))
    body = request_body(rid, tool, summary, risk, question)
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
    """要不要放行,照中立政策(policy.approvals)的規則;工具是哪一種動作由 runtime_claude.action() 翻譯。"""
    tool, inp = args.get("tool_name", ""), args.get("input") or {}
    rid = f"{LEASE}:{args.get('tool_use_id') or int(time.time() * 1000)}"
    kind, _, target, _ = runtime_claude.action(tool, inp)
    ap = policy.approvals(PROJECT, MODE)
    rule = ap["rules"].get(kind, "deny")
    if rule == "allow":
        return {"behavior": "allow", "updatedInput": inp}
    if kind == "ask_user" and rule in ("ask", "ask_once"):
        return decide_question(rid, tool, inp)
    if kind == "open_web" and rule == "listed":
        return decide_web(rid, tool, inp, ap)
    if rule == "inside":
        return decide_write(rid, tool, kind, target, inp)
    if rule in ("ask", "ask_once"):
        return decide_ask(rid, tool, kind, target, inp)
    tell_runner("end", tool=tool, kind=kind, decision="deny_readonly" if MODE != "work" else "deny_policy")
    if MODE != "work":
        return {"behavior": "deny", "message": f"這是唯讀的深談(discuss)租約,不能用 {tool}。需要改東西就寫進交接,讓 Knas 決定。"}
    return {"behavior": "deny", "message": f"這個租約不能用 {tool}(可以讀、改這個資料夾裡的檔案、問過 Knas 才能跑指令)。"}


def decide_question(rid: str, tool: str, inp: dict) -> dict:
    qs = inp.get("questions") or []
    summary = " / ".join(q.get("question", "") for q in qs)[:500]
    tell_runner("start", tool=tool, summary=summary, kind="ask_user")
    res = ask_owner(rid, tool, summary, "question", inp)
    if res.get("decision") in ("allow", "allow_session") and res.get("answers"):
        tell_runner("end", tool=tool, kind="ask_user", decision="answered")
        return {"behavior": "allow", "updatedInput": {**inp, "answers": runtime_claude.native_answers(res["answers"])}}
    tell_runner("end", tool=tool, kind="ask_user", decision="no_answer")
    return {"behavior": "deny", "message": "Knas 現在沒有回答這個問題。用一般文字問他,不要再用提問工具重試。"}


def decide_web(rid: str, tool: str, inp: dict, ap: dict) -> dict:
    url = str(inp.get("url", ""))
    host = urllib.parse.urlparse(url).hostname or ""
    if host and (host in _allowed_domains or host in ap["web_allowed"]):
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("start", tool=tool, summary=url, kind="open_web", target=url)
    res = ask_owner(rid, tool, url, "external_fetch", None)
    d = res.get("decision")
    if d == "allow_session" and no_session(host):
        d = "allow"  # 護欄:任何人都能架/看請求的網域,只准這一次(Muninn 那邊也會藏按鈕;兩邊都擋)
    if d in ("allow", "allow_session"):
        if d == "allow_session" and host:
            _allowed_domains.add(host)
        tell_runner("end", tool=tool, kind="open_web", decision=d, summary=url, target=url)
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("end", tool=tool, kind="open_web", decision="deny" if d == "deny" else "timeout", summary=url, target=url)
    return {"behavior": "deny", "message": ("Knas 拒絕了這個網址。" if d == "deny" else "沒有人核准這個網址(5 分鐘內沒回)。")
            + "不要重試同一個網址;需要的話用文字跟 Knas 說明為什麼要讀它。"}


def inside_ws(path: str) -> bool:
    """解析成真實路徑(../、symlink 都展開)之後還在專案資料夾裡。"""
    if not path:
        return False
    p = os.path.realpath(path if os.path.isabs(path) else os.path.join(WS, path))
    try:
        return os.path.commonpath([WS, p]) == WS
    except ValueError:
        return False


def decide_write(rid: str, tool: str, kind: str, path: str, inp: dict) -> dict:
    if not inside_ws(path):
        tell_runner("end", tool=tool, kind=kind, decision="deny_outside", summary=path, target=path)
        return {"behavior": "deny", "message": f"只能改這個專案資料夾({WS})裡的檔案,{path or '(沒有路徑)'} 不在裡面。"}
    # 資料夾內:直接放行(OS sandbox 本來就只准寫這裡);照樣回報 runner 留稽核紀錄
    tell_runner("end", tool=tool, kind=kind, decision="auto_allow_inside", summary=path, target=path)
    return {"behavior": "allow", "updatedInput": inp}


def decide_ask(rid: str, tool: str, kind: str, target: str, inp: dict) -> dict:
    """送手機問(ask_once:每次都問,「這個租約都允許」只算這一次——目前 run_command 用這個,見檔頭)。"""
    summary = target[:500]
    tell_runner("start", tool=tool, summary=summary, kind=kind, target=summary)
    res = ask_owner(rid, tool, summary, kind, None)
    d = res.get("decision")
    if d in ("allow", "allow_session"):
        tell_runner("end", tool=tool, kind=kind, decision="allow", summary=summary, target=summary)
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("end", tool=tool, kind=kind, decision="deny" if d == "deny" else "timeout", summary=summary, target=summary)
    noun = "指令" if kind == "run_command" else "動作"
    return {"behavior": "deny", "message": (f"Knas 拒絕了這個{noun}。" if d == "deny" else f"沒有人核准這個{noun}(5 分鐘內沒回)。")
            + f"不要重試同一個{noun};用文字跟他說明。"}


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


def show_file(args: dict) -> tuple:
    """平台的「給使用者看」服務(runner 的 /v1/internal/<lease>/show)接成 Claude Code 的工具:轉給 runner、回結果。
    檢查(資料夾裡、類型、大小、不像憑證)全部在 runner(lease_files.py),這裡不判斷。"""
    code, res = http("POST", f"{RUNNER}/v1/internal/{LEASE}/show", secret(hubconf.ROOT / ".secrets" / "runner-token"),
                     {"path": args.get("path"), "summary": args.get("summary")}, timeout=30)
    if code == 200 and res.get("id"):
        return f"已經給 Knas 看了:{res.get('path')}({res.get('mime')},{res.get('size')} bytes)。", False
    if code == 0:
        return "沒送出去:連不到平台(runner),先用文字跟 Knas 說檔案在哪。", True
    return f"沒送出去:{res.get('detail') or res.get('error') or code}", True


def handle_show(mid, args: dict) -> None:
    try:
        text, is_error = show_file(args)
    except Exception as e:
        text, is_error = f"沒送出去:平台出錯({e!r})", True
    send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}], "isError": is_error}})


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
                                                                 "tool_use_id": {"type": "string"}}}}, {
                "name": "show_file",
                "description": "把專案資料夾裡的一個檔案(圖片 png/jpg/webp/gif、pdf、glb 3D 模型、txt/md/json/csv)直接顯示給正在跟你對話的"
                               "使用者看(他在手機上)。path 是專案資料夾裡的路徑,summary 是一句說明(這是什麼、要他看哪裡)。",
                "inputSchema": {"type": "object", "required": ["path"], "properties": {
                    "path": {"type": "string", "description": "專案資料夾裡的檔案路徑(相對或絕對)"},
                    "summary": {"type": "string", "description": "一句說明"}}}}]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            target = handle_show if params.get("name") == "show_file" else handle_call
            threading.Thread(target=target, args=(mid, params.get("arguments") or {}), daemon=True).start()
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
