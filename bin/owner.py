#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The built-in minimal owner side: the principal side of Principal Handoff 0.3 (docs/design/tool-agnostic.md,
"the owner side can be swapped").

For people without a personal assistant: hub.json `owner_side: builtin` (the default when there is no assistant.api).
It and the assistant are two implementations of the same interface: the registrar (requester side) still writes
〔owner〕 to-dos as `raised` records and applies the `settled` ones it gets back, without knowing who's on the other
side.

  owner.py list [--project P]                 open to-dos (Acts) and questions
  owner.py done | drop | already-done <key> [--note "…"]
                                              close a to-do: write `settled` (authority principal = the owner said so),
                                              the registrar deletes that line by key tonight
  owner.py answer <question id> "answer"      answer a question a project asked (goes back to the asking project; the
                                              librarian / that session applies it)
  owner.py notify                             send a notification when there are new to-dos or questions (the channels
                                              in hub.json alerts); used by a scheduled job

Only the owner runs closing and answering (this is not a command for project sessions: the permission rules only let
the mailbox commands through, not this one). When the owner side is the assistant this script can only list: closing
is done on the assistant's side, otherwise two principal sides would fight (spec R2: settlement belongs only to the
principal side).
"""
from __future__ import annotations

import argparse
import datetime
import importlib.machinery
import importlib.util
import json
import pathlib
import sys

BIN = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))
import hubconf  # noqa: E402
import i18n  # noqa: E402

_ = i18n.domain(__file__)

EXT_ACTS = "acts/0.3"
OUTCOMES = {"done": "done", "drop": "dropped", "already-done": "already_done"}
SEEN = hubconf.STATE / "owner-notified.json"


def _hubbus():
    loader = importlib.machinery.SourceFileLoader("hubbus_owner", str(BIN / "hubbus"))
    spec = importlib.util.spec_from_loader("hubbus_owner", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


def _registrar():
    spec = importlib.util.spec_from_file_location("registrar_owner", BIN / "registrar.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def projects() -> list[str]:
    return sorted({*hubconf.roster(), hubconf.hub_project()})


def open_items(only: str | None = None) -> list[dict]:
    """[{kind: task, project, key, text} | {kind: question, project, id, text}]"""
    reg = _registrar()
    out = []
    for p in projects():
        if only and p != only:
            continue
        led = reg.Ledger(p)
        for k in led.open():
            text = led.terms(k)[0]
            out.append({"kind": "task", "project": p, "key": k, "text": text or ""})
    hb = _hubbus()
    c = hb.db()
    for r in hb.open_questions(c, only):
        out.append({"kind": "question", "project": hb.canon(r["source"]), "id": r["id"],
                    "text": hb.payload(r).get("text") or ""})
    return out


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def settle(key: str, outcome: str, note: str | None) -> int:
    reg = _registrar()
    for p in projects():
        led = reg.Ledger(p)
        if key not in led.known():
            continue
        if led.withdrawn(key):
            print(_("{key} was withdrawn by {project}; nothing to settle", key=key, project=p), file=sys.stderr)
            return 1
        rev = led.terms(key)[1]
        heads = [r["id"] for r in led.settle.get(key, [])
                 if r["id"] == (reg.current(led.settle.get(key, [])) or {}).get("id")]
        n = len(led.settle.get(key, [])) + 1
        rec = {"ph": "0.3", "ext": EXT_ACTS, "id": f"s:{key}:{n}", "type": "settled", "origin": "principal",
               "by": hubconf.party("principal_side"), "key": key, "project": p, "ts": _now(), "outcome": outcome,
               "terms_rev": rev or 1, "authority": {"kind": "principal", "decider": hubconf.party("principal")}}
        if heads:
            rec["supersedes"] = heads
        if note:
            rec["note"] = note
        hb = _hubbus()
        errs = hb.record_errors(rec)
        if errs:
            print(_("record invalid: {errors}", errors="; ".join(errs)), file=sys.stderr)
            return 1
        c = hb.db()
        c.execute("BEGIN IMMEDIATE")
        eid, new = hb.publish_record(c, rec, hb.OWNER_ADDRESS, p)
        c.execute("COMMIT")
        print(_("{project}:{key} → {outcome} ({event}{again}); registrar updates STATE.md by key tonight",
                project=p, key=key, outcome=outcome, event=eid, again="" if new else _(", sent before")))
        return 0
    print(_("no to-do with key {key} (`agenthub owner list` shows the open ones)", key=key), file=sys.stderr)
    return 1


def answer(qid: str, text: str) -> int:
    hb = _hubbus()
    c = hb.db()
    q = c.execute("SELECT * FROM events WHERE id=? AND type='message'", (qid,)).fetchone()
    if q is None or hb.payload(q).get("type") != "question":
        print(_("no question with id {id} (`agenthub owner list` shows the open ones)", id=qid), file=sys.stderr)
        return 1
    proj = hb.canon(q["source"])
    owner = hubconf.owner_tag()
    p = {"v": 1, "type": "handoff", "text": f"{owner} 回答:{text}", "effect": "answers",
         "handoff": {"effect": "answers", "fact": text, "quote": text, "reply_to": qid, "item": hb.payload(q).get("text"),
                     "why": f"{owner} 在內建的 owner 端直接回答"}}
    mid = f"owner-answer-{qid}"
    c.execute("BEGIN IMMEDIATE")
    hb.publish(c, mid, "message", hb.OWNER_ADDRESS, proj, qid, p)
    c.execute("COMMIT")
    print(_("answer sent to {project} ({id}); the session that asked, or tonight's librarian, applies it",
            project=proj, id=mid))
    return 0


def notify() -> int:
    """Notify only about newly appeared to-dos and questions (remembers which were notified); nothing new, nothing sent.
    If it can't be sent, it's recorded in alert's failure record and the status page shows red."""
    import alert
    items = open_items()
    ids = {it.get("key") or it.get("id") for it in items}
    try:
        seen = set(json.loads(SEEN.read_text()))
    except (OSError, ValueError):
        seen = set()
    new = [it for it in items if (it.get("key") or it.get("id")) not in seen]
    if new:
        lines = [_("{owner}, {new} new {new:item is|items are} waiting for you ({total} in all; `agenthub owner list`):",
                   owner=hubconf.owner_tag(), new=len(new), total=len(items))]
        lines += [_("- {mark} {project}: {text}", mark="❓" if it["kind"] == "question" else "☐", project=it["project"],
                    text=it["text"][:120]) for it in new[:20]]
        if alert.send("\n".join(lines)):
            alert.clear_failure("owner-notify")
        else:
            alert.record_failure("owner-notify", _("the owner's to-do notification could not be sent "
                                                   "(no working channel in hub.json alerts?)"))
            print(_("the notification could not be sent"), file=sys.stderr)
            return 1
    SEEN.parent.mkdir(parents=True, exist_ok=True)
    SEEN.write_text(json.dumps(sorted(x for x in ids if x)))   # closed ones drop out; reopened ones are notified again
    print(_("owner side: {open} open, {new} new", open=len(items), new=len(new)))
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="owner.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list").add_argument("--project")
    for name in OUTCOMES:
        s = sub.add_parser(name)
        s.add_argument("key")
        s.add_argument("--note")
    s = sub.add_parser("answer")
    s.add_argument("id")
    s.add_argument("text")
    sub.add_parser("notify")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        items = open_items(a.project)
        for it in items:
            ref = it.get("key") or it.get("id")
            print(f"{'❓' if it['kind'] == 'question' else '☐'} {it['project']:<24} {ref}  {it['text'][:100]}")
        print(_("({n} open)", n=len(items)))
        return 0
    if hubconf.owner_side() != "builtin":
        print(_("the owner side is {assistant} (hub.json owner_side: assistant): settle and answer there; "
                "here you can only list (two principal sides would conflict)",
                assistant=hubconf.get("assistant.name") or _("the assistant")), file=sys.stderr)
        return 2
    if a.cmd in OUTCOMES:
        return settle(a.key, OUTCOMES[a.cmd], a.note)
    if a.cmd == "answer":
        return answer(a.id, a.text)
    return notify()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
