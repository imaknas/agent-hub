"""E2 for mailbox-propose (docs/design/i18n.md): a mailbox assignment handled in a throwaway worktree, where the agent
writes only memory notes and puts the letters to send / close in its output (bin/propose_apply.py).

Cases are letters (real ones read from the bus without touching it, or synthetic but realistic ones) with a frozen copy
of the project they go to: a shallow clone of the project's HEAD and of its .agent-memory (no remotes, so nothing can be
pushed; the real folders are only read). The cases are listed in a spec file outside the repo (project names are the
instance's):

    [{"name": "...", "project": "...", "today": "YYYY-MM-DD",
      "letters": [{"id": "<bus id>"} | {"id", "source", "type", "text"[, "reply_to", "reply_text"]}]}]

    e2.py freeze mailbox-propose <spec.json>

Case: cases/mailbox-propose/<project>/<name>/ with args.json {project, today, letters, owner, assistant}, snapshot/ (the
project, with .agent-memory inside) and prompt.mailbox-propose.txt (the prompt rendered by the code at e2.OLD_REF).
A run copies the snapshot and calls propose_apply.run() on the copy, exactly as production does on the real folder;
what it adopted is kept in the run's files/.

Scored per run: valid, contract (no refused paths, replies and closes only for the case's letters, the 🔔 line exactly
when the owner is asked), the language of everything written; per pair: each letter closed or left open, replied or
not, the owner asked or not, a note written or not, the text similarity (reported, not gated).
"""
from __future__ import annotations

import datetime
import difflib
import importlib.util
import json
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types

import e2
from e2_reflect import arms_and_pairs

POINT = "mailbox-propose"
ALLOW = [".agent-memory/memory/**"]
hubconf = e2.hubconf


def _show(path: str) -> str:
    return subprocess.run(["git", "-C", str(hubconf.ROOT), "show", f"{e2.OLD_REF}:{path}"], capture_output=True,
                          text=True, check=True).stdout


def _load_src(src: str, name: str):
    f = pathlib.Path(tempfile.mkdtemp(prefix="e2-propose-")) / "propose_apply.py"
    f.write_text(src)
    spec = importlib.util.spec_from_file_location(name, f)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(letters: list[dict]):
    """letters as the bus rows letters_text() reads, with a stand-in for the bus (hb.payload, c.execute)"""
    rows = [{"id": x["id"], "source": x["source"], "ref": x.get("reply_to"),
             "payload": {"type": x["type"], "text": x["text"]}} for x in letters]
    prev = {x["reply_to"]: {"payload": {"text": x["reply_text"]}} for x in letters if x.get("reply_to")}
    hb = types.SimpleNamespace(payload=lambda r: r["payload"])
    c = types.SimpleNamespace(execute=lambda sql, args: types.SimpleNamespace(fetchone=lambda: prev.get(args[0])))
    return hb, c, rows


def old_prompt(letters: list[dict], today: str) -> str:
    """the prompt as _mailbox() at OLD_REF builds it (the same lines)"""
    pa = _load_src(_show("bin/propose_apply.py"), "propose_apply_old_e2")
    tpl = _show("templates/prompts/mailbox-propose.md")
    hb, c, rows = _rows(letters)
    import prompts
    return prompts.fill(tpl, letters=pa.letters_text(hb, c, rows), today=today)


def _bus_letter(c, mid: str) -> dict:
    r = c.execute("SELECT * FROM events WHERE id=? AND type='message'", (mid,)).fetchone()
    if r is None:
        sys.exit(f"e2: no letter {mid} on the bus")
    p = json.loads(r["payload"])
    x = {"id": r["id"], "source": r["source"], "type": p.get("type"), "text": p.get("text", "")}
    if r["ref"]:
        prev = c.execute("SELECT * FROM events WHERE id=?", (r["ref"],)).fetchone()
        if prev is not None:
            x.update(reply_to=r["ref"], reply_text=json.loads(prev["payload"]).get("text", ""))
    return x


