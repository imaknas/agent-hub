#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The project registry: <registry_dir>/<name>.yaml (one file per project, directory set in hub.json) → validation →
JSON cache.

The platform's code holds no project names: a project's jobs, services, privacy labels and paths are all in the
registry. The scheduler calls refresh() on every tick: each file is validated on its own; valid ones are written to
.state/registry/<name>.json (the cache of the last valid version), invalid ones keep using the last valid version and
their errors go into index.json's errors (red on the status page), **without affecting other projects**.
index.json is for programs that may only use the standard library (hubconf.py, hubbus).

schema v1 (contracts/project.v1.md has the full description):
  v: 1                      required
  name                      required, = the file name
  type: project | idea      default project. idea = a project the assistant opens to explore an idea (created by
                            bin/create-idea-sandbox.py; the name is the idea's code name, following the ordinary
                            project-name rules (early ones were called idea-<n>); no GitHub remote;
                            bin/upgrade-idea.py upgrades it to project). Whether it's an idea sandbox depends only on
                            this field, not on the name
  purpose                   the idea itself (a sentence or two); required for type: idea, included in the lease's
                            system prompt
  paths: {workspace, memory_bare, origin}       workspace required (except for the platform itself)
  memory: true              takes part in memory tracking
  privacy: {reflection: include|stats_only|exclude}
  runtime: {deploy, test}   has a runtime = has <services_root>/<name>
  push_on_merge: false
  commits: conventional      commit titles use Conventional Commits: merge-branch checks before merging
                            (bin/commit_title.py), and the rules file automatically carries
                            templates/rules/conventional-commits.md. Not set = no check
  mailbox: {escalate: true}
  publishes: [{kind, path, contract, v}]
  provides:  [{contract, v, schema}]           schema relative to workspace
  services:  [{name, run, label?, keepalive?, throttle_s?, env?, cwd?, expose?}]
  enforce: {sandbox: false, capabilities: [], work: false}
                                  phase two: sandbox true = render-project-settings.py writes the sandbox +
                                  permission rules; capabilities = platform capabilities (hub.json capabilities,
                                  e.g. gpu), needs sandbox: true; inside the sandbox, hub-run --cap <capability> runs
                                  things through the platform (docs/design/capabilities.md);
                                  work = a discuss lease may make changes in work mode (writing files inside the
                                  folder, commands asked every time), needs sandbox: true.
                                  The record of the owner's explicit authorization (10-25): written when an idea
                                  sandbox is created, carried over on upgrade; other projects add it only with the
                                  owner's consent
  network: [domains]              what the sandbox needs to reach beyond the platform defaults
  allow_write: [paths]            what the sandbox needs to write beyond the platform defaults
    type, enforce, network, allow_write and privacy are security fields: a manifest.update a project sends that
    changes them is rejected whole (a project can't switch off its own sandbox, widen its scope or add capabilities);
    to change them, ask the agent-hub session (the owner confirms)
  secrets:   [{name, path, env_key?, expires? | max_age_days?, warn_days?, backup?, why?, rotate?}]
                                  the credentials this project uses: only name, location and expiry, **never the
                                  value** (check-secrets.py checks daily that they exist, are backed up, and aren't
                                  about to expire). path is relative to runtime (if there is one) else workspace, or
                                  ~/ or an absolute path; env_key = the file is a .env and the credential is one line
                                  of it (only checks that the key is there); expires is "YYYY-MM-DD" (quote it);
                                  max_age_days counts from the file's modification time (replacing it restarts the
                                  count); backup: false needs a why (e.g. just reissue it)
  gate: strict                    the merge gate enforced: main in the main checkout can't be committed to directly
                                  (only via merge-branch); by default it only detects
  jobs:      [{name, at | every, run | (kind: agent + prompt), cwd?, days?, days_of_month?, months?,
               until?, between?, background?, after?, max_attempts_per_day?, legacy_name?}]
    every: 15m / 1h; cwd: runtime (default, when there's a runtime) | workspace | hub | state (agent jobs only: runs
      in its own state directory, without loading the project's settings/MCP)
    after: [job] (same project) or [project/job]
    connectors: true (agent jobs only): needs claude.ai connectors (Upwork etc.) → uses the subscription login, not
      the default headless token (tools in allow with mcp__claude_ai_ but this not set: invalid)
    legacy_name: the old name in schedule.yaml: while the old name is still in schedule.yaml this job isn't used
                 (during the switch the old one wins; delete that entry from schedule.yaml to switch over; the state
                 moves over from the old name too)
Validation: unknown fields, duplicate names, after pointing at a job that doesn't exist, the first word of
run/deploy/test not found (a file relative to cwd, a command on PATH, or an existing absolute path) all count as
invalid.

Usage: registry.py [check [name...]]   validate and refresh the cache, print the result
       registry.py check --rebuild         rebuild even from another or an empty registry dir (wrong_registry())
       registry.py add <name> <workspace> <origin> <memory_bare> [--extra <JSON>]
                                           add at onboarding (left alone if it exists; extra overrides default
                                           fields; nothing written if validation fails)
       registry.py rename <old> <new> <workspace> <origin> <memory_bare>
                                           rename (the file and the prompt directory move together)
       registry.py apply-updates           apply the manifest.update letters projects sent (platform job, every
                                           15 minutes)
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import seatbelt  # noqa: E402

import yaml  # noqa: E402

_ = i18n.domain(__file__)

CACHE = hubconf.STATE / "registry"
TOP = {"v", "name", "type", "purpose", "paths", "memory", "privacy", "runtime", "push_on_merge", "commits", "mailbox",
       "publishes", "provides", "consumes", "services", "jobs", "enforce", "network", "allow_write", "gate", "secrets"}
SECRET = {"name", "path", "env_key", "expires", "max_age_days", "warn_days", "backup", "why", "rotate"}
JOB = {"name", "at", "every", "run", "kind", "prompt", "cwd", "days", "days_of_month", "months", "until",
       "between", "background", "after", "before", "max_attempts_per_day", "legacy_name", "allow", "connectors"}
SERVICE = {"name", "run", "label", "keepalive", "interval_s", "throttle_s", "env", "cwd", "log", "expose"}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
EVERY = re.compile(r"^(\d+)(m|h)$")


def dirs(doc: dict) -> dict[str, pathlib.Path | None]:
    """This project's runtime / workspace / hub directories."""
    name = doc.get("name", "")
    if name == hubconf.hub_project() or doc.get("type") == "module":
        return {"runtime": hubconf.ROOT, "workspace": hubconf.ROOT, "hub": hubconf.ROOT}
    ws = hubconf.path((doc.get("paths") or {}).get("workspace"))
    rt = hubconf.p("services_root") / name if doc.get("runtime") is not None else None
    return {"runtime": rt, "workspace": ws, "hub": hubconf.ROOT}


def default_cwd(doc: dict) -> str:
    if doc.get("type") == "module":
        return "hub"
    return "runtime" if doc.get("runtime") is not None else "workspace"


def resolves(cmd: str, cwd: pathlib.Path | None, search: str | None = None) -> str | None:
    """Can the first word be found? If not, return an error message."""
    try:
        words = shlex.split(cmd)
    except ValueError as e:
        return _("the command cannot be parsed: {error}", error=e)
    if not words:
        return _("the command is empty")
    first = words[0]
    if "=" in first and not first.startswith(("/", ".")):  # VAR=x cmd
        return None
    if first.startswith("/"):
        if first.startswith(str(pathlib.Path.home())):
            return _("no absolute paths under the home directory ({first}); make it relative to cwd", first=first)
        return None if pathlib.Path(first).exists() else _("{path} does not exist", path=first)
    if "/" in first:
        if cwd is None:
            return _("{first}: there is no cwd to look in", first=first)
        return None if (cwd / first).exists() else _("{path} does not exist", path=cwd / first)
    found = shutil.which(first, path=search or f"{hubconf.LAUNCHD_PATH}:{os.environ.get('PATH', '')}")
    return None if found or first in ("cd", "test", "[", "true") else _("{first} is not on PATH", first=first)


def validate_secrets(secrets) -> list[str]:
    if secrets is None:
        return []
    if not isinstance(secrets, list):
        return [_("secrets must be a list")]
    errs, seen = [], set()
    for i, sec in enumerate(secrets):
        where = f"secrets[{i}]"
        if not isinstance(sec, dict):
            errs.append(_("{where} must be a mapping", where=where))
            continue
        errs += [_("{where}: unknown field {key}", where=where, key=k) for k in sec if k not in SECRET]
        for k in ("name", "path"):
            if not isinstance(sec.get(k), str) or not sec.get(k):
                errs.append(_("{where}: {key} is required", where=where, key=k))
        if sec.get("name") in seen:
            errs.append(_("{where}: name {name} is a duplicate", where=where, name=sec.get("name")))
        seen.add(sec.get("name"))
        if "expires" in sec and not (isinstance(sec["expires"], str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sec["expires"])):
            errs.append(_("{where}: expires must be a quoted \"YYYY-MM-DD\"", where=where))
        if "expires" in sec and "max_age_days" in sec:
            errs.append(_("{where}: give only one of expires and max_age_days", where=where))
        for k in ("max_age_days", "warn_days"):
            if k in sec and (not isinstance(sec[k], int) or isinstance(sec[k], bool) or sec[k] <= 0):
                errs.append(_("{where}: {key} must be a positive integer", where=where, key=k))
        if "backup" in sec and not isinstance(sec["backup"], bool):
            errs.append(_("{where}: backup must be true/false", where=where))
        if sec.get("backup") is False and not sec.get("why"):
            errs.append(_("{where}: backup: false needs a why (why it needs no backup)", where=where))
    return errs


def capabilities() -> dict[str, dict]:
    return hubconf.capabilities()


def validate_enforce(doc: dict) -> list[str]:
    enf = doc.get("enforce")
    if enf is None:
        return []
    if not isinstance(enf, dict) or set(enf) - {"sandbox", "capabilities", "work"} or not isinstance(enf.get("sandbox", False), bool):
        return [_("enforce can only be {{sandbox: true|false, capabilities: [{cap}], work: true|false}}", cap=_("capability"))]
    work = enf.get("work", False)
    if not isinstance(work, bool):
        return [_("enforce.work must be true or false")]
    # a discuss lease can write only because the OS sandbox blocks writes outside the folder
    if work and not enf.get("sandbox"):
        return [_("enforce.work needs enforce.sandbox: true")]
    caps = enf.get("capabilities")
    if caps is None:
        return []
    if not isinstance(caps, list) or not all(isinstance(c, str) for c in caps):
        return [_("enforce.capabilities must be a list of strings")]
    errs, have = [], capabilities()
    errs += [_("enforce.capabilities: the platform has no capability {cap} (it has: {have})",
               cap=c, have=", ".join(sorted(have)) or _("none")) for c in caps if c not in have]
    if caps and not enf.get("sandbox"):
        errs.append(_("enforce.capabilities needs enforce.sandbox: true"))
    # Not based on type: after an idea sandbox is upgraded into a real project, the capabilities the owner agreed to
    # stay with it (10-25 m10251626). The record of that consent is enforce itself: a platform-owned field that the
    # project's own manifest.update can't change (PLATFORM_OWNED); what a capability can do is decided by the
    # platform's Seatbelt profile, regardless of what kind of project it is
    return errs


PLATFORM_OWNED = ("type", "enforce", "network", "allow_write", "privacy")


def platform_owned_changes(new: dict, cur: dict) -> list[str]:
    """Security fields manifest.update can't change: list them when they differ from the current registry entry."""
    return [_("{key} is a security field that only agent-hub can change ({owner} confirms); this letter changes it", key=k,
              owner=hubconf.owner_tag())
            for k in PLATFORM_OWNED
            if new.get(k) != cur.get(k)]


def validate(doc, stem: str) -> list[str]:
    errs: list[str] = []
    if not isinstance(doc, dict):
        return [_("not a YAML mapping")]
    if doc.get("v") != 1:
        errs.append(_("v must be 1 (it is {v!r})", v=doc.get("v")))
    if doc.get("name") != stem:
        errs.append(_("name ({name!r}) must be the same as the file name {stem}", name=doc.get("name"), stem=stem))
    errs += [_("unknown field {key}", key=k) for k in doc if k not in TOP]
    if doc.get("type", "project") not in ("project", "idea"):
        errs.append(_("type can only be project or idea"))
    if doc.get("purpose") is not None and (not isinstance(doc["purpose"], str) or not doc["purpose"].strip()):
        errs.append(_("purpose must be a non-empty string"))
    if doc.get("type") == "idea" and not doc.get("purpose"):
        errs.append(_("type: idea needs a purpose (the idea itself)"))
    is_hub = stem == hubconf.hub_project()
    paths = doc.get("paths") or {}
    if not is_hub and not paths.get("workspace"):
        errs.append(_("paths.workspace is required"))
    errs += validate_enforce(doc)
    if doc.get("commits") not in (None, "conventional"):
        errs.append(_("commits can only be conventional (absent = commit titles are not checked)"))
    if doc.get("gate") not in (None, "strict", "detect"):
        errs.append(_("gate can only be strict or detect (the default)"))
    errs += validate_secrets(doc.get("secrets"))
    for key in ("network", "allow_write"):
        v = doc.get(key)
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v)):
            errs.append(_("{key} must be a list of strings", key=key))
    if (doc.get("privacy") or {}).get("reflection", "include") not in ("include", "stats_only", "exclude"):
        errs.append(_("privacy.reflection can only be include / stats_only / exclude"))
    d = dirs(doc)
    rt = doc.get("runtime")
    if rt is not None:
        if not isinstance(rt, dict):
            errs.append(_("runtime must be a mapping"))
        elif d["runtime"] and not d["runtime"].is_dir():
            errs.append(_("the runtime directory {path} does not exist", path=d["runtime"]))
    for key in ("publishes", "provides", "services", "jobs"):
        if not isinstance(doc.get(key, []), list):
            errs.append(_("{key} must be a list", key=key))
    errs += validate_runs(doc, stem, d)
    for key in ("deploy", "test"):
        if isinstance(rt, dict) and rt.get(key):
            base = d["workspace"] if key == "test" else d["workspace"]
            if (e := resolves(rt[key], base)):
                errs.append(_("runtime.{key}: {error}", key=key, error=e))
    return errs


