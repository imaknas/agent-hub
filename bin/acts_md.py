"""The Acts Markdown binding of Principal Handoff 0.5 (agent-ops-specs acts-markdown/SPEC.md §2-§4): reading a
project's STATE.md to-do sections and the registrar's document steps. Follows the spec's reference registrar
(specs/principal-handoff/reference/ph_acts.py, Apache-2.0), with one difference: a document is read in several
profiles at once. brunna's readers accept both token sets in one file (contracts/state-todo.v2.md: an English
instance may hold an older zh-Hant section, a line may carry one set's label and the other's marker), so every reading
takes a tuple of profiles, and the writer's profile (the content language) is the one used for what is written.

    profiles = acts_md.profiles(owner_tag)         # both sets (bin/todo_tokens.py), for reading
    writer = acts_md.profile(lang, owner_tag)      # the content language's, for writing
    parse(doc, profiles)                           -> {"sections", "tasks", "problems", "unclosed"}
    relink(doc, active, profiles, writer, bound, known)   -> (doc, [key])              step 2
    register(doc, project, profiles, writer, bound, derive, known) -> (doc, [{key, text}], [line])   step 3
    amend(doc, latest, profiles)                   -> {"amended": [{key, text}], "warn": [key]}   step 4
    apply(doc, record, profiles, writer, latest, bound)   -> (doc, changed)            step 1, one record
    reconcile(doc, active, profiles, bound)        -> [{key, action, line?}]           step 5

`bound`: the keys the registrar raised from lines of this document (spec §2); None means every key passed is bound.
`known`: keys outside the binding the registrar holds need records for (an item the project raised itself, spec §2
step 3): a line carrying one is neither raised nor relinked, and reported.
`active` / `latest` map active bound keys to their latest terms text (apply: {text, terms_rev, terms_digest?} of a bound
or known key).
Documents are strings; line numbers are 0-based. Standard library only, no platform imports but todo_tokens.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

import todo_tokens

ASSIGNEES = ("principal", "agent", "undecided")
ROLE = {"principal": "owner", "agent": "agent", "undecided": "tbd"}   # spec assignee -> todo_tokens role
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
CHECKBOX = re.compile(r"^- \[[ xX]\](\s|$)")
TICKED = re.compile(r"^- \[[xX]\]")
LIST = r"(?:[-*+]|[0-9]{1,9}[.)])[ \t]+"
HEADING = re.compile(r"^#{1,6}(\s|$)")
SECTION_END = re.compile(r"^#{1,2}([ \t]|$)")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
COMMENT = re.compile(r"^ {0,3}<!--")
SIMILAR = 0.6   # reconcile: a vanished task this similar to some line counts as rewritten (implementation choice)


@dataclass(frozen=True)
class Profile:
    heading: str
    label_open: str
    label_close: str
    label_separator: str
    labels: tuple            # (principal, agent, undecided) label texts
    placeholder: str
    marker_render: str       # contains "{key}"
    marker_pattern: str      # regex; group 1 the key; searched against a whole line

    @classmethod
    def from_json(cls, d: dict, **params: str) -> "Profile":
        """A profile as the spec's profiles.json describes it, parameters (principal_tag) substituted."""
        values = {**d.get("parameters", {}), **params}

        def fill(s: str, escape: bool = False) -> str:
            for k, v in values.items():
                s = s.replace("{" + k + "}", re.escape(v) if escape else v)
            return s

        return cls(d["heading"], d["label_open"], d["label_close"], d["label_separator"],
                   tuple(fill(d["labels"][a]) for a in ASSIGNEES), d["placeholder"], fill(d["marker_render"]),
                   fill(d["marker_pattern"], escape=True))

    def assignee(self, label: str) -> str | None:
        return next((a for a, l in zip(ASSIGNEES, self.labels) if l == label), None)

    @property
    def _links(self) -> bool:
        """§3.3: with [ and ] as brackets, a bracketed span followed by ( is a Markdown link, not a label."""
        return (self.label_open, self.label_close) == ("[", "]")

    @property
    def _x(self) -> str:
        return r"(?!\()" if self._links else ""

    @property
    def _label(self) -> str:
        o, c = re.escape(self.label_open), re.escape(self.label_close)
        return rf"{o}({'|'.join(re.escape(l) for l in self.labels)}){c}{self._x}"

    def compiled(self) -> "_Rx":
        return _rx(self)

    def render_marker(self, key: str) -> str:
        return self.marker_render.replace("{key}", key)

    def principal_line(self, text: str, key: str) -> str:
        return (f"- [ ] {self.label_open}{self.labels[0]}{self.label_close}{self.label_separator}{text}"
                + self.render_marker(key))


