#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""agent-hub 唯一呼叫 `claude -p` 的地方。每次呼叫都要是 contracts/llm-points.yaml 宣告過的判斷點:
旗標(模型、權限模式、工具、allowedTools)照宣告加,輸出照宣告的 schema 驗證,模型不能用或輸出不合格時
照宣告回安全預設或交給呼叫端的失敗路徑,並在匯流排記 job.failed。

  llm.py run <point> [--cwd D] [--model M] [--prompt-file F]   prompt 從 --prompt-file 或 stdin
  輸出:成功時跟 `claude -p --output-format json` 一樣;失敗時
        {"type": "result", "is_error": true, "structured_output": <default 或 null>, "llm_point": …, "llm_error": …}
        登錄有寫 `default` 值 → structured_output 是那個值、exit 0;否則 null、exit 1(呼叫端走自己的失敗路徑)。
        on_failure(skip / retry_later / ask_user / say_so / default)是宣告「失敗時實際發生什麼事」,
        給人跟評估看的,不影響這支的行為——格式見 contracts/llm-points.v1.schema.json
  llm.py run <point> --dry-run ...         只印出會執行的指令(JSON list),不呼叫模型
  llm.py lint     列出 bin/ 裡繞過這支的 `claude -p`(contracts-check 用)
  llm.py health   hub.json llm 的每一家 provider 能不能用

判斷點宣告 `shape: completion` → 交給 completion.py(每家同一套處理;docs/design/llm-providers.md):hub.json 有 `llm` 區塊就照它
選 provider(任何 key),沒有就交給 runtime.default 的 completion 介面(Claude Code 關掉工具跑)。agent 形狀的點照舊交給 agent 介面。

憑證(2026-10-24 Knas 決定統一):預設用 ~/.claude/headless-oauth-token(`claude setup-token`,一年,launchd/SSH 都一樣,
只有一份要換);判斷點宣告 `auth: subscription`、或呼叫端設 LLM_AUTH=subscription(登錄 job 的 `connectors: true`)
才用訂閱登入(keychain / .credentials.json)——claude.ai connector(Upwork 等)只有訂閱登入才會載入。
要用 token 卻沒有 token 檔就失敗(不默默改用訂閱登入;agent-hub/claude-login 也會發警報)。
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
import i18n  # noqa: E402
import minischema  # noqa: E402

import yaml  # noqa: E402

POINTS = hubconf.ROOT / "contracts" / "llm-points.yaml"
_ = i18n.domain(__file__)
CALL = re.compile(r"""(\bclaude\s+-p\b|["']claude["']\s*,\s*["']-p["'])""")


def points() -> dict:
    return yaml.safe_load(POINTS.read_text())


def job_failed(point: str, reason: str) -> None:
    subprocess.run([sys.executable, str(hubconf.ROOT / "bin" / "hubbus"), "publish", "job.failed",
                    "--source", "llm", "--payload",
                    json.dumps({"job": f"llm:{point}", "detail": reason[:500]}, ensure_ascii=False)],
                   capture_output=True)


def fail(point: str, spec: dict, reason: str) -> int:
    job_failed(point, reason)
    # 只有登錄真的寫了 default 值才回 exit 0:lease-handover 是 on_failure: default,但安全結果由 runner 提供
    # (它看 exit != 0 才重試、做最小交接),不能因為標記就改成 exit 0
    has_default = "default" in spec
    print(json.dumps({"type": "result", "is_error": True, "structured_output": spec.get("default"),
                      "llm_point": point, "llm_error": reason}, ensure_ascii=False))
    return 0 if has_default else 1


def output_schema(spec: dict) -> str | None:
    """宣告的輸出 schema 路徑(相對 contracts/);format: text 的點沒有。"""
    out = spec.get("output") or {}
    return out.get("schema") if out.get("format") == "json" else None


