#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The project registry: <registry_dir>/<name>.yaml (one file per project, directory set in {settings}) → validation →
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
                                  permission rules; capabilities = platform capabilities ({settings} capabilities,
                                  e.g. gpu), needs sandbox: true; inside the sandbox, hub-run --cap <capability> runs
                                  things through the platform (docs/design/capabilities.md);
                                  work = a discuss lease may make changes in work mode (writing files inside the
                                  folder, commands asked every time), needs sandbox: true.
                                  The record of the owner's explicit authorization (10-25): written when an idea
                                  sandbox is created, carried over on upgrade; other projects add it only with the
                                  owner's consent
  models: {<point>: {tier}}       model calls this project makes through the platform (brunna complete; tier fast |
                                  standard | strong, {settings} decides the model). Undeclared points are refused
  metered_models: true            the owner allows metered (pay-per-use) providers for those calls; without it a call
                                  that would go to one is refused (llm.py project)
  network: [domains]              what the sandbox needs to reach beyond the platform defaults
  allow_write: [paths]            what the sandbox needs to write beyond the platform defaults
    type, enforce, network, allow_write, privacy, metered_models and paths are security fields: a manifest.update a project
    sends that changes them is rejected whole (a project can't switch off its own sandbox, widen its scope or add
    capabilities);
    to change them, ask the brunna session (the owner confirms)
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
  jobs:      [{name, at | every | trigger, run | (kind: agent + prompt), cwd?, days?, days_of_month?, months?,
               until?, between?, background?, after?, max_attempts_per_day?, timeout?, legacy_name?, catch_up?}]
    catch_up: period (a job with at and day filters): when the machine was asleep or off on the scheduled day, the
      job runs at the first tick after it wakes instead of skipping to the next scheduled day (newcomer-path.md B7)
    timeout: 45m / 2h: the run is stopped (its whole process group) past this; without it the scheduler's default
    every: 15m / 1h; cwd: runtime (default, when there's a runtime) | workspace | hub | state (agent jobs only: runs
      in its own state directory, without loading the project's settings/MCP)
    trigger: {events: [bus event type], projects?: [self|<project>|*], sources?: [glob], where?: {payload.key: value |
      [values]}, debounce?: 0m/10m/1h} = an event-triggered job (docs/design/event-jobs.md): runs when matching bus
      events wait (the scheduler's tick). Not `on:` (YAML reads it as true). events come from the hubbus contract's
      type enum; message only with where.type limited to info/progress/done (the rest is mailbox work).
      An ordinary project sees only events addressed to it (self, including names from before a rename); the platform
      project and modules may name others or *. Bus events are their writers' claims: the platform's event jobs are
      never agent jobs, and a project's agent event job must declare allow: (its boundary) and run in the workspace
      (cwd: workspace); it runs under judgment point agent-job-event, which loads only the platform's settings for
      the project (no accumulated project or user permissions). message.delivered and message.done are refused like record.
      max_attempts_per_day defaults to 12; an agent event job always runs as the scheduler's background job, in
      place, so the scheduler sees its outcome. legacy_name is refused
    after: [job] (same project) or [project/job]
    allow: [rules] (agent jobs only): the job's permission rules, its whole boundary: it runs unattended (dontAsk)
      under judgment point agent-job-confined (agent-job-event when triggered), which loads only the platform's
      settings for the project, never the project's saved permissions, the user's or its hooks. Without allow: an
      agent job runs in auto mode with the project's settings (flagged on the status page)
    connectors: true (agent jobs only): needs claude.ai connectors (Upwork etc.) → uses the subscription login, not
      the default headless token (tools in allow with mcp__claude_ai_ but this not set: invalid). Such a run loads the
      machine's tool servers; every one whose tools allow doesn't name is denied for it (tool_servers: allowed_only)
    legacy_name: the old name in schedule.yaml: while the old name is still in schedule.yaml this job isn't used
                 (during the switch the old one wins; delete that entry from schedule.yaml to switch over; the state
                 moves over from the old name too)

schema v2 (contracts/project.v2.schema.json; docs/design/test-waiting.md B1): v1's fields, except that tests leave
runtime. validate_v2() checks it; validate() reads both, a v1 file upgraded to v2 as it is read (tiers.upgrade),
since the runner and the readers switched over (test-waiting step 2.3).
  runtime: {deploy}         only deployment is left (present = has <services_root>/<name>, as in v1)
  code: [glob]              the changed files that count as this project's code, relative to its folder; absent =
                            every file
  inputs: [kind:value]      what changes results without being code: file:<path in the repo outside the project's
                            folders> | config:<file name listed in {settings} tests.config_inputs> | env:<NAME> |
                            cmd:<command whose output counts>
  tests: [{tier, run, when, paths?, jobs?}]
                            tier: a name, unique; run: the command, run in an exported tree ({jobs} = the worker
                            count); when: any of commit (needs commit_gate) | merge | nightly | deploy; paths: the
                            tier runs only when a change touches one of them; jobs: {max?, env?} (max needs {jobs} in
                            run or env)
  commit_gate: true         the repo opts into the platform's commit gate (a repo-level field)
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
import event_jobs  # noqa: E402
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402
import isolation  # noqa: E402
import tiers  # noqa: E402

import yaml  # noqa: E402

_ = i18n.domain(__file__)

CACHE = hubconf.STATE / "registry"
TOP = {"v", "name", "type", "purpose", "paths", "memory", "privacy", "runtime", "push_on_merge", "commits", "mailbox",
       "publishes", "provides", "consumes", "services", "jobs", "enforce", "network", "allow_write", "gate", "secrets",
       "models", "metered_models", "layout_intent"}
TOP_V2 = TOP | {"tests", "code", "inputs", "commit_gate"}
# what the index's documents are: 2 = project v2 (v1 files upgraded as they are read). Readers that need v2 fields
# (merge-branch) refuse an index of an older format; registry.refresh never overwrites a newer one
INDEX_FORMAT = 2
MOMENTS = ("commit", "merge", "nightly", "deploy")
TIER_KEYS = {"tier", "run", "when", "paths", "jobs"}
ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SECRET = {"name", "path", "env_key", "expires", "max_age_days", "warn_days", "backup", "why", "rotate"}
JOB = {"name", "at", "every", "trigger", "run", "kind", "prompt", "cwd", "days", "days_of_month", "months", "until",
       "between", "background", "after", "before", "max_attempts_per_day", "timeout", "legacy_name", "allow",
       "connectors", "catch_up"}
TRIGGER = {"events", "projects", "sources", "where", "debounce"}
# the mailbox's own work and bookkeeping (inbox records, delivery and closing written by dispatch, which relays others'
# letters without a chain): no event job reacts to them (event-jobs.md 3.5)
MAILBOX_WORK = ("record", "message.delivered", "message.done")
NOTICES = ("info", "progress", "done")   # the only letter types an event job may react to (event-jobs.md 3.5)
SERVICE = {"name", "run", "label", "keepalive", "interval_s", "throttle_s", "env", "cwd", "log", "expose"}
DAYS = {"mon", "tue", "wed", "thu", "fri", "sat", "sun"}
EVERY = re.compile(r"^(\d+)(m|h)$")
TIERS = ("fast", "standard", "strong")
MODEL_POINT = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def dirs(doc: dict) -> dict[str, pathlib.Path | None]:
    """This project's runtime / workspace / hub directories."""
    name = doc.get("name", "")
    if name == hubconf.hub_project() or doc.get("type") in ("module", "platform"):
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


def validate_models(doc: dict) -> list[str]:
    """models: {<point>: {tier}}: the model calls this project makes through the platform (agenthub complete);
    metered_models: true = the owner allows metered (pay-per-use) providers for them (owner-only, PLATFORM_OWNED)."""
    errs = []
    models = doc.get("models")
    if models is not None:
        if not isinstance(models, dict):
            return [_("models must be a mapping of point name → {{tier: fast|standard|strong}}")]
        for name, decl in models.items():
            if not isinstance(name, str) or not MODEL_POINT.match(name):
                errs.append(_("models: point name {name!r} must be lowercase letters, digits and -", name=name))
            if not isinstance(decl, dict) or set(decl) - {"tier"} or decl.get("tier") not in TIERS:
                errs.append(_("models.{name} must be {{tier: fast|standard|strong}}", name=name))
    if "metered_models" in doc and not isinstance(doc["metered_models"], bool):
        errs.append(_("metered_models must be true or false"))
    return errs


# paths too: a manifest.update setting paths.workspace to ~ would make the whole home folder its sandbox's to write
# (round 6, 11-01); onboarding, renaming and recovery write paths through their own scripts, not through a letter
PLATFORM_OWNED = ("type", "enforce", "network", "allow_write", "privacy", "metered_models", "paths")


def platform_owned_changes(new: dict, cur: dict) -> list[str]:
    """Security fields manifest.update can't change: list them when they differ from the current registry entry."""
    return [_("{key} is a security field that only brunna can change ({owner} confirms); this letter changes it", key=k,
              owner=hubconf.owner_tag())
            for k in PLATFORM_OWNED
            if new.get(k) != cur.get(k)]


def validate(doc, stem: str) -> list[str]:
    """A registry document as it is read: v2, or v1 upgraded to v2 (tiers.upgrade; the one upgrade function, kept as
    long as any deployment may hold v1 files)."""
    if not isinstance(doc, dict):
        return [_("not a YAML mapping")]
    if doc.get("v") not in (1, 2):
        return [_("v must be 1 or 2 (it is {v!r})", v=doc.get("v"))]
    return validate_v2(tiers.upgrade(doc), stem)


def validate_v2(doc, stem: str) -> list[str]:
    """Project v2 (the header's "schema v2")."""
    if not isinstance(doc, dict):
        return [_("not a YAML mapping")]
    errs = [] if doc.get("v") == 2 else [_("v must be 2 (it is {v!r})", v=doc.get("v"))]
    rt = doc.get("runtime")
    if isinstance(rt, dict) and set(rt) - {"deploy"}:
        errs.append(_("runtime keeps only deploy in project v2 (tests go in tests); not allowed: {keys}",
                      keys=", ".join(sorted(map(str, set(rt) - {"deploy"})))))
    return errs + _validate_body(doc, stem, TOP_V2, ("deploy",)) + validate_tests(doc, dirs(doc))


def _strings(v) -> bool:
    return isinstance(v, list) and all(isinstance(x, str) and x for x in v)


def validate_tests(doc: dict, d: dict) -> list[str]:
    """v2's code, inputs, tests and commit_gate."""
    errs: list[str] = []
    if "code" in doc and not _strings(doc["code"]):
        errs.append(_("code must be a list of patterns"))
    if "commit_gate" in doc and not isinstance(doc["commit_gate"], bool):
        errs.append(_("commit_gate must be true or false"))
    inputs = doc.get("inputs", [])
    if not _strings(inputs):
        errs.append(_("inputs must be a list of kind:value strings"))
        inputs = []
    for item in inputs:
        kind, _sep, value = item.partition(":")
        if kind not in ("file", "config", "env", "cmd") or not value:
            errs.append(_("inputs: {item!r} must be file:<path>, config:<name>, env:<NAME> or cmd:<command>", item=item))
        elif kind == "file" and (value.startswith(("/", "~")) or ".." in pathlib.PurePosixPath(value).parts):
            errs.append(_("inputs: {item!r}: a file input is a path inside the repo", item=item))
        elif kind == "config" and ("/" in value or value in (".", "..")):
            errs.append(_("inputs: {item!r}: a config input is a bare file name", item=item))
        elif kind == "env" and not ENV_NAME.match(value):
            errs.append(_("inputs: {item!r}: an environment variable name is upper case letters, digits and _",
                          item=item))
        elif kind == "cmd" and (e := resolves(value, d["workspace"])):
            errs.append(_("inputs: {item!r}: {error}", item=item, error=e))
    tiers = doc.get("tests", [])
    if not isinstance(tiers, list):
        return errs + [_("tests must be a list")]
    seen: set[str] = set()
    for i, t in enumerate(tiers):
        if not isinstance(t, dict):
            errs.append(_("tests[{i}] must be a mapping", i=i))
            continue
        name = t.get("tier")
        where = name if isinstance(name, str) else f"tests[{i}]"
        errs += [_("tests.{tier}: unknown field {key}", tier=where, key=k) for k in t if k not in TIER_KEYS]
        if not isinstance(name, str) or not MODEL_POINT.match(name):
            errs.append(_("tests.{tier}: tier must be lowercase letters, digits and -", tier=where))
        elif name in seen:
            errs.append(_("tests.{tier}: the tier name is used twice", tier=where))
        seen.add(where)
        run = t.get("run")
        if not isinstance(run, str) or not run.strip():
            errs.append(_("tests.{tier}: run is required", tier=where))
        elif (e := resolves(run.replace("{jobs}", "1"), d["workspace"])):
            errs.append(_("tests.{tier}: run: {error}", tier=where, error=e))
        when = t.get("when")
        if not isinstance(when, list) or not when or any(w not in MOMENTS for w in when) or len(set(when)) != len(when):
            errs.append(_("tests.{tier}: when must list one or more of commit, merge, nightly, deploy", tier=where))
        elif "commit" in when and doc.get("commit_gate") is not True:
            errs.append(_("tests.{tier}: when: commit needs commit_gate: true", tier=where))
        if "paths" in t and not _strings(t["paths"]):
            errs.append(_("tests.{tier}: paths must be a list of patterns", tier=where))
        jobs = t.get("jobs")
        if jobs is not None:
            if not isinstance(jobs, dict) or set(jobs) - {"max", "env"}:
                errs.append(_("tests.{tier}: jobs can only be {{max, env}}", tier=where))
            else:
                mx, env = jobs.get("max"), jobs.get("env")
                if mx is not None and (isinstance(mx, bool) or not isinstance(mx, int) or mx < 1):
                    errs.append(_("tests.{tier}: jobs.max must be a whole number of at least 1", tier=where))
                if env is not None and (not isinstance(env, str) or not ENV_NAME.match(env)):
                    errs.append(_("tests.{tier}: jobs.env must be an environment variable name", tier=where))
                if mx is not None and env is None and "{jobs}" not in (run or ""):
                    errs.append(_("tests.{tier}: jobs.max needs {{jobs}} in run or jobs.env", tier=where))
    return errs


def _validate_body(doc: dict, stem: str, top: set[str], runtime_keys: tuple[str, ...]) -> list[str]:
    """What v1 and v2 share: everything but v and what tests are."""
    errs: list[str] = []
    if doc.get("name") != stem:
        errs.append(_("name ({name!r}) must be the same as the file name {stem}", name=doc.get("name"), stem=stem))
    errs += [_("unknown field {key}", key=k) for k in doc if k not in top]
    if doc.get("type", "project") not in ("project", "idea", "platform"):
        errs.append(_("type can only be project, idea or platform"))
    if doc.get("type") == "platform" and stem not in names.all("project"):
        errs.append(_("type: platform is the platform's own entry only"))
    if stem in names.all("project") and doc.get("type") != "platform" and stem != hubconf.hub_project():
        # both of the platform's names are kept for it (docs/design/rename-brunna.md): a project under either would
        # take letters addressed to the platform
        errs.append(_("{name} is reserved for the platform itself; pick another name", name=stem))
    if doc.get("purpose") is not None and (not isinstance(doc["purpose"], str) or not doc["purpose"].strip()):
        errs.append(_("purpose must be a non-empty string"))
    if doc.get("type") == "idea" and not doc.get("purpose"):
        errs.append(_("type: idea needs a purpose (the idea itself)"))
    is_hub = stem == hubconf.hub_project() or doc.get("type") == "platform"
    paths = doc.get("paths") or {}
    if not is_hub and not paths.get("workspace"):
        errs.append(_("paths.workspace is required"))
    errs += validate_enforce(doc)
    errs += validate_models(doc)
    li = doc.get("layout_intent")
    if li is not None and not (isinstance(li, dict) and (li.get("split") is True or isinstance(li.get("span"), list))):
        errs.append(_("layout_intent is split: true, or span: a list of projects (docs/design/newcomer-path.md B6)"))
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
    for key in runtime_keys:
        if isinstance(rt, dict) and rt.get(key):
            base = d["workspace"] if key == "test" else d["workspace"]
            if (e := resolves(rt[key], base)):
                errs.append(_("runtime.{key}: {error}", key=key, error=e))
    return errs


def bus_event_types() -> list[str]:
    """The event types a job can react to: the type enum of the hubbus contract (one source of truth)."""
    schema = json.loads((hubconf.ROOT / "contracts" / "hubbus.v1.schema.json").read_text())
    return schema["properties"]["type"]["enum"]


def platform_doc(doc: dict, stem: str) -> bool:
    """The platform project and modules may react to other projects' events; an ordinary project only to its own."""
    return stem == hubconf.hub_project() or doc.get("type") in ("module", "platform")


def validate_trigger(job: dict, doc: dict, stem: str) -> list[str]:
    """A job's trigger: (docs/design/event-jobs.md section 2)."""
    on = job["trigger"]
    if not isinstance(on, dict):
        return [_("trigger must be a mapping")]
    errs = [_("trigger: unknown field {key}", key=k) for k in on if k not in TRIGGER]
    if "legacy_name" in job:
        errs.append(_("an event job (trigger:) cannot have legacy_name"))
    events = on.get("events")
    if not isinstance(events, list) or not events or not all(isinstance(x, str) and x for x in events):
        errs.append(_("trigger.events must be a non-empty list of bus event types"))
        events = []
    else:
        known = bus_event_types()
        errs += [_("trigger.events: {type} is not a bus event type (contracts/hubbus.v1.schema.json: {known})", type=t,
                   known=", ".join(known)) for t in events if t not in known]
        errs += [_("trigger.events: {type} is mailbox work (the mailbox delivers it); an event job can't react to it",
                   type=t) for t in events if t in MAILBOX_WORK]
    projects = on.get("projects", ["self"])
    if not isinstance(projects, list) or not projects or not all(isinstance(x, str) and x for x in projects):
        errs.append(_("trigger.projects must be a non-empty list (self, a project name or *)"))
    elif any(x not in ("self", stem) for x in projects) and not platform_doc(doc, stem):
        errs.append(_("trigger.projects: a project's job only sees its own project's events (self)"))
    # bus events are their writers' claims (any project can write the bus; event-jobs.md section 3.2)
    if job.get("kind") == "agent" and platform_doc(doc, stem):
        errs.append(_("the platform's event jobs can't be agent jobs: an event is what its writer claims, and an agent "
                      "with the platform's authority must not take instructions from it"))
    elif job.get("kind") == "agent":
        if job.get("connectors"):
            errs.append(_("an agent event job can't use connectors: (it would load the user's MCP servers)"))
        if not job.get("allow"):
            errs.append(_("an agent event job must declare allow: (anyone who can write the bus can start it with their "
                          "text; allow: is its boundary)"))
        if (job.get("cwd") or default_cwd(doc)) != "workspace":
            # the project's own protections (its sandbox, its rules) live in its workspace's settings: run anywhere
            # else, an agent anyone can start would run without them
            errs.append(_("an agent event job must run in the workspace (cwd: workspace), where the project's sandbox "
                          "and rules apply"))
    sources = on.get("sources")
    if sources is not None and (not isinstance(sources, list) or not sources
                                or not all(isinstance(x, str) and x for x in sources)):
        errs.append(_("trigger.sources must be a non-empty list of senders (globs allowed)"))
    where = on.get("where", {})
    if not isinstance(where, dict) or not all(isinstance(k, str) and k for k in where):
        errs.append(_("trigger.where must be a mapping from payload keys to values"))
        where = {}
    for k, v in where.items():
        vals = v if isinstance(v, list) else [v]
        if not vals or not all(isinstance(x, (str, int, float, bool)) for x in vals):
            errs.append(_("trigger.where.{key} must be a value or a non-empty list of values", key=k))
    if "message" in events:
        types = where.get("type")
        types = types if isinstance(types, list) else [types]
        if not types or not all(t in NOTICES for t in types):
            errs.append(_("trigger.events has message: limit it to notices with where.type ({types}); requests and questions "
                          "belong to the mailbox", types=", ".join(NOTICES)))
    deb = on.get("debounce", "0m")
    if str(deb) != "0m" and not EVERY.match(str(deb)):
        errs.append(_("trigger.debounce must look like 0m / 10m / 1h"))
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
        if sum(k in job for k in ("at", "every", "trigger")) != 1:
            errs.append(_("job {name}: give exactly one of at, every and trigger", name=n))
        if "trigger" in job:
            errs += [_("job {name}: {error}", name=n, error=e) for e in validate_trigger(job, doc, stem)]
        if "at" in job and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(job["at"])):
            errs.append(_("job {name}: at must be HH:MM (quoted)", name=n))
        if "every" in job and not EVERY.match(str(job["every"])):
            errs.append(_("job {name}: every must look like 15m / 1h", name=n))
        if "timeout" in job and not EVERY.match(str(job["timeout"])):
            errs.append(_("job {name}: timeout must look like 45m / 2h", name=n))
        if "catch_up" in job and (job["catch_up"] != "period" or "at" not in job):
            errs.append(_("job {name}: catch_up can only be period, on a job with at", name=n))
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
        if job.get("connectors") is True and not job.get("allow"):
            errs.append(_("job {name}: connectors: true needs an allow: list (its run denies every MCP server the list "
                          "doesn't name, so without one it could use no connector)", name=n))
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
MODULE_TOP = {"v", "name", "type", "description", "requires_config", "needs_llm", "jobs", "services", "secrets",
              "runtime_preset",
              "consumes"}


def validate_module(doc, stem: str) -> list[str]:
    if not isinstance(doc, dict):
        return [_("not a YAML mapping")]
    errs = [_("unknown field {key}", key=k) for k in doc if k not in MODULE_TOP]
    rp = doc.get("runtime_preset")
    if rp is not None:
        if (not isinstance(rp, dict) or not isinstance(rp.get("label"), str) or not isinstance(rp.get("find"), str)
                or not isinstance((rp.get("settings") or {}).get("runtime"), dict)
                or not all(isinstance(x, str) for x in rp.get("install") or [])
                or not all(isinstance(x, str) for x in rp.get("mcp_register") or [])
                or not all(isinstance(x, str) for x in rp.get("mcp_unregister") or [])):
            errs.append(_("runtime_preset needs label, find, settings.runtime and install as a list of lines "
                          "(docs/design/newcomer-path.md B4)"))
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
        errs.append(_("requires_config must be a list of {settings} keys"))
    else:
        errs += [_("{key} must be set in {settings} first", key=k) for k in req if hubconf.get(k) in (None, "", {}, [])]
    # needs_llm: true = the module's work is judgment points, so it does nothing useful without a model for them
    # (agenthub init leaves it off then; agenthub modules says so)
    if not isinstance(doc.get("needs_llm", False), bool):
        errs.append(_("needs_llm must be true or false"))
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
            errors[f"module:{name}"] = [_("{settings} enables module {name}, but there is no modules/{name}.yaml", name=name)]
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
    # the platform's own entry isn't a user project, under either name (an index from before the rename lists it as
    # agent-hub, one from before type: platform without the type)
    users = [n for n, d in (old.get("projects") or {}).items()
             if n not in names.all("project") and (d or {}).get("type") != "platform"]
    if not users:
        return None
    if old.get("registry_dir") not in (None, str(reg.resolve())):
        return _("the registry index was built from {old} and this process sees {dir}; not rebuilt (another config "
                 "reaching this hub's state?). If the registry really moved: registry.py check --rebuild",
                 old=old["registry_dir"], dir=reg)
    if not [f for f in files if f.stem not in names.all("project")]:
        return _("{dir} has no project files, but the registry index lists {n} {n:project|projects}; not rebuilt. If they really "
                 "were all removed: registry.py check --rebuild", dir=reg, n=len(users))
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
    """Validate the whole registry, refresh the cache and index.json, return the index. From a linked worktree of the
    hub that found the live state through the main checkout (hubconf.worktree_lookup) it validates and returns the
    index but writes nothing: the live index is built only by the main checkout's code."""
    live = not hubconf.worktree_lookup()
    if live:
        CACHE.mkdir(parents=True, exist_ok=True)
    reg = hubconf.registry_dir()
    files = sorted(reg.glob("*.yaml")) if reg.is_dir() else []
    try:
        old = json.loads((CACHE / "index.json").read_text())
    except (OSError, ValueError):
        old = {}
    if live and not rebuild and (old.get("format") or 1) > INDEX_FORMAT:
        # an index built by newer code (a long-running process with old code refreshing on a request): left alone,
        # said loudly; after a revert, `registry.py check --rebuild` is the owner's way back
        why = _("the index was built by newer code (format {have}, this code writes {mine}); not overwritten",
                have=old.get("format"), mine=INDEX_FORMAT)
        print(f"registry: {why}", file=sys.stderr)
        return {**old, "errors": {**(old.get("errors") or {}), "(registry)": [why]}}
    if not rebuild and (why := wrong_registry(old, reg, files)):
        who = record_refusal(why, reg) if live else ""
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
            doc = tiers.upgrade(doc)   # what the index holds is v2, whatever the file says
        except yaml.YAMLError as e:
            doc, errs = None, [_("cannot read the YAML: {error}", error=e)]
        cache = CACHE / f"{stem}.json"
        if not errs:
            if live:
                cache.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str))
            projects[stem] = doc
        else:
            errors[stem] = errs
            if cache.exists():  # keep using the last valid version (upgraded too: it may predate v2)
                projects[stem] = tiers.upgrade(json.loads(cache.read_text()))
    # projects whose registry file was deleted: drop the cache too (deleting the entry = removing the project)
    for c in CACHE.glob("*.json") if live else []:
        if c.name != "index.json" and c.stem not in {f.stem for f in files}:
            c.unlink()
    modules, mod_errors = load_modules()
    errors.update(mod_errors)
    index = {"v": 1, "format": INDEX_FORMAT, "generated": datetime.datetime.now().isoformat(timespec="seconds"), "registry_dir": str(reg.resolve()),
             "projects": projects, "modules": modules, "errors": errors}
    if (cycles := job_cycles(index)):
        errors["(jobs)"] = [_("after:/before: form a cycle, these jobs would wait for each other forever: {cycle}", cycle=c)
                            for c in cycles]
    if live:
        tmp = CACHE / "index.json.tmp"
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=1, default=str))
        tmp.replace(CACHE / "index.json")
    return index


