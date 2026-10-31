#!/bin/sh -c exec${IFS}"${0%/*}/../py"${IFS}"$0"${IFS}"$@"
"""E2 of docs/design/i18n.md: run a judgment point's prompt versions on frozen inputs and score them mechanically.

Cases live in the eval dir (hubconf.evals_dir(): cases/<point>/<date>/<name>/), recorded each night by librarian.py or
frozen here from history. A run calls the point through bin/llm.py exactly as production does, but with this run's own
state dir and bus (a copy of the config with bus_dir changed), so eval calls never reach the real bus or status page.

    e2.py freeze librarian-state <project> [--since <rev> | --night N]   a case from the memory repo (see freeze_state)
    e2.py freeze librarian-index <YYYY-MM-DD>                a case from that night's digest (see freeze_index)
    e2.py freeze librarian-todo-format <state case> --kind K [--state-file F]   a broken STATE.md (see freeze_todo)
    e2.py freeze consolidate <project> [--today D] [--since <rev>]   a memory repo clone + survey (bin/evals/e2_consolidate.py)
    e2.py cases [<point>]                                    list cases
    e2.py run <point> --arm old|new|new-en [--rules old|new] [--k 3] [--case <glob>] [--run <id>] [--jobs 3]
    e2.py score <run> [--json]                               per-arm validity, language, agreement vs the noise floor

Arms: `old` = the prompt file recorded with the case (byte-exact what production sent); `new` = re-rendered from the
case's raw arguments with the code as it is now, content language zh-Hant (before translation, new == old: a check of
the renderer itself); `new-en` = the same with content language en (scored on validity and language only).
--rules new (the default for new arms) swaps the snapshot's AGENT.md for templates/AGENT.md as it is now, which is what
production reads after the rules change; --rules old keeps the snapshot's, to tell a prompt regression from a rules
one. The arm's label then says so: `new~oldrules`.
Points: librarian-state (agent: runs on a fresh copy of the snapshot, scored on STATE.md after), librarian-state-edits
(completion: edits applied to the snapshot's STATE.md). Others are added as their cases exist.
A point whose shape differs keeps its freeze / render / run / score code in its own module (PLUGINS: e2_<name>.py with
freeze(a) → the case dirs, prompt(case, arm), one(case, arm, i, run_dir, env), report(run_dir) → arms and pairs, and
cmd_run(a) when its runs need more than e2's loop); e2.py only dispatches to it.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import difflib
import fnmatch
import importlib.util
import itertools
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BIN))
import edits  # noqa: E402
import hubconf  # noqa: E402

AGENT_POINTS = {"librarian-state", "librarian-index", "librarian-todo-format"}
POINT_CASES = {"librarian-state": "librarian-state", "librarian-state-edits": "librarian-state",
               "librarian-index": "librarian-index", "librarian-index-edits": "librarian-index",
               "librarian-todo-format": "librarian-todo-format", "consolidate": "consolidate"}
# points whose freeze / run / score live in their own module bin/evals/e2_<point>.py (their shapes differ)
PLUGINS = {"consolidate": "e2_consolidate", "reflect-week": "e2_reflect"}
# The code before step 5 (docs/design/i18n.md): renders the `old` arm of a case frozen after the prompts changed
OLD_REF = "ebd7f90"
ROSTER = re.compile(r"<!-- roster:begin.*?<!-- roster:end -->", re.S)
INDEX_MD, DIGEST_DIR = "index.md", "digest"   # the layout of an index run's temp data dir (what the agent sees)
RATE_LIMIT = re.compile(r"rate.?limit|usage limit|429|overloaded", re.I)

HUB_JSON = hubconf.config_file("hub.json").name   # the file name, inside a run's config copy


def plugin(point: str):
    """the module of a point with its own E2 code (bin/evals/e2_<name>.py), or None"""
    if point not in PLUGINS:
        return None
    sys.modules.setdefault("e2", sys.modules[__name__])   # run as a script: the plugin's `import e2` is this module
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    return importlib.import_module(PLUGINS[point])


def _load(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, BIN / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def librarian():
    return _load("librarian_e2", "librarian.py")


def old_librarian():
    """librarian.py as it was at OLD_REF (the Chinese prompts), loaded from a temp file; its imports resolve to bin/."""
    src = subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{OLD_REF}:bin/librarian.py"], capture_output=True,
                         text=True, check=True).stdout
    f = pathlib.Path(tempfile.mkdtemp(prefix="e2-old-")) / "librarian.py"
    f.write_text(src)
    spec = importlib.util.spec_from_file_location("librarian_old_e2", f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def root() -> pathlib.Path:
    d = hubconf.evals_dir()
    if d is None:
        sys.exit("e2: no eval dir (set hub.json evals_dir)")
    return d


# ---------- cases ----------

def cases(point: str, pattern: str = "*") -> list[pathlib.Path]:
    base = root() / "cases" / POINT_CASES.get(point, point)
    found = sorted(p for p in base.glob("*/*") if p.is_dir() and (p / "args.json").exists())
    return [p for p in found if fnmatch.fnmatch(f"{p.parent.name}/{p.name}", pattern)]


def case_id(c: pathlib.Path) -> str:
    return f"{c.parent.name}/{c.name}"


def freeze_state(project: str, since: str | None, night: int | None = None) -> pathlib.Path:
    """a case from the memory repo, in a temp clone (the librarian's own copy is not touched).
    Default: HEAD, range from the librarian's checkpoint (what tonight's run would see). night=N: the Nth most recent
    night the librarian changed STATE.md: the input is the commit before its commit, the range starts after the
    previous librarian/registrar commit, and what it wrote is kept as that night's outcome."""
    lib = librarian()
    copy = hubconf.p("memory_copies", "~/.memory-copies") / project
    if not (copy / ".git").is_dir():
        sys.exit(f"e2: no memory copy for {project}")
    origin = lib.git(copy, "remote", "get-url", "origin")
    if since is None:
        ckpt = hubconf.STATE / f"{project}.sha"
        since = ckpt.read_text().strip() if ckpt.exists() else "HEAD~3"
    with tempfile.TemporaryDirectory(prefix="e2-freeze-") as tmp:
        d = pathlib.Path(tmp) / project
        subprocess.run(["git", "clone", "-q", origin, str(d)], check=True)
        after = None
        if night:
            log = lib.git(d, "log", "--format=%H %s").splitlines()
            ups = [i for i, l in enumerate(log) if l.split(" ", 1)[1] == "librarian: update STATE.md"]
            if len(ups) < night:
                sys.exit(f"e2: {project} has only {len(ups)} librarian updates")
            i = ups[night - 1]
            target = log[i].split()[0]
            prev = next((l.split()[0] for l in log[i + 1:] if l.split(" ", 1)[1].startswith(("librarian:", "registrar:"))),
                        None)
            since = prev or lib.git(d, "rev-list", "--max-parents=0", "HEAD")
            after = lib.git(d, "show", f"{target}:STATE.md")
            subprocess.run(["git", "-C", str(d), "checkout", "-q", f"{target}^"], check=True)
        return _freeze_in(lib, project, d, since, after)