@dataclass(frozen=True)
class _Rx:
    item: re.Pattern
    group: re.Pattern
    bare: re.Pattern
    tasklike: re.Pattern
    label: re.Pattern
    label_at: re.Pattern
    marker: re.Pattern
    mention: re.Pattern


_RX: dict[Profile, _Rx] = {}


def _rx(p: Profile) -> _Rx:
    if p not in _RX:
        o, c = re.escape(p.label_open), re.escape(p.label_close)
        ahead = rf"{o}[^{c}]*{c}{p._x}" if p._links else o
        mention = p.marker_pattern
        for end in (r"\s*$", "$"):
            if mention.endswith(end):
                mention = mention[: -len(end)]
                break
        _RX[p] = _Rx(item=re.compile(rf"^- \[ \][ \t]+{p._label}\s*(.+)$"),
                     group=re.compile(rf"^#{{3,4}}\s*{o}([^{c}]+){c}\s*$"),
                     bare=re.compile(rf"^- \[ \][ \t]+(?!{ahead})(.+)$"),
                     tasklike=re.compile(rf"^{LIST}(?:\[[ xX]\]|{p._label})"),
                     label=re.compile(rf"^{LIST}(?:\[[ xX]\][ \t]*)?{p._label}"),
                     label_at=re.compile(rf"^{p._label}"),
                     marker=re.compile(p.marker_pattern), mention=re.compile(mention))
    return _RX[p]


def profile(lang: str, owner: str) -> Profile:
    """agent-hub's profile for one token set (todo_tokens): zh-Hant with the configured owner tag as principal_tag,
    en with its default principal_tag (`owner`)."""
    s = todo_tokens.tokens(lang)
    tag = s["owner_label"] or owner
    o, c = s["tag"].split("{}")
    return Profile(s["todo_heading"], o, c, s["tag_join"],
                   tuple(todo_tokens.label(lang, ROLE[a], owner) for a in ASSIGNEES), s["todo_none"],
                   s["key_join"] + s["key_marker"].replace("{owner}", tag),
                   s["key_pattern"].replace("{owner}", re.escape(tag)))


def profiles(owner: str) -> tuple[Profile, ...]:
    """Every set, for reading."""
    return tuple(profile(lang, owner) for lang in todo_tokens.SETS)


def norm(text: str) -> str:
    return " ".join(text.split())


def _eol(doc: str) -> str:
    return "\r\n" if "\r\n" in doc else "\n"


def _lines(doc: str) -> list[str]:
    lines = doc.split(_eol(doc))
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _join(lines: list[str], eol: str = "\n") -> str:
    return eol.join(lines) + eol


def _ignored(lines: list[str]) -> tuple[set[int], int | None]:
    """§3.2: lines in fenced code blocks and HTML comment blocks, found in one pass from the top, and the line where
    an unclosed block opens (the document is then incomplete)."""
    out: set[int] = set()
    i = 0
    while i < len(lines):
        f = FENCE.match(lines[i])
        if f and not (f.group(1)[0] == "`" and "`" in f.group(2)):
            fence = f.group(1)
            close = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
            end = next((j for j in range(i + 1, len(lines)) if close.match(lines[j].rstrip())), None)
        elif COMMENT.match(lines[i]):
            end = next((j for j in range(i, len(lines)) if "-->" in lines[j]), None)
        else:
            i += 1
            continue
        if end is None:
            return out | set(range(i, len(lines))), i
        out |= set(range(i, end + 1))
        i = end + 1
    return out, None


@dataclass
class Line:
    i: int
    kind: str               # task, malformed or tasklike
    owner: str | None       # assignee class: a task's, or for other lines their label's or else their group's
    section: int
    text: str = ""
    key: str | None = None


