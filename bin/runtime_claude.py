"""The runtime adapter (Claude Code): the only place the platform learns about Claude Code's sessions and settings
format.

Switching tools, or Claude Code shipping an official way to "wake an open session", changes only this file; hubbus and
the projects' agents don't need to change. Design: docs/design/mailbox-routing.md (sessions),
docs/design/capabilities.md (settings). Standard library only; the system python3 (3.9) can import it too, which is why
this doesn't import registry/policy and only takes the dict policy.policy() computed.

  live_sessions()        session ids still alive (Claude Code processes' arguments carry --resume=<id> or
                         --session-id=<id>; verified 10-24 to be the same as the hook's session_id and
                         CLAUDE_CODE_SESSION_ID)
  context_tokens(sid)    how much context resuming this session would load (the usage of the last reply in the
                         transcript; for a compacted one, the size after compaction). None if not found
  resume_args(sid)       the claude arguments for resuming in the background: --resume <sid> --fork-session (tested
                         10-24: the fork carries the full context, gets a new id, and the original transcript is left
                         completely untouched: an open session isn't affected)

Settings (neutral policy → Claude Code's settings; the policy's fields are in policy.py):
  settings(pol)          → (sandbox, deny, allow): the platform-managed part of the project's
                         .claude/settings.local.json
                         (render-project-settings.py writes the file, records it, detects drift)
  lease_settings(pol)    → the content of a lease's `--settings`: the same deny + user-level credentials + blocking
                         `security`
  settings_path(ws), sandbox_rendered(ws)   where the settings file is, whether the OS sandbox really has been written
  control_paths(), session_folders(ws), foreign_controls(ws), platform_hooks()
                                            the files sessions load settings, hooks and tools from (runtime_ports'
                                            control interface)
                                            into it

Tool servers (runtime_ports' tool_servers interface; docs/design/evaluators.md step 0):
  ambient_servers(task)  the MCP servers a run without --strict-mcp-config would load: `claude mcp list` for that run
                         (the only place the account's claude.ai connectors show up) plus the config files; None for a
                         sealed run; anything it can't read → ValueError, so the run doesn't happen
  servers_named(task)    the servers its allow rules name; Task.deny_servers becomes --disallowedTools mcp__<id>
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess

import i18n

_ = i18n.domain(__file__)
NOT_FOUND = "claude not found"   # also what is_login_error looks for (with its translation)
PROJECTS = pathlib.Path.home() / ".claude" / "projects"
# a judgment point's model_group → model (runtime_ports.models)
MODELS = {"fast": "haiku", "standard": "sonnet", "strong": "opus"}
_ID = re.compile(r"--(?:resume|session-id)[= ]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def live_sessions() -> set[str]:
    r = subprocess.run(["ps", "-axo", "command"], capture_output=True, text=True)
    return set(_ID.findall(r.stdout)) if r.returncode == 0 else set()


def alive(sid: str | None) -> bool:
    return bool(sid) and sid in live_sessions()


def transcript(sid: str) -> pathlib.Path | None:
    hits = list(PROJECTS.glob(f"*/{sid}.jsonl")) if sid and re.fullmatch(r"[0-9a-f-]{36}", sid) else []
    return max(hits, key=lambda p: p.stat().st_mtime) if hits else None


def context_tokens(sid: str) -> int | None:
    t = transcript(sid)
    if not t:
        return None
    try:
        lines = t.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        u = (e.get("message") or {}).get("usage") if e.get("type") == "assistant" else None
        if u:
            return int(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                       + u.get("cache_creation_input_tokens", 0))
    return None


def resume_args(sid: str) -> list[str]:
    return ["--resume", sid, "--fork-session"]


# ---------- settings ----------

def _tilde(p) -> str:
    s, h = str(p), str(pathlib.Path.home())
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def _cli_rules(commands) -> list[str]:
    """The platform's session commands ("agenthub send") → Claude rules: one without arguments and one with."""
    return [r for c in commands for r in (f"Bash({c})", f"Bash({c} *)")]


def settings(pol: dict) -> tuple[dict, list[str], list[str]]:
    deny = []
    for p in pol["other_projects"]:
        deny += [f"Read(/{p}/**)", f"Edit(/{p}/**)"]  # //abs: an absolute path
    deny += [f"Edit({_tilde(p)}/**)" for p in pol["deny_write"]]
    deny += [f"Read({_tilde(p)}/**)" for p in pol["deny_read"]]
    # the files its sessions load settings and hooks from, at any depth (worktrees included): never written by the
    # session's file tools (its Bash is kept off them by the sandbox itself)
    ws = pol.get("workspace")
    deny += [r for c in pol.get("protect") or [] if c["tree"] and ws
             for r in (f"Edit(/{ws}/**/{c['name']})", f"Edit(/{ws}/**/{c['name']}/**)")]
    allow = [f"Bash({_tilde(c)} *)" for c in pol["platform_commands"]] + _cli_rules(pol.get("platform_cli") or [])
    sandbox = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": list(pol["write"]),
            "denyRead": [_tilde(p) for p in pol["other_projects"]] + [_tilde(p) for p in pol["deny_read"]],
        },
        "network": {"allowedDomains": list(pol["network"]), "allowLocalBinding": bool(pol.get("local_bind"))},
    }
    return sandbox, deny, allow


def session_env(pol: dict) -> dict[str, str]:
    """The settings file's env for a sandboxed project's sessions (its own tool caches: policy.py env)."""
    return dict(pol.get("env") or {})


def lease_settings(pol: dict) -> dict:
    deny = settings(pol)[1]
    deny += [f"Read({_tilde(p)})" for p in pol["credential_files"]] + [f"Read({_tilde(p)}/**)" for p in pol["credentials"]]
    # The Keychain isn't a file, so the OS sandbox can't block it (tested 10-25: Bash inside the sandbox could look up
    # Claude's login credentials).
    # These rules are only reinforcement: another way of writing it (sh -c, python) gets around them; the real gate
    # is that every work-mode command asks the owner
    deny += ["Bash(security:*)", "Bash(security *)", "Bash(/usr/bin/security *)"]
    return {"permissions": {"deny": deny}}


