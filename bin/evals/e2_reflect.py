"""E2 for reflect-week (docs/design/i18n.md): the weekly analysis, a completion over the week's statistics.

Cases are the stored weekly statistics (the data dir's reflect/YYYY-Www.json, what exists), frozen with what the
analysis would be given now: the statistics alone, and, for a week the assistant has session summaries for, the
statistics with the summaries (bin/reflect_llm.py's privacy rule applies as in production: the summaries leave out the
excluded projects; nothing else of the conversations is read). Case: cases/reflect-week/<week>/<stats|digests>/
with args.json {week, stats, digests, owner} and prompt.reflect-week.txt, the prompt rendered by the code at e2.OLD_REF.

Scored per run: valid (the schema's output), the language of the free text; per pair of runs: the projects listed
(name-set Jaccard), the same status for the projects both list, the same level for the four fluency dimensions, and
the text similarity (reported, not gated).

    e2.py freeze reflect-week all | <YYYY-Www>

Freezing reads the hub's registry index (privacy labels): run it with the hub's AGENT_HUB_STATE, or the summaries are
left out as production leaves them out when the registry can't be read.
"""
from __future__ import annotations

import datetime
import difflib
import importlib.util
import itertools
import json
import pathlib
import subprocess
import sys
import tempfile
from unittest import mock

import e2

POINT = "reflect-week"
hubconf = e2.hubconf
ENUMS = ("name", "status", "dimension", "level")   # not free text: the report page reads these values


def _load(src: str, name: str):
    f = pathlib.Path(tempfile.mkdtemp(prefix="e2-reflect-")) / "reflect_llm.py"
    f.write_text(src)
    spec = importlib.util.spec_from_file_location(name, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def old_reflect():
    """reflect_llm.py at e2.OLD_REF (the Chinese prompt); its imports resolve to bin/"""
    src = subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{e2.OLD_REF}:bin/reflect_llm.py"],
                         capture_output=True, text=True, check=True).stdout
    return _load(src, "reflect_llm_old_e2")


def old_render(rl, stats: dict, digests: bool) -> tuple[str, dict | None]:
    """(the prompt analyze() at OLD_REF sends, the summaries in it): analyze() itself runs, with the model call caught
    and, for the statistics-only variant, the assistant's summaries command failing (as production does then)"""
    seen: dict = {}
    real = subprocess.run

    def run(cmd, *a, **kw):
        if any(str(c).endswith("llm.py") for c in cmd):   # the model call: keep the prompt, send nothing
            seen["prompt"] = kw.get("input")
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if not digests:
            return subprocess.CompletedProcess(cmd, 1, "", "")
        r = real(cmd, *a, **kw)   # the assistant's summaries, read only
        seen["raw"] = r.stdout if r.returncode == 0 else None
        return r

    with mock.patch.object(rl.subprocess, "run", run):
        rl.analyze(stats)
    d = None
    if seen.get("raw"):
        d = json.loads(seen["raw"])
        d["sessions"] = [x for x in d.get("sessions", []) if x.get("project") not in rl.EXCLUDED]
        if not d["sessions"]:
            d = None
    return seen.get("prompt") or "", d


def freeze(a) -> list[pathlib.Path]:
    what = a.project
    weeks = sorted(hubconf.data_file("reflect").glob("2*-W*.json"))
    if what != "all":
        weeks = [w for w in weeks if w.stem == what]
    rl = old_reflect()
    out = []
    for w in weeks:
        stats = {k: v for k, v in json.loads(w.read_text()).items() if k != "analysis"}
        if not stats.get("totals", {}).get("prompts"):
            continue   # production sends nothing for a week without activity
        for name in ("stats", "digests"):
            try:
                text, dg = old_render(rl, stats, name == "digests")
            except (KeyError, TypeError, AttributeError) as e:   # data older than the code: production can't send it either
                print(f"e2: {w.stem} {name}: not renderable ({e!r})", file=sys.stderr)
                continue
            if name == "digests" and dg is None:
                continue   # no summaries for that week: the statistics case is the one production sends
            c = e2.root() / "cases" / POINT / stats["week"] / name
            c.mkdir(parents=True, exist_ok=True)
            (c / "args.json").write_text(json.dumps({"week": stats["week"], "stats": stats, "digests": dg,
                                                     "owner": hubconf.owner_tag(), "frozen": datetime.date.today().isoformat()},
                                                    ensure_ascii=False, indent=1))
            (c / f"prompt.{POINT}.txt").write_text(text)
            out.append(c)
    return out


def prompt(case: pathlib.Path, arm: str) -> str:
    if arm == "old":
        return (case / f"prompt.{POINT}.txt").read_text()
    a = json.loads((case / "args.json").read_text())
    rl = e2._load("reflect_llm_e2", "reflect_llm.py")
    return rl.build_prompt(a["stats"], a["digests"], lang="en" if e2.base_arm(arm) == "new-en" else "zh-Hant",
                           owner=a["owner"])


