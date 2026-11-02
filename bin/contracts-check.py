#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The nightly contract check (platform job; zero LLM). Each repo's own tests only do ② (examples validated against
its own copy, not depending on other repos); this one does what only the platform can:

  ① copy == original: every `consumes` in the project registry (copy path, from which project) is compared with the
     original that project `provides`, in both directions (brunna's vendor copies ↔ others' originals; others'
     vendored brunna contracts ↔ brunna's originals)
  ③ real data follows the contracts: todo.md, every registry file, recent bus events, the latest week's reflect json,
     the owner's assistant's current `handoffs --json` output

On a mismatch it publishes job.failed on the bus (visible on the status page) and exits 1.

Usage: contracts-check.py [--quiet]
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import contracts  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import names  # noqa: E402
import registry  # noqa: E402

import yaml  # noqa: E402

_ = i18n.domain(__file__)
HUB = hubconf.ROOT
C = HUB / "contracts"


def ws(index: dict, name: str) -> pathlib.Path | None:
    if name in (index.get("modules") or {}):
        return HUB   # a module's files are all in the platform
    doc = index["projects"].get(name)
    return registry.dirs(doc)["workspace"] if doc else None


def cross_copies(index: dict) -> list[str]:
    errs = []
    # projects + enabled modules (contracts read by a module that's off aren't compared: e.g. no assistant, no need for
    # the assistant's format)
    for name, doc in [*index["projects"].items(), *(index.get("modules") or {}).items()]:
        for cons in doc.get("consumes") or []:
            producer = cons.get("from") or hubconf.hub_project()
            if producer in names.all("project"):
                # the platform under either of its names: its entry is the one of type platform, whatever its stem
                producer = hubconf.hub_project()
                pdoc = next((d for d in index["projects"].values() if d.get("type") == "platform"),
                            index["projects"].get(producer))
            else:
                pdoc = index["projects"].get(producer)
            provs = [p for p in (pdoc or {}).get("provides") or [] if p["contract"] == cons["contract"]]
            # while two versions coexist the same contract has two provides: look for the same version first, else take
            # the first one (the version mismatch is reported below)
            prov = next((p for p in provs if p.get("v") == cons.get("v")), provs[0] if provs else None)
            if prov is None:
                errs.append(_("{name} reads {contract}, but {producer}'s registry entry does not provide it", name=name,
                              contract=cons['contract'], producer=producer))
                continue
            if prov.get("v") != cons.get("v"):
                errs.append(_("{name} reads {contract} v{v}, {producer} is now at v{current} "
                              "(the producer keeps both versions until the consumer has switched)", name=name,
                              contract=cons['contract'], v=cons.get('v'), producer=producer, current=prov.get('v')))
            orig, copy = registry.dirs(pdoc)["workspace"] / prov["schema"], ws(index, name) / cons["copy"]
            if not orig.exists() or not copy.exists():
                errs.append(_("{contract}: the {which} is missing ({path})", contract=cons['contract'],
                              which=_("original") if not orig.exists() else _("copy"),
                              path=orig if not orig.exists() else copy))
            elif not contracts.same(orig, copy):
                errs.append(_("{name}'s copy of {contract} differs from {producer}'s original: {copy} vs {orig}",
                              name=name, contract=cons['contract'], producer=producer, copy=copy, orig=orig))
    return errs


def live(index: dict) -> list[str]:
    errs = []
    todo = hubconf.data_file("todo.md")
    if todo.exists():
        errs += [f"todo.md:{e}" for e in contracts.check_todo_md(todo.read_text(), C / "todo-md.v2.md", hubconf.owner_tag())]
    import minischema
    # the shared format of the judgment-point registry (the Muninn project checks against it too)
    pts_schema = json.loads((C / "llm-points.v1.schema.json").read_text())
    errs += [f"contracts/llm-points.yaml:{e}" for e in minischema.errors(pts_schema, yaml.safe_load((C / "llm-points.yaml").read_text()))]
    # each registry file against the contract of its own version (v1 files are upgraded when read, until config apply
    # rewrites them)
    schemas = {v: json.loads((C / f"project.v{v}.schema.json").read_text()) for v in (1, 2)}
    for f in sorted([*hubconf.registry_dir().glob("*.yaml"), hubconf.platform_registry()]):
        doc = yaml.safe_load(f.read_text())
        schema = schemas.get(doc.get("v") if isinstance(doc, dict) else None, schemas[2])
        errs += [f"projects/{f.name}:{e}" for e in minischema.errors(schema, doc)]
    bus = hubconf.bus_db()
    if bus.exists():
        c = sqlite3.connect(bus, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 500")]
        finally:
            c.close()
        for ev in rows:
            ev["payload"] = json.loads(ev["payload"] or "{}")
            ev["payload"].setdefault("v", 1)  # events before 2026-10-22 have no v; read as 1
            if (e := contracts.check_json(C / "hubbus.v1.schema.json", ev)):
                errs.append(_("bus event {id}: {problem}", id=ev['id'], problem=e[0]))
    weeks = sorted(hubconf.data_file("reflect").glob("20*-W*.json"))
    if weeks:
        d = json.loads(weeks[-1].read_text())
        d.setdefault("v", 1)   # weeks before 2026-W43 have no v: read as 1
        schema = C / f"reflect-week.v{d['v']}.schema.json"
        errs += [f"{weeks[-1].name}:{e}" for e in contracts.check_json(schema, d)] if schema.exists() else \
            [_("{week}: no contract for reflect-week v{v}", week=weeks[-1].name, v=d["v"])]
    cli = hubconf.p("assistant.cli")
    copy = C / "vendor" / "muninn" / "handoffs.v1.schema.json"
    if cli and cli.exists() and copy.exists():
        r = subprocess.run([str(cli), "handoffs", "--json"], cwd=cli.parents[2], capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(r.stdout)
            errs += [f"handoffs --json:{e}" for e in contracts.check_json(copy, data)[:5]]
        except ValueError:
            errs.append(_("handoffs --json is not JSON (exit {code})", code=r.returncode))
    return errs


def llm_lint() -> list[str]:
    """Phase two: every non-interactive call to the Claude CLI in bin/ must go through bin/llm.py (the judgment points
    declared in contracts/llm-points.yaml)."""
    r = subprocess.run([sys.executable, str(HUB / "bin" / "llm.py"), "lint"], capture_output=True, text=True)
    return [_("LLM call: {problem}", problem=l) for l in r.stdout.splitlines() if l.strip()] + \
        ([_("llm.py lint does not run: {error}", error=r.stderr.strip()[-200:])] if r.returncode not in (0, 1) else [])


def main() -> int:
    index = registry.refresh()
    errs = cross_copies(index) + live(index) + llm_lint()
    for e in errs:
        print(f"✗ {e}")
    if errs:
        subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "publish", "job.failed",
                        "--source", "contracts-check", "--payload",
                        json.dumps({"job": "contracts-check",
                                    "detail": _("{n} {n:problem|problems}: ", n=len(errs)) + _("; ").join(errs[:3])},
                                   ensure_ascii=False)], capture_output=True)
        return 1
    if "--quiet" not in sys.argv:
        print(_("contracts: all consistent"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
