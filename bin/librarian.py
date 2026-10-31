#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The librarian: every night it reads what is new in each project's memory/, updates STATE.md where needed, writes the
project-summary section of today's digest, and asks the model to mark the relations between projects (index.md).
Platform job agent-hub/librarian (runs after consolidate; the mechanical steps after it -- the assistant's task
registry, reconcile-questions, todo, memory-gaps, state-anchors, librarian-publish -- are jobs of their own, not in this script).

10-27: rewritten from librarian.sh in Python (same behaviour; prompt, commit, review-log, digest and checkpoint were
compared byte for byte in an isolated environment with a stand-in llm.py): paths and names all go through hubconf
instead of each being worked out in shell; the LLM's JSON output is read directly, no jq.

Robustness decisions (carried over):
1. The per-project STATE.md judgment uses haiku (high volume, simple task); cross-project relations use sonnet. Model,
   permissions, tools and output schema are declared in contracts/llm-points.yaml (judgment points librarian-state,
   librarian-todo-format, librarian-index); hub.json llm.points can override them.
2. Whether to commit is decided by the actual git state, not the state_updated the model reports; when the two
   disagree a warning is logged.
3. Every real change to STATE.md writes an entry (diff included) to review-log.md for a person to spot-check later;
   no blocking approval.
4. Every push goes through git-safe-push.sh (on divergence it pulls and retries; only a real content conflict fails
   and is left for a person).
5. The model may touch only ./STATE.md (enforced by permission rules); if it touches other files they are moved to
   quarantine and the checkpoint does not advance (second line of defence).

Usage: librarian.py (log written to .logs/librarian-<date>.log; also printed to the screen when run in a terminal)
"""
from __future__ import annotations

import datetime
import difflib
import glob
import json
import pathlib
import re
import shutil
import subprocess
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import edits  # noqa: E402
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import prompts  # noqa: E402
import state_anchors  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
HUB = hubconf.ROOT
STATE_DIR, LOG_DIR = hubconf.STATE, hubconf.LOGS
REVIEW_LOG = hubconf.data_file("review-log.md")
SAFE_PUSH = HUB / "bin" / "git-safe-push.sh"
PY = str(HUB / "bin" / "py")
MAX_DIFF_CHARS = 60000
# Handoffs carrying a task key are not given to the model; the assistant module's task-registry job later applies
# them by key
KEYED_EFFECTS = ("done", "dropped", "already_done", "reopened")

TODAY = datetime.date.today().isoformat()
LOG = LOG_DIR / f"librarian-{TODAY}.log"
OWNER = hubconf.get("owner.tag") or "owner"            # how the prompt names the owner and the assistant
ASSISTANT = hubconf.assistant_name()
CONTENT_LANG = hubconf.lang_content()                 # the language the model writes STATE.md and index.md in
# Shadow mode (docs/design/llm-providers.md step 4): the completion version (librarian-state-edits,
# librarian-index-edits) runs every night alongside today's agent version; only the comparison of its proposal with
# the actual result is recorded in .state/librarian-shadow/, no real file is written. Switch over once the comparison
# is good enough, then remove this.
SHADOW = True
SHADOW_DIR = STATE_DIR / "librarian-shadow"
# registrar's key marker in either token set (without the end-of-line anchor)
KEY_MARK = todo_tokens.patterns(OWNER).key_mark
total_cost = 0.0
cost_added = False   # the shell version printed $0 when there was no cost at all, %.6f only when there was


def used_model(obj) -> str:
    """The model this call actually used: the llm_model llm.py reports (the model is computed only in llm.model_for; no
    guessing here)."""
    m = (obj or {}).get("llm_model")
    return m if isinstance(m, str) and m else _("tool default")


def log(msg: str) -> None:
    """Write to LOG only; print as well only when run by hand in a terminal. The scheduler sends stdout into the same
    log, so always printing would write every line twice."""
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    if sys.stdout.isatty():
        print(line)


def run(cmd: list, cwd=None, stdin: str | None = None, to_log: bool = False) -> subprocess.CompletedProcess:
    """Run an external command; with to_log=True both stdout and stderr are appended to LOG (like the shell version's
    >>"$LOG" 2>&1)."""
    if to_log:
        with LOG.open("a") as f:
            return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, stdout=f, stderr=f)
    return subprocess.run(cmd, cwd=cwd, input=stdin, text=True, capture_output=True)


def out(cmd: list, cwd=None) -> str:
    """The command's stdout without the trailing newline (like shell's $(…))."""
    return run(cmd, cwd=cwd).stdout.rstrip("\n")


def git(repo, *args) -> str:
    return out(["git", "-C", str(repo), *args])


def llm(point: str, prompt: str, cwd) -> tuple[int, dict | None, str]:
    """Run a judgment point (bin/llm.py run <point>, prompt on stdin); stderr is appended to LOG. Returns
    (exit, parsed JSON, raw text)."""
    with LOG.open("a") as f:
        r = subprocess.run([PY, str(HUB / "bin" / "llm.py"), "run", point], cwd=cwd, input=prompt, text=True,
                           stdout=subprocess.PIPE, stderr=f)
    text = r.stdout.rstrip("\n")
    try:
        obj = json.loads(text) if text else None
    except ValueError:
        obj = None
    return r.returncode, obj if isinstance(obj, dict) else None, text


def add_cost(obj: dict | None) -> str:
    """Add up the cost; returns the string for the log (empty when there is no cost)."""
    global total_cost, cost_added
    c = (obj or {}).get("total_cost_usd")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and c >= 0:
        total_cost += c
        cost_added = True
        return str(c)
    return ""


def jstr(v) -> str:
    """jq's \\(…) string interpolation: null -> "null", strings as they are, anything else as JSON."""
    if v is None:
        return "null"
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def present(v) -> bool:
    """jq truthiness: only null and false are false (an empty string or 0 counts as present)."""
    return v is not None and v is not False


