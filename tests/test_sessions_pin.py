"""Characterization: the three session-based features (the weekly review's statistics, the memory-gap count, the
waiting report) on one Claude Code-shaped tree of transcripts, frozen in tests/fixtures/sessions-pin.json.

Written before the features stopped asking the platform's runtime for transcripts and started reading every session
source on the machine (bin/sessions.py): the same tree must give the same numbers after the move.

    bin/py -m unittest tests.test_sessions_pin
    bin/py tests/test_sessions_pin.py --update      # only for a deliberate change in what is counted
"""
import datetime
import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

GOLDEN = ROOT / "tests" / "fixtures" / "sessions-pin.json"
UTC = datetime.timezone.utc
START = datetime.datetime(2026, 10, 19, tzinfo=UTC)   # this week's Monday
END = START + datetime.timedelta(days=7)
ALPHA, BETA = "/w/alpha", "/w/beta"
WT = ALPHA + "/.claude/worktrees/wt1"
DOC = {"v": 2, "commit_gate": True,
       "tests": [{"tier": "full", "run": "pytest -n {jobs} tests", "when": ["merge"]},
                 {"tier": "e2e", "run": "npx playwright test", "when": ["nightly"]}]}


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "bin" / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def at(day: int, hour: int, minute: float = 0) -> str:
    t = START + datetime.timedelta(days=day, hours=hour, minutes=minute)
    return t.isoformat().replace("+00:00", "Z")


def human(ts, sid, uid, cwd, text="go"):
    return {"type": "user", "timestamp": ts, "sessionId": sid, "uuid": uid, "cwd": cwd, "origin": {"kind": "human"},
            "message": {"role": "user", "content": text}}


def meta(ts, sid, uid, cwd):
    return {"type": "user", "timestamp": ts, "sessionId": sid, "uuid": uid, "cwd": cwd, "isMeta": True,
            "message": {"role": "user", "content": "<system-reminder>x</system-reminder>"}}


def tool(name, call, **inp):
    return {"type": "tool_use", "name": name, "id": call, "input": inp}


def assistant(ts, sid, uid, cwd, *blocks, side=False):
    o = {"type": "assistant", "timestamp": ts, "sessionId": sid, "uuid": uid, "cwd": cwd,
         "message": {"role": "assistant", "content": list(blocks)}}
    if side:
        o["isSidechain"] = True
    return o


def result(ts, sid, uid, cwd, *calls, side=False):
    o = {"type": "user", "timestamp": ts, "sessionId": sid, "uuid": uid, "cwd": cwd,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": c, "content": "ok"}
                                                 for c in calls]}}
    if side:
        o["isSidechain"] = True
    return o


def claude_tree():
    """{relative file: [records]} in Claude Code's layout: <slug>/<session>.jsonl, subagents beside their session."""
    a = [human(at(1, 10), "A", "a1", ALPHA, "look at the tests"),
         meta(at(1, 10, .5), "A", "a2", ALPHA),
         assistant(at(1, 10, 1), "A", "a3", ALPHA, tool("Agent", "t1", prompt="x"), tool("Skill", "t2", skill="dataviz"),
                   tool("Bash", "c1", command="bin/merge-branch --note x"),
                   tool("Bash", "c2", command="pytest -q", run_in_background=True)),
         result(at(1, 10, 4), "A", "a4", ALPHA, "c1"),
         result(at(1, 10, 6), "A", "a5", ALPHA, "c2"),
         assistant(at(1, 10, 7), "A", "a6", ALPHA, tool("Bash", "c3", command="git commit -m y")),
         result(at(1, 10, 8), "A", "a7", ALPHA, "c3"),
         human(at(1, 10, 9), "A", "a8", ALPHA, "thanks"),
         assistant(at(1, 10, 10), "A", "a9", ALPHA, tool("SendMessage", "t3", to="x"), tool("Workflow", "t4"),
                   tool("Read", "t5", file_path="/w/alpha/x")),
         {"type": "progress", "timestamp": at(1, 10, 11), "sessionId": "A"}]
    sub = [assistant(at(1, 10, 2), "A", "s1", ALPHA, tool("Bash", "sc1", command="brunna test"), side=True),
           result(at(1, 10, 5), "A", "s2", ALPHA, "sc1", side=True)]
    prev = [human(at(-3, 9), "P", "p1", ALPHA, "last week"),
            assistant(at(-3, 9, 1), "P", "p2", ALPHA, tool("Agent", "pt1", prompt="y"))]
    wt = [human(at(2, 11), "W", "w1", WT, "in the worktree"),
          assistant(at(2, 11, 1), "W", "w2", WT, tool("Bash", "e1", command="npm run test:e2e")),
          result(at(2, 11, 4), "W", "w3", WT, "e1"),
          assistant(at(2, 11, 5), "W", "w4", WT, tool("Edit", "e2", file_path="/w/alpha/y")),
          human(at(2, 11, 6), "W", "w5", WT, "ok")]
    beta = [human(at(3, 14), "C", "b1", BETA, "hello"),
            assistant(at(3, 14, 1), "C", "b2", BETA, tool("Grep", "g1", pattern="x")),
            human(at(3, 14, 30), "C", "b3", BETA, "again")]
    slug = lambda p: p.replace("/", "-").replace(".", "-")   # noqa: E731
    return {f"{slug(ALPHA)}/A.jsonl": a,
            f"{slug(ALPHA)}/A-copy.jsonl": a[:3],               # a resumed session's copied history: same ids
            f"{slug(ALPHA)}/A/subagents/agent-1.jsonl": sub,
            f"{slug(ALPHA)}/P.jsonl": prev,
            f"{slug(WT)}/W.jsonl": wt,
            f"{slug(BETA)}/C.jsonl": beta}