class Doc:
    """One reading of a document in some profiles: ignored regions, tasks sections, the classified lines."""

    def __init__(self, doc: str, ps: tuple[Profile, ...]):
        self.ps, self.rx = ps, [p.compiled() for p in ps]
        self.lines, self.eol = _lines(doc), _eol(doc)
        self.ignored, self.unclosed = _ignored(self.lines)
        self.sections = self._sections()
        self.entries: list[Line] = []
        self.problems = [b[0] - 1 for b in self.sections[1:]]   # every tasks heading after the first
        self._classify()

    def _sections(self) -> list[tuple[int, int]]:
        heads = {p.heading for p in self.ps}
        out = []
        for start, line in enumerate(self.lines):
            if start not in self.ignored and line.rstrip() in heads:
                end = next((i for i in range(start + 1, len(self.lines))
                            if i not in self.ignored and SECTION_END.match(self.lines[i].rstrip())), len(self.lines))
                out.append((start + 1, end))
        return out

    def _first(self, what: str, line: str) -> tuple[Profile, re.Match] | None:
        for p, rx in zip(self.ps, self.rx):
            if (m := getattr(rx, what).match(line)):
                return p, m
        return None

    def _classify(self) -> None:
        for n, (start, end) in enumerate(self.sections):
            group = None
            for i in range(start, end):
                if i in self.ignored:
                    continue
                line = self.lines[i].rstrip()
                if (g := self._first("group", line)):
                    group = g[0].assignee(g[1].group(1)) or "unknown"
                    continue
                if HEADING.match(line):
                    group = "unknown"   # any other heading ends the group
                    continue
                own = self._first("label", line)
                owner = own[0].assignee(own[1].group(1)) if own else (group if group in ASSIGNEES else None)
                if not CHECKBOX.match(line):
                    if self._first("tasklike", line):
                        self.problems.append(i)
                        self.entries.append(Line(i, "tasklike", owner, n, self.text_of(line)))
                    continue   # placeholder, prose, quotes, indented continuation lines
                if (m := self._first("item", line)):
                    assignee, body = m[0].assignee(m[1].group(1)), m[1].group(2)
                elif group in ASSIGNEES and all(rx.bare.match(line) for rx in self.rx):
                    assignee, body = group, self.rx[0].bare.match(line).group(1)
                else:
                    self.problems.append(i)   # ticked box, unknown label, or bare item outside a known group
                    self.entries.append(Line(i, "malformed", owner, n, self.text_of(line)))
                    continue
                key, cut = self.key_of(body)
                text = body[:cut].strip()
                if not text or (key is not None and not KEY_RE.match(key)):
                    self.problems.append(i)
                    self.entries.append(Line(i, "malformed", owner, n, self.text_of(line)))
                    continue
                self.entries.append(Line(i, "task", assignee, n, text, key))
        self.problems.sort()

    def key_of(self, text: str) -> tuple[str | None, int]:
        """(key, where the marker starts) of the earliest marker of any profile, or (None, len(text))."""
        hits = [m for rx in self.rx if (m := rx.marker.search(text))]
        if not hits:
            return None, len(text)
        m = min(hits, key=lambda m: m.start())
        return m.group(1), m.start()

    def carries(self, i: int) -> str | None:
        return self.key_of(self.lines[i].rstrip())[0]

    def body_of(self, i: int) -> str:
        """a task line after its checkbox and label, key marker kept (what todo.md lists)"""
        rest = re.sub(r"^- \[[ xX]\][ \t]*", "", self.lines[i].rstrip())
        for rx in self.rx:
            if (m := rx.label_at.match(rest)):
                return rest[m.end():].strip()
        return rest.strip()

    def text_of(self, line: str) -> str:
        """§3.5.2: the line without list marker, checkbox, label and key marker."""
        rest = re.sub(rf"^{LIST}(?:\[[ xX]\][ \t]*)?", "", line.rstrip())
        for rx in self.rx:
            if (m := rx.label_at.match(rest)):
                rest = rest[m.end():]
                break
        return rest[: self.key_of(rest)[1]].strip()

    def tasks(self) -> list[Line]:
        return [e for e in self.entries if e.kind == "task"]

    def holding(self, key: str) -> list[Line]:
        """§2: principal task lines of the first tasks section that carry the key."""
        return [e for e in self.tasks() if e.section == 0 and e.owner == "principal" and e.key == key]

    def mentioned(self) -> dict[str, int]:
        """§2: every key some line mentions (anywhere in the document) -> the first line that does."""
        out: dict[str, int] = {}
        for i, line in enumerate(self.lines):
            for rx in self.rx:
                for m in rx.mention.finditer(line):
                    out.setdefault(m.group(1), i)
        return out

    def stamp(self, i: int, key: str, writer: Profile) -> None:
        line = self.lines[i].rstrip()
        k, cut = self.key_of(line)
        if k is not None:
            line = line[:cut].rstrip()
        self.lines[i] = line + writer.render_marker(key)

    def text(self) -> str:
        return _join(self.lines, self.eol)


