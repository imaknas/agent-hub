#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The permission relay for channel leases: Claude Code's `--permission-prompt-tool` (stdio MCP, one tool, `approve`).

What the rules (--allowedTools, deny) can decide never reaches here (2026-10-22 trial T3); what does is decided by
the neutral policy `policy.approvals(project, mode)` (what the rules mean: see policy.py;
docs/design/lease-contract-neutral.md). Which kind of action a tool is (kind) is translated by
`runtime_claude.action()`; this script only handles Claude Code's permission protocol (MCP, answering
behavior/updatedInput). Current rules:
  ask_user   -> sent to the owner's assistant to ask the owner; answers allow + updatedInput.answers =
               {question: option}
  open_web   -> domains in the registry's network are allowed; others go to the phone showing the full URL;
               allow / allow_session (allow this domain for the whole lease) / deny
  write_file -> refused in discuss; in work (idea sandboxes only), a path that resolves to a real path inside the
               project folder is allowed directly without asking (decided by the owner on 10-25: "ask only when
               it touches something outside the folder"); outside the folder, escaping with ../ or a symlink pointing
               out: denied outright (stricter than asking)
  run_command-> refused in discuss; in work every command goes to the phone (ask_once: allow_session counts only
               this once; a conservative choice for the trial period): tested on 10-24, the OS sandbox does block
               writes outside the folder and reading ~/.ssh and the credentials in ~/.claude, but other places that
               hold secrets (each service's .env etc.) have not been checked one by one, and it cannot block the
               Keychain
  other      -> deny
It also provides the platform tool show_file(path, summary): shows a file in the project to the user (forwarded to
the runner's /v1/internal/<lease>/show; the checks are in the runner; docs/design/lease-show-file.md). It is in the
judgment point's auto-allow list and does not go through the permission decisions above.
No answer for 5 minutes -> deny, and tell the session not to retry. Every request and decision is reported to the
runner (audit; waiting does not count as idle).

Environment variables (given by the runner): LEASE_ID, LEASE_PROJECT, LEASE_RUNNER (the runner's base URL). The token
is read from a file, not put in the environment.
The assistant's API (defined by Muninn, contracts/vendor/muninn/permission-requests.v2.schema.json):
  POST <base>/api/permission-requests {v: 2, id, lease, project, kind, summary, target?, question? (generic
       structure), detail: {tool}}
       -> 200 {v: 2, decision: allow|deny|allow_session, answers?} or 202 {v: 2, id, pending: true}
  GET  <base>/api/permission-requests/<id>?wait=25 -> same as above (long polling)
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
import i18n  # noqa: E402
import policy  # noqa: E402
# this script is itself Claude Code's permission relay (--permission-prompt-tool): the tool -> kind translation is there
import runtime_claude  # noqa: E402

_ = i18n.domain(__file__)

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
    # when the runner refuses, the body holds the reason (error, detail): pass it back so the session can say why
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError, ValueError):
        return 0, {}


def tell_runner(phase: str, **kw) -> None:
    http("POST", f"{RUNNER}/v1/internal/{LEASE}/relay", secret(hubconf.secrets_dir() / "runner-token"),
         {"phase": phase, **kw}, timeout=5)


def request_body(rid: str, tool: str, summary: str, risk: str, question: dict | None) -> dict:
    """The permission request sent to the assistant (Muninn's permission-requests v2, contracts/vendor/muninn/):
    independent of the runtime -- kind, summary, target, the generic question structure; the native tool name goes
    only into detail (the assistant does not read it). risk is the relay's own classification, for the audit only;
    not sent."""
    kind = runtime_claude.action(tool, {})[0]
    body = {"v": 2, "id": rid, "lease": LEASE, "project": PROJECT, "kind": kind, "summary": summary or f"({kind})",
            "detail": {"tool": tool}}
    if kind in ("open_web", "run_command", "write_file") and summary:
        body["target"] = summary
    if question is not None:
        body["question"] = runtime_claude.neutral_question(question)
    return body


def ask_owner(rid: str, tool: str, summary: str, risk: str, question: dict | None) -> dict:
    """Send to the assistant and wait for an answer or the timeout. Returns {decision, answers?} or {} (timeout/cannot
    connect)."""
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
            time.sleep(5)  # cannot connect: wait a little and retry (the wait counts toward the 5 minutes)
        wait = max(1, min(25, int(deadline - time.time())))
        code, res = http("GET", f"{base}/api/permission-requests/{urllib.parse.quote(rid)}?wait={wait}", token,
                         timeout=wait + 10)
    return {}


def no_session(host: str) -> bool:
    """hub.json lease.no_session_domains (subdomains included)."""
    host = host.lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in hubconf.get("lease.no_session_domains", []))


def decide(args: dict) -> dict:
    """Whether to allow, by the neutral policy's rules (policy.approvals); which kind of action a tool is, is
    translated by runtime_claude.action()."""
    tool, inp = args.get("tool_name", ""), args.get("input") or {}
    rid = f"{LEASE}:{args.get('tool_use_id') or int(time.time() * 1000)}"
    kind, _collab, target, _summary = runtime_claude.action(tool, inp)
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
        return {"behavior": "deny", "message": _("This is a read-only (discuss) lease; {tool} cannot be used. Write what needs "
                                                  "changing into the handover and let the owner decide.", tool=tool)}
    return {"behavior": "deny", "message": _("This lease cannot use {tool} (it can read and edit files in this folder, and run "
                                              "commands after asking the owner).", tool=tool)}


def decide_question(rid: str, tool: str, inp: dict) -> dict:
    qs = inp.get("questions") or []
    summary = " / ".join(q.get("question", "") for q in qs)[:500]
    tell_runner("start", tool=tool, summary=summary, kind="ask_user")
    res = ask_owner(rid, tool, summary, "question", inp)
    if res.get("decision") in ("allow", "allow_session") and res.get("answers"):
        tell_runner("end", tool=tool, kind="ask_user", decision="answered")
        return {"behavior": "allow", "updatedInput": {**inp, "answers": runtime_claude.native_answers(res["answers"])}}
    tell_runner("end", tool=tool, kind="ask_user", decision="no_answer")
    return {"behavior": "deny", "message": _("The owner is not answering this question now. Ask in plain text; do not retry "
                                              "with the question tool.")}


def decide_web(rid: str, tool: str, inp: dict, ap: dict) -> dict:
    url = str(inp.get("url", ""))
    host = urllib.parse.urlparse(url).hostname or ""
    if host and (host in _allowed_domains or host in ap["web_allowed"]):
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("start", tool=tool, summary=url, kind="open_web", target=url)
    res = ask_owner(rid, tool, url, "external_fetch", None)
    d = res.get("decision")
    if d == "allow_session" and no_session(host):
        # guard rail: a domain where anyone can set up an endpoint and see the requests is allowed only this
        # once (the assistant hides the button too; both sides block it)
        d = "allow"
    if d in ("allow", "allow_session"):
        if d == "allow_session" and host:
            _allowed_domains.add(host)
        tell_runner("end", tool=tool, kind="open_web", decision=d, summary=url, target=url)
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("end", tool=tool, kind="open_web", decision="deny" if d == "deny" else "timeout", summary=url, target=url)
    why = _("The owner refused this URL.") if d == "deny" else _("Nobody approved this URL (no answer within 5 minutes).")
    return {"behavior": "deny", "message": _("{why} {advice}", why=why, advice=_(
        "Do not retry the same URL; if you need it, tell the owner in plain text why you want to read it."))}


def inside_ws(path: str) -> bool:
    """Still inside the project folder after resolving to the real path (../ and symlinks expanded)."""
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
        return {"behavior": "deny", "message": _("Only files in this project folder ({ws}) can be changed; {path} is not in it.",
                                                  ws=WS, path=path or _("(no path)"))}
    # inside the folder: allow directly (the OS sandbox only allows writes here anyway); still report to the runner for
    # the audit trail
    tell_runner("end", tool=tool, kind=kind, decision="auto_allow_inside", summary=path, target=path)
    return {"behavior": "allow", "updatedInput": inp}


def decide_ask(rid: str, tool: str, kind: str, target: str, inp: dict) -> dict:
    """Ask on the phone (ask_once: ask every time; "allow for the whole lease" counts only this once -- run_command uses
    this for now, see the file header)."""
    summary = target[:500]
    tell_runner("start", tool=tool, summary=summary, kind=kind, target=summary)
    res = ask_owner(rid, tool, summary, kind, None)
    d = res.get("decision")
    if d in ("allow", "allow_session"):
        tell_runner("end", tool=tool, kind=kind, decision="allow", summary=summary, target=summary)
        return {"behavior": "allow", "updatedInput": inp}
    tell_runner("end", tool=tool, kind=kind, decision="deny" if d == "deny" else "timeout", summary=summary, target=summary)
    noun = _("command") if kind == "run_command" else _("action")
    why = (_("The owner refused this {noun}.", noun=noun) if d == "deny"
           else _("Nobody approved this {noun} (no answer within 5 minutes).", noun=noun))
    return {"behavior": "deny", "message": _("{why} {advice}", why=why, advice=_(
        "Do not retry the same {noun}; explain it to the owner in plain text.", noun=noun))}


def send(obj: dict) -> None:
    with _out:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def handle_call(mid, args: dict) -> None:
    try:
        decision = decide(args)
    except Exception as e:  # the relay broke: deny (the safe side) rather than leave the session stuck
        decision = {"behavior": "deny", "message": _("The permission relay failed ({e!r}); do not use this tool for now.", e=e)}
    send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(decision, ensure_ascii=False)}]}})


