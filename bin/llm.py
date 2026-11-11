#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The only place in brunna that calls `claude -p`. Every call must be a judgment point declared in
contracts/llm-points.yaml: flags (model, permission mode, tools, allowedTools) are added as declared, the output is
validated against the declared schema, and when the model can't be used or the output is invalid, it returns the
declared safe default or takes the caller's failure path, recording job.failed on the bus.

  llm.py run <point> [--cwd D] [--model M] [--prompt-file F]   prompt from --prompt-file or stdin
  Output: on success, the same as `claude -p --output-format json`; on failure
        {"type": "result", "is_error": true, "structured_output": <default or null>, "llm_point": …, "llm_error": …}
        the registry has a `default` value → structured_output is that value, exit 0; otherwise null, exit 1 (the
        caller takes its own failure path).
        on_failure (skip / retry_later / ask_user / say_so / default) declares "what actually happens on failure",
        for people and evaluations to read; it doesn't affect what this does. Format:
        contracts/llm-points.v1.schema.json
  llm.py run <point> --dry-run ...         only prints the command it would run (a JSON list), doesn't call the model
  llm.py lint     lists the `claude -p` calls in bin/ that bypass this (for contracts-check)
  llm.py health   whether each provider in {settings}'s llm can be used
  llm.py project <project> <point> --request F   a project's own declared model call (cap-runner runs it; see
                  project_call)

A judgment point declaring `tool_servers: allowed_only` (the agent jobs): before it runs, the runtime says which of
the environment's tool servers this run would load, and every one its allow list doesn't name is denied
(policy.tool_server_denials); when that can't be told the run doesn't happen: job.failed, an alert, exit 1.

A judgment point declared `shape: completion` → handed to completion.py (the same handling for every provider;
docs/design/llm-providers.md): if {settings} has an `llm` block, the provider is chosen from it (any key); otherwise it
goes to runtime.default's completion interface (Claude Code run with its tools off). Agent-shaped points go to the
agent interface as before.

