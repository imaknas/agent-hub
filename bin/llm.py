#!/usr/bin/env python3
"""agent-hub 唯一呼叫 `claude -p` 的地方。每次呼叫都要是 contracts/llm-points.yaml 宣告過的判斷點:
旗標(模型、權限模式、工具、allowedTools)照宣告加,輸出照宣告的 schema 驗證,模型不能用或輸出不合格時
照宣告回安全預設或交給呼叫端的失敗路徑,並在匯流排記 job.failed。

  llm.py run <point> [--cwd D] [--model M] [--prompt-file F]   prompt 從 --prompt-file 或 stdin
  輸出:成功時跟 `claude -p --output-format json` 一樣;失敗時
        {"type": "result", "is_error": true, "structured_output": <default 或 null>, "llm_point": …, "llm_error": …}
        on_failure: caller → exit 1;default → exit 0
  llm.py lint     列出 bin/ 裡繞過這支的 `claude -p`(contracts-check 用)
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import minischema  # noqa: E402

import yaml  # noqa: E402

POINTS = hubconf.ROOT / "contracts" / "llm-points.yaml"
CALL = re.compile(r"""(\bclaude\s+-p\b|["']claude["']\s*,\s*["']-p["'])""")


def points() -> dict:
    return yaml.safe_load(POINTS.read_text())


def job_failed(point: str, reason: str) -> None:
    subprocess.run(["/usr/bin/python3", str(hubconf.ROOT / "bin" / "hubbus"), "publish", "job.failed",
                    "--source", "llm", "--payload",
                    json.dumps({"job": f"llm:{point}", "detail": reason[:500]}, ensure_ascii=False)],
                   capture_output=True)


def fail(point: str, spec: dict, reason: str) -> int:
    job_failed(point, reason)
    default = spec.get("default") if spec.get("on_failure") == "default" else None
    print(json.dumps({"type": "result", "is_error": True, "structured_output": default,
                      "llm_point": point, "llm_error": reason}, ensure_ascii=False))
    return 0 if spec.get("on_failure") == "default" else 1


def run(a) -> int:
    spec = points()["points"].get(a.point)
    if spec is None:
        print(f"llm.py:{a.point} 不是宣告過的判斷點(contracts/llm-points.yaml)", file=sys.stderr)
        return 2
    prompt = "" if a.cmd == "exec" else (pathlib.Path(a.prompt_file).read_text() if a.prompt_file else sys.stdin.read())
    model = a.model or (os.environ.get(spec["model_env"]) if spec.get("model_env") else None) or spec.get("model_default")
    cmd = [hubconf.tool("claude"), "-p", prompt]  # 排程底下的 PATH 沒有 nvm:先照 PATH,再照平台的(nvm 預設版本)
    if model:
        cmd += ["--model", model]
    mode, allowed, prompts = spec.get("permission_mode"), list(spec.get("allowed_tools") or []), spec.get("prompts")
    if spec.get("extra_allow_env") and os.environ.get(spec["extra_allow_env"]):
        allowed += json.loads(os.environ[spec["extra_allow_env"]])  # 只加規則,不改權限模式
    if spec.get("allow_from_env") and os.environ.get(spec["allow_from_env"]):
        # 登錄 job 宣告的 allow 清單:無人值守 = dontAsk,沒列的一律拒絕、不提示
        allowed += json.loads(os.environ[spec["allow_from_env"]])
        mode, prompts = "dontAsk", "none"
    if mode:
        cmd += ["--permission-mode", mode]
    if prompts:
        cmd += ["--permission-prompts", prompts]
    if "tools" in spec:
        cmd += ["--tools", spec["tools"]]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    cmd += list(spec.get("extra_args") or []) + list(a.extra or [])
    if a.cmd == "exec":
        # 長駐的串流 session(例:通道租借):旗標照宣告組好,直接換成 claude,stdin/stdout 交給呼叫的人
        if not spec.get("stream"):
            print(f"llm.py:{a.point} 沒有宣告 stream: true,不能 exec", file=sys.stderr)
            return 2
        cmd[1:3] = ["-p"]  # exec 模式沒有 prompt 參數,訊息從 stdin(stream-json)來
        cmd += ["--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
        os.execvp(cmd[0], cmd)
    cmd += ["--output-format", "json"]
    schema = None
    if spec.get("schema"):
        schema = json.loads((POINTS.parent / spec["schema"]).read_text())
        cmd += ["--json-schema", json.dumps(schema, ensure_ascii=False)]
    try:
        r = subprocess.run(cmd, cwd=a.cwd or None, capture_output=True, text=True, timeout=spec.get("timeout_s", 1200))
    except subprocess.TimeoutExpired:
        return fail(a.point, spec, f"超過 {spec.get('timeout_s', 1200) // 60} 分鐘")
    except FileNotFoundError:
        return fail(a.point, spec, "找不到 claude")
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not r.stdout.strip():
        # claude -p 的錯誤(例:Login expired)常常只在 stdout 的 JSON 裡
        return fail(a.point, spec, f"exit {r.returncode}:{(r.stderr.strip() or r.stdout.strip())[-300:]}")
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return fail(a.point, spec, "輸出不是 JSON:" + r.stdout[:200])
    # 沒有 structured_output 不算失敗:原樣交給呼叫端(librarian 刻意「仍以實際檔案變化為準」);有的話要合格
    if schema is not None and out.get("structured_output") is not None:
        so = out["structured_output"]
        if (errs := minischema.errors(schema, so)):
            return fail(a.point, spec, "輸出不符合 schema:" + "; ".join(errs[:3]))
    out["llm_point"] = a.point
    print(json.dumps(out, ensure_ascii=False))
    return 0


def lint() -> list[str]:
    p = points()
    exempt = {e["file"] for e in p.get("exempt") or []}
    bad = []
    for f in sorted((hubconf.ROOT / "bin").iterdir()):
        rel = f"bin/{f.name}"
        if not f.is_file() or rel in exempt or f.name == "llm.py":
            continue
        try:
            text = f.read_text()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0] if not f.suffix == ".md" else line
            if CALL.search(code):
                bad.append(f"{rel}:{i}:沒有經過 bin/llm.py 的 claude -p")
    for name, spec in p["points"].items():
        if spec.get("schema") and not (POINTS.parent / spec["schema"]).exists():
            bad.append(f"判斷點 {name} 的 schema {spec['schema']} 不存在")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(prog="llm.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("point")
    r.add_argument("--cwd")
    r.add_argument("--model")
    r.add_argument("--prompt-file")
    r.add_argument("extra", nargs="*", help="額外的 claude 參數(放在 -- 後面)")
    e = sub.add_parser("exec", help="長駐串流 session:照宣告組旗標後 exec 成 claude(stdin/stdout 是 stream-json)")
    e.add_argument("point")
    e.add_argument("--cwd")
    e.add_argument("--model")
    e.add_argument("--prompt-file")
    e.add_argument("extra", nargs="*")
    sub.add_parser("lint")
    a = ap.parse_args()
    if a.cmd == "exec" and a.cwd:
        os.chdir(a.cwd)
    if a.cmd in ("run", "exec"):
        return run(a)
    bad = lint()
    for b in bad:
        print(b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
