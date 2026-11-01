"""The runtime's interfaces (ports): the platform depends only on what is defined here; Claude Code is just one
implementation. Design: docs/design/runtime-abstraction.md.

One implementation = one module (`runtime_<name>.py`) providing the functions each interface below requires; the
Strategy chooses "which module". `load()` **checks mechanically** that the module provides every function and refuses
to load it if any is missing; the contract tests (tests/test_runtime_contracts.py) find every implementation from
RUNTIMES automatically, so a third implementation can't be added without going through the contract tests.

The current interfaces (the other layers get added one at a time, following the design doc's steps):
  transcripts (layer 5): read conversation records in a neutral format
    turns(paths, since, until=None, worktrees=False, subagents=False) -> Iterator[Turn]
                                                        every entry of a project (its working directories, maybe
                                                        several) within the time range; worktrees=True also counts
                                                        sessions in worktrees the runtime opened under those working
                                                        directories; subagents=True also the subagents those sessions
                                                        started (Turn.subagent). Each entry carries its own id (a
                                                        branched or resumed session copies earlier entries: count an id
                                                        once), the folder it ran in, and the tool calls it started
                                                        (Action.call, .command) or returned results for (Turn.results),
                                                        so a call's duration is the time between the two
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
    install_lifecycle(workspace, check=False) -> bool    install the hooks in this project (the platform's replaced
                                                         with new ones, the user's own kept); whether that changed
                                                         anything. check=True only says whether it would, writing
                                                         nothing (an applier's plan)
    remove_lifecycle(workspace, check=False) -> bool     optional: the platform's hooks out of this project, the
                                                         user's own kept (agenthub uninstall); whether that changed
                                                         anything. Without it, uninstall says the hooks stay
  instructions (layer 4, rules file): which file the runtime reads that the rules and memory notes go into (the
  content is the platform's, templates/)
    instructions_file(workspace) -> Path                 the file the platform-managed block is written into
                                                         (untracked)
    local_ignores() -> list[str]                         this runtime's local-only files (relative to the project
                                                         folder), to add to .git/info/exclude
                                                         (.agent-memory is the platform's own, not listed here)
  tool_servers: which of the environment's tool servers a run would load (a judgment point declaring
  `tool_servers: allowed_only` denies every one its allow list doesn't name; policy.tool_server_denials)
    ambient_servers(task) -> list[str] | None            the servers this run would load from its environment (the
                                                         user's, the project's, plugins', the account's connectors), in
                                                         the runtime's own server ids, found for that run (its folder,
                                                         its login); None = it loads none of them (only what the
                                                         platform gives it). Can't tell → ValueError: the run doesn't
                                                         happen
    servers_named(task) -> list[str]                     the servers the run's allow list names a tool of (same ids)
    The servers to deny go back in Task.deny_servers; the runtime denies each one whole.
  control: the files in a project folder the runtime loads settings, hooks and tools from. A sandboxed project's own
  processes must never write them (its sessions would run what it put there, outside its sandbox); the platform
  writes the settings itself
    control_paths() -> list[(name, tree)]                names matched at any depth under the project folder; tree:
                                                         the whole subtree, else only that folder entry itself (so it
                                                         can't be moved away or replaced)
    session_folders(workspace) -> list[Path]             where sessions of this project start (the folder, its
                                                         worktrees): each gets the platform's settings
    foreign_controls(workspace) -> list[str]             what in the folder would make its sessions run something the
                                                         platform didn't put there (committed hooks, tool servers)
  sessions (part of layer 1):
    alive(sid) -> bool, live_sessions() -> set[str], context_size(sid) -> int | None (how many tokens resuming would
    load)
    current_session(env) -> str | None                   which session the current process belongs to (for hubbus
                                                         send to record the sender)
  wake (optional; docs/design/mailbox-runtimes.md B1): nudge a live session when a letter arrives for it
    wake(sid, text) -> bool                              ask that session to start a turn with this short notice (never
                                                         the letter itself: that stays on the bus and the session's hook
                                                         delivers it); True = handed over, False = it could not be. A
                                                         runtime without it is Unsupported: letters wait for the next
                                                         hook as before
    Outside the port, a runtime may also provide wake_address(env) -> dict | None: what a session's hook environment
    says about reaching that session, which the platform keeps in secrets_dir()/sessions/<sid>.json (mode 600; in no
    sandbox, the bus, .state or the backup) for wake to use; wait(raw, env) -> (exit code, stderr, how): a hook that
    waits for a wake (`hub-event wait`; its files go in the same folder, hubconf.session_file); unsupported(port) ->
    str | None: why it lacks a port (doctor shows it in the feature table instead of only the port's name);
    note(port) -> str | None: what to know about a port it has (doctor shows it after "available"); and
    session_pid(env) -> int | None: the session's own process, from a hook's environment, which the platform records
    beside the session's id (record_session below; its live_sessions adds recorded_sessions(<its name>)), so a session
    whose id is on no command line (a fork) still counts as live; with person_session(pid, env) -> bool: whether that
    session is one a person works in (a headless run gets no record)
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

More than one runtime the user opens sessions with (hub.json runtime.installed; docs/design/mailbox-runtimes.md A1):
installed(), qualify(name, id) / runtime_of(sid) between a runtime's own session ids and the platform's (bare for
runtime.bare_ids, <runtime>:<id> otherwise), Sessions (the sessions port over every installed runtime, each call routed
by the session's runtime), current_session (the nearest ancestor process a runtime claims, when several answer).

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
RUNTIMES = {"claude": "runtime_claude", "fake": "runtime_fake", "none": "runtime_none", "command": "runtime_command",
            "acp": "runtime_acp"}
DEFAULT = "claude"   # the default when hub.json has no runtime.default
# those implementing only the completion interface (LLM APIs): hub.json llm.providers' impl names these, or the name of
# any runtime with a completion interface
PROVIDERS = {"anthropic": "provider_anthropic", "openai": "provider_openai", "ollama": "provider_ollama"}

# The judgment points the platform runs only in its own throwaway worktree (bin/propose_apply.py marks the run with
# HUB_THROWAWAY_COPY). A runtime that can only be trusted there declares THROWAWAY_ONLY = True (runtime_acp: the agent
# decides when to ask, so nothing else holds it to a point's limits); config check refuses to pin it on other points,
# and doctor lists what it would refuse.
THROWAWAY_POINTS = ("mailbox-propose",)
# Judgment points whose callers pass the default runtime's own arguments (resuming a session): they cannot be pinned
# to another runtime (hub.json llm.points.<point>.runtime), which would drop those arguments.
RUNTIME_ARG_POINTS = ("mailbox-conversation", "lease-handover")


def throwaway_only(name: str | None = None) -> bool:
    return bool(getattr(_module(name or default_name()), "THROWAWAY_ONLY", False))


# Owner-facing names of the ports (Unsupported reasons, doctor's capability table). PORT_LABELS / FEATURES are built
# when read (module __getattr__), so they follow the UI language of the moment.
def port_labels() -> dict[str, str]:
    return {"agent": _("agent runs"), "lifecycle": _("session events (hooks)"), "instructions": _("rules file"),
            "transcripts": _("transcripts"), "sessions": _("session state"),
            "stream": _("long-running streaming sessions"), "auth": _("login state"),
            "completion": _("completion interface"), "confine": _("confined runs (declared permissions only)"),
            # each reads after "has no …" (doctor, Unsupported reasons)
            "control": _("map of where sessions load settings and hooks from"),
            "wake": _("way to wake an idle session"),
            "tool_servers": _("way to tell which tool servers a run loads")}


# Which ports each platform feature needs (doctor's "tool x feature" table; docs/design/tool-agnostic.md principle 3).
# Missing any one = that feature is off.
def features() -> tuple:
    return (
        (_("judgment points (librarian, index, consolidate, weekly report analysis)"), ("agent", "auth")),
        # dispatch needs the session state too: it must know which sessions are live before it delegates (hubbus)
        (_("mailbox tasks (the platform sends an agent for letters nobody picked up)"), ("agent", "auth", "sessions")),
        (_("unattended agent jobs with allow:, and event-triggered agent jobs"), ("agent", "auth", "confine")),
        (_("mailbox follow-up (replying with an idle session's context)"), ("agent", "sessions")),
        (_("channel leasing (also needs the assistant module)"), ("stream", "sessions", "agent", "auth")),
        (_("letters seen live in the session, reminders to write notes"), ("lifecycle",)),
        # reinforcement only: without it a letter waits for the session's next hook (docs/design/mailbox-runtimes.md)
        (_("an idle open session is woken when a letter arrives for it"), ("sessions", "wake")),
        (_("platform rules written into the project's rules file"), ("instructions",)),
        (_("a sandboxed project can't write what its sessions load hooks and settings from"), ("control",)),
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


def missing_label(name: str | None, port: str) -> str:
    """What a runtime lacks, for people: the port's name, followed by the runtime's own reason when it gives one
    (unsupported(port), e.g. the tool has the feature but doesn't document how to use it)."""
    label = port_labels().get(port, port)
    why = getattr(_module(name or default_name()), "unsupported", None)
    why = why(port) if callable(why) else None
    return _("{label}: {why}", label=label, why=why) if why else label


def feature_notes(name: str | None = None) -> dict[str, str]:
    """{feature: what to know} for the features this runtime has, from its note(port) for the ports they need."""
    fn = getattr(_module(name or default_name()), "note", None)
    have, out = capabilities(name), {}
    for feat, need in features():
        notes = [n for p in need if p in have and callable(fn) and (n := fn(p))]
        if notes and set(need) <= have:
            out[feat] = _(", ").join(notes)
    return out


def optional_features() -> set[str]:
    """The features that only reinforce (they need an OPTIONAL port): off is said, but is nothing to fix."""
    return {f for f, need in features() if set(need) & set(OPTIONAL)}


def feature_table(name: str | None = None) -> list[tuple[str, bool, str]]:
    """(feature, usable or not, what's missing): what's missing uses PORT_LABELS' names (missing_label)."""
    have = capabilities(name)
    return [(f, not (miss := [missing_label(name, p) for p in need if p not in have]), or_list(miss))
            for f, need in features()]


PORTS = {
    "agent": ("run", "describe"),
    "lifecycle": ("parse_event", "inject", "deny", "install_lifecycle"),
    "instructions": ("instructions_file", "local_ignores"),
    "control": ("control_paths", "session_folders", "foreign_controls"),
    "transcripts": ("turns", "last_activity"),
    "sessions": ("alive", "live_sessions", "context_size", "current_session"),
    "stream": ("action", "parse_stream", "classify_exit", "resumable", "resume_command", "relocate"),
    "auth": ("auth_env", "clean_env", "credentials", "is_login_error"),
    "completion": ("complete", "health"),
    # confine: the runtime honours a judgment point's declared permissions (allow_from_env: only those rules, nothing
    # asked; settings: platform: none of the user's, project's or local settings, only the platform's for the project).
    # A point that declares them refuses to run on a runtime without it (llm.py)
    "confine": ("platform_settings",),
    "tool_servers": ("ambient_servers", "servers_named"),
    "wake": ("wake",),
}
# Ports a runtime may lack without any platform feature switching off (only reinforcement is lost): load() without
# ports= doesn't ask for them
OPTIONAL = ("wake",)

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
    call: str = ""       # the call's id: the entry that returns its result names it in Turn.results
    command: str = ""    # for run_command, the command text
    background: bool = False    # started without waiting for it


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
    id: str = ""             # the entry's own id ("" when the runtime has none)
    folder: str = ""         # the working folder the session was in at this entry
    subagent: bool = False   # an entry of a subagent the session started
    results: tuple = ()      # the call ids (Action.call) whose results this entry returns


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
    deny_servers: tuple = ()  # tool servers this run may not use at all, each one whole (llm.py:
                              # policy.tool_server_denials over the tool_servers interface)


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


def load(name: str | None = None, ports=None):
    """Get a runtime's module. A requested interface only partly implemented → MissingFunctions (a bug, not something
    that breaks only when called); entirely absent → Unsupported (lack of capability: the caller says why and switches
    the feature off)."""
    name = name or default_name()
    mod = _module(name)
    ports = tuple(p for p in PORTS if p not in OPTIONAL) if ports is None else ports
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


# ---------- more than one runtime the user opens sessions with (docs/design/mailbox-runtimes.md A1) ----------
# runtime.default is the agent the platform starts (headless dispatch, background resume, agent jobs, leases).
# runtime.installed lists every agent the user opens sessions with (default: [runtime.default]). A session id keeps the
# form its runtime gives it when that runtime is runtime.bare_ids (the platform's ids from before A1: every one written
# then is Claude Code's, so unset it means DEFAULT, whatever runtime.default says now); any other runtime's ids are
# written <runtime>:<id>, and mcp:<pid>:<start> are the MCP server's (A3). So nothing on the bus needs migrating, and
# changing runtime.default never reroutes an old id.

def installed() -> list[str]:
    """hub.json runtime.installed: the registered runtimes the user opens sessions with, in order, no repeats."""
    try:
        import hubconf
        names = hubconf.get("runtime.installed") or None
    except Exception:
        names = None
    out = []
    for n in names if isinstance(names, list) else [default_name()]:
        if isinstance(n, str) and n in RUNTIMES and n not in out:
            out.append(n)
    return out or [default_name()]


def bare_runtime() -> str:
    """The runtime whose session ids are written without a prefix (hub.json runtime.bare_ids, else DEFAULT)."""
    try:
        import hubconf
        name = hubconf.get("runtime.bare_ids") or DEFAULT
    except Exception:
        name = DEFAULT
    return name if name in RUNTIMES else DEFAULT


def qualify(name: str | None, native: str | None) -> str | None:
    """A runtime's own session id → the platform's: as it is for the bare runtime, <runtime>:<id> otherwise."""
    if not native:
        return native
    name = name or default_name()
    return native if name == bare_runtime() else f"{name}:{native}"


def runtime_of(sid: str | None) -> tuple[str | None, str | None]:
    """The platform's session id → (runtime, that runtime's own id). An MCP server's id (mcp:…) and an empty one belong
    to no runtime: (None, sid)."""
    if not sid or sid.startswith("mcp:"):
        return None, sid
    head, sep, rest = sid.partition(":")
    if sep and head in RUNTIMES and head != bare_runtime():
        return head, rest
    return bare_runtime(), sid


class Sessions:
    """The sessions port over every installed runtime that has it, each call routed by runtime_of(sid): what dispatch
    asks (live_sessions is their union, in the platform's ids). wake goes to the session's runtime when it has the
    wake port. Resuming or forking a session in the background runs the mailbox-conversation point, which runs on
    runtime.default and can't be pinned elsewhere (RUNTIME_ARG_POINTS): only sessions of that runtime, when it has the
    agent port, can be resumed (can_resume); the others are handled as delegations."""

    def __init__(self, names=None):
        self.mods = {}
        for n in names or installed():
            try:
                self.mods[n] = load(n, ports=("sessions",))
            except Unsupported:
                pass

    def _mod(self, sid):
        name, native = runtime_of(sid)
        return self.mods.get(name), native

    def live_sessions(self) -> set[str]:
        return {qualify(n, s) for n, m in self.mods.items() for s in m.live_sessions()}

    def alive(self, sid) -> bool:
        m, native = self._mod(sid)
        return bool(m) and m.alive(native)

    def context_size(self, sid):
        m, native = self._mod(sid)
        return m.context_size(native) if m else None

    def can_resume(self, sid) -> bool:
        name, _native = runtime_of(sid)
        return name == default_name() and name in self.mods and "agent" in capabilities(name) \
            and callable(getattr(self.mods[name], "resume_args", None))

    def resume_args(self, sid) -> list[str]:
        if not self.can_resume(sid):
            raise Unsupported(_("session {sid} can't be resumed in the background (its runtime isn't {runtime})",
                                sid=sid, runtime=default_name()))
        m, native = self._mod(sid)
        return m.resume_args(native)

    def wake(self, sid, text) -> bool:
        m, native = self._mod(sid)
        fn = getattr(m, "wake", None) if m else None
        if not callable(fn):
            raise Unsupported(missing_label(runtime_of(sid)[0], "wake") if m else sid)
        return fn(native, text)


def _parent_and_command(pid: int) -> tuple[int | None, str]:
    """A process's parent pid and command line (/proc on Linux, ps elsewhere); (None, "") when it can't be read."""
    import subprocess
    try:
        if os.path.exists("/proc/self/stat"):
            with open(f"/proc/{pid}/stat") as fh:
                ppid = int(fh.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                return ppid, fh.read().replace(b"\0", b" ").decode(errors="replace").strip()
        r = subprocess.run(["ps", "-o", "ppid=,command=", "-p", str(pid)], capture_output=True, text=True, timeout=10)
        ppid, _sp, cmd = r.stdout.strip().partition(" ")
        return (int(ppid), cmd.strip()) if r.returncode == 0 and ppid.isdigit() else (None, "")
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None, ""


def ancestors(pid: int | None = None, limit: int = 64):
    """(pid, command line) of this process's parent, its parent, … up to pid 1 (included: in a container the init
    can be the parent)."""
    pid = os.getpid() if pid is None else pid
    for _i in range(limit):
        ppid, _cmd = _parent_and_command(pid)
        if not ppid:
            return
        yield ppid, _parent_and_command(ppid)[1]
        if ppid <= 1:
            return
        pid = ppid


def process_starts(pids) -> dict:
    """When each of these processes started, in whole seconds since the epoch: {pid: "<seconds>"}, a process that
    doesn't exist (or can't be read) left out. With the pid it names one process: a pid reused later has another start
    time. One ps for all of them (macOS), or /proc (Linux)."""
    import subprocess
    import time
    pids = sorted({p for p in pids if isinstance(p, int) and not isinstance(p, bool) and p > 0})
    if not pids:
        return {}
    out = {}
    try:
        if os.path.exists("/proc/self/stat"):
            with open("/proc/stat") as fh:
                btime = next(int(line.split()[1]) for line in fh if line.startswith("btime "))
            tick = os.sysconf("SC_CLK_TCK")
            for pid in pids:
                try:
                    with open(f"/proc/{pid}/stat") as fh:
                        # field 22: clock ticks after boot (the fields after "(comm)" start at field 3)
                        out[pid] = str(btime + int(fh.read().rsplit(")", 1)[1].split()[19]) // tick)
                except (OSError, ValueError, IndexError):
                    pass
            return out
        import calendar
        r = subprocess.run(["ps", "-o", "pid=,lstart=", "-p", ",".join(map(str, pids))], capture_output=True,
                           text=True, timeout=10, env={**os.environ, "LC_ALL": "C", "TZ": "UTC"})
        for line in r.stdout.splitlines():
            pid, _sp, when = line.strip().partition(" ")
            try:
                out[int(pid)] = str(calendar.timegm(time.strptime(" ".join(when.split()), "%a %b %d %H:%M:%S %Y")))
            except ValueError:
                pass
    except (OSError, ValueError, StopIteration, subprocess.SubprocessError):
        pass
    return out


def process_start(pid: int) -> str | None:
    """When process pid started (process_starts); None: no such process, or it can't be read."""
    return process_starts([pid]).get(pid)


# ---------- the sessions the hooks saw running (docs/design/mailbox-runtimes.md B1, "forked sessions") ----------
# A session's hook knows both its real session id (the hook input) and its process (the runtime's session_pid(env)),
# which the process table alone doesn't pair up: a session started as a fork of another runs under a new id that is on
# no command line. So every hook in a project records <session>.live in hubconf.session_addresses_dir() (the secrets
# directory: written by the hook outside any sandbox, in every sandbox's deny_read, not backed up, removed at the
# session's end with its other files): {runtime, pid, start, cwd}. A record counts while that pid runs with that start
# time, so a reused pid never fakes it. One process runs one session at a time: writing a record drops the others
# naming the same process (a /clear, a fork whose first hook still carried the parent's id).

SESSION_RECORD = "live"


def _read_record(path) -> dict | None:
    import json
    try:
        rec = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    ok = isinstance(rec, dict) and isinstance(rec.get("pid"), int) and isinstance(rec.get("start"), str) \
        and isinstance(rec.get("runtime"), str)
    return rec if ok else None


def record_session(session: str, runtime: str, pid, cwd: str, *, refresh: bool = True, wanted=None) -> bool:
    """Record that this session (a platform id) runs in process pid. refresh=False (the hooks after a session's start)
    leaves a record naming the same process as it is, so a tool call costs no process lookup. wanted(pid): asked only
    when it would write (the runtime's person_session: a headless run gets none). Whether it wrote."""
    import hubconf
    f = hubconf.session_file(session, SESSION_RECORD)
    if not f or not isinstance(pid, int) or pid <= 1:
        return False
    if not refresh:
        old = _read_record(f)
        if old and old["pid"] == pid and old["runtime"] == runtime:
            return False
    if wanted is not None and not wanted(pid):
        return False
    start = process_start(pid)
    if not start:
        return False
    import json
    f.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"runtime": runtime, "pid": pid, "start": start, "cwd": cwd}, fh)
    os.replace(tmp, f)
    for other in f.parent.glob(f"*.{SESSION_RECORD}"):
        rec = _read_record(other) if other != f else None
        if rec and rec["pid"] == pid and rec["start"] == start:
            other.unlink(missing_ok=True)
    return True


def running_records(paths) -> dict:
    """Of these session records, the ones whose process still runs with the start time recorded: {path: record}. One
    process lookup for all of them."""
    recs = {p: r for p in paths if (r := _read_record(p))}
    starts = process_starts([r["pid"] for r in recs.values()])
    return {p: r for p, r in recs.items() if starts.get(r["pid"]) == r["start"]}


def recorded_sessions(runtime: str) -> set[str]:
    """This runtime's sessions (its own ids) whose recorded process still runs."""
    import hubconf
    try:
        files = list(hubconf.session_addresses_dir().glob(f"*.{SESSION_RECORD}"))
    except OSError:
        return set()
    out = set()
    for f, rec in running_records(files).items():
        session = hubconf.session_of_file(f.name)
        name, native = runtime_of(session) if session else (None, None)
        if rec["runtime"] == runtime and name == runtime:
            out.add(native)
    return out


def current_session(env=None) -> str | None:
    """Which session the current process belongs to, in the platform's ids. With one installed runtime: that runtime
    answers from the environment, as before A1. With several, each that has session state may find an id in the
    environment (a session started inside another runtime's session inherits that one's variables), so the nearest
    ancestor process that one of them claims as its session (its session_process(command)) decides; nobody claiming
    one = unknown rather than a guess.
    No session interface (runtime_none) = unknown, not an error: mail is sent and presence recorded as usual, just
    without a sending session."""
    env = os.environ if env is None else env
    names = installed()
    if len(names) == 1:
        try:
            return qualify(names[0], load(names[0], ports=("sessions",)).current_session(env))
        except Unsupported:
            return None
    found = {}
    for n in names:
        try:
            sid = load(n, ports=("sessions",)).current_session(env)
        except Unsupported:
            continue
        if sid:
            found[n] = sid
    if len(found) <= 1:
        return next((qualify(n, s) for n, s in found.items()), None)
    for _pid, cmd in ancestors():
        for n, sid in found.items():
            claims = getattr(_module(n), "session_process", None)
            if callable(claims) and claims(cmd):
                return qualify(n, sid)
    return None
