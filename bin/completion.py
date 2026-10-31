"""Completion for judgment points (answer only): which provider, which model, where the key is, and the same handling
every provider goes through. docs/design/llm-providers.md.

hub.json's `llm` block (no such block = the current behaviour: judgment points go to runtime.default's agent interface
as before, and this module takes no part):

  "llm": {
    "providers": {
      "<name>": {"impl": "anthropic | openai | ollama | <a runtime with completion, e.g. claude>",
                 "base_url": "…", "key": {"secret": "<file name in secrets_dir>"} | {"env": "<environment variable>"},
                 "location": "cloud | local",
                 "models": {"fast": "<name>" | {"name", "context", "price": {"in", "out"}}, …},
                 "strict": false, "max_tokens_field": "max_tokens"}
    },
    "use": {"fast": "<provider>", "standard": "<provider>", "strong": "<provider>"},   point's model_group → provider
    "points": {"<judgment point>": {"provider": "<name>", "model": "<name>"}},           override for one point
    "budget": {"daily_usd": 2.0}
  }

The handling is the same for every provider (an implementation only "sends it and brings back the raw text"):
  1. before sending, mask strings that look like credentials (one masking point for the whole platform)
  2. estimate the length: over the model's context → not sent (the provider isn't allowed to truncate silently)
  3. today's spending over budget.daily_usd → not sent
  4. 429/503/529: back off and retry (at most 3 times); a length truncation isn't retried (it would truncate the same
     way again)
  5. strip a reasoning model's <think>…</think>, parse the JSON (tolerating ``` fences), validate with minischema;
     if invalid, retry once with the errors attached
  6. every call is recorded in .state/llm-usage.jsonl (tokens, cost, billing = metered | subscription | local; and the
     project, for a project's own calls). A subscription call's cost_usd is the tool's own estimate, not a charge
The key is read only here (secrets_dir or an environment variable) and handed to the implementation in conf["_key"],
never put into any subprocess's environment.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import minischema  # noqa: E402

_ = i18n.domain(__file__)

USAGE = hubconf.STATE / "llm-usage.jsonl"
GROUPS = ("fast", "standard", "strong")
DEFAULT_GROUP = "standard"
MAX_RATE_RETRIES = 3

# Mask strings that look like credentials before sending to a model (originally in reflect_llm.py: in the first trial
# run the model quoted the first few characters of a Finnhub API key pasted in a conversation into the report).
# Tokens with known prefixes, and long enough mixed letter-digit strings, are all replaced by the mask text below.
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{10,}|sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-.]+"
    r"|(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,})")


def redact(text: str) -> str:
    return SECRET.sub("[已遮蔽]", text)


class Refused(Exception):
    """Not sent this time (wrong settings, no key, too long, budget used up): the reason is for a person to read."""


def settings() -> dict | None:
    s = hubconf.get("llm")
    return s if isinstance(s, dict) and s.get("providers") else None


def key_envs() -> set[str]:
    """Keys the settings give through environment variables: removed before handing over to any subprocess (agent job,
    mailbox session), or any agent with Bash could read them."""
    s = settings() or {}
    return {p["key"]["env"] for p in (s.get("providers") or {}).values()
            if isinstance(p, dict) and isinstance(p.get("key"), dict) and p["key"].get("env")}


def read_key(conf: dict) -> str | None:
    """None = this provider needs no key (e.g. a local Ollama, Claude Code logging in by itself). Needed but not
    available → Refused."""
    k = conf.get("key")
    if not k:
        return None
    if k.get("secret"):
        f = hubconf.secrets_dir() / k["secret"]
        try:
            v = f.read_text().strip()
        except OSError:
            raise Refused(_("no key: {file} does not exist (`agenthub llm set-key` writes it)", file=f))
        if not v:
            raise Refused(_("no key: {file} is empty", file=f))
        return v
    if k.get("env"):
        v = os.environ.get(k["env"])
        if not v:
            raise Refused(_("no key: environment variable {var} is not set (the scheduler runs under launchd and does not "
                            "see the shell's settings; a secret is more reliable)", var=k["env"]))
        return v
    raise Refused(_("key must be {{\"secret\": {file}}} or {{\"env\": {var}}}", file=_("file name"),
                    var=_("variable name")))


def resolve(point: str, spec: dict) -> dict:
    """Which provider and model this judgment point goes to: llm.points' choice > models[model_group] of the provider
    llm.use[model_group] names."""
    s = settings()
    if not s:
        raise Refused(_("hub.json has no llm section"))
    providers = s["providers"]
    group = spec.get("model_group") or DEFAULT_GROUP
    pin = (s.get("points") or {}).get(point) or {}
    name = pin.get("provider") or (s.get("use") or {}).get(group)
    if not name:
        raise Refused(_("llm.use does not say which provider serves the {group} group", group=group))
    conf = providers.get(name)
    if not isinstance(conf, dict):
        raise Refused(_("llm.providers has no {name}", name=name))
    entry = pin.get("model") or (conf.get("models") or {}).get(group)
    if not entry:
        raise Refused(_("llm.providers.{name}.models has no {group}", name=name, group=group))
    m = entry if isinstance(entry, dict) else {"name": entry}
    return {"provider": name, "impl": conf.get("impl"), "conf": conf, "group": group, "model": m.get("name"),
            "context": m.get("context"), "price": m.get("price"), "location": conf.get("location", "cloud")}


def estimate_tokens(text: str) -> int:
    """Rather overestimate: UTF-8 bytes / 3 (a Chinese character is about one token, English about 4 characters a
    token, so both come out high)."""
    return len(text.encode()) // 3 + 1


def today_cost() -> float:
    today = datetime.date.today().isoformat()
    total = 0.0
    try:
        for line in USAGE.read_text().splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if str(r.get("ts", "")).startswith(today) and r.get("cost_usd"):
                total += float(r["cost_usd"])
    except OSError:
        pass
    return total


def metered(target: dict) -> bool:
    """Pay-per-use: a cloud provider reached with an API key. A runtime's own login (subscription / headless token) and
    local providers are not. The one rule the project opt-in (registry metered_models) is checked against."""
    return target.get("location", "cloud") == "cloud" and bool((target.get("conf") or {}).get("key"))


def billing(target: dict) -> str:
    """How a call is paid for, written on every usage row: metered (pay-per-use, the cost is real), local (no money),
    subscription (a runtime's own login: cost_usd is what the tool reports the call would cost, not a charge)."""
    if metered(target):
        return "metered"
    return "local" if target.get("location") == "local" else "subscription"


def record(point: str, target: dict, res, project: str | None = None) -> float | None:
    cost = res.cost_usd
    price = target.get("price") or {}
    if cost is None and price and res.input_tokens is not None:
        cost = ((res.input_tokens or 0) * float(price.get("in", 0)) + (res.output_tokens or 0) * float(price.get("out", 0))) / 1e6
    row = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": point,
           "provider": target["provider"], "model": target["model"], "input_tokens": res.input_tokens,
           "output_tokens": res.output_tokens, "cost_usd": cost, "stop": res.stop, "billing": billing(target)}
    if project:
        row["project"] = project
    try:
        USAGE.parent.mkdir(parents=True, exist_ok=True)
        with USAGE.open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return cost


