#!/usr/bin/env python3
"""一個專案的權限政策,從專案登錄算出來,只用跟執行環境無關的詞(路徑、網域、能力名稱)。

翻譯成各執行環境的設定是別的模組的事(設計:docs/design/capabilities.md):
  runtime_claude.settings(policy) / lease_settings(policy)   Claude Code 的 settings
  seatbelt.profile(policy, cap)                               平台代跑(cap-runner)用的 macOS Seatbelt profile
這裡不准出現任何執行環境的語法或路徑(tests/test_runtime_boundary.py 檢查)。

policy(name, doc, index) → {
  workspace:          專案資料夾(Path 或 None)
  write:              平台預設之外還准寫的(自己的 runtime、匯流排目錄、工具 cache、登錄的 allow_write;字串,~ 開頭)
  other_projects:     別的專案的 workspace/runtime(讀寫都不准;Path)
  deny_write:         平台的地方(hub、bare repo、memory 副本、LaunchAgents;Path)
  deny_read:          memory 副本的原始筆記(glob)、平台 secrets(Path)
  credentials:        使用者層級的憑證目錄(Path);credential_files:憑證檔(Path)——位置在 hub.json 的 credentials
  network:            准連的網域(平台預設 + 登錄的 network)
  platform_commands:  跨界的唯一通道(信箱 CLI、合併閘門;Path)
  capabilities:       登錄 enforce.capabilities
}
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
# registry 要 PyYAML:只在算路徑政策時才 import,approvals() 只用標準函式庫(lease-relay、系統的 python3 也 import 得到)

HOME = pathlib.Path.home()
WRITE_CACHES = ["~/.cache/uv", "~/.cache/pip", "~/.npm", "~/.cache/node", "~/Library/Caches/pip",
                "~/Library/Caches/uv", "~/.local/share/uv"]
DOMAINS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
           "raw.githubusercontent.com", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]


def tilde(p: pathlib.Path | str) -> str:
    s, h = str(p), str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def project_paths(index: dict) -> dict[str, list[pathlib.Path]]:
    import registry
    out = {}
    for name, doc in index["projects"].items():
        if name == hubconf.hub_project():
            continue
        d = registry.dirs(doc)
        out[name] = [p for p in (d["workspace"], d["runtime"]) if p]
    return out


def policy(name: str, doc: dict, index: dict) -> dict:
    import registry
    d = registry.dirs(doc)
    others = [p for n, ps in project_paths(index).items() if n != name for p in ps]
    copies = hubconf.p("memory_copies")
    return {
        "workspace": d["workspace"],
        "write": ([tilde(d["runtime"])] if d["runtime"] else []) + [tilde(hubconf.BUS_DIR)] + WRITE_CACHES
                 + list(doc.get("allow_write") or []),
        "other_projects": others,
        "deny_write": [hubconf.ROOT, hubconf.p("git_origin"), copies, HOME / "Library" / "LaunchAgents"],
        "deny_read": [copies / "*" / "memory", hubconf.ROOT / ".secrets"],
        "credentials": [hubconf.path(x) for x in hubconf.get("credentials.dirs", [])],
        "credential_files": [hubconf.path(x) for x in hubconf.get("credentials.files", [])],
        "network": DOMAINS + list(doc.get("network") or []),
        "platform_commands": [hubconf.ROOT / "bin" / "hubbus", hubconf.ROOT / "bin" / "merge-branch"],
        "capabilities": list((doc.get("enforce") or {}).get("capabilities") or []),
    }


def for_project(name: str) -> dict:
    import registry
    index = registry.refresh()
    return policy(name, index["projects"][name], index)


# ---------- 哪些動作要問人(approvals;docs/design/lease-contract-neutral.md)----------
# 規則的值:
#   allow       直接放行            deny        直接拒絕(不問)
#   ask         問 owner(手機),可以「這個租約都允許」
#   ask_once    問 owner,每次都問(「這個租約都允許」只算這一次)
#   inside      專案資料夾裡放行、外面拒絕(不問;路徑解析成真實路徑再判斷)
#   listed      open_web 的網域在 web_allowed 裡放行,其他 ask;no_session_domains 不准「這個租約都允許」
# 10-25 Knas 決定的 work 模式:資料夾內寫檔不問、指令每次都問。
APPROVALS = {
    "discuss": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
                "write_file": "deny", "run_command": "deny", "other": "deny"},
    "work": {"read": "allow", "search": "allow", "ask_user": "ask", "open_web": "listed",
             "write_file": "inside", "run_command": "ask_once", "other": "deny"},
}


def approvals(name: str, mode: str, doc: dict | None = None) -> dict:
    """{mode, rules: {kind: 規則}, web_allowed: [網域], no_session_domains: [網域]}——各 runtime 接到自己的權限機制。"""
    doc = doc if doc is not None else (hubconf.projects().get(name) or {})
    return {"mode": mode, "rules": dict(APPROVALS["work" if mode == "work" else "discuss"]),
            "web_allowed": list(doc.get("network") or []),
            "no_session_domains": list(hubconf.get("lease.no_session_domains", []))}