def one(case: pathlib.Path, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / case.parent.name / case.name / f"{arm}-{i}"
    if (out / "result.json").exists():
        return json.loads((out / "meta.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(prompt(case, arm))
    with tempfile.TemporaryDirectory(prefix="e2-") as tmp:
        t0 = datetime.datetime.now()
        r = subprocess.run([str(e2.BIN / "py"), str(e2.BIN / "llm.py"), "run", POINT, "--prompt-file", str(out / "prompt.txt")],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=1500)
        secs = (datetime.datetime.now() - t0).total_seconds()
    return record(case, arm, i, out, r, secs)


def record(case: pathlib.Path, arm: str, i: int, out: pathlib.Path, r, secs: float) -> dict:
    """result.json / stderr.txt / meta.json of one run, as e2.one writes them"""
    (out / "result.json").write_text(r.stdout)
    (out / "stderr.txt").write_text(r.stderr[-20000:])
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = {}
    meta = {"case": e2.case_id(case), "arm": arm, "i": i, "exit": r.returncode, "secs": round(secs, 1),
            "model": obj.get("llm_model") or obj.get("model"), "error": obj.get("llm_error"),
            "rate_limited": bool(e2.RATE_LIMIT.search((obj.get("llm_error") or "") + r.stderr[-2000:])) and r.returncode != 0}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ---------- scoring ----------

def free_text(o, key: str = "") -> list[str]:
    if isinstance(o, dict):
        return [t for k, v in o.items() if k not in ENUMS and k != "meta" for t in free_text(v, k)]
    if isinstance(o, list):
        return [t for v in o for t in free_text(v, key)]
    return [o] if isinstance(o, str) and o.strip() else []


def outcome(out: pathlib.Path) -> dict:
    try:
        obj = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        obj = {}
    so = obj.get("structured_output")
    r = {"valid": isinstance(so, dict) and not obj.get("is_error"), "error": obj.get("llm_error")}
    if not r["valid"]:
        return r
    text = "\n".join(free_text(so))
    r.update({"status": {p.get("name"): p.get("status") for p in so.get("projects") or []},
              "level": {f.get("dimension"): f.get("level") for f in so.get("fluency") or []},
              "text": text, "lang": e2.language(text)})
    return r


def agree(x: dict, y: dict) -> dict:
    if not (x["valid"] and y["valid"]):
        return {}
    names = set(x["status"]) | set(y["status"])
    both = set(x["status"]) & set(y["status"])
    dims = set(x["level"]) | set(y["level"])
    return {"projects": round(len(both) / len(names), 3) if names else 1.0,
            "status": round(sum(x["status"][n] == y["status"][n] for n in both) / len(both), 3) if both else None,
            "level": round(sum(x["level"].get(d) == y["level"].get(d) for d in dims) / len(dims), 3) if dims else None,
            "text_sim": round(difflib.SequenceMatcher(None, x["text"], y["text"]).ratio(), 3)}


METRICS = ("projects", "status", "level", "text_sim")


def report(run_dir: pathlib.Path) -> dict:
    by: dict[str, dict[str, list[dict]]] = {}
    for out in sorted(run_dir.glob("*/*/*-*")):
        if (out / "result.json").exists():
            by.setdefault(f"{out.parent.parent.name}/{out.parent.name}", {}).setdefault(
                out.name.rsplit("-", 1)[0], []).append(outcome(out))
    return arms_and_pairs(by, agree, METRICS)


def arms_and_pairs(by: dict, agree_fn, metrics, extra=None) -> dict:
    """per arm: validity and language; per pair of arms (x~x = the noise floor): the mean and 90% interval of each
    agreement metric over the case's run pairs. extra(runs) adds per-arm rates."""
    rep = {"arms": {}, "pairs": {}}
    arms = sorted({arm for v in by.values() for arm in v})
    for arm in arms:
        rs = [r for v in by.values() for r in v.get(arm, [])]
        langs = [r["lang"] for r in rs if r.get("lang")]
        rep["arms"][arm] = {
            "runs": len(rs), "valid": e2.mean([r["valid"] for r in rs]),
            **(extra(rs) if extra else {}),
            "cjk_share": e2.mean([g["cjk_share"] for g in langs]),
            "cjk_share_min": min((g["cjk_share"] for g in langs if g["cjk_share"] is not None), default=None),
            "simplified_runs": sum(bool(g["simplified"]) for g in langs),
            "simplified_chars": sorted({c for g in langs for c in g["simplified"]}),
            "full_punct": e2.mean([g["full_punct"] for g in langs]),
            "errors": sorted({str(r["error"]) for r in rs if r.get("error")})[:5]}
    for x, y in [(p, p) for p in arms] + list(itertools.combinations(arms, 2)):
        rows = []
        for v in by.values():
            xs, ys = v.get(x, []), v.get(y, [])
            pairs = itertools.combinations(xs, 2) if x == y else itertools.product(xs, ys)
            rows += [agree_fn(p, q) for p, q in pairs]
        rows = [r for r in rows if r]
        rep["pairs"][f"{x}~{y}"] = {k: {"mean": e2.mean([r.get(k) for r in rows]), "ci90": e2.boot([r.get(k) for r in rows])}
                                    for k in metrics} | {"n": len(rows)}
    return rep
