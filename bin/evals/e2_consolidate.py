#!/bin/sh -c exec${IFS}"${0%/*}/../py"${IFS}"$0"${IFS}"$@"
"""E2 for the judgment point `consolidate` (docs/design/i18n.md): an agent folding a memory repo's old date files into
memory/reference/ and correcting overturned statements. Called from e2.py (freeze / run / score consolidate).

Cases (cases/consolidate/<today>/<project>/): `repo/`, a clone of the project's memory bare repo (branch agent-memory)
at a fixed commit with its history (build_prompt reads `git log` dates; the checks diff against that commit) and no
remote; args.json: the commit, the pinned TODAY, the owner tag and the survey consolidate computes for that day
(batch, raw, …); prompt.consolidate.txt: the prompt production would send (the `old` arm, rendered at freeze time).

A run copies the case repo to a temp dir laid out like production's (consolidate-<project>-…/repo), calls
`llm.py run consolidate --prompt-file` there with the run's own config/state/bus (e2.run_env) and production's git
environment, then keeps the repo (the model's changes uncommitted) next to result.json for scoring.

Scored with consolidate-memory.py's own checks, in its order, on a scratch copy of the kept repo (stray files,
verify_corrections, hedge_upgrades, state_todo_broken, the folded/kept validation, salient_missing, README index),
plus: valid structured output, the folded / kept sets and the label each batch file gets (agreement between runs),
destinations, changed and new files, corrections; the language of what was written: lines added to an existing
reference file against that file's own language (the prompt says keep it), new files / STATE.md / the free-text answer
fields against the content language, no Simplified characters.
"""
from __future__ import annotations

import concurrent.futures
import datetime
import difflib
import importlib.util
import itertools
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

EVALS = pathlib.Path(__file__).resolve().parent
BIN = EVALS.parent
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(EVALS))
import e2  # noqa: E402
import hubconf  # noqa: E402

POINT = "consolidate"
# no call may be running during the nightly jobs (consolidate 02:00, librarian 03:00); a call takes up to 20 minutes
QUIET = (datetime.time(1, 25), datetime.time(4, 0))
LABELS = ("folded", "kept", "none")


def cm():
    """consolidate-memory.py as it is now (its checks; build_prompt for the new arms)"""
    spec = importlib.util.spec_from_file_location("consolidate_e2", BIN / "consolidate-memory.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo: pathlib.Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=check,
                          env={**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
                               "GIT_CONFIG_VALUE_0": "/dev/null"}).stdout


# ---------- cases ----------

