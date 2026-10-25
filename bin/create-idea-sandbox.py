#!/opt/homebrew/bin/python3
"""替 Muninn 的想法開一個探討用的專案(想法沙盒,docs/design/idea-sandboxes.md)。

想法沙盒 = 正常 onboard(有 .agent-memory:深談收回的交接筆記寫在那裡)、沒有 GitHub remote、
登錄標 `type: idea` 的專案,放在 <workspace_root>/idea-<n>。登錄一起寫 purpose、`enforce: {sandbox: true}`
(真正的寫入封鎖)+ 預設的能力(hub.json capabilities 的 default_for,例 gpu;docs/design/capabilities.md)、`privacy: {reflection: stats_only}`,跟登記同一個 commit(onboard 的 ONBOARD_REGISTRY_EXTRA)。

用法:create-idea-sandbox.py <idea-n> <purpose>
輸出:最後一行是一個 JSON 物件(lease-runner 的 POST /v1/sandboxes 直接轉成回應):
  {"result": "created"|"exists_same", "name"}                              exit 0
  {"result": "bad_name"|"exists"|"too_many"|"create_failed", "detail", ...}  exit 1
  too_many 另外帶 limit、current。
- 重送:同名、同 purpose 已經登錄 → exists_same(Muninn 呼叫逾時重送不會開出第二個)。
- 撞名:同名但 purpose 不同、跟正式專案同名、或資料夾/bare repo/memory 副本已經存在 → exists。
- 上限:hub.json 的 ideas.limit(預設 20)個 type: idea 的專案。
- onboard 失敗:清掉這次建的資料夾、bare repo、memory 副本、還沒 commit 的登錄檔(事前都確認過不存在),
  再回 create_failed。
同時只跑一個(鎖在 <state>/idea-sandbox.lock):onboard 會 commit agent-hub。
"""
from __future__ import annotations

import fcntl
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

NAME = re.compile(r"^idea-[0-9]+$")


def default_capabilities() -> list[str]:
    """hub.json capabilities 裡 default_for 有 idea、risk 不是 high 的能力(明寫進登錄,不靠 type 隱含)。"""
    return sorted(k for k, v in hubconf.capabilities().items()
                  if "idea" in (v.get("default_for") or []) and v.get("risk") != "high")
MAX_PURPOSE = 500
HUB = hubconf.ROOT


def out(result: str, code: int, **extra) -> int:
    print(json.dumps({"result": result, **extra}, ensure_ascii=False))
    return code


def registered() -> dict[str, dict]:
    """登錄目錄裡的原始檔(比快取新:剛 onboard 的也算)。"""
    import yaml
    docs = {}
    for f in hubconf.registry_dir().glob("*.yaml"):
        try:
            docs[f.stem] = yaml.safe_load(f.read_text()) or {}
        except Exception:
            docs[f.stem] = {}
    return docs


def create(name: str, purpose: str) -> int:
    if not NAME.match(name):
        return out("bad_name", 1, detail=f"名稱要是 idea-<數字>,收到 {name!r}")
    purpose = " ".join(purpose.split())
    if not purpose:
        return out("bad_name", 1, detail="purpose 不能是空的")
    if len(purpose) > MAX_PURPOSE:
        return out("bad_name", 1, detail=f"purpose 太長({len(purpose)} 字,上限 {MAX_PURPOSE})")

    docs = registered()
    if name in docs:
        d = docs[name]
        if d.get("type") == "idea" and " ".join(str(d.get("purpose", "")).split()) == purpose:
            return out("exists_same", 0, name=name)
        what = "另一個想法" if d.get("type") == "idea" else "一個正式專案"
        return out("exists", 1, detail=f"{name} 已經是{what}的名稱")

    ws = hubconf.p("workspace_root", "~/workspace") / name
    bare = hubconf.p("git_origin", "~/git-origin") / f"{name}-memory.git"
    copy = hubconf.p("memory_copies", "~/.memory-copies") / name
    taken = [str(x) for x in (ws, bare, copy) if x.exists()]
    if taken:
        return out("exists", 1, detail=f"{name} 沒有登錄,但這些已經存在:{', '.join(taken)}")

    limit = int(hubconf.get("ideas.limit", 20))
    current = sum(1 for d in docs.values() if d.get("type") == "idea")
    if current >= limit:
        return out("too_many", 1, detail=f"已經有 {current} 個想法沙盒(上限 {limit})", limit=limit, current=current)

    ws.mkdir(parents=True)
    (ws / "README.md").write_text(
        f"# {name}\n\n探討用的專案(想法沙盒),由 Muninn 替這個想法開的:\n\n> {purpose}\n\n"
        "深談的結論、決定、待辦記在 memory(`.agent-memory/`);要變成正式專案時用 agent-hub 的 "
        "`bin/upgrade-idea.sh`。\n")
    extra = {"type": "idea", "purpose": purpose, "enforce": {"sandbox": True, "capabilities": default_capabilities()},
             "privacy": {"reflection": "stats_only"}}
    env = {**os.environ, "ONBOARD_REGISTRY_EXTRA": json.dumps(extra, ensure_ascii=False)}
    r = subprocess.run([str(HUB / "bin" / "onboard-project.sh"), "--no-remote", str(ws)],
                       capture_output=True, text=True, env=env)
    reg_file = hubconf.registry_dir() / f"{name}.yaml"
    if r.returncode != 0:
        tracked = subprocess.run(["git", "-C", str(HUB), "ls-files", "--error-unmatch", str(reg_file)],
                                 capture_output=True).returncode == 0
        for x in (ws, bare, copy):
            shutil.rmtree(x, ignore_errors=True)
        if reg_file.exists() and not tracked:
            reg_file.unlink()
        tail = " / ".join((r.stderr or r.stdout).strip().splitlines()[-3:])
        note = "(登錄檔已經 commit,沒有刪,要手動處理)" if tracked else ""
        return out("create_failed", 1, detail=f"onboard 失敗:{tail}{note}")

    subprocess.run(["/opt/homebrew/bin/python3", str(HUB / "bin" / "registry.py"), "check", name],
                   capture_output=True)  # 更新快取:attachable 馬上看得到
    # sandbox 設定馬上寫進去,不等 15 分鐘的平台 job:work 模式(lease-runner 的 work_ready)要看到它才放行
    subprocess.run(["/opt/homebrew/bin/python3", str(HUB / "bin" / "render-project-settings.py"), "--only", name],
                   capture_output=True)
    return out("created", 0, name=name)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.split("\n\n")[2], file=sys.stderr)
        return 2
    lock = hubconf.STATE / "idea-sandbox.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return create(argv[0], argv[1])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
