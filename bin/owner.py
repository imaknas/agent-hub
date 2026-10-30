#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""內建的最小 owner 端:Principal Handoff 0.3 的 principal 端(docs/design/tool-agnostic.md「owner 端可以換」)。

沒有個人助理的人用這個:hub.json `owner_side: builtin`(沒有 assistant.api 時的預設)。跟助理是同一個介面的
兩個實作——registrar(requester 端)照樣把〔owner〕待辦寫成 `raised` record、收回 `settled` 套用,不知道對面是誰。

  owner.py list [--project P]                 還開著的待辦(Acts)跟問題
  owner.py done | drop | already-done <key> [--note "…"]
                                              結案一個待辦:寫 `settled`(authority principal = owner 本人說的),
                                              registrar 今晚照 key 刪掉那一行
  owner.py answer <問題 id> "回答"             回答專案問的問題(回到發問的專案,librarian / 那個 session 套用)
  owner.py notify                             有新的待辦或問題就發通知(hub.json alerts 的管道);排程 job 用

結案、回答只有 owner 本人跑(這不是專案 session 的指令:權限規則只放行信箱指令,不放行這個)。owner 端是 assistant 時
這支只能 list——結案在助理那邊做,不然兩個 principal 端會打架(規範 R2:settlement 只屬於 principal 端)。
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
    print(_("no to-do {key} (owner.py list shows the open ones)", key=key), file=sys.stderr)
    return 1


def answer(qid: str, text: str) -> int:
    hb = _hubbus()
    c = hb.db()
    q = c.execute("SELECT * FROM events WHERE id=? AND type='message'", (qid,)).fetchone()
    if q is None or hb.payload(q).get("type") != "question":
        print(_("no question {id} (owner.py list shows the open ones)", id=qid), file=sys.stderr)
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
    """新出現的待辦、問題才通知(記住通知過哪些);沒有新的不發。發不出去記在 alert 的失敗紀錄,狀態頁標紅。"""
    import alert
    items = open_items()
    ids = {it.get("key") or it.get("id") for it in items}
    try:
        seen = set(json.loads(SEEN.read_text()))
    except (OSError, ValueError):
        seen = set()
    new = [it for it in items if (it.get("key") or it.get("id")) not in seen]
    if new:
        lines = [_("{owner}, {new} new things are waiting for you ({total} in all; `agenthub owner list`):",
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
    SEEN.write_text(json.dumps(sorted(x for x in ids if x)))   # 結案的拿掉,重開的會再通知一次
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
