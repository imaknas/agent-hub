#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""每晚的契約檢查(平台 job;零 LLM)。各 repo 自己的測試只做 ②(範例照自己的副本驗證,不依賴別的 repo);
這支做只有平台做得到的:

  ① 副本 == 原版:專案登錄裡每個 `consumes`(copy 路徑,from 哪個專案)跟那個專案 `provides` 的原版比,
     兩個方向都比(agent-hub 的 vendor 副本 ↔ 別人的原版;別人 vendor 的 agent-hub 契約 ↔ agent-hub 的原版)
  ③ 實際資料照契約:todo.md、所有登錄檔、最近的匯流排事件、最新一週的 reflect json、
     owner 助理現在的 `handoffs --json` 輸出

不一致就在匯流排發 job.failed(狀態頁看得到)並 exit 1。

用法:contracts-check.py [--quiet]
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
import registry  # noqa: E402

import yaml  # noqa: E402

HUB = hubconf.ROOT
C = HUB / "contracts"


def ws(index: dict, name: str) -> pathlib.Path | None:
    if name in (index.get("modules") or {}):
        return HUB   # 模組的檔案都在平台裡
    doc = index["projects"].get(name)
    return registry.dirs(doc)["workspace"] if doc else None


def cross_copies(index: dict) -> list[str]:
    errs = []
    # 專案 + 開著的模組(沒開的模組讀的契約不比對:例:沒有助理就不用助理的格式)
    for name, doc in [*index["projects"].items(), *(index.get("modules") or {}).items()]:
        for cons in doc.get("consumes") or []:
            producer = cons.get("from") or hubconf.hub_project()
            pdoc = index["projects"].get(producer)
            provs = [p for p in (pdoc or {}).get("provides") or [] if p["contract"] == cons["contract"]]
            # 兩版並存時同一個契約會有兩筆 provides:先找同一版,沒有才拿第一筆(底下會報版本不合)
            prov = next((p for p in provs if p.get("v") == cons.get("v")), provs[0] if provs else None)
            if prov is None:
                errs.append(f"{name} 讀 {cons['contract']},但 {producer} 的登錄沒有 provides 它")
                continue
            if prov.get("v") != cons.get("v"):
                errs.append(f"{name} 讀 {cons['contract']} v{cons.get('v')},{producer} 現在是 v{prov.get('v')}"
                            "(producer 要兩版並存到 consumer 換完)")
            orig, copy = ws(index, producer) / prov["schema"], ws(index, name) / cons["copy"]
            if not orig.exists() or not copy.exists():
                errs.append(f"{cons['contract']}:{'原版' if not orig.exists() else '副本'}不在({orig if not orig.exists() else copy})")
            elif not contracts.same(orig, copy):
                errs.append(f"{name} 的 {cons['contract']} 副本跟 {producer} 的原版不一樣:{copy} vs {orig}")
    return errs


def live(index: dict) -> list[str]:
    errs = []
    todo = hubconf.data_file("todo.md")
    if todo.exists():
        errs += [f"todo.md:{e}" for e in contracts.check_todo_md(todo.read_text(), C / "todo-md.v1.md", hubconf.owner_tag())]
    import minischema
    pts_schema = json.loads((C / "llm-points.v1.schema.json").read_text())  # 判斷點登錄的共用格式(muninn 也照它檢查)
    errs += [f"contracts/llm-points.yaml:{e}" for e in minischema.errors(pts_schema, yaml.safe_load((C / "llm-points.yaml").read_text()))]
    schema = json.loads((C / "project.v1.schema.json").read_text())
    for f in sorted(hubconf.registry_dir().glob("*.yaml")):
        errs += [f"projects/{f.name}:{e}" for e in minischema.errors(schema, yaml.safe_load(f.read_text()))]
    bus = hubconf.bus_db()
    if bus.exists():
        c = sqlite3.connect(bus, timeout=30)
        c.row_factory = sqlite3.Row
        for r in c.execute("SELECT * FROM events ORDER BY seq DESC LIMIT 500"):
            ev = dict(r)
            ev["payload"] = json.loads(ev["payload"] or "{}")
            ev["payload"].setdefault("v", 1)  # 2026-10-22 之前的事件沒有 v,讀成 1
            if (e := contracts.check_json(C / "hubbus.v1.schema.json", ev)):
                errs.append(f"匯流排事件 {ev['id']}:{e[0]}")
    weeks = sorted(hubconf.data_file("reflect").glob("20*-W*.json"))
    if weeks:
        d = json.loads(weeks[-1].read_text())
        d.setdefault("v", 1)
        errs += [f"{weeks[-1].name}:{e}" for e in contracts.check_json(C / "reflect-week.v1.schema.json", d)]
    cli = hubconf.p("assistant.cli")
    copy = C / "vendor" / "muninn" / "handoffs.v1.schema.json"
    if cli and cli.exists() and copy.exists():
        r = subprocess.run([str(cli), "handoffs", "--json"], cwd=cli.parents[2], capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(r.stdout)
            errs += [f"handoffs --json:{e}" for e in contracts.check_json(copy, data)[:5]]
        except ValueError:
            errs.append(f"handoffs --json 不是 JSON(exit {r.returncode})")
    return errs


def llm_lint() -> list[str]:
    """第二階段:bin/ 裡每一次叫 Claude CLI 的非互動呼叫都要經過 bin/llm.py(contracts/llm-points.yaml 宣告的判斷點)。"""
    r = subprocess.run([sys.executable, str(HUB / "bin" / "llm.py"), "lint"], capture_output=True, text=True)
    return [f"LLM 呼叫:{l}" for l in r.stdout.splitlines() if l.strip()] + \
        ([f"llm.py lint 跑不起來:{r.stderr.strip()[-200:]}"] if r.returncode not in (0, 1) else [])


def main() -> int:
    index = registry.refresh()
    errs = cross_copies(index) + live(index) + llm_lint()
    for e in errs:
        print(f"✗ {e}")
    if errs:
        subprocess.run([sys.executable, str(HUB / "bin" / "hubbus"), "publish", "job.failed",
                        "--source", "contracts-check", "--payload",
                        json.dumps({"job": "contracts-check", "detail": f"{len(errs)} 項:" + ";".join(errs[:3])},
                                   ensure_ascii=False)], capture_output=True)
        return 1
    if "--quiet" not in sys.argv:
        print("契約:全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