def freeze_index(date: str) -> pathlib.Path:
    """a librarian-index case: the latest recorded index.md with the digest of night `date`. The data dir keeps every
    night's digest but not index.md's history, so these cases pair one index.md with several nights' digests. The `old`
    prompt is rendered by the code at OLD_REF."""
    rec = sorted(p for p in (root() / "cases" / "librarian-index").glob("2*/index") if (p / "args.json").exists())
    if not rec:
        sys.exit("e2: no recorded librarian-index case to take index.md from")
    base = json.loads((rec[-1] / "args.json").read_text())
    digest_f = hubconf.data_file("digest") / f"{date}.md"
    if not digest_f.exists():
        sys.exit(f"e2: no digest for {date}")
    c = root() / "cases" / "librarian-index" / "frozen" / date
    c.mkdir(parents=True, exist_ok=True)
    args = {**base, "date": date, "digest": digest_f.read_text(), "frozen_from": f"index.md of {rec[-1].parent.name}"}
    (c / "args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1))
    old = old_librarian()
    old.TODAY, old.OWNER, old.ASSISTANT = date, base["owner"], base["assistant"]
    (c / "prompt.librarian-index.txt").write_text(old.index_prompt())
    (c / "prompt.librarian-index-edits.txt").write_text(old.index_edits_prompt(base["index_before"], args["digest"]))
    return c


TODO_HEADING = "## \u5f85\u8fa6"   # the zh-Hant to-do heading (contracts/state-todo.v1.md)


def _broken(kind: str, text: str) -> str:
    """a STATE.md with one typical to-do format problem (made from a real one)"""
    lines = text.split("\n")
    tagged = [i for i, line in enumerate(lines) if re.match(r"- \[ \] \u3014", line)]
    if kind == "untagged":         # owner tags left off
        for i in tagged[:2]:
            lines[i] = re.sub(r"^- \[ \] \u3014[^\u3015]*\u3015", "- [ ] ", lines[i])
    elif kind == "done-left":      # a finished item ticked instead of removed
        lines[tagged[0]] = lines[tagged[0]].replace("- [ ]", "- [x]", 1)
    elif kind == "no-section":     # the to-dos kept under another heading
        lines = ["## \u4e0b\u4e00\u6b65" if line.strip() == TODO_HEADING else line for line in lines]
    return "\n".join(lines)


def freeze_todo(kind: str, source: str, state_file: str | None = None) -> pathlib.Path:
    """a librarian-todo-format case: the snapshot of librarian-state case `source` with a broken STATE.md, either made
    (kind untagged / done-left / no-section) or a real output that failed the check (state_file, kind real-<name>).
    The problems are what check-state-todo.py reports; the `old` prompt is rendered by the code at OLD_REF."""
    src = root() / "cases" / "librarian-state" / source
    a = json.loads((src / "args.json").read_text())
    broken = pathlib.Path(state_file).read_text() if state_file else _broken(kind, (src / "snapshot" / "STATE.md").read_text())
    c = root() / "cases" / "librarian-todo-format" / kind / src.name
    if c.exists():
        shutil.rmtree(c)
    shutil.copytree(src / "snapshot", c / "snapshot")
    (c / "snapshot" / "STATE.md").write_text(broken)
    chk = subprocess.run([*hubconf.launch(BIN / "check-state-todo.py"), str(c / "snapshot" / "STATE.md")],
                         capture_output=True, text=True)
    if chk.returncode == 0:
        shutil.rmtree(c)
        sys.exit(f"e2: {kind} of {source} passes the check; not a case")
    args = {k: a[k] for k in ("date", "owner", "assistant", "project")} | {"problems": chk.stdout.strip(), "kind": kind,
                                                                          "from": source}
    (c / "args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1))
    old = old_librarian()
    old.TODAY, old.OWNER, old.ASSISTANT = a["date"], a["owner"], a["assistant"]
    (c / "prompt.librarian-todo-format.txt").write_text(old.todo_fix_prompt(args["problems"]))
    return c


def _freeze_in(lib, project: str, d: pathlib.Path, since: str, after: str | None = None) -> pathlib.Path:
    head = lib.git(d, "rev-parse", "HEAD")
    base = lib.git(d, "rev-parse", since)
    rng = f"{base}..{head}"
    names = lib.git(d, "log", "--name-only", "--pretty=format:", rng, "--", "memory/")
    changed = "\n".join(sorted({n for n in names.split("\n") if n}))
    if not changed:
        sys.exit(f"e2: no memory/ changes in {since}..HEAD")
    memory_section, diff, truncated = lib.memory_input(d, rng, changed)
    dangling = lib.dangling_refs(d)
    before = (d / "STATE.md").read_text(errors="replace") if (d / "STATE.md").exists() else ""
    raw = {"changed": changed, "diff": diff, "truncated": truncated, "handoffs": [], "dangling": dangling,
           "frozen_from": rng}
    rendered = {"memory_section": memory_section, "h_note": "", "d_note": lib.dangling_note(dangling)}
    c = lib.record_state_case(project, d, head, before, raw, rendered, name=f"{project}@{head[:8]}")
    if c is None:
        sys.exit("e2: case not recorded (see the librarian log)")
    if after is not None:
        lib.record_outcome(c, {"changed": after != before, "from": "history"}, {"STATE.md": after})
    return c


# ---------- prompts ----------

def base_arm(arm: str) -> str:
    return arm.split("~")[0]


def new_rules(arm: str) -> bool:
    return base_arm(arm) != "old" and not arm.endswith("~oldrules")


def snapshot(case: pathlib.Path, arm: str, into: pathlib.Path) -> pathlib.Path:
    """A copy of the case's snapshot as this arm's agent sees it (the current AGENT.md template under the new rules)."""
    shutil.copytree(case / "snapshot", into)
    if new_rules(arm):
        shutil.copy(hubconf.ROOT / "templates" / "AGENT.md", into / "AGENT.md")
    return into


def prompt(point: str, case: pathlib.Path, arm: str) -> str:
    if p := plugin(point):
        return p.prompt(case, arm)
    if arm == "old":
        return (case / f"prompt.{point}.txt").read_text()
    a = json.loads((case / "args.json").read_text())
    lib = librarian()
    lib.TODAY, lib.OWNER, lib.ASSISTANT = a["date"], a["owner"], a["assistant"]   # pin what the prompt interpolates
    lib.CONTENT_LANG = "en" if base_arm(arm) == "new-en" else "zh-Hant"
    if point == "librarian-index":
        return lib.index_prompt()
    if point == "librarian-todo-format":
        return lib.todo_fix_prompt(a["problems"])
    if point == "librarian-index-edits":
        return lib.index_edits_prompt(a["index_before"], a["digest"])
    if point in ("librarian-state", "librarian-state-edits"):
        ms = lib.memory_text(a["changed"], a["diff"], a["truncated"])
        hn = lib.handoff_note(a["handoffs"]) if a["handoffs"] else ""
        dn = lib.dangling_note(a["dangling"])
        if point == "librarian-state":
            return lib.state_prompt(a["project"], ms, hn, dn)
        before = (case / "snapshot" / "STATE.md").read_text(errors="replace") if (case / "snapshot" / "STATE.md").exists() else ""
        with tempfile.TemporaryDirectory(prefix="e2-snap-") as tmp:
            return lib.state_edits_prompt(a["project"], snapshot(case, arm, pathlib.Path(tmp) / "s"), before, ms, hn, dn)
    raise SystemExit(f"e2: no renderer for {point}")


# ---------- runs ----------

def run_env(run_dir: pathlib.Path) -> dict:
    """The real config, except this run's own bus and state dir (credentials stay in the real HOME)."""
    cfg = run_dir / "config"
    if not cfg.exists():
        shutil.copytree(hubconf.config_dir(), cfg, ignore=shutil.ignore_patterns(".git"))
        hub = json.loads((cfg / HUB_JSON).read_text())
        hub["bus_dir"] = str(run_dir / "bus")
        hub.pop("evals_dir", None)                    # eval calls never record cases themselves
        if isinstance(hub.get("assistant"), dict) and hub["assistant"].get("api"):
            hub["assistant"]["api"] = "http://127.0.0.1:9/eval-disabled"   # belt and braces: only the bridge posts there
        (cfg / HUB_JSON).write_text(json.dumps(hub, ensure_ascii=False, indent=2))
    return {**os.environ, "AGENT_HUB_CONFIG": str(cfg), "AGENT_HUB_STATE": str(run_dir / "state"),
            "AGENT_HUB_DATA": str(hubconf.data_dir()), "AGENT_HUB_SECRETS": str(hubconf.secrets_dir())}


def one(point: str, case: pathlib.Path, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    if p := plugin(point):
        return p.one(case, arm, i, run_dir, env)
    out = run_dir / case.parent.name / case.name / f"{arm}-{i}"
    if (out / "result.json").exists():
        return json.loads((out / "meta.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    text = prompt(point, case, arm)
    (out / "prompt.txt").write_text(text)
    with tempfile.TemporaryDirectory(prefix="e2-") as tmp:
        cwd = pathlib.Path(tmp) / case.name.split("@")[0]
        if point == "librarian-index":   # the data dir as the agent sees it: index.md and that night's digest
            a = json.loads((case / "args.json").read_text())
            (cwd / DIGEST_DIR).mkdir(parents=True)
            (cwd / INDEX_MD).write_text(a["index_before"])
            (cwd / DIGEST_DIR / f"{a['date']}.md").write_text(a["digest"])
        elif point in AGENT_POINTS:
            snapshot(case, arm, cwd)
        else:
            cwd.mkdir()
        t0 = datetime.datetime.now()
        r = subprocess.run([str(BIN / "py"), str(BIN / "llm.py"), "run", point, "--prompt-file", str(out / "prompt.txt")],
                           cwd=cwd, env=env, capture_output=True, text=True, timeout=3600)
        secs = (datetime.datetime.now() - t0).total_seconds()
        if point == "librarian-index" and (cwd / INDEX_MD).exists():
            shutil.copy(cwd / INDEX_MD, out / "after.index.md")
        elif point in AGENT_POINTS and (cwd / "STATE.md").exists():
            shutil.copy(cwd / "STATE.md", out / "after.STATE.md")
    (out / "result.json").write_text(r.stdout)
    (out / "stderr.txt").write_text(r.stderr[-20000:])
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = {}
    meta = {"case": case_id(case), "arm": arm, "i": i, "exit": r.returncode, "secs": round(secs, 1),
            "model": obj.get("llm_model") or obj.get("model"), "error": obj.get("llm_error"),
            "rate_limited": bool(RATE_LIMIT.search((obj.get("llm_error") or "") + r.stderr[-2000:])) and r.returncode != 0}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def cmd_run(a) -> int:
    cs = cases(a.point, a.case)
    if not cs:
        sys.exit(f"e2: no cases for {a.point} matching {a.case}")
    run_id = a.run or f"{datetime.datetime.now():%Y%m%d-%H%M}-{a.point}"
    run_dir = root() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"point": a.point, "code": subprocess.run(
        ["git", "-C", str(hubconf.ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()}, indent=1))
    env = run_env(run_dir)
    arm = a.arm + ("~oldrules" if a.arm != "old" and a.rules == "old" else "")
    jobs = [(c, arm, i) for i in range(a.k) for c in cs]
    print(f"e2: {a.point} arm={arm} k={a.k} cases={len(cs)} → {run_dir}")
    stop = False
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        futs = {pool.submit(one, a.point, c, arm, i, run_dir, env): (c, i) for c, arm, i in jobs}
        for f in concurrent.futures.as_completed(futs):
            m = f.result()
            print(f"  {m['case']} {m['arm']}-{m['i']}: exit {m['exit']} {m['secs']}s {m['model'] or ''} {m['error'] or ''}")
            if m["rate_limited"] and not stop:
                stop = True
                print("e2: rate limited, cancelling the rest (rerun the same command to continue)")
                for g in futs:
                    g.cancel()
    print(run_id)
    return 1 if stop else 0


# ---------- scoring ----------

CJK = re.compile("[\u3400-\u9fff\uf900-\ufaff]")
LATIN = re.compile(r"[A-Za-z]")
SCRIPT = json.loads((hubconf.ROOT / "locales" / "zh-Hant.script.json").read_text())
SIMPLIFIED = set(SCRIPT["simplified_only"])
FULL_PUNCT = set(SCRIPT["full_punct"])
HALF_PUNCT = set(",.:;!?()")


def added_text(before: str, after: str) -> str:
    """the lines the model wrote (added or changed), the text whose language is scored"""
    return "\n".join(l[1:] for l in difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=0)
                     if l.startswith("+") and not l.startswith("+++"))


def language(text: str) -> dict:
    cjk, lat = len(CJK.findall(text)), len(LATIN.findall(text))
    punct = [ch for i, ch in enumerate(text) if ch in FULL_PUNCT | HALF_PUNCT
             and CJK.match(text[i - 1] if i else " ") ]   # punctuation right after a Chinese character
    return {"cjk_share": round(cjk / (cjk + lat), 3) if cjk + lat else None,
            "simplified": sorted({ch for ch in text if ch in SIMPLIFIED}),
            "full_punct": round(sum(ch in FULL_PUNCT for ch in punct) / len(punct), 3) if punct else None}


_gen_todo = None


def todo_items(text: str) -> set[tuple[str, str]] | None:
    global _gen_todo
    _gen_todo = _gen_todo or _load("gen_todo_e2", "gen-todo.py")
    items, problems = {o: [] for o in _gen_todo.OWNERS}, []
    if _gen_todo.todo_section(text) is None:
        return None
    _gen_todo.parse("x", text, items, problems)
    return {(o, _gen_todo.OWNER_KEY.sub("", t).strip()) for o, lst in items.items() for _, t in lst}


def state_check(text: str) -> list[str]:
    import contracts
    return contracts.check_state_todo(text, hubconf.ROOT / "contracts" / "state-todo.v1.md", hubconf.owner_tag())


def index_outcome(point: str, case: pathlib.Path, out: pathlib.Path) -> dict:
    """an index run → valid, changed, the roster block untouched (contract), the language of what it wrote"""
    before = json.loads((case / "args.json").read_text())["index_before"]
    try:
        obj = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        obj = {}
    so = obj.get("structured_output")
    r = {"valid": isinstance(so, dict) and not obj.get("is_error"), "after": None, "error": obj.get("llm_error")}
    if point == "librarian-index-edits" and r["valid"]:
        try:
            r["after"] = edits.apply(before, so.get("edits") or []) if so.get("index_updated") else before
        except edits.EditError as e:
            r["valid"], r["error"] = False, f"edits: {e}"
    elif point == "librarian-index" and (out / "after.index.md").exists():
        r["after"] = (out / "after.index.md").read_text(errors="replace")
    if r["after"] is None:
        return r
    roster = (lambda t: (m.group(0) if (m := ROSTER.search(t)) else None))
    r.update({"changed": r["after"] != before, "contract_ok": roster(r["after"]) == roster(before), "items": None,
              "lang": language(added_text(before, r["after"]) + "\n" +
                               (str(so.get("relations_found") or "") if isinstance(so, dict) else ""))})
    return r


def outcome(point: str, case: pathlib.Path, out: pathlib.Path) -> dict:
    """one run → what is scored: valid, decision, resulting STATE.md, to-do items, language of what it wrote"""
    if point.startswith("librarian-index"):
        return index_outcome(point, case, out)
    before_f = case / "snapshot" / "STATE.md"
    before = before_f.read_text(errors="replace") if before_f.exists() else ""
    try:
        obj = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        obj = {}
    so = obj.get("structured_output")
    r = {"valid": isinstance(so, dict) and not obj.get("is_error"), "after": None, "error": obj.get("llm_error")}
    if point == "librarian-todo-format":   # no structured output: valid = the call ended normally
        r["valid"] = bool(obj) and not obj.get("is_error") and not obj.get("llm_error")
    if point in ("librarian-state", "librarian-todo-format") and (out / "after.STATE.md").exists():
        r["after"] = (out / "after.STATE.md").read_text(errors="replace")
    elif point == "librarian-state-edits" and r["valid"]:
        try:
            r["after"] = edits.apply(before, so.get("edits") or []) if so.get("state_updated") else before
        except edits.EditError as e:
            r["valid"], r["error"] = False, f"edits: {e}"
    if r["after"] is None:
        return r
    after = r["after"]
    r["changed"] = after != before
    r["claimed"] = bool(so.get("state_updated")) if isinstance(so, dict) else None
    r["contract_ok"] = not state_check(after)
    r["items"] = todo_items(after)
    free = added_text(before, after) + "\n" + (str(so.get("summary") or "") + "\n" + str(so.get("reason") or "")
                                               if isinstance(so, dict) else "")
    r["lang"] = language(free)
    return r


def prod_outcome(case: pathlib.Path) -> dict:
    before_f = case / "snapshot" / "STATE.md"
    before = before_f.read_text(errors="replace") if before_f.exists() else ""
    after = (case / "after.STATE.md").read_text(errors="replace")
    return {"valid": True, "after": after, "changed": after != before, "contract_ok": not state_check(after),
            "items": todo_items(after), "lang": language(added_text(before, after))}


def grams(t: str) -> set[str]:
    t = re.sub(r"\s+", "", t)
    return {t[i:i + 2] for i in range(len(t) - 1)} or {t}


def match(a: set | None, b: set | None, threshold: float = 0.5) -> dict | None:
    """to-do items of two runs paired one-to-one by character-bigram Jaccard (owner ignored for identity; the same model
    rewords items and moves them between owners, measured apart): jaccard of the pairing, owner agreement among pairs"""
    if a is None or b is None:
        return None
    if not a and not b:
        return {"items": 1.0, "owner": 1.0}
    cand = sorted(((len(grams(t) & grams(t2)) / len(grams(t) | grams(t2)), i, j)
                   for i, (_, t) in enumerate(sorted(a)) for j, (_, t2) in enumerate(sorted(b))), reverse=True)
    used_a, used_b, pairs = set(), set(), []
    for sim, i, j in cand:
        if sim < threshold:
            break
        if i not in used_a and j not in used_b:
            used_a.add(i), used_b.add(j), pairs.append((sorted(a)[i][0], sorted(b)[j][0]))
    return {"items": round(len(pairs) / (len(a) + len(b) - len(pairs)), 3),
            "owner": round(sum(x == y for x, y in pairs) / len(pairs), 3) if pairs else None}


def agree(x: dict, y: dict) -> dict:
    if x.get("after") is None or y.get("after") is None:
        return {}
    m = match(x["items"], y["items"]) or {}
    exact = (len(x["items"] & y["items"]) / len(x["items"] | y["items"]) if x["items"] or y["items"] else 1.0) \
        if x["items"] is not None and y["items"] is not None else None
    return {"decision": x["changed"] == y["changed"], "items_exact": exact, "items": m.get("items"),
            "owner": m.get("owner"), "count_diff": abs(len(x["items"] or ()) - len(y["items"] or ())),
            "text_sim": round(difflib.SequenceMatcher(None, x["after"], y["after"]).ratio(), 3)}


METRICS = ("decision", "items_exact", "items", "owner", "count_diff", "text_sim")


def mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def boot(v, n=1000, seed=0):
    """90% bootstrap interval of the mean"""
    v = [x for x in v if x is not None]
    if len(v) < 2:
        return None
    rnd = random.Random(seed)
    ms = sorted(sum(rnd.choice(v) for _ in v) / len(v) for _ in range(n))
    return round(ms[int(0.05 * n)], 3), round(ms[int(0.95 * n)], 3)


def cmd_score(a) -> int:
    run_dir = root() / "runs" / a.run
    point = json.loads((run_dir / "run.json").read_text())["point"]
    if p := plugin(point):
        return emit({"run": a.run, "point": point} | p.report(run_dir), a, run_dir)
    by: dict[str, dict[str, list[dict]]] = {}
    for out in sorted(run_dir.glob("*/*/*-*")):
        if not (out / "result.json").exists():
            continue
        cid, arm = f"{out.parent.parent.name}/{out.parent.name}", out.name.rsplit("-", 1)[0]
        case = root() / "cases" / POINT_CASES.get(point, point) / cid
        by.setdefault(cid, {}).setdefault(arm, []).append(outcome(point, case, out))
    for cid in by:   # what production wrote that night, as one more arm (recorded outcome or history)
        case = root() / "cases" / POINT_CASES.get(point, point) / cid
        if point in ("librarian-state", "librarian-state-edits") and (case / "after.STATE.md").exists():
            by[cid]["prod"] = [prod_outcome(case)]
    report = {"run": a.run, "point": point, "arms": {}, "pairs": {}}
    arms = sorted({arm for v in by.values() for arm in v})
    for arm in arms:
        rs = [r for v in by.values() for r in v.get(arm, [])]
        langs = [r["lang"] for r in rs if "lang" in r]
        report["arms"][arm] = {
            "runs": len(rs), "valid": mean([r["valid"] for r in rs]),
            "contract_ok": mean([r.get("contract_ok") for r in rs if "contract_ok" in r]),
            "changed": mean([r.get("changed") for r in rs if "changed" in r]),
            "cjk_share": mean([g["cjk_share"] for g in langs]),
            "cjk_share_min": min((g["cjk_share"] for g in langs if g["cjk_share"] is not None), default=None),
            "simplified_runs": sum(bool(g["simplified"]) for g in langs),
            "simplified_chars": sorted({c for g in langs for c in g["simplified"]}),
            "full_punct": mean([g["full_punct"] for g in langs]),
            "errors": sorted({r["error"] for r in rs if r.get("error")})[:5]}
    for x, y in [(p, p) for p in arms] + list(itertools.combinations(arms, 2)):
        rows = []
        for v in by.values():
            xs, ys = v.get(x, []), v.get(y, [])
            pairs = itertools.combinations(xs, 2) if x == y else itertools.product(xs, ys)
            rows += [agree(p, q) for p, q in pairs]
        rows = [r for r in rows if r]
        report["pairs"][f"{x}~{y}"] = {k: {"mean": mean([r[k] for r in rows]), "ci90": boot([r[k] for r in rows])}
                                       for k in METRICS} | {"n": len(rows)}
    return emit(report, a, run_dir)


def emit(report: dict, a, run_dir: pathlib.Path) -> int:
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    else:
        print(f"{a.run} ({report['point']})")
        for arm, s in report["arms"].items():
            print(f"  {arm}: " + ", ".join(f"{k}={v}" for k, v in s.items()))
        for k, s in report["pairs"].items():
            print(f"  {k}: n={s['n']} " + ", ".join(f"{m}={v['mean']} {v['ci90'] or ''}" for m, v in s.items() if m != "n"))
    (run_dir / "score.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="e2.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("point", choices=["librarian-state", "librarian-index", "librarian-todo-format", *PLUGINS])
    f.add_argument("project", help="librarian-state: the project; librarian-index: the night (YYYY-MM-DD) whose digest")
    f.add_argument("--since", help="start of the range (default: the librarian's checkpoint)")
    f.add_argument("--night", type=int, help="the Nth most recent night the librarian changed STATE.md")
    f.add_argument("--kind", help="librarian-todo-format: untagged | done-left | no-section | real-<name>")
    f.add_argument("--state-file", help="librarian-todo-format: a real STATE.md that fails the check")
    f.add_argument("--today", help="consolidate: the day the survey is made for (default today)")
    c = sub.add_parser("cases")
    c.add_argument("point", nargs="?", default="librarian-state")
    r = sub.add_parser("run")
    r.add_argument("point", choices=sorted({*POINT_CASES, *PLUGINS}))
    r.add_argument("--arm", choices=["old", "new", "new-en"], required=True)
    r.add_argument("--rules", choices=["old", "new"], default="new", help="AGENT.md the agent reads (new arms only)")
    r.add_argument("--k", type=int, default=3)
    r.add_argument("--case", default="*")
    r.add_argument("--run")
    r.add_argument("--jobs", type=int, default=3)
    s = sub.add_parser("score")
    s.add_argument("run")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "freeze" and (p := plugin(a.point)):
        for c in p.freeze(a):
            print(case_id(c))
    elif a.cmd == "freeze":
        if a.point == "librarian-todo-format":
            print(freeze_todo(a.kind, a.project, a.state_file))
        else:
            print(freeze_index(a.project) if a.point == "librarian-index" else freeze_state(a.project, a.since, a.night))
    elif a.cmd == "cases":
        for p in cases(a.point):
            print(case_id(p))
    elif a.cmd == "run":
        p = plugin(a.point)
        return p.cmd_run(a) if hasattr(p, "cmd_run") else cmd_run(a)
    elif a.cmd == "score":
        return cmd_score(a)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
