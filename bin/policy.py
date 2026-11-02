#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""A project's permission policy, computed from the project registry, in runtime-neutral terms only (paths, domains,
capability names).

Translating it into each runtime's settings is other modules' job (design: docs/design/capabilities.md):
  runtime_claude.settings(policy) / lease_settings(policy)   Claude Code's settings
  seatbelt.profile(policy, cap)                               the macOS Seatbelt profile for platform-run commands
                                                              (cap-runner)
  seatbelt.service_profile / landlock.plan                    a sandboxed project's own services and command jobs
                                                              (through isolation.py: macOS / Linux)
No runtime syntax or paths allowed here (checked by tests/test_runtime_boundary.py).

policy(name, doc, index) → {
  workspace:          the project folder (Path or None)
  write:              what may be written beyond the platform defaults (its own runtime, the registry's
                      allow_write; strings starting with ~). Not the shared tool caches: one project could change a
                      package another installs next. The bus directory only when the project's mail doesn't go through
                      its outbox (mail_outbox; docs/design/bus-sender.md): a sandboxed project's letters do, so the
                      bus is in no sandbox
  env:                environment for its sessions, services and jobs: the tool caches (uv, pip, npm, corepack) in its
                      own repo's git dir (<workspace>/.git/agent-hub/cache/<tool>), which only it can write
  protect:            what its sessions load settings, hooks and tools from ([{name, tree}] matched at any depth under
                      the workspace; the runtime's control port): written by nothing of the project's, only by the
                      platform. Empty (and doctor says so) when the runtime can't name them
  other_projects:     other projects' workspace/runtime, and every memory repo that isn't its own: other projects'
                      bare repos and memory copies, the mirrors, anything else under git_origin / memory_copies (no
                      reading or writing; Path)
  deny_write:         the platform's places (hub, bare repos, memory copies, LaunchAgents; Path)
  deny_read:          the raw notes in the memory copies (glob), the platform's secrets (Path; during the move both the
                      old and new locations are blocked), the bus directory (a project whose mail goes through its
                      outbox reads its own letters from the view the platform writes into its folder)
  credentials:        user-level credential directories (Path); credential_files: credential files (Path); the
                      locations are in hub.json's credentials
  read:               platform paths readable although platform state is blocked: the registry index the platform CLI
                      (agenthub send, complete, cap-run) reads, and the worker slots agenthub test locks (Path)
  network:            domains that may be reached (platform defaults + the registry's network)
  local_bind:         may listen on 127.0.0.1 (to test its own server before declaring it a service; 10-31 owner
                      confirmed in the agent-hub session)
  platform_commands:  the only channel across the boundary (mailbox CLI, merge gate; Path: the old form, removed once
                      the rules files use platform_cli)
  platform_cli:       the same channels as agenthub subcommands on PATH (strings, e.g. "agenthub send")
  capabilities:       the registry's enforce.capabilities
}
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import names  # noqa: E402
# registry needs PyYAML: imported only when computing the path policy; approvals() uses only the standard library
# (so lease-relay can import this without yaml)

HOME = pathlib.Path.home()
# each tool's cache (and uv's Python installs and tools) per repo, under its git dir: not shared, never untracked files
CACHE_ENV = {"UV_CACHE_DIR": "uv", "UV_PYTHON_INSTALL_DIR": "uv-python", "UV_TOOL_DIR": "uv-tools",
             "PIP_CACHE_DIR": "pip", "npm_config_cache": "npm", "COREPACK_HOME": "corepack"}
DOMAINS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
           "raw.githubusercontent.com", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]


def tilde(p: pathlib.Path | str) -> str:
    s, h = str(p), str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def project_paths(index: dict) -> dict[str, list[pathlib.Path]]:
    import registry
    out = {}
    for name, doc in index["projects"].items():
        if name == hubconf.hub_project():
            continue
        d = registry.dirs(doc)
        out[name] = [p for p in (d["workspace"], d["runtime"]) if p]
    return out


