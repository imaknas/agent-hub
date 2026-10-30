#!/bin/sh -c exec${IFS}"${0%/*}/../py"${IFS}"$0"${IFS}"$@"
"""E3 of docs/design/i18n.md: the rules every project agent reads, tested by running a headless agent in a throwaway
project with the old and the new rules, and checking what it did.

Each run builds a small project (git repo, `.agent-memory/` with AGENT.md, STATE.md and a note, the platform's managed
block in the runtime's instructions file) and runs the owner's request through bin/llm.py as the `agent-job` point (the
default permission mode of an unattended agent job). The agent uses the real `agenthub`, but the run has its own copy of
the config (the project registered there, the assistant's API pointed nowhere) and its own bus, so nothing is
delivered; the checks read that bus. (A fake `agenthub` first on PATH does not work: the agent's shell rebuilds PATH from
the login profile.) Scenario inputs are per content language in `locales/<lang>.e3.json`.

    e3.py run --arm old|new [--k 3] [--scenario <name>] [--run <id>] [--jobs 3]
    e3.py score <run> [--json]

Arms: `old` = templates/ at OLD_REF (before translation); `new` = templates/ as they are now.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

BIN = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BIN))
sys.path.insert(0, str(BIN / "evals"))
import commit_title  # noqa: E402
import e2  # noqa: E402
import hubconf  # noqa: E402
import runtime_ports  # noqa: E402

OLD_REF = "13b6788"
LANG = "zh-Hant"
PROJECT = "rates"
RULES = ("engineering", "conventional-commits")
GIT_ENV = {"GIT_AUTHOR_NAME": "owner", "GIT_AUTHOR_EMAIL": "owner@example.invalid",
           "GIT_COMMITTER_NAME": "owner", "GIT_COMMITTER_EMAIL": "owner@example.invalid"}

def data() -> dict:
    return json.loads((hubconf.ROOT / "locales" / f"{LANG}.e3.json").read_text())


def template(rel: str, arm: str) -> str:
    if arm == "new":
        return (hubconf.ROOT / rel).read_text()
    return subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{OLD_REF}:{rel}"], capture_output=True, text=True,
                          check=True).stdout


def managed_block(arm: str) -> str:
    """what sync-local-setup.sh writes into the instructions file: the template minus its title, plus the rules"""
    body = "\n".join(template("templates/agent-instructions.md", arm).splitlines()[2:]).strip("\n")
    for name in RULES:
        body += "\n\n" + template(f"templates/rules/{name}.md", arm).strip("\n")
    return f"<!-- agent-hub:begin -->\n{hubconf.fill_names(body)}\n<!-- agent-hub:end -->\n"


def build(proj: pathlib.Path, arm: str, scenario: str) -> None:
    d = data()
    proj.mkdir(parents=True)
    env = {**os.environ, **GIT_ENV, "GIT_CONFIG_GLOBAL": "/dev/null"}
    git = lambda *a: subprocess.run(["git", "-C", str(proj), *a], env=env, check=True, capture_output=True)  # noqa: E731
    (proj / "README.md").write_text(d["readme"])
    (proj / "app.py").write_text("print('rates')\n")
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("commit", "-q", "-m", "chore: initial commit")
    rt = runtime_ports.load()
    ignores = [".agent-memory/", *rt.local_ignores()]
    with (proj / ".git" / "info" / "exclude").open("a") as f:
        f.write("\n".join(ignores) + "\n")
    mem = proj / ".agent-memory"
    (mem / "memory").mkdir(parents=True)
    (mem / "AGENT.md").write_text(hubconf.fill_names(template("templates/AGENT.md", arm)))
    (mem / "STATE.md").write_text(d["state"].replace("{owner}", hubconf.owner_tag()))
    (mem / "memory" / "2026-10-29.md").write_text(d["note_old"])
    (mem / "RULES").write_text("\n".join(RULES) + "\n")
    rt.instructions_file(proj).write_text(managed_block(arm))


def snapshot(proj: pathlib.Path) -> dict[str, str]:
    out = {}
    for p in sorted(proj.rglob("*")):
        rel = p.relative_to(proj)
        if p.is_file() and ".git" not in rel.parts:
            out[str(rel)] = p.read_text(errors="replace")
    return out


def register(cfg: pathlib.Path, name: str, ws: pathlib.Path, extra: str = "") -> None:
    reg = cfg / (json.loads((cfg / e2.HUB_JSON).read_text()).get("registry_dir") or "projects")
    (reg / f"{name}.yaml").write_text(f"v: 1\nname: {name}\npaths:\n  workspace: {ws}\n{extra}")


def one(scenario: str, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / scenario / f"{arm}-{i}"
    if (out / "meta.json").exists():
        return json.loads((out / "meta.json").read_text())
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    d = data()
    with tempfile.TemporaryDirectory(prefix="e3-") as tmp:
        tmp = pathlib.Path(tmp).resolve()
        proj = tmp / PROJECT
        build(proj, arm, scenario)
        before = snapshot(proj)
        # this run's own config and bus: the throwaway project and the letter's sender are registered only there
        cfg = out / "config"
        shutil.copytree(env["AGENT_HUB_CONFIG"], cfg)
        hub = json.loads((cfg / e2.HUB_JSON).read_text())
        hub["bus_dir"] = str(out / "bus")
        (cfg / e2.HUB_JSON).write_text(json.dumps(hub, ensure_ascii=False, indent=2))
        register(cfg, PROJECT, proj, "commits: conventional\n")
        (tmp / "dashboard").mkdir()
        register(cfg, "dashboard", tmp / "dashboard")
        renv = {**env, **GIT_ENV, "AGENT_HUB_CONFIG": str(cfg), "AGENT_HUB_STATE": str(out / "state")}
        hb = [str(BIN / "py"), str(BIN / "hubbus")]
        subprocess.run([str(BIN / "py"), str(BIN / "registry.py"), "check"], env=renv, capture_output=True, check=True)
        letter = None
        if scenario == "letter":
            r = subprocess.run([*hb, "send", "--from", "dashboard", "--to", PROJECT, "--type", "request",
                                "--text", d["letter_text"]], env=renv, capture_output=True, text=True, check=True)
            letter = re.search(r"\bm\d{8}-[0-9a-f]{4}\b", r.stdout + r.stderr).group(0)
        (out / "prompt.txt").write_text(d["scenarios"][scenario]["prompt"])
        t0 = datetime.datetime.now()
        r = subprocess.run([str(BIN / "py"), str(BIN / "llm.py"), "run", "agent-job", "--prompt-file", str(out / "prompt.txt")],
                           cwd=proj, env=renv, capture_output=True, text=True, timeout=3600)
        secs = (datetime.datetime.now() - t0).total_seconds()
        after = snapshot(proj)
        log = subprocess.run(["git", "-C", str(proj), "log", "--format=%s"], capture_output=True, text=True).stdout
    (out / "result.json").write_text(r.stdout)
    (out / "stderr.txt").write_text(r.stderr[-20000:])
    (out / "files.json").write_text(json.dumps({"before": before, "after": after, "git_log": log.splitlines(),
                                                "letter": letter}, ensure_ascii=False, indent=1))
    try:
        obj = json.loads(r.stdout)
    except ValueError:
        obj = {}
    meta = {"scenario": scenario, "arm": arm, "i": i, "exit": r.returncode, "secs": round(secs, 1),
            "model": obj.get("llm_model") or obj.get("model"), "error": obj.get("llm_error"),
            "rate_limited": r.returncode != 0 and bool(e2.RATE_LIMIT.search((obj.get("llm_error") or "") + r.stderr[-2000:]))}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ---------- checks ----------

def bus(out: pathlib.Path) -> list[dict]:
    """this run's mailbox events: {type, source, to, ref, payload}"""
    db = out / "bus" / "bus.db"
    if not db.exists():
        return []
    c = sqlite3.connect(db)
    try:
        rows = c.execute("SELECT type, source, to_project, ref, payload FROM events ORDER BY seq").fetchall()
    finally:
        c.close()
    return [{"type": t, "source": s, "to": to, "ref": ref, "payload": json.loads(p or "{}")} for t, s, to, ref, p in rows]


