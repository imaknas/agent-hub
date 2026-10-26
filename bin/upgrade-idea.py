#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""把想法沙盒(登錄 type: idea)升級成正式專案:歷史、檔案、memory 都留著(docs/design/idea-sandboxes.md)。

用法:upgrade-idea.py <沙盒名稱> [--rename <新名稱>] [--public] [--apply] [--json]
  不加 --apply 只印出會做什麼。--json:最後一行印結果(lease-runner 的 POST /v1/sandboxes/<name>/upgrade 用):
    {"result": "upgraded"|"already", "name", "origin"}                                  exit 0
    {"result": "not_found"|"not_idea"|"dirty"|"name_taken"|"failed", "detail"}        exit 1
  已經升級過的(記在 .state/idea-upgrades.json)再叫一次回 already + 升級後的名稱,重送安全。
做的事(照順序):
  1. 檢查(全部在動任何東西之前):登錄是 type: idea;主 repo 沒有未 commit 的追蹤檔變更;新名稱沒被用;
     **升級後的登錄先驗證一次**(以前驗證放在建好 GitHub repo 之後,不合格就停在一半,10-25 idea-1);
     GitHub 上已經有同名 repo:內容是這個沙盒自己的歷史(遠端的 HEAD 在本機歷史裡,或是空的)= 上次做到一半,接著做;
     不是 = name_taken
  2. --rename:呼叫 rename-project.sh <沙盒名稱> <新名稱> --apply(資料夾、memory bare repo/副本、登錄一起改)
  3. gh repo create <github.owner>/<名稱>(預設 private)+ push(repo 已經是自己的就只 push),origin 改成 github.remote
  4. 登錄:type 改成 project、paths.origin 寫上;purpose、enforce(含 Knas 同意過的能力跟 work)、privacy 留著
     (以前建的沙盒沒明寫 enforce.work 的,升級時補上 work: true——想法沙盒本來就能在深談裡動手)
  5. mirror-list.txt 加上 origin;重生 index.md 名冊;commit-hub-files 只 commit 這幾個檔
每做完一個會改外面的步驟(改名、repo)就記進 .state/idea-upgrades.json 的 stage;中途失敗再叫一次,從記錄的地方接著做
(改名過的用新名稱)。不刪 GitHub repo 來「退回」:刪 repo 不能復原,接著做比較安全。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import registry  # noqa: E402

HUB = hubconf.ROOT
RECORD = hubconf.STATE / "idea-upgrades.json"
JSON = False


def done(result: str, code: int, **extra):
    if JSON:
        print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    elif code:
        print(f"upgrade-idea: {extra.get('detail', result)}", file=sys.stderr)
    sys.exit(code)


def fail(msg: str, result: str = "failed") -> None:
    done(result, 1, detail=msg)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, text=True, capture_output=True, **kw)
    if r.returncode != 0:
        fail(f"{' '.join(cmd[:3])} 失敗:{(r.stderr or r.stdout).strip()[-400:]}")
    return r


def records() -> dict:
    try:
        return json.loads(RECORD.read_text())
    except (OSError, ValueError):
        return {}


