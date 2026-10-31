"""The to-do formats' two token sets, in one place (contracts/state-todo.v2.md, contracts/todo-md.v2.md;
docs/design/i18n.md "Machine formats").

The to-do block of a project's STATE.md, todo.md and the review-log label the registrar reads back are machine formats:
other scripts and the owner's assistant parse them. Each has a zh-Hant set (the v1 contracts) and an English one.
**Readers accept both sets whatever the settings** (patterns() below); **writers emit one set**: the STATE.md block in
`language.content`, todo.md's headings and the review-log label in `language.ui` (the caller picks the language and
passes it in; this module reads no settings). Standard library only, no platform imports: gen-todo.py, registrar.py,
librarian.py, contracts.py and prompts.py all import it.

    import todo_tokens
    t = todo_tokens.tokens("en")                 # one set, for writing
    t["todo_heading"], todo_tokens.tag("en", "agent"), todo_tokens.stamp("en", "Alex", "task-p-0123456789")
    P = todo_tokens.patterns("Alex")             # both sets, for reading
    P.item.match(line)                           # role(m) -> "owner" | "agent" | "tbd"

A file that mixes the sets (an English instance with an older Chinese section, a Chinese line stamped by an English
writer) is read line by line: every line in either set counts. A writer writes its own set on the lines it creates or
changes (a stamp, a re-added line, the empty marker) and leaves the other lines as they are; keys are opaque and never
change.
"""
from __future__ import annotations

import re

SETS = {
    "zh-Hant": {
        # STATE.md's to-do block (state-todo v1)
        "todo_heading": "## 待辦", "tag": "〔{}〕", "tag_join": "", "tbd": "待定", "todo_none": "- (無)",
        "key_marker": "(等 {owner}:{key})", "key_join": "",
        # todo.md (todo-md v1): the three owner sections in order owner, tbd, agent, and a section's empty line
        "todo_md_headings": ("## 只有 {owner} 能做", "## 待定(需要先做決定)", "## agent 可以做"),
        "todo_md_none": "(無)", "todo_md_version": 1,
        # the review-log's reason line, read back by the registrar (the removal reason of a claimed item)
        "reason_label": "**理由**",
    },
    "en": {
        "todo_heading": "## To-do", "tag": "[{}]", "tag_join": " ", "tbd": "tbd", "todo_none": "- (none)",
        "key_marker": "(waiting on {owner}: {key})", "key_join": " ",
        "todo_md_headings": ("## Only {owner} can do", "## To be decided (a decision comes first)",
                             "## An agent can do"),
        "todo_md_none": "(none)", "todo_md_version": 2,
        "reason_label": "**Reason**",
    },
}
ROLES = ("owner", "tbd", "agent")   # todo.md's section order


def tokens(lang: str) -> dict:
    return SETS[lang]


def tag(lang: str, who: str) -> str:
    """who: the owner's tag text, "agent", or the set's tbd word"""
    return SETS[lang]["tag"].format(who)


def item(lang: str, who: str, text: str) -> str:
    """a to-do line: `- [ ] ` + the tag + the text"""
    return f"- [ ] {tag(lang, who)}{SETS[lang]['tag_join']}{text}"


def marker(lang: str, owner: str, key: str) -> str:
    return SETS[lang]["key_marker"].format(owner=owner, key=key)


def stamp(lang: str, owner: str, key: str) -> str:
    """what is appended to a line to stamp its key (with the set's separator)"""
    return SETS[lang]["key_join"] + marker(lang, owner, key)


def todo_md_headings(lang: str, owner: str) -> dict[str, str]:
    """role -> todo.md's section heading"""
    return dict(zip(ROLES, (h.format(owner=owner) for h in SETS[lang]["todo_md_headings"])))


def _alt(*words: str) -> str:
    return "|".join(re.escape(w) for w in words)


class Patterns:
    """Both sets' patterns for one owner tag. A tag is 〔…〕 or […]; the English bracket is not a tag when a `(`
    follows it (a markdown link)."""

    def __init__(self, owner: str):
        zh, en = SETS["zh-Hant"], SETS["en"]
        self.owner = owner
        o = re.escape(owner)
        self.headings = (zh["todo_heading"], en["todo_heading"])
        self.nones = (zh["todo_none"], en["todo_none"])
        self.heading = re.compile(rf"^(?:{_alt(*self.headings)})[ \t]*$")
        self._roles = {owner: "owner", "agent": "agent", zh["tbd"]: "tbd", en["tbd"]: "tbd"}
        zh_tags, en_tags = f"{o}|agent|{re.escape(zh['tbd'])}", f"{o}|agent|{re.escape(en['tbd'])}"
        # a known tag in either set (group zh or en), then the text
        self.item = re.compile(rf"^- \[ \] (?:〔(?P<zh>{zh_tags})〕|\[(?P<en>{en_tags})\](?!\())\s*(?P<text>.+)$")
        # any tag, known or not (the text after it)
        self.labelled = re.compile(r"^- \[ \] (?:〔[^〕]+〕|\[[^\]]+\](?!\())\s*(?P<text>.+)$")
        # a line with no tag at all (the grouped form's lines)
        self.bare = re.compile(r"^- \[ \] (?!〔)(?!\[[^\]]+\](?!\())(?P<text>.+)$")
        # a line that starts with a tag, text or not (a tag with nothing after it is a format problem, not prose)
        self.tag_start = re.compile(r"^- \[ \] (?:〔|\[[^\]]+\](?!\())")
        # a group subheading, known tag or not: an unknown one still ends the previous group
        self.group = re.compile(r"^#{3,4}\s*(?:〔(?P<zh>[^〕]+)〕|\[(?P<en>[^\]]+)\])\s*$")
        # the key marker, either set; half- and full-width parentheses and colons; at the end of the line
        mark = rf"[(（](?:等 ?{o}|waiting on {o})\s*[:：]\s*"
        self.key = re.compile(rf"{mark}(?P<key>[^()（）]+?)\s*[)）]\s*$")
        # the same anywhere in the text (edits.check_markers: keys may not be rewritten; compared by key, so a marker
        # rewritten into the other set with the same key is the same marker)
        self.key_mark = re.compile(rf"{mark}(?P<key>[^()（）\n]+?)\s*[)）]")
        # the review-log reason line, either set
        self.reason = re.compile(rf"^(?:{_alt(zh['reason_label'], en['reason_label'])})\s*[:：]\s*")

    def role(self, m: re.Match) -> str | None:
        """the role of a matched item / group tag: owner, agent, tbd; None for an unknown tag (or a known word in the
        other set's brackets)"""
        if m.group("zh") is not None:
            return self._roles.get(m.group("zh")) if m.group("zh") != SETS["en"]["tbd"] else None
        return self._roles.get(m.group("en")) if m.group("en") != SETS["zh-Hant"]["tbd"] else None

    def is_none(self, line: str) -> bool:
        return line.strip() in self.nones

    def section(self, lines: list[str]) -> list[tuple[int, int]]:
        """(first line, end) of every to-do section in the file, either heading; each runs to the next `## `"""
        out = []
        for i, l in enumerate(lines):
            if self.heading.match(l.rstrip()):
                end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
                out.append((i + 1, end))
        return out


_cache: dict[str, Patterns] = {}


def patterns(owner: str) -> Patterns:
    if owner not in _cache:
        _cache[owner] = Patterns(owner)
    return _cache[owner]