def parse(doc: str, ps: tuple[Profile, ...]) -> dict:
    d = Doc(doc, ps)
    return {"sections": [b[0] - 1 for b in d.sections],
            "tasks": [{"line": e.i, "assignee": e.owner, "text": e.text, "key": e.key} for e in d.tasks()],
            "problems": d.problems, "unclosed": d.unclosed}


def _unbound(key: str | None, bound: set | None) -> bool:
    return key is None or (bound is not None and key not in bound)


def relink(doc: str, active: dict, ps: tuple[Profile, ...], writer: Profile,
           bound: set | None = None, known: set = frozenset()) -> tuple[str, list[str]]:
    """Step 2. Of the active bound keys no line mentions, a key whose latest text equals exactly one unkeyed (or
    wrongly keyed, but not known) principal line of the first section, that line's text no other such key, gets that
    line back."""
    d = Doc(doc, ps)
    if d.unclosed is not None:
        return doc, []
    seen = d.mentioned()
    vanished = {k: norm(t) for k, t in active.items() if (bound is None or k in bound) and k not in seen}
    lines = [e for e in d.tasks() if e.section == 0 and e.owner == "principal" and _unbound(e.key, bound)
             and e.key not in known]
    done = []
    for e in lines:
        keys = [k for k, t in vanished.items() if t == norm(e.text)]
        rivals = [x for x in lines if norm(x.text) == norm(e.text)]
        if len(keys) == 1 and len(rivals) == 1:
            d.stamp(e.i, keys[0], writer)
            done.append(keys[0])
    return (d.text() if done else doc), done


def register(doc: str, project: str, ps: tuple[Profile, ...], writer: Profile, bound: set | None,
             derive, known: set = frozenset()) -> tuple[str, list[dict], list[int]]:
    """Step 3: every principal line of the first section that carries no key, or one neither bound nor known -> (the
    stamped document, the items to raise [{key, text, line}] in order, the lines not raised and reported). A line
    carrying a known key is reported, never raised or restamped. A real run sends each `raised` before writing its
    stamp; the caller does that with the lines returned."""
    d = Doc(doc, ps)
    if d.unclosed is not None:
        return doc, [], []
    events, reported, seen = [], [], set()
    for e in d.tasks():
        if e.section != 0 or e.owner != "principal" or not _unbound(e.key, bound):
            continue
        if e.key is not None and e.key in known:
            reported.append(e.i)   # an item raised outside the binding, its marker copied onto the line
            continue
        key = derive(project, e.text)
        if any(rx.mention.search(e.text) for rx in d.rx) or (bound is not None and key in bound) \
                or norm(e.text) in seen:
            reported.append(e.i)   # text after a marker, a text already raised, or a duplicate in this run
            continue
        seen.add(norm(e.text))
        events.append({"key": key, "text": norm(e.text), "line": e.i})
        d.stamp(e.i, key, writer)
    return (d.text() if events else doc), events, reported


def amend(doc: str, latest: dict, ps: tuple[Profile, ...]) -> dict:
    """Step 4. A key whose holding lines share one text that differs from the latest terms -> amended; holding lines
    that disagree -> warn."""
    d = Doc(doc, ps)
    out: dict = {"amended": [], "warn": []}
    if d.unclosed is not None:
        return out
    for e in d.tasks():
        k = e.key
        if k not in latest or e.owner != "principal" or e.section != 0 or k in out["warn"] \
                or any(a["key"] == k for a in out["amended"]):
            continue
        texts = {norm(x.text) for x in d.holding(k)}
        if len(texts) > 1:
            out["warn"].append(k)
        elif norm(e.text) != norm(latest[k]):
            out["amended"].append({"key": k, "text": norm(e.text)})
    return out