def model_for(point: str, spec: dict | None = None, cli: str | None = None, runtime: str | None = None) -> str | None:
    """這個判斷點交給執行環境(runtime)時用哪個模型——全平台只在這裡算(librarian、consolidate、週報、schedulectl 都問這裡,
    不各自留一份預設表):--model > hub.json `llm.points.<點>.model`(沒指定 provider、或 provider 就是這個 runtime)>
    runtime 的組別表(`runtime_ports.models`)。None = 不指定,用工具自己的預設(model_group: default 的點)。
    hub.json 有 llm.providers 時 completion 點由 completion.resolve 選,不經過這裡。"""
    import runtime_ports
    if cli:
        return cli
    spec = spec if spec is not None else points()["points"].get(point) or {}
    name = runtime or spec.get("runtime") or runtime_ports.default_name()
    pin = ((hubconf.get("llm") or {}).get("points") or {}).get(point) or {}
    if pin.get("model") and pin.get("provider") in (None, name):
        return pin["model"]
    return runtime_ports.models(name).get(spec.get("model_group") or "standard")


def auth_mode(spec: dict) -> str:
    """用哪種登入:LLM_AUTH(呼叫端這次指定,例:login-check 檢查 token)> 判斷點宣告的 auth > 預設 token。
    憑證放哪、環境變數叫什麼是 runtime 的事(auth 介面,第 6 層)。"""
    return os.environ.get("LLM_AUTH") or spec.get("auth") or "token"


def auth_env(spec: dict, rt) -> tuple[dict | None, str]:
    """(要交給 agent 的環境變數, 沒有時給人看的原因)。LLM_AUTH 是 llm.py 自己的變數,不往下傳;
    hub.json llm 用環境變數給的 API key 也不往下傳(有 Bash 的 agent 都讀得到)。"""
    import completion
    drop = {"LLM_AUTH"} | completion.key_envs()
    base = {k: v for k, v in os.environ.items() if k not in drop}
    return rt.auth_env(auth_mode(spec), base)


def run_completion(a, spec: dict, prompt: str, model: str | None) -> int:
    """shape: completion 的判斷點:交給 completion.py(每家同一套處理)。
    hub.json 有 llm.providers → completion.resolve 選 provider 跟模型(--model 不用);
    沒有 → 交給 runtime.default 的 completion 介面,模型是 model_for 算的那個。"""
    import completion
    import runtime_ports
    schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    target = None
    if not completion.settings():
        name = spec.get("runtime") or runtime_ports.default_name()
        if "completion" not in runtime_ports.capabilities(name):
            return fail(a.point, spec, _("runtime {name} has no completion interface, and hub.json has no llm section "
                                         "(set up an LLM provider)", name=name))
        target = completion.runtime_target(name, model, auth_mode(spec))
    try:
        out = completion.run(a.point, spec, prompt, schema, target=target)
    except completion.Refused as e:
        return fail(a.point, spec, str(e))
    print(json.dumps(out, ensure_ascii=False))
    return 0


