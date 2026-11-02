"""Any coding agent that speaks the Agent Client Protocol (ACP, agentclientprotocol.com, stable protocol version 1):
the platform is the ACP client. Design: docs/design/runtime-acp.md.

{settings}:
  "runtime": {"acp": {"agent": "<name>",                      which entry below the acp runtime uses
                      "agents": {"<name>": {
                          "command": ["npx", "-y", "<acp agent package>@<version>", ...],   argv, no shell;
                                                   placeholders {model} (dropped with the flag before it when there is
                                                   no model) and {cwd}
                          "env": {"X": "y"},       extra variables (never credential values)
                          "models": {"fast": "...", "standard": "...", "strong": "..."},   group -> model
                          "credential": {"env": "<variable>", "file": "<file in the secrets directory>"},
                                                   optional: a key the platform hands the agent for token logins
                          "mode": "<session mode id>",   optional: set before the prompt (session/set_mode, or a
                                                   mode config option); not offered = the run is refused
                          "auth_method": "<id>",   optional: an authMethods id to `authenticate` with after
                                                   initialize (a login the agent does itself once asked)
                          "login": "how a person logs this agent in"}}}}

Two interfaces: agent (one run) and auth. A run starts the agent, `initialize`s with no client capabilities (the
platform is not the agent's file system, shell or form UI), opens one session in the working directory, sends one
prompt, answers the agent's permission requests by the neutral unattended rules (policy.APPROVALS["unattended"]),
and stops the process. The agent decides when to ask, so the rules are advice; the guarantee is where the run
happens, and the adapter refuses any other place: only the throwaway copy propose_apply marks
(HUB_THROWAWAY_COPY = that directory; no working directory given = the process's own, checked the same way).
That copy contains the work in the repository and what the platform applies; writes by absolute path or through a
shell are contained only by the agent's own mode or sandbox (the optional `mode` below is set and checked). A Task asking to continue an earlier conversation (extra) is refused too. The agent's own MCP servers
are its own: the session gets none from the platform, but `mcpServers` adds, it does not block; an agent's flag for
"no configured MCP servers" goes into its command.
Everything else (long-running sessions, session state, completion, hooks, rules file, transcripts) is absent: the
platform switches those features off with the reason (runtime_ports.Unsupported). Fork is unstable in ACP and not used.
Standard library.
"""
from __future__ import annotations

import json
import os
import pathlib
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import i18n  # noqa: E402
import names  # noqa: E402
from runtime_ports import Credential, TaskResult  # noqa: E402

_ = i18n.domain(__file__)

PROTOCOL_VERSION = 1
AUTH_REQUIRED = -32000          # ACP's "Authentication required"
METHOD_NOT_FOUND = -32601
LOGIN_PREFIX = "auth_required:"  # how this adapter marks a login error (is_login_error looks only for this)
CANCEL_GRACE_S = 2.0
STOP_REASONS = ("end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled")
THROWAWAY_ONLY = True   # runs only where the platform confines them (runtime_ports.THROWAWAY_POINTS)
COPY_VAR = "HUB_THROWAWAY_COPY"   # set by propose_apply: this working directory is its throwaway worktree
# ACP tool kind → the platform's action kind (runtime_ports.KINDS)
_KIND = {"read": "read", "search": "search", "edit": "write_file", "delete": "write_file", "move": "write_file",
         "execute": "run_command", "fetch": "open_web"}
_PATH_KEYS = ("path", "file_path", "abs_path", "filepath", "target", "source", "destination")
_PLACE_KEY = re.compile(r"^(to|from|dest|destination|target|source|new_path|old_path|file|files|paths|dir|dirs|cwd|uri|"
                        r"url)$|(path|_file|_dir|_uri)$", re.I)


def _conf() -> dict:
    import hubconf
    c = hubconf.get("runtime.acp") or {}
    name = c.get("agent")
    agent = (c.get("agents") or {}).get(name) if name else None
    if not agent or not agent.get("command"):
        raise RuntimeError(_("{settings} runtime.acp is not set up (runtime.acp.agent must name an entry of "
                             "runtime.acp.agents with a command)"))
    return {"name": name, **agent}