THINK = re.compile(r"<think>.*?</think>", re.S)


def parse_json(text: str):
    t = THINK.sub("", text)
    if "</think>" in t:            # no opening tag (some models print only the closing one)
        t = t.split("</think>", 1)[1]
    t = t.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.S)
    if fence:
        t = fence.group(1)
    try:
        return json.loads(t)
    except ValueError:
        i, j = t.find("{"), t.rfind("}")
        if i != -1 and j > i:
            return json.loads(t[i:j + 1])   # if that fails too, ValueError is raised
        raise


def runtime_target(name: str, model: str | None, auth: str) -> dict:
    """Without llm.providers: hand over to the runtime's (runtime.default) completion interface, with the model decided
    by llm.model_for."""
    return {"provider": name, "impl": name, "conf": {"auth": auth}, "group": None, "model": model,
            "context": None, "price": None, "location": "cloud"}


def run(point: str, spec: dict, prompt: str, schema: dict | None, sleep=time.sleep, target: dict | None = None,
        project: str | None = None) -> dict:
    """Run one completion. Returns the same shape llm.py gives its callers; raises Refused(reason) on failure.
    target not given = decided by hub.json's llm; given (runtime_target) = handed to that runtime."""
    import runtime_ports
    target = target or resolve(point, spec)
    conf = dict(target["conf"])
    conf["_key"] = read_key(conf)
    conf["_context"] = target["context"]
    budget = ((settings() or {}).get("budget") or {}).get("daily_usd")
    if budget is not None and today_cost() >= float(budget):
        raise Refused(_("today's model spending has reached the budget (llm.budget.daily_usd ${budget}); not sending this "
                        "one", budget=budget))
    prompt = redact(prompt)
    max_tokens = int(spec.get("max_tokens") or 8192)
    if target["context"] and estimate_tokens(prompt) + max_tokens > int(target["context"]):
        raise Refused(_("prompt too long: about {tokens} tokens plus {max_tokens} of output exceed "
                        "{provider}/{model}'s {context}-token context", tokens=estimate_tokens(prompt), max_tokens=max_tokens,
                        provider=target["provider"], model=target["model"], context=target["context"]))
    impl = runtime_ports.load_completion(target["impl"])
    req = runtime_ports.CompletionRequest(point=point, prompt=prompt, model=target["model"], schema=schema,
                                          max_tokens=max_tokens, timeout_s=int(spec.get("timeout_s") or 600))
    cost_total, last_errs = 0.0, None
    for attempt in range(2):   # the second = ask again with the errors attached when the output was invalid
        rate_tries = 0
        while True:
            res = impl.complete(req, conf)
            cost_total += record(point, target, res, project) or 0
            if res.stop != "rate_limited" or rate_tries >= MAX_RATE_RETRIES:
                break
            rate_tries += 1
            sleep(min(res.retry_after_s or 5 * 2 ** rate_tries, 60))
        if not res.ok:
            if res.stop == "length":
                raise Refused(_("output cut off at the length limit ({provider}/{model}, max_tokens {max_tokens}); not "
                                "retried", provider=target["provider"], model=target["model"], max_tokens=max_tokens))
            raise Refused(f"{target['provider']}/{target['model']}:{res.stop}:{res.error}")
        if schema is None:
            return _out(point, target, res.text, None, cost_total)
        try:
            obj = parse_json(res.text)
            last_errs = minischema.errors(schema, obj)
        except ValueError:
            obj, last_errs = None, [_("not JSON")]
        if not last_errs:
            return _out(point, target, res.text, obj, cost_total)
        req = req._replace(prompt=prompt + "\n\n---\n上一次的回答不合格(" + "; ".join(last_errs[:5])
                           + ")。這次只回一個合格的 JSON,不要其他文字。")
    raise Refused(_("output does not match the schema (after one retry): {errors}", errors="; ".join(last_errs[:3])))


