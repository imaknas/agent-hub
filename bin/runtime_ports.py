"""The runtime's interfaces (ports): the platform depends only on what is defined here; Claude Code is just one
implementation. Design: docs/design/runtime-abstraction.md.

One implementation = one module (`runtime_<name>.py`) providing the functions each interface below requires; the
Strategy chooses "which module". `load()` **checks mechanically** that the module provides every function and refuses
to load it if any is missing; the contract tests (tests/test_runtime_contracts.py) find every implementation from
RUNTIMES automatically, so a third implementation can't be added without going through the contract tests.

The current interfaces (the other layers get added one at a time, following the design doc's steps):
  transcripts (layer 5): read conversation records in a neutral format
    turns(paths, since, until=None, worktrees=False) -> Iterator[Turn]   every entry of a project (its working
                                                        directories, maybe several) within the time range;
                                                        worktrees=True also counts sessions in worktrees the runtime
                                                        opened under those working directories
    last_activity(path) -> datetime | None              when this working directory last had a conversation
  agent (layer 1, a single run):
    run(task: Task) -> TaskResult                       run a judgment point once (prompt, working directory, model,
                                                        output schema, timeout)
    describe(task: Task) -> list[str]                   how it would run (for llm.py --dry-run; the content is the
                                                        runtime's own)
    The failure policy (on_failure, job.failed, schema validation, cost accounting) stays in llm.py, not in the
    runtime (design principle 10).
    Task.spec is the judgment point's declaration (contracts/llm-points.yaml); it still has Claude-specific fields
    (allowed_tools, permission_mode…), to be replaced once layer 2's neutral policy (another session) is settled;
    until then only the runtime itself reads those fields, llm.py doesn't.
  lifecycle (layer 3, events): the platform's own event names (EVENTS); each runtime wires its native hooks to
  `bin/hub-event <event>`
    parse_event(event, raw) -> HubEvent | None          native hook input → a neutral event (None if no real session
                                                        is running)
    inject(event, text) -> str                           text to put into the agent's context → the native output
                                                         format ("" if this event can't take it)
    deny(event, reason) -> str                           block this action → the native output format
    install_lifecycle(workspace) -> None                 install the hooks in this project (the platform's replaced
                                                         with new ones, the user's own kept)
  instructions (layer 4, rules file): which file the runtime reads that the rules and memory notes go into (the
  content is the platform's, templates/)
    instructions_file(workspace) -> Path                 the file the platform-managed block is written into
                                                         (untracked)
    local_ignores() -> list[str]                         this runtime's local-only files (relative to the project
                                                         folder), to add to .git/info/exclude
                                                         (.agent-memory is the platform's own, not listed here)
  sessions (part of layer 1):
    alive(sid) -> bool, live_sessions() -> set[str], context_size(sid) -> int | None (how many tokens resuming would
    load)
    current_session(env) -> str | None                   which session the current process belongs to (for hubbus
                                                         send to record the sender)
  auth (layer 6, login): the platform says only "which kind of login" (mode: token = a long-lived credential for
  unattended use, subscription = the interactive login, for jobs that need connectors; the names follow the auth field
  of contracts/llm-points.yaml); where the credentials are and what the environment variables are called is the
  runtime's business
    auth_env(mode, env) -> (env | None, reason)          the environment variables to give the agent for this kind of
                                                         login (starting from env, with this runtime's own credential
                                                         variables cleared first); credentials missing →
                                                         (None, a reason for a person to read)
    clean_env(env) -> dict                               remove this runtime's credential variables (before handing
                                                         over to someone else, or to use the default login)
    credentials() -> list[Credential]                   which logins this machine has, whether present, when they
                                                        expire (values never read or printed)
    is_login_error(text) -> bool                         whether this error message is a login problem (a person has
                                                         to deal with it) or something else (timeout, quota…)
  completion (a judgment point's "answer only"; docs/design/llm-providers.md): the model has no tools and touches no
  files
    complete(req: CompletionRequest, conf: dict) -> CompletionResult   conf = this provider's settings in hub.json
                                                        llm.providers (the key is read by completion.py and put in
                                                        conf["_key"]; implementations don't look for credentials)
    health(conf) -> (ok, reason)                         the smallest check (preferably one that spends no tokens, e.g.
                                                         listing models)
    Besides runtimes (Claude Code run with its tools off), providers that only do completion also implement this
    interface (PROVIDERS).
    JSON parsing, validation, retries, truncation, backoff and accounting are in completion.py, not in the
    implementations.
  stream (translating a long-running session's output; docs/design/lease-contract-neutral.md):
    action(tool, input) -> (kind, collab, target, summary)   tool → KINDS/COLLABS; target is for judging, summary for
                                                             people
    parse_stream(line) -> list[StreamEvent]             one line of the runtime's native output → neutral conversation
                                                        events
    classify_exit(code, stderr_tail) -> StreamEvent      the process ended: not logged in (agent_login) or crashed
                                                         (process_crashed)
    resumable(sid) -> bool                               whether this conversation can still be resumed (its record
                                                         still exists)
    resume_command(sid) -> (command, hint)               the command to resume it in a terminal and a sentence for
                                                         people
    relocate(sid, old_cwd, new_cwd) -> bool              the project folder moved (rename, upgrade): make this
                                                         conversation resumable in the new folder too

Standard library only; the system python3 (3.9) can import it too (hubbus uses that one).
"""
from __future__ import annotations