def platform_settings(project: str) -> pathlib.Path:
    """A judgment point with settings: platform: the only settings file the run loads (with --restricted, Claude Code
    reads none of the user's, project's or local files): the project's sandbox when it's enforced, the deny list and
    the platform's commands, all from the policy, never from what is rendered in the workspace."""
    import hubconf
    import policy
    import registry
    index = registry.refresh()
    doc = index["projects"][project]
    pol = policy.policy(project, doc, index)
    sandbox = settings(pol)[0]
    # only the mail commands (read, send, close letters): not raw hubbus, merging or running code through the platform
    allow = _cli_rules(policy.platform_cli(mail_only=True))
    out = {"permissions": {"deny": lease_settings(pol)["permissions"]["deny"], "allow": allow}}
    # the registry's enforce.sandbox, as render-project-settings decides it (not the services-only exemption)
    if (doc.get("enforce") or {}).get("sandbox") is True:
        # autoAllowBashIfSandboxed defaults to true: a sandboxed Bash command would be approved before allow: is
        # consulted, so allow: would stop being the boundary exactly in the sandboxed projects
        out["sandbox"] = {**sandbox, "autoAllowBashIfSandboxed": False}
    p = hubconf.STATE / "agent-jobs" / ".settings" / f"{project}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f".{p.name}.{os.getpid()}")
    tmp.write_text(json.dumps(out, ensure_ascii=False))
    tmp.replace(p)
    return p


def settings_path(ws: pathlib.Path) -> pathlib.Path:
    return pathlib.Path(ws) / ".claude" / "settings.local.json"


def sandbox_rendered(ws) -> tuple[bool, str]:
    """The registry's enforce.sandbox is only an intention; the settings file written is the fact."""
    import nofollow   # the project's folder: no link followed, no FIFO waited on
    try:
        st = json.loads(nofollow.read(ws, settings_path(ws).relative_to(ws)) or b"")
    except (OSError, ValueError, TypeError):
        return False, _("the sandbox is not in this project's settings yet (settings.local.json cannot be read)")
    if not (st.get("sandbox") or {}).get("enabled"):
        return False, _("the sandbox is not in this project's settings yet (sandbox.enabled is not true)")
    return True, ""


# ---------- sessions (runtime_ports' sessions interface) ----------

def context_size(sid: str) -> int | None:
    return context_tokens(sid)


def current_session(env) -> str | None:
    """The processes Claude Code runs inside a session (Bash, hooks) all carry this environment variable."""
    return env.get("CLAUDE_CODE_SESSION_ID") or None


_SESSION_PROCESS = re.compile(r"(?:^|/)claude(?:\s|$)|/claude-code/|@anthropic-ai/claude-code")


def session_process(command: str) -> bool:
    """Whether a process with this command line is a Claude Code session (runtime_ports.current_session asks, walking
    up from the current process, only when more than one runtime is installed): the claude executable, or the npm
    package's entry point."""
    return bool(_SESSION_PROCESS.search(command or ""))


# ---------- waking a live session (runtime_ports' wake; docs/design/mailbox-runtimes.md B1) ----------
# Claude Code binds a per-session inbox socket and exports its path and a per-session token to hooks, SessionStart
# included (code.claude.com/docs/en/cross-session-messaging, "The session's inbox socket"; env-vars,
# CLAUDE_CODE_MESSAGING_SOCKET / _TOKEN). The docs give only the connection's first line, {"type":"auth","token":…};
# the format of a message after it is not documented (checked 11-01). So wake() is not implemented and the port is
# Unsupported with that reason, rather than reverse-engineered. The address is kept already (wake_address), so wake()
# is the only piece to add once the format is documented. The other documented wake, an asyncRewake hook, ends at its
# hook timeout and at its first wake, so it needs a respawn design of its own (left to the owner).

MESSAGING_VARS = {"socket": "CLAUDE_CODE_MESSAGING_SOCKET", "token": "CLAUDE_CODE_MESSAGING_TOKEN"}


def wake_address(env) -> dict | None:
    """The session's inbox socket and token from its hook environment (None: messaging is off in that session)."""
    out = {k: env.get(v) for k, v in MESSAGING_VARS.items() if env.get(v)}
    return out if out.get("socket") else None


def unsupported(port: str) -> str | None:
    """Why a port is missing here, for doctor's feature table."""
    if port == "wake":
        return _("Claude Code documents only the auth line of a session's inbox socket, not the message format; "
                 "a letter waits for the session's next hook")
    return None


# ---------- transcripts (runtime_ports' transcripts interface) ----------
# Claude Code's transcripts are an undocumented internal format (~/.claude/projects/<slug>/<session>.jsonl). This is
# the only place the platform reads it: parsed into neutral Turn/Action, failing explicitly on an unrecognised format
# (TranscriptFormatError) rather than letting statistics go silently wrong.

import datetime  # noqa: E402

from runtime_ports import Action, Turn  # noqa: E402


class TranscriptFormatError(Exception):
    pass


def _mini_home() -> str:
    try:
        import hubconf
        return hubconf.get("hosts.mini_home", str(pathlib.Path.home()))
    except Exception:
        return str(pathlib.Path.home())


def transcript_roots() -> tuple:
    """Where to read: what the laptop backs up hourly (the personal profile) + the hub machine's local sessions.
    **Do not** add the work profile's backup (~/transcript-backups/claude-work-projects): weekly-reflect sends the
    message text read from here to a cloud model, and work conversations may only be processed locally (see
    known-issues)."""
    return (pathlib.Path.home() / "transcript-backups" / "claude-personal-projects",
            pathlib.Path(_mini_home()) / ".claude" / "projects",
            # the personal profile when run by hand on the remote machine
            pathlib.Path.home() / ".claude-personal" / "projects")


def slug_for_path(path: str) -> str:
    """The folder name Claude Code stores transcripts under: / and . in the path replaced by -."""
    return path.replace("/", "-").replace(".", "-")


