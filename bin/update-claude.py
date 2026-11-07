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

A platform run starts claude in a project's folder, and claude runs git of its own there unless told not to (runtime_claude.py
NO_FOLDER_GIT): what stops a sandboxed project's repo config from running as the user rests on that one variable of the tool's. So
each version this script finds gets a real probe (two small requests in a throwaway repo with a clean filter, with the variable and
without it, the same flags): the filter ran, or the probe couldn't tell, goes to the alerts and is tried again next night. Claude's own auto-update can change the
version between runs, so it goes by the version last probed, not by whether this script updated it.

Usage: update-claude.py [--dry-run]
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import pathlib
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import alert  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import runtime_ports  # noqa: E402

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
                           input="Reply with just the two letters: ok", capture_output=True, text=True, timeout=180,
                           env={**os.environ, "LLM_AUTH": "token"})
    except subprocess.TimeoutExpired:
        return _("no answer for 3 minutes")
    if r.returncode == 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1]).get("llm_error") or f"exit {r.returncode}"
    except (ValueError, IndexError):
        return (r.stderr.strip() or r.stdout.strip())[-300:] or f"exit {r.returncode}"


REQUEST = "Reply with just the two letters: ok"


def agent_run(repo: pathlib.Path, guarded: bool = True) -> str | None:
    """One minimal request in this folder. Why not, or None. Guarded: on the platform's own path (llm.py, so the environment it really
    gives). Not guarded: that same run's flags (llm.py --dry-run), with the variable taken out, to show the probe still tests something."""
    llm = [sys.executable, str(hubconf.ROOT / "bin" / "llm.py"), "run", "login-check", "--cwd", str(repo)]
    env = {**os.environ, "LLM_AUTH": "token"}
    try:
        if guarded:
            r = subprocess.run(llm, input=REQUEST, capture_output=True, text=True, timeout=180, cwd=repo, env=env)
        else:
            dry = subprocess.run([*llm, "--dry-run"], input=REQUEST, capture_output=True, text=True, timeout=60, cwd=repo, env=env)
            if dry.returncode != 0:
                return (dry.stderr or dry.stdout).strip()[-200:] or f"exit {dry.returncode}"
            rt = runtime_ports.load(ports=("auth",))
            run_env, why = rt.auth_env("token", dict(os.environ))
            if run_env is None:
                return why
            for k in getattr(rt, "NO_FOLDER_GIT", ()):
                run_env.pop(k, None)
            run_env["HUB_LLM_POINT"] = "login-check"
            r = subprocess.run(json.loads(dry.stdout), input=REQUEST, capture_output=True, text=True, timeout=180, cwd=repo, env=run_env)
    except subprocess.TimeoutExpired:
        return _("no answer for 3 minutes")
    return None if r.returncode == 0 else (r.stderr.strip() or r.stdout.strip())[-200:] or f"exit {r.returncode}"


def filter_probe(run=agent_run) -> str | None:
    """Whether a platform run still leaves a repo's git filter alone: None, or what is wrong. All of it in hubconf.private_tmp: the
    system's temp folder is every sandboxed project's to write, and this runs the filter as the user. A plain `git status` shows git
    fires the filter, and the same run without the variable shows claude still would: with either silent, a pass would say nothing."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    with tempfile.TemporaryDirectory(prefix="filter-probe-", dir=hubconf.private_tmp()) as tmp:
        repo, marker, script = pathlib.Path(tmp) / "repo", pathlib.Path(tmp) / "ran", pathlib.Path(tmp) / "filter.sh"
        repo.mkdir()
        script.write_text(f'#!/bin/sh\necho x >> "{marker}"\ncat\n')
        script.chmod(0o755)

        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env, timeout=60)

        def stat_dirty():
            marker.unlink(missing_ok=True)
            t = (repo / "a.txt").stat().st_mtime + 5
            os.utime(repo / "a.txt", (t, t))
        (repo / "a.txt").write_text("a\n")
        git("init", "-q", "-b", "main")
        git("add", "a.txt")
        git("commit", "-qm", "x")
        git("config", "filter.x.clean", str(script))
        (repo / ".git" / "info" / "attributes").write_text("a.txt filter=x\n")
        stat_dirty()
        git("--no-optional-locks", "status", "--short")
        if not marker.exists():
            return _("the probe says nothing: a plain git status didn't run the filter either")
        stat_dirty()
        if (why := run(repo, False)):
            return _("the probe could not run: {why}", why=why)
        if not marker.exists():
            return _("the probe no longer tests anything: without the variable claude didn't run the filter either, so its git "
                     "status has moved or gone; look again at what stops it (runtime_claude.py NO_FOLDER_GIT)")
        stat_dirty()
        if (why := run(repo, True)):
            return _("the probe could not run: {why}", why=why)
        if marker.exists():
            return _("claude ran a repo's git filter as you, in a platform run: a sandboxed project can run code outside its "
                     "sandbox this way, until the variable that stops it is put right (runtime_claude.py NO_FOLDER_GIT)")
    return None


PROBED = hubconf.STATE / "claude-filter-probe.json"


def probe_new_version() -> None:
    """Once per version of the claude platform runs use, whoever installed it: alerts while it isn't known to be right."""
    bin_dir = hubconf.nvm_bin()
    now = version(bin_dir / "claude") if bin_dir else None
    if not now:
        return
    try:
        if json.loads(PROBED.read_text()).get("version") == now:
            return
    except (OSError, ValueError):
        pass
    try:
        why = filter_probe()
    except Exception as e:   # what can't be told is said too: a pass must be earned
        why = _("the probe broke: {error!r}", error=e)
    if not why:
        PROBED.parent.mkdir(parents=True, exist_ok=True)
        PROBED.write_text(json.dumps({"version": now}))
    alert.once("claude-git-filter", [_("claude {version}: {why}", version=now, why=why)] if why else [],
               _("claude {version} leaves a repo's git filter alone again", version=now))


def main() -> int:
    dry = "--dry-run" in sys.argv
    ok, msg = update(dry)
    print(msg, file=sys.stdout if ok else sys.stderr)
    if not dry:
        alert.once("claude-version", [] if ok else [_("claude cannot get to the latest version: {msg}", msg=msg)],
                   _("claude is on the latest version again ({msg})", msg=msg))
        probe_new_version()
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
                         "(a login or network problem, see brunna/claude-login): {error}", old=old, latest=latest,
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