def secret_dirs() -> list[pathlib.Path]:
    """The folders holding the platform's credentials, all blocked: the current one (hubconf.secrets_dir()), plus the
    old pre-move location if it still exists and isn't the same one: the move only copies, and the old one is deleted
    only once confirmed fine (the 2026-10-26 instance-directory move), so meanwhile both hold real credentials."""
    cur = hubconf.secrets_dir()
    old = hubconf.legacy_secrets_dir()
    return [cur] + ([old] if old.exists() and old.resolve() != cur.resolve() else [])


def memory_bare(name: str, doc: dict) -> pathlib.Path:
    return hubconf.path((doc.get("paths") or {}).get("memory_bare")) \
        or hubconf.p("git_origin", "~/git-origin") / f"{name}-memory.git"


def others_memory(name: str, doc: dict, index: dict) -> list[pathlib.Path]:
    """Every memory repo that isn't this project's: other projects' bare repos and copies (registered, whether they
    exist yet or not), plus whatever else is under git_origin (mirrors, the hub's bare repo, old ones) and
    memory_copies right now."""
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    own = {memory_bare(name, doc), copies / name}
    out = [p for n, d in index["projects"].items() if n != name for p in (memory_bare(n, d), copies / n)]
    for root in (hubconf.p("git_origin", "~/git-origin"), copies):
        out += sorted(root.iterdir()) if root and root.is_dir() else []
    seen, res = set(), []
    for p in out:
        if p not in own and p not in seen:
            seen.add(p)
            res.append(p)
    return res


def protect() -> list[dict]:
    """The control files of every installed runtime that can name them (docs/design/mailbox-runtimes.md A1): the
    default runtime's first, as before; a runtime without the control port adds nothing (its hooks are never installed
    in a sandboxed project)."""
    import runtime_ports
    out = []
    for name in dict.fromkeys([runtime_ports.default_name(), *runtime_ports.installed()]):
        try:
            rt = runtime_ports.load(name, ports=("control",))
        except runtime_ports.Unsupported:
            continue
        out += [x for x in ({"name": n, "tree": bool(t)} for n, t in rt.control_paths()) if x not in out]
    return out


def cache_env(ws: pathlib.Path | None) -> dict[str, str]:
    return {k: str(names.repo_dir(ws / ".git") / "cache" / v) for k, v in CACHE_ENV.items()} if ws else {}


def mail_outbox(doc: dict | None) -> bool:
    """Whether a project's letters go through its outbox instead of the bus (docs/design/bus-sender.md): its sessions
    are sandboxed (enforce.sandbox, as render-project-settings decides it; the services-only exemption doesn't change
    it), so the bus file is in none of its sandboxes and the platform takes its letters from its own folder."""
    return bool(doc) and (doc.get("enforce") or {}).get("sandbox") is True


def policy(name: str, doc: dict, index: dict) -> dict:
    import registry
    d = registry.dirs(doc)
    outbox = mail_outbox(doc)
    others = [p for n, ps in project_paths(index).items() if n != name for p in ps]
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    return {
        "workspace": d["workspace"],
        "write": ([tilde(d["runtime"])] if d["runtime"] else []) + ([] if outbox else [tilde(hubconf.BUS_DIR)])
                 + list(doc.get("allow_write") or []),
        "env": cache_env(d["workspace"]),
        "protect": protect(),
        "other_projects": others + others_memory(name, doc, index),
        "deny_write": [hubconf.ROOT, hubconf.p("git_origin"), copies, HOME / "Library" / "LaunchAgents"],
        "deny_read": [copies / "*" / "memory", *secret_dirs(), *([hubconf.BUS_DIR] if outbox else [])],
        "credentials": [hubconf.path(x) for x in hubconf.get("credentials.dirs", [])],
        "credential_files": [hubconf.path(x) for x in hubconf.get("credentials.files", [])],
        # the registry index; the machine's worker slots (bin/test-runner.py: locked read-only, created by the platform)
        "read": [hubconf.STATE / "registry", hubconf.STATE / "test-slots"],
        "network": DOMAINS + list(doc.get("network") or []),
        "local_bind": True,
        "platform_commands": [hubconf.ROOT / "bin" / "hubbus", hubconf.ROOT / "bin" / "merge-branch"],
        "platform_cli": platform_cli(),
        "capabilities": list((doc.get("enforce") or {}).get("capabilities") or []),
    }


