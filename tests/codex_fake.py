"""A stand-in `codex` for the tests of the platform's own Codex runs (bin/runtime_codex.py, docs/design/runtime-codex.md
Step 1): `debug prompt-input` and `exec` as 0.169 answers them (the shapes measured 11-14, no login), `--version`, and
`login status`. What it does is set in a JSON file beside it (its runs get a minimal environment, so no variable reaches
it), and each call is logged there too.

    exe = codex_fake.write(tmp, answer='{"x": 1}')      # then runtime_codex.check_binary(exe)
    codex_fake.calls(exe)                              # [{"argv", "env", "cwd", "stdin"}]

Settings: answer (the last message), fail (a turn.failed message, exit 1), pre_extra / post_extra (a collaboration
namespace in prompt-input's list / in the rollout), no_list (prompt-input shows no tool list), entries_extra (one more
write entry in the rollout's profile), no_rollout.
"""
import json
import pathlib
import sys

SCRIPT = r'''#!{python}
import json, os, pathlib, sys, tomllib
me = pathlib.Path(sys.argv[0])
conf = json.loads(me.with_suffix(".json").read_text())
args = sys.argv[1:]
stdin = sys.stdin.read() if "-" in args or "--with-api-key" in args else ""
with open(str(me) + ".log", "a") as f:
    f.write(json.dumps({{"argv": args, "env": dict(os.environ), "cwd": os.getcwd(), "stdin": stdin}}) + "\n")
cvals = [args[i + 1] for i, a in enumerate(args) if a == "-c" and i + 1 < len(args)]
shell = "features.shell_tool=true" in cvals
def nested(extra):
    names = ["apply_patch", "list_mcp_resources", "read_mcp_resource"] + (["exec_command", "write_stdin"] if shell else [])
    desc = "".join("declare const tools: {{ %s(args: any): Promise<unknown>; }};\n" % n for n in names)
    spaces = [{{"type": "namespace", "name": "functions", "tools": [
        {{"type": "custom", "name": "exec", "description": desc}}, {{"type": "function", "name": "wait"}}]}}]
    if extra:
        spaces.append({{"type": "namespace", "name": "collaboration", "tools": [{{"type": "function", "name": "spawn_agent"}}]}})
    return {{"type": "additional_tools", "role": "developer", "tools": spaces}}
def cval(key):
    for v in cvals:
        k, _s, rest = v.partition("=")
        if k == key:
            return tomllib.loads("v = " + rest)["v"]
    return None
if args[:1] == ["--version"]:
    print("codex-cli 0.169.0")
    sys.exit(0)
if args[:2] == ["login", "status"]:
    print("Logged in using ChatGPT")
    sys.exit(0)
if args[:2] == ["debug", "prompt-input"]:
    items = [] if conf.get("no_list") else [nested(conf.get("pre_extra"))]
    items.append({{"type": "message", "role": "developer", "content": [{{"type": "input_text", "text": "You are Codex"}}]}})
    print(json.dumps(items))
    sys.exit(0)
if args[:1] == ["exec"]:
    home = pathlib.Path(os.environ["CODEX_HOME"])
    profile = cval("default_permissions")
    fs = cval("permissions.%s.filesystem" % profile) or {{}}
    entries = [{{"path": {{"type": "glob_pattern", "pattern": k}} if "*" in k else {{"type": "path", "path": k}}, "access": v}}
               for k, v in fs.items()]
    entries.append({{"path": {{"type": "special", "value": {{"kind": "root"}}}}, "access": "read"}})
    entries.append({{"path": {{"type": "path", "path": str(home / "tmp" / "arg0-macos" / "codex-arg0x")}}, "access": "read"}})
    if conf.get("entries_extra"):
        entries.append({{"path": {{"type": "path", "path": "/"}}, "access": "write"}})
    lines = [{{"type": "session_meta", "payload": {{"id": "t1", "originator": "codex_exec", "cli_version": "0.169.0"}}}},
             {{"type": "response_item", "payload": nested(conf.get("post_extra"))}},
             {{"type": "world_state", "payload": {{"state": {{"top_level_tools": {{"functions": "a", "functions.exec": "b",
                                                                          "functions.wait": "c"}}}}}}}},
             {{"type": "turn_context", "payload": {{"model": cval("model"), "approval_policy": "never",
               "multi_agent_version": "disabled", "active_permission_profile": {{"id": profile, "extends": ":read-only"}},
               "permission_profile": {{"type": "managed", "file_system": {{"type": "restricted", "entries": entries}}}}}}}}]
    if not conf.get("no_rollout"):
        day = home / "sessions" / "2026" / "11" / "14"
        day.mkdir(parents=True, exist_ok=True)
        (day / "rollout-2026-11-14T00-00-00-t1.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    print(json.dumps({{"type": "thread.started", "thread_id": "t1"}}))
    if conf.get("fail"):
        print(json.dumps({{"type": "turn.failed", "error": {{"message": conf["fail"]}}}}))
        sys.exit(1)
    out = args[args.index("-o") + 1]
    pathlib.Path(out).write_text(conf.get("answer", "ok"))
    print(json.dumps({{"type": "turn.completed", "usage": {{"input_tokens": 11, "output_tokens": 3}}}}))
    sys.exit(0)
sys.exit(2)
'''


def write(tmp, **conf) -> pathlib.Path:
    """The stand-in at <tmp>/vendor/aarch64-apple-darwin/bin/codex (the native binary's place in an npm copy)."""
    exe = pathlib.Path(tmp) / "node_modules" / "@openai" / "codex-x" / "vendor" / "triple" / "bin" / "codex"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text(SCRIPT.format(python=sys.executable))
    exe.chmod(0o755)
    configure(exe, **conf)
    return exe


def configure(exe, **conf) -> None:
    pathlib.Path(exe).with_suffix(".json").write_text(json.dumps(conf))


def calls(exe) -> list[dict]:
    log = pathlib.Path(str(exe) + ".log")
    return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
