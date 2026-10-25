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
import registry  # noqa: E402

HOME = pathlib.Path.home()
WRITE_CACHES = ["~/.cache/uv", "~/.cache/pip", "~/.npm", "~/.cache/node", "~/Library/Caches/pip",
                "~/Library/Caches/uv", "~/.local/share/uv"]
DOMAINS = ["github.com", "api.github.com", "codeload.github.com", "objects.githubusercontent.com",
           "raw.githubusercontent.com", "pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]


def tilde(p: pathlib.Path | str) -> str:
    s, h = str(p), str(HOME)
    return "~" + s[len(h):] if s == h or s.startswith(h + "/") else s


def project_paths(index: dict) -> dict[str, list[pathlib.Path]]:
    out = {}
    for name, doc in index["projects"].items():
        if name == hubconf.hub_project():
            continue
        d = registry.dirs(doc)
        out[name] = [p for p in (d["workspace"], d["runtime"]) if p]
    return out


def policy(name: str, doc: dict, index: dict) -> dict:
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
    index = registry.refresh()
    return policy(name, index["projects"][name], index)