def __getattr__(name: str):
    if name == "MODELS":  # the selected agent's group → model table (runtime_ports.models reads it)
        try:
            return dict(_conf().get("models") or {})
        except RuntimeError:
            return {}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _argv(template: list, model: str | None, cwd: str) -> list:
    out = []
    for part in template:
        if "{model}" in part and not model:
            if out and out[-1].startswith("-") and not part.startswith("-"):
                out.pop()   # drop "-m {model}" or "-c model={model}" as a pair
            continue
        out.append(part.replace("{cwd}", cwd).replace("{model}", model or ""))
    return out


def _schema_note(schema: dict | None) -> str:
    if schema is None:
        return ""
    return ("\n\n---\nAt the end, reply with one JSON object only (no other text, not wrapped in ```), matching this "
            "JSON schema:\n" + json.dumps(schema, ensure_ascii=False))


class _RpcError(Exception):
    def __init__(self, code, message: str):
        super().__init__(message)
        self.code = code


class _Gone(Exception):
    """The agent's process ended or the run's time is up."""


class _Client:
    """One ACP connection over the agent's stdin/stdout (JSON-RPC 2.0, one message per line)."""

    def __init__(self, argv: list, cwd: str, env: dict, deadline: float):
        self.err = tempfile.TemporaryFile()
        try:
            self.proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=self.err, start_new_session=True)
        except OSError:
            self.err.close()
            raise
        self.deadline, self.next_id, self.lines = deadline, 0, queue.Queue()
        self.writer = None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.on_request = lambda method, params: (None, {"code": METHOD_NOT_FOUND, "message": "Method not found"})
        self.on_update = lambda params: None

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _send(self, obj: dict, deadline: float | None = None) -> None:
        """Write one message, never past the deadline (an agent that stops reading cannot block the run)."""
        data, failed = (json.dumps(obj, ensure_ascii=False) + "\n").encode(), []

        def write():
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                failed.append(e)
        w = self.writer = threading.Thread(target=write, daemon=True)
        w.start()
        w.join(max(0.0, (deadline or self.deadline) - time.monotonic()))
        if w.is_alive():
            raise _Gone("timeout")
        if failed:
            try:   # usually it has exited: say why, from its exit code and stderr
                self.proc.wait(timeout=CANCEL_GRACE_S)
            except subprocess.TimeoutExpired:
                raise _Gone(_("the agent stopped reading its input"))
            raise _Gone(_("the agent exited ({code}): {detail}", code=self.proc.returncode, detail=self.stderr()))

    def notify(self, method: str, params: dict, deadline: float | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params}, deadline)

    def request(self, method: str, params: dict, deadline: float | None = None):
        rid = self.next_id
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, deadline)
        while True:
            msg = self._next(deadline or self.deadline)
            if "method" not in msg:
                if msg.get("id") == rid:
                    if msg.get("error"):
                        e = msg["error"]
                        raise _RpcError(e.get("code"), str(e.get("message") or e.get("code")))
                    return msg.get("result") or {}
                continue   # an answer to nothing we asked: ignored
            if "id" in msg:   # the agent asks the client something
                result, error = self.on_request(msg["method"], msg.get("params") or {})
                self._send({"jsonrpc": "2.0", "id": msg["id"], **({"error": error} if error else {"result": result})},
                           deadline)
            elif msg["method"] == "session/update":
                self.on_update(msg.get("params") or {})

    def _next(self, deadline: float) -> dict:
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise _Gone("timeout")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                raise _Gone("timeout")
            if line is None:   # stdout closed: the agent is ending (a wrapper may linger; never wait past the deadline)
                try:
                    self.proc.wait(timeout=max(0.0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise _Gone("timeout")
                raise _Gone(_("the agent exited ({code}): {detail}", code=self.proc.returncode, detail=self.stderr()))
            try:
                msg = json.loads(line)
            except ValueError:
                continue   # not a protocol message (some agents log to stdout): skipped
            if isinstance(msg, dict):
                return msg

    def stderr(self) -> str:
        self.err.seek(0)
        return self.err.read().decode(errors="replace").strip()[-300:]

    def kill(self) -> None:
        """Stop the agent and everything in its group, never waiting without bound: a process that left the group
        (its own session) may still hold the pipes, so a pipe whose thread is still blocked on it is left to that
        daemon thread instead of being closed here (closing would wait for the thread)."""
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)   # the whole group: what the agent started stops too
        except (ProcessLookupError, PermissionError):
            pass
        try:
            self.proc.wait(timeout=CANCEL_GRACE_S)
        except subprocess.TimeoutExpired:
            pass
        self.reader.join(timeout=CANCEL_GRACE_S)
        pipes = ([] if self.reader.is_alive() else [self.proc.stdout]) + \
                ([] if self.writer and self.writer.is_alive() else [self.proc.stdin])
        for f in [*pipes, self.err]:
            try:
                f.close()
            except OSError:
                pass


class _Turn:
    """What the agent reports during one prompt turn."""

    def __init__(self, cwd: str):
        self.cwd = os.path.realpath(cwd)
        self.calls: dict[str, dict] = {}
        self.messages: list[list[str]] = []
        self.last_mid = None
        self.tool_since_text = True
        self.cost = None
        self.denials: list[dict] = []
        self.no_option = ""
        self.cancel = None   # set by the run: sends session/cancel
        self.held: dict[str, str] = {}    # category (mode, model) → the value the platform set; it must stay
        self.state: dict[str, str] = {}   # category → the agent's current value, from every report of it
        self.drift = ""

    def update(self, params: dict) -> None:
        u = params.get("update") or {}
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            mid, content = u.get("messageId"), u.get("content") or {}
            new = (mid != self.last_mid) if mid is not None else self.tool_since_text
            if new or not self.messages:
                self.messages.append([])
            self.last_mid, self.tool_since_text = mid, False
            if content.get("type") == "text":
                self.messages[-1].append(content.get("text", ""))
        elif kind in ("tool_call", "tool_call_update"):
            tid = u.get("toolCallId")
            if tid:
                self.calls.setdefault(tid, {}).update({k: v for k, v in u.items() if v is not None})
            if kind == "tool_call":   # a late status update does not split the message after it
                self.tool_since_text = True
        elif kind == "current_mode_update":
            self.observe(mode_id=u.get("currentModeId"))
        elif kind == "config_option_update":
            self.observe(u.get("configOptions"))
        elif kind == "usage_update" and isinstance(u.get("cost"), dict):   # cumulative; an update without it keeps it
            c = u["cost"]
            if c.get("currency") == "USD":   # another unit (credits…) is not a cost in dollars: the last USD value stays
                self.cost = float(c["amount"])

    def observe(self, options=None, mode_id=None) -> None:
        """A report of the session's settings (session/new, a set_config_option answer, an update): a held setting
        that moved away (a dependent change, a fallback to another model, the agent switching its own mode) cancels
        the turn and fails the run."""
        for o in options if isinstance(options, list) else []:
            if isinstance(o, dict) and o.get("currentValue") is not None:
                if o.get("id") is not None:   # by id: category is only a UX hint in the spec
                    self.state[f"option {o['id']}"] = o["currentValue"]
                if o.get("category") in ("mode", "model"):
                    self.state[o["category"]] = o["currentValue"]
        if mode_id is not None:
            self.state["mode"] = mode_id
        for what, want in self.held.items():
            now = self.state.get(what)
            if now is not None and now != want and not self.drift:
                self.drift = _("the agent changed its {what} from {want} to {now}; the platform cancelled the run",
                               what=what, want=want, now=now)
                if self.cancel:
                    self.cancel()

    def text(self) -> str:
        return "".join(self.messages[-1]).strip() if self.messages else ""

    def _paths(self, call: dict) -> list[str]:
        paths = [str(loc.get("path")) for loc in call.get("locations") or [] if isinstance(loc, dict) and loc.get("path")]
        paths += [str(c["path"]) for c in call.get("content") or [] if isinstance(c, dict) and c.get("type") == "diff"
                  and c.get("path")]
        # rawInput walked through lists and nested objects: a value under a key that names a place (path, files, to,
        # dest, cwd, uri…) is a target; for a read or search (no content to mistake) so is any string that looks like a
        # place (absolute, ~, $, file:, ..). A write's content is never taken for a path.
        reading = _KIND.get(call.get("kind")) in ("read", "search")

        def walk(x, key=""):
            if isinstance(x, dict):
                for k, v in x.items():
                    yield from walk(v, str(k))
            elif isinstance(x, list):
                for v in x:
                    yield from walk(v, key)
            elif isinstance(x, str) and x:
                if key in _PATH_KEYS or _PLACE_KEY.search(key) or (
                        reading and (x.startswith(("/", "~", "$", "file:")) or ".." in x.split("/"))):
                    yield x.removeprefix("file://")
        return paths + list(walk(call.get("rawInput")))

    def _inside(self, path: str) -> bool:
        if path.startswith(("~", "$")):   # the agent's tool may expand it: not judged as relative to cwd
            return False
        if ".git" in (part.casefold() for part in pathlib.PurePath(path).parts):   # the copy's repository metadata
            return False   # (casefolded: on a case-insensitive file system .GIT is .git)
        p = os.path.realpath(path if os.path.isabs(path) else os.path.join(self.cwd, path))
        try:
            return os.path.commonpath([self.cwd, p]) == self.cwd
        except ValueError:
            return False

    def permission(self, params: dict) -> dict:
        """session/request_permission → the outcome, by policy.APPROVALS["unattended"]."""
        import policy
        if self.no_option or self.drift:   # the platform has cancelled the turn: the spec's answer from now on
            return {"outcome": {"outcome": "cancelled"}}
        tc = params.get("toolCall") if isinstance(params.get("toolCall"), dict) else {}
        call = {**self.calls.get(tc.get("toolCallId"), {}), **{k: v for k, v in tc.items() if v is not None}}
        kind = _KIND.get(call.get("kind"), "other")
        rule = policy.APPROVALS["unattended"].get(kind, "deny")
        paths = self._paths(call)
        # a read or search that names no place works where it was started (cwd); a write must always name its target
        allow = rule == "allow" or (rule == "inside" and (all(self._inside(p) for p in paths) if paths
                                                          else kind in ("read", "search")))
        want = "allow_once" if allow else "reject_once"
        summary = (str(call.get("title") or "") + " " + " ".join(paths)).strip()[:200]
        if not allow:
            self.denials.append({"kind": kind, "summary": summary})
        # refusing for good is as safe as refusing once; allowing is only ever once
        kinds = (want,) if allow else ("reject_once", "reject_always")
        opt = next((o for k in kinds for o in params.get("options") or [] if isinstance(o, dict) and o.get("kind") == k
                    and o.get("optionId") is not None), None)
        if opt:
            return {"outcome": {"outcome": "selected", "optionId": opt["optionId"]}}
        # nothing to select that the rules allow (e.g. only allow_always): cancel the turn first (the spec's
        # "cancelled" answers requests pending after session/cancel), and say that it was us
        if self.cancel:
            self.cancel()
        self.no_option = _("the agent offered no {kind} option for {summary}; the platform cancelled the turn",
                           kind=want, summary=summary or kind)
        if allow:
            self.denials.append({"kind": kind, "summary": summary})
        return {"outcome": {"outcome": "cancelled"}}


def _set_mode(c, sid: str, new: dict, mode: str, turn) -> str:
    """Put the session in the configured mode and hold it there; "" when done, else why not (the run is then
    refused). A mode config option comes first (the spec: clients that use config options use them exclusively)."""
    turn.held["mode"] = mode
    oid = _option(new, "mode", mode)
    if oid is not None:
        turn.held[f"option {oid}"] = mode
        res = c.request("session/set_config_option", {"sessionId": sid, "configId": oid, "value": mode})
        turn.state["mode"] = turn.state[f"option {oid}"] = mode
        turn.observe(res.get("configOptions"))
        return ""
    if any(isinstance(o, dict) and o.get("category") == "mode" for o in new.get("configOptions") or []):
        return _("this agent offers no mode {mode} ({settings} runtime.acp mode)", mode=mode)   # options are exclusive
    modes = new.get("modes") if isinstance(new.get("modes"), dict) else {}
    if mode in [m.get("id") for m in modes.get("availableModes") or [] if isinstance(m, dict)]:
        c.request("session/set_mode", {"sessionId": sid, "modeId": mode})
        turn.observe(mode_id=mode)
        return ""
    return _("this agent offers no mode {mode} ({settings} runtime.acp mode)", mode=mode)


def _model_option(new: dict, model: str) -> str | None:
    return _option(new, "model", model)


def _option(new: dict, category: str, value: str) -> str | None:
    """The id of a select config option of this category that offers this value (grouped options flattened)."""
    for o in new.get("configOptions") or []:
        if not isinstance(o, dict) or o.get("category") != category or o.get("type", "select") != "select":
            continue
        values = []
        for x in o.get("options") or []:
            values += [y.get("value") for y in x.get("options") or []] if isinstance(x, dict) and "group" in x \
                else [x.get("value") if isinstance(x, dict) else None]
        if value in values:
            return o.get("id")
    return None


# ---------- agent (one run) ----------

def describe(task) -> list:
    conf = _conf()
    cwd = str(pathlib.Path(task.cwd or os.getcwd()).resolve())
    return _argv(list(conf["command"]), task.model, cwd) + [
        "# acp: initialize (no client capabilities)", f"# acp: session/new cwd={cwd}", "# acp: session/prompt"]


def _in_copy(task) -> tuple[str | None, str]:
    """(working directory, reason refused). A run is allowed only where the platform contains it: the throwaway
    worktree propose_apply marks (HUB_THROWAWAY_COPY). No working directory given means the process's own, checked
    the same way (callers such as the librarian set it through the subprocess)."""
    if task.extra:
        return None, _("the ACP runtime takes no extra runtime arguments ({args})", args=" ".join(map(str, task.extra)))
    import runtime_ports
    cwd = os.path.realpath(task.cwd or os.getcwd())
    marked = ((os.environ if task.env is None else task.env).get(COPY_VAR) or "")
    # the mark, the point, and the place itself: propose_apply makes its copy as <temp dir>/hub-propose-<project>-*/ws
    # (no git is run here: the platform runs git in a project's repo only as the project)
    parent = os.path.dirname(cwd)
    copy = (os.path.basename(cwd) == "ws" and os.path.basename(parent).startswith("hub-propose-")
            and os.path.dirname(parent) == os.path.realpath(tempfile.gettempdir()))
    if task.point not in runtime_ports.THROWAWAY_POINTS or not marked or os.path.realpath(marked) != cwd or not copy:
        return None, _("the ACP runtime runs only in the platform's throwaway worktree, and {cwd} is not one (the "
                       "agent decides when to ask, so nothing else would hold it to the point's limits)", cwd=cwd)
    return cwd, ""


def run(task) -> TaskResult:
    try:
        conf = _conf()
    except RuntimeError as e:
        return TaskResult(False, error=str(e))
    cwd, why = _in_copy(task)
    if cwd is None:
        return TaskResult(False, error=why)
    return _run(task, conf, cwd)


def _run(task, conf: dict, cwd: str) -> TaskResult:
    template = list(conf["command"])
    argv = _argv(template, task.model, cwd)
    env = {**(os.environ if task.env is None else task.env), **(conf.get("env") or {})}
    deadline = time.monotonic() + task.timeout_s
    try:
        c = _Client(argv, cwd, env, deadline)
    except FileNotFoundError:
        return TaskResult(False, error=_("{exe} not found", exe=argv[0]))
    except OSError as e:
        return TaskResult(False, error=_("cannot start {exe}: {e}", exe=argv[0], e=e))
    turn, sid, methods = _Turn(cwd), None, []
    c.on_update = turn.update
    c.on_request = lambda m, p: ((turn.permission(p), None) if m == "session/request_permission"
                                 else (None, {"code": METHOD_NOT_FOUND, "message": "Method not found"}))
    try:
        init = c.request("initialize", {"protocolVersion": PROTOCOL_VERSION, "clientCapabilities": {},
                                        "clientInfo": {"name": names.current("product"), "version": "1"}})
        if init.get("protocolVersion") != PROTOCOL_VERSION:
            return TaskResult(False, error=_("the agent speaks ACP protocol version {theirs}; this adapter speaks {ours}",
                                             theirs=init.get("protocolVersion"), ours=PROTOCOL_VERSION))
        methods = [m.get("id") for m in init.get("authMethods") or [] if isinstance(m, dict)]
        if conf.get("auth_method"):   # a protocol-driven login the agent does itself once asked
            offered = {m.get("id"): m for m in init.get("authMethods") or [] if isinstance(m, dict)}
            chosen = offered.get(conf["auth_method"])
            if not chosen or chosen.get("type", "agent") != "agent":
                return TaskResult(False, error=_(
                    "the configured login method {method} is not one this agent does itself (offered: {have})",
                    method=conf["auth_method"], have=", ".join(map(str, offered)) or "-"))
            c.request("authenticate", {"methodId": conf["auth_method"]})
        new = c.request("session/new", {"cwd": cwd, "mcpServers": []})
        sid = new["sessionId"]
        turn.observe(new.get("configOptions"), (new.get("modes") or {}).get("currentModeId")
                     if isinstance(new.get("modes"), dict) else None)
        if task.model and any("{model}" in p for p in template) and any(
                isinstance(o, dict) and o.get("category") == "model" for o in new.get("configOptions") or []):
            turn.held["model"] = task.model   # given in the command; the agent reports a model, so it must be this one
            turn.observe(new.get("configOptions"))
        if task.model and not any("{model}" in p for p in template):   # the model first: it may change the mode
            oid = _model_option(new, task.model)
            if oid is None:
                return TaskResult(False, session_id=sid, error=_(
                    "this agent offers no model {model} (no {{model}} in its command, and no model option has it)",
                    model=task.model))
            turn.held["model"] = turn.held[f"option {oid}"] = task.model
            res = c.request("session/set_config_option", {"sessionId": sid, "configId": oid, "value": task.model})
            turn.state["model"] = turn.state[f"option {oid}"] = task.model   # accepted; the answer's state has the last word
            turn.observe(res.get("configOptions"))
        if conf.get("mode"):
            why = _set_mode(c, sid, new, conf["mode"], turn)
            if why:
                return TaskResult(False, session_id=sid, error=why)
        if turn.drift:   # the settings did not hold before the prompt was even sent
            return TaskResult(False, session_id=sid, error=turn.drift)
        turn.cancel = lambda: c.notify("session/cancel", {"sessionId": sid})
        prompt = task.prompt + _schema_note(task.output_schema)
        res = c.request("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]})
        stop = res.get("stopReason")
        caps = (init.get("agentCapabilities") or {}).get("sessionCapabilities") or {}
        if caps.get("close") is not None:
            try:
                c.request("session/close", {"sessionId": sid}, deadline=time.monotonic() + CANCEL_GRACE_S)
            except (_RpcError, _Gone):
                pass
    except _RpcError as e:
        if e.code == AUTH_REQUIRED:
            return TaskResult(False, session_id=sid, error=_(
                "{prefix} the agent needs a login ({methods}): {detail}", prefix=LOGIN_PREFIX,
                methods=", ".join(str(m) for m in methods) or "-", detail=str(e)))
        return TaskResult(False, session_id=sid, error=_("the agent answered with an error ({code}): {detail}",
                                                         code=e.code, detail=str(e)))
    except _Gone as e:
        if str(e) == "timeout":
            if sid:
                try:
                    c.notify("session/cancel", {"sessionId": sid}, deadline=time.monotonic() + CANCEL_GRACE_S)
                    time.sleep(CANCEL_GRACE_S)
                except _Gone:
                    pass
            return TaskResult(False, session_id=sid, denials=tuple(turn.denials),   # the platform's own cancel first
                              error=turn.drift or turn.no_option or _("took longer than {secs} s", secs=task.timeout_s))
        return TaskResult(False, session_id=sid, denials=tuple(turn.denials), error=str(e))
    except (KeyError, TypeError, ValueError, AttributeError) as e:   # a message that does not follow the protocol
        return TaskResult(False, session_id=sid, denials=tuple(turn.denials),
                          error=_("the agent sent a message that does not follow ACP: {e!r}", e=e))
    finally:
        c.kill()
    if stop not in STOP_REASONS:
        return TaskResult(False, session_id=sid, denials=tuple(turn.denials),
                          error=_("the agent ended the turn with no known stop reason ({stop})", stop=stop))
    if turn.no_option or turn.drift or stop == "cancelled":
        return TaskResult(False, session_id=sid, stop=stop, denials=tuple(turn.denials),
                          error=turn.no_option or turn.drift or _("the agent cancelled the turn"))
    text = turn.text()
    so = None
    if task.output_schema is not None:
        import completion
        # the last message that holds the answer: an agent may reply, then make one more call and say "done"
        for msg in reversed(turn.messages):
            try:
                so = completion.parse_json("".join(msg).strip())
            except ValueError:
                continue
            if isinstance(so, dict):
                break
            so = None   # none at all: llm.py handles it as "no structured_output"
    return TaskResult(True, incomplete=stop in ("max_tokens", "max_turn_requests", "refusal"), text=text, structured_output=so,
                      cost_usd=turn.cost, session_id=sid, turns=None, stop=stop, denials=tuple(turn.denials))


# ---------- auth (the agent keeps its own login; optionally a key the platform hands it) ----------

def _credential() -> dict:
    try:
        return _conf().get("credential") or {}
    except RuntimeError:
        return {}


def _key(f: pathlib.Path) -> str:
    """The key file's value, "" when missing or empty (doctor and runs judge it the same way)."""
    try:
        return f.read_text().strip() if f.is_file() else ""
    except OSError:
        return ""


def clean_env(env) -> dict:
    var = _credential().get("env")
    return {k: v for k, v in env.items() if k != var}


def auth_env(mode: str, env) -> tuple:
    out, cred = clean_env(env), _credential()
    if mode != "token" or not cred.get("env"):
        return out, ""   # the agent's own login
    import hubconf
    f = hubconf.secrets_dir() / cred.get("file", "")
    value = _key(f) if cred.get("file") else ""
    if not value:
        return None, _("the ACP agent's key file {path} is missing or empty ({settings} runtime.acp credential)", path=f)
    out[cred["env"]] = value
    return out, ""


def credentials() -> list:
    try:
        conf = _conf()
    except RuntimeError:
        return []
    cred, renew = conf.get("credential") or {}, conf.get("login") or _("log the agent in with its own command")
    if cred.get("env"):
        import hubconf
        f = hubconf.secrets_dir() / cred.get("file", "")
        return [Credential(name=f"acp {conf['name']}", label=_("ACP agent {name} (key file)", name=conf["name"]),
                           mode="token", present=_key(f) != "", renew=renew, path=str(f))]
    return [Credential(name=f"acp {conf['name']}", label=_("ACP agent {name} (its own login)", name=conf["name"]),
                       mode="token", present=True, renew=renew,
                       note=_("the agent keeps its own login; only a real request confirms it"))]


# ---------- settings check (config-check calls it) ----------

def warnings(cfg: dict) -> list:
    """(text, hint) pairs doctor shows about an ACP setup that is allowed but weak or has no effect."""
    acp = (cfg.get("runtime") or {}).get("acp") or {}
    agent = (acp.get("agents") or {}).get(acp.get("agent")) or {}
    pins = [pt for pt, pin in ((cfg.get("llm") or {}).get("points") or {}).items()
            if isinstance(pin, dict) and pin.get("runtime") == "acp"]
    out = []
    if pins and agent and not agent.get("mode"):
        out.append((_("the ACP agent {name} has no mode set: what it writes outside the throwaway copy by absolute "
                      "path or through a shell is held only by its own default mode", name=acp.get("agent")),
                    _("set runtime.acp.agents.<agent>.mode to a mode that asks before running commands")))
    if "mailbox-propose" in pins and (cfg.get("mailbox") or {}).get("handler") != "propose":
        out.append((_("mailbox-propose is pinned to acp, but mailbox.handler is not propose, so it never runs"),
                    _("set {settings} mailbox.handler to propose")))
    return out


def config_errors(cfg: dict) -> list:
    """Problems in hub.json runtime.acp that would otherwise show only at run time, or silently."""
    import completion
    acp = (cfg.get("runtime") or {}).get("acp")
    if acp is None:
        used = (cfg.get("runtime") or {}).get("default") == "acp" or any(
            isinstance(pin, dict) and pin.get("runtime") == "acp"
            for pin in ((cfg.get("llm") or {}).get("points") or {}).values())
        return [_("the acp runtime is used but {settings} runtime.acp is not set up")] if used else []
    errs, agents = [], acp.get("agents") or {}
    if acp.get("agent") not in agents:
        errs.append(_("runtime.acp.agent `{name}` is not an entry of runtime.acp.agents (have: {have})",
                      name=acp.get("agent"), have=", ".join(agents) or "-"))
    for name, a in agents.items():
        cred = ((a or {}).get("credential") or {}).get("env")
        for k, v in ((a or {}).get("env") or {}).items():
            if k == "PATH" or k.startswith(("HUB_", "LLM_")) or k == cred:
                errs.append(_("runtime.acp.agents.{name}.env.{key}: the platform sets this variable", name=name, key=k))
            elif completion.KEYLIKE.search(str(v)):
                errs.append(_("runtime.acp.agents.{name}.env.{key} looks like a key value: use credential (a file in the "
                              "secrets directory)", name=name, key=k))
    return errs


def is_login_error(text: str) -> bool:
    return (text or "").startswith(LOGIN_PREFIX)   # only this adapter's own wording (the rest may quote the agent)