def freeze(project: str, today: str | None, rev: str | None = None) -> pathlib.Path:
    """a case: the project's memory bare repo cloned at `rev` (default the agent-memory tip), the survey at `today`"""
    m = cm()
    origin = hubconf.p("git_origin", "~/git-origin") / f"{project}-memory.git"
    if not origin.is_dir():
        sys.exit(f"e2: no memory bare repo for {project}")
    day = datetime.date.fromisoformat(today) if today else datetime.date.today()
    c = e2.root() / "cases" / POINT / str(day) / project
    if c.exists():
        shutil.rmtree(c)
    c.mkdir(parents=True)
    repo = c / "repo"
    subprocess.run(["git", "clone", "-q", "--branch", "agent-memory", str(origin), str(repo)], check=True)
    if rev:
        git(repo, "reset", "-q", "--hard", rev)
    git(repo, "remote", "remove", "origin")          # nothing in a run can push anywhere
    m.TODAY = day
    s = m.survey(project, str(repo / ".git"))
    if not s["batch"]:
        shutil.rmtree(c)
        sys.exit(f"e2: {project} has no date files older than {m.OLDER_THAN_DAYS} days at {day}")
    base = git(repo, "rev-parse", "HEAD").strip()
    args = {"project": project, "today": str(day), "base": base, "owner": hubconf.owner_tag(),
            "assistant": hubconf.assistant_name(), "survey": {k: v for k, v in s.items() if k != "origin"}}
    (c / "args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1))
    (c / f"prompt.{POINT}.txt").write_text(m.build_prompt(s, repo))
    return c


def survey(case: pathlib.Path, repo: pathlib.Path) -> dict:
    a = json.loads((case / "args.json").read_text())
    return {**a["survey"], "origin": str(repo / ".git")}


def prompt(case: pathlib.Path, arm: str) -> str:
    if e2.base_arm(arm) == "old":
        return (case / f"prompt.{POINT}.txt").read_text()
    a = json.loads((case / "args.json").read_text())
    m = cm()
    m.TODAY = datetime.date.fromisoformat(a["today"])
    lang = "en" if e2.base_arm(arm) == "new-en" else "zh-Hant"
    return m.build_prompt(survey(case, case / "repo"), case / "repo", owner=a["owner"], lang=lang)


# ---------- runs ----------

def quiet_wait() -> None:
    """sleep through the nightly jobs' window before starting a call"""
    now = datetime.datetime.now()
    if QUIET[0] <= now.time() < QUIET[1]:
        until = datetime.datetime.combine(now.date(), QUIET[1])
        print(f"e2: {now:%H:%M} is in the nightly jobs' window, waiting until {until:%H:%M}", flush=True)
        time.sleep((until - now).total_seconds())


def one(case: pathlib.Path, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / case.parent.name / case.name / f"{arm}-{i}"
    if (out / "result.json").exists():
        return json.loads((out / "meta.json").read_text())
    quiet_wait()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "prompt.txt").write_text(prompt(case, arm))
    tmp = pathlib.Path(tempfile.mkdtemp(prefix=f"consolidate-{case.name}-"))
    try:
        repo = tmp / "repo"
        shutil.copytree(case / "repo", repo, symlinks=True)
        t0 = time.monotonic()
        try:
            r = subprocess.run([str(BIN / "py"), str(BIN / "llm.py"), "run", POINT, "--prompt-file",
                                str(out / "prompt.txt")], cwd=repo, env=env, capture_output=True, text=True,
                               timeout=25 * 60)
            stdout, stderr, rc = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as e:
            stdout, stderr, rc = "", f"timeout: {e}", -1
        secs = time.monotonic() - t0
        shutil.move(str(repo), out / "repo")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    (out / "result.json").write_text(stdout)
    (out / "stderr.txt").write_text(stderr[-20000:])
    try:
        obj = json.loads(stdout)
    except ValueError:
        obj = {}
    meta = {"case": e2.case_id(case), "arm": arm, "i": i, "exit": rc, "secs": round(secs, 1),
            "model": obj.get("llm_model") or obj.get("model"), "error": obj.get("llm_error"),
            "turns": obj.get("num_turns"), "denials": len(obj.get("permission_denials") or []),
            "cost": obj.get("total_cost_usd"),
            "rate_limited": bool(e2.RATE_LIMIT.search((obj.get("llm_error") or "") + stderr[-2000:])) and rc != 0}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def cmd_run(a) -> int:
    cs = e2.cases(POINT, a.case)
    if not cs:
        sys.exit(f"e2: no cases for {POINT} matching {a.case}")
    run_id = a.run or f"{datetime.datetime.now():%Y%m%d-%H%M}-{POINT}"
    run_dir = e2.root() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"point": POINT, "code": git(hubconf.ROOT, "rev-parse", "HEAD").strip()},
                                                 indent=1))
    env = {**e2.run_env(run_dir), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath",
           "GIT_CONFIG_VALUE_0": "/dev/null"}   # what consolidate-memory.py's GIT_ENV adds
    arm = a.arm
    jobs = [(c, arm, i) for i in range(a.k) for c in cs]
    print(f"e2: {POINT} arm={arm} k={a.k} cases={len(cs)} → {run_dir}", flush=True)
    stop = False
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        futs = {pool.submit(one, c, arm, i, run_dir, env): (c, i) for c, arm, i in jobs}
        for f in concurrent.futures.as_completed(futs):
            if f.cancelled():
                continue
            m = f.result()
            print(f"  {m['case']} {m['arm']}-{m['i']}: exit {m['exit']} {m['secs']}s turns={m['turns']} "
                  f"denials={m['denials']} {m['error'] or ''}", flush=True)
            if m["rate_limited"] and not stop:
                stop = True
                print("e2: rate limited, cancelling the rest (rerun the same command to continue)", flush=True)
                for g in futs:
                    g.cancel()
    print(run_id)
    return 1 if stop else 0