def stale(record: dict, latest: dict | None) -> bool:
    """A settlement made against older terms than the registrar's latest (core §6)."""
    if latest is None:
        return False
    rev = record.get("terms_rev", 0)
    return rev < latest["terms_rev"] or (rev == latest["terms_rev"] and "terms_digest" in latest
                                         and "terms_digest" in record
                                         and record["terms_digest"] != latest["terms_digest"])


def apply(doc: str, record: dict, ps: tuple[Profile, ...], writer: Profile,
          latest: dict | None = None, bound: set | None = None) -> tuple[str, bool]:
    """Step 1, one record: only the principal side's settled (strike) and reopened (re-add) change the document. A
    settlement that is stale against `latest`, or whose holding lines were edited away from it, strikes nothing. Only a
    bound key is re-added after a reopening (bound None: the key is bound)."""
    if record.get("origin") != "principal" or record.get("type") not in ("settled", "reopened"):
        return doc, False
    d = Doc(doc, ps)
    if d.unclosed is not None:
        return doc, False
    key = record["key"]
    if record["type"] == "settled":
        if stale(record, latest) or (latest is not None and any(norm(e.text) != norm(latest["text"])
                                                                for e in d.holding(key))):
            return doc, False
        hits = [e for e in d.entries if e.owner == "principal" and d.carries(e.i) == key
                and (latest is None or norm(e.text) == norm(latest["text"]))]
        if not hits:
            return doc, False
        gone, emptied = {e.i for e in hits}, sorted({e.section for e in hits})
        kept = [l for i, l in enumerate(d.lines) if i not in gone]
        for n in reversed(emptied):   # each section a strike left blank gets the placeholder
            start, end = Doc(_join(kept), ps).sections[n]
            if not any(kept[i].strip() for i in range(start, end)):
                kept.insert(start, writer.placeholder)
        return _join(kept, d.eol), True
    if d.holding(key) or (bound is not None and key not in bound):
        return doc, False   # reopened, and the line is still there; or a key the registrar did not raise
    text = latest["text"] if latest is not None else record.get("restore_text")
    if text is None:
        return doc, False
    new, lines = writer.principal_line(norm(text), key), d.lines
    if not d.sections:
        return _join(lines + ["", writer.heading, "", new], d.eol), True
    start, end = d.sections[0]   # re-adding uses the first tasks section
    body = [i for i in range(start, end) if lines[i].strip()]
    if len(body) == 1 and lines[body[0]].strip() in {p.placeholder for p in ps}:
        lines[body[0]] = new
    else:
        lines.insert(body[-1] + 1 if body else start, new)
    return _join(lines, d.eol), True


def reconcile(doc: str, active: dict, ps: tuple[Profile, ...], bound: set | None = None) -> list[dict]:
    """Step 5, for active bound keys with no holding line: ticked (a ticked principal box carries it: claim done),
    displaced (its marker is still somewhere: note, no claim; `line` says where), warn (vanished, a line looks like a
    rewrite), claim (vanished: claim done); then closed for principal task lines carrying a bound key not active."""
    d = Doc(doc, ps)
    if d.unclosed is not None:
        return []
    texts = [norm(e.text) for e in d.tasks()]
    seen = d.mentioned()
    out = []
    for key, text in sorted(active.items()):
        if (bound is not None and key not in bound) or d.holding(key):
            continue
        if any(e.owner == "principal" and TICKED.match(d.lines[e.i]) and d.carries(e.i) == key for e in d.entries):
            out.append({"key": key, "action": "ticked"})
        elif key in seen:
            out.append({"key": key, "action": "displaced", "line": seen[key]})
        else:
            a = norm(text)
            similar = any(a == t or difflib.SequenceMatcher(None, a, t).ratio() >= SIMILAR for t in texts)
            out.append({"key": key, "action": "warn" if similar else "claim"})
    if bound is not None:
        closed = {e.key for e in d.tasks() if e.owner == "principal" and e.key in bound and e.key not in active}
        out += [{"key": k, "action": "closed"} for k in sorted(closed)]
    return out