Credentials (unified, decided by the owner on 2026-10-24): by default ~/.claude/headless-oauth-token (`claude
setup-token`, one year, the same under launchd and SSH, only one thing to replace); only judgment points declaring
`auth: subscription`, or callers setting LLM_AUTH=subscription (a registry job's `connectors: true`), use the
subscription login (keychain / .credentials.json): claude.ai connectors (Upwork etc.) load only with the subscription
login. Wanting the token but having no token file fails (no silent switch to the subscription login;
brunna/claude-login raises an alert too).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import names  # noqa: E402
import i18n  # noqa: E402
import minischema  # noqa: E402

import yaml  # noqa: E402

POINTS = hubconf.ROOT / "contracts" / "llm-points.yaml"
_ = i18n.domain(__file__)
CALL = re.compile(r"""(\bclaude\s+-p\b|["']claude["']\s*,\s*["']-p["'])""")


def points() -> dict:
    return yaml.safe_load(POINTS.read_text())


def job_failed(point: str, reason: str) -> None:
    subprocess.run([sys.executable, str(hubconf.ROOT / "bin" / "hubbus"), "publish", "job.failed",
                    "--source", "llm", "--payload",
                    json.dumps({"job": f"llm:{point}", "detail": reason[:500]}, ensure_ascii=False)],
                   capture_output=True)


def fail(point: str, spec: dict, reason: str) -> int:
    job_failed(point, reason)
    # exit 0 only if the registry really has a default value: lease-handover is on_failure: default, but its safe
    # result comes from the runner (which retries and does a minimal handover only on exit != 0), so the marker alone
    # can't turn it into exit 0
    has_default = "default" in spec
    print(json.dumps({"type": "result", "is_error": True, "structured_output": spec.get("default"),
                      "llm_point": point, "llm_error": reason}, ensure_ascii=False))
    return 0 if has_default else 1


def output_schema(spec: dict) -> str | None:
    """The declared output schema path (relative to contracts/); points with format: text have none."""
    out = spec.get("output") or {}
    return out.get("schema") if out.get("format") == "json" else None


def point_runtime(point: str, spec: dict) -> str | None:
    """Which runtime a judgment point uses: hub.json `llm.points.<point>.runtime` (a deployment's choice) > the
    declaration's `runtime` > None (runtime.default)."""
    pin = ((hubconf.get("llm") or {}).get("points") or {}).get(point) or {}
    return pin.get("runtime") or spec.get("runtime")


def model_for(point: str, spec: dict | None = None, cli: str | None = None, runtime: str | None = None) -> str | None:
    """Which model this judgment point uses when handed to a runtime: computed only here for the whole platform
    (librarian, consolidate, the weekly report, schedulectl all ask here rather than each keeping a default table):
    --model > hub.json `llm.points.<point>.model` (when no provider is given, or the provider is this runtime) >
    the runtime's group table (`runtime_ports.models`). None = unspecified, the tool's own default (points with
    model_group: default).
    When hub.json has llm.providers, completion points are chosen by completion.resolve, not here."""
    import runtime_ports
    if cli:
        return cli
    spec = spec if spec is not None else points()["points"].get(point) or {}
    name = runtime or point_runtime(point, spec) or runtime_ports.default_name()
    pin = ((hubconf.get("llm") or {}).get("points") or {}).get(point) or {}
    if pin.get("model") and pin.get("provider") in (None, name):
        return pin["model"]
    return runtime_ports.models(name).get(spec.get("model_group") or "standard")


def auth_mode(spec: dict) -> str:
    """Which login: LLM_AUTH (set by the caller for this call, e.g. login-check checking the token) > the judgment
    point's declared auth > the token by default.
    Where the credentials are and what the environment variables are called is the runtime's business (the auth
    interface, layer 6)."""
    return os.environ.get("LLM_AUTH") or spec.get("auth") or "token"


def auth_env(spec: dict, rt) -> tuple[dict | None, str]:
    """(the environment variables to give the agent, the reason shown to a person when there are none). LLM_AUTH is
    llm.py's own variable and isn't passed down; API keys hub.json's llm gives through environment variables aren't
    passed down either (any agent with Bash could read them)."""
    import completion
    drop = {"LLM_AUTH"} | completion.key_envs()
    base = {k: v for k, v in os.environ.items() if k not in drop}
    return rt.auth_env(auth_mode(spec), base)


def completion_target(point: str, spec: dict, model: str | None = None) -> dict:
    """Where a shape: completion judgment point goes. hub.json has llm.providers → completion.resolve picks the provider and
    model (model is not used); otherwise → runtime.default's completion interface, with the model model_for computes.
    Raises completion.Refused when it has nowhere to go (no provider, and a runtime with no completion interface)."""
    import completion
    import runtime_ports
    if completion.settings():
        return completion.resolve(point, spec)
    name = point_runtime(point, spec) or runtime_ports.default_name()
    if "completion" not in runtime_ports.capabilities(name):
        raise completion.Refused(_("runtime {name} has no completion interface, and {settings} has no llm section "
                                   "(set up an LLM provider)", name=name))
    return completion.runtime_target(name, model if model is not None else model_for(point, spec, runtime=name),
                                     auth_mode(spec))


def run_completion(a, spec: dict, prompt: str, model: str | None) -> int:
    """A shape: completion judgment point: handed to completion.py (the same handling for every provider), to the target
    completion_target names."""
    import completion
    schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    try:
        out = completion.run(a.point, spec, prompt, schema, target=completion_target(a.point, spec, model))
    except completion.Refused as e:
        return fail(a.point, spec, str(e))
    print(json.dumps(out, ensure_ascii=False))
    return 0


def answer(point: str, prompt: str):
    """A completion point's structured answer, or None, for a caller none of whose failures may carry text
    (mailbox-route: a reason can quote the model, and its input is a letter anyone can send). It prints nothing and
    publishes nothing, so no job.failed: the caller says, in its own fixed words, that the point gave no answer. The
    declaration's checks are run.py's: the same target, schema and max_attempts."""
    import completion
    spec = points()["points"].get(point) or {}
    if spec.get("shape") != "completion":
        return None
    try:
        schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
        out = completion.run(point, spec, prompt, schema, target=completion_target(point, spec))
    except Exception:   # Refused, and whatever an implementation raises: no answer, said by the caller
        return None
    return out.get("structured_output") if isinstance(out, dict) else None


def project_call(project: str, point: str, request: pathlib.Path) -> int:
    """A project's own model call (agenthub complete → cap-runner → here, outside the project's sandbox): request is
    {"prompt", "schema"?} written by cap-runner in platform state (never read from the project's folder). Checked
    before anything is sent: the point is declared in the project's registry entry (models: {point: {tier}}); the
    provider it resolves to is metered (completion.metered) only if the owner set metered_models: true. Not available
    → refused with the reason, no other provider or model is tried. Usage rows carry the project."""
    import completion
    import runtime_ports
    name = f"{project}/{point}"
    spec = {"shape": "completion", "model_group": "standard"}
    doc = hubconf.projects().get(project)
    if not doc:
        return fail(name, spec, _("{project} is not in the registry", project=project))
    decl = (doc.get("models") or {}).get(point)
    if not decl:
        return fail(name, spec, _("{project} has not declared the model call {point} (registry models:)",
                                  project=project, point=point))
    spec["model_group"] = decl["tier"]
    try:
        req = json.loads(request.read_text())
        prompt, schema = req["prompt"], req.get("schema")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return fail(name, spec, _("cannot read the request: {err}", err=e))
    try:
        if completion.settings():
            target = completion.resolve(name, spec)
        else:
            rt = runtime_ports.default_name()
            if "completion" not in runtime_ports.capabilities(rt):
                return fail(name, spec, _("runtime {name} has no completion interface, and {settings} has no llm "
                                          "section (set up an LLM provider)", name=rt))
            target = completion.runtime_target(rt, model_for(name, spec), auth_mode(spec))
        if completion.metered(target) and doc.get("metered_models") is not True:
            return fail(name, spec, _("{provider}/{model} is pay-per-use and the owner has not allowed metered models for "
                                      "{project} (registry metered_models); not sent", provider=target["provider"],
                                      model=target["model"], project=project))
        out = completion.run(name, spec, prompt, schema, target=target, project=project)
    except completion.Refused as e:
        return fail(name, spec, str(e))
    print(json.dumps(out, ensure_ascii=False))
    return 0


AGENT_POINT = "project-agent"


def project_agent(project: str, name: str, request: pathlib.Path, cwd: str) -> int:
    """A project's declared agent call (brunna agent → cap-runner → here, outside the project's sandbox;
    docs/design/project-agent-calls.md A1): request is {"prompt", "schema"?}, written by cap-runner in platform state.
    The rules, the tier and the timeout come from the registry, never from the request; the folder must be one of the
    project's registry folders. It runs judgment point project-agent under the name <project>/<name>: an owner's pin,
    the usage row and a failure go by that name."""
    import registry
    key = f"{project}/{name}"
    spec = points()["points"][AGENT_POINT]
    doc = registry.refresh()["projects"].get(project)
    if not doc:
        return fail(key, spec, _("{project} is not in the registry", project=project))
    decl = (doc.get("agents") or {}).get(name)
    if not isinstance(decl, dict) or not decl.get("allow"):
        return fail(key, spec, _("{project} has not declared the agent call {name} (registry agents:)", project=project,
                                 name=name))
    d = registry.dirs(doc)
    folders = {pathlib.Path(p).resolve() for p in (d.get("workspace"), d.get("runtime")) if p}
    here = pathlib.Path(cwd).resolve()
    if here not in folders:
        return fail(key, spec, _("{cwd} is not one of {project}'s folders; an agent call runs only in one", cwd=cwd,
                                 project=project))
    try:
        req = json.loads(request.read_text())
        prompt, schema = req["prompt"], req.get("schema")
    except (OSError, ValueError, KeyError, TypeError) as e:
        return fail(key, spec, _("cannot read the request: {err}", err=e))
    if not isinstance(prompt, str) or not prompt.strip() or (schema is not None and not isinstance(schema, dict)):
        return fail(key, spec, _("the request needs a prompt, and a schema that is a JSON object if any"))
    timeout = registry.agent_timeout_s(decl)
    limit = int(hubconf.get("cap_runner.agent_max_timeout_s", 3600))
    if timeout > limit:
        return fail(key, spec, _("{key} declares a timeout of {minutes} minutes; the most is {limit}", key=key,
                                 minutes=timeout // 60, limit=limit // 60))
    for var in ("AGENT_JOB_MAIL", "LLM_AUTH"):   # nothing of the caller's environment chooses how it runs
        os.environ.pop(var, None)
    os.environ.update({"AGENT_JOB_PROJECT": project, "AGENT_JOB_TIER": str(decl.get("tier") or ""),
                       "AGENT_JOB_ALLOW": json.dumps(decl["allow"], ensure_ascii=False)})
    a = argparse.Namespace(cmd="run", point=AGENT_POINT, cwd=str(here), model=None, prompt_file=None, extra=[],
                           dry_run=False, key=key, prompt=prompt, schema=schema, timeout_s=timeout)
    return run(a)


def deny_unnamed_servers(point: str, task):
    """A judgment point declaring `tool_servers: allowed_only` (policy.tool_server_denials): ask the runtime which of
    the environment's tool servers this run would load, and deny every one the run's allow list doesn't name.
    → (task with deny_servers, None, True); (task, None, False) for a run that loads none of them (sealed); or (task,
    the reason, False) when it can't be told: the run must not happen then (fail closed: never without the deny)."""
    import policy
    import runtime_ports
    try:
        rt = runtime_ports.load(point_runtime(point, task.spec), ports=("tool_servers",))
        loaded = rt.ambient_servers(task)
        if loaded is None:
            return task, None, False
        if (taken := sorted(set(loaded) & set(RESERVED_SERVERS))):
            # the platform's own id: a server the user, a plugin or the folder named so would take its tools' place
            return task, _("{point} did not run: a tool server it would load is named {name}, the id of the platform's "
                           "own server", point=point, name=", ".join(taken)), False
        return task._replace(deny_servers=tuple(policy.tool_server_denials(loaded, rt.servers_named(task)))), None, True
    except Exception as e:   # whatever goes wrong here, the answer is "can't tell" (a bug included): no run
        return task, _("{point} did not run: which tool servers it would load could not be told, and it never runs "
                       "without denying those it doesn't use ({reason})", point=point, reason=e), False


def tool_servers_alert(point: str, why: str | None) -> None:
    """A run refused for this is a safety stop the owner must see (alert.py: once, reminded every 24 hours, one message
    when it runs again); a broken alert doesn't change the outcome."""
    try:
        import alert
        alert.once(f"tool-servers:{point}", [why] if why else [],
                   _("{point} can tell which tool servers it loads again", point=point))
    except Exception as e:
        print(_("llm.py: the alert was not sent: {error!r}", error=e), file=sys.stderr)


def memory_copy_alert(of: str, why: str | None) -> None:
    """A copy of a project's memory branch an agent won't start in: that project's notes stop being read every night until a commit
    removes what's there, so the owner hears of it (once, reminded every 24 hours) and hears when it runs clean again. of: the project
    (HUB_MEMORY_OF, its callers set it), or the folder. A broken alert doesn't change the outcome."""
    try:
        import alert
        alert.once(f"memory-copy-refused:{of}", [why] if why else [], _("the memory copy of {project} holds only notes again", project=of))
    except Exception as e:
        print(_("llm.py: the alert was not sent: {error!r}", error=e), file=sys.stderr)


def run(a) -> int:
    """The judgment-point layer: read the declaration, build the Task, decide the login, hand over to the runtime
    (runtime_ports' agent interface; Claude Code is just one implementation).
    Schema validation, the failure policy (fail → job.failed, default) and the output format stay here, not in the
    runtime."""
    import runtime_ports
    spec = points()["points"].get(a.point)
    if spec is None:
        print(_("llm.py: {point} is not a declared judgment point (contracts/llm-points.yaml)", point=a.point),
              file=sys.stderr)
        return 2
    # a.key: the call's own name where a point serves many (a project's declared agent, <project>/<name>): the owner's
    # pin, the usage row and the failure are by it. a.prompt: given by the caller (llm.py agent), not read here
    key = getattr(a, "key", None) or a.point
    if getattr(a, "prompt", None) is not None:
        prompt = a.prompt
    else:
        prompt = "" if a.cmd == "exec" else (pathlib.Path(a.prompt_file).read_text() if a.prompt_file else sys.stdin.read())
    try:
        tier = job_tier(spec, os.environ)
    except ValueError as e:
        return fail(key, spec, str(e))
    if tier:   # the job's strength is the run's group; an owner's pin on the point still wins (model_for)
        spec = {**spec, "model_group": tier}
    model = model_for(key, spec, a.model, runtime=point_runtime(a.point, spec)) if key != a.point \
        else model_for(a.point, spec, a.model)
    if a.cmd == "run" and spec.get("shape") == "completion" and not getattr(a, "dry_run", False):
        return run_completion(a, spec, prompt, model)
    # declared permissions (unattended allow:, settings: platform) must be honoured, never dropped by a runtime that
    # can't (docs/design/event-jobs.md section 3.2)
    confined = spec.get("settings") or (spec.get("allow_from_env") and os.environ.get(spec["allow_from_env"]))
    # a sandboxed project's agent work runs only confined: an unconfined point loads the project's saved permissions
    # and hooks (registry.py doesn't schedule such a job either; docs/design/project-agent-calls.md A2)
    if not confined and spec.get("project_env") and (proj := os.environ.get(spec["project_env"])):
        import registry
        doc = registry.refresh()["projects"].get(proj)
        why = (_("{project} is not in the registry, so what it is held to can't be told; {point} doesn't run",
                 project=proj, point=a.point) if doc is None
               else _("{project} is sandboxed, and {point} would run with its saved permissions: a sandboxed project's "
                      "agent job declares allow: and runs confined", project=proj, point=a.point)
               if registry.sandbox_enforced(doc) else None)
        if why:
            if getattr(a, "dry_run", False):   # a dry run only shows, never publishes job.failed
                print(why, file=sys.stderr)
                return 2
            return fail(key, spec, why)
    try:
        # a settings: platform run's rules are checked when it starts: a runtime that can't check them runs none; a run
        # that gets the mail tools needs the runtime to attach the platform's server
        ports = ("agent",) + (("confine",) if confined else ()) + (("rule_check",) if spec.get("settings") == "platform" else ()) \
            + (("run_server",) if mail_tools(spec, os.environ) else ())
        rt = runtime_ports.load(point_runtime(a.point, spec), ports=ports)
    except runtime_ports.Unsupported as e:   # no coding agent: take the declared failure path, reason spelled out
        return fail(key, spec, str(e))
    # works_in: memory: the folder is a copy of a project's memory branch, so an agent started there loads what the project wrote. What
    # holds it is this look at the files, on every runtime: only notes, or no run
    in_memory = spec.get("works_in") == "memory"
    if in_memory and not getattr(a, "dry_run", False):
        import memory_paths
        bad = memory_paths.folder_problems(a.cwd or os.getcwd())
        why = _("{point} works in a copy of a memory branch, and this one holds what a memory branch may not: {paths}. No agent starts there "
                "until a commit on that branch removes it", point=a.point, paths="; ".join(bad)) if bad else None
        memory_copy_alert(os.environ.get("HUB_MEMORY_OF") or os.path.realpath(a.cwd or os.getcwd()), why)
        if why:
            return fail(key, spec, why)
    # restricted: a runtime that can't be told to load no settings file runs it only in the platform's throwaway copy (doctor says what
    # that leaves), never anywhere else with the ask dropped. A memory copy just checked is fine too: there the flag only reinforces
    if spec.get("restricted") and not in_memory and "confine" not in runtime_ports.capabilities(point_runtime(a.point, spec)) \
            and not hubconf.is_throwaway_copy(a.cwd or os.getcwd(), marked=os.environ.get("HUB_THROWAWAY_COPY") or ""):
        return fail(key, spec, _("{point} asks that no settings file be loaded, and this runtime can't be told that: it runs only in the "
                                     "platform's throwaway copy, and {cwd} is not one", point=a.point, cwd=a.cwd or os.getcwd()))
    if "auth" in runtime_ports.capabilities(point_runtime(a.point, spec)):
        env, why = auth_env(spec, rt)
        if env is None:
            return fail(key, spec, why)
    # a runtime without a login interface (e.g. a command template): the tool logs in itself; the platform's keys still
    # aren't passed down
    else:
        import completion
        drop = {"LLM_AUTH"} | completion.key_envs()
        env = {k: v for k, v in os.environ.items() if k not in drop}
    how = None
    if tier:
        how, why = billing_gate(a.point, spec, rt, env, tier)
        if why:
            if getattr(a, "dry_run", False):
                print(why, file=sys.stderr)
                return 2
            return fail(key, spec, why)
    # the process started (hooks included) knows it's a platform headless call: hubbus doesn't deliver mail to it
    env["HUB_LLM_POINT"] = a.point
    if spec.get("settings") == "platform":
        # a confined run (docs/design/event-jobs.md 3.2): the mail commands act only as and for its own project
        names.env_set(env, "CONFINED", "1")
    # agents the platform starts can reach `agenthub …` (the rules file and mailbox prompt name only the command, not
    # where the hub is installed)
    env["PATH"] = f"{hubconf.ROOT / 'bin'}:{env.get('PATH', '')}".rstrip(":")
    run_dir = make_run_dir(a.point, spec)
    try:
        return _launch(a, spec, rt, env, prompt, model, run_dir, how)
    finally:
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)


def job_tier(spec: dict, env) -> str | None:
    """The model strength a job chose (registry tier:, which the scheduler puts in AGENT_JOB_TIER) on a point that takes
    one (tier_from_env); None when it chose none. ValueError when the value isn't a strength."""
    import registry
    var = spec.get("tier_from_env")
    tier = (env or {}).get(var) if var else None
    if not tier:
        return None
    if tier not in registry.TIERS:
        raise ValueError(_("${var} is {tier}, not one of {tiers}", var=var, tier=tier, tiers=", ".join(registry.TIERS)))
    return tier


def billing_gate(point: str, spec: dict, rt, env: dict, tier: str) -> tuple[str | None, str | None]:
    """(how the run is paid for, why it doesn't run) for a run at a strength its project chose
    (docs/design/project-agent-calls.md A5): the runtime's auth interface says; unknown is refused, metered runs only
    when the owner allowed metered models for the project and today's metered spending is under the budget."""
    import runtime_ports
    name = point_runtime(point, spec) or runtime_ports.default_name()
    restricted = spec.get("settings") == "platform" or bool(spec.get("restricted"))
    how = rt.billing(auth_mode(spec), env, restricted) if "auth" in runtime_ports.capabilities(name) else "unknown"
    if how == "unknown":
        return how, _("{point} runs at the strength its job chose ({tier}) only where how {runtime} is paid for can be "
                      "told, and it can't be; not run", point=point, tier=tier, runtime=name)
    if how == "metered":
        import completion
        import registry
        project = env.get(spec.get("project_env") or "") or ""
        doc = registry.refresh()["projects"].get(project) if project else None
        if not doc or doc.get("metered_models") is not True:
            return how, _("{point} at strength {tier} would be paid per use ({runtime}), and the owner has not allowed "
                          "metered models for {project} (registry metered_models); not run", point=point, tier=tier,
                          runtime=name, project=project or "?")
        if completion.unknown_cost_today():
            return how, _("a pay-per-use run earlier today ended without saying what it cost, so today's spending can't "
                          "be told; {point} not run until tomorrow", point=point)
        # llm.budget holds with or without llm.providers (completion.settings() needs providers)
        budget = (((hubconf.get("llm") or {}) if isinstance(hubconf.get("llm"), dict) else {}).get("budget") or {}).get("daily_usd")
        if budget is not None and completion.today_cost() >= float(budget):
            return how, _("today's model spending has reached the budget (llm.budget.daily_usd ${budget}); {point} not "
                          "run", budget=budget, point=point)
    return how, None


RUN_DIR_MAX_AGE_S = 86400   # a run folder older than this was left by a crash (no run takes a day)


def run_dirs() -> pathlib.Path:
    return hubconf.STATE / "agent-jobs" / ".settings"


def make_run_dir(point: str, spec: dict) -> pathlib.Path | None:
    """A confined run's own folder (docs/design/confined-mail-tools.md, "Depends on"): what the runtime writes for this run
    alone (its settings file) goes there, and it is removed on every way out of run(). None when the point isn't confined or
    names no project: the adapter's own refusal then decides. Folders a crash left are removed first."""
    project = os.environ.get(spec.get("project_env") or "") if spec.get("settings") == "platform" else None
    if not project or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project):   # a registry name, never a path
        return None
    base = run_dirs()
    base.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in base.iterdir():
        try:
            if old.is_dir() and not old.is_symlink() and now - old.lstat().st_mtime > RUN_DIR_MAX_AGE_S:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass
    d = base / f"{project}.{point}.{secrets.token_hex(8)}"
    d.mkdir(mode=0o700)   # never an existing one: two runs never share a folder
    return d


# the shape of every id the platform writes (hubbus EVENT_ID, whose pattern ends in $: matched whole here, so a final
# newline never passes; tests check the two agree)
EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")


def mail_tools(spec: dict, env) -> bool:
    """Whether this run gets the platform's mail tools (docs/design/confined-mail-tools.md M1): mail: tools, and the
    caller's opt-in when the point asks for one (a registry job's mail: true)."""
    if spec.get("mail") != "tools":
        return False
    return not spec.get("mail_from_env") or (env or {}).get(spec["mail_from_env"]) == "1"


# the id the platform's own tool server has in a run, under both CLI names: no other server may load under it
RESERVED_SERVERS = tuple(f"{cli}-run" for cli in names.all("cli"))


def attach_server(point: str, spec: dict, rt, task, run_dir) -> tuple:
    """The platform's MCP server for this run (M2): → (task, None), or (task, why it can't run). The server reads the run's
    values from server.json, written here once the runtime has made the tools final, never from its env or arguments,
    where the runtime expands variables: a letter id a sender chose could otherwise read the agent's login."""
    import mcp_server
    import runtime_ports
    if not run_dir:
        return task, None   # no project: the runtime's own refusal says so
    env = {k: os.environ[k] for k in ("HOME", "PATH") if k in os.environ}
    for loc in mcp_server.LOCATIONS:   # copied as they are here: e3 runs on its own instance
        for k in names.env_names(loc):
            if k in os.environ:
                env[k] = os.environ[k]
    run_file = pathlib.Path(run_dir) / "server.json"
    server = runtime_ports.ToolServer(id=f"{names.current('cli')}-run", command=sys.executable,
                                      args=(str(hubconf.ROOT / "bin" / "mcp_server.py"), "--run", str(run_file)),
                                      env=env, tools=tuple(mcp_server.run_tools(spec)))
    if any("$" in x for x in (server.command, *server.args, *env.values())):
        return task, _("{point} did not start: the platform's tool server for it would be given a $ in its command, "
                       "arguments or environment, which the runtime may expand", point=point)
    task = rt.run_server(task._replace(servers=(server,)))
    tools = [t for s in task.servers for t in s.tools]
    body = {"project": (task.env or {}).get(spec.get("project_env") or ""), "point": point, "tools": tools,
            "chain": names.env("JOB_CHAIN"), "depth": names.env("JOB_DEPTH")}
    if spec.get("letters_env"):
        body["letters"] = run_letters(task.env, spec["letters_env"])
    fd = os.open(run_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(body, ensure_ascii=False))
    return task, None


def run_letters(env, var: str) -> list[str] | None:
    """The letters a mail handler's run is started for (a JSON list in env[var]): None when there is none, or when any
    entry isn't a letter id."""
    try:
        ids = json.loads((env or {}).get(var) or "")
    except ValueError:
        return None
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and EVENT_ID.fullmatch(i) for i in ids):
        return None
    return ids