# ---------- scoring ----------

def checks(m, case: pathlib.Path, out: pathlib.Path) -> dict:
    """consolidate()'s checks after the model call, in its order, on a scratch copy of the run's repo"""
    a = json.loads((case / "args.json").read_text())
    s, base = a["survey"], a["base"]
    try:
        obj = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        obj = {}
    so = obj.get("structured_output")
    r = {"valid": isinstance(so, dict) and not obj.get("is_error") and not obj.get("llm_error"),
         "error": obj.get("llm_error"), "denials": len(obj.get("permission_denials") or [])}
    if not r["valid"] or not (out / "repo").is_dir():
        return r
    with tempfile.TemporaryDirectory(prefix="e2-score-") as tmp:
        repo = pathlib.Path(tmp) / "repo"
        shutil.copytree(out / "repo", repo, symlinks=True)
        r["head_moved"] = git(repo, "rev-parse", "HEAD").strip() != base
        changed, stray = [], []
        for line in git(repo, "status", "--porcelain", "--untracked-files=all").splitlines():
            path = line[3:].strip().strip('"')
            (changed if path == "STATE.md" or m.REF_RE.match(path) else stray).append(path)
        r["changed"], r["stray"] = sorted(changed), stray
        r["new_files"] = sorted(f for f in changed if m.at_base(repo, base, f) is None)
        r["lang"] = written_language(m, repo, base, changed, so)   # what the model wrote, before any revert
        r["diff"] = git(repo, "diff", "--no-color", base, "--", "STATE.md", "memory/reference") + "".join(
            f"\n+++ {f}\n{(repo / f).read_text(errors='replace')}" for f in r["new_files"])
        reverted: set[str] = set()
        good, bad = m.verify_corrections(repo, base, so.get("corrections", []))
        for c in bad:
            f = c.get("file", "").lstrip("./")
            if f in changed and f not in reverted:
                m.revert(repo, base, f)
                reverted.add(f)
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]
        hedges = m.hedge_upgrades(repo, base, [c for c in changed if c not in reverted], s["batch"], good)
        for f in hedges:
            m.revert(repo, base, f)
            reverted.add(f)
        todo_broken = "STATE.md" in changed and "STATE.md" not in reverted and m.state_todo_broken(repo, base)
        if todo_broken:
            m.revert(repo, base, "STATE.md")
            reverted.add("STATE.md")
        good = [c for c in good if c.get("file", "").lstrip("./") not in reverted]
        to_delete, kept, blocked, dests = [], [k.get("file", "").lstrip("./") for k in so.get("kept", [])], [], set()
        declared = []
        for item in so.get("folded", []):
            f = item.get("file", "").lstrip("./")
            ds = [d.lstrip("./") for d in item.get("destinations", [])]
            declared.append(f)
            if f not in s["batch"]:
                continue
            bad_dest = [d for d in ds if not (d == "STATE.md" or m.REF_RE.match(d)) or not (repo / d).exists()]
            if (reverted & set(ds)) or bad_dest or (not ds and not item.get("dropped", "").strip()):
                blocked.append(f)
                kept.append(f)
            else:
                to_delete.append(f)
                dests |= set(ds)
        missing = m.salient_missing(repo, base, to_delete)
        refs = sorted(p.name for p in (repo / "memory" / "reference").glob("*.md") if p.name != "README.md")
        readme = repo / "memory" / "reference" / "README.md"
        unindexed = [x for x in refs if x not in (readme.read_text() if readme.exists() else "")]
        r.update({"declared_folded": sorted(declared), "folded": sorted(to_delete), "kept": sorted(set(kept)),
                  "blocked": blocked, "destinations": sorted(dests), "corrections_good": len(good),
                  "corrections_bad": len(bad), "hedge_reverts": sorted(hedges), "todo_broken": todo_broken,
                  "reverted": sorted(reverted), "salient_missing": sum(len(v) for v in missing.values()),
                  "unindexed": unindexed,
                  "labels": {f: ("folded" if f in to_delete else "kept" if f in kept else "none") for f in s["batch"]}})
        r["contract_ok"] = not (stray or r["head_moved"] or reverted or blocked)
    return r


