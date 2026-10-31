"""任何有 headless 模式的 CLI agent:設定裡寫指令樣板就能接,不用寫程式(docs/design/tool-agnostic.md 原則 4)。

hub.json:
  "runtime": {"default": "command",
              "command": {"run": ["agy", "--add-dir", ".", "--mode", "accept-edits", "--model", "{model}", "-p", "{prompt}"],
                                                   指令樣板(list)
                          "stdin": true,           prompt 從 stdin 給(預設:樣板裡沒有 {prompt}/{prompt_file} 就從 stdin)
                          "output_file": false,    true = 樣板裡有 {output_file},跑完讀那個檔(例:codex exec --output-last-message)
                          "env": {"X": "y"}}}      額外的環境變數(不要放 key 的值;key 用工具自己的登入或 hub.json llm)
  樣板的佔位:{prompt}(prompt 全文當一個參數:不讀 stdin 的工具用)、{prompt_file}(prompt 寫在暫存檔的路徑)、
  {output_file}、{cwd}(工作目錄的絕對路徑:把工具的讀寫權限限在丟棄式 worktree 裡用)、{model}(沒有模型就整個
  參數拿掉:含 {model} 的那一個,連同它前面那個旗標)。
  例(Antigravity CLI,只准讀寫工作目錄、不准跑指令):
    ["agy", "--add-dir", "{cwd}", "--mode", "accept-edits", "--settings",
     "{\"permissions\": {\"allow\": [\"read_file({cwd})\", \"write_file({cwd})\"], \"deny\": [\"command(*)\"]}}",
     "-p", "{prompt}"]
  例(Codex CLI,prompt 從 stdin、答案寫進檔案;寫入由它自己的沙盒限在工作目錄,讀不限):
    "run": ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
            "--color", "never", "-o", "{output_file}", "-"], "output_file": true
  跑完 exit 0 卻沒有任何輸出算失敗(有的工具權限被擋就這樣靜靜結束),原因用 stderr。

只提供兩個介面:agent(跑一次)跟 completion。其他(接續、串流、hook、對話紀錄、登入狀態)都沒有——平台照
Unsupported 明確關掉那些功能。所以「跑一次」只該用在平台的丟棄式 worktree 裡(bin/propose_apply.py):這種工具的權限
平台管不到,保證來自「它碰不到真的 checkout,平台只套用准許的路徑」。
結構化輸出:prompt 最後附上 schema,回來的文字由呼叫端(llm.py / completion.py)解析、驗證。標準函式庫。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
from runtime_ports import CompletionResult, TaskResult  # noqa: E402

_ = i18n.domain(__file__)


def _conf() -> dict:
    import hubconf
    c = hubconf.get("runtime.command") or {}
    if not c.get("run"):
        raise RuntimeError(_("hub.json runtime.command.run is not set (the command template)"))
    return c


def _argv(template: list, model: str | None, prompt_file: str, output_file: str, prompt: str = "",
          cwd: str = "") -> list:
    out = []
    for i, part in enumerate(template):
        if "{model}" in part and not model:
            if out and out[-1].startswith("-") and part.strip() == "{model}":
                out.pop()   # 「-m {model}」整組拿掉
            continue
        if part == "{prompt}":
            out.append(prompt)   # 整個參數就是 prompt:不再替換裡面的佔位(prompt 本身可能有 {model} 之類的字)
            continue
        out.append(part.replace("{prompt_file}", prompt_file).replace("{output_file}", output_file)
                   .replace("{cwd}", cwd).replace("{model}", model or ""))
    return out


def _schema_note(schema: dict | None) -> str:
    if schema is None:
        return ""
    return ("\n\n---\n最後只回一個 JSON 物件(不要其他文字、不要 ``` 圍起來),符合這個 JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))


def _run(prompt: str, model: str | None, cwd: str | None, env: dict | None, timeout_s: int) -> tuple[int | None, str, str]:
    conf = _conf()
    with tempfile.TemporaryDirectory(prefix="hub-command-") as d:
        pf, of = pathlib.Path(d) / "prompt.md", pathlib.Path(d) / "output.txt"
        pf.write_text(prompt)
        argv = _argv(list(conf["run"]), model, str(pf), str(of), prompt, str(pathlib.Path(cwd or os.getcwd()).resolve()))
        use_stdin = conf.get("stdin", not any("{prompt_file}" in p or p == "{prompt}" for p in conf["run"]))
        e = {**(os.environ if env is None else env), **(conf.get("env") or {})}
        try:
            p = subprocess.Popen(argv, cwd=cwd or None, env=e, stdin=subprocess.PIPE if use_stdin else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        except FileNotFoundError:
            return None, "", _("{exe} not found", exe=argv[0])
        try:
            out, err = p.communicate(prompt if use_stdin else None, timeout=timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)   # 整個程序群組:工具自己叫起的子程序也一起停
            p.communicate()
            return None, "", _("took longer than {secs} s", secs=timeout_s)
        if conf.get("output_file") and of.exists():
            out = of.read_text()
        return p.returncode, out, err


# ---------- agent(跑一次)----------

def describe(task) -> list:
    conf = _conf()
    return _argv(list(conf["run"]), task.model, "<prompt_file>", "<output_file>", "<prompt>", task.cwd or "<cwd>")


def run(task) -> TaskResult:
    code, out, err = _run(task.prompt + _schema_note(task.output_schema), task.model, task.cwd, task.env, task.timeout_s)
    if code is None:
        return TaskResult(False, error=err)
    if code != 0:
        return TaskResult(False, error=f"exit {code}:{(err.strip() or out.strip())[-300:]}")
    if not out.strip():
        return TaskResult(False, error=_("no output: {detail}", detail=err.strip()[-300:] or _("(stderr is empty too)")))
    so = None
    if task.output_schema is not None:
        import completion
        try:
            so = completion.parse_json(out)
        except ValueError:
            so = None   # llm.py 照「沒有 structured_output」處理
    return TaskResult(True, text=out.strip(), structured_output=so, stop="end")


# ---------- completion ----------

def complete(req, conf) -> CompletionResult:
    code, out, err = _run(req.prompt + _schema_note(req.schema), req.model, tempfile.gettempdir(), None, req.timeout_s)
    if code is None or code != 0:
        return CompletionResult(False, stop="error", error=err or f"exit {code}")
    if not out.strip():
        return CompletionResult(False, stop="error", error=_("no output: {detail}", detail=err.strip()[-300:]))
    return CompletionResult(True, text=out, stop="end")


def health(conf) -> tuple:
    try:
        c = _conf()
    except RuntimeError as e:
        return False, str(e)
    exe = c["run"][0]
    return (bool(shutil.which(exe)), "" if shutil.which(exe) else _("{exe} not found", exe=exe))