def rule_refusal(point: str, spec: dict, rt, task) -> str | None:
    """Why a settings: platform run's own rules don't let it start (the runtime's rule_check port, A2 of
    docs/design/project-agent-calls.md), as one sentence; None when they do, or when the point isn't one, or names no
    project (the runtime's own refusal says so then)."""
    if spec.get("settings") != "platform" or not (project := (task.env or {}).get(spec.get("project_env") or "")):
        return None
    import registry
    doc = registry.refresh()["projects"].get(project)
    if doc is None:
        return _("{project} is not in the registry, so what it is held to can't be told; {point} doesn't run",
                 project=project, point=point)
    if spec.get("mail") == "tools" and spec.get("letters_env") and not run_letters(task.env, spec["letters_env"]):
        # every call it made would be refused, or hubbus would read "not a letter run" and lift the letters' limits
        return _("{point} did not start: it acts on the letters ${var} lists, and that lists no letter id",
                 point=point, var=spec["letters_env"])
    problems = rt.rule_problems(task, sandboxed=registry.sandbox_enforced(doc), network=list(doc.get("network") or []),
                                untrusted=spec.get("prompt_source") == "untrusted")
    if not problems:
        return None
    return _("{point} did not start: its rules aren't what a confined run of {project} may have: {problems}",
             point=point, project=project, problems="; ".join(problems))