def jq_bool(v) -> str:
    """How jq -r prints a value: true/false/null, strings as they are."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return jstr(v)


def head_spaced(text: str, n: int) -> str:
    """`echo "$x" | head -n | tr '\\n' ' '`: the first n lines, each followed by a space."""
    return "".join(line + " " for line in text.split("\n")[:n])


# ---------- handoffs ----------

def load_handoffs() -> list[dict]:
    """The assistant's handoffs (facts about a project that the owner told the personal assistant): hubbus-bridge puts
    them into each project's mailbox every 15 minutes; every night the ones not yet closed are used directly as extra
    input to this STATE.md call (not written as running notes; what changed and why stays in review-log.md). Only when
    this project's result is trustworthy (the checkpoint advances) does it hubbus done them, and the bridge reports the
    closure to the assistant; otherwise they wait for the next night."""
    r = run([PY, str(HUB / "bin" / "hubbus"), "open-handoffs"])
    if r.stderr:
        with LOG.open("a") as f:
            f.write(r.stderr)
    try:
        items = json.loads(r.stdout) if r.returncode == 0 else None
    except ValueError:
        items = None
    if not isinstance(items, list):
        log(_("hubbus open-handoffs could not be read; handoffs are not processed this time"))
        return []
    log(_("Assistant handoffs: {n} to process", n=len(items)))
    for p in sorted({str(h.get("project")) for h in items if isinstance(h, dict)}):
        if not (hubconf.p("memory_copies", "~/.memory-copies") / p / ".git").is_dir():
            log(_("Assistant handoffs: project {project} has no memory copy, not processed this time (left in the mailbox)",
                  project=p))
    return [h for h in items if isinstance(h, dict)]


def load_records(proj: str) -> list[dict]:
    """v2 (Principal Handoff 0.3): records sent by the principal side and not yet done become the same kind of input as
    handoffs -- note, Decisions' settled (an answer or "no answer needed"), same_as. Acts' settled/reopened are applied
    mechanically by registrar; not touched here."""
    r = run([PY, str(HUB / "bin" / "hubbus"), "records", "--project", proj])
    try:
        events = json.loads(r.stdout) if r.returncode == 0 else []
    except ValueError:
        events = []
    asked = {}
    for e in events:   # oldest to newest: a key's last terms are what is being asked now
        rec = e.get("record") or {}
        if e.get("direction") == "out" and rec.get("type") in ("raised", "amended"):
            asked[rec.get("key")] = (rec.get("terms") or {}).get("text")
    out = []
    for e in events:
        rec = e.get("record") or {}
        if e.get("direction") != "in" or e.get("done"):
            continue
        kind, t = str(rec.get("ext", "")).split("/")[0], rec.get("type")
        if t == "note":
            effect, fact = "note", rec.get("note")
        elif t == "settled" and kind == "decisions":
            decided = rec.get("outcome") == "decided"
            effect, fact = ("answers", (rec.get("answer") or {}).get("text")) if decided else ("dismissed", rec.get("note"))
        elif t == "related" and (rec.get("relation") or {}).get("kind") == "same_as":
            tg = (rec.get("relation") or {}).get("target") or {}
            effect, fact = "same_as", f"the same item as {tg.get('key') or ''} in {tg.get('project') or tg.get('uri')}"
        else:
            continue
        about = rec.get("about") or {}
        out.append({"id": e.get("id"), "handoff_id": rec.get("id"), "project": proj, "effect": effect, "fact": fact,
                    "quote": None,
                    "item": about.get("quote") or asked.get(about.get("key") or rec.get("key")) or e.get("question"),
                    "why": authority_text(rec) or "(no explanation)", "ts": rec.get("ts"), "v2": True})
    return out


def authority_text(rec: dict) -> str | None:
    """Who decided a closure and with what kind of authority (spec core §5), in plain words for the model."""
    a = rec.get("authority") or {}
    basis = (rec.get("evidence") or {}).get("basis")
    return {"principal": f"{OWNER} decided it personally", "confirmed": f"{OWNER} confirmed {ASSISTANT}'s proposal",
            "attested": f"{ASSISTANT} concluded it from evidence ({basis})" if basis else f"{ASSISTANT} concluded it from evidence",
            "delegated": f"{ASSISTANT} decided it under authority {OWNER} gave"}.get(a.get("kind"), rec.get("context"))


def handoff_text(h: dict) -> str:
    s = f"- [{jstr(h.get('effect'))}] Fact: {jstr(h.get('fact'))}"
    if present(h.get("quote")):
        s += f"\n  {OWNER}'s own words: {jstr(h.get('quote'))}"
    item = h.get("item") if present(h.get("item")) else "(none given)"
    ts = h.get("ts")
    return s + (f"\n  Matching to-do: {jstr(item)}\n  The assistant's reasoning: {jstr(h.get('why'))}"
                f"\n  ({jstr(ts[0:16] if isinstance(ts, str) else ts)})")


# ---------- prompt (templates/prompts/librarian-*.md) ----------

def render(name: str, **values) -> str:
    """A librarian prompt in English, the model writing in the content language; the names and date are this module's
    (an eval pins them)."""
    return prompts.render(name, lang=CONTENT_LANG, owner=OWNER, assistant=ASSISTANT, today=TODAY, **values)


def state_prompt(proj: str, memory_section: str, handoff_note: str, dangling_note: str) -> str:
    return render("librarian-state", project=proj, memory_section=memory_section, handoff_note=handoff_note,
                  dangling_note=dangling_note, rules=_state_rules())


def handoff_note(items: list[dict]) -> str:
    note = render("librarian-handoffs", items="\n".join(handoff_text(h) for h in items))
    return note + (render("librarian-handoffs-v2") if any(h.get("v2") for h in items) else "")


def todo_fix_prompt(problems: str) -> str:
    return render("librarian-todo-format", problems=problems)


def index_prompt() -> str:
    return render("librarian-index")


# ---------- shadow mode: the completion version ----------

def _state_rules() -> str:
    """The rules shared by the agent version and the edit-list version (the agent version adds its file-scope rule)."""
    return render("librarian-state-rules")


def _reference(d: pathlib.Path, limit: int = 12000) -> str:
    ref = d / "memory" / "reference"
    if not ref.is_dir():
        return "(no memory/reference/)"
    names = sorted(p.name for p in ref.glob("*.md"))
    readme = (ref / "README.md").read_text(errors="replace")[:limit] if (ref / "README.md").exists() else "(no README.md)"
    return "Files: " + ", ".join(names) + "\n\nREADME.md:\n" + readme


def state_edits_prompt(proj: str, d: pathlib.Path, before: str, memory_section: str, h_note: str, d_note: str) -> str:
    agent_md = (d / "AGENT.md").read_text(errors="replace") if (d / "AGENT.md").exists() else "(no AGENT.md)"
    return render("librarian-state-edits", project=proj, memory_section=memory_section, handoff_note=h_note,
                  dangling_note=d_note, agent_md=agent_md, reference=_reference(d), before=before, rules=_state_rules())


def _shadow_write(rec: dict, name: str | None = None, text: str | None = None) -> None:
    day = SHADOW_DIR / TODAY
    day.mkdir(parents=True, exist_ok=True)
    if name and text is not None:
        (day / name).write_text(text)
    with (SHADOW_DIR / f"{TODAY}.jsonl").open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _shadow_call(point: str, prompt: str) -> tuple[dict | None, dict]:
    rc, obj, _text = llm(point, prompt, None)
    so = (obj or {}).get("structured_output") if rc == 0 else None
    meta = {"cost_usd": (obj or {}).get("total_cost_usd"), "model": (obj or {}).get("model"),
            "provider": (obj or {}).get("provider")}
    if not isinstance(so, dict):
        meta["error"] = (obj or {}).get("llm_error") or f"exit {rc}"
        return None, meta
    return so, meta


def shadow_state(proj: str, d: pathlib.Path, before: str, after: str, agent_changed: bool,
                 memory_section: str, h_note: str, d_note: str) -> None:
    """Compare the completion version's proposal for the same input with the STATE.md the agent version actually wrote.
    Records only; writes no real file."""
    if not SHADOW:
        return
    try:
        so, meta = _shadow_call("librarian-state-edits", state_edits_prompt(proj, d, before, memory_section, h_note, d_note))
        rec = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": "librarian-state-edits",
               "project": proj, "agent_changed": agent_changed, **meta}
        if so is None:
            rec["ok"] = False
            _shadow_write(rec)
            log(_("{project}: shadow (completion version) has no result: {error}", project=proj, error=meta.get("error")))
            return
        proposed = None
        try:
            proposed = edits.apply(before, so.get("edits") or [])
        except edits.EditError as e:
            rec["edit_error"] = str(e)
        shadow_changed = bool(so.get("state_updated")) and proposed is not None and proposed != before
        rec.update(ok=proposed is not None, shadow_changed=shadow_changed, same_decision=shadow_changed == agent_changed,
                   edits=len(so.get("edits") or []), reason=str(so.get("reason") or "")[:400])
        if proposed is not None:
            rec["marker_problems"] = edits.check_markers(before, proposed, KEY_MARK)
            rec["identical_to_agent"] = proposed == after
            rec["similarity"] = round(difflib.SequenceMatcher(None, proposed, after).ratio(), 3)
            tmp = SHADOW_DIR / TODAY / f"{proj}.STATE.md"
            _shadow_write(rec, tmp.name, proposed)
            rec_todo = run([PY, str(HUB / "bin" / "check-state-todo.py"), str(tmp)])
            if rec_todo.returncode != 0:
                log(_("{project}: the shadow's proposed to-do section is malformed: {problems}", project=proj,
                      problems=head_spaced(rec_todo.stdout.rstrip(chr(10)), 2)))
        else:
            _shadow_write(rec)
        log(shadow_line(proj, shadow_changed, agent_changed, rec)
            + (_(", marker problems: {n}", n=len(rec["marker_problems"])) if rec.get("marker_problems") else ""))
    except Exception as e:   # a broken shadow does not affect the real run
        log(_("{project}: shadow mode failed: {error_type}: {error}", project=proj, error_type=type(e).__name__, error=e))


def shadow_line(what: str, shadow_changed: bool, agent_changed: bool, rec: dict) -> str:
    """the log line comparing the shadow's decision with the agent's (a project, or index.md)"""
    return (_("{project}: shadow (completion version): change={shadow} (agent version={agent})", project=what,
              shadow=shadow_changed, agent=agent_changed)
            + (_(", similarity {similarity}", similarity=rec["similarity"]) if "similarity" in rec else "")
            + (_(", edit list invalid: {error}", error=rec["edit_error"]) if "edit_error" in rec else ""))


def index_edits_prompt(before: str, digest_text: str) -> str:
    return render("librarian-index-edits", digest=digest_text, before=before)


ROSTER = re.compile(r"<!-- roster:begin -->.*?<!-- roster:end -->", re.S)


def shadow_index(before: str, after: str, digest_text: str) -> None:
    if not SHADOW:
        return
    try:
        so, meta = _shadow_call("librarian-index-edits", index_edits_prompt(before, digest_text))
        rec = {"ts": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "point": "librarian-index-edits",
               "agent_changed": before != after, **meta}
        if so is None:
            rec["ok"] = False
            _shadow_write(rec)
            log(_("{project}: shadow (completion version) has no result: {error}", project="index.md",
                  error=meta.get("error")))
            return
        proposed = None
        try:
            proposed = edits.apply(before, so.get("edits") or [])
        except edits.EditError as e:
            rec["edit_error"] = str(e)
        if proposed is not None:
            rec["roster_untouched"] = ROSTER.findall(before) == ROSTER.findall(proposed)
            rec["similarity"] = round(difflib.SequenceMatcher(None, proposed, after).ratio(), 3)
        shadow_changed = proposed is not None and proposed != before
        rec.update(ok=proposed is not None, shadow_changed=shadow_changed, same_decision=shadow_changed == (before != after),
                   relations=str(so.get("relations_found") or "")[:300])
        _shadow_write(rec, "index.md" if proposed is not None else None, proposed)
        log(shadow_line("index.md", shadow_changed, before != after, rec))
    except Exception as e:
        log(_("{project}: shadow mode failed: {error_type}: {error}", project="index.md", error_type=type(e).__name__,
              error=e))


# ---------- eval cases (docs/design/i18n.md E2) ----------
# Each night's real inputs to the librarian's judgment points, saved so prompt changes can be compared on the same
# inputs: the raw builder arguments (to re-render with new code), the prompts as rendered tonight (the "old" arm), a copy
# of what the model could read, and tonight's outcome. Platform eval dir only (0700, not in git, not backed up).
# Recording must never change or break the run.
CASE_KEEP_DAYS = 60


def _case_dir(point: str, name: str) -> pathlib.Path | None:
    base = hubconf.evals_dir()
    if base is None:
        return None
    root = base / "cases" / point
    d = root / TODAY / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    cutoff = datetime.date.today() - datetime.timedelta(days=CASE_KEEP_DAYS)
    for old in root.iterdir():
        if old.is_dir() and old.name < cutoff.isoformat():
            shutil.rmtree(old, ignore_errors=True)
    return d


def _case_meta() -> dict:
    return {"v": 1, "date": TODAY, "owner": OWNER, "assistant": ASSISTANT,
            "code": out(["git", "-C", str(HUB), "rev-parse", "HEAD"]).strip()}


def record_state_case(proj: str, d: pathlib.Path, sha: str, before: str, raw: dict, rendered: dict,
                      name: str | None = None) -> pathlib.Path | None:
    try:
        c = _case_dir("librarian-state", name or proj)
        if c is None:
            return None
        snap = c / "snapshot"
        snap.mkdir()
        tar = subprocess.run(["git", "-C", str(d), "archive", "--format=tar", sha], capture_output=True)
        subprocess.run(["tar", "-x", "-C", str(snap)], input=tar.stdout, check=True)
        (c / "args.json").write_text(json.dumps({**_case_meta(), "project": proj, "sha": sha, **raw, **rendered},
                                                ensure_ascii=False, indent=1))
        (c / "prompt.librarian-state.txt").write_text(
            state_prompt(proj, rendered["memory_section"], rendered["h_note"], rendered["d_note"]))
        (c / "prompt.librarian-state-edits.txt").write_text(state_edits_prompt(
            proj, snap, before, rendered["memory_section"], rendered["h_note"], rendered["d_note"]))
        return c
    except Exception as e:
        log(f"{proj}: eval case not recorded: {type(e).__name__}: {e}")
        return None


def record_index_case(before: str, digest_text: str) -> pathlib.Path | None:
    try:
        c = _case_dir("librarian-index", "index")
        if c is None:
            return None
        (c / "args.json").write_text(json.dumps({**_case_meta(), "index_before": before, "digest": digest_text},
                                                ensure_ascii=False, indent=1))
        (c / "prompt.librarian-index.txt").write_text(index_prompt())
        (c / "prompt.librarian-index-edits.txt").write_text(index_edits_prompt(before, digest_text))
        return c
    except Exception as e:
        log(f"index.md: eval case not recorded: {type(e).__name__}: {e}")
        return None


def record_outcome(case: pathlib.Path | None, outcome: dict, files: dict[str, str]) -> None:
    if case is None:
        return
    try:
        (case / "outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=1))
        for name, text in files.items():
            (case / f"after.{name}").write_text(text)
    except Exception as e:
        log(f"eval outcome not recorded ({case.name}): {type(e).__name__}: {e}")


# ---------- one project ----------

def repair_state_todo(proj: str, d: pathlib.Path) -> None:
    """The to-do section is malformed (haiku does not always follow the format, and then gen-todo cannot collect it):
    ask the model to fix only the format, content unchanged, and check again afterwards; if it is still wrong, log it
    and gen-todo lists it at the end of todo.md."""
    check = [PY, str(HUB / "bin" / "check-state-todo.py"), str(d / "STATE.md")]
    r = run(check)
    if r.returncode == 0:
        return
    problems = r.stdout.rstrip("\n")
    log(_("{project}: the STATE.md to-do section is malformed, asking the model to fix only the format: {problems}",
          project=proj, problems=head_spaced(problems, 3)))
    _rc, obj, _text = llm("librarian-todo-format", todo_fix_prompt(problems), d)
    add_cost(obj)
    r = run(check)
    if r.returncode == 0:
        log(_("{project}: to-do section format fixed", project=proj))
    else:
        log(_("{project}: the to-do section still has problems after the fix (listed at the end of todo.md): {problems}",
              project=proj, problems=head_spaced(r.stdout.rstrip(chr(10)), 2)))


def quarantine_stray(proj: str, d: pathlib.Path) -> bool:
    """The model may touch only ./STATE.md: extra files (on 2026-09-26 five projects wrote memory/STATE.md) are moved
    to quarantine and logged, so the next call does not read them as real content. A tracked file that was changed or
    deleted: keep a copy of the changed one as evidence, then restore it."""
    stray = []
    for line in git(d, "status", "--porcelain", "--untracked-files=all").split("\n"):
        parts = line.split()
        if len(parts) > 1 and parts[1] != "STATE.md":
            stray.append(parts[1])
    if not stray:
        return False
    qdir = STATE_DIR / "stray" / f"{proj}-{TODAY}"
    qdir.mkdir(parents=True, exist_ok=True)
    for f in stray:
        if run(["git", "-C", str(d), "ls-files", "--error-unmatch", "--", f]).returncode == 0:
            if (d / f).exists():
                shutil.copy(d / f, qdir / f.replace("/", "_"))
            run(["git", "-C", str(d), "checkout", "-q", "--", f])
        else:
            shutil.move(str(d / f), str(qdir / f.replace("/", "_")))
    log(_("{project}: warning: the model touched files other than STATE.md, moved to {dir}: {files}", project=proj,
          dir=qdir, files=" ".join(stray)))
    return True


def dangling_refs(d: pathlib.Path) -> str:
    """Whether the memory/ paths STATE.md mentions (may contain *) still exist; returns the shell version's format
    (each preceded by a space)."""
    st = d / "STATE.md"
    refs = sorted({r for r in re.findall(r"memory/[A-Za-z0-9._*/-]+\.md", st.read_text(errors="replace"))
                   if "YYYY" not in r}) if st.exists() else []
    return "".join(f" {r}" for r in refs if not glob.glob(r, root_dir=d))


def workspace(proj: str) -> pathlib.Path | None:
    return hubconf.path((hubconf.roster().get(proj) or {}).get("workspace"))


def admit_anchors(proj: str, d: pathlib.Path, before: str) -> list[str]:
    """Anchors the model added are kept only if they resolve now (ok, or pending on an unmerged branch): the model never
    sees the code, and a path it took from the notes may be relative to the wrong folder. The others lose the sign and
    stay as plain code spans, so the nightly check never reports an anchor that was wrong from the start. Returns the
    ones it unanchored."""
    st = d / "STATE.md"
    if not st.exists():
        return []
    text = st.read_text(errors="replace")
    old = {a.raw for a in state_anchors.find(before, OWNER)}
    new = [a for a in state_anchors.find(text, OWNER) if a.raw not in old]
    if not new:
        return []
    repo, _why = state_anchors.Repo.open(workspace(proj))   # cannot be checked: nothing new is admitted
    bad = sorted({a.raw for a in new if repo is None or repo.resolve(a).outcome not in ("ok", "pending")})
    if bad:
        st.write_text(state_anchors.unanchor(text, OWNER, set(bad)))
    return bad


def stale_anchors(proj: str, d: pathlib.Path) -> list[dict]:
    """STATE.md's anchors that no longer resolve on the project's base branch (docs/design/state-anchors.md), as
    records; pending and untracked are not stale, and a workspace that cannot be checked gives none (the nightly
    state-anchors job reports that)"""
    st = d / "STATE.md"
    if not st.exists():
        return []
    results, _why = state_anchors.check(st.read_text(errors="replace"), OWNER, workspace(proj))
    return [state_anchors.record(r) for r in state_anchors.stale(results)]


def anchor_note(stale: list[dict]) -> str:
    return ("\nThese code anchors in STATE.md no longer resolve on the project's main branch. You cannot see the "
            "code, so change a statement only on the strength of the memory notes; what to do with each:\n"
            + "\n".join(state_anchors.note_line(r) for r in stale)) if stale else ""


def memory_input(d: pathlib.Path, rng: str, changed: str) -> tuple[str, str, bool]:
    """The prompt's memory section for commits `rng` (changed = the memory files they touch): (section, diff, truncated)."""
    if not changed:
        return memory_text("", "", False), "", False
    # The script supplies the diff: the model has no Bash, cannot run git log itself and cannot read files already
    # git rm'd. Too large: truncate it, and the model fills in with Read
    diff = git(d, "log", "-p", "--reverse", "--format==== commit %h %ad %s", "--date=short", rng, "--", "memory/")
    truncated = len(diff) > MAX_DIFF_CHARS
    return memory_text(changed, diff[:MAX_DIFF_CHARS], truncated), diff[:MAX_DIFF_CHARS], truncated