import datetime
import importlib
import os
from typing import NamedTuple

import i18n

_ = i18n.domain(__file__)

# Registration table: name → module. Several runtimes can be registered at once; each session uses its own (design
# principle 9).
RUNTIMES = {"claude": "runtime_claude", "fake": "runtime_fake", "none": "runtime_none", "command": "runtime_command"}
DEFAULT = "claude"   # the default when hub.json has no runtime.default
# those implementing only the completion interface (LLM APIs): hub.json llm.providers' impl names these, or the name of
# any runtime with a completion interface
PROVIDERS = {"anthropic": "provider_anthropic", "openai": "provider_openai", "ollama": "provider_ollama"}

# Owner-facing names of the ports (Unsupported reasons, doctor's capability table). PORT_LABELS / FEATURES are built
# when read (module __getattr__), so they follow the UI language of the moment.
def port_labels() -> dict[str, str]:
    return {"agent": _("agent runs"), "lifecycle": _("session events (hooks)"), "instructions": _("rules file"),
            "transcripts": _("transcripts"), "sessions": _("session state"),
            "stream": _("long-running streaming sessions"), "auth": _("login state"),
            "completion": _("completion interface")}


# Which ports each platform feature needs (doctor's "tool x feature" table; docs/design/tool-agnostic.md principle 3).
# Missing any one = that feature is off.
def features() -> tuple:
    return (
        (_("judgment points (librarian, index, consolidate, weekly report analysis)"), ("agent", "auth")),
        (_("mailbox tasks (the platform sends an agent for letters nobody picked up)"), ("agent", "auth")),
        (_("mailbox follow-up (replying with an idle session's context)"), ("agent", "sessions")),
        (_("channel leasing (also needs the assistant module)"), ("stream", "sessions", "agent", "auth")),
        (_("letters seen live in the session, reminders to write notes"), ("lifecycle",)),
        (_("platform rules written into the project's rules file"), ("instructions",)),
        (_("memory gaps, the weekly report's transcript statistics"), ("transcripts",)),
    )


def __getattr__(name: str):
    if name == "PORT_LABELS":
        return port_labels()
    if name == "FEATURES":
        return features()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def or_list(items) -> str:
    """Owner-facing "a", "a or b", "a, b, or c" (what is missing; zh-Hant joins every item with the
    ideographic comma, as before)."""
    items = list(items)
    if len(items) < 2:
        return "".join(items)
    if len(items) == 2:
        return _("{a} or {b}", a=items[0], b=items[1])
    return _("{items}, or {last}", items=_(", ").join(items[:-1]), last=items[-1])


def feature_table(name: str | None = None) -> list[tuple[str, bool, str]]:
    """(feature, usable or not, what's missing): what's missing uses PORT_LABELS' names."""
    have, labels = capabilities(name), port_labels()
    return [(f, not (miss := [labels[p] for p in need if p not in have]), or_list(miss)) for f, need in features()]


PORTS = {
    "agent": ("run", "describe"),
    "lifecycle": ("parse_event", "inject", "deny", "install_lifecycle"),
    "instructions": ("instructions_file", "local_ignores"),
    "transcripts": ("turns", "last_activity"),
    "sessions": ("alive", "live_sessions", "context_size", "current_session"),
    "stream": ("action", "parse_stream", "classify_exit", "resumable", "resume_command", "relocate"),
    "auth": ("auth_env", "clean_env", "credentials", "is_login_error"),
    "completion": ("complete", "health"),
}

# Neutral "kinds of action": what the agent did in a conversation record, without any runtime's tool names.


# The platform's events (layer 3). A runtime without a given moment doesn't support it; the platform treats it as
# unsupported rather than pretending.
EVENTS = ("session_start", "session_end", "pre_compact", "prompt", "tool_used", "open_session_attempt")


