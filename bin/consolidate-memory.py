#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Consolidation (looking back): fold each project's memory date files that have sat too long into reference/, and
along the way fix statements in reference/ and STATE.md that newer records overturned. The librarian only looks
forward (new memory -> STATE.md); this script looks back, so memory does not become a running log and outdated
statements do not keep looking settled.

What it does for each project:
1. Mechanical selection (zero LLM): only date files (memory/YYYY-MM-DD[-topic].md) older than OLDER_THAN_DAYS, at
   most MAX_FOLD_BYTES at a time (oldest first); none: skip. After a success, the same project is not run again
   within MIN_INTERVAL_DAYS.
2. Clone a clean memory repo (from the bare repo) into a temporary directory; the model works only there --
   ~/.memory-copies is not touched at all until the push succeeds, and on failure the whole thing is thrown away,
   so nothing is left half-changed.
3. The model (judgment point consolidate's model_group: strong; hub.json llm.points.consolidate.model can change it)
   may write only ./memory/reference/** and ./STATE.md (--permission-mode dontAsk + --allowedTools; the prompt does
   not stop haiku from changing other files); no Bash, so it cannot delete files. Through structured output it reports
   where each date file went, what it did not keep, and which statements it corrected (every correction must quote
   the newer file verbatim).
4. The script checks for itself and does not trust the model's own report:
   - git status shows changes outside STATE.md / memory/reference/*.md -> moved to .state/stray/, the whole project
     is given up
   - a hedge ("expected/possibly…") turned into a definite statement (comparing deleted/folded sentences that
     carry a hedge with the newly added ones) without verbatim evidence behind it -> that file is reverted; the date
     files that went into it are not deleted this time
   - a correction's quote cannot be found in the file it names -> that file is reverted
   - the to-do section of STATE.md got broken (check-state-todo.py) -> STATE.md is reverted
   - git rm only the date files the model declared folded and whose destinations all still exist (not reverted)
   - URLs/`code snippets` from the deleted date files that cannot be found after folding -> not blocking, listed in
     review-log for a person to spot-check
5. commit -> git-safe-push.sh (a divergence from a project session pushing at the same time is merged and retried
   automatically; a real conflict gives up, the patch is kept in .state/consolidate/failed/) -> update
   ~/.memory-copies/<project> under with-lock.sh.
6. Every change (reason, corrections, diff, warnings from the mechanical checks) is written to review-log.md and
   committed with commit-hub-files.sh. When the librarian's checkpoint sits exactly at this run's starting point it is
   advanced too, so the librarian does not read the consolidation diff again.

Usage:
  consolidate-memory.py                      for the scheduler: at most MAX_PROJECTS due projects (most needed first)
  consolidate-memory.py --list               list each project's status (zero LLM)
  consolidate-memory.py --dry-run [--project X] [--keep]
                                             only produce the proposed diff and review-log entry; no push, no
                                             review-log/state writes; report in .state/consolidate/dry-run/
  consolidate-memory.py --project X          run only X (ignores MIN_INTERVAL_DAYS; still needs old enough date files)
"""
from __future__ import annotations

import argparse
import datetime
import difflib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
HOME = pathlib.Path.home()
HUB = pathlib.Path(os.environ.get("AGENT_HUB") or hubconf.ROOT)
COPIES = hubconf.p("memory_copies", "~/.memory-copies")
STATE_DIR = HUB / ".state"
WORK_STATE = STATE_DIR / "consolidate" / "state.json"
LOG_DIR = HUB / ".logs"
REVIEW_LOG = hubconf.data_file("review-log.md")
BIN = HUB / "bin"
TODAY = datetime.date.today()

OLDER_THAN_DAYS = 14      # how old a date file must be to fold: ones from the last two weeks are still looked at often
MIN_INTERVAL_DAYS = 7     # after a success, not the same project again within a week
RETRY_AFTER_DAYS = 2      # how long after a failure to retry (don't burn the same failure every night)
MAX_PROJECTS = 3          # at most this many projects a night
BUDGET_MINUTES = 30       # past this, no new project is started: with CALL_TIMEOUT it ends by 02:50 at the
                          # latest, not overlapping the 03:00 librarian
CALL_TIMEOUT = 20 * 60    # limit for one model call
MAX_FOLD_BYTES = 60000    # total size limit of the date files folded at once (taken oldest first)
MAX_LOG_DIFF = 40000      # limit for the diff in review-log; beyond it, truncate and add the command for
                          # the full diff

RAW_RE = re.compile(r"^memory/(\d{4}-\d{2}-\d{2})(-[a-z0-9-]+)?\.md$")
REF_RE = re.compile(r"^memory/reference/[^/]+\.md$")

# Hedges: when these words vanish from a sentence and the rest of it stays the same, that is the summary drift of
# "expected -> confirmed". Content-language data: locales/<lang>.consolidate.json, per language the hedges, the ones the
# prompt names as examples, the punctuation the comparison ignores besides ASCII's, and what ends a link besides ASCII's
# brackets. The checks use every language's (a note may be in any of them), the content language's first; the prompt
# names the content language's examples.
def _text_data() -> dict[str, dict]:
    first = hubconf.lang_content()
    files = sorted(i18n.LOCALES.glob("*.consolidate.json"), key=lambda f: (f.name != f"{first}.consolidate.json", f.name))
    return {f.name.removesuffix(".consolidate.json"): json.loads(f.read_text()) for f in files}


TEXT = _text_data()
HEDGES = list(dict.fromkeys(h for d in TEXT.values() for h in d["hedges"]))
PUNCTUATION = "".join(dict.fromkeys(c for d in TEXT.values() for c in d["punctuation"]))
LINK_END = "".join(dict.fromkeys(c for d in TEXT.values() for c in d["link_end"]))

GIT_ENV = {
    **os.environ,
    # the memory repo does not run the project's git hooks (see known-issues, "The memory worktree inherits the
    # project's git hooks")
    "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
    "PATH": "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", ""),
}

_log_file = None


def log(msg: str) -> None:
    line = f"[{datetime.datetime.now():%H:%M:%S}] {msg}"
    if _log_file:
        with open(_log_file, "a") as f:
            f.write(line + "\n")
    # the scheduler sends stdout into the same log file; print as well only when run by hand in a terminal
    if sys.stdout.isatty() or not _log_file:
        print(line, flush=True)


def git(*args, cwd=None, git_dir=None, check=True) -> str:
    cmd = ["git"] + ([f"--git-dir={git_dir}"] if git_dir else []) + list(args)
    r = subprocess.run(cmd, cwd=cwd, env=GIT_ENV, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(_("git {args} failed: {stderr}", args=" ".join(args), stderr=r.stderr.strip()[:500]))
    return r.stdout


def load_state() -> dict:
    try:
        return json.loads(WORK_STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state: dict) -> None:
    WORK_STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = WORK_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    tmp.replace(WORK_STATE)


def days_since(iso: str | None) -> float:
    if not iso:
        return float("inf")
    return (TODAY - datetime.date.fromisoformat(iso)).days


# ---------- mechanical selection ----------

def project_origin(copy: pathlib.Path) -> str | None:
    r = subprocess.run(["git", "-C", str(copy), "remote", "get-url", "origin"], capture_output=True, text=True)
    url = r.stdout.strip()
    return url if r.returncode == 0 and url else None


def survey(name: str, origin: str) -> dict:
    """Read the bare repo's agent-memory branch directly (the latest, and the copy is not touched)."""
    files = git("ls-tree", "-r", "--name-only", "agent-memory", "memory/", git_dir=origin).split()
    cutoff = TODAY - datetime.timedelta(days=OLDER_THAN_DAYS)
    old = []
    for f in sorted(files):
        m = RAW_RE.match(f)
        if m and datetime.date.fromisoformat(m.group(1)) < cutoff:
            old.append(f)
    batch, total = [], 0
    for f in old:
        size = int(git("cat-file", "-s", f"agent-memory:{f}", git_dir=origin).strip())
        if batch and total + size > MAX_FOLD_BYTES:
            break
        batch.append(f)
        total += size
    raw = [f for f in files if RAW_RE.match(f)]
    return {"name": name, "origin": origin, "old": old, "batch": batch, "batch_bytes": total,
            "raw": raw, "oldest": old[0][7:17] if old else None}


def candidates(state: dict, only: str | None, ignore_interval: bool) -> list[dict]:
    out = []
    for copy in sorted(COPIES.iterdir()):
        if not (copy / ".git").exists() or (only and copy.name != only):
            continue
        origin = project_origin(copy)
        if not origin or not pathlib.Path(origin).is_dir():
            log(_("{project}: origin is not a local bare repo ({origin}), skipped", project=copy.name, origin=origin))
            continue
        try:
            s = survey(copy.name, origin)
        except (RuntimeError, ValueError) as e:
            log(_("{project}: cannot read the agent-memory branch, skipped: {error}", project=copy.name, error=e))
            continue
        st = state.get(copy.name, {})
        s["last_ok"], s["last_try"] = st.get("last_ok"), st.get("last_try")
        if not s["batch"]:
            s["skip"] = _("no date files older than {days} days", days=OLDER_THAN_DAYS)
        elif not ignore_interval and days_since(s["last_ok"]) < MIN_INTERVAL_DAYS:
            s["skip"] = _("consolidated recently ({date}); next run after a week", date=s["last_ok"])
        elif not ignore_interval and s["last_try"] != s["last_ok"] and days_since(s["last_try"]) < RETRY_AFTER_DAYS:
            s["skip"] = _("failed on {date}, retrying after {days} days", date=s["last_try"], days=RETRY_AFTER_DAYS)
        out.append(s)
    # most needed first: never consolidated, then the older the oldest date file, the earlier
    out.sort(key=lambda s: (s["last_ok"] is not None, s["oldest"] or "9999"))
    return out


# ---------- model call ----------

# The output schema is in contracts/llm/consolidate.schema.json (judgment point consolidate, applied by bin/llm.py)


def build_prompt(s: dict, repo: pathlib.Path, *, owner: str | None = None, lang: str | None = None) -> str:
    """The prompt (templates/prompts/consolidate.md); owner and lang default to the configured ones (an eval pins them)."""
    import prompts
    batch_text = "\n\n".join(f"===== {f} =====\n{(repo / f).read_text()}" for f in s["batch"])
    newer = [f for f in s["raw"] if f not in s["batch"]]
    refs = []
    for p in sorted((repo / "memory" / "reference").glob("*.md")):
        rel = str(p.relative_to(repo))
        when = git("log", "-1", "--format=%ad", "--date=short", "--", rel, cwd=repo).strip()
        refs.append(f"- {rel} (last modified {when or '?'})")
    state_when = git("log", "-1", "--format=%ad", "--date=short", "--", "STATE.md", cwd=repo).strip()
    return prompts.render("consolidate", lang=lang, owner=owner, project=s["name"], older_than_days=OLDER_THAN_DAYS,
                          batch_text=batch_text, newer=", ".join(newer) or "(none)",
                          refs="\n".join(refs) or "(no topic documents yet)", state_when=state_when or "?",
                          hedges="/".join(TEXT[lang or hubconf.lang_content()]["prompt_hedges"]))


def run_model(prompt: str, repo: pathlib.Path, model: str) -> tuple[dict | None, str]:
    # judgment point consolidate (permission dontAsk, only Edit memory/reference/** and STATE.md, schema:
    # contracts/llm-points.yaml)
    cmd = [sys.executable, str(HUB / "bin" / "llm.py"), "run", "consolidate", *(["--model", model] if model else [])]
    env = dict(GIT_ENV)  # llm.py decides claude's credentials (headless token by default)
    try:
        r = subprocess.run(cmd, input=prompt, cwd=repo, env=env, capture_output=True, text=True, timeout=CALL_TIMEOUT + 60)
    except subprocess.TimeoutExpired:
        return None, _("over {minutes} minutes", minutes=CALL_TIMEOUT // 60)
    if r.returncode != 0 or not r.stdout.strip():
        return None, _("exit {rc}: {stderr}", rc=r.returncode, stderr=r.stderr.strip()[-500:])
    try:
        return json.loads(r.stdout), ""
    except ValueError:
        return None, _("the output is not JSON: ") + r.stdout[:300]


# ---------- mechanical checks ----------

def norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def has_hedge(s: str) -> bool:
    low = s.lower()
    return any(h in low for h in HEDGES)


def strip_hedges(s: str) -> str:
    low = s.lower()
    for h in sorted(HEDGES, key=len, reverse=True):
        low = low.replace(h, "")
    return re.sub(r"[\s\-*#>`_,.;:()\[\]" + re.escape(PUNCTUATION) + "]+", "", low)


def diff_lines(repo: pathlib.Path, base: str, path: str) -> tuple[list[str], list[str]]:
    """The lines of this file removed/added between base and the working tree (a new file counts as all added)."""
    out = git("diff", "--no-color", "-U0", base, "--", path, cwd=repo, check=False)
    if not out and not git("ls-files", "--", path, cwd=repo).strip():
        return [], (repo / path).read_text().splitlines()
    removed = [l[1:] for l in out.splitlines() if l.startswith("-") and not l.startswith("---")]
    added = [l[1:] for l in out.splitlines() if l.startswith("+") and not l.startswith("+++")]
    return removed, added


def similar(a: str, b: str, threshold: float = 0.75) -> bool:
    if len(a) < 8 or len(b) < 8:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sm.real_quick_ratio() >= threshold and sm.quick_ratio() >= threshold and sm.ratio() >= threshold


def at_base(repo: pathlib.Path, base: str, path: str) -> str | None:
    r = subprocess.run(["git", "show", f"{base}:{path}"], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def verify_corrections(repo, base, corrections) -> tuple[list[dict], list[dict]]:
    ok, bad = [], []
    for c in corrections:
        src = at_base(repo, base, c.get("evidence_file", "").lstrip("./"))
        quote = norm(c.get("evidence_quote", ""))
        (ok if src is not None and len(quote) >= 4 and quote in norm(src) else bad).append(c)
    return ok, bad


def hedge_upgrades(repo, base, changed, batch, good_corrections) -> dict[str, list[tuple[str, str]]]:
    """Returns {file: [(original hedged sentence, newly added definite sentence)]}; corrections that already have
    verbatim evidence do not count."""
    hedged_src = []  # hedged sentences that were deleted or folded away
    for f in batch:
        hedged_src += [l for l in (at_base(repo, base, f) or "").splitlines() if has_hedge(l)]
    per_file_added = {}
    for f in changed:
        removed, added = diff_lines(repo, base, f)
        hedged_src += [l for l in removed if has_hedge(l)]
        per_file_added[f] = [l for l in added if not has_hedge(l)]
    src_norm = [(l, strip_hedges(l)) for l in hedged_src]
    afters = [norm(c["after"]) for c in good_corrections]
    found: dict[str, list[tuple[str, str]]] = {}
    for f, added in per_file_added.items():
        for a in added:
            an = strip_hedges(a)
            if any(x and (x in norm(a) or similar(x, norm(a), 0.8)) for x in afters):
                continue
            for orig, on in src_norm:
                if similar(on, an):
                    found.setdefault(f, []).append((orig.strip(), a.strip()))
                    break
    return found


def revert(repo: pathlib.Path, base: str, path: str) -> None:
    if at_base(repo, base, path) is not None:
        git("checkout", base, "--", path, cwd=repo)
    else:
        (repo / path).unlink(missing_ok=True)


def salient_missing(repo: pathlib.Path, base: str, deleted: list[str]) -> dict[str, list[str]]:
    corpus = norm((repo / "STATE.md").read_text() if (repo / "STATE.md").exists() else "")
    for p in (repo / "memory").rglob("*.md"):
        if str(p.relative_to(repo)) not in deleted:
            corpus += norm(p.read_text())
    out = {}
    for f in deleted:
        text = at_base(repo, base, f) or ""
        toks = set(re.findall(r"https?://[^\s)>\]" + re.escape(LINK_END) + "]+", text)) | \
            set(re.findall(r"`([^`\n]{3,80})`", text))
        miss = sorted(t for t in toks if norm(t) not in corpus)
        if miss:
            out[f] = miss[:15]
    return out


def state_todo_ok(path: pathlib.Path) -> bool:
    # the platform's Python (hubconf.launch), never python3 from PATH (AGENTS.md): check-state-todo.py needs 3.10+
    return subprocess.run([*hubconf.launch(BIN / "check-state-todo.py"), str(path)],
                          env=GIT_ENV, capture_output=True).returncode == 0


def state_todo_broken(repo: pathlib.Path, base: str) -> bool:
    """The to-do section was broken by this run (already broken before does not count; that is the librarian's business)."""
    if state_todo_ok(repo / "STATE.md"):
        return False
    with tempfile.NamedTemporaryFile("w", suffix=".md") as f:
        f.write(at_base(repo, base, "STATE.md") or "")
        f.flush()
        return state_todo_ok(pathlib.Path(f.name))


# ---------- one project ----------

def consolidate(s: dict, model: str, dry_run: bool, keep: bool) -> dict:
    """Returns {"status": ok|noop|failed, "entry": the review-log entry or None, ...}"""
    name = s["name"]
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"consolidate-{name}-"))
    repo = tmp / "repo"
    result = {"status": "failed", "entry": None, "tmp": tmp}
    try:
        # without symlinks: the date files are read, sent to the model and folded into what gets pushed back
        git("clone", "-q", "--config", "core.symlinks=false", "--branch", "agent-memory", s["origin"], str(repo))
        base = git("rev-parse", "HEAD", cwd=repo).strip()
        log(_("{project}: consolidating {n} {n:date file|date files} ({bytes} bytes, {first} … {last}), {left} left for next time; "
              "model {model}", project=name, n=len(s["batch"]), bytes=s["batch_bytes"], first=s["batch"][0],
              last=s["batch"][-1], left=len(s["old"]) - len(s["batch"]), model=model))

        out, err = run_model(build_prompt(s, repo), repo, model)
        if out is None:
            log(_("{project}: the model call failed ({error}); giving up, the copy is untouched", project=name,
                  error=err))
            return result
        cost = out.get("total_cost_usd")
        denials = out.get("permission_denials") or []
        log(_("{project}: model done (turns={turns}, cost=${cost}), blocked by permissions {n} {n:time|times}", project=name,
              turns=out.get("num_turns"), cost=cost, n=len(denials)))
        so = out.get("structured_output")
        if not so:
            log(_("{project}: no structured_output (the model last said: {said}); giving up", project=name,
                  said=str(out.get("result", ""))[:300]))
            return result

        if git("rev-parse", "HEAD", cwd=repo).strip() != base:
            log(_("{project}: HEAD was moved (the model should not be able to commit); giving up", project=name))
            return result
        changed, stray = [], []
        for line in git("status", "--porcelain", "--untracked-files=all", cwd=repo).splitlines():
            path = line[3:].strip().strip('"')
            (changed if path == "STATE.md" or REF_RE.match(path) else stray).append(path)
        if stray:
            qdir = STATE_DIR / "stray" / f"consolidate-{name}-{TODAY}"
            qdir.mkdir(parents=True, exist_ok=True)
            for p in stray:
                if (repo / p).is_file():
                    shutil.copy(repo / p, qdir / p.replace("/", "_"))
            log(_("{project}: warning: the model touched files it may not touch, evidence in {dir}: {files}; giving up "
                  "entirely", project=name, dir=qdir, files=" ".join(stray)))
            return result

        warnings: list[str] = []
        reverted: set[str] = set()

        good, bad = verify_corrections(repo, base, so.get("corrections", []))
        for c in bad:
            f = c.get("file", "").lstrip("./")
            if f in changed and f not in reverted:
                revert(repo, base, f)
                reverted.add(f)
            warnings.append(_("reverted `{file}`: the quote for the correction “{before}” → “{after}” is not in "
                              "`{evidence_file}`", file=f, before=c.get("before", "")[:80], after=c.get("after", "")[:80],
                              evidence_file=c.get("evidence_file")))
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]

        for f, pairs in hedge_upgrades(repo, base, [c for c in changed if c not in reverted],
                                       s["batch"], good).items():
            revert(repo, base, f)
            reverted.add(f)
            for orig, new in pairs[:3]:
                warnings.append(_("reverted `{file}`: hedging seems to have been removed, with no verbatim evidence: "
                                  "“{before}” → “{after}”", file=f, before=orig[:100], after=new[:100]))

        if "STATE.md" in changed and "STATE.md" not in reverted and state_todo_broken(repo, base):
            revert(repo, base, "STATE.md")
            reverted.add("STATE.md")
            warnings.append(_("reverted `STATE.md`: the `{heading}` section's format was broken",
                              heading=todo_tokens.tokens(hubconf.lang_content())["todo_heading"]))
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]

        # What to git rm: declared folded by the model ∩ this run's batch ∩ destinations all valid, existing and
        # not reverted
        to_delete, kept = [], list(so.get("kept", []))
        folded = []
        for item in so.get("folded", []):
            f = item.get("file", "").lstrip("./")
            dests = [d.lstrip("./") for d in item.get("destinations", [])]
            if f not in s["batch"]:
                warnings.append(_("the model says it consolidated `{file}`, but it is not in this batch; ignored", file=f))
                continue
            bad_dest = [d for d in dests if not (d == "STATE.md" or REF_RE.match(d)) or not (repo / d).exists()]
            why = None
            if reverted & set(dests):
                why = _("destination {files} was reverted", files=", ".join(sorted(reverted & set(dests))))
            elif bad_dest:
                why = _("destination not allowed or missing: {files}", files=", ".join(bad_dest))
            elif not dests and not item.get("dropped", "").strip():
                why = _("no destination and no reason for not keeping it")
            if why:
                kept.append({"file": f, "reason": _("(blocked by the script) {why}", why=why)})
            else:
                to_delete.append(f)
                folded.append({**item, "file": f, "destinations": dests})
        for f in to_delete:
            git("rm", "-q", "--", f, cwd=repo)

        missing = salient_missing(repo, base, to_delete)
        for f, toks in missing.items():
            warnings.append(_("strings in `{file}` not found after consolidating (maybe rewritten or lost, for spot "
                              "checks): ", file=f) + _(", ").join(f"`{t}`" for t in toks))
        curated = {"STATE.md": (repo / "STATE.md").read_text() if (repo / "STATE.md").exists() else ""}
        for p in (repo / "memory" / "reference").glob("*.md"):
            curated[str(p.relative_to(repo))] = p.read_text()
        for f in to_delete:
            where = [k for k, text in curated.items() if pathlib.PurePath(f).name in text]
            if where:
                warnings.append((_("`{files}` still refers to the deleted `{file}` (the librarian's broken-link check "
                                   "takes over)", files=", ".join(where), file=f) if len(where) == 1 else
                                 _("`{files}` still refer to the deleted `{file}` (the librarian's broken-link check "
                                   "takes over)", files=", ".join(where), file=f)))
        refs = sorted(p.name for p in (repo / "memory" / "reference").glob("*.md") if p.name != "README.md")
        readme = repo / "memory" / "reference" / "README.md"
        unindexed = [r for r in refs if r not in (readme.read_text() if readme.exists() else "")]
        if unindexed:
            warnings.append(_("memory/reference/README.md does not list: ") + _(", ").join(unindexed))

        git("add", "-A", "--", "STATE.md", "memory/reference", cwd=repo)
        if not git("status", "--porcelain", cwd=repo).strip():
            log(_("{project}: nothing to commit after the checks", project=name)
                + (_(" ({n} {n:warning|warnings})", n=len(warnings)) if warnings else ""))
            for w in warnings:
                log(f"{name}:   {w}")
            result["status"] = "noop"
            return result

        content_diff = git("diff", "--cached", "--no-color", "--", "STATE.md", "memory/reference", cwd=repo)
        stat = git("diff", "--cached", "--stat", cwd=repo)
        msg = f"consolidate: fold {len(to_delete)} raw notes into reference/ ({TODAY})"
        git("commit", "-q", "-m", msg, cwd=repo)
        new_sha = git("rev-parse", "HEAD", cwd=repo).strip()

        entry = review_entry(name, new_sha, model, so.get("summary", ""), folded, kept, good, warnings,
                             content_diff, stat, dry_run)
        result["entry"], result["diff"] = entry, content_diff
        for w in warnings:
            log(f"{name}: ⚠️ {w}")

        if dry_run:
            log(_("{project}: dry-run, no push; rm {n} {n:date file|date files}, {changed} {changed:file|files} changed",
                  project=name, n=len(to_delete), changed=len(changed) - len(reverted)))
            result["status"] = "ok"
            return result

        patch = git("format-patch", "--stdout", f"{base}..HEAD", cwd=repo)
        push = subprocess.run([str(BIN / "git-safe-push.sh")], cwd=repo, env=GIT_ENV, capture_output=True, text=True)
        if push.returncode != 0:
            fdir = STATE_DIR / "consolidate" / "failed"
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / f"{name}-{TODAY}.patch").write_text(patch)
            log(_("{project}: push failed (most likely a project session changed the same part at the same time), patch "
                  "left in {dir}; the copy is untouched: {stderr}", project=name, dir=fdir, stderr=push.stderr.strip()[:300]))
            return result
        pushed = git("rev-parse", "HEAD", cwd=repo).strip()
        log(_("{project}: pushed {sha}", project=name, sha=pushed))

        copy = COPIES / name
        hublib.harden_copy(copy)
        upd = subprocess.run([str(BIN / "with-lock.sh"), "git", *hublib.NO_SYMLINKS, "pull", "-q"], cwd=copy,
                             env=GIT_ENV, capture_output=True, text=True)
        if upd.returncode != 0:
            log(_("{project}: git pull of the copy failed (the librarian pulls again next time): {stderr}", project=name,
                  stderr=upd.stderr.strip()[:200]))

        # The librarian's checkpoint sits exactly at the starting point and this was a fast-forward (no one else's
        # commit merged): advance it, otherwise the librarian reads the consolidation diff (including the full text of
        # the deleted date files) again as new content
        ckpt = STATE_DIR / f"{name}.sha"
        if pushed == new_sha and ckpt.exists() and ckpt.read_text().strip() == base:
            ckpt.write_text(new_sha + "\n")
            log(_("{project}: librarian checkpoint advanced to {sha}", project=name, sha=new_sha[:8]))
        result["status"] = "ok"
        return result
    except Exception as e:  # noqa: BLE001 — any surprise affects only this project; the copy is untouched
        log(_("{project}: unexpected error, giving up (the copy is untouched): {error_type}: {error}", project=name,
              error_type=type(e).__name__, error=e))
        return result
    finally:
        if keep:
            log(_("{project}: the temp clone is kept in {dir}", project=name, dir=repo))
        else:
            shutil.rmtree(tmp, ignore_errors=True)


def review_entry(name, sha, model, summary, folded, kept, corrections, warnings, diff, stat, dry_run) -> str:
    lines = ["## " + _("{date} · {project} · consolidation", date=TODAY, project=name)
             + (_(" (dry-run, not pushed)") if dry_run else ""), "",
             _("**commit**: `{sha}` (model {model})", sha=sha, model=model), "",
             _("**Summary**: {summary}", summary=summary or _("(not given by the model)")), ""]
    if folded:
        lines += [_("**Date files folded** (git rm; see the original with `git show {sha}^:<path>`):", sha=sha[:8]), ""]
        for f in folded:
            lines.append(f"- `{f['file']}` → {', '.join(f'`{d}`' for d in f['destinations']) or _('(none)')}"
                         + (_("; not kept: {dropped}", dropped=f["dropped"]) if f.get("dropped", "").strip() else ""))
        lines.append("")
    if kept:
        lines += [_("**Not deleted this time**:"), ""] + ["- " + _("`{file}`: {reason}", file=k.get("file"),
                                                                     reason=k.get("reason")) for k in kept] + [""]
    if corrections:
        lines += [_("**Statements corrected** (overturned by newer notes):"), ""]
        for c in corrections:
            lines.append("- " + _("`{file}`: “{before}” → “{after}” (per `{evidence_file}`: “{evidence_quote}”)",
                                  file=c["file"], before=c["before"], after=c["after"], evidence_file=c["evidence_file"],
                                  evidence_quote=c["evidence_quote"]))
        lines.append("")
    if warnings:
        lines += [_("**Mechanical checks**:"), ""] + [f"- ⚠️ {w}" for w in warnings] + [""]
    if len(diff) > MAX_LOG_DIFF:
        diff = diff[:MAX_LOG_DIFF] + "\n" + _("… (truncated; the full diff: `git -C ~/.memory-copies/{project} show {sha}`)",
                                              project=name, sha=sha[:8])
    lines += ["```", stat.rstrip(), "```", "", "```diff", diff.rstrip(), "```", "", "---", ""]
    return "\n".join(lines) + "\n"


# ---------- main ----------

def main() -> int:
    global _log_file
    # the description is the docstring's first line (its own msgid: the docstring is translated in step 4)
    ap = argparse.ArgumentParser(description=_("Consolidation: fold each project's long-kept memory date files into "
                                               "reference/, and fix statements that newer notes overturned"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--project")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true", help=_("keep the temp clone for inspection"))
    ap.add_argument("--max", type=int, default=MAX_PROJECTS)
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not args.list:
        _log_file = LOG_DIR / f"consolidate-memory-{TODAY}.log"
    state = load_state()
    cands = candidates(state, args.project, ignore_interval=bool(args.project))

    if args.list:
        for s in cands:
            print(_("{name:28} date files {raw:3}  over {days} days {old:3}  last ok {last_ok:10}  {status}",
                    name=s["name"], raw=len(s["raw"]), days=OLDER_THAN_DAYS, old=len(s["old"]), last_ok=s["last_ok"] or "-",
                    status=s.get("skip") or _("due ({n} this time)", n=len(s["batch"]))))
        return 0

    import llm
    model = llm.model_for("consolidate")   # computed only there, platform-wide (hub.json llm.points can override)
    due = [s for s in cands if not s.get("skip")]
    for s in cands:
        if s.get("skip") and args.project:
            log(_("{project}: skipped: {reason}", project=s["name"], reason=s["skip"]))
    if args.dry_run and not args.project:
        due = due[:1]
    log(_("=== consolidate start ({dry_run}{n} due, at most {max} this time) ===", dry_run="dry-run, " if args.dry_run else "",
          n=len(due), max=args.max))

    start, failed, entries = time.monotonic(), 0, []
    for s in due[: args.max]:
        if time.monotonic() - start > BUDGET_MINUTES * 60:
            log(_("over {minutes} minutes; the rest runs tomorrow", minutes=BUDGET_MINUTES))
            break
        r = consolidate(s, model, args.dry_run, args.keep)
        if args.dry_run:
            if r["entry"]:
                ddir = STATE_DIR / "consolidate" / "dry-run"
                ddir.mkdir(parents=True, exist_ok=True)
                out = ddir / f"{s['name']}-{TODAY}.md"
                out.write_text(r["entry"])
                print(r["entry"])
                log(_("{project}: the proposed review-log entry is in {file}", project=s["name"], file=out))
            continue
        st = state.setdefault(s["name"], {})
        st["last_try"] = str(TODAY)
        if r["status"] in ("ok", "noop"):
            st["last_ok"] = str(TODAY)
            if r["entry"]:
                entries.append(r["entry"])
        else:
            failed += 1
        save_state(state)

    if entries:
        subprocess.run(["git", "fetch", "-q"], cwd=HUB, env=GIT_ENV, capture_output=True)   # agent-hub's main moves only through merge-branch (commit_gate): fetch, never pull
        with open(REVIEW_LOG, "a") as f:
            f.writelines(entries)
        c = subprocess.run([str(BIN / "commit-hub-files.sh"), f"consolidate: review-log {TODAY}", "review-log.md"],
                           env=GIT_ENV, capture_output=True, text=True)
        log("review-log.md " + (_("committed and pushed") if c.returncode == 0 else
                                _("commit/push failed: {output}", output=f"{c.stdout}{c.stderr}")))
    log(_("=== consolidate end ({n} failed) ===", n=failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