def _is_human_prompt(entry: dict) -> bool:
    """Count only messages the user actually sent. Excluded: those from other sessions, background task notifications,
    slash command output, compaction summaries, hook injections, etc. (they are type=user in the transcript too)."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    if not isinstance(entry.get("message", {}).get("content"), str):
        return False  # tool results
    origin = entry.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def _action(block: dict) -> Action:
    """One tool call in a transcript → a neutral Action (kind is KINDS, collab the collaboration kind). There's only
    one translation table: action().
    detail holds only skill names: Claude's tool names don't leak into the neutral format."""
    name, inp = block.get("name", ""), block.get("input") or {}
    kind, collab, _target, _summary = action(name, inp)
    return Action(kind, str(inp.get("skill", "?")) if collab == "skill" else "", collab, str(block.get("id") or ""),
                  str(inp.get("command") or "") if kind == "run_command" else "", bool(inp.get("run_in_background")))


def _parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


_KNOWN = {"user", "assistant", "system", "summary", "attachment", "progress", "queue-operation", "file-history-snapshot"}


def _turn(o: dict, fallback_sid: str) -> Turn | None:
    ts = o.get("timestamp")
    if not ts:
        return None
    sid = o.get("sessionId") or fallback_sid
    common = {"id": str(o.get("uuid") or ""), "folder": str(o.get("cwd") or ""), "subagent": bool(o.get("isSidechain"))}
    if _is_human_prompt(o):
        return Turn(_parse_ts(ts), sid, "human", o["message"]["content"], **common)
    content = (o.get("message") or {}).get("content")
    content = content if isinstance(content, list) else []
    if o.get("type") == "assistant":
        acts = tuple(_action(c) for c in content if isinstance(c, dict) and c.get("type") == "tool_use")
        return Turn(_parse_ts(ts), sid, "assistant", "", acts, **common)
    done = tuple(str(c.get("tool_use_id")) for c in content if isinstance(c, dict) and c.get("type") == "tool_result"
                 and c.get("tool_use_id"))
    return Turn(_parse_ts(ts), sid, "other", results=done, **common)


def _transcript_dirs(root: pathlib.Path, path: str, worktrees: bool) -> list:
    """This working directory's transcript folders; worktrees=True adds the sessions Claude Code opened in
    <path>/.claude/worktrees/<name> (folder name <slug>--claude-worktrees-<name>)."""
    slug = slug_for_path(str(path))
    return [root / slug, *(sorted(root.glob(slug + "--claude-worktrees-*")) if worktrees else ())]


def turns(paths, since: datetime.datetime, until: datetime.datetime | None = None, worktrees: bool = False,
          subagents: bool = False):
    # a session's subagents are in <session>/subagents/*.jsonl beside it
    globs = ("*.jsonl", "*/subagents/*.jsonl") if subagents else ("*.jsonl",)
    for root, path in ((r, p) for r in transcript_roots() for p in paths):
        for f in (f for d in _transcript_dirs(root, path, worktrees) for g in globs for f in d.glob(g)):
            if datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc) < since:
                continue  # not written to since before the range started
            parsed = recognised = 0
            with f.open(errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    parsed += 1
                    if not isinstance(o, dict) or o.get("type") not in _KNOWN:
                        continue
                    recognised += 1
                    t = _turn(o, f.stem)
                    if t and t.ts >= since and (until is None or t.ts < until):
                        yield t
            if parsed >= 5 and recognised == 0:
                raise TranscriptFormatError(_("{file}: {n} {n:record|records}, none in a known format (did Claude Code "
                                              "change its transcript format?)", file=f, n=parsed))


def last_activity(path) -> datetime.datetime | None:
    ts = [f.stat().st_mtime for root in transcript_roots()
          for f in (root / slug_for_path(str(path))).glob("*.jsonl")]
    return datetime.datetime.fromtimestamp(max(ts), datetime.timezone.utc) if ts else None


# ---------- a single run (runtime_ports' agent interface) ----------
# Claude Code's `claude -p` flags are assembled only here. The permission_mode / allowed_tools / tools / prompts /
# extra_args / allow_from_env / extra_allow_env in the judgment point declaration (Task.spec) are Claude-specific and
# read here for now, until layer 2's neutral policy is settled.

import os  # noqa: E402

from runtime_ports import TaskResult  # noqa: E402


def _env(task) -> dict:
    """claude's environment variables: PATH gets nvm's default-version bin added. claude itself can be called by its
    full path, but plugin hooks (e.g. vercel's SessionEnd `node …`) find node through PATH; under the scheduler PATH
    has no nvm and Homebrew's node is gone too (2026-10-24), so the librarian printed node: command not found every
    time."""
    env = dict(os.environ if task.env is None else task.env)
    try:
        import hubconf
        nvm = hubconf.nvm_bin()
    except Exception:
        nvm = None
    if nvm and str(nvm) not in env.get("PATH", "").split(":"):
        env["PATH"] = f"{nvm}:{env['PATH']}" if env.get("PATH") else str(nvm)
    return env


def _rules(task) -> tuple:
    """(permission mode, allow rules, permission prompts) the run gets: the declaration's, the caller's additions, and
    a registry job's allow list (which makes it unattended)."""
    spec = task.spec
    mode, allowed, prompts = spec.get("permission_mode"), list(spec.get("allowed_tools") or []), spec.get("prompts")
    # the judgment point declares "the platform's mailbox commands": the old hubbus path + the agenthub mail
    # subcommands on PATH
    if "{platform_mail}" in allowed:
        import policy
        i = allowed.index("{platform_mail}")
        hub_bin = pathlib.Path(__file__).resolve().parent
        allowed[i:i + 1] = [f"Bash({_tilde(hub_bin / 'hubbus')} *)", *_cli_rules(policy.platform_cli(mail_only=True))]
    env = os.environ if task.env is None else task.env
    if spec.get("extra_allow_env") and env.get(spec["extra_allow_env"]):
        allowed += json.loads(env[spec["extra_allow_env"]])  # adds rules only, doesn't change the permission mode
    if spec.get("allow_from_env") and env.get(spec["allow_from_env"]):
        # the allow list a registry job declares: unattended = dontAsk, anything not listed is refused without a prompt
        allowed += json.loads(env[spec["allow_from_env"]])
        mode, prompts = "dontAsk", "none"
    return mode, allowed, prompts


def _flags(task) -> list:
    spec = task.spec
    cmd = [_exe(), "-p", task.prompt]   # the scheduler's PATH has no nvm: PATH first, then the platform's (nvm default)
    if task.model:
        cmd += ["--model", task.model]
    mode, allowed, prompts = _rules(task)
    env = os.environ if task.env is None else task.env
    if mode:
        cmd += ["--permission-mode", mode]
    if prompts:
        cmd += ["--permission-prompts", prompts]
    if spec.get("settings") == "platform":
        # --restricted: no user/project/local settings files (only --settings applies), and the tools that run code
        # are off unless --tools names them; the tools named are the ones the rules use, plus reading
        project = env.get(spec.get("project_env") or "")
        if not project:
            raise ValueError(f"judgment point {task.point}: settings: platform needs the project in "
                             f"${spec.get('project_env')}")
        # built-in tools only (MCP tools aren't named in --tools)
        tools = {"Read", "Glob", "Grep", "Bash"} | {r.split("(", 1)[0] for r in allowed if not r.startswith("mcp__")}
        cmd += ["--restricted", "--settings", str(platform_settings(project)), "--tools", ",".join(sorted(tools))]
    elif "tools" in spec:
        cmd += ["--tools", spec["tools"]]
    if allowed:
        cmd += ["--allowedTools", *allowed]
    if task.deny_servers:
        # a whole server: `mcp__<id>` with no tool matches every tool it has (deny wins over any allow), and a tool
        # denied this way isn't even offered to the model (live probe 11-01). The built-in resource tools reach any
        # connected server's resources by name, past those rules: off too
        cmd += ["--disallowedTools", *(f"mcp__{s}" for s in task.deny_servers), *MCP_RESOURCE_TOOLS]
    return cmd + list(spec.get("extra_args") or []) + list(task.extra or [])


# ---------- the environment's MCP servers (runtime_ports' tool_servers interface) ----------
# A run without --strict-mcp-config loads every MCP server configured where it runs: the user's (~/.claude.json
# mcpServers), the folder's (local scope in ~/.claude.json, .mcp.json), plugins', the managed file's, and with the
# subscription login the account's claude.ai connectors (fetched from the account; nowhere on disk). Turning tools off
# doesn't stop them loading (10-27 experiment: 265 MCP tools, execute_sql on a production database among them).
SEAL = "--strict-mcp-config"   # only the servers --mcp-config gives
MCP_RESOURCE_TOOLS = ("ListMcpResourcesTool", "ReadMcpResourceTool", "ReadMcpResourceDirTool")
MCP_LIST_TIMEOUT_S = 180       # `claude mcp list` health-checks every server (each has its own connect timeout)
_LISTED = re.compile(r"^(.+?): .* - .+$")   # `<name>: <url or command> - <status>`; names have no ": " in them
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def mcp_id(name: str) -> str:
    """A server's name → its id in tool names (mcp__<id>__<tool>) and in permission rules: Claude Code turns every
    character outside [a-zA-Z0-9_-] into _ (`claude.ai Upwork` → claude_ai_Upwork, `plugin:vercel:vercel` →
    plugin_vercel_vercel)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def _sealed(task) -> bool:
    return SEAL in [*(task.spec.get("extra_args") or []), *(task.extra or ())]


def parse_mcp_list(out: str) -> list[str]:
    """`claude mcp list`'s output → the server names it lists. Anything not in the shape it has today → ValueError
    (fail closed: the deny is never computed from a list that was only half read)."""
    lines = [_ANSI.sub("", line).rstrip() for line in out.splitlines()]
    if any(line.startswith("No MCP servers configured") for line in lines):
        return []
    start = next((i for i, line in enumerate(lines) if line.startswith("Checking MCP server health")), None)
    if start is None:
        raise ValueError(_("`claude mcp list` printed no server list: {output}", output=out.strip()[:200]))
    names, begun = [], False
    for line in lines[start + 1:]:
        if not line.strip():
            if begun:
                break   # what follows (config diagnostics) isn't the list
            continue
        begun = True
        m = _LISTED.match(line)
        if not m:
            raise ValueError(_("cannot read this line of `claude mcp list`: {line}", line=line[:200]))
        names.append(m.group(1))
    return names


def _listed_servers(task) -> list[str]:
    """The runtime's own answer, for this run: `claude mcp list` in the run's folder with the run's environment (login,
    config directory): the only place the account's connectors show up. It health-checks each server (connects and
    lists its tools, calls none), as the run itself would on starting."""
    try:
        r = subprocess.run([_exe(), "mcp", "list"], cwd=task.cwd or None, env=_env(task), capture_output=True,
                           text=True, timeout=MCP_LIST_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ValueError(_("`claude mcp list` took over {seconds} seconds", seconds=MCP_LIST_TIMEOUT_S)) from None
    except OSError as e:
        raise ValueError(_("cannot run `claude mcp list`: {err}", err=e)) from None
    if r.returncode != 0:
        raise ValueError(_("`claude mcp list` failed (exit {code}): {detail}", code=r.returncode,
                           detail=(r.stderr.strip() or r.stdout.strip())[-200:]))
    return parse_mcp_list(r.stdout)


def _json_file(path: pathlib.Path) -> dict:
    """A config file: missing = empty; present but unreadable or not an object → ValueError."""
    try:
        d = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise ValueError(_("cannot read {file}: {err}", file=path, err=e)) from None
    if not isinstance(d, dict):
        raise ValueError(_("cannot read {file}: not a JSON object", file=path))
    return d


def _names(d) -> list[str]:
    servers = d.get("mcpServers") if isinstance(d, dict) else None
    return list(servers) if isinstance(servers, dict) else []


MANAGED_MCP = (pathlib.Path("/Library/Application Support/ClaudeCode/managed-mcp.json"),
               pathlib.Path("/etc/claude-code/managed-mcp.json"))


def configured_servers(cwd, env) -> tuple[list[str], list[str]]:
    """(the user scope's servers, every server named) in the config files: ~/.claude.json (or $CLAUDE_CONFIG_DIR's)
    mcpServers and every project's local ones (more than this run loads: denying a server that isn't loaded changes
    nothing), .mcp.json in the run's folder and each folder above it, and the managed file. Plugins' servers and the
    account's connectors aren't in these files: `claude mcp list` has them."""
    base = pathlib.Path(env.get("CLAUDE_CONFIG_DIR") or env.get("HOME") or pathlib.Path.home()).expanduser()
    top = _json_file(base / ".claude.json")
    user = _names(top)
    found = list(user)
    for proj in (top.get("projects") or {}).values():
        found += _names(proj)
    here = pathlib.Path(cwd).resolve()
    for d in (here, *here.parents):
        found += _names(_json_file(d / ".mcp.json"))
    for f in MANAGED_MCP:
        found += _names(_json_file(f))
    return user, found


def ambient_servers(task) -> list[str] | None:
    """The MCP servers this run would load from its environment, as ids (mcp_id): None when it carries
    --strict-mcp-config. The union of what `claude mcp list` says for the run and what the config files name; a user
    server the list doesn't show means its output changed under us → ValueError, as does anything unreadable."""
    if _sealed(task):
        return None
    env = _env(task)
    user, configured = configured_servers(task.cwd or os.getcwd(), env)
    listed = _listed_servers(task)
    if missing := sorted(set(user) - set(listed)):
        raise ValueError(_("`claude mcp list` does not show {names}, configured for the user: its output may have "
                           "changed", names=", ".join(missing)))
    return sorted({mcp_id(n) for n in (*configured, *listed)})


