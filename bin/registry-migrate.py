#!/usr/bin/env python3
"""從舊的設定(onboarded-projects.tsv、schedule.yaml、agent-jobs/)機械產生專案登錄,並驗證等價。

第一階段的遷移用;全部專案切換完、onboarded-projects.tsv 拿掉之後就可以刪掉這支。

  registry-migrate.py import [--force]   產生 projects/<name>.yaml(已經存在的不覆蓋,除非 --force)
       schedule.yaml 的 job 照指令歸到專案:`cd <services_root>/<p> && …` → p(cwd runtime);
       `run-agent-job.sh <name> <workspace_root>/<p>` → p(kind agent,prompt 複製到 projects/<p>/jobs/);
       hub 自己的 bin/… → 平台自己。歸不到的(例:沒有 cwd 的 agent job)留在 schedule.yaml,印出來。
       每個 job 帶 legacy_name = 舊名稱,舊名稱還在 schedule.yaml 時登錄的不會跑(scheduler.all_jobs)。
  registry-migrate.py diff [project...]   登錄算出來的 job 跟 schedule.yaml 的逐欄位比;指令比較時把
       `cd <cwd> && ` 跟指令裡 `<cwd>/` 的絕對路徑去掉。全部一樣才 exit 0。
  registry-migrate.py cutover <project> [--accept-diff]   diff 一樣才從 schedule.yaml 拿掉那個專案的舊項目
       (保留註解跟順序);差異是刻意的(例:登錄多加了 after:)就加 --accept-diff
"""
from __future__ import annotations

import os
import pathlib
import re
import shlex
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import registry  # noqa: E402

import yaml  # noqa: E402

HUB = hubconf.ROOT
SCHEDULE = HUB / "schedule.yaml"
HOME = str(pathlib.Path.home())
SERVICES = str(hubconf.p("services_root"))
WORKSPACE = str(hubconf.p("workspace_root"))
HUBP = hubconf.hub_project()
FIELDS = ("time", "every_minutes", "days_of_week", "days_of_month", "months", "end_date", "between",
          "background", "after", "max_attempts_per_day")
CD = re.compile(r"^cd (\S+) && (.*)$", re.S)


def tilde(p: str) -> str:
    return "~" + p[len(HOME):] if p.startswith(HOME) else p


def legacy_jobs() -> list[dict]:
    return (yaml.safe_load(SCHEDULE.read_text()) or {}).get("jobs", [])


def classify(job: dict) -> tuple[str | None, dict | None]:
    """舊 job → (專案, 登錄 job);歸不到回傳 (None, None)。"""
    cmd = job["command"].strip()
    entry: dict = {"name": None, "legacy_name": job["name"]}
    if "every_minutes" in job:
        m = job["every_minutes"]
        entry["every"] = f"{m // 60}h" if m % 60 == 0 else f"{m}m"
    else:
        entry["at"] = str(job["time"])
    for src, dst in (("days_of_week", "days"), ("days_of_month", "days_of_month"), ("months", "months"),
                     ("end_date", "until"), ("between", "between"), ("background", "background"),
                     ("max_attempts_per_day", "max_attempts_per_day")):
        if src in job:
            entry[dst] = job[src]
    if job.get("after"):
        entry["after"] = list(job["after"])  # 先記舊名稱,_fix_after 再換
    if not job.get("enabled", True):
        return None, None  # 停用的不搬,留在 schedule.yaml
    agent = re.match(rf"^{re.escape(str(HUB))}/bin/run-agent-job\.sh (\S+)(?: (\S+))?$", cmd)
    if agent:
        name, cwd = agent.group(1), agent.group(2)
        if not cwd or not cwd.startswith(WORKSPACE + "/"):
            return None, None
        proj = cwd[len(WORKSPACE) + 1:]
        entry.update(kind="agent", cwd="workspace", prompt=f"jobs/{_short(name, proj)}.md", _prompt_src=name)
        entry["name"] = _short(name, proj)
        return proj, entry
    m = CD.match(cmd)
    if m and m.group(1).startswith(SERVICES + "/"):
        proj = m.group(1)[len(SERVICES) + 1:]
        run = m.group(2).replace(m.group(1) + "/", "")
        run = run.replace(str(HUB / "bin" / "git-safe-push.sh"), '"$SAFE_PUSH"')
        entry.update(run=run, name=_short(job["name"], proj))
        return proj, entry
    if cmd.startswith(str(HUB) + "/bin/") or re.match(rf"^/usr/bin/python3 {re.escape(str(HUB))}/bin/", cmd):
        entry.update(run=cmd.replace(str(HUB) + "/", ""), cwd="hub", name=job["name"])
        return HUBP, entry
    return None, None


