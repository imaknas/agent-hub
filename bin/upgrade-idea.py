#!/opt/homebrew/bin/python3
"""把想法沙盒(登錄 type: idea)升級成正式專案:歷史、檔案、memory 都留著(docs/design/idea-sandboxes.md)。

用法:upgrade-idea.py <idea-n> [--rename <新名稱>] [--public] [--apply] [--json]
  不加 --apply 只印出會做什麼。--json:最後一行印結果(lease-runner 的 POST /v1/sandboxes/<name>/upgrade 用):
    {"result": "upgraded"|"already", "name", "origin"}                                  exit 0
    {"result": "not_found"|"not_idea"|"dirty"|"name_taken"|"failed", "detail"}        exit 1
  已經升級過的(記在 .state/idea-upgrades.json)再叫一次回 already + 升級後的名稱,重送安全。
做的事(照順序):
  1. 檢查:登錄是 type: idea;主 repo 沒有未 commit 的追蹤檔變更
  2. --rename:呼叫 rename-project.sh <idea-n> <新名稱> --apply(資料夾、memory bare repo/副本、登錄一起改)
  3. gh repo create <github.owner>/<名稱>(預設 private)+ push,origin 改成 ssh URL
  4. 登錄:type 改成 project、paths.origin 寫上;purpose、enforce、privacy 留著(要改另外改)
  5. mirror-list.txt 加上 origin;重生 index.md 名冊;commit-hub-files 只 commit 這幾個檔
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
        fail("用法:upgrade-idea.py <idea-n> [--rename <新名稱>] [--public] [--apply]")
    name = args[0]
    prev = records().get(name)
    if prev and (hubconf.registry_dir() / f"{prev['name']}.yaml").exists():
        done("already", 0, name=prev["name"], origin=prev["origin"])  # 升級過了:重送安全
    reg = hubconf.registry_dir() / f"{name}.yaml"
    if not reg.exists():
        fail(f"登錄裡沒有 {name}", "not_found")
    import yaml
    doc = yaml.safe_load(reg.read_text())
    if doc.get("type") != "idea":
        fail(f"{name} 不是想法沙盒(type: {doc.get('type', 'project')})", "not_idea")
    ws = registry.dirs(doc)["workspace"]
    if run(["git", "-C", str(ws), "status", "--porcelain", "--untracked-files=no"]).stdout.strip():
        fail(f"{ws} 有未 commit 的追蹤檔變更,先處理", "dirty")
    final = new or name
    owner = hubconf.get("github.owner") or fail("hub.json 沒有 github.owner")
    url = f"git@github.com:{owner}/{final}.git"
    if new:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", new):
            fail(f"新名稱只能用小寫英數跟 -:{new}", "name_taken")
        taken = [w for w, hit in ((f"projects/{new}.yaml", (hubconf.registry_dir() / f"{new}.yaml").exists()),
                                  (f"~/workspace/{new}", (hubconf.p("workspace_root", "~/workspace") / new).exists()))
                 if hit]
        if taken:
            fail(f"{new} 已經被用掉了:{', '.join(taken)}", "name_taken")
    if subprocess.run(["gh", "repo", "view", f"{owner}/{final}"], capture_output=True).returncode == 0:
        fail(f"GitHub 上已經有 {owner}/{final}", "name_taken")

    plan = [f"{'改名 ' + name + ' → ' + new + '(rename-project.sh)' if new else '不改名'}",
            f"gh repo create {owner}/{final} --{'public' if public else 'private'} + push,origin = {url}",
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
        reg = hubconf.registry_dir() / f"{final}.yaml"
        doc = yaml.safe_load(reg.read_text())
        ws = registry.dirs(doc)["workspace"]

    run(["gh", "repo", "create", f"{owner}/{final}", "--public" if public else "--private",
         f"--source={ws}", "--remote=origin", "--push"])
    run(["git", "-C", str(ws), "remote", "set-url", "origin", url])

    text = reg.read_text()
    head = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    doc["type"] = "project"
    doc.setdefault("paths", {})["origin"] = url
    errs = registry.validate(doc, final)
    if errs:
        fail(f"登錄不合格:{'; '.join(errs)}(GitHub repo 已經建好,登錄要手動補)")
    reg.write_text(head + yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))

    mirror = HUB / "mirror-list.txt"
    lines = mirror.read_text().splitlines() if mirror.exists() else []
    if url not in lines:
        mirror.write_text("\n".join(lines + [url]) + "\n")
    subprocess.run([str(HUB / "bin" / "gen-index-roster.sh")], check=False)
    subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), f"upgrade idea {name} → project {final} (upgrade-idea.py)",
                    f"projects/{final}.yaml", "mirror-list.txt", "index.md"], check=False)
    rec = records()
    rec[name] = {"name": final, "origin": url, "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
    RECORD.parent.mkdir(parents=True, exist_ok=True)
    RECORD.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    if not JSON:
        print(f"完成:{name} 已升級成正式專案 {final}({url})")
    done("upgraded", 0, name=final, origin=url)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