def show_file(args: dict) -> tuple:
    """The platform's "show the user" service (the runner's /v1/internal/<lease>/show) wired up as a Claude Code tool:
    forward to the runner, return the result. All checks (inside the folder, type, size, not credential-like) are in
    the runner (lease_files.py); nothing is decided here."""
    code, res = http("POST", f"{RUNNER}/v1/internal/{LEASE}/show", secret(hubconf.secrets_dir() / "runner-token"),
                     {"path": args.get("path"), "summary": args.get("summary")}, timeout=30)
    if code == 200 and res.get("id"):
        return _("Shown to the owner: {path} ({mime}, {size} {size:byte|bytes}).", path=res.get("path"), mime=res.get("mime"),
                 size=res.get("size")), False
    if code == 0:
        return _("Not sent: the platform (runner) is unreachable; tell the owner in plain text where the file is."), True
    return _("Not sent: {why}", why=res.get("detail") or res.get("error") or code), True


def handle_show(mid, args: dict) -> None:
    try:
        text, is_error = show_file(args)
    except Exception as e:
        text, is_error = _("Not sent: platform error ({e!r})", e=e), True
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
                "description": "Show the user you are talking with (who is on a phone) a file from the project folder: an image "
                               "(png/jpg/webp/gif), a pdf, a glb 3D model, or txt/md/json/csv. path is its path in the project "
                               "folder, summary one sentence (what it is, what to look at).",
                "inputSchema": {"type": "object", "required": ["path"], "properties": {
                    "path": {"type": "string", "description": "the file's path in the project folder (relative or absolute)"},
                    "summary": {"type": "string", "description": "one sentence"}}}}]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            target = handle_show if params.get("name") == "show_file" else handle_call
            threading.Thread(target=target, args=(mid, params.get("arguments") or {}), daemon=True).start()
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
