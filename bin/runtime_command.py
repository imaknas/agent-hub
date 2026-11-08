"""Any CLI agent with a headless mode: plug it in by writing a command template in the settings, no code needed
(docs/design/tool-agnostic.md principle 4).

{settings} (never as runtime.default, which config check refuses: pinned on a throwaway point, beside another default):
  "llm": {"points": {"mailbox-propose": {"runtime": "command"}}}, "mailbox": {"handler": "propose"},
  "runtime": {"command": {"run": ["agy", "--add-dir", ".", "--mode", "accept-edits", "--model", "{model}", "-p", "{prompt}"],
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

Only one interface is provided: agent (one run), and it runs only in the platform's throwaway worktree
(THROWAWAY_ONLY; runtime_ports.THROWAWAY_POINTS, bin/propose_apply.py marks the copy with HUB_THROWAWAY_COPY). A template
can't carry a judgment point's limits (its tools, allowed tools, permission mode, extra arguments) and the tool gets the
whole environment, so anywhere else it would have a shell and read anything (docs/design/runtime-codex.md, "Found by the
11-08 blind review"); in the copy the guarantee comes from "it cannot touch the real checkout, and the platform applies
only allowed paths". Every other run is refused with that reason. No completion interface either: a generic template
can't be made tool-free (a completion's contract), so completion points need llm.providers. Nothing else (resume,
streaming, hooks, login status) -- the platform turns those features off explicitly via Unsupported.
Structured output: the schema is appended to the end of the prompt, and the returned text is parsed and validated by
the caller (llm.py). Standard library.
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
from runtime_ports import TaskResult  # noqa: E402

_ = i18n.domain(__file__)
RULES_FILE_NAMES = ("AGENTS.md",)   # the cross-tool convention (Codex and others read it)
THROWAWAY_ONLY = True   # runs only where the platform confines them (runtime_ports.THROWAWAY_POINTS)
COPY_VAR = "HUB_THROWAWAY_COPY"   # set by propose_apply: this working directory is its throwaway worktree


def unsupported(port: str) -> str | None:
    """Why it lacks a port, for people (doctor's feature table, Unsupported reasons)."""
    if port == "completion":
        return _("a command template can't be held to no tools, and the tool gets a shell and reads anything; an answer-only "
                 "judgment point needs an LLM provider (brunna llm setup)")
    if port == "confine":
        return _("a command template can't carry a judgment point's limits: it runs only in the platform's throwaway worktree")
    return None


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
    try:
        tmp = tempfile.TemporaryDirectory(prefix="hub-command-", dir=hubconf.private_tmp(), ignore_cleanup_errors=True)   # its output is taken as the answer
    except OSError as e:
        return None, "", _("could not make a folder for the run: {e}", e=e)
    with tmp as d:
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


def _in_copy(task) -> str:
    """Why this run is refused, or "" when it may run: only a THROWAWAY_POINTS point, in the throwaway worktree propose_apply
    marks (the mark, the point and the place itself; no working directory given means the process's own)."""
    import hubconf
    import runtime_ports
    cwd = os.path.realpath(task.cwd or os.getcwd())
    marked = (os.environ if task.env is None else task.env).get(COPY_VAR) or ""
    if task.point in runtime_ports.THROWAWAY_POINTS and hubconf.is_throwaway_copy(cwd, marked=marked):
        return ""
    return _("the command runtime runs only {points} in the platform's throwaway worktree, and this is {point} in {cwd}: a "
             "command template can't carry a judgment point's limits, so anywhere else the tool would have a shell and read "
             "anything", points=", ".join(runtime_ports.THROWAWAY_POINTS), point=task.point, cwd=cwd)


def run(task) -> TaskResult:
    why = _in_copy(task)
    if why:
        return TaskResult(False, error=why)
    code, out, err = _run(task.prompt + _schema_note(task.output_schema), task.model, task.cwd, task.env, task.timeout_s)
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