def run(a) -> int:
    """判斷點這一層:讀宣告、組 Task、決定登入,交給執行環境(runtime_ports 的 agent 介面;Claude Code 只是一個實作)。
    schema 驗證、失敗政策(fail → job.failed、default)、輸出格式留在這裡,不在 runtime 裡。"""
    import runtime_ports
    spec = points()["points"].get(a.point)
    if spec is None:
        print(_("llm.py: {point} is not a declared judgment point (contracts/llm-points.yaml)", point=a.point),
              file=sys.stderr)
        return 2
    prompt = "" if a.cmd == "exec" else (pathlib.Path(a.prompt_file).read_text() if a.prompt_file else sys.stdin.read())
    model = model_for(a.point, spec, a.model)
    if a.cmd == "run" and spec.get("shape") == "completion" and not getattr(a, "dry_run", False):
        return run_completion(a, spec, prompt, model)
    try:
        rt = runtime_ports.load(spec.get("runtime"), ports=("agent",))
    except runtime_ports.Unsupported as e:   # 沒有 coding agent:照宣告的失敗路徑走,原因寫清楚
        return fail(a.point, spec, str(e))
    if "auth" in runtime_ports.capabilities(spec.get("runtime")):
        env, why = auth_env(spec, rt)
        if env is None:
            return fail(a.point, spec, why)
    else:   # 沒有登入介面的 runtime(例:指令樣板):工具自己登入;平台的 key 照樣不往下傳
        import completion
        drop = {"LLM_AUTH"} | completion.key_envs()
        env = {k: v for k, v in os.environ.items() if k not in drop}
    env["HUB_LLM_POINT"] = a.point   # 叫起的程序(含它的 hook)知道自己是平台的 headless 呼叫:hubbus 不把信送給它
    # 平台叫起的 agent 打得到 `agenthub …`(規則檔、信箱 prompt 只寫指令名稱,不寫 hub 裝在哪)
    env["PATH"] = f"{hubconf.ROOT / 'bin'}:{env.get('PATH', '')}".rstrip(":")
    schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    task = runtime_ports.Task(point=a.point, prompt=prompt, spec=spec, cwd=a.cwd or None, model=model,
                              output_schema=schema, timeout_s=spec.get("timeout_s", 1200), env=env,
                              extra=tuple(a.extra or ()))
    if a.cmd == "exec":
        # 長駐的串流 session(例:通道租借)。暫時的:之後由第 1 層的 open_session 取代
        if not spec.get("stream"):
            print(_("llm.py: {point} does not declare stream: true, so it cannot run under exec", point=a.point), file=sys.stderr)
            return 2
        if not hasattr(rt, "exec_stream"):
            print(_("llm.py: {point}: this runtime does not support long-running streaming sessions", point=a.point),
                  file=sys.stderr)
            return 2
        rt.exec_stream(task)
        return 0
    if getattr(a, "dry_run", False):
        print(json.dumps(rt.describe(task), ensure_ascii=False))
        return 0
    res = rt.run(task)
    if not res.ok:
        return fail(a.point, spec, res.error)
    # 沒有 structured_output 不算失敗:原樣交給呼叫端(librarian 刻意「仍以實際檔案變化為準」);有的話要合格
    if schema is not None and res.structured_output is not None:
        if (errs := minischema.errors(schema, res.structured_output)):
            return fail(a.point, spec, _("output does not match the schema: {errors}", errors="; ".join(errs[:3])))
    # 輸出的欄位名稱是 llm.py 對呼叫端的約定(librarian、consolidate、lease-runner…讀這些),不隨 runtime 改
    print(json.dumps({"type": "result", "is_error": res.incomplete, "result": res.text,
                      "structured_output": res.structured_output, "total_cost_usd": res.cost_usd,
                      "session_id": res.session_id, "num_turns": res.turns, "subtype": res.stop,
                      "permission_denials": list(res.denials), "llm_point": a.point, "llm_model": model},
                     ensure_ascii=False))
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
                bad.append(_("{file}:{line}: a claude -p that does not go through bin/llm.py", file=rel, line=i))
    for name, spec in p["points"].items():
        if output_schema(spec) and not (POINTS.parent / output_schema(spec)).exists():
            bad.append(_("judgment point {name}: output.schema {schema} does not exist", name=name,
                         schema=output_schema(spec)))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(prog="llm.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("point")
    r.add_argument("--cwd")
    r.add_argument("--model")
    r.add_argument("--prompt-file")
    r.add_argument("--dry-run", action="store_true", help=_("only print the command that would run; no model call"))
    r.add_argument("extra", nargs="*", help=_("extra claude arguments (after --)"))
    e = sub.add_parser("exec", help=_("long-running streaming session: build the flags from the declaration, then exec "
                                      "claude (stdin/stdout are stream-json)"))
    e.add_argument("point")
    e.add_argument("--cwd")
    e.add_argument("--model")
    e.add_argument("--prompt-file")
    e.add_argument("extra", nargs="*")
    sub.add_parser("lint")
    sub.add_parser("health", help=_("whether each provider in hub.json llm works (key, connection, model there)"))
    a = ap.parse_args()
    if a.cmd == "exec" and a.cwd:
        os.chdir(a.cwd)
    if a.cmd in ("run", "exec"):
        return run(a)
    if a.cmd == "health":
        import completion
        rows = completion.health_all()
        for name, ok, why in rows:
            mark = "✓" if ok else "✗"
            print(_("{mark} {name}: {why}", mark=mark, name=name, why=why) if why else f"{mark} {name}")
        return 0 if all(ok for _name, ok, _why in rows) else 1
    bad = lint()
    for b in bad:
        print(b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
