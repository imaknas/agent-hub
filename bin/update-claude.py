#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
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
Claude Code 自己的自動更新照常(Knas 2026-10-24:不用統一更新流程,但不能默默落後):這支是每晚的保險,
任何一種「沒辦法到最新版」(查不到最新版、裝不起來、裝回舊版、現在的版本就打不通)都發 Discord
(alert.once:沒解決的每 24 小時再提醒,回到最新版時發一次恢復),不只是狀態頁紅。

用法:update-claude.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

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
        r = subprocess.run([sys.executable, str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check"],
                           input="只回覆 ok 兩個字母。", capture_output=True, text=True, timeout=180,
                           env={**os.environ, "LLM_AUTH": "token"})
    except subprocess.TimeoutExpired:
        return _("no answer for 3 minutes")
    if r.returncode == 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]).get("llm_error") or f"exit {r.returncode}"
    except (ValueError, IndexError):
        return (r.stderr.strip() or r.stdout.strip())[-300:] or f"exit {r.returncode}"


def main() -> int:
    dry = "--dry-run" in sys.argv
    ok, msg = update(dry)
    print(msg, file=sys.stdout if ok else sys.stderr)
    if not dry:
        alert.once("claude-version", [] if ok else [_("claude cannot get to the latest version: {msg}", msg=msg)],
                   _("claude is on the latest version again ({msg})", msg=msg))
    return 0 if ok else 1


def update(dry: bool) -> tuple[bool, str]:
    """(有沒有在最新版, 說明)"""
    bin_dir = hubconf.nvm_bin()
    if not bin_dir:
        return False, _("no nvm default version (~/.nvm/alias/default)")
    npm, claude = bin_dir / "npm", bin_dir / "claude"
    old = version(claude) if claude.exists() else None
    r = run(str(npm), "view", PKG, "version", timeout=120)
    latest = r.stdout.strip()
    if r.returncode != 0 or not re.fullmatch(r"\d+\.\d+\.\d+", latest):
        return False, _("could not look up the latest version (now {old}): {detail}", old=old,
                         detail=(r.stderr or r.stdout).strip()[-200:])
    if old == latest:
        return True, _("claude {old} (node {node}): already the latest", old=old, node=bin_dir.parent.name)
    if dry:
        return True, _("(dry-run) claude {old} → {latest} (node {node})", old=old or _("not installed"), latest=latest,
                       node=bin_dir.parent.name)
    if old and (err := smoke()):
        return False, _("still on {old} (latest {latest}): the current version already fails a request, not updating "
                         "(a login or network problem, see agent-hub/claude-login): {error}", old=old, latest=latest,
                         error=err[-200:])
    r = run(str(npm), "install", "-g", f"{PKG}@{latest}", "--no-audit", "--no-fund")
    new = version(claude) if r.returncode == 0 else None
    err = smoke() if new == latest else None
    if new == latest and err is None:
        return True, _("claude {old} → {new} (node {node}), the request test passed", old=old or _("not installed"),
                       new=new, node=bin_dir.parent.name)
    detail = (err or (r.stderr or r.stdout).strip())[-200:]
    if old:
        back = run(str(npm), "install", "-g", f"{PKG}@{old}", "--no-audit", "--no-fund")
        restored = back.returncode == 0 and version(claude) == old
        return False, (_("{latest} would not install ({detail}); ", latest=latest, detail=detail) +
                       (_("{old} is back in place", old=old) if restored
                        else _("putting the old version back failed too, so claude may not work now")))
    return False, _("{latest} would not install: {detail}", latest=latest, detail=detail)


if __name__ == "__main__":
    sys.exit(main())