def validate_runs(doc: dict, stem: str, d: dict) -> list[str]:
    """services and jobs (shared by the project registry and modules)."""
    errs: list[str] = []
    names = set()
    for svc in doc.get("services") or []:
        n = svc.get("name")
        errs += [_("service {name}: unknown field {key}", name=n, key=k) for k in svc if k not in SERVICE]
        if not n or not svc.get("run"):
            errs.append(_("service {name}: name and run are required", name=n))
            continue
        cwd = d.get(svc.get("cwd") or default_cwd(doc))
        if (e := resolves(svc["run"], cwd, hubconf.expand_env((svc.get("env") or {}).get("PATH") or hubconf.LAUNCHD_PATH))):
            errs.append(_("service {name}: {error}", name=n, error=e))
    jobs = doc.get("jobs") or []
    for job in jobs:
        n = job.get("name")
        errs += [_("job {name}: unknown field {key}", name=n, key=k) for k in job if k not in JOB]
        if not n or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", str(n)):
            errs.append(_("job {name!r}: name must be lowercase letters, digits and -", name=n))
            continue
        if n in names:
            errs.append(_("job {name}: duplicate name", name=n))
        names.add(n)
        if ("at" in job) == ("every" in job):
            errs.append(_("job {name}: give exactly one of at and every", name=n))
        if "at" in job and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(job["at"])):
            errs.append(_("job {name}: at must be HH:MM (quoted)", name=n))
        if "every" in job and not EVERY.match(str(job["every"])):
            errs.append(_("job {name}: every must look like 15m / 1h", name=n))
        if set(job.get("days") or []) - DAYS:
            errs.append(_("job {name}: days can only be {days}", name=n, days=sorted(DAYS)))
        cwd_key = job.get("cwd") or default_cwd(doc)
        if cwd_key == "state" and job.get("kind") == "agent":
            # an agent job runs in its own state directory (without loading the project's settings/MCP; e.g. the
            # project's own MCP with the same name would shadow a claude.ai connector)
            pass
        elif cwd_key not in ("runtime", "workspace", "hub"):
            errs.append(_("job {name}: cwd can only be runtime / workspace / hub (an agent job may also use state)", name=n))
            continue
        if job.get("allow") is not None and (not isinstance(job["allow"], list)
                                             or not all(isinstance(x, str) and x for x in job["allow"])):
            errs.append(_("job {name}: allow must be a list of permission rule strings", name=n))
        if "connectors" in job and not isinstance(job["connectors"], bool):
            errs.append(_("job {name}: connectors must be true/false", name=n))
        if any(str(x).startswith("mcp__claude_ai_") for x in job.get("allow") or []) and not job.get("connectors"):
            errs.append(_("job {name}: allow has claude.ai connector tools; add connectors: true (the default headless token "
                           "loads no connectors)", name=n))
        if job.get("kind", "command") == "agent":
            prompt = hubconf.registry_dir() / stem / (job.get("prompt") or f"jobs/{n}.md")
            if not prompt.exists():
                errs.append(_("job {name}: prompt {prompt} does not exist", name=n, prompt=prompt))
        elif job.get("kind", "command") != "command":
            errs.append(_("job {name}: kind can only be command / agent", name=n))
        elif not job.get("run"):
            errs.append(_("job {name}: run is required", name=n))
        elif (e := resolves(job["run"], d.get(cwd_key))):
            errs.append(_("job {name}: {error}", name=n, error=e))
    for job in jobs:
        for key in ("after", "before"):
            v = job.get(key)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x for x in v)):
                errs.append(_("job {name}: {key} must be a list of job names", name=job.get("name"), key=key))
                continue
            for dep in v or []:
                if "/" not in dep and dep not in names:
                    errs.append(_("job {name}: {dep} in {key} is not in this project (another project or module is written "
                                   "<name>/<job>)", name=job.get("name"), key=key, dep=dep))
    return errs


