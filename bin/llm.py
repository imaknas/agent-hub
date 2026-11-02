#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The only place in agent-hub that calls `claude -p`. Every call must be a judgment point declared in
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
  llm.py health   whether each provider in hub.json's llm can be used
  llm.py project <project> <point> --request F   a project's own declared model call (cap-runner runs it; see
                  project_call)

A judgment point declaring `tool_servers: allowed_only` (the agent jobs): before it runs, the runtime says which of
the environment's tool servers this run would load, and every one its allow list doesn't name is denied
(policy.tool_server_denials); when that can't be told the run doesn't happen: job.failed, an alert, exit 1.

A judgment point declared `shape: completion` → handed to completion.py (the same handling for every provider;
docs/design/llm-providers.md): if hub.json has an `llm` block, the provider is chosen from it (any key); otherwise it
goes to runtime.default's completion interface (Claude Code run with its tools off). Agent-shaped points go to the
agent interface as before.

Credentials (unified, decided by the owner on 2026-10-24): by default ~/.claude/headless-oauth-token (`claude
setup-token`, one year, the same under launchd and SSH, only one thing to replace); only judgment points declaring
`auth: subscription`, or callers setting LLM_AUTH=subscription (a registry job's `connectors: true`), use the
subscription login (keychain / .credentials.json): claude.ai connectors (Upwork etc.) load only with the subscription
login. Wanting the token but having no token file fails (no silent switch to the subscription login;
agent-hub/claude-login raises an alert too).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys

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


def run_completion(a, spec: dict, prompt: str, model: str | None) -> int:
    """A shape: completion judgment point: handed to completion.py (the same handling for every provider).
    hub.json has llm.providers → completion.resolve picks the provider and model (--model is not used);
    otherwise → handed to runtime.default's completion interface, with the model model_for computes."""
    import completion
    import runtime_ports
    schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    target = None
    if not completion.settings():
        name = point_runtime(a.point, spec) or runtime_ports.default_name()
        if "completion" not in runtime_ports.capabilities(name):
            return fail(a.point, spec, _("runtime {name} has no completion interface, and hub.json has no llm section "
                                         "(set up an LLM provider)", name=name))
        target = completion.runtime_target(name, model, auth_mode(spec))
    try:
        out = completion.run(a.point, spec, prompt, schema, target=target)
    except completion.Refused as e:
        return fail(a.point, spec, str(e))
    print(json.dumps(out, ensure_ascii=False))
    return 0


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
                return fail(name, spec, _("runtime {name} has no completion interface, and hub.json has no llm "
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
    prompt = "" if a.cmd == "exec" else (pathlib.Path(a.prompt_file).read_text() if a.prompt_file else sys.stdin.read())
    model = model_for(a.point, spec, a.model)
    if a.cmd == "run" and spec.get("shape") == "completion" and not getattr(a, "dry_run", False):
        return run_completion(a, spec, prompt, model)
    # declared permissions (unattended allow:, settings: platform) must be honoured, never dropped by a runtime that
    # can't (docs/design/event-jobs.md section 3.2)
    confined = spec.get("settings") or (spec.get("allow_from_env") and os.environ.get(spec["allow_from_env"]))
    try:
        rt = runtime_ports.load(point_runtime(a.point, spec), ports=("agent", "confine") if confined else ("agent",))
    except runtime_ports.Unsupported as e:   # no coding agent: take the declared failure path, reason spelled out
        return fail(a.point, spec, str(e))
    if "auth" in runtime_ports.capabilities(point_runtime(a.point, spec)):
        env, why = auth_env(spec, rt)
        if env is None:
            return fail(a.point, spec, why)
    # a runtime without a login interface (e.g. a command template): the tool logs in itself; the platform's keys still
    # aren't passed down
    else:
        import completion
        drop = {"LLM_AUTH"} | completion.key_envs()
        env = {k: v for k, v in os.environ.items() if k not in drop}
    # the process started (hooks included) knows it's a platform headless call: hubbus doesn't deliver mail to it
    env["HUB_LLM_POINT"] = a.point
    if spec.get("settings") == "platform":
        # a confined run (docs/design/event-jobs.md 3.2): the mail commands act only as and for its own project
        names.env_set(env, "CONFINED", "1")
    # agents the platform starts can reach `agenthub …` (the rules file and mailbox prompt name only the command, not
    # where the hub is installed)
    env["PATH"] = f"{hubconf.ROOT / 'bin'}:{env.get('PATH', '')}".rstrip(":")
    schema = json.loads((POINTS.parent / output_schema(spec)).read_text()) if output_schema(spec) else None
    task = runtime_ports.Task(point=a.point, prompt=prompt, spec=spec, cwd=a.cwd or None, model=model,
                              output_schema=schema, timeout_s=spec.get("timeout_s", 1200), env=env,
                              extra=tuple(a.extra or ()))
    if spec.get("tool_servers") == "allowed_only":
        task, why, checked = deny_unnamed_servers(a.point, task)
        real = a.cmd == "run" and not getattr(a, "dry_run", False)   # a dry run only shows, never alerts
        if why:
            if real:
                tool_servers_alert(a.point, why)
                return fail(a.point, spec, why)
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
        return fail(a.point, spec, str(e))
    if not res.ok:
        return fail(a.point, spec, res.error)
    # no structured_output isn't a failure: handed to the caller as is (the librarian deliberately "still goes by the
    # actual file changes"); if present, it must be valid
    if schema is not None and res.structured_output is not None:
        if (errs := minischema.errors(schema, res.structured_output)):
            return fail(a.point, spec, _("output does not match the schema: {errors}", errors="; ".join(errs[:3])))
    # the output field names are llm.py's agreement with its callers (librarian, consolidate, lease-runner… read
    # these), not changed by the runtime
    print(json.dumps({"type": "result", "is_error": res.incomplete, "result": res.text,
                      "structured_output": res.structured_output, "total_cost_usd": res.cost_usd,
                      "session_id": res.session_id, "num_turns": res.turns, "subtype": res.stop,
                      "permission_denials": list(res.denials), "llm_point": a.point, "llm_model": model},
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
    sub.add_parser("lint")
    sub.add_parser("health", help=_("whether each provider in hub.json llm works (key, connection, model there)"))
    a = ap.parse_args()
    if a.cmd == "exec" and a.cwd:
        os.chdir(a.cwd)
    if a.cmd in ("run", "exec"):
        return run(a)
    if a.cmd == "project":
        return project_call(a.project, a.point, pathlib.Path(a.request))
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