def _clone(src: pathlib.Path, dst: pathlib.Path) -> None:
    """a shallow clone of src's HEAD with no remote (file:// so nothing is hard-linked to the real repo)"""
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{src}", str(dst)], check=True)
    subprocess.run(["git", "-C", str(dst), "remote", "remove", "origin"], check=True)


def freeze(a) -> list[pathlib.Path]:
    spec = json.loads(pathlib.Path(a.project).read_text())
    bus = hubconf.p("bus_dir", "~/.local/state/agent-hub-bus") / "bus.db"
    c = sqlite3.connect(f"file:{bus}?immutable=1", uri=True)   # read only, no lock: the real bus is never written
    c.row_factory = sqlite3.Row
    out = []
    for s in spec:
        letters = [_bus_letter(c, x["id"]) if set(x) == {"id"} else x for x in s["letters"]]
        ws = hubconf.workspace(s["project"])
        if not ws or not (ws / ".git").exists():
            sys.exit(f"e2: no workspace for {s['project']}")
        case = e2.root() / "cases" / POINT / s["project"] / s["name"]
        if case.exists():
            shutil.rmtree(case)
        case.mkdir(parents=True)
        snap = case / "snapshot"
        _clone(ws, snap)
        if (ws / ".agent-memory" / ".git").exists():
            _clone(ws / ".agent-memory", snap / ".agent-memory")
            with open(snap / ".git" / "info" / "exclude", "a") as f:
                f.write("\n.agent-memory\n")
        args = {"project": s["project"], "today": s["today"], "letters": letters, "owner": hubconf.owner_tag(),
                "assistant": hubconf.assistant_name(), "frozen": datetime.date.today().isoformat()}
        (case / "args.json").write_text(json.dumps(args, ensure_ascii=False, indent=1))
        (case / f"prompt.{POINT}.txt").write_text(old_prompt(letters, s["today"]))
        out.append(case)
    return out


def prompt(case: pathlib.Path, arm: str) -> str:
    if arm == "old":
        return (case / f"prompt.{POINT}.txt").read_text()
    a = json.loads((case / "args.json").read_text())
    pa = e2._load("propose_apply_e2", "propose_apply.py")
    return pa.mailbox_prompt(pa.letter_lines(a["letters"]), a["today"], owner=a["owner"], assistant=a["assistant"],
                             lang="en" if e2.base_arm(arm) == "new-en" else "zh-Hant")


RUN = """import json, pathlib, sys
sys.path.insert(0, sys.argv[1])
import propose_apply
r = propose_apply.run(sys.argv[2], pathlib.Path(sys.argv[3]), sys.argv[4], pathlib.Path(sys.argv[5]).read_text(),
                      json.loads(sys.argv[6]))
print(json.dumps(r, ensure_ascii=False))"""


