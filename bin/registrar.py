#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""The Principal Handoff Acts registrar (agent-ops-specs acts-markdown/SPEC.md §2, 0.5): the things in each project's
STATE.md to-do section that only the owner can do <-> records on the bus (a nightly job after the librarian, zero LLM).

The document is read and written by bin/acts_md.py (the Acts Markdown binding): either token set, also mixed in one
file (contracts/state-todo.v2.md, bin/todo_tokens.py); what the registrar writes (a key stamp, a re-added line, the
empty marker, a missing section's heading) is in the set of the content language, and it touches no other line.

**Bound keys** (spec §2): the keys this registrar raised from lines (its `raised` on the bus). **Known keys** (spec §2 step 3): items a project raised itself (brunna send --type task, a
lease handover), never bound. Their need records go out on the bus under the project like the registrar's, and their
settlements come back to the project, so the registrar holds them all (the deployment's duty in spec §2). Steps 1-5
leave them alone except striking a line whose text is the item's latest terms after a settlement of theirs; a line
carrying one is not raised, relinked or restamped, and is warned about every night until an agent fixes it.

**A requester** (core §12): the records of each item, in whatever version they came (0.3 to 0.6), are validated by
their own version's rules, held in their 0.6 form, deduplicated and folded by bin/ph_records.py; invalid ones are
ignored and named in the log, and those of a version brunna does not read are left alone.

For each project (the STATE.md of its memory copy, plus the platform's own openclaw-migration-status.md):
  0. Read: a document with an unclosed code fence or HTML comment is left alone (nothing sent, nothing written).
  1. Apply settled / reopened records from the principal side not yet done (hubbus records --direction in --pending):
     settled -> strike the owner lines carrying the key, unless the settlement is stale against the latest terms (the
     terms digest included) or the line was edited since (then, for a bound key, the amendment is sent; nothing is
     struck); reopened of a bound key -> re-add the line in the first section from the latest terms (restore_text only
     without them). note and related are the librarian's.
  2. Relink: an active bound key no line mentions, whose latest text equals exactly one keyless (or wrongly keyed, not
     known) owner line of the first section -> that line gets the key back instead of being raised.
  3. Raise: the remaining such lines of the first section -> key task-<project>-<sha1(normalised text)[:10]>,
     `raised` (id r:<key>) with the normalised text, stamped only after the bus accepts it. Lines in later sections are
     not raised (an archive is not a new request); a text that repeats a bound key is not raised and is warned about.
  4. Amend: an active bound key whose lines in the first section all have one text other than the latest terms ->
     `amended` (id a:<key>:<rev>, terms_rev + 1, superseding the current need record); lines that disagree: warned.
  5. Reconcile active bound keys with no line in the first section: a ticked owner box carries it -> claimed done;
     its marker is still somewhere (displaced: relabelled, moved, archived) -> no claim, a note once per open period
     (id d:<key>:r<n>) saying where; vanished but a very similar line exists -> warn once (again only after it was
     found, or that line has gone too); vanished -> claimed done (evidence.basis: the review-log entry that deleted
     the line). Claim ids c:<key>:r<times reopened>. A withdrawn item gets no claim.
  6. If anything changed, commit + push (memory copies through git-safe-push; the platform's file through
     commit-hub-files.sh), and only after that succeeds `hubbus done` the applied records; on failure, wait for the
     next night.

Records: reads acts 0.3 to 0.6, sends the version {settings} principal_handoff.send_version names (bin/ph_wire.py).

Only records count: v1's task letters and handoffs are not read. The items that existed only as v1 task letters were
given need records under their keys once, by `registrar.py migrate-v1` (context migration-v1; removed with v1, in git
history; docs/design/principal-handoff-0.3-migration.md).

Usage: registrar.py [--dry-run]
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import shlex
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import acts_md  # noqa: E402
import hubconf  # noqa: E402
import i18n  # noqa: E402
import ph_records  # noqa: E402
import ph_wire  # noqa: E402
import todo_tokens  # noqa: E402

_ = i18n.domain(__file__)
_c = i18n.domain(__file__, content=True)   # texts in the records the assistant reads: content

HUB = hubconf.ROOT
COPIES = hubconf.p("memory_copies")
HUBBUS = [sys.executable, str(HUB / "bin" / "hubbus")]
WARNED = hubconf.STATE / "registrar-warned.json"   # keys already warned about (very similar lines): warn only once
REVIEW_LOG = hubconf.data_file("review-log.md")
TAG = hubconf.owner_tag()
REGISTRAR = hubconf.party("registrar")
REGISTRARS = hubconf.party_ids("registrar")   # either spelling raised it (docs/design/rename-brunna.md)
READ = acts_md.profiles(TAG)   # both token sets
REASON = todo_tokens.patterns(TAG).reason   # the review-log's reason line, either set
DRY = "--dry-run" in sys.argv


def writer() -> acts_md.Profile:
    """the content language's set: what the registrar writes"""
    return acts_md.profile(hubconf.lang_content(), TAG)


def log(msg: str) -> None:
    print(f"[registrar] {msg}", flush=True)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(text: str) -> str:
    return " ".join(text.split())


def task_key(project: str, text: str) -> str:
    return f"task-{project}-{hashlib.sha1(norm(text).encode()).hexdigest()[:10]}"


def hubbus(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([*HUBBUS, *args], input=stdin, capture_output=True, text=True, timeout=60)


# --- records on the bus ---

def current(recs: list[dict]) -> dict | None:
    """core §6: of one register's (held, 0.6) records, the latest of those no other supersedes (equal ts: the greater
    id)."""
    return ph_records.register(recs)[0]


class Ledger:
    """One project's Acts records on the bus, grouped by key: the need register (what this project sent, the
    registrar's and the project's own) and the settlement register (what the principal side sent), each record
    held in its 0.6 form, validated and deduplicated (bin/ph_records.py). Records filed under a name the project had before
    a rename are its records too (hubbus records), and what is sent about such an item keeps that name (filed)."""

    def __init__(self, project: str):
        r = hubbus("records", "--project", project)
        try:
            events = json.loads(r.stdout or "[]")
        except ValueError:
            raise SystemExit(_("cannot read hubbus records (exit {code}): {stderr}", code=r.returncode,
                               stderr=r.stderr.strip()[-200:]))
        self.project = project
        self.need, self.settle, self.pending, self.raised_by, self.ignored, self.filed_as = {}, {}, [], {}, [], {}
        self.sent: set[tuple] = set()   # (origin, type, id) of every Acts record this project has sent
        held: dict[str, list[dict]] = {}
        for e in events:
            rec = e["record"]
            if ph_wire.family(rec.get("ext")) != "acts":   # every version read
                continue
            if e["direction"] == "out":
                self.sent.add((rec.get("origin"), rec.get("type"), rec.get("id")))
            if (e["direction"] == "out" and rec.get("type") in ph_records.NEED) or \
                    (e["direction"] == "in" and rec.get("type") in ph_records.SETTLEMENT):
                held.setdefault(str(rec.get("key")), []).append(rec)
                if e["direction"] == "in" and not e["done"]:
                    self.pending.append(e)
        cfg = ph_records.config()
        for key, recs in held.items():
            valid, ignored = ph_records.received(recs, cfg)
            self.ignored += ignored
            for rec in sorted(valid, key=ph_records.when):
                (self.need if rec["type"] in ph_records.NEED else self.settle).setdefault(key, []).append(rec)
                if rec["type"] == "raised":
                    self.raised_by.setdefault(key, rec.get("by"))
            if key in self.need:   # the project field of the item, from its need records (core §4: ext, project, key)
                self.filed_as[key] = self.need[key][0]["project"]
            elif key in self.settle:
                self.filed_as[key] = self.settle[key][0]["project"]

    def filed(self, key: str) -> str:
        """the project the item is filed under: the name it was raised under (a name from before a rename too)"""
        return self.filed_as.get(key, self.project)

    def latest_need(self, key: str) -> dict | None:
        """the need record with the latest terms (the highest terms_rev)"""
        return ph_records.latest_terms(self.need.get(key, []))

    def terms(self, key: str) -> tuple[str | None, int, str | None]:
        """(latest terms text, latest terms_rev, current requirement record id)."""
        latest, cur = self.latest_need(key), current(self.need.get(key, []))
        if latest is None:
            return None, 0, None
        return latest["terms"]["text"], latest["terms_rev"], cur["id"] if cur else None

    def known(self) -> set[str]:
        return set(self.need)

    def settled(self, key: str) -> bool:
        cur = current(self.settle.get(key, []))
        return cur is not None and cur["type"] == "settled"

    def withdrawn(self, key: str) -> bool:
        cur = current(self.need.get(key, []))
        return cur is not None and cur["type"] == "withdrawn"

    def reopens(self, key: str) -> int:
        return sum(1 for r in self.settle.get(key, []) if r["type"] == "reopened")

    def open(self) -> list[str]:
        return sorted(k for k in self.known() if not self.settled(k) and not self.withdrawn(k))

    def bound(self, key: str) -> bool:
        """Raised from a line of the file by this registrar (an item whose raised is not held counts as its own)."""
        return self.raised_by.get(key, REGISTRAR) in REGISTRARS

    def bound_keys(self) -> set[str]:
        """spec §2's bound keys"""
        return {k for k in self.known() if self.bound(k)}

    def known_keys(self) -> set[str]:
        """spec §2 step 3's known keys: not bound, a need record held (an item the project raised itself)"""
        return {k for k in self.need if not self.bound(k)}

    def latest(self, key: str) -> dict | None:
        """the latest terms of a bound or known key as the binding compares them ({text, terms_rev, terms_digest}, the
        digest as 0.5 computes it); None for a key with no terms held"""
        text, rev, _cur = self.terms(key)
        if text is None:
            return None
        need = self.latest_need(key)
        return {"text": text, "terms_rev": rev, **({"terms_digest": ph_wire.terms_digest(need)} if need else {})}


def publish(recs: list[dict], sent=frozenset()) -> dict[str, str]:
    """Send; returns {record id: status}. A record whose id was already sent (Ledger.sent: a claim or note re-created
    each night until the item is settled, a raise whose stamp did not get committed) is not sent again: core §10, an
    id names one record and a retransmission carries it unchanged, and this one would differ at least in ts. It is a
    duplicate, as the bus answered when it kept the first of an id."""
    already = {r["id"]: "duplicate" for r in recs if (r["origin"], r["type"], r["id"]) in sent}
    recs = [r for r in recs if r["id"] not in already]
    if not recs:
        return already
    if DRY:
        for rec in recs:
            log(_("(dry-run) would send {type} {key}", type=rec["type"], key=rec["key"]))
        return already | {rec["id"]: "dry-run" for rec in recs}
    r = hubbus("record-publish", "--json", "-", stdin=json.dumps(recs, ensure_ascii=False))
    try:
        res = {x["id"]: x["status"] for x in json.loads(r.stdout or "[]")}   # each {id: record id, event, status}
    except ValueError:
        res = {}
    for rec in recs:
        st = res.get(rec["id"], "failed")
        if st not in ("accepted", "duplicate"):
            log(_("{project}: {type} {key} could not be sent ({status}): {stderr}", project=rec["project"],
                  type=rec["type"], key=rec["key"], status=st, stderr=r.stderr.strip()[-160:]))
    return already | res


def record(project: str, type_: str, key: str, rid: str, **kw) -> dict:
    return {"ph": ph_wire.send_version(), "ext": ph_wire.ext("acts"), "id": rid, "type": type_,
            "origin": kw.pop("origin", "requester"), "by": REGISTRAR, "key": key, "project": project, "ts": now(), **kw}


def removal_reason(key: str) -> str | None:
    """The review-log entry that deleted the line carrying this key: "<heading>: <reason>" (the last such entry)."""
    if not REVIEW_LOG.exists():
        return None
    head = why = found = None
    for line in REVIEW_LOG.read_text().splitlines():
        if line.startswith("## "):
            head, why = line[3:].strip(), None
        elif m := REASON.match(line):   # the librarian's reason line, either set
            why = line[m.end():].strip()
        elif line.startswith("-") and not line.startswith("---") and key in line:
            found = f"{head}:{why}" if why else head
    return found


def targets() -> list[tuple[str, pathlib.Path, str]]:
    """(project, file, how to commit)"""
    out = [(d.name, d / "STATE.md", "copy") for d in sorted(COPIES.iterdir())
           if (d / ".git").exists() and (d / "STATE.md").exists()]
    mig = hubconf.data_file("openclaw-migration-status.md")
    if mig.exists():
        out.append((hubconf.hub_project(), mig, "hub"))
    return out


def commit(project: str, path: pathlib.Path, how: str, msg: str) -> bool:
    if how == "hub":
        r = subprocess.run([str(HUB / "bin" / "commit-hub-files.sh"), msg, path.name], cwd=HUB,
                           capture_output=True, text=True)
    else:
        r = subprocess.run([str(HUB / "bin" / "with-lock.sh"), "bash", "-c",
                            f"git add STATE.md && git commit -qm {shlex.quote(msg)} && {HUB}/bin/git-safe-push.sh"],
                           cwd=path.parent, capture_output=True, text=True)
    if r.returncode != 0:
        log(_("{project}: commit/push failed (left for tomorrow night): {output}", project=project,
              output=(r.stderr or r.stdout).strip()[-200:]))
    return r.returncode == 0


# --- one project ---

def run_project(project: str, path: pathlib.Path, how: str, warned: set[str]) -> tuple[bool, int]:
    """Returns (all went well, number of records sent)."""
    led, text, W = Ledger(project), path.read_text(), writer()
    # 0. read
    if (at := acts_md.parse(text, READ)["unclosed"]) is not None:
        log(_("{project}: STATE.md has an unclosed code fence or comment (from line {line}); left alone until it is "
              "fixed", project=project, line=at + 1))
        return True, 0
    before, ok, sent = text, True, 0
    bound, known = led.bound_keys(), led.known_keys()
    for r in led.ignored:
        log(_("{project}: record {id} ignored: not a valid Principal Handoff record", project=project,
              id=r.get("id") if isinstance(r, dict) else r))
    active = {k: led.terms(k)[0] or "" for k in led.open() if k in bound}
    # 1. apply
    applied, amends = [], []
    for key in sorted({str(e["record"].get("key")) for e in led.pending}):
        cur = current(led.settle.get(key, []))
        if cur is None:   # only invalid records: consumed, nothing applied
            continue
        text, changed = acts_md.apply(text, cur, READ, W, led.latest(key), bound)
        if not changed and cur["type"] == "settled" and (key in bound or key in known):
            holding = {norm(e.text) for e in acts_md.Doc(text, READ).holding(key)}
            if holding and holding != {norm(led.terms(key)[0] or "")}:
                if key in bound:
                    log(_("{project}: {key} was settled, but its line was changed since; not struck, the change is "
                          "sent as an amendment", project=project, key=key))
                    if not led.withdrawn(key) and len(holding) == 1:
                        amends.append((key, holding.pop()))
                else:
                    log(_("{project}: {key} was settled, but the line carrying it asks something else; not struck "
                          "(the item was raised outside STATE.md)", project=project, key=key))
        log(_("{project}: applied {type} {key}", project=project, type=cur["type"], key=key))
    applied += [e["id"] for e in led.pending]
    # 2. relink
    text, relinked = acts_md.relink(text, active, READ, W, bound, known)
    for k in relinked:
        log(_("{project}: restored key {key} (the key at the end of the line had been removed)", project=project, key=k))
    # 3. raise
    _stamped, events, reported = acts_md.register(text, project, READ, W, bound, task_key, known)
    doc = acts_md.Doc(text, READ)
    for i in reported:
        line, k = doc.lines[i].strip(), doc.carries(i)
        if k in known:   # spec §2 step 3: warned every night until an agent fixes the line
            log(_("{project}: warning — line {line} carries {key}, a to-do the project raised itself (its key was "
                  "copied onto the line); not raised. Delete the line, or delete the key so that the line is raised "
                  "and replace the old to-do with it", project=project, line=i + 1, key=k))
            continue
        log(_("{project}: not raised, line {line}: `{text}` (its text repeats a to-do already raised, appears twice, or "
              "goes on after a key)", project=project, line=i + 1, text=line[:80]))
    fresh = [(e, record(project, "raised", e["key"], f"r:{e['key']}", terms={"text": e["text"]}, terms_rev=1))
             for e in events]
    res = publish([r for _e, r in fresh], led.sent)
    for e, r in fresh:
        if res.get(r["id"]) in ("accepted", "duplicate", "dry-run"):
            sent += res.get(r["id"]) == "accepted"
            doc.stamp(e["line"], e["key"], W)
        else:
            ok = False
    text = doc.text() if fresh else text
    # 4. amend
    found = acts_md.amend(text, active, READ)
    for k in found["warn"]:
        log(_("{project}: {key} is on owner lines with different texts (was the key copied?); no amendment sent",
              project=project, key=k))
    for k, new_text in [*amends, *((a["key"], a["text"]) for a in found["amended"])]:
        _old, rev, cur = led.terms(k)
        amends_rec = record(led.filed(k), "amended", k, f"a:{k}:{rev + 1}", terms={"text": new_text}, terms_rev=rev + 1,
                            **({"supersedes": [cur]} if cur else {}))
        r = publish([amends_rec], led.sent)
        sent += r.get(amends_rec["id"]) == "accepted"
        ok &= r.get(amends_rec["id"]) in ("accepted", "duplicate", "dry-run")
    # 5. reconcile
    claims, notes = [], []
    found = acts_md.reconcile(text, active, READ, bound)
    # warned once per stretch: a key leaves the list when it no longer looks rewritten (found again, settled, or the
    # similar line gone too, which then gets its claim)
    still = {x["key"] for x in found if x["action"] == "warn"}
    warned.difference_update(k for k in led.known() if k not in still)
    for x in found:
        k, act = x["key"], x["action"]
        if act == "warn":
            if k not in warned:
                log(_("{project}: warning — {key} is not settled and its key is gone, but a very similar line exists "
                      "(the line end dropped in a rewrite?); no claim sent", project=project, key=k))
                warned.add(k)
        elif act == "closed":
            log(_("{project}: an owner line still carries {key}, which is already settled or withdrawn", project=project,
                  key=k))
        elif act == "displaced":
            line = acts_md.Doc(text, READ).lines[x["line"]].strip()
            notes.append(record(led.filed(k), "note", k, f"d:{k}:r{led.reopens(k)}", origin="registrar", about={"key": k},
                                note=_c("The key of this to-do is no longer on an owner line of STATE.md's to-dos (it was "
                                        "relabelled, moved or archived); it is now on line {line}: {text}",
                                        line=x["line"] + 1, text=line[:200])))
        else:
            if act == "ticked":
                basis = _c("Inferred done by the librarian: the to-do's box in STATE.md was ticked.")
            else:
                why = removal_reason(k)
                basis = (_c("Inferred done by the librarian: the item is no longer among STATE.md's to-dos. ")
                         + (_c("The run that deleted it: {why}", why=why[:300]) if why else
                            _c("The review-log does not record which run deleted it")))
            claims.append(record(led.filed(k), "claimed", k, f"c:{k}:r{led.reopens(k)}", origin="registrar",
                                 outcome="done", evidence={"basis": basis}))
    res = publish(claims + notes, led.sent)
    for r in notes:
        if res.get(r["id"]) == "accepted":
            log(_("{project}: {key} moved off the owner lines (relabelled, moved or archived); no claim, a note sent",
                  project=project, key=r["key"]))
    sent += sum(1 for r in claims + notes if res.get(r["id"]) == "accepted")
    ok &= all(res.get(r["id"]) in ("accepted", "duplicate", "dry-run") for r in claims + notes)
    # 6. write the file, commit, only then done
    changed = text != before
    if DRY:
        return ok, sent
    if changed:
        path.write_text(text)
        if not commit(project, path, how, _("registrar: owner to-dos in step with {assistant}",
                                            assistant=hubconf.assistant_name())):
            return False, sent
    if applied:
        hubbus("done", *applied, "--note", _("registrar updated the STATE.md to-dos by key") if changed else
               _("registrar: STATE.md was already in this state"))
    return ok, sent


def main() -> int:
    try:
        warned = set(json.loads(WARNED.read_text()))
    except (OSError, ValueError):
        warned = set()
    failed = total = 0
    for project, path, how in targets():
        ok, sent = run_project(project, path, how, warned)
        failed += not ok
        total += sent
    if not DRY:
        WARNED.write_text(json.dumps(sorted(warned)))
    log(_("sent {sent} {sent:record|records}; {failed} {failed:project|projects} had failures", sent=total, failed=failed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
