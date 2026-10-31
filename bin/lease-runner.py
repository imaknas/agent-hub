#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The channel-lease runner: the assistant "lends" a conversation thread to a project's Claude Code session; this
opens, resumes, relays and reclaims it.

Listens only on 127.0.0.1 (default 18797); every request needs `Authorization: Bearer <runner-token in the platform's
credentials directory (hubconf.secrets_dir())>`.
Contract v2 (contracts/lease-runner.v2.schema.json; docs/design/lease-contract-neutral.md): independent of the
runtime: actions use kind, quota uses quota.windows, the error code is agent_login. v1 was deleted on 10-25 once the
assistant had switched over (the schema is in contracts/retired/); events stored before the change are sent as they are.
How Claude Code's output becomes neutral events is in the runtime's parse_stream (rt()).
API:
  POST /v1/leases                     {v, project, mode: discuss, session_id, thread, resume?, max_turns?} → 201
  POST /v1/leases/<id>/messages       {v, text, client_msg_id, attachments?} → 202 (a client_msg_id is sent only once;
                                      a stopped process is resumed automatically)
                                      attachments: [{name, media_type, data(base64)}], at most 8, each ≤ 10 MB; stored
                                      in the project's attachments/<time>-<name> (not in git), images also shown to the
                                      model directly; with attachments, text may be an empty string
  GET  /v1/leases/<id>/events         SSE; replays what came after Last-Event-ID
  POST /v1/leases/<id>/warned         the assistant confirms the "still continuing?" push really went out (reclaimed
                                      only after 10 minutes with no reply from then)
  POST /v1/leases/<id>/keepalive      answers "continue"
  POST /v1/leases/<id>/terminal       L3: stop the process, return {command, hint} (the runtime's resume_command)
  POST /v1/leases/<id>/detach         {v, reason} → 202; the handover runs in the background, the result comes as
                                      events
  GET  /v1/leases, GET /v1/status, GET /v1/policy (the domains that can't get allow_session, hub.json
                                      lease.no_session_domains)
  POST /v1/leases/<id>/mode           {v, mode: discuss|work} → 200; 409 busy while answering; work only for idea
                                      sandboxes (see work_ready)
  POST /v1/sandboxes                  {v, name: <the idea's code name>, purpose} → 201 created / 200 same name and
                                      purpose already exists (a resend)
                                      / 400 bad_name / 409 exists / 429 too_many (with limit, current)
                                      / 500 create_failed
                                      idea sandboxes: created by bin/create-idea-sandbox.py
                                      (docs/design/idea-sandboxes.md), attachable as soon as they exist
  POST /v1/projects/<name>/rename     {v, to} → 200 {name, origin, renamed} (a retry of a half-done rename returns
                                      renamed:false) / 400 bad_name
                                      / 404 not_found / 409 busy|name_taken / 500 rename_failed = bin/project-rename.py
                                      (GitHub repo, registration, memory, folder); leases are handled the same way
                                      as for an upgrade (quiesce, follow_move)
  POST /v1/sandboxes/<name>/upgrade   {v, rename?} → 200 {name, origin, upgraded} (an already-upgraded one returns
                                      upgraded:false + the current name)
                                      / 404 not_found / 409 not_idea|dirty|name_taken / 500 upgrade_failed
                                      = bin/upgrade-idea.py --apply --json (GitHub repo private by default; the
                                      assistant sends it only after asking the owner)
  POST /v1/internal/<id>/relay        permission relay report (audit + waiting doesn't count as idle)
  POST /v1/internal/<id>/show         {path, summary?}: show the user a file in the project (lease_files.py checks and
                                      copies it) → v2's file event
  GET  /v1/leases/<id>/files/<file id> fetch that file (the copy, kept 30 days); design docs/design/lease-show-file.md
One long-running process per lease: `llm.py exec lease-session` (declared in contracts/llm-points.yaml) +
`--session-id`/`--resume`, only the permission-relay MCP (--strict-mcp-config), `--settings` (the second stage's deny
list, whether or not the project has enforce on), `--append-system-prompt` (the lease instructions), `--max-turns`;
cwd = the registry's workspace for the project, with HUBBUS_LEASE=1 in the environment (the mailbox hook shows it
letters without marking them delivered).
Idle: the process stops after 10 minutes with nothing to do (the lease remains; the next message resumes it with
--resume); after 30 minutes with nothing, the lease sends idle_warning, and only if there's still no reply 10 minutes
after the assistant reports warned is it reclaimed + handed over; time spent waiting for a permission/question answer
doesn't count as idle.
Reclaim: stop the process (busy if a terminal still has it open) → `llm.py run lease-handover -- --resume <id>`
(retried once if invalid, then a minimal handover) → bus session.report + the project mailbox + progress/task/question
to the owner (fixed ids, a resend doesn't duplicate) + notes written into memory → run hubbus bridge once right away.
"""
from __future__ import annotations

import datetime
import http.server
import importlib.util
import json
import os
import pathlib
import re
import secrets as pysecrets
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import runtime_ports  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)
_c = i18n.domain(__file__, content=True)   # the note written into the project's memory: content, read by the librarian


def rt():
    """The runtime this runner uses (runtime_ports; for now always hub.json's runtime.default). All of Claude Code's
    details are inside it."""
    return runtime_ports.load()

HUB = hubconf.ROOT
HOST, PORT = "127.0.0.1", int(os.environ.get("LEASE_RUNNER_PORT", "18797"))
DIR = pathlib.Path(os.environ.get("LEASE_RUNNER_DIR") or hubconf.STATE / "lease-runner")  # another directory for tests
TOKEN_FILE = hubconf.secrets_dir() / "runner-token"
PY = sys.executable
GLOBAL_CAP = 3
PROC_IDLE_S = int(os.environ.get("LEASE_PROC_IDLE_S", 600))     # how long a process may idle before it's stopped
LEASE_IDLE_S = int(os.environ.get("LEASE_IDLE_S", 1800))        # how long a lease may idle before idle_warning is sent
WARN_GRACE_S = int(os.environ.get("LEASE_WARN_GRACE_S", 600))   # how long with no reply after warned before reclaiming
IDLE_TICK_S = int(os.environ.get("LEASE_IDLE_TICK_S", 20))
DEFAULT_MAX_TURNS = 200
# The lease session's instructions (appended to its system prompt), English templates filled by prompts.fill when the
# session starts: the names configured, replies in the content language
PREAMBLE = """This is a channel lease, a discussion: {{owner}} is talking to you (this project's session) on a phone,
through {{assistant}}, {{owner}}'s assistant.
- This is a read-only discussion (discuss): you can read files, search and look things up, but not change files or run
  commands. What needs changing goes into the final handover, for {{owner}} to decide.
- When {{owner}} should choose between a few options, use the question tool (AskUserQuestion); {{owner}} answers on the
  phone. Without an answer, ask in text.
- Reading a website that is not on the allowed list asks {{owner}} first; if that is refused, don't retry the same URL.
- Reply in {{content_language}}, briefly, in a form that reads well on a phone. When the lease is detached, the system
  asks you for a structured handover (summary, decisions, open questions, what {{owner}} has to do, memory notes)."""

PREAMBLE_WORK = """This is a channel lease, a discussion, and {{owner}} has allowed you to work in this project (work):
{{owner}} is talking to you on a phone, through {{assistant}}, {{owner}}'s assistant.
- You can read files, search and look things up, and you can change files in this project's folder directly (without
  asking); **every command (Bash) is first sent to {{owner}}'s phone for approval**, so run one clear thing at a time
  and first say in a sentence or two what you will run and why. If one is refused, don't retry the same thing; ask in
  text.
- You may only touch this project's folder: writes outside it are refused outright, and commands run in a sandbox (no
  writing elsewhere, network only to the allowed list).
- When {{owner}} should choose between a few options, use the question tool (AskUserQuestion). Reply in
  {{content_language}}, briefly, in a form that reads well on a phone.
- When the lease is detached, the system asks you for a structured handover (summary, decisions, open questions, what
  {{owner}} has to do, memory notes)."""

HINT_SHOW_FILE = """
- To show {{owner}} a picture or a file from the project (png, jpg, pdf, glb, md, csv…), use the show_file tool (a path
  in the project folder + one sentence saying what it is): {{owner}} sees it right on the phone. Don't ask {{owner}} to
  open it on a computer; one at a time; files that are too large (20 MB or more) or look like credentials are not
  sent."""

HINT_CAPABILITIES = """
- This project can use capabilities the platform runs for it: {{capabilities}}. A program that needs the GPU (e.g.
  Blender) is blocked by the sandbox as a plain command; run it with `{{hub_run}} --cap gpu -- <command…>` instead (in
  the project folder; it is still a command {{owner}} approves). What the platform runs can only write the project
  folder and has no network. The output appears all at once at the end; if the command times out, the work goes on,
  and `{{hub_run}} --wait <id>` picks it up again."""

HINT_SERVICES = """
- Anything that should keep running or needs a port: don't give {{owner}} commands to run it. Put it in the handover;
  the project's session declares it under services: in its registry entry (a manifest.update to agent-hub, internal
  to the platform, no owner approval needed) and the platform runs it as a service."""

HINT_IDEA = """
- This project is an idea sandbox: {{assistant}} opened it to explore one of {{owner}}'s ideas. The idea: "{{purpose}}".
  The goal is to help {{owner}} get this idea clear (is it feasible, what does it need, the risks, the next step); the
  conclusions and decisions go into the handover notes."""

_lock = threading.RLock()
LEASES: dict[str, "Lease"] = {}


def now() -> datetime.datetime:
    return datetime.datetime.now().astimezone().replace(microsecond=0)


def iso(t: datetime.datetime | None = None) -> str:
    return (t or now()).isoformat()


def log(msg: str) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    with open(DIR / "runner.log", "a") as f:
        f.write(f"[{now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def handovers_logged(lease_id: str) -> int:
    """How many times this lease has been handed over, from the event log (events.jsonl)."""
    f = DIR / lease_id / "events.jsonl"
    try:
        return sum(1 for line in f.open() if '"type": "handover"' in line)
    except OSError:
        return 0


class Lease:
    def __init__(self, d: dict):
        self.id = d["lease_id"]
        self.project, self.mode, self.thread = d["project"], d.get("mode", "discuss"), d.get("thread", "")
        self.cwd = d["cwd"]
        self.state = d.get("state", "attaching")
        self.started_once = d.get("started_once", False)
        self.max_turns = d.get("max_turns", DEFAULT_MAX_TURNS)
        self.last_activity = d.get("last_activity") or iso()
        self.idle_warned_at = d.get("idle_warned_at")
        self.warned_at = d.get("warned_at")
        self.seq = d.get("seq", 0)
        self.crashes = 0
        self.waiting = 0
        self.in_turn = False
        self.intent_stop = False
        self.proc: subprocess.Popen | None = None
        self.seen_msgs: list[str] = d.get("seen_msgs", [])[-200:]
        # how many times this lease has been handed over: the message ids the handover turns into carry it (the
        # same id resumed and reclaimed again doesn't collide). Never lower than the handover count in the event log:
        # records from before the change lack this field, and counting from 0, the ids used for replays
        # (lease-<id>-<n>-…) would later collide with the real n-th handover
        self.handovers = max(int(d.get("handovers") or 0), handovers_logged(self.id))
        self.recent: list[dict] = []          # recent events (text_delta included), for SSE replays
        self.cond = threading.Condition(_lock)

    def dump(self) -> dict:
        return {"lease_id": self.id, "project": self.project, "mode": self.mode, "thread": self.thread, "cwd": self.cwd,
                "state": self.state, "started_once": self.started_once, "max_turns": self.max_turns,
                "last_activity": self.last_activity, "idle_warned_at": self.idle_warned_at, "warned_at": self.warned_at,
                "seq": self.seq, "seen_msgs": self.seen_msgs[-200:], "handovers": self.handovers}

    def public(self) -> dict:
        return {"lease_id": self.id, "project": self.project, "mode": self.mode, "thread": self.thread,
                "state": self.state, "last_activity": self.last_activity, "waiting": self.waiting > 0,
                "resumable": rt().resumable(self.id)}

    @property
    def dir(self) -> pathlib.Path:
        return DIR / self.id

    def emit(self, etype: str, **data) -> dict:
        """Contract v2 events. Fields starting with `_` are internal signals: not stored, not sent."""
        with _lock:
            self.seq += 1
            ev = {"v": 2, "seq": self.seq, "lease_id": self.id, "ts": iso(), "type": etype,
                  **{k: v for k, v in data.items() if not k.startswith("_")}}
            self.recent.append(ev)
            del self.recent[:-3000]
            # token-by-token deltas aren't stored (large; a reconnect replay has the full assistant event)
            if etype != "text_delta":
                self.dir.mkdir(parents=True, exist_ok=True)
                with open(self.dir / "events.jsonl", "a") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            self.cond.notify_all()
            save()
        return ev

    def set_state(self, st: str, **extra) -> None:
        self.state = st
        self.emit("state", state=st, **extra)

    def touch(self) -> None:
        self.last_activity = iso()
        self.idle_warned_at = self.warned_at = None


def render(ev: dict) -> dict:
    """A stored event → what's sent (internal fields removed). Events stored before the change (v: 1) are sent as they
    are."""
    return {k: v for k, v in ev.items() if k != "_v1"}


def save() -> None:
    with _lock:
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = DIR / "leases.json.tmp"
        tmp.write_text(json.dumps({lid: l.dump() for lid, l in LEASES.items()}, ensure_ascii=False, indent=1))
        tmp.replace(DIR / "leases.json")


def load() -> None:
    f = DIR / "leases.json"
    if not f.exists():
        return
    for lid, d in json.loads(f.read_text()).items():
        lease = Lease(d)
        if lease.state in ("running", "resuming", "attaching", "crashed"):
            lease.state = "idle_stopped"  # runner restarted: children gone; the next message resumes with --resume
        LEASES[lid] = lease


# ---------- settings: deny list, MCP, domains ----------

def deny_settings(project: str) -> pathlib.Path:
    """The deny from the same policy as render-project-settings (whether or not the project has enforce on) +
    user-level credentials (runtime_claude.lease_settings)."""
    import policy
    import runtime_claude
    p = DIR / project / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(runtime_claude.lease_settings(policy.for_project(project)), ensure_ascii=False))
    return p


def mcp_config(lease: Lease) -> pathlib.Path:
    # the lease's MCP settings are still in Claude Code's format (to move into the runtime with the long-running
    # session layer)
    import runtime_claude
    p = lease.dir / "mcp.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"mcpServers": {runtime_claude.RELAY_SERVER: {
        "command": PY, "args": [str(HUB / "bin" / "lease-relay.py")],
        "env": {"LEASE_ID": lease.id, "LEASE_PROJECT": lease.project, "LEASE_RUNNER": f"http://{HOST}:{PORT}"}}}}))
    return p


def allow_rules(project: str) -> list[str]:
    doc = hubconf.projects().get(project) or {}
    return [f"WebFetch(domain:{d})" for d in (doc.get("network") or [])]


def env_for(lease: Lease) -> dict:
    env = rt().clean_env(os.environ)  # credentials are decided by llm.py exec (the runtime's auth_env)
    env.update(HUBBUS_LEASE="1", LEASE_ALLOW=json.dumps(allow_rules(lease.project)),
               # permission relay: writes allowed (asking the owner) in work mode only
               LEASE_MODE=lease.mode, LEASE_WS=lease.cwd,
               PATH=os.environ.get("LEASE_PATH_PREFIX", "") +  # for tests: put the fake claude first
                    f"{hubconf.LAUNCHD_PATH}:{pathlib.Path.home()}/.local/bin:/usr/sbin:/sbin")
    return env


# ---------- processes ----------

def work_ready(project: str) -> tuple[bool, str]:
    """Work mode (the second stage) is open only to projects whose registry has enforce.work: true (explicitly
    authorized by the owner; written when an idea sandbox is created and carried over on upgrade, 10-25), and only if
    the OS sandbox really has been written into that project's settings: the registry's
    enforce.sandbox is only an intention, the settings.local.json render-project-settings writes is the fact (in the
    window after a sandbox is created and before its settings are rendered, work must not run without a sandbox)."""
    doc = hubconf.projects().get(project) or {}
    if not (doc.get("enforce") or {}).get("work"):
        return False, _("{project} has no work mode (enforce.work in its registry entry; added only with the owner's consent)",
                         project=project)
    import runtime_claude
    return runtime_claude.sandbox_rendered(hubconf.workspace(project))


def work_projects() -> list[str]:
    return sorted(n for n in hubconf.projects() if work_ready(n)[0])


def preamble(project: str, mode: str = "discuss") -> str:
    """The lease instructions (discuss is read-only / work can make changes, asking for every write); idea sandboxes
    (registry type: idea) get an extra section: which idea is being explored."""
    import prompts
    doc = hubconf.projects().get(project) or {}
    caps = (doc.get("enforce") or {}).get("capabilities") or []
    text = (PREAMBLE_WORK if mode == "work" else PREAMBLE) + HINT_SHOW_FILE
    # the platform's run service (docs/design/capabilities.md): what the sandbox blocks (e.g. the GPU) goes through it
    if mode == "work" and caps:
        text += HINT_CAPABILITIES
    if mode == "work":
        text += HINT_SERVICES
    if doc.get("type") == "idea":
        text += HINT_IDEA
    return prompts.fill(text, capabilities=", ".join(caps), hub_run=hubconf.ROOT / "bin" / "hub-run",
                        purpose=doc.get("purpose", ""))


def start_proc(lease: Lease, resume: bool) -> None:
    # resuming presumes the transcript exists: Claude Code writes it only after the first turn finishes. Switching
    # mode, an idle stop or a runner restart before anything was said would crash --resume with "No conversation
    # found" (the owner hit it on 10-25, lease 34c84c53)
    import runtime_claude  # the flags are still Claude Code's (to move into the runtime with the long-running sessions)
    wanted = resume or lease.started_once
    can_resume = wanted and rt().resumable(lease.id)
    flag = ["--resume", lease.id] if can_resume else ["--session-id", lease.id]
    point = "lease-work" if lease.mode == "work" else "lease-session"
    cmd = [PY, str(HUB / "bin" / "llm.py"), "exec", point, "--cwd", lease.cwd, "--",
           *flag, "--strict-mcp-config", "--mcp-config", str(mcp_config(lease)),
           "--permission-prompt-tool", runtime_claude.APPROVE_TOOL,
           "--settings", str(deny_settings(lease.project)), "--append-system-prompt", preamble(lease.project, lease.mode),
           "--max-turns", str(lease.max_turns)]
    lease.dir.mkdir(parents=True, exist_ok=True)
    err = open(lease.dir / "stderr.log", "a")
    lease.proc = subprocess.Popen(cmd, cwd=lease.cwd, env=env_for(lease), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=err, text=True, bufsize=1)
    # started_once is set only once the first turn really finishes (reader gets a result with num_turns > 0)
    lease.intent_stop = False
    if wanted and not can_resume:  # wanted to resume but can't (no record): say so rather than silently start a new one
        lease.set_state("running", pid=lease.proc.pid, resumed=False,
                        detail=_("The earlier conversation is gone (nothing was said yet, or it is past retention); "
                                  "starting a new conversation"))
    else:
        lease.set_state("running", pid=lease.proc.pid, resumed=can_resume)
    threading.Thread(target=reader, args=(lease, lease.proc), daemon=True).start()


def stop_proc(lease: Lease, new_state: str) -> None:
    p = lease.proc
    lease.intent_stop = True
    if p and p.poll() is None:
        try:
            p.stdin.close()
            p.wait(timeout=15)
        except Exception:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    lease.proc = None
    lease.in_turn = False
    lease.set_state(new_state)


def reader(lease: Lease, proc: subprocess.Popen) -> None:
    """The runtime's native output → neutral conversation events (rt().parse_stream) → sent; updates the lease's state
    when a turn ends."""
    runtime = rt()
    for line in proc.stdout:
        events = runtime.parse_stream(line)
        done = [e for e in events if e.type == "turn_end" and "num_turns" in e.data]
        if done:
            with _lock:
                lease.in_turn = False
                # a process that fails at start also emits a result with is_error and num_turns=0 before exiting: that
                # isn't a success and mustn't reset crashes to zero (or it never reaches 2, is never reclaimed
                # automatically, and every message crashes it again)
                if not (done[0].data.get("is_error") and not done[0].data.get("num_turns")):
                    lease.crashes = 0
                if done[0].data.get("num_turns"):
                    lease.started_once = True  # the transcript has been written; it can be resumed from now on
                lease.touch()
        for e in events:
            if e.type == "quota":
                STATUS["quota"] = {**e.data, "at": iso()}
            lease.emit(e.type, **e.data)
            if e.data.get("_detach"):
                threading.Thread(target=detach, args=(lease, e.data["_detach"]), daemon=True).start()
    proc.wait()
    with _lock:
        if lease.proc is not proc or lease.intent_stop:
            return
        lease.proc, lease.in_turn = None, False
        lease.crashes += 1
        tail = (lease.dir / "stderr.log").read_text(errors="replace")[-400:] if (lease.dir / "stderr.log").exists() else ""
        e = rt().classify_exit(proc.returncode, tail)
        lease.emit(e.type, **e.data)
        if lease.crashes >= 2:
            threading.Thread(target=detach, args=(lease, "crash"), daemon=True).start()
        else:
            lease.set_state("idle_stopped")  # the next message resumes once with --resume automatically


MAX_ATTACHMENTS, MAX_ATTACHMENT_BYTES, MAX_BODY_BYTES = 8, 10 * 1024 * 1024, 100 * 1024 * 1024
IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def check_attachments(atts) -> str | None:
    """Invalid → return the reason (400 bad_request)."""
    import base64
    if atts is None:
        return None
    if not isinstance(atts, list) or len(atts) > MAX_ATTACHMENTS:
        return _("attachments must be a list of at most {n}", n=MAX_ATTACHMENTS)
    for a in atts:
        if not isinstance(a, dict) or not a.get("name") or not a.get("media_type") or not isinstance(a.get("data"), str):
            return _("each attachment needs name, media_type, data (base64)")
        try:
            raw = base64.b64decode(a["data"], validate=True)
        except ValueError:
            return _("{name}: data is not valid base64", name=a["name"])
        if len(raw) > MAX_ATTACHMENT_BYTES:
            return _("{name}: over {mb} MB", name=a["name"], mb=MAX_ATTACHMENT_BYTES // 1024 // 1024)
    return None


def save_attachments(lease: Lease, atts: list) -> list[str]:
    """Store into the project folder's attachments/ (cleaned file name, never overwriting) and return the relative path.
    attachments/ is written into .git/info/exclude: photos stay out of the repo history (idea sandboxes have no remote,
    but things other than memory shouldn't be committed either)."""
    import base64
    ws = pathlib.Path(lease.cwd)
    d = ws / "attachments"
    d.mkdir(exist_ok=True)
    r = subprocess.run(["git", "-C", str(ws), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        ex = pathlib.Path(r.stdout.strip()) / "info" / "exclude"
        ex.parent.mkdir(parents=True, exist_ok=True)
        lines = ex.read_text().splitlines() if ex.exists() else []
        if "attachments/" not in lines:
            ex.write_text("\n".join(lines + ["attachments/"]) + "\n")
    stamp, out = f"{now():%Y%m%d-%H%M%S}", []
    for a in atts:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", pathlib.Path(a["name"]).name).strip("._") or "file"
        f, i = d / f"{stamp}-{name[:80]}", 1
        while f.exists():
            f, i = d / f"{stamp}-{i}-{name[:80]}", i + 1
        f.write_bytes(base64.b64decode(a["data"]))
        out.append(str(f.relative_to(ws)))
    return out


def send_message(lease: Lease, text: str, attachments: list | None = None) -> None:
    content: str | list = text
    if attachments:
        paths = save_attachments(lease, attachments)
        note = _("Attachments saved to: {paths}", paths=_(", ").join(paths))
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": a["media_type"], "data": a["data"]}}
                  for a in attachments if a["media_type"] in IMAGE_TYPES]
        content = blocks + [{"type": "text", "text": (text + "\n\n" if text else "") + note}]
        lease.emit("attachments", paths=paths)
    with _lock:
        if lease.proc is None or lease.proc.poll() is not None:
            lease.set_state("resuming")
            start_proc(lease, resume=True)
        lease.touch()
        lease.in_turn = True
        msg = json.dumps({"type": "user", "message": {"role": "user", "content": content}}, ensure_ascii=False)
        lease.proc.stdin.write(msg + "\n")
        lease.proc.stdin.flush()


# ---------- reclaim and handover ----------

def terminal_open(lease: Lease) -> bool:
    """Does a terminal (lease-resume) still have this session open: the transcript was written in the last 2 minutes,
    or a process has it open."""
    slug = re.sub(r"[/.]", "-", str(pathlib.Path(lease.cwd).resolve()))
    tr = pathlib.Path.home() / ".claude" / "projects" / slug / f"{lease.id}.jsonl"
    if tr.exists() and time.time() - tr.stat().st_mtime < 120:
        return True
    r = subprocess.run(["/usr/sbin/lsof", "-t", str(tr)], capture_output=True, text=True) if tr.exists() else None
    return bool(r and r.stdout.strip())


def empty_handover() -> dict:
    return {"summary": _("This lease never started a conversation (the session did not start, or it was detached before "
                         "anything was said); there is nothing to hand over."),
            "decisions": [], "open_questions": [], "tasks_for_owner": [], "notes_markdown": ""}


def handover_prompt(earlier: list[dict] | None = None, **names) -> str:
    """judgment point lease-handover's prompt, said to the resumed session (templates/prompts/lease-handover.md, in
    English; the handover is written in the content language). names: owner= / lang= pinned by an eval. With earlier
    items (earlier_tasks) it also lists them (templates/prompts/lease-handover-earlier.md), so the handover can name
    them by ref instead of raising them again."""
    import prompts
    text = prompts.render("lease-handover", **names)
    if earlier:
        items = "\n".join(f"- {e['ref']}: {' '.join(e['text'].split())}" for e in earlier)
        text += "\n\n" + prompts.render("lease-handover-earlier", items=items, **names)
    return text


def earlier_tasks(project: str) -> list[dict]:
    """The project's to-dos still waiting on the owner (Acts items raised, not settled, not withdrawn), as the handover
    sees them: [{ref: T1…, key, text, bound: raised by the registrar from a STATE.md line}]. Every handover used to
    raise each still-open task again under a new key (lease-<id>-<n>-task-<i>), leaving the assistant to tell which
    were the same; now it names the earlier item instead. [] when the register cannot be read (logged)."""
    try:
        spec = importlib.util.spec_from_file_location("registrar_lease", HUB / "bin" / "registrar.py")
        reg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reg)
        led = reg.Ledger(project)
        return [{"ref": f"T{i}", "key": k, "text": led.terms(k)[0] or "", "bound": led.bound(k)}
                for i, k in enumerate(led.open(), 1)]
    except (Exception, SystemExit) as e:
        log(_("{project}: the earlier to-dos could not be read, the handover goes without them: {e!r}", project=project,
              e=e))
        return []


def run_handover(lease: Lease, earlier: list[dict] | None = None) -> tuple[dict | None, str]:
    """(handover, reason for failure). No transcript = nothing was said: return the fixed "no content", not a failure
    (10-25 lease 34c84c53: the session crashed right at the start, and the handover used --resume on a conversation
    that didn't exist, which was taken as "the model didn't write a valid handover")."""
    if not rt().resumable(lease.id):
        return empty_handover(), ""
    cmd = [PY, str(HUB / "bin" / "llm.py"), "run", "lease-handover", "--cwd", lease.cwd, "--",
           "--resume", lease.id, "--strict-mcp-config", "--settings", str(deny_settings(lease.project))]
    why = ""
    for attempt in (1, 2):  # retry once if invalid
        r = subprocess.run(cmd, input=handover_prompt(earlier), cwd=lease.cwd, env=env_for(lease), capture_output=True, text=True,
                           timeout=900)
        try:
            out = json.loads(r.stdout)
        except ValueError:
            out = {}
        so = out.get("structured_output")
        if r.returncode == 0 and isinstance(so, dict):
            return so, ""
        why = str(out.get("llm_error") or out.get("result") or r.stderr.strip()[-300:] or f"exit {r.returncode}")[:300]
        log(_("{id} handover attempt {n} is not valid: {why}", id=lease.id, n=attempt, why=why))
    return None, why


def hubbus(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), *args], capture_output=True, text=True)


def handover_id(sid: str, n: int, part: str) -> str:
    """The message ids a handover turns into: lease-<lease>-<handover number>-<part>. 10-26 m10262322: resuming and
    reclaiming the same lease keeps the lease id, and numbering used to start from 0 each time
    (lease-<id>-task-0…), so from the second handover on the colliding ones were all swallowed as resends (by both the
    bus and the assistant).
    Old-format ids are unchanged; the new format has an extra count segment, so it can't collide with the old."""
    return f"lease-{sid}-{n}-{part}"


def deliver(lease: Lease, h: dict, n: int, earlier: list[dict] | None = None) -> None:
    sid, proj, owner = lease.id, lease.project, hubconf.owner_address()
    keys, byref = [], {e["ref"]: e for e in earlier or []}
    unused = dict(byref)
    for i, t in enumerate(h.get("tasks_for_owner") or []):
        ref = t.pop("ref", None)
        e = unused.pop(ref, None) if ref else None
        if ref and e is None:
            log(_("{id}: handover task ref {ref} is not an earlier item (or is used twice); sent as a new task", id=sid,
                  ref=ref))
        if e is not None and e["bound"]:
            t["key"] = e["key"]   # a STATE.md line: its wording changes there (the registrar amends from the line)
            keys.append(e["key"])
            continue
        # an earlier item keeps its key: hubbus sends amended when the wording changed, nothing when it did not
        key = e["key"] if e is not None else handover_id(sid, n, f"task-{i}")
        args = ["send", "--from", proj, "--to", owner, "--type", "task", "--id", key, "--text", t["text"],
                "--context", f"lease:{sid}"]
        if t.get("due"):
            args += ["--due", t["due"]]
        hubbus(*args)
        t["key"] = key
        keys.append(key)
    for i, x in enumerate(h.get("ended") or []):
        e = byref.get(x.pop("ref", None))
        if e is None:
            log(_("{id}: handover ended item is not an earlier item; ignored", id=sid))
            continue
        x["key"] = e["key"]
        if x.get("outcome") == "done":   # heard in the conversation: a claim, the assistant settles it
            hubbus("send", "--from", proj, "--to", owner, "--type", "done", "--task", e["key"], "--id",
                   handover_id(sid, n, f"done-{i}"), "--text", x.get("basis") or "", "--context", f"lease:{sid}")
        elif not e["bound"]:   # no longer needed: withdrawn (a STATE.md line is withdrawn by editing the line)
            hubbus("send", "--from", proj, "--to", owner, "--type", "drop", "--task", e["key"], "--id",
                   handover_id(sid, n, f"drop-{i}"), "--text", x.get("basis") or "", "--context", f"lease:{sid}")
    h["ended"] = [x for x in h.get("ended") or [] if "key" in x]
    for i, q in enumerate(h.get("open_questions") or []):
        hubbus("send", "--from", proj, "--to", owner, "--type", "question", "--id", handover_id(sid, n, f"q-{i}"), "--text", q,
               "--context", f"lease:{sid}")
    hubbus("send", "--from", proj, "--to", owner, "--type", "progress", "--id", handover_id(sid, n, "progress"),
           "--text", _("[{project} lease detached] {summary}", project=proj, summary=h.get("summary", "")),
           "--context", f"lease:{sid}")
    body = [_("Handover from the detached lease ({id}):", id=sid[:8]), h.get("summary", "")]
    body += [_("- Decision: {what} ({why})", what=d.get("what"), why=d.get("why", "")) for d in h.get("decisions") or []]
    body += [_("- Open question: {q}", q=q) for q in h.get("open_questions") or []]
    hubbus("send", "--from", hubconf.hub_project(), "--to", proj, "--type", "info", "--id", handover_id(sid, n, "report"),
           "--text", "\n".join(body))
    # notes written into the project's memory (registered in managed-files); the task keys sent are attached, so the
    # librarian doesn't send the same thing again
    h["notes_written"] = False
    notes = (h.get("notes_markdown") or "").strip()
    mem = pathlib.Path(lease.cwd) / ".agent-memory" / "memory"
    if notes and mem.is_dir():
        f = mem / f"{now():%Y-%m-%d}.md"
        extra = ("\n\n" + _c("Tasks registered for {owner}: {keys}", owner=hubconf.owner_tag(),
                                 keys=_c(", ").join(keys))) if keys else ""
        with open(f, "a") as fh:
            fh.write("\n" + _c("## Lease conversation {time} (session {session})", time=f"{now():%H:%M}", session=sid)
                     + f"\n\n{notes}{extra}\n")
        h["notes_written"] = True
    hubbus("publish", "session.report", "--source", proj, "--to", proj, "--id", handover_id(sid, n, "handover"),
           "--payload", json.dumps(h, ensure_ascii=False))
    subprocess.Popen([sys.executable, str(HUB / "bin" / "hubbus"), "bridge"], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)  # send right away, don't wait 15 minutes


def detach(lease: Lease, reason: str) -> None:
    with _lock:
        if lease.state in ("detaching", "detached"):
            return
        prev = lease.state
        if prev == "terminal" and terminal_open(lease):
            lease.emit("error", error="busy", detail=_("terminal_open: the session in the terminal is still open; end it before detaching"))
            return
        stop_proc(lease, "detaching")
    so, why, earlier = None, "", []
    try:
        earlier = earlier_tasks(lease.project)
        so, why = run_handover(lease, earlier)
        failed = _("the model did not produce a valid handover")
    except Exception as e:  # the platform's own error (not the model's): give the reason as it is
        log(_("{id} handover failed: {e!r}", id=lease.id, e=e))
        why, failed = repr(e)[:300], _("a platform error, not the model's")
    fallback = so is None
    h = {"v": 1, "project": lease.project, "session_id": lease.id, "mode": lease.mode, "reason": reason,
         "summary": _("Handover failed ({failed}); the conversation is still there and can be attached again",
                      failed=failed) if fallback else so.get("summary", ""),
         "decisions": [] if fallback else so.get("decisions", []),
         "open_questions": [] if fallback else so.get("open_questions", []),
         "tasks_for_owner": [] if fallback else so.get("tasks_for_owner", []),
         "ended": [] if fallback else so.get("ended", []),
         "notes_markdown": "" if fallback else so.get("notes_markdown", ""),
         "notes_written": False, "fallback": fallback, "branch": None, "commits": [], "tests": None}
    try:
        # which handover this is: recorded before sending, so a resend of the same handover gets the same ids (the bus
        # and the assistant both dedupe by id)
        with _lock:
            lease.handovers += 1
            save()
        deliver(lease, h, lease.handovers, earlier)
    except Exception as e:
        log(_("{id} sending the handover failed: {e!r}", id=lease.id, e=e))
        lease.emit("error", error="handover_failed", detail=repr(e)[:300])
    if fallback:
        lease.emit("error", error="handover_failed", detail=_("Sent a minimal handover. Reason: {why}", why=why)[:300])
    lease.emit("handover", handover=h)
    lease.set_state("detached", reason=reason)


# ---------- idle ----------

STATUS: dict = {"quota": None}


def idle_loop() -> None:
    while True:
        time.sleep(IDLE_TICK_S)
        with _lock:
            leases = list(LEASES.values())
        for l in leases:
            try:
                if l.state not in ("running", "idle_stopped"):
                    continue
                idle = (now() - datetime.datetime.fromisoformat(l.last_activity)).total_seconds()
                if l.waiting or l.in_turn:
                    continue
                if l.state == "running" and idle >= PROC_IDLE_S:
                    stop_proc(l, "idle_stopped")
                if idle >= LEASE_IDLE_S and not l.idle_warned_at:
                    l.idle_warned_at = iso()
                    l.emit("idle_warning", idle_s=int(idle))
                if l.warned_at and (now() - datetime.datetime.fromisoformat(l.warned_at)).total_seconds() >= WARN_GRACE_S:
                    threading.Thread(target=detach, args=(l, "idle"), daemon=True).start()
            except Exception as e:
                log(_("idle_loop {id}: {e!r}", id=l.id, e=e))
        try:
            prune_files()
        except Exception as e:
            log(_("prune_files: {e!r}", e=e))
        restart_if_code_changed()


BUSY = ("attaching", "running", "resuming", "detaching")
CODE_AT_START = 0.0
# each restart makes launchd wait 10 s before starting it (throttle); don't keep restarting while files keep changing
CODE_SETTLE_S = 60


def restart_if_code_changed() -> None:
    """The code in bin/ changed and no lease is running or being handed over: exit, so that launchd (KeepAlive)
    restarts with the new code. Otherwise old modules would mix with the new ones on disk (lease 9af483fb's handover
    failed that way on 10-25). Idle leases resume with --resume as usual after the restart."""
    stamp = hubconf.code_stamp()
    # mid-change (saving repeatedly): wait, and restart once when it settles
    if stamp <= CODE_AT_START or time.time() - stamp < CODE_SETTLE_S:
        return
    with _lock:
        busy = [l.id[:8] for l in LEASES.values() if l.state in BUSY or l.waiting or l.in_turn]
        if busy:
            return
        log(_("bin/ changed and no lease is running: exiting so launchd restarts with the new code"))
        os._exit(0)


# ---------- HTTP ----------

def token() -> str:
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(pysecrets.token_urlsafe(32))
        TOKEN_FILE.chmod(0o600)
    return TOKEN_FILE.read_text().strip()


def relay_event(b: dict) -> tuple:
    """A permission relay report → (event type, fields). kind is the action kind (runtime_ports.KINDS); when the relay
    sends a native tool name, the runtime translates it."""
    kind = b.get("kind")
    if kind not in runtime_ports.KINDS:
        kind = rt().action(b.get("tool") or "", {})[0]
    data = {"phase": b.get("phase"), "kind": kind, "summary": b.get("summary", "")}
    for k in ("target", "decision"):
        if b.get(k) is not None:
            data[k] = b[k]
    return ("question" if kind == "ask_user" else "permission"), data


def secret_paths(project: str) -> list:
    """The files the project registry's secrets: declares (relative to runtime, or to workspace if there's no runtime):
    never sent when showing the user a file."""
    doc = hubconf.projects().get(project) or {}
    base = (hubconf.p("services_root") / project) if doc.get("runtime") is not None else hubconf.workspace(project)
    out = []
    for sec in doc.get("secrets") or []:
        p = pathlib.Path(os.path.expanduser(str(sec.get("path", ""))))
        if str(p) and base is not None:
            out.append(p if p.is_absolute() else base / p)
    return out


def show_file(lease: "Lease", path: str, summary: str | None) -> tuple:
    """Show the user a file in the project (docs/design/lease-show-file.md): check it, copy it into the lease directory,
    send a file event (v2 only)."""
    import lease_files
    meta, why = lease_files.take(lease.cwd, path, summary, lease.dir / "files", secret_paths(lease.project))
    if meta is None:
        log(_("{id} show_file refused: {path}: {why}", id=lease.id[:8], path=path, why=why))
        return None, why
    lease.emit("file", **meta)
    log(f"{lease.id[:8]} show_file {meta['path']}({meta['size']} bytes)→ {meta['id']}")
    return meta, ""


LAST_FILE_PRUNE = [0.0]


def prune_files() -> None:
    """Files shown to the user are kept lease_files.KEEP_DAYS days; cleaned once an hour."""
    import lease_files
    if time.time() - LAST_FILE_PRUNE[0] < 3600:
        return
    LAST_FILE_PRUNE[0] = time.time()
    with _lock:
        dirs = [l.dir / "files" for l in LEASES.values()]
    for d in dirs:
        lease_files.prune(d, lease_files.KEEP_DAYS * 86400)


def quiesce(project: str) -> str:
    """Before a rename or upgrade: if this project has a lease answering, waiting for a reply, in a terminal or
    being reclaimed → return the reason (touching nothing); otherwise stop the processes first (the leases remain, and
    later messages resume them automatically)."""
    with _lock:
        mine = [l for l in LEASES.values() if l.project == project]
        if any(live(l) and (l.in_turn or l.waiting or l.state in ("attaching", "resuming", "detaching", "terminal"))
               for l in mine):
            return _("{project} has a lease that is answering, waiting for a reply, open in a terminal, or being "
                     "detached", project=project)
        for l in mine:
            if l.proc is not None:
                stop_proc(l, "idle_stopped")
    return ""


def follow_move(old: str, new: str, old_ws: str) -> None:
    """After a rename or upgrade: this project's leases follow the new name and folder (transcripts move along); if the
    new registry doesn't have work on, work goes back to discuss."""
    import registry
    registry.refresh()
    new_ws = str(hubconf.workspace(new) or old_ws)
    with _lock:
        mine = [l for l in LEASES.values() if l.project == old]
        for l in mine:
            moved = old_ws != new_ws and rt().relocate(l.id, old_ws, new_ws)
            renamed = (l.project, l.cwd) != (new, new_ws)
            l.project, l.cwd = new, new_ws
            downgraded = l.mode == "work" and not work_ready(new)[0]
            if downgraded:
                l.mode = "discuss"
            if live(l) and (renamed or downgraded):
                extra = {"project": new} if renamed else {}
                if downgraded:
                    extra.update(mode="discuss", detail=_("This project has no work mode (enforce.work in its registry "
                                                          "entry); this lease is read-only again"))
                l.emit("state", state=l.state, **extra)
            log(_("upgrade {old} → {new}: lease {id} follows ({moved})", old=old, new=new, id=l.id[:8],
                  moved=_("conversation moved") if moved else _("conversation not moved")))
        save()


def live(l: Lease) -> bool:
    return l.state not in ("detached",)


class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, code: int, body: dict) -> None:
        body = {**body, "v": 2} if "v" in body else body  # contract v2 (v1 is deleted)
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def err(self, code: int, kind: str, detail: str = "") -> None:
        self.reply(code, {"v": 2, "error": kind, "detail": detail})

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        # attachments are base64 inside the JSON; too-large requests aren't read (or they'd all go into memory)
        if n > MAX_BODY_BYTES:
            self.rfile.read(0)
            return {"__bad__": True}
        try:
            return json.loads(self.rfile.read(n) or b"{}") if n else {}
        except ValueError:
            return {"__bad__": True}

    def authed(self) -> bool:
        if self.headers.get("Authorization") != f"Bearer {token()}":
            self.err(401, "unauthorized")
            return False
        return True

    def lease(self, lid: str) -> Lease | None:
        l = LEASES.get(lid)
        if l is None:
            self.err(404, "not_found", lid)
        return l

    def do_GET(self):
        if not self.authed():
            return
        path = urllib.parse.urlparse(self.path).path
        parts = path.strip("/").split("/")
        if path == "/v1/leases":
            with _lock:
                ls = sorted(LEASES.values(), key=lambda x: x.last_activity, reverse=True)
                return self.reply(200, {"v": 1, "leases": [l.public() for l in ls if live(l)] +
                                        [l.public() for l in ls if not live(l)][:20]})
        # for the assistant's permission cards: the domains that can't be "allowed for the whole lease" (the only list)
        if path == "/v1/policy":
            import registry
            idx = registry.refresh()
            return self.reply(200, {"v": 1, "no_session_domains": hubconf.get("lease.no_session_domains", []),
                                    "attachable": sorted(idx["projects"]), "modes": ["discuss", "work"],
                                    "work_projects": work_projects()})
        if path == "/v1/status":
            with _lock:
                n = sum(1 for l in LEASES.values() if live(l))
            return self.reply(200, {"v": 2, "leased": n, "cap": GLOBAL_CAP, "quota": STATUS["quota"]})
        if len(parts) == 5 and parts[:2] == ["v1", "leases"] and parts[3] == "files":
            return self.send_file(parts[2], parts[4])
        if len(parts) == 4 and parts[:2] == ["v1", "leases"] and parts[3] == "events":
            l = self.lease(parts[2])
            if l:
                self.sse(l)
            return
        self.err(404, "not_found", path)

    def send_file(self, lid: str, fid: str) -> None:
        """GET /v1/leases/<id>/files/<file id>: a file shown to the user (what's sent is the copy made then)."""
        import lease_files
        l = LEASES.get(lid)
        got = lease_files.read(l.dir / "files", fid) if l else None
        if not got:
            return self.err(404, "not_found", _("no such file (or past retention)"))
        meta, data = got
        self.send_response(200)
        self.send_header("Content-Type", meta["mime"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(meta["name"]))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def sse_line(self, ev: dict) -> bytes:
        out = render(ev)
        return f"id: {out['seq']}\nevent: {out['type']}\ndata: {json.dumps(out, ensure_ascii=False)}\n\n".encode()

    def sse(self, l: Lease) -> None:
        last = int(self.headers.get("Last-Event-ID") or 0)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        # replay first: what's in the file (without deltas) + what's in memory
        sent = last
        backlog = []
        f = l.dir / "events.jsonl"
        if f.exists():
            for line in f.read_text().splitlines():
                ev = json.loads(line)
                if ev["seq"] > sent:
                    backlog.append(ev)
        with _lock:
            backlog += [e for e in l.recent if e["seq"] > sent and e["type"] == "text_delta"]
        try:
            for ev in sorted(backlog, key=lambda e: e["seq"]):
                self.wfile.write(self.sse_line(ev))
                sent = max(sent, ev["seq"])
            self.wfile.flush()
            while True:
                with l.cond:
                    l.cond.wait(timeout=15)
                    new = [e for e in l.recent if e["seq"] > sent]
                if not new:
                    self.wfile.write(b": keepalive\n\n")
                for ev in new:
                    self.wfile.write(self.sse_line(ev))
                    sent = ev["seq"]
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def do_POST(self):
        if not self.authed():
            return
        b = self.body()
        if b.get("__bad__"):
            return self.err(400, "bad_request", _("body is not JSON"))
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if parts == ["v1", "leases"]:
            return self.create(b)
        if parts == ["v1", "sandboxes"]:
            return self.create_sandbox(b)
        if len(parts) == 4 and parts[:2] == ["v1", "projects"] and parts[3] == "rename":
            return self.rename_project(parts[2], b)
        if len(parts) == 4 and parts[:2] == ["v1", "sandboxes"] and parts[3] == "upgrade":
            return self.upgrade_sandbox(parts[2], b)
        if len(parts) == 4 and parts[:2] == ["v1", "internal"] and parts[3] == "show":
            l = self.lease(parts[2])
            if l:
                meta, why = show_file(l, b.get("path"), b.get("summary"))
                if meta is None:
                    return self.err(400, "file_refused", why)
                with _lock:
                    l.touch()
                self.reply(200, {"v": 2, **meta})
            return
        if len(parts) == 4 and parts[:2] == ["v1", "internal"] and parts[3] == "relay":
            l = self.lease(parts[2])
            if l:
                with _lock:
                    if b.get("phase") == "start":
                        l.waiting += 1
                    elif l.waiting:
                        l.waiting -= 1
                    l.touch()
                etype, data = relay_event(b)
                l.emit(etype, **data)
                self.reply(200, {"v": 1})
            return
        if len(parts) != 4 or parts[:2] != ["v1", "leases"]:
            return self.err(404, "not_found")
        l = self.lease(parts[2])
        if not l:
            return
        action = parts[3]
        if action == "messages":
            atts = b.get("attachments")
            bad = check_attachments(atts)
            if bad:
                return self.err(400, "bad_request", bad)
            if not b.get("client_msg_id") or not (b.get("text") or atts) or not isinstance(b.get("text", ""), str):
                return self.err(400, "bad_request", _("client_msg_id is required, and at least one of text or attachments"))
            with _lock:
                if b["client_msg_id"] in l.seen_msgs:
                    return self.reply(202, {"v": 1, "duplicate": True})
                if l.state in ("terminal", "detaching", "detached"):
                    return self.err(409, "busy", l.state)
                l.seen_msgs.append(b["client_msg_id"])
            try:
                send_message(l, b.get("text") or "", atts)
            except Exception as e:
                return self.err(500, "resume_failed", repr(e)[:300])
            return self.reply(202, {"v": 1})
        if action == "warned":
            with _lock:
                l.warned_at = iso()
            l.emit("state", state=l.state, warned=True)
            return self.reply(200, {"v": 1})
        if action == "mode":
            mode = b.get("mode")
            if mode not in ("discuss", "work"):
                return self.err(400, "bad_request", _("mode must be discuss or work"))
            if mode == "work":
                ok, why = work_ready(l.project)
                if not ok:
                    return self.err(400, "work_not_allowed", why)
            with _lock:
                if l.state in ("terminal", "detaching", "detached"):
                    return self.err(409, "busy", l.state)
                if l.in_turn or l.waiting:
                    return self.err(409, "busy", _("answering or waiting for the owner's reply; switch after this turn"))
                if l.mode == mode:
                    return self.reply(200, {"v": 1, **l.public()})
                # switching judgment point = switching process: stop it, resume the same session with --resume (the
                # permission relay restarts too)
                stop_proc(l, "attaching")
                l.mode = mode
                l.emit("state", state="attaching", mode=mode)
                try:
                    start_proc(l, resume=True)
                except Exception as e:
                    l.set_state("detached", reason="crash")
                    return self.err(500, "internal", repr(e)[:300])
                save()
            log(f"lease {l.id[:8]} {l.project}: mode → {mode}")
            return self.reply(200, {"v": 1, **l.public()})
        if action == "keepalive":
            with _lock:
                l.touch()
            return self.reply(200, {"v": 1})
        if action == "terminal":
            with _lock:
                if l.state in ("detaching", "detached"):
                    return self.err(409, "busy", l.state)
                stop_proc(l, "terminal")
            command, hint = rt().resume_command(l.id)
            return self.reply(200, {"v": 2, "command": command, "hint": hint})
        if action == "detach":
            if l.state in ("detaching", "detached"):
                return self.err(409, "busy", l.state)
            if l.state == "terminal" and terminal_open(l):
                return self.err(409, "busy", "terminal_open")
            threading.Thread(target=detach, args=(l, b.get("reason") or "user"), daemon=True).start()
            return self.reply(202, {"v": 1})
        self.err(404, "not_found", action)

    def create_sandbox(self, b: dict) -> None:
        """Idea sandboxes: handed to create-idea-sandbox.py (validation, resends, the limit and failure cleanup are all
        there), with the result turned into HTTP."""
        name, purpose = b.get("name"), b.get("purpose")
        if not isinstance(name, str) or not isinstance(purpose, str):
            return self.err(400, "bad_name", _("name and purpose must be strings"))
        env = {**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"}
        try:
            r = subprocess.run([PY, str(HUB / "bin" / "create-idea-sandbox.py"), name, purpose],
                               capture_output=True, text=True, timeout=180, env=env)
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"sandbox {name}: {e!r}")
            return self.err(500, "create_failed", repr(e)[:300])
        log(f"sandbox {name}: {res.get('result')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("created", "exists_same"):
            return self.reply(201 if result == "created" else 200,
                              {"v": 1, "name": res["name"], "created": result == "created"})
        code = {"bad_name": 400, "exists": 409, "too_many": 429}.get(result, 500)
        body = {"v": 1, "error": result if code != 500 else "create_failed",
                "detail": res.get("detail") or (r.stderr or "").strip()[-300:]}
        body.update({k: res[k] for k in ("limit", "current") if k in res})
        return self.reply(code, body)

    def rename_project(self, name: str, b: dict) -> None:
        """POST /v1/projects/<name>/rename {v, to}: rename a regular project or an idea sandbox as a whole (10-25, the
        assistant's m10251650).
        Handed to project-rename.py (GitHub repo, registration, memory, folder; retries pick up where it stopped); deep
        talks are handled the same way as for an upgrade (quiesce, follow_move)."""
        to = b.get("to")
        if not isinstance(to, str) or not to:
            return self.err(400, "bad_name", _("to must be the new name"))
        why = quiesce(name)
        if why:
            return self.err(409, "busy", _("{why}; rename once it stops", why=why))
        old_ws = str(hubconf.workspace(name) or "")
        cmd = [PY, str(HUB / "bin" / "project-rename.py"), name, to, "--apply", "--json"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"})
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"rename {name} → {to}: {e!r}")
            return self.err(500, "rename_failed", repr(e)[:300])
        log(f"rename {name} → {to}: {res.get('result')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("renamed", "already"):
            follow_move(name, res["name"], old_ws)
            return self.reply(200, {"v": 2, "name": res["name"], "origin": res.get("origin", ""),
                                    "renamed": result == "renamed"})
        code = {"bad_name": 400, "not_found": 404, "name_taken": 409, "busy": 409}.get(result, 500)
        return self.reply(code, {"v": 2, "error": result if code != 500 else "rename_failed",
                                 "detail": res.get("detail") or (r.stderr or "").strip()[-300:]})

    def upgrade_sandbox(self, name: str, b: dict) -> None:
        """Upgrade an idea sandbox to a regular project: handed to upgrade-idea.py (checks, rename, creating the GitHub
        repo, registry), with the result turned into HTTP.
        When that sandbox has a lease (10-25 m10251626, m10251631): answering or waiting for the user's reply → 409
        busy; otherwise stop the process first (the lease remains, and later messages resume it automatically). With a
        rename the folder moves: the transcript moves to the new folder (rt().relocate), the lease's project/cwd become
        the new ones, and leases not yet reclaimed get a state event with the new project; later messages are sent as
        usual and the runner resumes the same conversation in the new folder.
        An upgrade carries work (enforce.work) over; if the new registry has no work, leases in work go back to discuss
        (the state event carries mode and detail)."""
        rename = b.get("rename")
        if rename is not None and not isinstance(rename, str):
            return self.err(400, "bad_request", _("rename must be a string"))
        why = quiesce(name)
        if why:
            return self.err(409, "busy", _("{why}; upgrade once it stops", why=why))
        old_ws = str(hubconf.workspace(name) or "")
        cmd = [PY, str(HUB / "bin" / "upgrade-idea.py"), name, "--apply", "--json"] + (["--rename", rename] if rename else [])
        env = {**os.environ, "PATH": f"{hubconf.LAUNCHD_PATH}:/usr/sbin:/sbin"}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=env)
            res = json.loads((r.stdout.strip().splitlines() or ["{}"])[-1])
        except (subprocess.TimeoutExpired, ValueError) as e:
            log(f"upgrade {name}: {e!r}")
            return self.err(500, "upgrade_failed", repr(e)[:300])
        log(f"upgrade {name}: {res.get('result')} {res.get('name', '')} {res.get('detail', '')}")
        result = res.get("result")
        if result in ("upgraded", "already"):
            follow_move(name, res["name"], old_ws)
            return self.reply(200, {"v": 1, "name": res["name"], "origin": res["origin"], "upgraded": result == "upgraded"})
        code = {"not_found": 404, "not_idea": 409, "dirty": 409, "name_taken": 409}.get(result, 500)
        return self.reply(code, {"v": 1, "error": result if code != 500 else "upgrade_failed",
                                 "detail": res.get("detail") or (r.stderr or "").strip()[-300:]})

    def create(self, b: dict) -> None:
        proj, sid = b.get("project"), str(b.get("session_id") or "")
        mode = b.get("mode", "discuss")
        need = {"agent", "auth", "sessions", "stream"}   # long-running streaming + resuming + login: what leasing needs
        if (miss := need - runtime_ports.capabilities()):
            return self.err(409, "runtime_unsupported",
                            _("runtime {name} has no {missing}; cannot open a lease", name=runtime_ports.default_name(),
                              missing=runtime_ports.or_list(runtime_ports.PORT_LABELS[p] for p in sorted(miss))))
        if mode not in ("discuss", "work"):
            return self.err(400, "bad_request", _("mode must be discuss or work"))
        try:
            uuid.UUID(sid)
        except ValueError:
            return self.err(400, "bad_request", _("session_id must be a UUID"))
        import registry
        registry.refresh()  # also recognise projects just added to the registry
        ws = hubconf.workspace(proj) if proj in hubconf.projects() or proj == hubconf.hub_project() else None
        if not ws or not ws.is_dir():
            return self.err(404, "not_registered", str(proj))
        if mode == "work":
            ok, why = work_ready(proj)
            if not ok:
                return self.err(400, "work_not_allowed", why)
        with _lock:
            if sid in LEASES and live(LEASES[sid]):
                return self.reply(200, {"v": 1, **LEASES[sid].public()})
            if sum(1 for l in LEASES.values() if live(l)) >= GLOBAL_CAP:
                return self.reply(409, {"v": 1, "error": "cap_reached", "detail": _("at most {n} at a time", n=GLOBAL_CAP),
                                        "leases": [l.public() for l in LEASES.values() if live(l)]})
            prev = LEASES.get(sid)  # a reclaimed lease resumed with the same id (the assistant back in that thread):
            if prev and prev.cwd != str(ws):  # the folder moved (rename, upgrade): the transcript follows, or no resume
                rt().relocate(sid, prev.cwd, str(ws))
            # the event sequence and processed messages must continue from the old ones, or the sequence restarts at 0,
            # collides with the old events in events.jsonl and SSE resumption gets confused
            lease = Lease({"lease_id": sid, "project": proj, "mode": mode, "thread": b.get("thread", ""),
                           "cwd": str(ws), "started_once": bool(b.get("resume")), "state": "attaching",
                           "max_turns": int(b.get("max_turns") or DEFAULT_MAX_TURNS),
                           "seq": prev.seq if prev else 0, "seen_msgs": prev.seen_msgs if prev else [],
                           "handovers": prev.handovers if prev else 0})
            LEASES[sid] = lease
            lease.emit("state", state="attaching", project=proj)
            try:
                start_proc(lease, resume=bool(b.get("resume")))
            except Exception as e:
                lease.set_state("detached", reason="crash")
                return self.err(500, "internal", repr(e)[:300])
        self.reply(201, {"v": 1, **lease.public()})


def main() -> None:
    global CODE_AT_START
    CODE_AT_START = hubconf.code_stamp()
    DIR.mkdir(parents=True, exist_ok=True)
    token()
    load()
    threading.Thread(target=idle_loop, daemon=True).start()
    srv = http.server.ThreadingHTTPServer((HOST, PORT), H)
    srv.daemon_threads = True
    log(_("lease-runner started, {host}:{port}, {n} lease {n:record|records}", host=HOST, port=PORT, n=len(LEASES)))
    srv.serve_forever()


if __name__ == "__main__":
    main()