def note_text(files: dict) -> str:
    """what the agent added to memory notes"""
    b, a = files["before"], files["after"]
    return "\n".join(e2.added_text(b.get(k, ""), v) for k, v in a.items()
                     if k.startswith(".agent-memory/memory/") and v != b.get(k))


def check(scenario: str, out: pathlib.Path) -> dict:
    files = json.loads((out / "files.json").read_text())
    expect = data()["scenarios"][scenario]["expect"]
    note = note_text(files)
    evs = bus(out)
    sends = [e for e in evs if e["type"] == "message" and e["source"] == PROJECT]
    text = lambda e: str(e["payload"].get("text") or "")  # noqa: E731
    r: dict = {"ran": json.loads((out / "meta.json").read_text())["exit"] == 0}
    lang = e2.language(note) if note.strip() else None
    if scenario == "note":
        r["note_written"] = bool(note.strip())
        r["note_has_fact"] = all(x in note for x in expect)
        r["code_untouched"] = files["after"].get("app.py") == files["before"].get("app.py")
    elif scenario == "owner_todo":
        st = files["after"].get(".agent-memory/STATE.md", "")
        items = e2.todo_items(st) or set()
        r["contract_ok"] = not e2.state_check(st)
        r["owner_item"] = any(o == hubconf.owner_tag() and all(x in t for x in expect) for o, t in items)
        # the rules give two routes: a [owner] line in STATE.md (registered at night) or `send --type task` right away
        tasks = [e for e in evs if e["type"] == "record" and e["source"] == PROJECT
                 and (e["payload"].get("record") or {}).get("type") == "raised"]
        r["owner_task_raised"] = any(all(x in str(e["payload"]["record"].get("terms")) for x in expect) for e in tasks)
        r["owner_registered"] = r["owner_item"] or r["owner_task_raised"]
        r["kept_others"] = len(items) >= len(e2.todo_items(files["before"][".agent-memory/STATE.md"]) or ())
    elif scenario == "ask_hub":
        to_hub = [e for e in sends if e["to"] == "agent-hub"]
        r["sent_to_hub"] = bool(to_hub)
        r["actionable_type"] = any(e["payload"].get("type") in ("request", "question") for e in to_hub)
        r["has_fact"] = any(all(x in text(e) for x in expect) for e in to_hub)
    elif scenario == "letter":
        letter = files["letter"]
        replies = [e for e in sends if e["ref"] == letter]
        r["replied"] = bool(replies)
        r["reply_has_fact"] = any(all(x in text(e) for x in expect) for e in replies)
        r["closed"] = any(e["type"] == "message.done" and e["ref"] == letter for e in evs) or \
            any(e["payload"].get("type") == "done" for e in replies)
    elif scenario == "commit":
        new = files["git_log"][:max(0, len(files["git_log"]) - 1)]
        r["committed"] = bool(new)
        r["title_ok"] = bool(new) and all(commit_title.problem(t, final=True) is None for t in new)
        r["fixed"] = all(x in files["after"].get("README.md", "") for x in expect) and "teh" not in files["after"].get("README.md", "")
        r["note_after_commit"] = bool(note.strip())
    if lang:
        r["note_cjk_share"] = lang["cjk_share"]
        r["note_simplified"] = bool(lang["simplified"])
    return r