def record(idea: str, **fields) -> None:
    rec = records()
    rec[idea] = {**rec.get(idea, {}), **fields,
                 "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=1))


def unfinished_rename(name: str) -> str | None:
    """改名端點(project-rename.py)做到一半的改名:同一個專案(舊名稱或新名稱)還沒 done 就回說明。
    兩邊各記各的進度,交錯會讓重試找不到登錄(例:升級改名停一半,中間又被改名),所以有一邊沒做完就不開始另一邊。"""
    try:
        recs = json.loads((hubconf.STATE / "project-renames.json").read_text())
    except (OSError, ValueError):
        return None
    for old, r in recs.items():
        if isinstance(r, dict) and r.get("stage") != "done" and name in (old, r.get("new")):
            return f"{old} → {r.get('new')} 的改名做到一半(stage {r.get('stage')}),先把改名的重試做完"
    return None


def remote_url(owner: str, name: str) -> str:
    return (hubconf.get("github.remote") or "git@github.com:{owner}/{name}.git").format(owner=owner, name=name)


def remote_is_ours(ws: pathlib.Path, url: str) -> bool:
    """遠端是這個沙盒自己推上去的(上次做到一半):空的,或遠端的 HEAD 在本機的歷史裡。"""
    r = subprocess.run(["git", "-C", str(ws), "ls-remote", url, "HEAD"], text=True, capture_output=True)
    if r.returncode != 0:
        return False
    sha = r.stdout.split()[0] if r.stdout.split() else ""
    if not sha:
        return True
    return subprocess.run(["git", "-C", str(ws), "merge-base", "--is-ancestor", sha, "HEAD"],
                          capture_output=True).returncode == 0


def upgraded_doc(doc: dict, final: str, url: str) -> dict:
    """升級後的登錄長什麼樣(改名的話名稱跟資料夾也換)——動手前先拿它驗證。"""
    import copy
    out = copy.deepcopy(doc)
    enf = out.get("enforce")
    if doc.get("type") == "idea" and isinstance(enf, dict) and enf.get("sandbox") and "work" not in enf:
        enf["work"] = True  # 想法沙盒預設可以在深談裡動手(10-25 Knas);以前建的沒明寫,升級時明寫帶過去
    out["type"] = "project"
    paths = out.setdefault("paths", {})
    if final != doc.get("name"):
        out["name"] = final
        for key, old in (("workspace", doc.get("name")), ("memory_bare", f"{doc.get('name')}-memory.git")):
            if isinstance(paths.get(key), str) and paths[key].endswith("/" + old):
                paths[key] = paths[key][: -len(old)] + (final if key == "workspace" else f"{final}-memory.git")
    paths["origin"] = url
    return out


def main(argv: list[str]) -> int:
    global JSON
    args, apply, public, new = [], False, False, None
    it = iter(argv)
    for a in it:
        if a == "--json":
            JSON = True
        elif a == "--apply":
            apply = True
        elif a == "--public":
            public = True
        elif a == "--rename":
            new = next(it, None) or fail("--rename 要給新名稱")
        else:
            args.append(a)
    if len(args) != 1:
        fail("用法:upgrade-idea.py <沙盒名稱> [--rename <新名稱>] [--public] [--apply]")
    name = args[0]
    if (why := unfinished_rename(name)):
        fail(why)
    prev = records().get(name)
    if prev and prev.get("stage", "done") == "done" and (hubconf.registry_dir() / f"{prev['name']}.yaml").exists():
        done("already", 0, name=prev["name"], origin=prev["origin"])  # 升級過了:重送安全
    renamed = bool(prev and prev.get("stage") in ("renamed", "repo") and prev["name"] != name)
    if renamed:  # 上次改名之後停了:登錄跟資料夾已經是新名稱
        if new and new != prev["name"]:
            fail(f"{name} 上次升級做到一半,已經改名成 {prev['name']};要用同一個名稱再叫一次", "name_taken")
        new = None
    final = prev["name"] if renamed else (new or name)
    reg = hubconf.registry_dir() / f"{final if renamed else name}.yaml"
    if not reg.exists():
        fail(f"登錄裡沒有 {name}", "not_found")
    import yaml
    doc = yaml.safe_load(reg.read_text())
    if doc.get("type") != "idea":
        fail(f"{name} 不是想法沙盒(type: {doc.get('type', 'project')})", "not_idea")
    ws = registry.dirs(doc)["workspace"]
    if run(["git", "-C", str(ws), "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        fail(f"{ws} 有未 commit 的追蹤檔變更,先處理", "dirty")
    owner = hubconf.get("github.owner") or fail("hub.json 沒有 github.owner")
    url = remote_url(owner, final)
    errs = registry.validate(upgraded_doc(doc, final, url), final)
    if errs:
        fail(f"升級後的登錄不合格,什麼都沒動:{'; '.join(errs)}")
    if new:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", new):
            fail(f"新名稱只能用小寫英數跟 -:{new}", "name_taken")
        taken = [w for w, hit in ((f"projects/{new}.yaml", (hubconf.registry_dir() / f"{new}.yaml").exists()),
                                  (f"~/workspace/{new}", (hubconf.p("workspace_root", "~/workspace") / new).exists()))
                 if hit]
        if taken:
            fail(f"{new} 已經被用掉了:{', '.join(taken)}", "name_taken")
    repo_exists = subprocess.run(["gh", "repo", "view", f"{owner}/{final}"], capture_output=True).returncode == 0
    if repo_exists and not remote_is_ours(ws, url):
        fail(f"GitHub 上已經有 {owner}/{final}(不是這個沙盒的歷史)", "name_taken")

    plan = [f"{'改名 ' + name + ' → ' + new + '(rename-project.sh)' if new else ('已經改名成 ' + final if renamed else '不改名')}",
            (f"GitHub 上的 {owner}/{final} 是上次建的:只 push" if repo_exists else
             f"gh repo create {owner}/{final} --{'public' if public else 'private'} + push") + f",origin = {url}",
            f"登錄 projects/{final}.yaml:type: project、paths.origin: {url}(purpose/enforce/privacy 留著)",
            "mirror-list.txt 加上 origin、重生 index.md、commit agent-hub"]
    if not JSON:
        print("\n".join(f"  {i}. {p}" for i, p in enumerate(plan, 1)))
    if not apply:
        print("(dry-run,加 --apply 執行)")
        return 0

    if new:
        r = subprocess.run([str(HUB / "bin" / "rename-project.sh"), name, new, "--apply"], text=True,
                           capture_output=JSON)
        if r.returncode != 0:
            fail("rename-project.sh 失敗,停在這裡(還沒建 GitHub repo)")
        record(name, name=final, origin=url, stage="renamed")
        reg = hubconf.registry_dir() / f"{final}.yaml"
        doc = yaml.safe_load(reg.read_text())
        ws = registry.dirs(doc)["workspace"]

    if not repo_exists:
        run(["gh", "repo", "create", f"{owner}/{final}", "--public" if public else "--private",
             f"--source={ws}", "--remote=origin", "--push"])
    has_origin = subprocess.run(["git", "-C", str(ws), "remote", "get-url", "origin"], capture_output=True).returncode == 0
    run(["git", "-C", str(ws), "remote", "set-url" if has_origin else "add", "origin", url])
    if repo_exists:
        run(["git", "-C", str(ws), "push", "-u", "origin", "HEAD"])
    record(name, name=final, origin=url, stage="repo")

    text = reg.read_text()
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    doc = upgraded_doc(doc, final, url)
    errs = registry.validate(doc, final)
    if errs:  # 動手前驗證過;到這裡還不合格是登錄在中途被改了。repo 留著,修好登錄再叫一次會接著做
        fail(f"登錄不合格:{'; '.join(errs)}(GitHub repo 已經建好;修好登錄再叫一次會接著做)")
    reg.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))

    mirror = hubconf.config_file("mirror-list.txt")   # config(實例目錄;沒搬家時就是 hub 裡那份)
    lines = mirror.read_text().splitlines() if mirror.exists() else []
    if url not in lines:
        mirror.write_text("\n".join(lines + [url]) + "\n")
    subprocess.run([str(HUB / "bin" / "gen-index-roster.sh")], check=False)
    subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), f"upgrade idea {name} → project {final} (upgrade-idea.py)",
                    f"projects/{final}.yaml", "mirror-list.txt", "index.md"], check=False)
    record(name, name=final, origin=url, stage="done")
    if not JSON:
        print(f"完成:{name} 已升級成正式專案 {final}({url})")
    done("upgraded", 0, name=final, origin=url)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
