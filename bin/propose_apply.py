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

  propose_apply.py mailbox <project> <letter id>…  a mailbox assignment (called by hubbus dispatch when {settings} has
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

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402
import isolation  # noqa: E402
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


def checkout_sigs(project: str, repo: pathlib.Path, base: str) -> dict:
    """What the checkout wrote where it is not the blob byte for byte (the project's own filters or line endings),
    taken before the agent starts: changed() compares those files with this instead of the blob."""
    try:
        sigs = project_git.data(project, repo, "checkout-sigs", repo, base, timeout=project_git.BUNDLE_TIMEOUT_S)
    except project_git.Unavailable as e:
        raise RuntimeError(str(e)) from None
    if not isinstance(sigs, dict):
        raise RuntimeError(f"{project}: checkout-sigs is not a JSON object")
    return sigs


def changed(project: str, repo: pathlib.Path, base: str, sigs: dict) -> list[tuple[str, bool]]:
    """(path relative to the repo, whether it still exists) -- files changed, added (untracked included) or deleted
    since commit `base`, committed or not: a hook of the agent's own runtime may commit inside the worktree (a user-level
    session-end hook syncing memory notes did, 10-31), and what it committed is still the agent's change. Decided by
    the files' bytes against the base tree (and `sigs`), never through attributes or filters, which the agent and the
    machine can set (project-git-job.py); listed by the project's job."""
    try:
        rows = project_git.data(project, repo, "changed", repo, base, input=json.dumps(sigs),
                                timeout=project_git.BUNDLE_TIMEOUT_S)
    except project_git.Unavailable as e:
        raise RuntimeError(str(e)) from None
    return [(p, e is True) for p, e in (x for x in rows if isinstance(x, list) and len(x) == 2) if isinstance(p, str)] \
        if isinstance(rows, list) else []


def unsupported(project: str) -> str | None:
    """Why this project can't have a propose run, or None. The agent runs as the user in the copy, and a sandboxed project's own job
    has to make that copy, so it is in a folder the project can write: it can then swap it for a link wherever that folder is, and the
    agent follows. Off, and said, until the agent process itself runs under the sandbox (AGENTS.md, "Tool neutrality"). A project the
    registry's index doesn't have can't be told apart from one, so that is off too."""
    doc = hubconf.projects().get(project)
    if doc is None:
        return _("{project} is not in the registry's index, so whether it is sandboxed can't be told: no propose run for it",
                 project=project)
    if isolation.sandboxed({**doc, "name": project}):
        return _("mailbox.handler: propose is off for {project}: it is sandboxed, and an agent running as you in a folder it can write "
                 "can be sent elsewhere by it. Its letters stay open for its sessions", project=project)
    return None


def run(project: str, ws: pathlib.Path, point: str, prompt: str, allow: list[str], timeout_s: int = 1800) -> dict:
    """allow: globs relative to ws (e.g. `.agent-memory/memory/**`). Every git command in the project's repo, and the
    copy back into its folder, run as the project (project_git.py); a symlink the agent leaves is refused, never
    followed, and so is a path with a dot component (.gitignore, .gitattributes: the platform's own memory sync would
    follow what they say).
    All or nothing: a run that failed (or that the runtime cancelled or left incomplete, or that ended without the
    structured answer the platform acts on) gets nothing copied back and hands back no output (no replies, no
    closes); the mailbox caller then holds the letters and asks the owner (hold_and_ask)."""
    def failed(why: str) -> dict:
        return {"ok": False, "output": None, "text": None, "applied": [], "refused": [], "error": why}
    if (why := unsupported(project)):
        return failed(why)
    try:
        wt = hubconf.throwaway_copy(project)   # the agent runs in it as the user: nothing a sandbox can write
    except OSError as e:
        return failed(_("could not make the throwaway copy: {e}", e=e))
    tmp, mem = wt.parent, ws / ".agent-memory"
    added, bases, sigs = [], {}, {}
    try:
        try:
            bases[wt] = add_worktree(project, ws, wt)   # what the agent started from
            added.append((ws, wt))
            if (mem / ".git").exists():
                bases[wt / ".agent-memory"] = add_worktree(project, mem, wt / ".agent-memory")
                added.append((mem, wt / ".agent-memory"))
            for repo, base in bases.items():   # before the agent touches anything
                sigs[repo] = checkout_sigs(project, repo, base)
        except RuntimeError as e:
            return failed(_("could not make the throwaway copy: {e}", e=e))
        # the agent itself may not send letters or close them (hubbus honours this variable and the parent process; the
        # platform decides sender and recipients from the output). HUB_THROWAWAY_COPY tells a runtime that runs only in
        # the platform's throwaway copy (runtime_acp) that this working directory is one
        try:
            r = subprocess.run([PY, str(BIN / "llm.py"), "run", point, "--cwd", str(wt)], input=prompt, text=True,
                               capture_output=True, timeout=timeout_s + 60,
                               env={**os.environ, "HUB_MAIL_BY_PLATFORM": "1", "HUB_THROWAWAY_COPY": str(wt)})
        except subprocess.TimeoutExpired:
            return failed(_("the agent's run took longer than {secs} s", secs=timeout_s + 60))
        try:
            res = json.loads(r.stdout) if r.stdout.strip() else {}
        except ValueError:
            res = {}
        ok = r.returncode == 0 and not res.get("is_error")
        error = "" if ok else (res.get("llm_error") or (_("the agent stopped before finishing ({why})", why=res["subtype"])
                                                        if res.get("is_error") and res.get("subtype") else "")
                               or r.stderr.strip()[-300:] or _("the agent's run did not finish"))
        if ok and not isinstance(res.get("structured_output"), dict):   # the platform acts only on the structured answer
            ok, error = False, _("the agent gave no structured answer: {text}", text=str(res.get("result") or "")[:200])
        applied, refused = [], []
        # a project whose notes are a plain folder (the hub's, memory: false): no checkout of them in the copy, and git
        # lists nothing there, so what the agent wrote there is looked for directly
        plain = wt / ".agent-memory" / "memory" if len(added) == 1 and mem.is_dir() and not mem.is_symlink() else None
        for repo, prefix in ((wt, ""), *([(wt / ".agent-memory", ".agent-memory/")] if len(added) > 1 else [])):
            take = []
            try:
                listed = [p for p, _exists in changed(project, repo, bases[repo], sigs[repo])]
            except RuntimeError as e:
                return failed(_("could not read the agent's changes: {e}", e=e))
            for path in listed:
                rel = prefix + path
                if prefix == "" and rel.startswith(".agent-memory/"):
                    continue
                hidden = any(part.startswith(".") for part in rel.removeprefix(".agent-memory/").split("/"))
                if not ok or hidden or not any(fnmatch.fnmatch(rel, g) for g in allow):
                    refused.append(rel)
                    continue
                take.append(path)
            if plain is not None and plain.is_dir() and not plain.is_symlink():
                for f in sorted(plain.rglob("*")):
                    rel = str(f.relative_to(wt))
                    hidden = any(part.startswith(".") for part in rel.removeprefix(".agent-memory/").split("/"))
                    if f.is_dir() and not f.is_symlink():
                        continue
                    if not ok or hidden or not any(fnmatch.fnmatch(rel, g) for g in allow):
                        refused.append(rel)
                    else:
                        take.append(rel)
            if take:
                try:
                    done = project_git.data(project, ws, "copy-back", repo, ws / prefix, *take)
                except project_git.Unavailable as e:   # say what is already in place
                    return {**failed(_("could not copy back: {e}", e=e)), "applied": applied, "refused": refused}
                done = done if isinstance(done, dict) else {}
                applied += [prefix + p for p in project_git.strs(done.get("copied"))]
                refused += [prefix + p for p in project_git.strs(done.get("refused"))]
        return {"ok": ok, "output": res.get("structured_output") if ok else None, "text": res.get("result"),
                "applied": applied, "refused": refused, "error": error}
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
    if (why := unsupported(project)):   # no run, so nothing to hold and nothing to ask anyone about
        print(why, file=sys.stderr)
        return 1
    _status(project, "running", ids=ids)
    try:
        return _mailbox(project, ids)
    finally:
        _status(project, "done", ids=ids)


def hold_and_ask(hb, c, project: str, rows, why: str, applied=()) -> None:
    """The run did not complete (the runtime failed, it stopped incomplete or without a structured answer, its changes
    conflict with the workspace, something broke while applying, or it left a letter unanswered): record job.failed, hold
    the letters (no platform run starts for them again, so nothing loops; they stay open for sessions) and ask the owner
    about each, saying why and what, if anything, was already applied. The question closes with its letter
    (hubbus.close_failed_questions). Never silent."""
    hb.publish(c, hb.new_id(), "job.failed", project, None, None,
               {"job": f"mailbox-propose:{project}", "detail": why[:500]})
    done_part = _(" Already applied: {paths}.", paths=", ".join(applied)) if applied else ""
    for r in rows:
        hb.publish(c, f"{r['id']}:delivered:held", "message.delivered", project, project, r["id"],
                   {"session": None, "via": "held"})
        hb.publish(c, f"{r['id']}:propose-failed", "message", hubconf.hub_project(), hb.OWNER_ADDRESS, r["id"],
                   {"type": "question", "from_session": None, "via": "propose", "text": _(
                       "The agent the platform sent could not handle letter {id} for {project}: {why}.{done} Open a "
                       "session in {project} to handle it.", id=r["id"], project=project, why=why[:300], done=done_part)})


def _mailbox(project: str, ids: list[str]) -> int:
    hb = _hubbus()
    c = hb.db()
    rows = [c.execute("SELECT * FROM events WHERE id=? AND type='message'", (i,)).fetchone() for i in ids]
    rows = [r for r in rows if r is not None and r["to_project"] == project]
    ws = hubconf.workspace(project)
    if not rows:
        print(_("{project}: no letters to handle", project=project), file=sys.stderr)
        return 1
    try:
        if not ws:
            raise RuntimeError(_("{project} has no workspace in the registry", project=project))
        prompt = mailbox_prompt(letters_text(hb, c, rows), datetime.date.today().isoformat())
        res = run(project, ws, "mailbox-propose", prompt, [".agent-memory/memory/**"])
    except Exception as e:   # the letters were triaged for this run: never left without a word
        c.execute("BEGIN IMMEDIATE")
        hold_and_ask(hb, c, project, rows, _("the propose run broke: {e!r}", e=e))
        c.execute("COMMIT")
        raise
    sent, done = [], []
    out = res["output"] if isinstance(res.get("output"), dict) else {}
    live = hb.Live(c=c)
    live.load()   # the process table, before the write lock
    try:
        c.execute("BEGIN IMMEDIATE")
        # the same rules as a handler's commands (hubbus.run_refusal), judged now: the run can take half an hour, and what
        # a session has taken meanwhile is the session's. What the agent wrote for such a letter is dropped
        handled = {r["id"]: r for r in rows if not hb.taken_by_another(c, r, live)}
        for rep in out.get("replies") or []:
            orig = handled.get(rep.get("reply_to"))
            if orig is None or not rep.get("text"):
                continue   # replies only to the letters of this request, to the original sender
            if not (rep.get("type") == "done" or (rep.get("type") == "info" and hb.payload(orig).get("effect") == "asks")):
                continue   # a question goes to the owner (ask_owner); info only to a letter that tells it to
            mid, to = hb.new_id(), hb.canon(orig["source"])
            hb.publish(c, mid, "message", project, to, orig["id"],
                       {"type": rep["type"], "text": rep["text"], "from_session": None, "via": "propose",
                        **hb.reply_fields(orig, to)})
            sent.append(mid)
        for q in out.get("ask_owner") or []:
            if q.get("text") and handled:   # with every letter taken there is nothing left to ask about
                mid = hb.new_id()
                hb.publish(c, mid, "message", project, hb.OWNER_ADDRESS, None,
                           {"type": "question", "text": q["text"], "from_session": None, "via": "propose"})
                sent.append(mid)
        for d in out.get("done") or []:
            if d.get("id") in handled:
                hb.publish(c, f"{d['id']}:done", "message.done", project, project, d["id"],
                           {"note": d.get("note") or _("handled by an agent the platform sent (propose)")})
                done.append(d["id"])
        free = [r for r in rows if r["id"] in handled]
        if not res["ok"]:
            hold_and_ask(hb, c, project, free, res["error"], res["applied"])
        else:
            replied = {ref for ref in (c.execute("SELECT ref FROM events WHERE id=?", (m,)).fetchone()[0] for m in sent)
                       if ref}
            # deferred on purpose (waits for a code change or for the owner): stays open for sessions; only a letter the
            # agent did not account for at all is held and asked about
            deferred = {d.get("id") for d in out.get("deferred") or [] if isinstance(d, dict)}
            unanswered = [r for r in free if r["id"] not in done and r["id"] not in replied and r["id"] not in deferred]
            if unanswered:
                hold_and_ask(hb, c, project, unanswered, _("the agent neither answered, closed nor deferred it"),
                             res["applied"])
        c.execute("COMMIT")
    except Exception as e:   # nothing of the above stands; the letters were triaged for this run: hold them, say why
        if c.in_transaction:
            c.execute("ROLLBACK")
        c.execute("BEGIN IMMEDIATE")
        hold_and_ask(hb, c, project, rows, _("recording the propose run's result broke: {e!r}", e=e), res["applied"])
        c.execute("COMMIT")
        raise
    # notes committed + pushed to the memory repo (the same path as the hook)
    if res["applied"] and (ws / ".agent-memory").is_dir():
        project_git.sync_memory_of(project, ws)
    deferred = [{"id": d.get("id"), "why": str(d.get("why") or "")[:300]} for d in out.get("deferred") or []
                if isinstance(d, dict) and d.get("id") in handled]   # why a letter waits, in the run's log
    print(json.dumps({"project": project, "ok": res["ok"], "applied": res["applied"], "refused": res["refused"],
                      "sent": sent, "done": done, "deferred": deferred, "taken": [r["id"] for r in rows if r["id"] not in handled],
                      "error": res["error"]}, ensure_ascii=False))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "mailbox":
        sys.exit(mailbox(sys.argv[2], sys.argv[3:]))
    print(_(__doc__), file=sys.stderr)
    sys.exit(2)
