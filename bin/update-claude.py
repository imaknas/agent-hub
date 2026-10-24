#!/usr/bin/env python3
"""每晚把 nvm 預設版本底下的 claude(npm 全域的 @anthropic-ai/claude-code)更新到最新版。

排程、通道租借、Muninn 用的都是這一份(hubconf.tool("claude"));Claude Desktop 的 SSH session 用它自己推過來的
~/.claude/remote/ccd-cli/<版本>,會自己升級。這一份以前只有人在終端機裝/升級時才會動,版本一直落後
(2026-10-24 Knas:不要每次手動更新)。

  1. 先用現在的版本實際打一次請求(llm.py 的 login-check,用排程用的 headless token)——已經失敗就不更新
     (那是登入或網路的問題,不是新版的問題;claude-login 會報),exit 1
  2. npm install -g @anthropic-ai/claude-code@latest(用 nvm 預設版本的 npm)
  3. 新版 `claude --version` 跑不起來、或同一個請求失敗(例:-p 的旗標改了)→ 裝回舊版本,exit 1
     (排程器記 job.failed,狀態頁紅)
  沒有新版就什麼都不做(不打請求)。

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


def smoke() -> str | None:
    """實際打一次最小的請求(跟排程同一條路:llm.py、headless token)。None = 成功,否則是原因。"""
    try:
        r = subprocess.run(["/opt/homebrew/bin/python3", str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check"],
                           input="只回覆 ok 兩個字母。", capture_output=True, text=True, timeout=180,
                           env={**os.environ, "LLM_AUTH": "token"})
    except subprocess.TimeoutExpired:
        return "超過 3 分鐘沒回應"
    if r.returncode == 0:
        return None
    return (r.stdout.strip() or r.stderr.strip())[-300:] or f"exit {r.returncode}"


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
    if old and (err := smoke()):
        print(f"update-claude:現在的 {old} 就打不通請求,先不更新(登入或網路的問題,看 agent-hub/claude-login):{err}",
              file=sys.stderr)
        return 1
    r = run(str(npm), "install", "-g", f"{PKG}@{latest}", "--no-audit", "--no-fund")
    new = version(claude) if r.returncode == 0 else None
    err = smoke() if new == latest else None
    if new == latest and err is None:
        print(f"claude {old or '沒有安裝'} → {new}(node {bin_dir.parent.name}),請求測試通過")
        return 0
    detail = err or (r.stderr or r.stdout).strip()[-300:]
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