def written_language(m, repo: pathlib.Path, base: str, changed: list[str], so: dict) -> dict:
    """lines added to existing reference files vs each file's own language; new files, STATE.md and the free-text
    answer fields vs the content language"""
    ref_pairs, content, refs = [], [], []
    for f in changed:
        _removed, added = m.diff_lines(repo, base, f)
        text = "\n".join(added)
        before = m.at_base(repo, base, f)
        if f == "STATE.md" or before is None:
            content.append(text)
        else:
            refs.append(text)
            ref_pairs.append((e2.language(before)["cjk_share"], e2.language(text)["cjk_share"]))
    free = [str(so.get("summary") or "")] + [str(x.get("dropped") or "") for x in so.get("folded", [])] + \
        [str(x.get("reason") or "") for x in so.get("kept", [])]
    written = "\n".join(content)
    everything = "\n".join([written, *refs, *free, *(str(c.get("after") or "") for c in so.get("corrections", []))])
    return {"content": e2.language(written), "free": e2.language("\n".join(free)),
            "ref_pairs": [(b, x) for b, x in ref_pairs if b is not None and x is not None],
            "all": e2.language(everything)}


def agree(x: dict, y: dict) -> dict:
    if "labels" not in x or "labels" not in y:
        return {}
    jac = (lambda p, q: len(set(p) & set(q)) / len(set(p) | set(q)) if set(p) | set(q) else 1.0)
    files = x["labels"].keys()
    return {"labels": sum(x["labels"][f] == y["labels"][f] for f in files) / len(files),
            "folded": jac(x["folded"], y["folded"]), "kept": jac(x["kept"], y["kept"]),
            "destinations": jac(x["destinations"], y["destinations"]), "changed": jac(x["changed"], y["changed"]),
            "new_files_diff": abs(len(x["new_files"]) - len(y["new_files"])),
            "corrections_diff": abs(x["corrections_good"] - y["corrections_good"]),
            "text_sim": round(difflib.SequenceMatcher(None, x["diff"][:20000], y["diff"][:20000],
                                                      autojunk=False).quick_ratio(), 3)}


METRICS = ("labels", "folded", "kept", "destinations", "changed", "new_files_diff", "corrections_diff", "text_sim")


def ref_keeps_language(pair: tuple[float, float]) -> bool:
    """an existing reference file's additions in its own language: both mostly Chinese or both mostly not (a file's
    Chinese share is diluted by code and paths, so the line sits low)"""
    base, added = pair
    return (base >= 0.15) == (added >= 0.15)