def _out(point, target, text, obj, cost) -> dict:
    # the field names are the ones llm.py has always given its callers (librarian, consolidate… read these)
    return {"type": "result", "is_error": False, "result": text, "structured_output": obj, "total_cost_usd": cost or None,
            "llm_point": point, "provider": target["provider"], "model": target["model"], "llm_model": target["model"]}


def health_all() -> list[tuple[str, bool, str]]:
    """(provider, healthy or not, reason): for doctor. A key that can't be read also counts as not healthy."""
    import runtime_ports
    out = []
    for name, conf in ((settings() or {}).get("providers") or {}).items():
        try:
            c = dict(conf)
            c["_key"] = read_key(c)
            ok, why = runtime_ports.load_completion(c.get("impl")).health(c)
        except (Refused, KeyError, runtime_ports.Unsupported, runtime_ports.MissingFunctions) as e:
            ok, why = False, str(e)
        out.append((name, ok, why))
    return out


# ---------- settings checks (called by config-check) ----------
# A key's value may never be written in hub.json: anything that looks like a key is blocked. Model names often have
# digits and hyphens (e.g. <family>-<model>-4-5-20251001), so SECRET's "long enough, mixed letters and digits" rule
# isn't used: only known prefixes and 32 or more letters/digits with no separator.
KEYLIKE = re.compile(r"^(?:sk-|sk-ant-|AIza|gh[pousr]_|xox[abpr]-|eyJ)|^[A-Za-z0-9]{32,}$")
PRIVATE = re.compile(r"^(?:localhost|127\.\d+\.\d+\.\d+|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+"
                     r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|[\w.-]+\.ts\.net)$")