def one(case: pathlib.Path, arm: str, i: int, run_dir: pathlib.Path, env: dict) -> dict:
    out = run_dir / case.parent.name / case.name / f"{arm}-{i}"
    if (out / "result.json").exists():
        return json.loads((out / "meta.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    (out / "prompt.txt").write_text(prompt(case, arm))
    project = json.loads((case / "args.json").read_text())["project"]
    with tempfile.TemporaryDirectory(prefix="e2-propose-") as tmp:
        ws = pathlib.Path(tmp) / project
        shutil.copytree(case / "snapshot", ws, symlinks=True)
        t0 = datetime.datetime.now()
        r = subprocess.run([str(e2.BIN / "py"), "-c", RUN, str(e2.BIN), project, str(ws), POINT, str(out / "prompt.txt"),
                            json.dumps(ALLOW)], cwd=tmp, env=env, capture_output=True, text=True, timeout=2400)
        secs = (datetime.datetime.now() - t0).total_seconds()
        try:
            res = json.loads(r.stdout)
        except ValueError:
            res = {}
        for rel in res.get("applied") or []:   # what the platform adopted, as it is now in the folder
            if (ws / rel).is_file():
                (out / "files" / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ws / rel, out / "files" / rel)
    (out / "result.json").write_text(r.stdout)
    (out / "stderr.txt").write_text(r.stderr[-20000:])
    err = res.get("error") or ("" if r.returncode == 0 else f"exit {r.returncode}")
    meta = {"case": e2.case_id(case), "arm": arm, "i": i, "exit": r.returncode, "secs": round(secs, 1), "model": None,
            "error": err or None,
            "rate_limited": bool(e2.RATE_LIMIT.search(str(err) + r.stderr[-2000:])) and not res.get("ok")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


# ---------- scoring ----------

def outcome(case: pathlib.Path, out: pathlib.Path) -> dict:
    a = json.loads((case / "args.json").read_text())
    ids = [x["id"] for x in a["letters"]]
    try:
        res = json.loads((out / "result.json").read_text())
    except (OSError, ValueError):
        res = {}
    so = res.get("output")
    r = {"valid": bool(res.get("ok")) and isinstance(so, dict), "error": res.get("error")}
    if not r["valid"]:
        return r
    replies = [x for x in so.get("replies") or [] if isinstance(x, dict)]
    done = [x for x in so.get("done") or [] if isinstance(x, dict)]
    asks = [x.get("text") for x in so.get("ask_owner") or [] if isinstance(x, dict) and x.get("text")]
    summary = str(so.get("summary") or "")
    notes = []
    for rel in res.get("applied") or []:
        f = out / "files" / rel
        before = case / "snapshot" / rel
        if f.is_file():
            notes.append(e2.added_text(before.read_text(errors="replace") if before.is_file() else "",
                                       f.read_text(errors="replace")))
    done_ids = {x.get("id") for x in done}
    replied = {x.get("reply_to"): x.get("type") for x in replies}
    bell = summary.lstrip().startswith("🔔")
    r.update({
        "closed": {i: i in done_ids for i in ids}, "replied": {i: replied.get(i) for i in ids},
        "asked": bool(asks), "note": bool(notes), "bell_ok": bell == bool(asks),
        "contract_ok": not res.get("refused") and done_ids <= set(ids) and set(replied) <= set(ids)
                       and all(t in ("info", "done", "question") for t in replied.values()),
        "text": "\n".join([*notes, *(str(x.get("text") or "") for x in replies), *asks,
                           *(str(x.get("note") or "") for x in done), summary])})
    r["lang"] = e2.language(r["text"])
    return r


def agree(x: dict, y: dict) -> dict:
    if not (x["valid"] and y["valid"]):
        return {}
    ids = list(x["closed"])
    return {"closed": round(sum(x["closed"][i] == y["closed"][i] for i in ids) / len(ids), 3),
            "replied": round(sum(bool(x["replied"][i]) == bool(y["replied"][i]) for i in ids) / len(ids), 3),
            "asked": float(x["asked"] == y["asked"]), "note": float(x["note"] == y["note"]),
            "text_sim": round(difflib.SequenceMatcher(None, x["text"], y["text"]).ratio(), 3)}


METRICS = ("closed", "replied", "asked", "note", "text_sim")


def rates(rs: list[dict]) -> dict:
    ok = [r for r in rs if r["valid"]]
    return {"contract_ok": e2.mean([r["contract_ok"] for r in ok]), "bell_ok": e2.mean([r["bell_ok"] for r in ok]),
            "closed": e2.mean([v for r in ok for v in r["closed"].values()]),
            "asked": e2.mean([r["asked"] for r in ok]), "note": e2.mean([r["note"] for r in ok])}


def report(run_dir: pathlib.Path) -> dict:
    by: dict[str, dict[str, list[dict]]] = {}
    for out in sorted(run_dir.glob("*/*/*-*")):
        if (out / "result.json").exists():
            cid = f"{out.parent.parent.name}/{out.parent.name}"
            by.setdefault(cid, {}).setdefault(out.name.rsplit("-", 1)[0], []).append(
                outcome(e2.root() / "cases" / POINT / cid, out))
    return arms_and_pairs(by, agree, METRICS, rates)