# ---------- modules (docs/design/new-user.md 3.2) ----------
# Optional features (personal assistant, backup, mirror, weekly reflection…): modules/<name>.yaml in the platform
# repo, in the same format as the project registry's jobs/services (cwd is always hub); used only with
# modules.<name>: true in hub.json. In the schedule a job is called <module>/<job>.
# A job moved into a module carries its old full name in legacy_name (e.g. agent-hub/backup), so its schedule state
# moves over and others' after: still match.

MODULES_DIR = hubconf.ROOT / "modules"
MODULE_TOP = {"v", "name", "type", "description", "requires_config", "jobs", "services", "secrets", "consumes"}


def validate_module(doc, stem: str) -> list[str]:
    if not isinstance(doc, dict):
        return [_("not a YAML mapping")]
    errs = [_("unknown field {key}", key=k) for k in doc if k not in MODULE_TOP]
    if doc.get("v") != 1:
        errs.append(_("v must be 1"))
    if doc.get("name") != stem:
        errs.append(_("name ({name!r}) must be the same as the file name {stem}", name=doc.get("name"), stem=stem))
    if doc.get("type") != "module":
        errs.append(_("type must be module"))
    if not isinstance(doc.get("description"), str) or not doc["description"].strip():
        errs.append(_("description is required"))
    req = doc.get("requires_config") or []
    if not isinstance(req, list):
        errs.append(_("requires_config must be a list of hub.json keys"))
    else:
        errs += [_("{key} must be set in hub.json first", key=k) for k in req if hubconf.get(k) in (None, "", {}, [])]
    for key in ("jobs", "services"):
        for x in doc.get(key) or []:
            if isinstance(x, dict) and x.get("cwd", "hub") != "hub":
                errs.append(_("{kind} {name}: a module's cwd can only be hub", kind=key[:-1], name=x.get("name")))
            if key == "jobs" and isinstance(x, dict) and x.get("kind", "command") != "command":
                errs.append(_("job {name}: a module can only have command jobs", name=x.get("name")))
    # the module's own credentials (the platform's; modules that are off aren't checked)
    errs += validate_secrets(doc.get("secrets"))
    # contracts of others the module reads (e.g. the assistant module reads the assistant's handoff format); modules
    # that are off aren't compared
    for c in doc.get("consumes") or []:
        if not isinstance(c, dict) or not all(c.get(k) for k in ("contract", "v", "copy")):
            errs.append(_("each consumes item needs contract, v and copy"))
    return errs + validate_runs(doc, stem, dirs(doc))