def every_minutes(s: str) -> int:
    n, unit = EVERY.match(s).groups()
    return int(n) * (60 if unit == "h" else 1)


NIGHTLY_AT = "04:00"


def follows_releases() -> bool:
    """The platform's checkout sits at a release tag (detached HEAD, as install.sh and `brunna upgrade` leave it), not on
    main or a branch where it is developed."""
    r = subprocess.run(["git", "-C", str(hubconf.ROOT), "symbolic-ref", "-q", "HEAD"], capture_output=True, text=True)
    return r.returncode != 0 and (hubconf.ROOT / ".git").exists()


def nightly_test_jobs(index: dict, taken: set[str]) -> list[dict]:
    """The nightly run of every project that declares a tier for it (docs/design/test-waiting.md B4): synthesized here,
    not written into the project's file, so a manifest.update cannot remove the run that overrides flaky passes.
    `agenthub test --when nightly --force --rev HEAD` in the project's main checkout, as one of its command jobs
    (sandboxed like the others when the project is)."""
    out = []
    # the platform tests itself only where it is developed: an install at a release tag would run the whole suite of a
    # release that was tested before it was tagged, every night
    released = follows_releases()
    for name, doc in sorted(index["projects"].items()):
        if not any("nightly" in (t.get("when") or []) for t in doc.get("tests") or []):
            continue
        if name == hubconf.hub_project() and released:
            continue
        full = f"{name}/nightly-tests"
        cwd = dirs(doc)["workspace"]
        if full in taken or not cwd:
            continue
        argv = [*hubconf.launch(hubconf.ROOT / "bin" / "test-runner.py"), "--when", "nightly", "--force", "--rev", "HEAD"]
        cmd = (shlex.join(isolation.wrap(name, cwd, argv)) if isolation.sandboxed(doc)
               else f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)}")
        out.append({"name": full, "command": cmd, "project": name, "time": NIGHTLY_AT, "background": True,
                    "timeout_minutes": 180})
    return out


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
                # unattended permissions: only these rules and the platform's settings for the project (run-agent-job.sh
                # runs it under judgment point agent-job-confined; docs/design/event-jobs.md 3.2)
                if job.get("allow"):
                    cmd = (f"AGENT_JOB_ALLOW={shlex.quote(json.dumps(job['allow'], ensure_ascii=False))} "
                           f"AGENT_JOB_PROJECT={shlex.quote(name)} {cmd}")
                # needs claude.ai connectors: use the subscription login, not the default headless token
                if job.get("connectors"):
                    cmd = f"LLM_AUTH=subscription {cmd}"
            elif isolation.sandboxed(doc):
                # a sandboxed project's command job runs under its profile (bin/sandboxed-run; refuses where it can't)
                cmd = shlex.join(isolation.wrap(name, cwd, ["/bin/sh", "-c", job["run"]]))
            else:
                cmd = f"cd {shlex.quote(str(cwd))} && {job['run']}"
            j = {"name": full, "command": cmd, "project": name}
            if job.get("kind") == "agent":
                j["agent_name"] = agent_name
            if "at" in job:
                j["time"] = str(job["at"])
            elif "every" in job:
                j["every_minutes"] = every_minutes(str(job["every"]))
            for src, dst in (("days", "days_of_week"), ("days_of_month", "days_of_month"), ("months", "months"),
                             ("until", "end_date"), ("between", "between"), ("background", "background"),
                             ("max_attempts_per_day", "max_attempts_per_day"), ("catch_up", "catch_up")):
                if src in job:
                    j[dst] = job[src]
            if "trigger" in job:
                on = job["trigger"]
                deb = str(on.get("debounce", "0m"))
                # self = this project under its name and the names it had before a rename (old events keep those)
                own = [name, *[o for o in hubconf.former_names(name) if o not in index["projects"]]]
                projects = [x for p in on.get("projects", ["self"]) for x in (own if p in ("self", name) else [p])]
                j["trigger"] = {"events": list(on["events"]), "sources": list(on.get("sources") or []),
                           "projects": list(dict.fromkeys(projects)),
                           "where": dict(on.get("where") or {}),
                           "debounce_minutes": 0 if deb == "0m" else every_minutes(deb)}
                j.setdefault("max_attempts_per_day", event_jobs.DEFAULT_CAP)
                j["platform"] = platform_doc(doc, name)
                if job.get("kind") == "agent":
                    # the scheduler owns the detachment so it sees the run's real outcome (event-jobs.md section 2);
                    # judgment point agent-job-event: only allow: and the platform's settings apply (section 3.2)
                    j["command"] = f"RUN_AGENT_JOB_FOREGROUND=1 AGENT_JOB_POINT=agent-job-event {j['command']}"
                    j["background"] = True
            if "timeout" in job:
                j["timeout_minutes"] = every_minutes(str(job["timeout"]))
            if job.get("after"):
                j["after"] = [a if "/" in a else f"{name}/{a}" for a in job["after"]]
            for t in job.get("before") or []:
                before.setdefault(t if "/" in t else f"{name}/{t}", []).append(full)
            out.append(j)
    out += nightly_test_jobs(index, {j["name"] for j in out})
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
    return {"v": 2, "name": name,
            "paths": {"workspace": tilde(workspace), "memory_bare": tilde(memory_bare), "origin": origin},
            "memory": True, "privacy": {"reflection": "include"}, "push_on_merge": False,
            "mailbox": {"escalate": True},
            "publishes": [{"kind": "state", "path": "STATE.md", "contract": "state-todo", "v": 2}]}


