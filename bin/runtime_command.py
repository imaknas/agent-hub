"""Any CLI agent with a headless mode: plug it in by writing a command template in the settings, no code needed
(docs/design/tool-agnostic.md principle 4).

{settings}:
  "runtime": {"default": "command",
              "command": {"run": ["agy", "--add-dir", ".", "--mode", "accept-edits", "--model", "{model}", "-p", "{prompt}"],
                                                   the command template (list)
                          "stdin": true,           the prompt is given on stdin (default: stdin when the template has
                                                   no {prompt}/{prompt_file})
                          "output_file": false,    true = the template has {output_file}, read that file afterwards
                                                   (e.g. codex exec --output-last-message)
                          "env": {"X": "y"}}}      extra environment variables (no key values here; keys come from
                                                   the tool's own login or {settings} llm)
  Template placeholders: {prompt} (the whole prompt as one argument: for tools that do not read stdin), {prompt_file}
  (path of a temporary file holding the prompt), {output_file}, {cwd} (absolute path of the working directory: for
  confining the tool's reads and writes to the throwaway worktree), {model} (no model: the whole argument is
  dropped -- the one containing {model}, together with the flag before it).
  Example (Antigravity CLI, may read and write only the working directory, may not run commands):
    ["agy", "--add-dir", "{cwd}", "--mode", "accept-edits", "--settings",
     "{\"permissions\": {\"allow\": [\"read_file({cwd})\", \"write_file({cwd})\"], \"deny\": [\"command(*)\"]}}",
     "-p", "{prompt}"]
  Example (Codex CLI, prompt on stdin, answer written to a file; its own sandbox confines writes to the working
  directory, reads are not limited):
    "run": ["codex", "exec", "--sandbox", "workspace-write", "--skip-git-repo-check", "--ephemeral",
            "--color", "never", "-o", "{output_file}", "-"], "output_file": true
  Exit 0 without any output counts as a failure (some tools end quietly like that when a permission is blocked);
  stderr gives the reason.

Only two interfaces are provided: agent (one run) and completion. Nothing else (resume, streaming, hooks,
login status) -- the platform turns those features off explicitly via Unsupported. So "one run" should
only be used in the platform's throwaway worktree (bin/propose_apply.py): the platform cannot control such a tool's
permissions, and the guarantee comes from "it cannot touch the real checkout, and the platform applies only allowed
paths".
Structured output: the schema is appended to the end of the prompt, and the returned text is parsed and validated by
the caller (llm.py / completion.py). Standard library.
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
        raise RuntimeError(_("{settings} runtime.command.run is not set (the command template)"))
    return c


def _argv(template: list, model: str | None, prompt_file: str, output_file: str, prompt: str = "",
          cwd: str = "") -> list:
    out = []
    for i, part in enumerate(template):
        if "{model}" in part and not model:
            if out and out[-1].startswith("-") and part.strip() == "{model}":
                out.pop()   # drop "-m {model}" as a pair
            continue
        if part == "{prompt}":
            out.append(prompt)   # the whole argument is the prompt: no placeholder replacement inside it
            # (the prompt itself may contain text like {model})
            continue
        out.append(part.replace("{prompt_file}", prompt_file).replace("{output_file}", output_file)
                   .replace("{cwd}", cwd).replace("{model}", model or ""))
    return out


def _schema_note(schema: dict | None) -> str:
    if schema is None:
        return ""
    return ("\n\n---\nAt the end, reply with one JSON object only (no other text, not wrapped in ```), matching this "
            "JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))


def _run(prompt: str, model: str | None, cwd: str | None, env: dict | None, timeout_s: int) -> tuple[int | None, str, str]:
    conf = _conf()
    import hubconf
    with tempfile.TemporaryDirectory(prefix="hub-command-", dir=hubconf.private_tmp()) as d:   # its output is taken as the answer
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
            os.killpg(p.pid, signal.SIGKILL)   # the whole process group: child processes the tool started stop too
            p.communicate()
            return None, "", _("took longer than {secs} s", secs=timeout_s)
        if conf.get("output_file") and of.exists():
            out = of.read_text()
        return p.returncode, out, err


# ---------- agent (one run) ----------

def describe(task) -> list:
    conf = _conf()
    return _argv(list(conf["run"]), task.model, "<prompt_file>", "<output_file>", "<prompt>", task.cwd or "<cwd>")


def run(task) -> TaskResult:
    try:
        code, out, err = _run(task.prompt + _schema_note(task.output_schema), task.model, task.cwd, task.env, task.timeout_s)
    except OSError as e:
        return TaskResult(False, error=_("could not make a folder for the run: {e}", e=e))
    if code is None:
        return TaskResult(False, error=err)
    if code != 0:
        return TaskResult(False, error=_("exit {code}: {detail}", code=code, detail=(err.strip() or out.strip())[-300:]))
    if not out.strip():
        return TaskResult(False, error=_("no output: {detail}", detail=err.strip()[-300:] or _("(stderr is empty too)")))
    so = None
    if task.output_schema is not None:
        import completion
        try:
            so = completion.parse_json(out)
        except ValueError:
            so = None   # llm.py handles it as "no structured_output"
    return TaskResult(True, text=out.strip(), structured_output=so, stop="end")


# ---------- completion ----------

def complete(req, conf) -> CompletionResult:
    import hubconf
    try:
        # an empty folder of its own: a template's {cwd} may grant the tool all of it
        with tempfile.TemporaryDirectory(prefix="hub-command-cwd-", dir=hubconf.private_tmp(), ignore_cleanup_errors=True) as cwd:
            code, out, err = _run(req.prompt + _schema_note(req.schema), req.model, cwd, None, req.timeout_s)
    except OSError as e:
        return CompletionResult(False, stop="error", error=_("could not make a folder for the run: {e}", e=e))
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
