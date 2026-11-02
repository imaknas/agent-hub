#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The waiting report (docs/design/test-waiting.md B7): where agent sessions wait on tests, per project. Zero LLM.

Two sources:
  transcripts   through the runtime's transcript port (runtime_ports, never a runtime's own format): every session in
                every project's folders and worktrees, subagents kept apart. A session belongs to the project whose
                workspace holds its first folder (project_git.project_at; projects-and-repos' resolver replaces it at
                its step 3, without changing what is counted). Entries are counted once by their id and tool calls
                once by theirs (a branched or resumed session copies its history). A command's wait is the time from
                its call to the entry returning its result, capped at CAP_MIN minutes; a call started in the
                background is counted as such, never as waiting. A command is classified by what it runs, in this
                order: deploy (a deploy.sh), merge (merge-branch), commit (git commit, in a repo with commit_gate),
                runner (brunna test), a declared tier (the command contains the tier's run text), then patterns
                for undeclared commands: e2e, targeted. Agent time is section 1's: the gaps between a top-level
                session's consecutive entries, each capped at CAP_MIN, except a gap a person's message ends; the
                model's share is the gaps an assistant entry ends (gaps other entries end -- notifications, system
                entries -- are not counted).
                "The owner waited" (a proxy): a top-level wait inside a turn the owner answered within OWNER_MIN
                minutes of its last entry, i.e. the owner was watching that turn end.
                Each e2e run is followed to the next edit or gate in its session: a gate first (merge-branch,
                a commit, brunna test, a deploy) makes it a safety run, an edit first inner-loop work.
  run log       the runner's own measurements (tiers.RunLog: every tier per invocation, hits included, seconds, who
                called), read as each project through a project_git job (test-runs), since the file sits in the
                project-writable git dir. A project whose log can't be read is said so, never shown as zero.