def _launch(a, spec: dict, rt, env: dict, prompt: str, model, run_dir, how: str | None = None) -> int:
    import runtime_ports
    key = getattr(a, "key", None) or a.point
    # a.schema / a.timeout_s: the call's own (a project's declared agent: its request's schema, its declared timeout)
    if getattr(a, "schema", None) is not None:
        schema = a.schema
    else:
        schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    task = runtime_ports.Task(point=a.point, prompt=prompt, spec=spec, cwd=a.cwd or None, model=model,
                              output_schema=schema, timeout_s=getattr(a, "timeout_s", None) or spec.get("timeout_s", 1200),
                              env=env,
                              extra=tuple(a.extra or ()), run_dir=str(run_dir) if run_dir else None)
    if (why := rule_refusal(a.point, spec, rt, task)):
        if getattr(a, "dry_run", False):   # a dry run only shows, never publishes job.failed
            print(why, file=sys.stderr)
            return 2
        return fail(key, spec, why)
    if mail_tools(spec, env):
        task, why = attach_server(a.point, spec, rt, task, run_dir)
        if why:
            if getattr(a, "dry_run", False):
                print(why, file=sys.stderr)
                return 2
            return fail(key, spec, why)
    if spec.get("tool_servers") == "allowed_only":
        task, why, checked = deny_unnamed_servers(a.point, task)
        real = a.cmd == "run" and not getattr(a, "dry_run", False)   # a dry run only shows, never alerts
        if why:
            if real:
                tool_servers_alert(a.point, why)
                return fail(key, spec, why)
            print(why, file=sys.stderr)
            return 2
        if checked and real:
            tool_servers_alert(a.point, None)
    if a.cmd == "exec":
        # a long-lived streaming session (e.g. a channel lease). Temporary: layer 1's open_session will replace it
        if not spec.get("stream"):
            print(_("llm.py: {point} does not declare stream: true, so it cannot run under exec", point=a.point), file=sys.stderr)
            return 2
        if not hasattr(rt, "exec_stream"):
            print(_("llm.py: {point}: this runtime does not support long-running streaming sessions", point=a.point),
                  file=sys.stderr)
            return 2
        rt.exec_stream(task)
        return 0
    if getattr(a, "dry_run", False):
        try:
            print(json.dumps(rt.describe(task), ensure_ascii=False))
        except ValueError as e:   # the declaration can't be met (e.g. settings: platform without its project)
            print(e, file=sys.stderr)
            return 2
        return 0
    try:
        res = rt.run(task)
    except ValueError as e:
        return fail(key, spec, str(e))
    if how:   # a strength the project chose: every run is a usage row (the metered ones count toward the budget)
        import completion
        completion.record_run(key, point_runtime(a.point, spec) or runtime_ports.default_name(), model, how,
                              res.cost_usd, res.stop, env.get(spec.get("project_env") or "") or None)
    if not res.ok:
        return fail(key, spec, res.error)
    # no structured_output isn't a failure: handed to the caller as is (the librarian deliberately "still goes by the
    # actual file changes"); if present, it must be valid
    if schema is not None and res.structured_output is not None:
        if (errs := minischema.errors(schema, res.structured_output)):
            return fail(key, spec, _("output does not match the schema: {errors}", errors="; ".join(errs[:3])))
    # the output field names are llm.py's agreement with its callers (librarian, consolidate, lease-runner… read
    # these), not changed by the runtime
    print(json.dumps({"type": "result", "is_error": res.incomplete, "result": res.text,
                      "structured_output": res.structured_output, "total_cost_usd": res.cost_usd,
                      "session_id": res.session_id, "num_turns": res.turns, "subtype": res.stop,
                      "permission_denials": list(res.denials), "llm_point": key, "llm_model": model},
                     ensure_ascii=False))
    return 0


