"""A fake Agent Client Protocol agent for tests: JSON-RPC 2.0 over stdio, one message per line, no model.

FAKE_ACP_MODE picks the script:
  ok          a turn with a thought, a first message, a tool call that asks permission to read a file inside cwd, a
              final message (JSON) in USD, end_turn
  incomplete  ends with max_turn_requests
  fail        prints "boom" on stderr and exits 3 after initialize
  auth        session/new fails with -32000 (Authentication required) unless the client sent authenticate first
  refusal     ends with refusal
  version     answers initialize with protocol version 2
  fs          asks the client fs/read_text_file (must get -32601), then ends the turn saying what it got
  outside     asks permission to edit a file outside cwd and to execute a command; reports which options it got
  hang        never answers session/prompt
  always      offers only allow_always for a read inside cwd; ends cancelled if the client cancels
  nostop      answers session/prompt with no stopReason
  malformed   answers session/new without a sessionId
  costkeep    a usage update with a cost, then one without
  modedrift   switches itself back to mode "auto" in the middle of the turn
  depmode     model and mode as config options; setting the model resets the mode to "auto" (a dependent change,
              reported only in that answer)
  fallback    model and mode as config options; mid-turn the agent falls back to model "small"
  jsonfirst   replies with the JSON, makes one more tool call, then says "Done."
  lateupdate  no messageIds; a tool call's status update arrives after the final message has started
  model       offers a model config option; answers only after the client sets it
FAKE_ACP_LOG: a file each received message is appended to (JSON lines), for the test to check.
The fake checks what it must receive (absolute cwd, empty client capabilities) and exits 9 loudly otherwise.
"""
import json
import os
import sys

MODE = os.environ.get("FAKE_ACP_MODE", "ok")
LOG = os.environ.get("FAKE_ACP_LOG")
SID = "sess_fake_1"
_next = 1000


def log(msg):
    if LOG:
        with open(LOG, "a") as f:
            f.write(json.dumps(msg) + "\n")


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def read():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    msg = json.loads(line)
    log(msg)
    return msg


def die(why):
    print(f"fake acp agent: {why}", file=sys.stderr)
    sys.exit(9)


def update(u):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": SID, "update": u}})


def ask(method, params):
    """A request to the client; returns its answer (result or error)."""
    global _next
    _next += 1
    send({"jsonrpc": "2.0", "id": _next, "method": method, "params": params})
    while True:
        msg = read()
        if msg.get("id") == _next and "method" not in msg:
            return msg


def chunk(text, mid=None):
    u = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}
    if mid:
        u["messageId"] = mid
    update(u)


OPTIONS = [{"optionId": "a1", "name": "Allow once", "kind": "allow_once"},
           {"optionId": "aa", "name": "Always allow reads in this folder", "kind": "allow_always"},
           {"optionId": "r1", "name": "Reject", "kind": "reject_once"}]