def enabled_modules() -> list[str]:
    m = hubconf.get("modules") or {}
    return sorted(k for k, v in m.items() if not k.startswith("_") and v is True)


def load_modules() -> tuple[dict, dict]:
    """(enabled module → doc, errors). Enabled but no file, or invalid: record the error and don't use it (its
    jobs/services then don't exist)."""
    mods, errors = {}, {}
    for name in enabled_modules():
        f = MODULES_DIR / f"{name}.yaml"
        if not f.exists():
            errors[f"module:{name}"] = [_("hub.json enables module {name}, but there is no modules/{name}.yaml", name=name)]
            continue
        try:
            doc = yaml.safe_load(f.read_text())
            errs = validate_module(doc, name)
        except yaml.YAMLError as e:
            doc, errs = None, [_("cannot read the YAML: {error}", error=e)]
        if errs:
            errors[f"module:{name}"] = errs
        else:
            mods[name] = doc
    return mods, errors


def job_cycles(index: dict) -> list[str]:
    """Whether after:/before: chain into a cycle (if so the scheduler would wait forever)."""
    jobs, _skipped, _renames = scheduler_jobs(index, set())
    graph = {j["name"]: set(j.get("after") or []) for j in jobs}
    out, state = [], {}

    def visit(n, path):
        state[n] = 1
        for dep in graph.get(n, ()):
            if dep not in graph:
                continue
            if state.get(dep) == 1:
                out.append(" → ".join(path[path.index(dep):] + [dep]) if dep in path else f"{n} → {dep}")
            elif not state.get(dep):
                visit(dep, path + [dep])
        state[n] = 2
    for n in sorted(graph):
        if not state.get(n):
            visit(n, [n])
    return out


