#!/usr/bin/env python3
"""第二階段:每個專案的 sandbox + 權限規則從專案登錄產生,寫進 `.claude/settings.local.json`(managed file)。

要寫什麼:policy.py 算中立的政策、runtime_claude.settings() 翻成 Claude Code 的格式;這支只管寫檔、記錄、偵測偏移。

只對登錄 `enforce: {sandbox: true}` 的專案寫;其他專案只算出「會寫什麼」(--report)。平台自己(agent-hub)不套。
寫的內容:
  sandbox(整個 key 由平台管):enabled、failIfUnavailable、allowUnsandboxedCommands: false、
    filesystem.allowWrite(自己的 runtime、匯流排目錄、工具 cache、登錄的 allow_write)、
    filesystem.denyRead(其他專案的 workspace/runtime、memory 原始筆記、平台的 secrets)、
    network.allowedDomains(平台預設 + 登錄的 network)
  permissions.deny / permissions.allow 裡平台加的那幾條(其他條不動——互動 session 按「一律允許」也寫在這裡):
    deny  Read/Edit 其他專案的 workspace/runtime、Edit 平台/bare repo/memory 副本/LaunchAgents、
          Read memory 副本的原始筆記、Read 平台 secrets
    allow 信箱 CLI、合併閘門(跨界的唯一通道)
  上一次寫了哪些條記在 .state/rendered-settings/<專案>.json,下次先拿掉再加,不會累積。

偏移偵測:實際檔案跟應該的不一樣(session 改了自己的設定、或有人手改)→ 印出來、exit 1(平台 job 失敗 →
狀態頁),而且馬上寫回去。

用法:render-project-settings.py [--report] [--only <專案>]
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import policy  # noqa: E402
import registry  # noqa: E402
import runtime_claude  # noqa: E402

RENDERED = hubconf.STATE / "rendered-settings"


def desired(name: str, doc: dict, index: dict) -> tuple[dict, list[str], list[str]]:
    """(sandbox, deny 規則, allow 規則):中立政策(policy.py)翻成 Claude Code 的設定(runtime_claude.py)。"""
    return runtime_claude.settings(policy.policy(name, doc, index))


def settings_path(doc: dict) -> pathlib.Path | None:
    ws = registry.dirs(doc)["workspace"]
    return runtime_claude.settings_path(ws) if ws else None


def apply(name: str, doc: dict, index: dict, report: bool) -> list[str]:
    """回傳偏移(實際跟應該的不一樣的地方)。report=True 只算、不寫。"""
    path = settings_path(doc)
    if not path:
        return []
    sandbox, deny, allow = desired(name, doc, index)
    cur = json.loads(path.read_text()) if path.exists() else {}
    prev = json.loads((RENDERED / f"{name}.json").read_text()) if (RENDERED / f"{name}.json").exists() else {}
    perms = cur.get("permissions", {})
    drift = []
    if prev:  # 寫過才談得上偏移
        if cur.get("sandbox") != prev.get("sandbox"):
            drift.append("sandbox 被改過")
        for key in ("deny", "allow"):
            missing = set(prev.get(key, [])) - set(perms.get(key, []))
            if missing:
                drift.append(f"permissions.{key} 少了 {len(missing)} 條(例:{sorted(missing)[0]})")
    if report:
        print(f"{name}:會寫 sandbox(allowWrite {len(sandbox['filesystem']['allowWrite'])}、denyRead "
              f"{len(sandbox['filesystem']['denyRead'])}、網域 {len(sandbox['network']['allowedDomains'])})、"
              f"deny {len(deny)} 條、allow {len(allow)} 條 → {path}")
        return drift
    new = dict(cur)
    new["sandbox"] = sandbox
    p2 = dict(perms)
    for key, mine in (("deny", deny), ("allow", allow)):
        keep = [r for r in p2.get(key, []) if r not in set(prev.get(key, []))]  # 拿掉上次平台加的,留別人的
        p2[key] = keep + [r for r in mine if r not in keep]
    new["permissions"] = p2
    if new != cur:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    RENDERED.mkdir(parents=True, exist_ok=True)
    (RENDERED / f"{name}.json").write_text(json.dumps({"sandbox": sandbox, "deny": deny, "allow": allow},
                                                      ensure_ascii=False, indent=1))
    return drift


def unapply(name: str, doc: dict) -> None:
    """登錄關掉 enforce:拿掉平台寫的 sandbox 跟那幾條規則(退回)。"""
    rec = RENDERED / f"{name}.json"
    path = settings_path(doc)
    if not rec.exists() or not path or not path.exists():
        return
    prev = json.loads(rec.read_text())
    cur = json.loads(path.read_text())
    cur.pop("sandbox", None)
    perms = cur.get("permissions", {})
    for key in ("deny", "allow"):
        if key in perms:
            perms[key] = [r for r in perms[key] if r not in set(prev.get(key, []))]
    path.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n")
    rec.unlink()
    print(f"{name}:enforce 關了,已拿掉平台的 sandbox/規則")


GATE_MARK = "# agent-hub:gate (managed; registry gate: strict)"
GATE_HOOK = GATE_MARK + """
# 主 checkout 的 main/master 不能直接 commit 或 merge commit;merge-branch 是 fast-forward,不會觸發這個 hook。
b=$(git symbolic-ref --short -q HEAD)
case "$b" in main|master) ;; *) exit 0 ;; esac
[ -n "$MERGE_GATE" ] && exit 0
echo "這個專案開了合併閘門(gate: strict):不能直接 commit 到 $b。在自己的 worktree/branch 做,完成用 ~/agent-hub/bin/merge-branch。" >&2
exit 1
"""


def gate(name: str, doc: dict, report: bool) -> None:
    """登錄 gate: strict → 裝 pre-commit / pre-merge-commit;關掉就拿掉(只動有平台標記的)。
    專案有自己的 core.hooksPath(例:husky)就不裝,只偵測。"""
    import subprocess
    ws = registry.dirs(doc)["workspace"]
    if not ws or not (ws / ".git").is_dir():
        return
    want = doc.get("gate") == "strict"
    hp = subprocess.run(["git", "-C", str(ws), "config", "core.hooksPath"], capture_output=True, text=True).stdout.strip()
    for hook in ("pre-commit", "pre-merge-commit"):
        f = ws / ".git" / "hooks" / hook
        mine = f.exists() and GATE_MARK in f.read_text(errors="replace")
        if want and hp:
            print(f"{name}:gate strict 沒裝(專案用自己的 core.hooksPath={hp}),只偵測")
            return
        if want and not mine:
            if f.exists():
                print(f"{name}:.git/hooks/{hook} 已經有專案自己的 hook,不覆蓋(gate 只偵測)")
                continue
            if not report:
                f.write_text("#!/bin/sh\n" + GATE_HOOK)
                f.chmod(0o755)
                print(f"{name}:裝了 {hook}(gate strict)")
        elif not want and mine and not report:
            f.unlink()
            print(f"{name}:gate 關了,拿掉 {hook}")


def main() -> int:
    report = "--report" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    index = registry.refresh()
    bad = 0
    for name, doc in sorted(index["projects"].items()):
        if name == hubconf.hub_project() or (only and name != only):
            continue
        gate(name, doc, report)
        on = bool((doc.get("enforce") or {}).get("sandbox"))
        if not on:
            if report:
                apply(name, doc, index, report=True)
            else:
                unapply(name, doc)
            continue
        drift = apply(name, doc, index, report=report)
        if drift:
            bad += 1
            print(f"⚠️ {name} 的設定偏移(已{'(report 模式不)' if report else ''}寫回):{'; '.join(drift)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