def turn(cwd):
    if MODE == "hang":
        while True:
            read()
    if MODE == "fs":
        ans = ask("fs/read_text_file", {"sessionId": SID, "path": os.path.join(cwd, "x")})
        chunk(json.dumps({"fs_error": (ans.get("error") or {}).get("code")}))
        return "end_turn"
    if MODE == "always":
        update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read", "kind": "read",
                "locations": [{"path": os.path.join(cwd, "a")}]})
        ans = ask("session/request_permission", {"sessionId": SID, "toolCall": {"toolCallId": "t1"},
                                                 "options": [OPTIONS[1]]})
        return "cancelled" if ans["result"]["outcome"] == {"outcome": "cancelled"} else "end_turn"
    if MODE == "costkeep":
        update({"sessionUpdate": "usage_update", "used": 1, "size": 9, "cost": {"amount": 0.25, "currency": "USD"}})
        update({"sessionUpdate": "usage_update", "used": 2, "size": 9})
        chunk("ok")
        return "end_turn"
    if MODE == "modedrift":
        update({"sessionUpdate": "current_mode_update", "currentModeId": "auto"})
        chunk('{"x": 1}')
        return "end_turn"
    if MODE == "fallback":
        update({"sessionUpdate": "config_option_update", "configOptions": options("small", "ask")})
        chunk('{"x": 1}')
        return "end_turn"
    if MODE == "jsonfirst":
        chunk('{"x": 1}', "m1")
        update({"sessionUpdate": "tool_call", "toolCallId": "t8", "title": "x", "kind": "think"})
        chunk("Done.", "m2")
        return "end_turn"
    if MODE == "lateupdate":
        update({"sessionUpdate": "tool_call", "toolCallId": "t9", "title": "x", "kind": "think"})
        chunk('{"x": ')
        update({"sessionUpdate": "tool_call_update", "toolCallId": "t9", "status": "completed"})
        chunk('1}')
        return "end_turn"
    if MODE == "outside":
        got = []
        for tid, kind, raw in (("t1", "edit", {"path": "/etc/hosts"}), ("t2", "execute", {"command": "ls"}),
                               ("t3", "read", {})):
            update({"sessionUpdate": "tool_call", "toolCallId": tid, "title": f"{kind} something", "kind": kind,
                    "status": "pending", "rawInput": raw})
            ans = ask("session/request_permission", {"sessionId": SID, "toolCall": {"toolCallId": tid},
                                                     "options": OPTIONS})
            got.append(ans["result"]["outcome"])
        chunk(json.dumps({"outcomes": got}))
        return "end_turn"
    update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "thinking {not json"}})
    chunk("Let me look ", "m1")
    chunk("at the file.", "m1")
    update({"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "Read notes", "kind": "read",
            "status": "pending", "locations": [{"path": os.path.join(cwd, "notes.md")}]})
    ans = ask("session/request_permission", {"sessionId": SID, "toolCall": {"toolCallId": "t1"}, "options": OPTIONS})
    if ans.get("result", {}).get("outcome") != {"outcome": "selected", "optionId": "a1"}:
        die(f"expected allow_once for a read inside cwd, got {ans}")
    update({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"})
    chunk("done ", "m2")
    chunk('{"x": 1}', "m2")
    update({"sessionUpdate": "usage_update", "used": 100, "size": 1000, "cost": {"amount": 0.5, "currency": "USD"}})
    return {"incomplete": "max_turn_requests", "refusal": "refusal"}.get(MODE, "end_turn")


def options(model, mode):
    """the complete config state (the spec: every answer and update carries all of it)"""
    return [{"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": model,
             "options": [{"value": "small", "name": "Small"}, {"value": "big", "name": "Big"}]},
            {"id": "mode", "name": "Mode", "category": "mode", "type": "select", "currentValue": mode,
             "options": [{"value": "auto", "name": "Auto"}, {"value": "ask", "name": "Ask"}]}]


def main():
    cfg = {"model": "small", "mode": "auto"}
    model_set = authed = False
    while True:
        msg = read()
        m, mid, p = msg.get("method"), msg.get("id"), msg.get("params") or {}
        if m is None:
            continue
        if m == "initialize":
            if p.get("clientCapabilities") not in ({}, None):
                die(f"expected no client capabilities, got {p.get('clientCapabilities')}")
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": 2 if MODE == "version" else 1,
                "agentCapabilities": {"sessionCapabilities": {"close": {}}},
                "authMethods": [{"id": "fake-login", "name": "Fake login"}]}})
            if MODE == "fail":
                print("boom: not signed in", file=sys.stderr)
                sys.exit(3)
        elif m == "session/new":
            if not os.path.isabs(p.get("cwd", "")) or p.get("mcpServers") != []:
                die(f"bad session/new {p}")
            if MODE == "auth" and not authed:
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            res = {} if MODE == "malformed" else {"sessionId": SID, "modes": {"currentModeId": "auto", "availableModes": [
                {"id": "auto", "name": "Auto"}, {"id": "ask", "name": "Ask before acting"}]}}
            if MODE == "model":
                res["configOptions"] = [{"id": "model", "name": "Model", "category": "model", "type": "select",
                                         "currentValue": "small", "options": [
                                             {"group": "g", "name": "All", "options": [
                                                 {"value": "small", "name": "Small"}, {"value": "big", "name": "Big"}]}]}]
            if MODE in ("depmode", "fallback"):
                res["configOptions"] = options(cfg["model"], cfg["mode"])
            send({"jsonrpc": "2.0", "id": mid, "result": res})
            cwd = p["cwd"]
        elif m == "session/set_mode":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif m == "authenticate":
            authed = p.get("methodId") == "fake-login"
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif m == "session/set_config_option":
            model_set = p.get("configId") == "model" and p.get("value") == "big"
            if MODE in ("depmode", "fallback"):
                cfg[p.get("configId")] = p.get("value")
                if MODE == "depmode" and p.get("configId") == "model":
                    cfg["mode"] = "auto"   # a dependent change
                send({"jsonrpc": "2.0", "id": mid, "result": {"configOptions": options(cfg["model"], cfg["mode"])}})
                continue
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif m == "session/prompt":
            if MODE == "model" and not model_set:
                die("the model was not set before the prompt")
            stop = turn(cwd)
            send({"jsonrpc": "2.0", "id": mid, "result": {} if MODE == "nostop" else {"stopReason": stop}})
        elif m == "session/close":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    main()