def header() -> str:
    return "# " + _("Project registry (schema v2, see bin/registry.py)") + "\n"


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
    # known at once, not at the scheduler's next tick (which may not run yet on a new machine): the commands run right
    # after connecting (first-look, send in the new folder) read the index
    refresh()
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


V1_LINE = re.compile(r"^v:\s*1\s*(#.*)?$")
RUNTIME_BLOCK = re.compile(r"^runtime:\s*(#.*)?$")
RUNTIME_FLOW = re.compile(r"^runtime:\s*(\{.*\})\s*(#.*)?$")
TEST_LINE = re.compile(r"^(\s+)test:\s*(.*?)\s*$")


def upgrade_text(text: str) -> str | None:
    """A v1 registry file rewritten as v2 by editing its text, so its comments and layout stay: `v: 2`, runtime.test
    taken out of runtime (an emptied runtime stays as {}), a `full` tier added at the end. None when the file is not
    v1, or when the edited text would not read as exactly tiers.upgrade() of the old one (a form this doesn't know,
    left for a person)."""
    try:
        old = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(old, dict) or old.get("v") != 1:
        return None
    lines = text.splitlines(keepends=True)
    out, test, i = [], None, 0
    while i < len(lines):
        line = lines[i]
        if V1_LINE.match(line.rstrip("\n")):
            out.append(line.replace("1", "2", 1))
        elif RUNTIME_BLOCK.match(line.rstrip("\n")):
            block = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i])
                i += 1
            kept = []
            for b in block:
                m = TEST_LINE.match(b.rstrip("\n"))
                if m and test is None:
                    test = m.group(2)
                else:
                    kept.append(b)
            out.append(line if any(b.strip() for b in kept) else line.replace("runtime:", "runtime: {}", 1))
            out += kept
            continue
        elif (m := RUNTIME_FLOW.match(line.rstrip("\n"))):
            rt = yaml.safe_load(m.group(1)) or {}
            if "test" in rt:
                test = json.dumps(rt.pop("test"))
                flow = "{" + ", ".join(f"{k}: {json.dumps(v)}" for k, v in rt.items()) + "}"
                out.append(f"runtime: {flow}" + (f"  {m.group(2)}" if m.group(2) else "") + "\n")
            else:
                out.append(line)
        else:
            out.append(line)
        i += 1
    new = "".join(out)
    if test is not None:
        new = new.rstrip("\n") + f"\ntests:\n- tier: full\n  run: {test}\n  when: [merge, nightly, deploy]\n"
    try:
        return new if yaml.safe_load(new) == tiers.upgrade(old) else None
    except yaml.YAMLError:
        return None