def roster_mismatch(doc, text: str) -> list[str]:
    """The paths hubconf.roster() reads (a line-by-line parse without PyYAML, used by recover, sync-all-memory and
    the roster) must match the YAML, or those scripts silently get empty or wrong paths."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths") or {}, dict):
        return []
    want = {k: (None if v is None else str(v)) for k, v in (doc.get("paths") or {}).items()}
    got = hubconf.roster_paths(text)
    if got == want:
        return []
    return [_("paths must be written in block format, one key per line (`paths:`, then `workspace: …` indented on the "
              "next line): the reader that does not use YAML got {got}, YAML has {want}", got=got, want=want)]


def wrong_registry(old: dict, reg: pathlib.Path, files: list[pathlib.Path]) -> str | None:
    """Why this process must not rebuild the index (None = it may). The index lives in STATE, which follows the hub root,
    not the config: a process with another config (a test's, an empty HOME's) that reaches the real STATE would
    otherwise rebuild it from its own registry and drop every project (10-30, between 11:49 and 11:53, nobody could
    send). So an index that has projects is only rebuilt from the registry dir it was built from, and never from an
    empty one; `registry.py check --rebuild` is for when that really changed."""
    if not any(n != hubconf.hub_project() for n in old.get("projects") or {}):
        return None
    if old.get("registry_dir") not in (None, str(reg.resolve())):
        return _("the registry index was built from {old} and this process sees {dir}; not rebuilt (another config "
                 "reaching this hub's state?). If the registry really moved: registry.py check --rebuild",
                 old=old["registry_dir"], dir=reg)
    if not [f for f in files if f.stem != hubconf.hub_project()]:
        return _("{dir} has no project files, but the registry index lists {n} {n:project|projects}; not rebuilt. If they really "
                 "were all removed: registry.py check --rebuild", dir=reg, n=len(old["projects"]))
    return None


REFUSED_LOG = hubconf.STATE / "registry-refused.jsonl"


def record_refusal(why: str, reg: pathlib.Path) -> str:
    """Who tried to rebuild the index from the wrong registry: appended to REFUSED_LOG, so the next attempt names the
    process instead of us replaying test runs. Returns a one-line summary for the '(registry)' error."""
    ppid = os.getppid()
    try:
        parent = subprocess.run(["ps", "-o", "command=", "-p", str(ppid)], capture_output=True, text=True,
                                timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):   # a tool's sandbox may forbid ps
        parent = None
    entry = {"at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "why": why,
             "argv": sys.argv, "pid": os.getpid(), "ppid": ppid, "parent": parent, "cwd": os.getcwd(),
             "registry_dir": str(reg), "config": str(hubconf.config_dir()), "home": os.environ.get("HOME"),
             "state": str(hubconf.STATE)}
    try:
        with REFUSED_LOG.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return f"at {entry['at']} by pid {entry['pid']} (parent {ppid}: {(parent or '?')[:200]}), argv {sys.argv}, " \
           f"cwd {entry['cwd']}, config {entry['config']}; log {REFUSED_LOG}"


def refresh(only: list[str] | None = None, rebuild: bool = False) -> dict:
    """Validate the whole registry, refresh the cache and index.json, return the index."""
    CACHE.mkdir(parents=True, exist_ok=True)
    reg = hubconf.registry_dir()
    files = sorted(reg.glob("*.yaml")) if reg.is_dir() else []
    try:
        old = json.loads((CACHE / "index.json").read_text())
    except (OSError, ValueError):
        old = {}
    if not rebuild and (why := wrong_registry(old, reg, files)):
        who = record_refusal(why, reg)
        print(f"registry: {why}\n  {who}", file=sys.stderr)
        return {**old, "errors": {**(old.get("errors") or {}), "(registry)": [why, who]}}
    # The platform's own registry entry is read wherever registry_dir is, and always from the platform repo: a file
    # with the same name in the user's registry (e.g. an extra copy made during the move) isn't used, or the
    # platform's updates to its own entry would never take effect
    plat = hubconf.platform_registry()
    if plat.exists():
        files = sorted([f for f in files if f.stem != plat.stem] + [plat])
    projects, errors = {}, {}
    for f in files:
        stem = f.stem
        try:
            text = f.read_text()
            doc = yaml.safe_load(text)
            errs = validate(doc, stem) + roster_mismatch(doc, text)
        except yaml.YAMLError as e:
            doc, errs = None, [_("cannot read the YAML: {error}", error=e)]
        cache = CACHE / f"{stem}.json"
        if not errs:
            cache.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
            projects[stem] = doc
        else:
            errors[stem] = errs
            if cache.exists():  # keep using the last valid version
                projects[stem] = json.loads(cache.read_text())
    # projects whose registry file was deleted: drop the cache too (deleting the entry = removing the project)
    for c in CACHE.glob("*.json"):
        if c.name != "index.json" and c.stem not in {f.stem for f in files}:
            c.unlink()
    modules, mod_errors = load_modules()
    errors.update(mod_errors)
    index = {"v": 1, "generated": datetime.datetime.now().isoformat(timespec="seconds"), "registry_dir": str(reg.resolve()),
             "projects": projects, "modules": modules, "errors": errors}
    if (cycles := job_cycles(index)):
        errors["(jobs)"] = [_("after:/before: form a cycle, these jobs would wait for each other forever: {cycle}", cycle=c)
                            for c in cycles]
    tmp = CACHE / "index.json.tmp"
    tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1, default=str))
    tmp.replace(CACHE / "index.json")
    return index


def every_minutes(s: str) -> int:
    n, unit = EVERY.match(s).groups()
    return int(n) * (60 if unit == "h" else 1)


def scheduler_jobs(index: dict, legacy_names: set[str]) -> tuple[list[dict], list[str], dict[str, str]]:
    """Registry → scheduler.py's job dicts. Returns (jobs, the ones not used because their old name is still in
    schedule.yaml, new name → old name)."""
    out, skipped, renames = [], [], {}
    before: dict[str, list[str]] = {}
    docs = sorted(index["projects"].items()) + sorted((index.get("modules") or {}).items())
    for name, doc in docs:
        d = dirs(doc)
        for job in doc.get("jobs") or []:
            full = f"{name}/{job['name']}"
            if job.get("legacy_name") in legacy_names:
                skipped.append(full)
                continue
            if job.get("legacy_name"):
                renames[full] = job["legacy_name"]
            cwd = d.get(job.get("cwd") or default_cwd(doc))
            if job.get("kind") == "agent" and job.get("cwd") == "state":
                cwd = hubconf.STATE / "agent-jobs" / (job.get("legacy_name") or f"{name}-{job['name']}")
            if job.get("kind") == "agent":
                prompt = hubconf.registry_dir() / name / (job.get("prompt") or f"jobs/{job['name']}.md")
                agent_name = job.get("legacy_name") or f"{name}-{job['name']}"
                cmd = (f"{shlex.quote(str(hubconf.ROOT / 'bin' / 'run-agent-job.sh'))} {shlex.quote(agent_name)} "
                       f"{shlex.quote(str(cwd))} {shlex.quote(str(prompt))}")
                # unattended permissions: only these allowed (llm.py's agent-job judgment point switches to dontAsk)
                if job.get("allow"):
                    cmd = f"AGENT_JOB_ALLOW={shlex.quote(json.dumps(job['allow'], ensure_ascii=False))} {cmd}"
                # needs claude.ai connectors: use the subscription login, not the default headless token
                if job.get("connectors"):
                    cmd = f"LLM_AUTH=subscription {cmd}"
            elif seatbelt.sandboxed(doc):
                # a sandboxed project's command job runs under its profile (bin/sandboxed-run; refuses where it can't)
                cmd = shlex.join(seatbelt.wrap(name, cwd, ["/bin/sh", "-c", job["run"]]))
            else:
                cmd = f"cd {shlex.quote(str(cwd))} && {job['run']}"
            j = {"name": full, "command": cmd, "project": name}
            if job.get("kind") == "agent":
                j["agent_name"] = agent_name
            if "at" in job:
                j["time"] = str(job["at"])
            else:
                j["every_minutes"] = every_minutes(str(job["every"]))
            for src, dst in (("days", "days_of_week"), ("days_of_month", "days_of_month"), ("months", "months"),
                             ("until", "end_date"), ("between", "between"), ("background", "background"),
                             ("max_attempts_per_day", "max_attempts_per_day")):
                if src in job:
                    j[dst] = job[src]
            if job.get("after"):
                j["after"] = [a if "/" in a else f"{name}/{a}" for a in job["after"]]
            for t in job.get("before") or []:
                before.setdefault(t if "/" in t else f"{name}/{t}", []).append(full)
            out.append(j)
    # before: [X] = X's after gets one more entry, me (a module inserts itself into the core flow without the core
    # knowing about it). If X doesn't exist (that module is off), ignore it
    by_name = {j["name"]: j for j in out}
    for target, deps in before.items():
        if target in by_name:
            by_name[target]["after"] = list(dict.fromkeys((by_name[target].get("after") or []) + deps))
    return out, skipped, renames


def tilde(p: str) -> str:
    home = str(pathlib.Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def new_doc(name: str, workspace: str, origin: str, memory_bare: str) -> dict:
    """The default registry entry at onboarding."""
    return {"v": 1, "name": name,
            "paths": {"workspace": tilde(workspace), "memory_bare": tilde(memory_bare), "origin": origin},
            "memory": True, "privacy": {"reflection": "include"}, "push_on_merge": False,
            "mailbox": {"escalate": True},
            "publishes": [{"kind": "state", "path": "STATE.md", "contract": "state-todo", "v": 1}]}


def header() -> str:
    return "# " + _("Project registry (schema v1, see bin/registry.py)") + "\n"


def cmd_add(name: str, workspace: str, origin: str, memory_bare: str, extra: str | None = None) -> int:
    """extra: a JSON object overriding fields of the default entry (e.g. an idea sandbox's
    type/purpose/enforce/privacy). Nothing is written if validation fails."""
    f = hubconf.registry_dir() / f"{name}.yaml"
    if f.exists():
        print(_("{name} is already in the registry ({file}); not overwriting", name=name, file=f))
        return 0
    doc = new_doc(name, workspace, origin, memory_bare)
    if extra:
        doc.update(json.loads(extra))
        errs = validate(doc, name)
        if errs:
            print(_("the registry entry is invalid; not written: {errors}", errors="; ".join(errs)), file=sys.stderr)
            return 1
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(header() + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
    print(_("registry: {file}", file=f))
    return 0


def cmd_rename(old: str, new: str, workspace: str, origin: str, memory_bare: str) -> int:
    reg = hubconf.registry_dir()
    src, dst = reg / f"{old}.yaml", reg / f"{new}.yaml"
    if not src.exists() or dst.exists():
        sys.exit(_("registry rename: {src} must exist and {dst} must not", src=src, dst=dst))
    text = src.read_text()
    doc = yaml.safe_load(text)
    doc["name"] = new
    doc.setdefault("paths", {}).update(workspace=tilde(workspace), origin=origin, memory_bare=tilde(memory_bare))
    # keep the header comments
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#")) if text.startswith("#") else header()
    dst.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
    src.unlink()
    if (reg / old).is_dir():
        (reg / old).rename(reg / new)
    print(_("registry: {old} → {new}", old=old, new=new))
    return 0


def cmd_apply_updates() -> int:
    """manifest.update letters sent by projects (text = the full registry YAML): if valid, write it into the registry
    + commit; if not, send the errors back to that project's mailbox; either way the letter is closed. Only the
    sending project's own entry can be changed. Zero LLM."""
    import sqlite3
    bus = hubconf.bus_db()
    if not bus.exists():
        return 0
    hub = hubconf.hub_project()
    c = sqlite3.connect(bus, timeout=30)
    rows = c.execute("SELECT id, source, payload FROM events m WHERE m.type='message' AND m.to_project=?"
                     " AND NOT EXISTS(SELECT 1 FROM events x WHERE x.type='message.done' AND x.ref=m.id) ORDER BY seq",
                     (hub,)).fetchall()
    hubbus = [sys.executable, str(hubconf.ROOT / "bin" / "hubbus")]

    def reply(to, mid, text, note):
        subprocess.run([*hubbus, "send", "--from", hub, "--to", to, "--reply-to", mid, "--type", "info", "--text", text],
                       capture_output=True)
        subprocess.run([*hubbus, "done", mid, "--note", note], capture_output=True, cwd=hubconf.ROOT)

    for mid, sender, pl in rows:
        p = json.loads(pl or "{}")
        if p.get("type") != "manifest.update":
            continue
        try:
            doc = yaml.safe_load(p.get("text") or "")
            errs = validate(doc, sender)
        except yaml.YAMLError as e:
            doc, errs = None, [_("cannot read the YAML: {error}", error=e)]
        if isinstance(doc, dict) and doc.get("name") != sender:
            errs.append(_("a project can only change its own registry entry (the sender is {sender}, the YAML's name is {name})",
                           sender=sender, name=doc.get("name")))
        elif isinstance(doc, dict):
            cur_f = hubconf.registry_dir() / f"{sender}.yaml"
            cur = (yaml.safe_load(cur_f.read_text()) or {}) if cur_f.exists() else {}
            errs += platform_owned_changes(doc, cur)
        if errs:
            reply(sender, mid, _("manifest.update was not applied; the registry is unchanged:\n- {errors}", errors="\n- ".join(errs)),
                  _("returned: {errors}", errors="; ".join(errs)[:200]))
            print(_("{sender}: manifest.update {mid} returned ({n} {n:problem|problems})", sender=sender, mid=mid, n=len(errs)))
            continue
        f = hubconf.registry_dir() / f"{sender}.yaml"
        f.write_text("# " + _("Project registry (schema v1, see bin/registry.py). Last updated by manifest.update {mid}", mid=mid) + "\n"
                     + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
        r = subprocess.run([str(hubconf.ROOT / "bin" / "commit-hub-files.sh"), f"registry: {sender} manifest.update {mid}",
                            f"projects/{sender}.yaml"], capture_output=True, text=True)
        reply(sender, mid, _("manifest.update applied") + ("" if r.returncode == 0 else _(" (the commit failed; the file is written)"))
              + _("; it takes effect at the next scheduler tick."), _("applied"))
        print(_("{sender}: manifest.update {mid} applied", sender=sender, mid=mid))
    return 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["add"] and len(args) == 7 and args[5] == "--extra":
        return cmd_add(*args[1:5], extra=args[6])
    if args[:1] == ["add"] and len(args) == 5:
        return cmd_add(*args[1:])
    if args[:1] == ["rename"] and len(args) == 6:
        return cmd_rename(*args[1:])
    if args[:1] == ["apply-updates"]:
        return cmd_apply_updates()
    if args and args[0] == "check":
        args = args[1:]
    rebuild = "--rebuild" in args
    args = [x for x in args if x != "--rebuild"]
    index = refresh(rebuild=rebuild)
    names = args or sorted(set(index["projects"]) | set(index["errors"]))
    bad = 0
    for n in names:
        if n in index["errors"]:
            bad += 1
            print(f"✗ {n}" + (_(" (keeping the last valid one)") if n in index["projects"] else ""))
            for e in index["errors"][n]:
                print(f"    {e}")
        elif n in index["projects"]:
            doc = index["projects"][n]
            print(_("✓ {name}: {jobs} {jobs:job|jobs}, {services} {services:service|services}", name=n, jobs=len(doc.get("jobs") or []),
                     services=len(doc.get("services") or [])))
        else:
            bad += 1
            print(_("? {name}: not in the registry", name=n))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