class HubEvent(NamedTuple):
    event: str               # one of EVENTS
    session: str             # which session
    cwd: str                 # which folder this session is in
    command: str | None = None   # tool_used: the command run (for now only "run a command" tools are wired up)
    # open_session_attempt: where it wants to open {"cwd": path or None, "elsewhere": kind or None}
    # elsewhere = {"kind": "repository", "name"} | {"kind": "machine"} | {"kind": "unknown_place"}
    target: dict | None = None


# Effects of actions (docs/design/lease-contract-neutral.md): the only set in the whole platform: lease events,
# permissions (approvals), judgment point declarations and transcript statistics all use it rather than naming things
# their own way. Each runtime's action() translates which tool counts as which.
KINDS = ("read", "search", "ask_user", "open_web", "write_file", "run_command", "other")
# Collaboration kinds (a different dimension from effects): handing off to a subagent, running a workflow, messaging
# across sessions, using a skill; other actions are None.
COLLABS = ("subagent", "workflow", "peer_message", "skill")


class Action(NamedTuple):
    kind: str            # one of KINDS (effects: read, search, ask a person, open a web page, write a file, run a
                         # command, other)
    detail: str = ""     # the skill name (when collab is skill); never any runtime's tool name
    collab: str | None = None   # one of COLLABS, or None


# Conversation events (a long-running session's output; the lease contract v2's events have exactly this shape). Not the
# same thing as HubEvent (hook events).
STREAM_TYPES = ("text_delta", "assistant", "tool_use", "tool_result", "turn_end", "quota", "error")
QUOTA_STATUS = ("ok", "warning", "exhausted")


class StreamEvent(NamedTuple):
    """type is one of STREAM_TYPES; data holds the neutral fields (see contracts/lease-runner.v2.schema.json); keys
    starting with `_` are internal signals for the runner (e.g. `_detach`) and are never sent out."""
    type: str
    data: dict


class Turn(NamedTuple):
    ts: datetime.datetime    # aware, UTC
    session: str
    role: str                # "human" (what the user actually sent) | "assistant" | "other" (tool results, system
                             # messages, messages from other sessions…)
    text: str = ""           # for role == human, what they typed
    actions: tuple = ()      # for role == assistant, which Actions this entry did


class Credential(NamedTuple):
    """One login (layer 6). The wording fields are for people (alerts, check output), and the platform builds sentences
    from them; the thresholds are properties of this credential itself."""
    name: str                    # short name (the "…: ok" in the check output)
    label: str                   # the subject when it's broken
    mode: str | None             # which login a real request check uses; None = can't be checked with a request
                                 # (expiry only)
    present: bool                # whether it's there (logins that can't be seen, e.g. the keychain, count as present;
                                 # only a request confirms)
    renew: str                   # how to log in again (for a person to do)
    path: str | None = None      # where the credential file is (only shown to people)
    since: datetime.datetime | None = None          # when it was created (for its age)
    max_age: datetime.timedelta | None = None       # remind to replace it after this
    lifetime: str | None = None                     # something like "one year", for people
    expires: datetime.datetime | None = None        # when it expires
    warn_before: datetime.timedelta | None = None   # how long before expiry to remind
    used_by: str | None = None                      # who uses it (for people; the platform states the uses it knows)
    note: str | None = None                         # a note to print when checking (e.g. the expiry date can't be seen)


class Task(NamedTuple):
    point: str               # judgment point name
    prompt: str
    spec: dict               # the judgment point's declaration (see above: carries runtime-specific fields for now)
    cwd: str | None = None
    model: str | None = None
    output_schema: dict | None = None
    timeout_s: int = 1200
    env: dict | None = None  # the run's environment variables (login already applied: llm.py computes it with the auth
                             # interface's auth_env)
    extra: tuple = ()        # extra arguments from the caller (e.g. which session a lease handover resumes): to be
                             # replaced by a resume interface


class TaskResult(NamedTuple):
    ok: bool                         # whether the runtime ran to the end and gave a result (whether the structured
                                     # output fits the schema is llm.py's call)
    incomplete: bool = False         # there is a result, but the agent reports this run didn't finish normally (e.g.
                                     # the turn limit): not a failure, the caller decides
    text: str = ""                   # the final reply text
    structured_output: object = None
    cost_usd: float | None = None
    session_id: str | None = None
    turns: int | None = None
    stop: str = ""                   # why it ended (the runtime's own wording, only for the log)
    denials: tuple = ()              # actions that were refused
    error: str = ""                  # the reason when ok is False


class CompletionRequest(NamedTuple):
    """One request to the completion interface (docs/design/llm-providers.md): the model has no tools, touches no files,
    returns only text or JSON matching the schema."""
    point: str                       # which judgment point (for the log and accounting)
    prompt: str
    model: str | None = None         # this implementation's own model name (from provider.models in the llm settings)
    schema: dict | None = None       # the output JSON schema wanted; None = text
    system: str | None = None
    max_tokens: int = 8192
    timeout_s: int = 600