def cmd_upgrade_files(apply: bool) -> int:
    """The registry's v1 files rewritten as v2 (upgrade_text), every one in the registry folder; dry-run unless
    --apply, which commits them with commit-hub-files.sh. Reading already upgrades v1 (validate, refresh): this only
    makes the files say what the platform reads."""
    changed, stuck = [], []
    for f in sorted(hubconf.registry_dir().glob("*.yaml")):
        text = f.read_text()
        try:
            v = (yaml.safe_load(text) or {}).get("v")
        except (yaml.YAMLError, AttributeError):
            v = None
        if v != 1:
            continue
        new = upgrade_text(text)
        if new is None:
            stuck.append(f.name)
            continue
        changed.append(f)
        print(_("{file}: v1 → v2{tests}", file=f.name, tests=_(" (runtime.test → a full tier)") if "\ntests:" in new and "tests:" not in text else ""))
        if apply:
            f.write_text(new)
    for name in stuck:
        print(_("{file}: not rewritten (a form this command does not know; edit it by hand)", file=name), file=sys.stderr)
    if not changed:
        print(_("no v1 registry files to rewrite"))
    elif not apply:
        print(_("(dry-run; add --apply to rewrite and commit them)"))
    else:
        subprocess.run([str(hubconf.ROOT / "bin" / "commit-hub-files.sh"), "registry: files rewritten as project v2",
                        *[f"projects/{f.name}" for f in changed]], check=False)
    return 1 if stuck else 0


def main() -> int:
    args = sys.argv[1:]
    if args[:1] == ["upgrade-files"]:
        return cmd_upgrade_files("--apply" in args)
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
