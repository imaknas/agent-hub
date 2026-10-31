"""E2 for lease-handover (docs/design/i18n.md): the structured handover written when a lease is detached.

In production the handover prompt is said to the lease's own session, resumed. The eval does not resume anything: it
would need a conversation record in the runtime's own store, which is real data (weekly reflect, the memory-gap check
and mailbox routing read it), and the runtime has no throwaway store. So a case is a conversation (synthetic but
realistic), and each run gives it in the prompt, ahead of the handover prompt; the judgment is the same (the handover
of that conversation), and both arms get the conversation the same way, so only the handover prompt differs. The call is
the point's own (bin/llm.py run lease-handover: its model, no tools, its schema), in an empty folder.

The cases are listed in a spec file outside the repo:

    [{"name": "...", "project": "...", "turns": [["user" | "assistant", "text"], ...]}]

    e2.py freeze lease-handover <spec.json>

Case: cases/lease-handover/<project>/<name>/ with args.json {project, turns, owner} and prompt.lease-handover.txt (the
conversation + the handover prompt at e2.OLD_REF).

Scored per run: valid, the language of every text; per pair: the decisions, open questions and owner tasks each paired
by character-bigram Jaccard (e2.match), whether each list is empty in both or neither, the due dates of paired tasks, the
text similarity of summary + notes (reported, not gated).
"""
from __future__ import annotations

import datetime
import difflib
import importlib.util
import json
import pathlib
import subprocess
import tempfile

import e2
from e2_reflect import arms_and_pairs, record

POINT = "lease-handover"
hubconf = e2.hubconf
LISTS = ("decisions", "open_questions", "tasks_for_owner")


def conversation(turns: list[list[str]]) -> str:
    """the conversation as both arms get it, ahead of the handover prompt"""
    body = "\n\n".join(f"[{role}]\n{text}" for role, text in turns)
    return f"<conversation>\n{body}\n</conversation>\n\n"


def _runner(src: str, name: str):
    f = pathlib.Path(tempfile.mkdtemp(prefix="e2-handover-")) / "lease_runner.py"
    f.write_text(src)
    spec = importlib.util.spec_from_file_location(name, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def old_handover() -> str:
    """HANDOVER_PROMPT of bin/lease-runner.py at e2.OLD_REF"""
    src = subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{e2.OLD_REF}:bin/lease-runner.py"],
                         capture_output=True, text=True, check=True).stdout
    return _runner(src, "lease_runner_old_e2").HANDOVER_PROMPT


def freeze(a) -> list[pathlib.Path]:
    old = old_handover()
    out = []
    for s in json.loads(pathlib.Path(a.project).read_text()):
        case = e2.root() / "cases" / POINT / s["project"] / s["name"]
        case.mkdir(parents=True, exist_ok=True)
        args = {"project": s["project"], "turns": s["turns"], "owner": hubconf.owner_tag(),
                "frozen": datetime.date.today().isoformat()}
        (case / "args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1))
        (case / f"prompt.{POINT}.txt").write_text(conversation(s["turns"]) + old)
        out.append(case)
    return out


def prompt(case: pathlib.Path, arm: str) -> str:
    if arm == "old":
        return (case / f"prompt.{POINT}.txt").read_text()
    a = json.loads((case / "args.json").read_text())
    lr = _runner((e2.BIN / "lease-runner.py").read_text(), "lease_runner_e2")
    return conversation(a["turns"]) + lr.handover_prompt(owner=a["owner"],
                                                         lang="en" if e2.base_arm(arm) == "new-en" else "zh-Hant")


def one(case: pathlib.Path, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / case.parent.name / case.name / f"{arm}-{i}"
    if (out / "result.json").exists():
        return json.loads((out / "meta.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(prompt(case, arm))
    with tempfile.TemporaryDirectory(prefix="e2-") as tmp:
        t0 = datetime.datetime.now()
        # what production adds after `--` besides resuming the session and the project's deny settings (no tools here)
        r = subprocess.run([str(e2.BIN / "py"), str(e2.BIN / "llm.py"), "run", POINT, "--cwd", tmp, "--prompt-file",
                            str(out / "prompt.txt"), "--", "--strict-mcp-config"],
                           cwd=tmp, env=env, capture_output=True, text=True, timeout=900)
        secs = (datetime.datetime.now() - t0).total_seconds()
    return record(case, arm, i, out, r, secs)


# ---------- scoring ----------

def texts(so: dict) -> list[str]:
    return [str(so.get("summary") or ""), str(so.get("notes_markdown") or ""),
            *(f"{d.get('what', '')} {d.get('why', '')}" for d in so.get("decisions") or [] if isinstance(d, dict)),
            *(str(q) for q in so.get("open_questions") or []),
            *(str(t.get("text", "")) for t in so.get("tasks_for_owner") or [] if isinstance(t, dict))]


def outcome(out: pathlib.Path) -> dict:
    try:
        obj = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        obj = {}
    so = obj.get("structured_output")
    r = {"valid": isinstance(so, dict) and not obj.get("is_error"), "error": obj.get("llm_error")}
    if not r["valid"]:
        return r
    r["lists"] = {
        "decisions": {("", str(d.get("what", ""))) for d in so.get("decisions") or [] if isinstance(d, dict)},
        "open_questions": {("", str(q)) for q in so.get("open_questions") or []},
        "tasks_for_owner": {(str(t.get("due") or ""), str(t.get("text", ""))) for t in so.get("tasks_for_owner") or []
                            if isinstance(t, dict)}}
    r["text"] = "\n".join(texts(so))
    r["prose"] = str(so.get("summary") or "") + "\n" + str(so.get("notes_markdown") or "")
    r["lang"] = e2.language(r["text"])
    return r


def agree(x: dict, y: dict) -> dict:
    if not (x["valid"] and y["valid"]):
        return {}
    row = {}
    for k in LISTS:
        m = e2.match(x["lists"][k], y["lists"][k]) or {}
        row[k] = m.get("items")
        if k == "tasks_for_owner":
            row["due"] = m.get("owner")   # e2.match's "owner" is the first tuple element: here the due date
        row[f"{k}_empty"] = float(bool(x["lists"][k]) == bool(y["lists"][k]))
    row["text_sim"] = round(difflib.SequenceMatcher(None, x["prose"], y["prose"]).ratio(), 3)
    return row


METRICS = ("decisions", "open_questions", "tasks_for_owner", "due", "decisions_empty", "open_questions_empty",
           "tasks_for_owner_empty", "text_sim")


def counts(rs: list[dict]) -> dict:
    ok = [r for r in rs if r["valid"]]
    return {f"n_{k}": e2.mean([len(r["lists"][k]) for r in ok]) for k in LISTS}


def report(run_dir: pathlib.Path) -> dict:
    by: dict[str, dict[str, list[dict]]] = {}
    for out in sorted(run_dir.glob("*/*/*-*")):
        if (out / "result.json").exists():
            by.setdefault(f"{out.parent.parent.name}/{out.parent.name}", {}).setdefault(
                out.name.rsplit("-", 1)[0], []).append(outcome(out))
    return arms_and_pairs(by, agree, METRICS, counts)
