#!/usr/bin/env python3
"""每晚把 nvm 預設版本底下的 claude(npm 全域的 @anthropic-ai/claude-code)更新到最新版。

排程、通道租借、Muninn 用的都是這一份(hubconf.tool("claude"));Claude Desktop 的 SSH session 用它自己推過來的
~/.claude/remote/ccd-cli/<版本>,會自己升級。這一份以前只有人在終端機裝/升級時才會動,版本一直落後
(2026-10-24 Knas:不要每次手動更新)。

  1. 記下現在的版本 → npm install -g @anthropic-ai/claude-code@latest(用 nvm 預設版本的 npm)
  2. 新版跑不起來(`claude --version` 失敗)就裝回舊版本,exit 1(排程器記 job.failed,狀態頁紅)
  沒有新版就什麼都不做。

用法:update-claude.py [--dry-run]
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

PKG = "@anthropic-ai/claude-code"


def run(*args: str, timeout: int = 600) -> subprocess.CompletedProcess:
    # npm 是 `#!/usr/bin/env node` 的腳本:PATH 要找得到 nvm 的 node
    env = {**os.environ, "PATH": hubconf.LAUNCHD_PATH}
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)


def version(claude: pathlib.Path) -> str | None:
    try:
        r = run(str(claude), "--version", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.match(r"\s*(\d+\.\d+\.\d+)", r.stdout)
    return m.group(1) if r.returncode == 0 and m else None


def main() -> int:
    dry = "--dry-run" in sys.argv
    bin_dir = hubconf.nvm_bin()
    if not bin_dir:
        print("update-claude:找不到 nvm 的預設版本(~/.nvm/alias/default)", file=sys.stderr)
        return 1
    npm, claude = bin_dir / "npm", bin_dir / "claude"
    old = version(claude) if claude.exists() else None
    r = run(str(npm), "view", PKG, "version", timeout=120)
    latest = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"\d+\.\d+\.\d+", latest):
        print(f"update-claude:查不到最新版本:{(r.stderr or r.stdout).strip()[-200:]}", file=sys.stderr)
        return 1
    if old == latest:
        print(f"claude {old}(node {bin_dir.parent.name}):已經是最新版")
        return 0
    if dry:
        print(f"(dry-run) claude {old or '沒有安裝'} → {latest}(node {bin_dir.parent.name})")
        return 0
    r = run(str(npm), "install", "-g", f"{PKG}@{latest}", "--no-audit", "--no-fund")
    new = version(claude) if r.returncode == 0 else None
    if new == latest:
        print(f"claude {old or '沒有安裝'} → {new}(node {bin_dir.parent.name})")
        return 0
    detail = (r.stderr or r.stdout).strip()[-300:]
    if old:
        back = run(str(npm), "install", "-g", f"{PKG}@{old}", "--no-audit", "--no-fund")
        print(f"update-claude:{latest} 裝不起來({detail}),已裝回 {old}"
              + ("" if back.returncode == 0 and version(claude) == old else "——裝回也失敗,claude 現在可能不能用"),
              file=sys.stderr)
    else:
        print(f"update-claude:{latest} 裝不起來:{detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