def write_tree(root: pathlib.Path, tree: dict) -> None:
    for rel, recs in tree.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("".join(json.dumps(r) + "\n" for r in recs) + "not json\n")


def claude_home(tmp: pathlib.Path) -> pathlib.Path:
    """A home with the tree where Claude Code keeps it (~/.claude/projects); the sources find it by presence."""
    write_tree(tmp / ".claude" / "projects", claude_tree())
    return tmp


def sources_at(home: pathlib.Path, data: pathlib.Path | None = None):
    """Read the session sources under this home only (and this import folder)."""
    import contextlib
    import runtime_claude
    import runtime_ports
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(runtime_claude, "transcript_roots", lambda: (home / ".claude" / "projects",)))
    stack.enter_context(mock.patch.object(runtime_ports, "load", lambda *a, **k: runtime_claude))
    return stack


PROJECTS = {"alpha": [ALPHA], "beta": [BETA]}


def features(home: pathlib.Path, data: pathlib.Path | None = None) -> dict:
    """The three features' numbers on the sessions under this home."""
    out = {}
    wr = load("wr_pin", "weekly-reflect.py")
    mg = load("mg_pin", "check-memory-gaps.py")
    tw = load("tw_pin", "test-waiting-report.py")
    owner = lambda d: ("alpha", DOC) if d.startswith(ALPHA) else ("beta", {}) if d.startswith(BETA) else None  # noqa
    with sources_at(home, data), \
            mock.patch.object(wr.hublib, "project_paths", lambda: PROJECTS), \
            mock.patch.object(tw.hubconf, "projects", lambda: {"alpha": DOC, "beta": {}}), \
            mock.patch.object(tw.project_git, "project_at", owner):
        weeks = wr.scan(START, END)
        out["weekly"] = {k: {"prompts": dict(sorted(w.prompts.items())),
                             "sessions": {n: sorted(s) for n, s in sorted(w.sessions.items())},
                             "days": {n: sorted(d.isoformat() for d in s) for n, s in sorted(w.days.items())},
                             "times": sorted(t.astimezone(UTC).isoformat() for t in w.times),
                             "agents": w.agents, "workflows": w.workflows, "peer": w.peer_msgs,
                             "skills": dict(sorted(w.skills.items()))} for k, w in weeks.items()}
        out["memory_gaps"] = {n: mg.human_messages_since(p, START) for n, p in PROJECTS.items()}
        out["waiting"] = tw.scan(START, END)
    return json.loads(json.dumps(out, sort_keys=True, default=str))


class Pinned(unittest.TestCase):
    def test_claude_shaped_tree(self):
        with tempfile.TemporaryDirectory() as d:
            got = features(claude_home(pathlib.Path(d)))
        self.assertEqual(got, json.loads(GOLDEN.read_text()))


if __name__ == "__main__":
    if sys.argv[1:] == ["--update"]:
        with tempfile.TemporaryDirectory() as d:
            GOLDEN.write_text(json.dumps(features(claude_home(pathlib.Path(d))), indent=1, sort_keys=True) + "\n")
        print(GOLDEN)
    else:
        unittest.main()