def memory_text(changed: str, diff: str, truncated: bool) -> str:
    if not changed:
        return f"Nothing changed in memory/ this time; there are only {OWNER}'s handoffs below to handle."
    note = (f" (the diff is too long: only its first {MAX_DIFF_CHARS} characters are attached; read the rest with Read, "
            "from the files listed above that still exist)") if truncated else ""
    return (f"Since the last check, these memory files of this project changed:\n{changed}\n\n"
            f"What actually changed (git log -p, oldest first){note}:\n{diff}")


def dangling_note(dangling: str) -> str:
    return ("\nSTATE.md now refers to these files, which no longer exist (moved or deleted); this time the references "
            "must be fixed\nto point where the content actually lives now (usually a topic document under "
            f"memory/reference/):{dangling}") if dangling else ""


def process(proj: str, d: pathlib.Path, handoffs: list[dict], digest: list[str]) -> None:
    log(f"--- {proj} ---")
    with LOG.open("a") as fh:   # hardened against committed symlinks first (hublib.pull_copy)
        pulled = hublib.pull_copy(d, text=True, stdout=fh, stderr=fh)
    if pulled.returncode != 0:
        log(f"{proj}: git pull failed, skip")
        return
    head = git(d, "rev-parse", "HEAD")
    ckpt = STATE_DIR / f"{proj}.sha"
    last = ckpt.read_text().strip() if ckpt.exists() else ""
    mine = [h for h in handoffs if h.get("project") == proj and h.get("effect") not in KEYED_EFFECTS] + load_records(proj)
    ids = [jstr(h.get("id")) for h in mine]
    nums = " ".join(f"#{jstr(h.get('handoff_id'))}" for h in mine)
    if head == last and not ids:
        log(f"{proj}: no new commits, skip")
        return

    changed, rng = "", ""
    if head != last:
        rng = f"{last}..{head}" if last and run(["git", "-C", str(d), "cat-file", "-e", last]).returncode == 0 else head
        names = git(d, "log", "--name-only", "--pretty=format:", rng, "--", "memory/")
        changed = "\n".join(sorted({n for n in names.split("\n") if n}))
    if not changed and not ids:
        log(f"{proj}: no memory/ changes in range {rng}")
        ckpt.write_text(head + "\n")
        return

    if changed:
        log(f"{proj}: changed memory files: {changed.replace(chr(10), ' ')} ")
    memory_section, diff, truncated = memory_input(d, rng, changed)

    h_note = ""
    if ids:
        log(_("{project}: assistant handoffs {nums}", project=proj, nums=nums))
        h_note = handoff_note(mine)

    dangling = dangling_refs(d)
    if dangling:
        log(_("{project}: STATE.md refers to files that do not exist:{files}", project=proj, files=dangling))
    anchors = stale_anchors(proj, d)
    if anchors:
        log(_("{project}: STATE.md has stale code anchors: {anchors}", project=proj,
              anchors=" ".join(a["raw"] for a in anchors)))
    d_note = dangling_note(dangling) + anchor_note(anchors)

    pre = git(d, "rev-parse", "HEAD")
    before = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""   # input for shadow mode
    # Only ./STATE.md may be written: saying so in the prompt does not stop it (a small model changed memory/reference/
    # anyway), so the librarian-state judgment point enforces it with permission rules (dontAsk, only Edit(./STATE.md));
    # the quarantine check below is the second line of defence
    case = record_state_case(proj, d, pre, before, {"changed": changed, "diff": diff, "truncated": truncated,
                                                     "handoffs": mine, "dangling": dangling, "anchors": anchors},
                             {"memory_section": memory_section, "h_note": h_note, "d_note": d_note})
    rc, obj, text = llm("librarian-state", state_prompt(proj, memory_section, h_note, d_note), d)
    if rc != 0 or not text:
        # The call itself failed: it may have stopped halfway, so nothing is kept. Restore the copy to a clean state
        # rather than leave it for the next night
        run(["git", "-C", str(d), "checkout", "-q", "--", "."])
        run(["git", "-C", str(d), "clean", "-fdq"])
        log(_("{project}: claude invocation failed (exit {rc}); the copy is restored and the checkpoint not advanced",
              project=proj, rc=rc))
        return
    if (cost := add_cost(obj)):
        log(f"{proj}: call cost ${cost} (model: {used_model(obj)})")

    so = (obj or {}).get("structured_output")
    # No structured_output (happens every few nights): use git as usual to decide whether anything actually changed,
    # and commit if it did; there is just no summary for the digest, and the checkpoint does not advance (a fixed
    # STATE.md used to be left until the next night)
    no_output = so is None
    if no_output:
        o = obj or {}
        # shell: jq's newline became a trailing space through tr
        said = (jstr(o.get("result")) if present(o.get("result")) else "") + "\n"
        log(_("{project}: no structured_output (subtype={subtype}, turns={turns}; the model last said: {said}); "
              "going by the actual file changes", project=proj,
              subtype=jstr(o.get("subtype")) if present(o.get("subtype")) else "?",
              turns=jstr(o.get("num_turns")) if present(o.get("num_turns")) else "?",
              said=said.replace(chr(10), " ")[:300]))
    so = so if isinstance(so, dict) else {}
    summary = jstr(so["summary"]) if present(so.get("summary")) else ""
    reason = jstr(so["reason"]) if present(so.get("reason")) else ""
    reported = "unknown" if no_output or obj is None else jq_bool(so.get("state_updated"))
    worth = jq_bool(so.get("worth_digesting"))

    stray = quarantine_stray(proj, d)
    repair_state_todo(proj, d)
    # what the model wrote, before admission (the eval case records this: evals score the model, not the platform)
    written = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""
    if (unanchored := admit_anchors(proj, d, before)):
        log(_("{project}: anchors that do not resolve on the project's main were turned back into plain code: "
              "{anchors}", project=proj, anchors=" ".join(unanchored)))
        if git(d, "rev-parse", "HEAD") != pre:
            # the project's hook already committed the model's edit: commit this one on top (below only commits when
            # HEAD did not move)
            if run(["git", "add", "STATE.md"], cwd=d).returncode == 0 and \
                    run(["git", "commit", "-m", "librarian: admit STATE.md anchors", "-q"], cwd=d).returncode == 0:
                run([str(SAFE_PUSH)], cwd=d)

    # Go by the actual git state. Trap: the project's own SessionEnd hook may already have committed and pushed
    # STATE.md while the call was still running (a headless call is a full session too) -- so first compare whether
    # HEAD moved; that is the truth the hook cannot overwrite
    post = git(d, "rev-parse", "HEAD")
    changed_state, state_diff, new_sha = False, "", post
    if post != pre:
        state_diff = git(d, "diff", pre, post, "--", "STATE.md")
        changed_state = bool(state_diff) or not unanchored   # admission may have undone the model's only edit
    elif run(["git", "-C", str(d), "diff", "--quiet", "--", "STATE.md"]).returncode != 0:
        changed_state, state_diff = True, git(d, "diff", "--", "STATE.md")
    actually = "true" if changed_state else "false"
    if unanchored and not changed_state and written != before:
        # the model's only edit was anchors that do not resolve: it did change the file, and the run is done
        log(_("{project}: admission undid the model's only edit (anchors that do not resolve)", project=proj))
        actually = "true"
    if reported != "unknown" and reported != actually:
        log(_("{project}: warning: the agent reported state_updated={reported}, but the actual file change={actually} "
              "(going by the actual files)", project=proj, reported=reported, actually=actually))
    after = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""
    shadow_state(proj, d, before, after, changed_state, memory_section, h_note, d_note)
    record_outcome(case, {"rc": rc, "structured_output": so or None, "changed": changed_state, "stray": bool(stray),
                          "model": used_model(obj), "unanchored": unanchored}, {"STATE.md": written})

    if changed_state:
        # the hook did not take over (the project did not go through its hook this time, or the hook is not set
        # up): commit and push ourselves
        if post == pre:
            if run(["git", "add", "STATE.md"], cwd=d).returncode == 0 and \
                    run(["git", "commit", "-m", "librarian: update STATE.md", "-q"], cwd=d).returncode == 0:
                run([str(SAFE_PUSH)], cwd=d)
            new_sha = git(d, "rev-parse", "HEAD")
        log(f"{proj}: STATE.md updated & pushed ({new_sha})")
        # the reason label is a token registrar.py reads back (removal_reason, either set): the UI language's, like the
        # rest of the block
        label = todo_tokens.tokens(hubconf.lang_ui())["reason_label"]
        block = (f"## {TODAY} · {proj}\n\n**commit**: `{new_sha}`\n\n{label}: {reason or _('(the agent gave no reason)')}\n\n"
                 + (_("**Handoffs from {assistant}**: {nums}", assistant=hubconf.assistant_name(), nums=nums) + "\n\n" if ids else "")
                 + f"```diff\n{state_diff}\n```\n\n---\n\n")
        with REVIEW_LOG.open("a") as f:
            f.write(block)
        log(_("{project}: written to review-log.md for later spot checks", project=proj))
    else:
        # Keep the reason even when nothing is updated: when STATE.md stays placeholder content, that is how to tell
        # whether the model is too conservative or the prompt is wrong
        log(_("{project}: STATE.md not updated; reason: {reason}", project=proj,
              reason=reason or _("(the agent gave no reason)")))

    # The model said it updated but nothing changed, or it touched files it should not have: treat this run as not done,
    # do not advance the checkpoint, and run the same range next time
    if (reported == "true" and actually != "true") or stray or no_output:
        log(_("{project}: this result is not trustworthy; the checkpoint is not advanced, retrying next time", project=proj))
        return
    ckpt.write_text(new_sha + "\n")

    if ids:
        r = run([PY, str(HUB / "bin" / "hubbus"), "done", *ids, "--note",
                 _("librarian applied this to STATE.md ({date}, see review-log)", date=TODAY)], to_log=True)
        if r.returncode == 0:
            log(_("{project}: assistant handoffs {nums} closed (the bridge reports it to the assistant)", project=proj,
                  nums=nums))
        else:
            log(_("{project}: closing the assistant handoffs failed; they are processed again tomorrow night (STATE.md "
                  "was already changed; processing them again should change nothing)", project=proj))

    if worth == "true" and summary:
        digest.append(f"## {proj}\n\n{summary}\n\n")