def _short(name: str, proj: str) -> str:
    """project-36-ig-midnight → ig-midnight(去掉專案名稱前綴)。"""
    return name[len(proj) + 1:] if name.startswith(proj + "-") else name


def cmd_import(force: bool) -> int:
    reg = hubconf.registry_dir()
    reg.mkdir(parents=True, exist_ok=True)
    docs: dict[str, dict] = {}
    # 名冊:舊的 onboarded-projects.tsv
    tsv = HUB / "onboarded-projects.tsv"
    for line in tsv.read_text().splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) < 4 or not cols[0]:
            continue
        name = cols[0]
        bare = cols[3].split(":", 1)[-1]  # hub-host:~/git-origin/x.git → ~/git-origin/x.git
        docs[name] = {"v": 1, "name": name,
                      "paths": {"workspace": tilde(cols[1]), "memory_bare": bare, "origin": cols[2]},
                      "memory": True, "privacy": {"reflection": "include"}, "push_on_merge": False,
                      "mailbox": {"escalate": True},
                      "publishes": [{"kind": "state", "path": "STATE.md", "contract": "state-todo", "v": 1}],
                      "jobs": []}
        if (hubconf.p("services_root") / name).is_dir():
            docs[name]["runtime"] = {"deploy": "bin/deploy.sh"} if (pathlib.Path(cols[1]) / "bin" / "deploy.sh").exists() else {}
    docs[HUBP] = {"v": 1, "name": HUBP, "memory": False, "push_on_merge": False, "jobs": []}
    # 隱私:舊的寫死清單(reflect_llm.EXCLUDED)
    import reflect_llm
    for name in getattr(reflect_llm, "LEGACY_EXCLUDED", reflect_llm.EXCLUDED):
        if name in docs:
            docs[name]["privacy"]["reflection"] = "exclude"
    left = []
    for job in legacy_jobs():
        proj, entry = classify(job)
        if proj is None or proj not in docs:
            left.append(job["name"])
            continue
        src = entry.pop("_prompt_src", None)
        if src:
            dst = reg / proj / entry["prompt"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy(HUB / "agent-jobs" / f"{src}.md", dst)
        docs[proj]["jobs"].append(entry)
    # after:舊名稱 → 登錄名稱
    legacy_to_new = {j["legacy_name"]: f"{p}/{j['name']}" for p, d in docs.items() for j in d["jobs"]}
    for p, d in docs.items():
        for j in d["jobs"]:
            if "after" in j:
                j["after"] = [legacy_to_new.get(a, a).replace(f"{p}/", "", 1) if legacy_to_new.get(a, a).startswith(p + "/")
                              else legacy_to_new.get(a, a) for a in j["after"]]
    wrote = 0
    for name, d in sorted(docs.items()):
        f = reg / f"{name}.yaml"
        if f.exists() and not force:
            continue
        if not d.get("jobs"):
            d.pop("jobs")
        f.write_text(f"# 專案登錄(schema v1,見 bin/registry.py);由 registry-migrate.py import 產生,之後直接改這個檔\n"
                     + yaml.safe_dump(d, allow_unicode=True, sort_keys=False, width=200))
        wrote += 1
    print(f"寫了 {wrote} 個登錄檔(已存在的沒覆蓋);留在 schedule.yaml 的:{', '.join(left) or '無'}")
    return 0


def norm_cmd(cmd: str) -> str:
    cmd = cmd.strip()
    m = CD.match(cmd)
    if m:
        cwd, cmd = m.group(1).strip("'\""), m.group(2)
        cmd = cmd.replace(cwd + "/", "")
    cmd = cmd.replace(str(HUB) + "/", "").replace('"$SAFE_PUSH"', "bin/git-safe-push.sh")
    cmd = re.sub(r"'([^' ]*)'", r"\1", cmd)  # shlex.quote 加的引號
    return " ".join(cmd.split())


def cmd_diff(only: list[str]) -> int:
    index = registry.refresh()
    legacy = {j["name"]: j for j in legacy_jobs()}
    reg_jobs, _, _ = registry.scheduler_jobs(index, set())
    bad = 0
    for rj in reg_jobs:
        if only and rj["project"] not in only:
            continue
        doc = index["projects"][rj["project"]]
        src = next(j for j in doc["jobs"] if f"{doc['name']}/{j['name']}" == rj["name"])
        old = legacy.get(src.get("legacy_name"))
        if old is None:
            print(f"  {rj['name']}:schedule.yaml 沒有舊項目({src.get('legacy_name')}),新增的 job")
            continue
        diffs = []
        for f in FIELDS:
            a, b = old.get(f), rj.get(f)
            if f == "after":
                a = [hubconf_alias(x, index) for x in (a or [])] or None
            if str(a) != str(b) and not (a is None and b is None):
                diffs.append(f"{f}: {a!r} → {b!r}")
        if rj.get("agent_name"):
            if old["command"].split()[1] != rj["agent_name"]:
                diffs.append(f"agent 名稱:{old['command'].split()[1]} → {rj['agent_name']}")
        elif norm_cmd(old["command"]) != norm_cmd(rj["command"]):
            diffs.append(f"command:\n      舊 {norm_cmd(old['command'])}\n      新 {norm_cmd(rj['command'])}")
        if diffs:
            bad += 1
            print(f"✗ {rj['name']} ← {old['name']}\n    " + "\n    ".join(diffs))
        else:
            print(f"✓ {rj['name']} ← {old['name']}")
    if index["errors"]:
        bad += 1
        for n, errs in index["errors"].items():
            if not only or n in only:
                print(f"✗ 登錄 {n} 不合格:{'; '.join(errs)}")
    return 1 if bad else 0


def hubconf_alias(name: str, index: dict) -> str:
    for p, d in index["projects"].items():
        for j in d.get("jobs") or []:
            if j.get("legacy_name") == name:
                return f"{p}/{j['name']}"
    return name


def cmd_cutover(project: str, accept: bool) -> int:
    if cmd_diff([project]) != 0 and not accept:
        print(f"{project} 的 diff 不一樣,不切換(差異是刻意的就加 --accept-diff)")
        return 1
    index = registry.refresh()
    names = {j.get("legacy_name") for j in index["projects"][project].get("jobs") or []} - {None}
    # 照行刪,保留檔案裡的註解跟其他 job 的寫法(schedulectl 的 yaml.dump 會把註解洗掉)
    lines = SCHEDULE.read_text().splitlines(keepends=True)
    out, skip, removed = [], False, []
    for l in lines:
        m = re.match(r"^- name: (\S+)\s*$", l)
        if m:
            skip = m.group(1) in names
            if skip:
                removed.append(m.group(1))
        elif skip and not l.startswith("  "):
            skip = False
        if not skip:
            out.append(l)
    SCHEDULE.write_text("".join(out))
    print(f"從 schedule.yaml 拿掉 {len(removed)} 項:{', '.join(removed)}")
    return 0


def main() -> int:
    a = sys.argv[1:]
    if not a:
        print(__doc__)
        return 2
    if a[0] == "import":
        return cmd_import("--force" in a)
    if a[0] == "diff":
        return cmd_diff(a[1:])
    if a[0] == "cutover" and len(a) >= 2:
        return cmd_cutover(a[1], "--accept-diff" in a)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
