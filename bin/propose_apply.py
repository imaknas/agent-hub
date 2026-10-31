#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Agent proposes, platform applies (docs/design/tool-agnostic.md "Core"): agent work that the platform starts and that
changes files runs in a throwaway worktree.

Every agent (Claude Code, Gemini CLI / Codex / opencode through `runtime: command`…) is treated the same: nothing it
does in the worktree reaches the real checkout; when it is done the platform looks at which files it changed, copies
**only the allowed paths** back into the real folder, records the rest as "refused" and throws them away with the
worktree.
Letters to send and things to close go into the agent's structured output, and the platform sends them (the sender is
the one the platform gives, not the one the agent claims). hubbus subcommands that change the bus (send, done, ack…) are
refused when the agent runs them itself (hubbus.platform_mails: the environment variable set here, or this script being
an ancestor process). The guarantee does not rely on the tool's permission settings and holds on systems without a
sandbox (Linux) too.

  propose_apply.py mailbox <project> <letter id>…  a mailbox assignment (called by hubbus dispatch when hub.json has
                                                   mailbox.handler: propose)

  run(project, ws, point, prompt, allow)        for other code: returns {ok, output, applied, refused, error}

worktree: the project's HEAD (detached) + the HEAD of its .agent-memory; the agent works in the project's copy.
"""
from __future__ import annotations

import datetime
import fnmatch
import importlib.machinery
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import project_git  # noqa: E402

_ = i18n.domain(__file__)
PY = sys.executable


def add_worktree(project: str, repo: pathlib.Path, path: pathlib.Path) -> str:
    """A detached worktree of repo's HEAD at path, made as the project (its .git runs hooks and filters: project_git.py);
    returns its HEAD."""
    r = project_git.job(project, repo, "worktree-add", repo, path, timeout=project_git.BUNDLE_TIMEOUT_S)
    if r.returncode != 0:
        raise RuntimeError(f"git worktree add: {r.stderr.strip()[-200:]}")
    return r.stdout.decode(errors="replace").strip()


def changed(project: str, repo: pathlib.Path, base: str) -> list[tuple[str, bool]]:
    """(path relative to the repo, whether it still exists) -- files changed, added (untracked included) or deleted
    since commit `base`, committed or not: a hook of the agent's own runtime may commit inside the worktree (a user-level
    session-end hook syncing memory notes did, 10-31), and what it committed is still the agent's change. Listed by the
    project's job (the worktree shares the project's .git config)."""
    try:
        rows = project_git.data(project, repo, "changed", repo, base)
    except project_git.Unavailable as e:
        raise RuntimeError(str(e)) from None
    return [(p, e is True) for p, e in (x for x in rows if isinstance(x, list) and len(x) == 2) if isinstance(p, str)] \
        if isinstance(rows, list) else []


def run(project: str, ws: pathlib.Path, point: str, prompt: str, allow: list[str], timeout_s: int = 1800) -> dict:
    """allow: globs relative to ws (e.g. `.agent-memory/memory/**`). Every git command in the project's repo, and the
    copy back into its folder, run as the project (project_git.py); a symlink the agent leaves is refused, never
    followed."""
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"hub-propose-{project}-"))
    wt, mem = tmp / "ws", ws / ".agent-memory"
    added, bases = [], {}
    try:
        bases[wt] = add_worktree(project, ws, wt)   # what the agent started from
        added.append((ws, wt))
        if (mem / ".git").exists():
            bases[wt / ".agent-memory"] = add_worktree(project, mem, wt / ".agent-memory")
            added.append((mem, wt / ".agent-memory"))
        # the agent itself may not send letters or close them (hubbus honours this variable and the parent process; the
        # platform decides sender and recipients from the output)
        r = subprocess.run([PY, str(BIN / "llm.py"), "run", point, "--cwd", str(wt)], input=prompt, text=True,
                           capture_output=True, timeout=timeout_s + 60, env={**os.environ, "HUB_MAIL_BY_PLATFORM": "1"})
        try:
            res = json.loads(r.stdout) if r.stdout.strip() else {}
        except ValueError:
            res = {}
        applied, refused = [], []
        for repo, prefix in ((wt, ""), *([(wt / ".agent-memory", ".agent-memory/")] if len(added) > 1 else [])):
            take = []
            for path, _exists in changed(project, repo, bases[repo]):
                rel = prefix + path
                if prefix == "" and rel.startswith(".agent-memory/"):
                    continue
                if not any(fnmatch.fnmatch(rel, g) for g in allow):
                    refused.append(rel)
                    continue
                take.append(path)
            if take:
                try:
                    done = project_git.data(project, ws, "copy-back", repo, ws / prefix, *take)
                except project_git.Unavailable as e:
                    raise RuntimeError(str(e)) from None
                done = done if isinstance(done, dict) else {}
                applied += [prefix + p for p in project_git.strs(done.get("copied"))]
                refused += [prefix + p for p in project_git.strs(done.get("refused"))]
        ok = r.returncode == 0 and not res.get("is_error")
        return {"ok": ok, "output": res.get("structured_output"), "text": res.get("result"), "applied": applied,
                "refused": refused, "error": "" if ok else (res.get("llm_error") or r.stderr.strip()[-300:])}
    finally:
        for repo, path in reversed(added):
            project_git.job(project, repo, "worktree-remove", repo, path, "--force")
        shutil.rmtree(tmp, ignore_errors=True)


# ---------- mailbox requests ----------

def _hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_pa", str(BIN / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_pa", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def letters_text(hb, c, rows) -> str:
    """the letters of a mailbox request as the prompt lists them (the bus rows, with the letter each one answers)"""
    letters = []
    for r in rows:
        p = hb.payload(r)
        x = {"id": r["id"], "source": r["source"], "type": p.get("type"), "text": p.get("text", "")}
        if r["ref"]:
            prev = c.execute("SELECT * FROM events WHERE id=?", (r["ref"],)).fetchone()
            if prev is not None:
                x.update(reply_to=r["ref"], reply_text=hb.payload(prev).get("text", ""))
        letters.append(x)
    return letter_lines(letters)


def letter_lines(letters: list[dict]) -> str:
    """letters: {id, source, type, text[, reply_to, reply_text]}; the letter text is data and stays as it is"""
    out = []
    for x in letters:
        thread = f"\n  (reply to {x['reply_to']}: {x['reply_text'][:600]})" if x.get("reply_to") else ""
        out.append(f"- id {x['id']}, from {x['source']}, type {x['type']}:\n  {x['text']}{thread}")
    return "\n".join(out)


def mailbox_prompt(letters: str, today: str, **names) -> str:
    """judgment point mailbox-propose's prompt (templates/prompts/mailbox-propose.md, in English; the agent writes in the
    content language). names: owner= / assistant= / lang= pinned by an eval"""
    import prompts
    return prompts.render("mailbox-propose", letters=letters, today=today, **names)


def _status(project: str, state: str, **extra) -> None:
    # hubbus dispatch does not re-dispatch while it sees running
    f = hubconf.STATE / "agent-jobs" / f"mailbox-{project}" / "status.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"state": state, "ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                             "via": "propose", **extra}, ensure_ascii=False))


def mailbox(project: str, ids: list[str]) -> int:
    _status(project, "running", ids=ids)
    try:
        return _mailbox(project, ids)
    finally:
        _status(project, "done", ids=ids)


def _mailbox(project: str, ids: list[str]) -> int:
    hb = _hubbus()
    c = hb.db()
    rows = [c.execute("SELECT * FROM events WHERE id=? AND type='message'", (i,)).fetchone() for i in ids]
    rows = [r for r in rows if r is not None and r["to_project"] == project]
    ws = hubconf.workspace(project)
    if not rows or not ws:
        print(_("{project}: no letters to handle", project=project), file=sys.stderr)
        return 1
    prompt = mailbox_prompt(letters_text(hb, c, rows), datetime.date.today().isoformat())
    res = run(project, ws, "mailbox-propose", prompt, [".agent-memory/memory/**"])
    sent = []
    out = res["output"] if isinstance(res.get("output"), dict) else {}
    handled = {r["id"]: r for r in rows}
    c.execute("BEGIN IMMEDIATE")
    for rep in out.get("replies") or []:
        orig = handled.get(rep.get("reply_to"))
        if orig is None or rep.get("type") not in ("info", "done", "question") or not rep.get("text"):
            continue   # replies only to the letters of this request, to the original sender
        mid = hb.new_id()
        hb.publish(c, mid, "message", project, hb.canon(orig["source"]), orig["id"],
                   {"type": rep["type"], "text": rep["text"], "from_session": None, "via": "propose"})
        sent.append(mid)
    for q in out.get("ask_owner") or []:
        if q.get("text"):
            mid = hb.new_id()
            hb.publish(c, mid, "message", project, hb.OWNER_ADDRESS, None,
                       {"type": "question", "text": q["text"], "from_session": None, "via": "propose"})
            sent.append(mid)
    done = []
    for d in out.get("done") or []:
        if d.get("id") in handled:
            hb.publish(c, f"{d['id']}:done", "message.done", project, project, d["id"],
                       {"note": d.get("note") or _("handled by an agent the platform sent (propose)")})
            done.append(d["id"])
    c.execute("COMMIT")
    # notes committed + pushed to the memory repo (the same path as the hook)
    if res["applied"] and (ws / ".agent-memory").is_dir():
        subprocess.run([str(BIN / "with-lock.sh"), str(BIN / "git-sync-memory.sh")], cwd=ws / ".agent-memory",
                       capture_output=True)
    print(json.dumps({"project": project, "ok": res["ok"], "applied": res["applied"], "refused": res["refused"],
                      "sent": sent, "done": done, "error": res["error"]}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "mailbox":
        sys.exit(mailbox(sys.argv[2], sys.argv[3:]))
    print(_(__doc__), file=sys.stderr)
    sys.exit(2)