def main() -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    copies = hubconf.p("memory_copies", "~/.memory-copies")
    log("=== librarian run start ===")
    # quarantine only keeps evidence; cleared automatically after 14 days
    cutoff = datetime.datetime.now().timestamp() - 14 * 86400
    for q in (STATE_DIR / "stray").glob("*") if (STATE_DIR / "stray").is_dir() else []:
        if q.stat().st_mtime < cutoff:
            shutil.rmtree(q, ignore_errors=True) if q.is_dir() else q.unlink()

    handoffs = load_handoffs()
    digest: list[str] = []
    for d in sorted(p for p in copies.glob("*/") if (p / ".git").is_dir()):
        process(d.name, d, handoffs, digest)

    # Today's digest: the project-summary section, then ask the model to mark the relations between projects
    # (index.md). The digest is written in sections (hublib.digest_append): each job writes its own, and a rerun
    # replaces only its own
    run(["git", "pull", "-q"], cwd=HUB, to_log=True)
    if digest:
        try:
            hublib.digest_append("projects", "".join(digest))
        except OSError:
            log(_("digest {date}: write failed", date=TODAY))
        index_file = hubconf.data_file("index.md")
        index_before = index_file.read_text(errors="replace") if index_file.exists() else ""
        digest_now = hublib.digest_path()
        icase = record_index_case(index_before, digest_now.read_text(errors="replace") if digest_now.exists() else "")
        rc, obj, text = llm("librarian-index", index_prompt(), hubconf.data_dir())
        if rc == 0 and text:
            if (cost := add_cost(obj)):
                log(f"index.md: call cost ${cost} (model: {used_model(obj)})")
            so = (obj or {}).get("structured_output")
            rel = so.get("relations_found") if isinstance(so, dict) else None
            log(f"index.md: {jstr(rel) if present(rel) else ''}")
        else:
            log(f"index.md update failed (exit {rc})")
        digest_file = hublib.digest_path()
        so = (obj or {}).get("structured_output") if rc == 0 else None
        record_outcome(icase, {"rc": rc, "structured_output": so if isinstance(so, dict) else None, "model": used_model(obj)},
                       {"index.md": index_file.read_text(errors="replace") if index_file.exists() else ""})
        shadow_index(index_before, index_file.read_text(errors="replace") if index_file.exists() else "",
                     digest_file.read_text(errors="replace") if digest_file.exists() else "".join(digest))
    else:
        # no project summaries today: drop this section (in case an earlier run the same day wrote one)
        hublib.digest_append("projects", "")
        log("nothing to digest today")
    log(f"=== librarian run end (total cost today: ${f'{total_cost:.6f}' if cost_added else '0'}) ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