def lint() -> list[str]:
    p = points()
    exempt = {e["file"] for e in p.get("exempt") or []}
    bad = []
    for f in sorted((hubconf.ROOT / "bin").iterdir()):
        rel = f"bin/{f.name}"
        if not f.is_file() or rel in exempt or f.name == "llm.py":
            continue
        try:
            text = f.read_text()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0] if not f.suffix == ".md" else line
            if CALL.search(code):
                bad.append(_("{file}:{line}: a claude -p that does not go through bin/llm.py", file=rel, line=i))
    for name, spec in p["points"].items():
        if output_schema(spec) and not (POINTS.parent / output_schema(spec)).exists():
            bad.append(_("judgment point {name}: output.schema {schema} does not exist", name=name,
                         schema=output_schema(spec)))
        bad += mail_problems(name, spec)
    return bad


def mail_problems(name: str, spec: dict) -> list[str]:
    """The mail keys' cross-field rules (docs/design/confined-mail-tools.md M1)."""
    bad = []
    confined = spec.get("settings") == "platform"
    if confined and spec.get("mail") not in ("tools", "none"):
        bad.append(_("judgment point {name}: settings: platform needs mail: tools or mail: none", name=name))
    if not confined and "mail" in spec:
        bad.append(_("judgment point {name}: mail: is only for settings: platform points", name=name))
    for key in ("mail_from_env", "letters_env"):
        if key in spec and spec.get("mail") != "tools":
            bad.append(_("judgment point {name}: {key} needs mail: tools", name=name, key=key))
    if spec.get("mail") == "tools" and spec.get("tool_servers") != "allowed_only":
        bad.append(_("judgment point {name}: mail: tools needs tool_servers: allowed_only (a server under the platform's "
                     "own id would otherwise go unchecked)", name=name))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(prog="llm.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("point")
    r.add_argument("--cwd")
    r.add_argument("--model")
    r.add_argument("--prompt-file")
    r.add_argument("--dry-run", action="store_true", help=_("only print the command that would run; no model call"))
    r.add_argument("extra", nargs="*", help=_("extra claude arguments (after --)"))
    e = sub.add_parser("exec", help=_("long-running streaming session: build the flags from the declaration, then exec "
                                      "claude (stdin/stdout are stream-json)"))
    e.add_argument("point")
    e.add_argument("--cwd")
    e.add_argument("--model")
    e.add_argument("--prompt-file")
    e.add_argument("extra", nargs="*")
    pc = sub.add_parser("project", help=_("a project's own declared model call (run by cap-runner)"))
    pc.add_argument("project")
    pc.add_argument("point")
    pc.add_argument("--request", required=True)
    ag = sub.add_parser("agent", help=_("a project's own declared agent call (run by cap-runner)"))
    ag.add_argument("project")
    ag.add_argument("name")
    ag.add_argument("--request", required=True)
    ag.add_argument("--cwd", required=True)
    sub.add_parser("lint")
    sub.add_parser("health", help=_("whether each provider in {settings} llm works (key, connection, model there)"))
    a = ap.parse_args()
    if a.cmd == "exec" and a.cwd:
        os.chdir(a.cwd)
    if a.cmd in ("run", "exec"):
        return run(a)
    if a.cmd == "project":
        return project_call(a.project, a.point, pathlib.Path(a.request))
    if a.cmd == "agent":
        return project_agent(a.project, a.name, pathlib.Path(a.request), a.cwd)
    if a.cmd == "health":
        import completion
        rows = completion.health_all()
        for name, ok, why in rows:
            mark = "✓" if ok else "✗"
            print(_("{mark} {name}: {why}", mark=mark, name=name, why=why) if why else f"{mark} {name}")
        return 0 if all(ok for _name, ok, _why in rows) else 1
    bad = lint()
    for b in bad:
        print(b)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