Only counts and durations are written: no command text, no paths (the status page is readable from other machines).

  bin/test-waiting-report.py [--days N] [--end YYYY-MM-DD] [--json]
    the N days (default 7) ending at the start of --end (default today, the owner's clock): written to
    <data>/test-waiting/<end>.json and summarized; --json prints the report and writes nothing.

The status page shows the newest file (bin/gen-status-page.py); the nightly job brunna/test-waiting writes it.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import itertools
import json
import pathlib
import re
import statistics
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import hublib  # noqa: E402
import i18n  # noqa: E402
import project_git  # noqa: E402
import runtime_ports  # noqa: E402
import tiers  # noqa: E402

_ = i18n.domain(__file__)

VERSION = 1
CAP_MIN = 20
OWNER_MIN = 5
OUT = hubconf.data_file("test-waiting")
GATES = ("deploy", "merge", "commit", "runner")
DEPLOY = re.compile(r"(^|[\s;&|/])deploy\.sh\b")
MERGE = re.compile(r"\bmerge-branch\b")
COMMIT = re.compile(r"\bgit(\s+-C\s+\S+)?\s+commit\b")
RUNNER = re.compile(r"\b(?:agenthub|brunna)\s+test\b|test-runner\.py")
E2E = re.compile(r"playwright|\s-m\s+['\"]?e2e\b|test:e2e|\be2e\b")
TARGETED = re.compile(r"\bpytest\b|\bunittest\b|run-tests|\bnpm\s+(run\s+)?test\b|\bvitest\b|\bjest\b|\bgo\s+test\b|"
                      r"\bcargo\s+test\b")


def norm(s: str) -> str:
    return " ".join(s.split())


def tier_patterns(doc: dict) -> list[tuple[str, re.Pattern]]:
    out = []
    for t in tiers.upgrade(doc).get("tests") or []:
        run = norm(str(t.get("run") or ""))
        if run and t.get("tier"):
            out.append((str(t["tier"]), re.compile(re.escape(run).replace(re.escape("{jobs}"), r"\d+"))))
    return out


def classify(command: str, doc: dict, declared: list[tuple[str, re.Pattern]]) -> str | None:
    """A test command's kind, or None for any other command."""
    c = norm(command)
    if DEPLOY.search(c):
        return "deploy"
    if MERGE.search(c):
        return "merge"
    if doc.get("commit_gate") and COMMIT.search(c):
        return "commit"
    if RUNNER.search(c):
        return "runner"
    for name, pat in declared:
        if pat.search(c):
            return f"tier:{name}"
    if E2E.search(c):
        return "e2e"
    if TARGETED.search(c):
        return "targeted"
    return None


def is_e2e(kind: str) -> bool:
    return kind == "e2e" or (kind.startswith("tier:") and "e2e" in kind)


def minutes(secs: float) -> float:
    return round(secs / 60, 1)


class Tally:
    def __init__(self) -> None:
        self.agent = self.model = self.tests = self.owner = 0.0
        self.kinds: dict[str, list] = collections.defaultdict(lambda: [0.0, 0])
        self.background = 0
        self.sub_tests = 0.0
        self.sub_kinds: dict[str, list] = collections.defaultdict(lambda: [0.0, 0])
        self.e2e = collections.Counter()
        self.sessions: set = set()

    def out(self) -> dict:
        kinds = lambda d: {k: {"min": minutes(v[0]), "runs": v[1]} for k, v in sorted(d.items())}  # noqa: E731
        return {"sessions": len(self.sessions), "agent_min": minutes(self.agent), "model_min": minutes(self.model),
                "tests_min": minutes(self.tests), "owner_waited_min": minutes(self.owner), "by_kind": kinds(self.kinds),
                "background_runs": self.background,
                "subagents": {"tests_min": minutes(self.sub_tests), "by_kind": kinds(self.sub_kinds)},
                "e2e": {k: self.e2e[k] for k in ("safety", "inner", "other")}}


def session(tally: Tally, turns: list, doc: dict, declared: list, subagent: bool, calls_seen: set) -> None:
    turns.sort(key=lambda t: t.ts)
    cap = CAP_MIN * 60
    open_calls: dict[str, tuple] = {}
    waits: list[tuple] = []          # (start, end, kind)
    steps: list[tuple] = []          # (ts, order, "gate" | "edit" | "e2e"), in the order the actions were made
    counter = itertools.count()
    for t in turns:
        if t.role == "assistant":
            for a in t.actions:
                kind = classify(a.command, doc, declared) if a.kind == "run_command" and a.command else None
                order = next(counter)
                if a.kind == "write_file":
                    steps.append((t.ts, order, "edit"))
                elif kind in GATES:
                    steps.append((t.ts, order, "gate"))
                if not a.call or a.call in calls_seen:
                    continue
                calls_seen.add(a.call)
                if kind and a.background:
                    tally.background += 1
                elif kind:
                    open_calls[a.call] = (t.ts, kind, order)
        for c in t.results:
            if c in open_calls:
                ts, kind, order = open_calls.pop(c)
                waits.append((ts, t.ts, kind))
                if is_e2e(kind):
                    steps.append((ts, order, "e2e"))
    for ts, end, kind in waits:
        secs = min(max((end - ts).total_seconds(), 0.0), cap)
        bucket = tally.sub_kinds if subagent else tally.kinds
        bucket[kind][0] += secs
        bucket[kind][1] += 1
        if subagent:
            tally.sub_tests += secs
        else:
            tally.tests += secs
    steps.sort(key=lambda s: (s[0], s[1]))
    for i, (_ts, _order, what) in enumerate(steps):
        if what != "e2e":
            continue
        nxt = next((s[2] for s in steps[i + 1:] if s[2] in ("gate", "edit")), None)
        tally.e2e["safety" if nxt == "gate" else "inner" if nxt == "edit" else "other"] += 1
    if subagent:
        return
    for a, b in zip(turns, turns[1:]):
        if b.role != "assistant" and not b.results:
            continue   # a person's message, or anything else (a notification, a system entry): not the agent's time
        gap = min(max((b.ts - a.ts).total_seconds(), 0.0), cap)
        tally.agent += gap
        if b.role == "assistant":
            tally.model += gap
    humans = [t for t in turns if t.role == "human"]
    for ts, end, _kind in waits:
        nxt = next((h for h in humans if h.ts >= end), None)
        if nxt is None:
            continue
        last = max((t.ts for t in turns if t.role != "human" and end <= t.ts < nxt.ts), default=end)
        if (nxt.ts - last).total_seconds() <= OWNER_MIN * 60:
            tally.owner += min(max((end - ts).total_seconds(), 0.0), cap)


def scan(start: datetime.datetime, end: datetime.datetime) -> dict[str, dict]:
    rt = runtime_ports.load(ports=("transcripts",))
    docs = hubconf.projects()
    utc = datetime.timezone.utc
    seen: set = set()
    by_session: dict[tuple, list] = collections.defaultdict(list)
    owner_of: dict[str, str] = {}
    for name, paths in hublib.project_paths().items():
        for t in rt.turns(paths, start.astimezone(utc), end.astimezone(utc), worktrees=True, subagents=True):
            if t.id:
                if t.id in seen:
                    continue
                seen.add(t.id)
            if t.session not in owner_of:
                found = project_git.project_at(t.folder) if t.folder else None
                owner_of[t.session] = found[0] if found else name
            by_session[(t.session, t.subagent)].append(t)
    tallies: dict[str, Tally] = collections.defaultdict(Tally)
    calls_seen: set = set()
    declared = {n: tier_patterns(d) for n, d in docs.items()}
    for (sid, subagent), turns in sorted(by_session.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        proj = owner_of[sid]
        tally = tallies[proj]
        if not subagent:
            tally.sessions.add(sid)
        session(tally, turns, docs.get(proj) or {}, declared.get(proj) or [], subagent, calls_seen)
    return {n: t.out() for n, t in sorted(tallies.items())}


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1)


def num(x) -> float:
    try:
        return max(float(x), 0.0)
    except (TypeError, ValueError):
        return 0.0


def runner_summary(recs: list[dict]) -> dict:
    """The run log's lines (a project's data, untrusted: every field coerced) → per tier and per caller."""
    per_tier: dict[str, dict] = collections.defaultdict(lambda: {"runs": 0, "hits": 0, "fails": 0, "secs": []})
    per_run: dict[str, dict] = {}
    for r in recs:
        tier, result = f"{r.get('project')}/{r.get('tier')}", str(r.get("result"))
        t = per_tier[tier]
        t["runs"] += 1
        if result == "hit":
            t["hits"] += 1
        else:
            t["secs"].append(num(r.get("secs")))
            t["fails"] += result == "fail"
        inv = per_run.setdefault(str(r.get("run")), {"caller": str(r.get("caller") or "session"), "total": 0.0,
                                                     "hit": True, "ok": True})
        inv["total"] = max(inv["total"], num(r.get("total")))
        inv["hit"] = inv["hit"] and result == "hit"
        inv["ok"] = inv["ok"] and result != "fail"
    callers: dict[str, dict] = collections.defaultdict(lambda: {"invocations": 0, "all_hits": 0, "failed": 0,
                                                                 "totals": []})
    for inv in per_run.values():
        c = callers[inv["caller"]]
        c["invocations"] += 1
        c["all_hits"] += inv["hit"]
        c["failed"] += not inv["ok"]
        c["totals"].append(inv["total"])
    return {
        "tiers": {k: {"runs": v["runs"], "hits": v["hits"], "fails": v["fails"], "run_min": minutes(sum(v["secs"])),
                      "median_s": round(statistics.median(v["secs"]), 1) if v["secs"] else None,
                      "p90_s": pct(v["secs"], 0.9)} for k, v in sorted(per_tier.items())},
        "callers": {k: {"invocations": v["invocations"], "all_hits": v["all_hits"], "failed": v["failed"],
                        "total_min": minutes(sum(v["totals"])),
                        "median_s": round(statistics.median(v["totals"]), 1) if v["totals"] else None,
                        "p90_s": pct(v["totals"], 0.9)} for k, v in sorted(callers.items())}}


def as_project(name: str) -> str | None:
    """Who reads the log: the project, or the platform in its own checkout."""
    return None if name == hubconf.hub_project() else name


def runner(start: datetime.datetime, end: datetime.datetime) -> dict[str, dict]:
    docs = dict(hubconf.projects())
    docs.setdefault(hubconf.hub_project(), {})
    out = {}
    for name in sorted(docs):
        repo = hubconf.workspace(name)
        if name != hubconf.hub_project() and not tiers.upgrade(docs[name]).get("tests"):
            continue
        if not repo or not (repo / ".git").exists():
            continue
        try:
            recs = project_git.data(as_project(name), repo, "test-runs", repo, start.isoformat())
        except project_git.Unavailable as e:
            out[name] = {"unavailable": str(e)[:300]}
            continue
        keep = []
        for r in recs if isinstance(recs, list) else []:
            try:
                if isinstance(r, dict) and datetime.datetime.fromisoformat(str(r.get("at"))) < end:
                    keep.append(r)
            except (ValueError, TypeError):
                continue
        out[name] = runner_summary(keep)
    return out


def report(days: int, end_day: datetime.date) -> dict:
    tz = hubconf.tz()
    end = datetime.datetime.combine(end_day, datetime.time(), tz)
    start = end - datetime.timedelta(days=days)
    sessions = scan(start, end)
    runs = runner(start, end)
    return {"v": VERSION, "from": start.isoformat(), "to": end.isoformat(), "days": days,
            "generated": datetime.datetime.now(tz).isoformat(timespec="seconds"),
            "rules": {"cap_min": CAP_MIN, "owner_min": OWNER_MIN},
            "projects": {n: {"sessions": sessions.get(n), "runner": runs.get(n)}
                         for n in sorted(set(sessions) | set(runs))}}


def latest() -> dict | None:
    files = sorted(OUT.glob("*.json")) if OUT.is_dir() else []
    for f in reversed(files):
        try:
            return json.loads(f.read_text())
        except (OSError, ValueError):
            continue
    return None


def kind_label(kind: str) -> str:
    if kind.startswith("tier:"):
        return _("tier {name}", name=kind[5:])
    return {"deploy": _("deploy script"), "merge": _("merge-branch"), "commit": _("commit gate"),
            "runner": _("brunna test"), "e2e": _("e2e (undeclared)"),
            "targeted": _("targeted (undeclared)")}.get(kind, kind)


def caller_label(caller: str) -> str:
    return {"merge-branch": _("merge-branch"), "commit-gate": _("commit gate"), "nightly": _("nightly run"),
            "deploy": _("deploy"), "session": _("a session's own run")}.get(caller, caller)


def text(rep: dict) -> str:
    lines = [_("Waiting on tests, {start} to {end} ({days} days)", start=rep["from"][:10], end=rep["to"][:10],
               days=rep["days"])]
    for name, p in rep["projects"].items():
        s, r = p.get("sessions"), p.get("runner")
        lines.append(f"\n{name}")
        if s:
            share = round(100 * s["tests_min"] / s["agent_min"]) if s["agent_min"] else 0
            lines.append(_("  agent time {agent} min (model {model} min) in {n} {n:session|sessions}; waiting on tests "
                           "{tests} min ({share}%), the owner waited {owner} min; subagents {sub} min",
                           agent=s["agent_min"], model=s["model_min"], n=s["sessions"], tests=s["tests_min"],
                           share=share, owner=s["owner_waited_min"], sub=s["subagents"]["tests_min"]))
            for k, v in s["by_kind"].items():
                lines.append(_("    {kind}: {min} min, {n} {n:run|runs}", kind=kind_label(k), min=v["min"], n=v["runs"]))
            if s["background_runs"]:
                lines.append(_("    started in the background (not waited on): {n}", n=s["background_runs"]))
            e = s["e2e"]
            if sum(e.values()):
                lines.append(_("  e2e runs: {safety} before a gate (safety), {inner} before an edit (inner loop), "
                               "{other} neither", **e))
        if r and "unavailable" in r:
            lines.append(_("  run log: could not be read ({why})", why=r["unavailable"]))
        elif r:
            for k, v in r["tiers"].items():
                lines.append(_("  tier {tier}: {runs} {runs:run|runs}, {hits} {hits:hit|hits}, {fails} failed; median "
                               "{median} s, p90 {p90} s, {min} min run", tier=k, runs=v["runs"], hits=v["hits"],
                               fails=v["fails"], median=v["median_s"] if v["median_s"] is not None else "-",
                               p90=v["p90_s"] if v["p90_s"] is not None else "-", min=v["run_min"]))
            for k, v in r["callers"].items():
                lines.append(_("  {caller}: {n} {n:invocation|invocations} ({hits} only hits, {failed} failed), "
                               "median {median} s to the result, p90 {p90} s", caller=caller_label(k),
                               n=v["invocations"], hits=v["all_hits"], failed=v["failed"],
                               median=v["median_s"] if v["median_s"] is not None else "-",
                               p90=v["p90_s"] if v["p90_s"] is not None else "-"))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="test-waiting-report.py")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", type=datetime.date.fromisoformat)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    end = a.end or datetime.datetime.now(hubconf.tz()).date()
    try:
        rep = report(a.days, end)
    except runtime_ports.Unsupported as e:
        print(_("waiting report: the runtime has no transcripts ({why}); not written", why=e), file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    f = OUT / f"{end.isoformat()}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    tmp.replace(f)
    print(text(rep))
    print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