def cmd_run(a) -> int:
    scen = [a.scenario] if a.scenario else list(data()["scenarios"])
    run_id = a.run or f"{datetime.datetime.now():%Y%m%d-%H%M}-e3"
    run_dir = e2.root() / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(json.dumps({"point": "e3", "old_ref": OLD_REF, "lang": LANG}, indent=1))
    env = e2.run_env(run_dir)
    print(f"e3: arm={a.arm} k={a.k} scenarios={scen} → {run_dir}")
    stop = False
    with concurrent.futures.ThreadPoolExecutor(a.jobs) as pool:
        futs = [pool.submit(one, s, a.arm, i, run_dir, env) for i in range(a.k) for s in scen]
        for f in concurrent.futures.as_completed(futs):
            m = f.result()
            print(f"  {m['scenario']} {m['arm']}-{m['i']}: exit {m['exit']} {m['secs']}s {m['model'] or ''} {m['error'] or ''}")
            if m["rate_limited"] and not stop:
                stop = True
                print("e3: rate limited, cancelling the rest (rerun the same command to continue)")
                for g in futs:
                    g.cancel()
    print(run_id)
    return 1 if stop else 0


def cmd_score(a) -> int:
    run_dir = e2.root() / "runs" / a.run
    report: dict = {}
    for out in sorted(run_dir.glob("*/*-*")):
        if not (out / "meta.json").exists():
            continue
        scenario, arm = out.parent.name, out.name.rsplit("-", 1)[0]
        report.setdefault(scenario, {}).setdefault(arm, []).append(check(scenario, out))
    summary = {s: {arm: {k: e2.mean([r.get(k) for r in rs]) for k in sorted({k for r in rs for k in r})}
                   for arm, rs in arms.items()} for s, arms in report.items()}
    (run_dir / "score.json").write_text(json.dumps({"runs": report, "summary": summary}, ensure_ascii=False, indent=1))
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    else:
        for s, arms in summary.items():
            for arm, m in arms.items():
                print(f"{s} {arm} (n={len(report[s][arm])}): " + ", ".join(f"{k}={v}" for k, v in m.items()))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="e3.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", choices=["old", "new"], required=True)
    r.add_argument("--k", type=int, default=3)
    r.add_argument("--scenario")
    r.add_argument("--run")
    r.add_argument("--jobs", type=int, default=3)
    s = sub.add_parser("score")
    s.add_argument("run")
    s.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_score(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