def servers_named(task) -> list[str]:
    """The servers whose tools the run's allow rules name (`mcp__<id>` or `mcp__<id>__<tool>`), as ids."""
    out = set()
    for rule in _rules(task)[1]:
        parts = rule.split("(", 1)[0].split("__")
        if parts[0] == "mcp" and len(parts) > 1 and parts[1]:
            out.add(mcp_id(parts[1]))
    return sorted(out)


def describe(task) -> list:
    cmd = _flags(task) + ["--output-format", "json"]
    if task.output_schema is not None:
        cmd += ["--json-schema", json.dumps(task.output_schema, ensure_ascii=False)]
    return cmd


def run(task) -> TaskResult:
    cmd = describe(task)
    try:
        r = subprocess.run(cmd, cwd=task.cwd or None, capture_output=True, text=True, timeout=task.timeout_s,
                           env=_env(task))
    except subprocess.TimeoutExpired:
        return TaskResult(False, error=_("over {minutes} {minutes:minute|minutes}", minutes=task.timeout_s // 60))
    except FileNotFoundError:
        return TaskResult(False, error=_(NOT_FOUND))
    import sys
    sys.stderr.write(r.stderr)
    if r.returncode != 0 or not r.stdout.strip():
        # claude -p errors (e.g. Login expired) are often only in the JSON on stdout
        return TaskResult(False, error=_("exit {code}: {detail}", code=r.returncode,
                                                 detail=(r.stderr.strip() or r.stdout.strip())[-300:]))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return TaskResult(False, error=_("output is not JSON: {output}", output=r.stdout[:200]))
    # exit 0 with readable JSON = it ran to the end (ok). is_error only marks it incomplete: llm.py always passed it on
    # to the caller as is, and the librarian relies on that ("still goes by the actual file changes"), so it can't
    # become a failure
    return TaskResult(
        ok=True, incomplete=bool(out.get("is_error")), text=str(out.get("result") or ""),
        structured_output=out.get("structured_output"), cost_usd=out.get("total_cost_usd"),
        session_id=out.get("session_id"), turns=out.get("num_turns"), stop=str(out.get("subtype") or ""),
        denials=tuple(out.get("permission_denials") or ()))


def exec_stream(task) -> None:
    """A long-running streaming session (for channel leases): assemble the flags as declared and exec into claude, with
    stdin/stdout handed to the caller.
    Temporary: layer 1's open_session will replace it (who does the lease contract neutralisation m10251446 isn't
    decided yet), so it isn't in the interface."""
    cmd = _flags(task)
    cmd[1:3] = ["-p"]  # no prompt argument; messages come from stdin (stream-json)
    cmd += ["--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
    os.execvpe(cmd[0], cmd, _env(task))


# ---------- events (runtime_ports' lifecycle interface) ----------
# Claude Code's hooks: event names, matchers, the input JSON and the hookSpecificOutput output format are only here.
# The platform side has a single entry point `bin/hub-event <platform event>`; handlers return neutral text or a reason
# to refuse.

from runtime_ports import HubEvent  # noqa: E402

# (Claude hook event, matcher, platform event); matcher None = every occurrence of the event triggers it
HOOK_TABLE = (
    ("SessionStart", None, "session_start"),
    ("SessionEnd", None, "session_end"),
    ("PreCompact", None, "pre_compact"),
    ("UserPromptSubmit", None, "prompt"),
    ("PostToolUse", "Bash", "tool_used"),                                    # only "run a command" for now
    ("PreToolUse", "mcp__ccd_session__start_session", "open_session_attempt"),  # Desktop's open-session tool
)
_HOOK_NAME = {ev: name for name, _matcher, ev in HOOK_TABLE}
# A platform-managed hook (the user's own never look like these): one calling the platform's entry point, wherever the
# hub was when it was written (so a hub that moved replaces its old ones instead of adding a second set), or one of the
# hooks written before the entry point, which all named the hub's scripts under an agent-hub folder.
MANAGED = re.compile(r"/bin/hub-event(?=['\s]|$)|/agent-hub/bin/")


NAME = "claude"   # this runtime's name in runtime_ports.RUNTIMES: the hooks say whose they are


def hook_command(event: str) -> str:
    # as before: if the entry point is missing, quietly do nothing; the exit code is always 0, so the session never
    # shows an error. The hub's location is resolved now (hubconf.hub_bin); after the hub moves, installing again
    # rewrites it. --runtime: hub-event parses the input with this runtime's lifecycle and records the session in the
    # platform's ids (docs/design/mailbox-runtimes.md A1)
    import hubconf
    return (f"[ -x {hubconf.hub_bin('hub-event')} ] && {hubconf.sh_py(hubconf.MAIN / 'bin' / 'hub-event')} "
            f"--runtime {NAME} {event} || true")


def parse_event(event: str, raw: str):
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None
    session = data.get("session_id")
    if not session:  # no hook JSON = no real session running (onboard/recover also run the setup)
        return None
    cwd = data.get("cwd") or os.getcwd()
    ti = data.get("tool_input") or {}
    if event == "tool_used":
        return HubEvent(event, session, cwd, command=str(ti.get("command", "")))
    if event == "open_session_attempt":
        elsewhere = None  # a neutral kind (the platform writes the wording): repository / machine / unknown_place
        if ti.get("repository"):
            elsewhere = {"kind": "repository", "name": str(ti["repository"])}
        elif ti.get("host") or ti.get("target") == "ssh":
            elsewhere = {"kind": "machine"}
        elif ti.get("preset"):
            elsewhere = {"kind": "unknown_place"}
        target_cwd = os.path.expanduser(ti["cwd"]) if ti.get("cwd") else None
        return HubEvent(event, session, cwd, target={"cwd": target_cwd, "elsewhere": elsewhere})
    return HubEvent(event, session, cwd)


def inject(event: str, text: str) -> str:
    if not text:
        return ""
    if event == "session_start":
        return text  # SessionStart's stdout goes straight into context
    if event in ("prompt", "tool_used"):
        return json.dumps({"hookSpecificOutput": {"hookEventName": _HOOK_NAME[event], "additionalContext": text}},
                          ensure_ascii=False)
    return ""


def deny(event: str, reason: str) -> str:
    if event != "open_session_attempt":
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}, ensure_ascii=False)


def install_hooks(settings: dict) -> dict:
    """Platform-managed hooks (MANAGED in the command) are replaced as a set by HOOK_TABLE's hub-event; the user's own
    stay; rerunning gives the same result."""
    hooks = settings.setdefault("hooks", {})
    for name in list(hooks):
        kept = []
        for entry in hooks[name]:
            inner = [h for h in entry.get("hooks", []) if not MANAGED.search(str(h.get("command", "")))]
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[name] = kept
        else:
            del hooks[name]
    for name, matcher, event in HOOK_TABLE:
        entry = {"hooks": [{"type": "command", "command": hook_command(event)}]}
        if matcher:
            entry = {"matcher": matcher, **entry}
        hooks.setdefault(name, []).append(entry)
    return settings


def install_lifecycle(workspace, check: bool = False) -> bool:
    path = settings_path(workspace)
    settings = json.loads(path.read_text()) if path.exists() else {}
    out = json.dumps(install_hooks(settings), indent=2) + "\n"
    changed = not path.exists() or path.read_text() != out
    if changed and not check:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(out)
    return changed



# ---------- control files (runtime_ports' control interface) ----------
# What Claude Code loads settings, hooks and code from inside a project folder (its docs: sandboxing, "protected
# paths"; hooks, "hook locations"). The session's own sandbox already keeps its Bash off them; the platform's sandbox
# for a project's services and jobs, and its Edit rules, use this list too (policy.py protect).
CONTROL = ((".claude", False), (".claude/settings.json", True), (".claude/settings.local.json", True),
           (".claude/hooks", True), (".claude/skills", True), (".claude/agents", True), (".claude/commands", True),
           (".mcp.json", True))
WORKTREES = ".claude/worktrees"


def control_paths() -> list[tuple[str, bool]]:
    return list(CONTROL)


def session_folders(workspace) -> list[pathlib.Path]:
    """The folder and its worktrees (the app makes them under .claude/worktrees): real folders only, no links."""
    import os
    ws = pathlib.Path(workspace)
    out = [ws]
    try:
        with os.scandir(ws / WORKTREES) as it:
            out += [ws / WORKTREES / e.name for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        pass
    return out


def foreign_controls(workspace) -> list[str]:
    """Committed hooks or tool servers its sessions would load: the platform can't remove a committed file, so these
    are only reported (read without following links: nofollow.py)."""
    import nofollow
    ws, out = pathlib.Path(workspace), []
    try:
        raw = nofollow.read(ws, ".claude/settings.json")
        doc = json.loads(raw) if raw else {}
        if isinstance(doc, dict) and doc.get("hooks"):
            out.append(_("{file} declares hooks", file=".claude/settings.json"))
    except (OSError, ValueError) as e:
        out.append(_("{file} can't be read as a plain file ({why})", file=".claude/settings.json", why=e))
    if os.path.lexists(ws / ".mcp.json"):
        out.append(_("{file} declares tool servers", file=".mcp.json"))
    for kind in ("agents", "skills"):
        for f in sorted((ws / ".claude" / kind).rglob("*.md")) if (ws / ".claude" / kind).is_dir() else []:
            if not f.is_symlink() and re.search(r"(?m)^hooks:", f.read_text(errors="replace")[:4000]):
                out.append(_("{file} declares hooks", file=str(f.relative_to(ws))))
    return out


def platform_hooks() -> dict:
    """The hooks a sandboxed project's settings hold: the platform's only (render-project-settings writes them)."""
    return install_hooks({})["hooks"]


# ---------- rules file (runtime_ports' instructions interface) ----------

def instructions_file(workspace) -> pathlib.Path:
    """The local file Claude Code loads along with CLAUDE.md (untracked); the platform's managed block goes here."""
    return pathlib.Path(workspace) / "CLAUDE.local.md"


def local_ignores() -> list:
    return ["CLAUDE.local.md", ".claude/settings.local.json"]


# ---------- translating a long-running session's output (stream interface; docs/design/lease-contract-neutral.md) ----

from runtime_ports import StreamEvent  # noqa: E402

_TOOL_KIND = {"Read": "read", "Glob": "search", "Grep": "search", "WebSearch": "search", "WebFetch": "open_web",
              "Edit": "write_file", "Write": "write_file", "MultiEdit": "write_file", "NotebookEdit": "write_file",
              "Bash": "run_command", "AskUserQuestion": "ask_user"}
_TARGET_KEYS = ("file_path", "notebook_path", "url", "command", "pattern", "query", "path")
_QUOTA_STATUS = {"allowed": "ok", "allowed_warning": "warning", "rejected": "exhausted"}
RELAY_SERVER = "lease_relay"                      # the name in the settings of the lease's MCP server (lease-relay.py)
APPROVE_TOOL = f"mcp__{RELAY_SERVER}__approve"     # permission relay (--permission-prompt-tool)
SHOW_FILE_TOOL = f"mcp__{RELAY_SERVER}__show_file"  # platform tool show_file (llm-points' allow list uses this name)


def _verbs() -> dict:
    return {"read": _("read"), "search": _("search"), "open_web": _("open web page"), "write_file": _("write"),
            "run_command": _("run")}


def _windows() -> tuple:
    return (("five_hour", _("5 hours")), ("seven_day", _("7 days")))


def _questions(inp: dict) -> str:
    return " / ".join(q.get("question", "") for q in inp.get("questions") or [])[:200]


def action(tool: str, inp: dict | None) -> tuple:
    """Claude's tool → (kind, collab, target, summary). target is for judging (path, URL, command), summary a sentence
    for people."""
    inp = inp or {}
    kind = _TOOL_KIND.get(tool, "other")
    collab = {"Agent": "subagent", "Task": "subagent", "Workflow": "workflow", "Skill": "skill"}.get(tool) or (
        "peer_message" if tool == "SendMessage" or tool.endswith("send_message") else None)
    target = next((str(inp[k]) for k in _TARGET_KEYS if inp.get(k)), "")
    if tool == SHOW_FILE_TOOL:  # the platform's "show the user" tool (the lease's MCP): read a file to show them
        return "read", None, str(inp.get("path", "")), _("showing you {path}", path=inp.get("path", "")).strip()
    if kind == "ask_user":
        summary = _("question: {questions}", questions=_questions(inp))
    elif kind == "other":
        summary = f"{tool} {target[:160]}".strip()
    else:
        summary = f"{_verbs()[kind]} {target[:160]}".strip()
    return kind, collab, target, summary


def _iso(epoch) -> str | None:
    import datetime
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds") if epoch else None


def parse_stream(line: str) -> list:
    """One line of Claude Code's stream-json output (`--output-format stream-json`) → neutral conversation events.
    Unrecognised lines return []."""
    try:
        d = json.loads(line)
    except ValueError:
        return []
    if not isinstance(d, dict):
        return []
    t, out = d.get("type"), []
    if t == "stream_event":
        ev = d.get("event") or {}
        delta = ev.get("delta") or {}
        if ev.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            out.append(StreamEvent("text_delta", {"text": delta.get("text", "")}))
    elif t == "assistant":
        for block in (d.get("message") or {}).get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                out.append(StreamEvent("assistant", {"text": block["text"]}))
            elif block.get("type") == "tool_use":
                name, inp = block.get("name", ""), block.get("input") or {}
                kind, _collab, _target, summary = action(name, inp)
                out.append(StreamEvent("tool_use", {"kind": kind, "summary": summary, "detail": {"tool": name}}))
    elif t == "user":
        content = (d.get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                c = block.get("content")
                text = c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
                out.append(StreamEvent("tool_result", {"is_error": bool(block.get("is_error")), "summary": text[:200],
                                                       "truncated": len(text) > 200}))
    elif t == "rate_limit_event":
        info = d.get("rate_limit_info") or {}
        w = info.get("unifiedWindows") or {}
        windows = [{"id": wid, "label": label, "used": (w.get(wid) or {})["utilization"],
                    "resets_at": _iso((w.get(wid) or {}).get("resetsAt"))}
                   for wid, label in _windows() if (w.get(wid) or {}).get("utilization") is not None]
        out.append(StreamEvent("quota", {"status": _QUOTA_STATUS.get(info.get("status")), "windows": windows}))
    elif t == "system" and d.get("subtype") == "post_turn_summary":
        out.append(StreamEvent("turn_end", {"summary": d.get("status_detail", "")}))
    elif t == "result":
        text = str(d.get("result") or "")
        # the usage-limit wording in any language claude may write it in (the catalog's variants)
        if d.get("is_error") and re.search("|".join([r"rate.?limit", *map(re.escape, _.variants("usage limit"))]),
                                           text, re.I):
            out.append(StreamEvent("error", {"error": "rate_limit", "detail": text[:300]}))
        elif d.get("subtype") == "error_max_turns":
            out.append(StreamEvent("error", {"error": "bad_request", "detail": _("turn limit reached; detaching the lease"),
                                             "_detach": "budget"}))
        out.append(StreamEvent("turn_end", {"cost": d.get("total_cost_usd"), "num_turns": d.get("num_turns"),
                                            "duration_ms": d.get("duration_ms"), "is_error": bool(d.get("is_error"))}))
    return out


def classify_exit(code, stderr_tail: str) -> StreamEvent:
    raw = _("exit {code}: {detail}", code=code, detail=stderr_tail[-200:])
    if is_login_error(stderr_tail):  # the same login check as llm.py's (layer 6)
        return StreamEvent("error", {"error": "agent_login", "detail": _(
            "Claude Code (used by project sessions) is not logged in, or its credentials have expired (the platform's "
            "agent-hub/claude-login sends an alert). {raw}", raw=raw)})
    return StreamEvent("error", {"error": "process_crashed", "detail": raw})


def resumable(sid: str) -> bool:
    """Resumable = the transcript still exists (Claude Code writes it only after the first turn finishes, and it's
    cleaned up once the retention period passes)."""
    return transcript(sid) is not None


def resume_command(sid: str) -> tuple:
    import hubconf
    return (f"{hubconf.hub_bin('lease-resume')} {sid}",
            _("Paste it in a terminal on the hub machine: it resumes the same conversation (Claude Code). When you are "
              'done, go back to the assistant and press "detach" for the handover.'))


def neutral_question(inp: dict) -> dict:
    """AskUserQuestion's input → the generic question structure {questions: [{question, options: [{label,
    description?}], multi}]}."""
    qs = []
    for q in (inp or {}).get("questions") or []:
        opts = [{"label": o.get("label", ""), **({"description": o["description"]} if o.get("description") else {})}
                for o in q.get("options") or [] if isinstance(o, dict)]
        qs.append({"question": q.get("question", ""), "options": opts, "multi": bool(q.get("multiSelect"))})
    return {"questions": qs}


def native_answers(answers: dict) -> dict:
    """Generic answers {question: label or [label…]} → the {question: "label"} AskUserQuestion wants (multiple
    choices joined with commas)."""
    return {q: (", ".join(a) if isinstance(a, list) else a) for q, a in (answers or {}).items()}


# ---------- auth (layer 6, login) ----------
# Three logins: the subscription login (keychain; readable under launchd, not under SSH), the headless token (`claude
# setup-token`, one year, the default for unattended use), ~/.claude/.credentials.json (for interactive claude under
# SSH). Paths are computed at call time (tests swapping HOME swap them).
# No value is ever read or printed; only auth_env puts the token into the environment variables handed to claude.

from runtime_ports import Credential  # noqa: E402

TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"
_LOGIN_ERROR = re.compile(r"login|401|unauthori[sz]ed|oauth|invalid api key|authentication|credential|expired", re.I)


def _token_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / "headless-oauth-token"


def _creds_file() -> pathlib.Path:
    return pathlib.Path.home() / ".claude" / ".credentials.json"


def install_info() -> dict:
    """What `agenthub init`'s agent step needs to offer Claude Code (docs/design/newcomer-path.md B4): how to find it,
    how to install it (through nvm only: the platform's one copy, kept current by update-claude), its credential for
    unattended runs, and the settings that make it the platform's agent. The steps are shell lines run on the owner's
    yes, user level only."""
    return {"label": "Claude Code",
            "gives": _("everything: session hooks, the mailbox in your sessions, background work"),
            "find": "claude",
            "install": ['[ -s "$HOME/.nvm/nvm.sh" ] || curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash',
                        '. "$HOME/.nvm/nvm.sh" && nvm install --lts && nvm alias default "lts/*" '
                        '&& npm install -g @anthropic-ai/claude-code'],
            "credential": {"kind": "pasted-token", "command": ["claude", "setup-token"], "file": str(_token_file()),
                           "needs": _("a Claude Pro, Max, Ultra, Team or Enterprise plan")},
            # user scope: ~/.claude.json, which the Desktop app's Code tab reads too
            "mcp_register": ["claude", "mcp", "add", "--scope", "user", "agenthub", "--", "{agenthub}", "mcp"],
            "settings": {"runtime": {"default": "claude"}, "modules": {"claude": True}}}


def clean_env(env) -> dict:
    return {k: v for k, v in env.items() if k != TOKEN_VAR}


def auth_env(mode: str, env) -> tuple:
    out = clean_env(env)
    if mode != "token":  # subscription: no token; claude uses the keychain login itself
        return out, ""
    f = _token_file()
    if not f.is_file():
        return None, _("{file} does not exist (the default is the headless token; to use the subscription login, "
                       "declare auth: subscription)", file=f)
    out[TOKEN_VAR] = f.read_text().strip()
    return out, ""


def credentials() -> list:
    token, creds = _token_file(), _creds_file()
    out = [Credential(name=_("subscription login (launchd)"), label=_("claude's subscription login"), mode="subscription",
                      present=True, renew=_("log in on the hub machine (`claude` → /login)")),
           Credential(name="headless token", label="headless token", mode="token", present=token.exists(),
                      renew=_("rerun `claude setup-token` and write it to {path}", path=token), path=str(token),
                      since=datetime.datetime.fromtimestamp(token.stat().st_mtime) if token.exists() else None,
                      max_age=datetime.timedelta(days=335), lifetime=_("one year"))]
    expires, note, present = None, None, creds.exists()
    if present:
        try:
            oauth = json.loads(creds.read_text()).get("claudeAiOauth") or {}
        except ValueError:
            oauth = {}
        exp = oauth.get("refreshTokenExpiresAt")
        if exp:
            expires = datetime.datetime.fromtimestamp(exp / 1000)
        elif oauth:
            note = _(".credentials.json: no refreshTokenExpiresAt, expiry check skipped")
    out.append(Credential(name=_(".credentials.json (for SSH)"), label="~/.claude/.credentials.json", mode=None,
                          present=present, renew=_("run claude once in a terminal on the hub machine so it refreshes, "
                                                   "or /login again"),
                          path=str(creds), expires=expires, warn_before=datetime.timedelta(days=7),
                          used_by=_("used by claude under SSH"), note=note))
    return out


def is_login_error(text: str) -> bool:
    """claude's login errors (Login expired, /login, 401, OAuth…); claude itself not being found also needs a person,
    so it counts too."""
    text = text or ""
    # the not-found message comes from another process (llm.py's JSON), maybe in another language: the msgid or any
    # catalog's text
    return bool(_LOGIN_ERROR.search(text)) or any(s in text for s in _.variants(NOT_FOUND))


def relocate(sid: str, old_cwd: str, new_cwd: str) -> bool:
    """The folder moved (rename, upgrade): Claude Code stores transcripts by the folder's slug, and `--resume` looks
    only in the current folder's slot, so move <sid>.jsonl (and the subagents' <sid>/) to the new folder's slot.
    Already in the new place = True; found nowhere = False."""
    import shutil
    if not re.fullmatch(r"[0-9a-f-]{36}", sid or ""):
        return False
    old_dir, new_dir = PROJECTS / slug_for_path(str(old_cwd)), PROJECTS / slug_for_path(str(new_cwd))
    if (new_dir / f"{sid}.jsonl").exists():
        return True
    if not (old_dir / f"{sid}.jsonl").exists():
        return False
    new_dir.mkdir(parents=True, exist_ok=True)
    for name in (f"{sid}.jsonl", sid):
        if (old_dir / name).exists() and not (new_dir / name).exists():
            shutil.move(str(old_dir / name), str(new_dir / name))
    return True


# ---------- completion (runtime_ports' completion interface; docs/design/llm-providers.md) ----------
# A judgment point's "answer only", run by Claude Code: all tools off, no MCP loaded, no session kept; the prompt goes
# in through stdin (argv has a length limit, and "put it all in if it fits" prompts exceed it); runs in an empty temp
# directory: running in a project copy would trigger the project's own hooks (the librarian hit that one).

from runtime_ports import CompletionResult  # noqa: E402


def _exe() -> str:
    try:
        import hubconf
        return hubconf.tool("claude")
    except Exception:
        return "claude"


def complete(req, conf) -> CompletionResult:
    import tempfile
    cmd = [_exe(), "-p", "--tools", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"]
    if req.model:
        cmd += ["--model", req.model]
    if req.system:
        cmd += ["--append-system-prompt", req.system]
    if req.schema is not None:
        cmd += ["--json-schema", json.dumps(req.schema, ensure_ascii=False)]
    env, why = auth_env(conf.get("auth") or "token", os.environ)
    if env is None:
        return CompletionResult(False, stop="error", error=why)
    env["HUB_LLM_POINT"] = req.point   # the process started (hooks included) knows it's a platform headless call
    env = _env(type("T", (), {"env": env})())
    with tempfile.TemporaryDirectory(prefix="hub-completion-") as cwd:
        try:
            r = subprocess.run(cmd, input=req.prompt, cwd=cwd, capture_output=True, text=True, timeout=req.timeout_s, env=env)
        except subprocess.TimeoutExpired:
            return CompletionResult(False, stop="error", error=_("over {seconds} {seconds:second|seconds}", seconds=req.timeout_s))
        except FileNotFoundError:
            return CompletionResult(False, stop="error", error=_(NOT_FOUND))
    try:
        out = json.loads(r.stdout)
    except ValueError:
        return CompletionResult(False, stop="error", error=_("exit {code}: {detail}", code=r.returncode,
                                                                    detail=(r.stderr.strip() or r.stdout.strip())[-300:]))
    usage = out.get("usage") or {}
    tokens_in = sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")) or None
    if r.returncode != 0 or out.get("is_error"):
        text = str(out.get("result") or r.stderr.strip())[-300:]
        return CompletionResult(False, stop="error", error=_("exit {code}: {detail}", code=r.returncode, detail=text),
                                input_tokens=tokens_in,
                                output_tokens=usage.get("output_tokens"), cost_usd=out.get("total_cost_usd"))
    so = out.get("structured_output")
    return CompletionResult(True, text=json.dumps(so, ensure_ascii=False) if so is not None else str(out.get("result") or ""),
                            stop="end", input_tokens=tokens_in, output_tokens=usage.get("output_tokens"),
                            cost_usd=out.get("total_cost_usd"))


def health(conf) -> tuple:
    """No request made (the hourly claude-login already makes real ones): whether claude is there and whether the login
    to use is there."""
    import shutil
    if not shutil.which(_exe()) and not pathlib.Path(_exe()).is_file():
        return False, _(NOT_FOUND)
    env, why = auth_env(conf.get("auth") or "token", {})
    return (env is not None), why
