#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Every night, update the claude under nvm's default version (npm global @anthropic-ai/claude-code) to the latest.

The scheduler, channel leases and the assistant all use this copy (hubconf.tool("claude")); Claude Desktop's SSH
sessions use the ~/.claude/remote/ccd-cli/<version> it pushes over itself, which upgrades itself. This copy used to
change only when someone installed/upgraded it in a terminal, so it kept falling behind (the owner, 2026-10-24: no
manual updating every time).

  1. First make a real request with the current version (llm.py's login-check, with the scheduler's headless
     token) -- if that already fails, do not update (it is a login or network problem, not the new version's;
     claude-login reports it), exit 1
  2. npm install -g @anthropic-ai/claude-code@latest (with the nvm default version's npm)
  3. The new version's `claude --version` does not run, or the same request fails (e.g. a -p flag changed) ->
     reinstall the old version, exit 1 (the scheduler records job.failed, the status page turns red)
  No new version: nothing is done (no request made).
Claude Code's own auto-update carries on as usual (the owner, 2026-10-24: no unified update process needed, but it
must not fall behind silently): this script is the nightly safety net, and any kind of "cannot get to the latest
version" (latest version cannot be looked up, install fails, reinstalled the old version, the current version
cannot get through) goes to Discord (alert.once: unresolved ones are repeated every 24 hours, one recovery message
when back on the latest), not just a red status page.

Usage: update-claude.py [--dry-run]
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
    # npm is a `#!/usr/bin/env node` script: PATH must find nvm's node
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
    """Make one real minimal request (the same path as the scheduler: llm.py, headless token). None = success,
    otherwise the reason."""
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
    """(whether it is on the latest version, explanation)"""
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