def _strings(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from _strings(v, f"{path}.{k}" if path else k)
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from _strings(v, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def config_errors(llm: dict) -> list[str]:
    import urllib.parse
    import runtime_ports
    errs = []
    for where, v in _strings(llm):
        if KEYLIKE.search(v):
            errs.append(_("{where} looks like a key value: the value goes in the secrets directory ({{\"secret\": file "
                          "name}}) or an environment variable ({{\"env\": name}})", where=where))
    providers = llm.get("providers") or {}
    if not isinstance(providers, dict):
        return errs + [_("providers must be an object")]
    if not providers and llm.get("use"):
        errs.append(_("use is set but providers is not (to pin only some judgment points' models, write only points)"))
    if providers and not llm.get("use"):
        errs.append(_("with providers, use is required (which provider serves fast / standard / strong)"))
    for name, p in providers.items():
        if not isinstance(p, dict):
            errs.append(_("providers.{name} must be an object", name=name))
            continue
        impl = p.get("impl")
        if impl in runtime_ports.PROVIDERS:
            pass
        elif impl in runtime_ports.RUNTIMES:
            if "completion" not in runtime_ports.capabilities(impl):
                errs.append(_("providers.{name}: runtime {impl} has no completion interface", name=name, impl=impl))
        else:
            errs.append(_("providers.{name}.impl `{impl}` is not known (have: {have})", name=name, impl=impl,
                          have=", ".join([*runtime_ports.PROVIDERS, *runtime_ports.RUNTIMES])))
        k = p.get("key")
        if k is not None and (not isinstance(k, dict) or len([x for x in ("secret", "env") if k.get(x)]) != 1):
            errs.append(_("providers.{name}.key must be {{\"secret\": file name}} or {{\"env\": variable name}} (only "
                          "one of them)", name=name))
        elif k and k.get("secret") and ("/" in k["secret"] or k["secret"].startswith(".")):
            errs.append(_("providers.{name}.key.secret is a file name only (it lives in the platform's secrets directory)",
                          name=name))
        if p.get("location", "cloud") not in ("cloud", "local"):
            errs.append(_("providers.{name}.location can only be cloud / local", name=name))
        if p.get("location") == "local":
            host = urllib.parse.urlparse(p.get("base_url") or "").hostname or ""
            if not (p.get("base_url") and PRIVATE.match(host)):
                errs.append(_("providers.{name}: location local only allows a base_url on this machine, a private "
                              "network or the tailnet (now {base_url!r})", name=name, base_url=p.get("base_url")))
        for g, m in (p.get("models") or {}).items():
            if g not in GROUPS:
                errs.append(_("providers.{name}.models.{group}: the group can only be {groups}", name=name, group=g,
                              groups=" / ".join(GROUPS)))
            if not (isinstance(m, str) and m) and not (isinstance(m, dict) and m.get("name")):
                errs.append(_("providers.{name}.models.{group} must be a model name, or {{name, context?, price?}}",
                              name=name, group=g))
    for g, name in (llm.get("use") or {}).items():
        if g not in GROUPS:
            errs.append(_("use.{group}: the group can only be {groups}", group=g, groups=" / ".join(GROUPS)))
        elif name not in providers:
            errs.append(_("use.{group}: no provider {name}", group=g, name=name))
        elif g not in ((providers[name] or {}).get("models") or {}):
            errs.append(_("use.{group} → {name}, but providers.{name}.models has no {group}", group=g, name=name))
    try:
        import yaml
        known = set(yaml.safe_load((hubconf.ROOT / "contracts" / "llm-points.yaml").read_text())["points"])
    except Exception:
        known = None
    for pt, pin in (llm.get("points") or {}).items():
        if known is not None and pt not in known:
            errs.append(_("points.{point}: not a declared judgment point", point=pt))
        if not isinstance(pin, dict):
            errs.append(_("points.{point} must be an object ({{model}} or {{provider, model?}})", point=pt))
        elif pin.get("provider") is not None and pin["provider"] not in providers:
            errs.append(_("points.{point}.provider must name one of the providers", point=pt))
        elif pin.get("provider") is None and not pin.get("model"):
            errs.append(_("points.{point}: without a provider, model is required (the model the runtime uses)", point=pt))
    return errs