class CompletionResult(NamedTuple):
    ok: bool
    text: str = ""                   # the model's raw reply (JSON is here too; parsing, validation, retries are in
                                     # completion.py, not in implementations)
    stop: str = ""                   # one of COMPLETION_STOPS
    error: str = ""                  # the reason when ok is False
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None    # filled only if the implementation knows the cost itself (e.g. Claude Code returns
                                     # it); APIs return only tokens, and completion.py prices them from the price list
    retry_after_s: float | None = None   # when stop is rate_limited, how long the service suggests waiting


# end = finished normally; length = truncated by length (a retry would truncate the same way, so no retry); refused =
# the model refused; rate_limited = 429/529 and the like, retried after backing off; error = any other failure
COMPLETION_STOPS = ("end", "length", "refused", "rate_limited", "error")


class MissingFunctions(Exception):
    """Part of an interface's functions implemented: that's a bug, refused at load time."""


class Unsupported(Exception):
    """This runtime has no such interface at all (e.g. runtime_none, a completion-only adapter): not an error, a lack of
    capability. Callers treat it as "unsupported": switch that feature off explicitly and say why (str(e)), **never run
    on empty data** (docs/design/tool-agnostic.md principle 3)."""


def _module(name: str):
    if name not in RUNTIMES:
        raise KeyError(_("no registered runtime: {name} (have: {have})", name=name, have=", ".join(RUNTIMES)))
    return importlib.import_module(RUNTIMES[name])


def capabilities(name: str | None = None) -> set[str]:
    """Which interfaces this runtime has: inferred from which functions it implements, not declared separately (a
    declaration would drift from the implementation)."""
    mod = _module(name or default_name())
    return {p for p, fns in PORTS.items() if all(callable(getattr(mod, fn, None)) for fn in fns)}


def models(name: str | None = None) -> dict:
    """This runtime's group → model (`MODELS`; a judgment point's model_group is fast/standard/strong). A group not
    listed = no model given, the tool's own default. Model names are written only in adapters (AGENTS.md, Tool
    neutrality)."""
    return dict(getattr(_module(name or default_name()), "MODELS", None) or {})


def default_name() -> str:
    try:
        import hubconf
        return hubconf.get("runtime.default", DEFAULT) or DEFAULT
    except Exception:
        return DEFAULT


def load(name: str | None = None, ports=tuple(PORTS)):
    """Get a runtime's module. A requested interface only partly implemented → MissingFunctions (a bug, not something
    that breaks only when called); entirely absent → Unsupported (lack of capability: the caller says why and switches
    the feature off)."""
    name = name or default_name()
    mod = _module(name)
    absent = []
    for p in ports:
        have = [fn for fn in PORTS[p] if callable(getattr(mod, fn, None))]
        if not have:
            absent.append(p)
        elif len(have) < len(PORTS[p]):
            missing = [f"{p}.{fn}" for fn in PORTS[p] if fn not in have]
            raise MissingFunctions(_("runtime {name} ({module}) is missing: {missing}", name=name, module=RUNTIMES[name],
                                     missing=", ".join(missing)))
    if absent:
        labels = port_labels()
        raise Unsupported(_("runtime {name} has no {missing} (hub.json runtime.default; to use this feature, install a "
                            "coding agent that supports it)", name=name, missing=or_list(labels.get(p, p) for p in absent)))
    return mod


def load_completion(impl: str):
    """hub.json llm.providers' impl → the module implementing the completion interface (a provider, or a runtime with
    completion)."""
    if impl in PROVIDERS:
        mod = importlib.import_module(PROVIDERS[impl])
        missing = [fn for fn in PORTS["completion"] if not callable(getattr(mod, fn, None))]
        if missing:
            raise MissingFunctions(_("provider {impl} is missing: {missing}", impl=impl, missing=", ".join(missing)))
        return mod
    if impl in RUNTIMES:
        return load(impl, ports=("completion",))
    raise KeyError(_("no such completion implementation: {impl} (have: {have})", impl=impl,
                     have=", ".join([*PROVIDERS, *RUNTIMES])))


def current_session(env=None) -> str | None:
    """Which session the current process belongs to: asks the default runtime (for now, reading the environment
    variables Claude Code provides).
    No session interface (runtime_none) = unknown, not an error: mail is sent and presence recorded as usual, just
    without a sending session."""
    try:
        return load(ports=("sessions",)).current_session(os.environ if env is None else env)
    except Unsupported:
        return None