# The platform commands a project's session uses (agenthub on PATH; the same list as bin/agenthub's
# SESSION_COMMANDS): permissions let only these subcommands through, not agenthub's admin commands (config apply,
# onboard…)
MAIL_SUBCOMMANDS = ("send", "inbox", "show", "ack", "done", "thread", "open-questions")
SESSION_SUBCOMMANDS = (*MAIL_SUBCOMMANDS, "merge-branch", "test", "cap-run", "complete")


def platform_cli(mail_only: bool = False) -> list[str]:
    """mail_only: a mailbox agent dispatched by the platform can only read, send and close letters (no merging, no
    platform-run commands)."""
    return [f"agenthub {c}" for c in (MAIL_SUBCOMMANDS if mail_only else SESSION_SUBCOMMANDS)]


# ---------- the environment's tool servers in an unattended run (docs/design/evaluators.md step 0) ----------
# A judgment point declaring `tool_servers: allowed_only`: when its run loads the tool servers configured in its
# environment (a registry job's connectors: true needs the account's connectors, so it can't be held to the platform's
# own servers), every server it would load whose tools its allow list doesn't name is denied, all of its tools.
# What it would load is found by the runtime for that very run (the user's, the project's, plugins' and the account's
# servers), never kept by hand: a server added tomorrow is denied tomorrow. When the runtime can't tell, the run
# doesn't happen (llm.py).

def tool_server_denials(loaded, named) -> list[str]:
    """loaded: the servers this run would load from its environment; named: the servers its allow list names a tool
    of (both in the runtime's own server ids) → the servers to deny whole."""
    named = set(named)
    return sorted({s for s in loaded if s not in named})


def for_project(name: str) -> dict:
    import registry
    index = registry.refresh()
    return policy(name, index["projects"][name], index)


# ---------- which actions need a person to approve (approvals; docs/design/lease-contract-neutral.md) ----------
# Rule values:
#   allow       let through directly      deny        reject directly (no asking)
#   ask         ask the owner (phone); "allow for this whole lease" is possible
#   ask_once    ask the owner, every time ("allow for this whole lease" only counts for this once)
#   inside      let through inside the project folder, reject outside (no asking; paths are resolved to real paths
#               first)
#   listed      open_web domains in web_allowed are let through, others ask; no_session_domains may not be "allowed
#               for this whole lease"
# The work mode decided by the owner on 10-25: writing files inside the folder isn't asked, commands are asked every
# time. unattended: an agent run the platform started with nobody to ask (docs/design/runtime-acp.md); it belongs in
# the throwaway worktree, so these rules are what the agent is told, not the guarantee ("inside" for a read or search
# that names no place means where it was started).
APPROVALS = {
    "discuss": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
                "write_file": "deny", "run_command": "deny", "other": "deny"},
    "work": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
             "write_file": "inside", "run_command": "ask_once", "other": "deny"},
    "unattended": {"read": "inside", "search": "inside", "ask_user": "deny", "open_web": "deny",
                   "write_file": "inside", "run_command": "deny", "other": "deny"},
}


def approvals(name: str, mode: str, doc: dict | None = None) -> dict:
    """{mode, rules: {kind: rule}, web_allowed: [domains], no_session_domains: [domains]}: each runtime hooks it into
    its own permission mechanism."""
    doc = doc if doc is not None else (hubconf.projects().get(name) or {})
    return {"mode": mode, "rules": dict(APPROVALS["work" if mode == "work" else "discuss"]),
            "web_allowed": list(doc.get("network") or []),
            "no_session_domains": list(hubconf.get("lease.no_session_domains", []))}