def cmd_score(a) -> int:
    m = cm()
    run_dir = e2.root() / "runs" / a.run
    by: dict[str, dict[str, list[dict]]] = {}
    for out in sorted(run_dir.glob("*/*/*-*")):
        if not (out / "result.json").exists():
            continue
        cid, arm = f"{out.parent.parent.name}/{out.parent.name}", out.name.rsplit("-", 1)[0]
        case = e2.root() / "cases" / POINT / cid
        r = checks(m, case, out)
        r["secs"] = json.loads((out / "meta.json").read_text()).get("secs")
        by.setdefault(cid, {}).setdefault(arm, []).append(r)
    report = {"run": a.run, "point": POINT, "arms": {}, "pairs": {}, "cases": {}}
    arms = sorted({arm for v in by.values() for arm in v})
    mean = e2.mean
    for arm in arms:
        rs = [r for v in by.values() for r in v.get(arm, [])]
        ok = [r for r in rs if "labels" in r]
        langs = [r["lang"] for r in ok]
        pairs = [p for g in langs for p in g["ref_pairs"]]
        report["arms"][arm] = {
            "runs": len(rs), "valid": mean([r["valid"] for r in rs]),
            "contract_ok": mean([r["contract_ok"] for r in ok]),
            "stray": sum(bool(r["stray"]) for r in ok), "hedge_reverts": sum(bool(r["hedge_reverts"]) for r in ok),
            "corrections_bad": sum(r["corrections_bad"] for r in ok), "todo_broken": sum(r["todo_broken"] for r in ok),
            "blocked": sum(len(r["blocked"]) for r in ok),
            "folded_share": mean([sum(v == "folded" for v in r["labels"].values()) / len(r["labels"]) for r in ok]),
            "corrections": mean([r["corrections_good"] for r in ok]), "new_files": mean([len(r["new_files"]) for r in ok]),
            "salient_missing": mean([r["salient_missing"] for r in ok]),
            "unindexed_runs": sum(bool(r["unindexed"]) for r in ok), "denials": sum(r["denials"] for r in rs),
            "content_cjk": mean([g["content"]["cjk_share"] for g in langs]),
            "free_cjk": mean([g["free"]["cjk_share"] for g in langs]),
            "free_cjk_min": min((g["free"]["cjk_share"] for g in langs if g["free"]["cjk_share"] is not None),
                                default=None),
            "ref_lang_kept": mean([ref_keeps_language(p) for p in pairs]), "ref_files": len(pairs),
            "simplified_runs": sum(bool(g["all"]["simplified"]) for g in langs),
            "simplified_chars": sorted({c for g in langs for c in g["all"]["simplified"]}),
            "full_punct": mean([g["all"]["full_punct"] for g in langs]),
            "secs": mean([r["secs"] for r in rs]),
            "errors": sorted({str(r["error"])[:120] for r in rs if r.get("error")})[:5]}
    for x, y in [(p, p) for p in arms] + list(itertools.combinations(arms, 2)):
        rows = []
        for v in by.values():
            xs, ys = v.get(x, []), v.get(y, [])
            pairs = itertools.combinations(xs, 2) if x == y else itertools.product(xs, ys)
            rows += [agree(p, q) for p, q in pairs]
        rows = [r for r in rows if r]
        report["pairs"][f"{x}~{y}"] = {k: {"mean": mean([r[k] for r in rows]), "ci90": e2.boot([r[k] for r in rows])}
                                       for k in METRICS} | {"n": len(rows)}
    for cid, v in by.items():   # per case: what each run decided, to read where a score drops
        report["cases"][cid] = {arm: [{"labels": r.get("labels"), "dest": r.get("destinations"),
                                       "new": r.get("new_files"), "corr": r.get("corrections_good"),
                                       "reverted": r.get("reverted"), "valid": r["valid"]} for r in rs]
                                for arm, rs in v.items()}
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"{a.run} ({POINT})")
        for arm, st in report["arms"].items():
            print(f"  {arm}: " + ", ".join(f"{k}={v}" for k, v in st.items()))
        for k, st in report["pairs"].items():
            print(f"  {k}: n={st['n']} " + ", ".join(f"{mt}={st[mt]['mean']} {st[mt]['ci90'] or ''}" for mt in METRICS))
    (run_dir / "score.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0
