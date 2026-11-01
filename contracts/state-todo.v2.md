<!-- contract: state-todo v2 -->
# Contract: the to-do section of a project's STATE.md, v2

Every project has one status file with one fixed to-do section. Below, `{owner}` is the owner's tag from the settings
(each reader fills it in from its own settings).
This file is rewritten by a model, so **it carries no version marker** (a model drops lines it does not recognize); the
version is set by this contract.

v2 = v1 plus a second, English token set. A file in the v1 (zh-Hant) set is a valid v2 file and changes nothing; a file
in the English set needs a v2 reader. The two sets are the profiles of agent-ops-specs' Principal Handoff Acts Markdown
binding 0.5 (§3.1, `acts-markdown/conformance/profiles.json`): zh-Hant is the `zh-hant` profile with `{owner}` as its
`principal_tag`, English is the `en` profile as it is.

## The two token sets

| | zh-Hant (v1, profile `zh-hant`) | English (profile `en`) |
|---|---|---|
| section heading | `## 待辦` | `## Tasks` |
| owner labels | `〔{owner}〕` `〔agent〕` `〔待定〕` | `[owner]` `[agent]` `[undecided]` |
| between label and text | nothing | one space |
| no items | `- (無)` | `- (none)` |
| key marker, as written | `(等 {owner}:<key>)` right after the text | ` (waiting on owner: <key>)` (after a space) |

In the zh-Hant set the owner's label is the configured owner tag; in the English set it is the word `owner`, whoever
the owner is (the configured tag in English brackets is not a known label). `待定` and `undecided` mean "to be decided";
`agent` means an agent can do it; the owner's label means only the owner can.

## Format

- The section heading is exactly one of the two heading lines, and the section runs to the next `## `. A file has one
  to-do section; two (one per set) are a format problem (readers still read both, so nothing is lost).
- One item per line, with one of the three labels of either set, after `- [ ] `. A label is known only in its own
  set's brackets (`[待定]` and `〔undecided〕` are unknown). In the English set a bracket followed by `(` is a markdown
  link, not a label. With no items the section is the single empty line of either set.
- Grouped form is also accepted: a `### <label>` subheading with `- [ ] <text>` lines (no label) under it. A subheading
  whose label is not one of the three of its set, and a line whose label is not recognized, are format problems; they
  are never assigned to the previous group.
- Once an owner item is registered as a task, the key marker is appended to the line. Readers use each profile's
  `marker_pattern` (below), and also accept full-width parentheses and colons; in zh-Hant `等` with or without a space
  after it; in English the key is one run of non-space characters. The key is an opaque string (today
  `task-<project>-<10 hex digits>`); when that task is closed, the line is removed by its key.

## How the section is read (Principal Handoff Acts Markdown binding 0.5 §3.2-§3.3)

Readers follow the 0.5 binding (Acts 0.4 §5.2-§5.3 before it moved; no rule changed); this adds no token and changes
no written form, so the version stays:

- Fenced code blocks and HTML comments are not read. A file with one that never closes is a format problem, and the
  registrar leaves that file alone until it is fixed.
- Every to-do section is read. A second one is a format problem, and new owner to-dos are registered only from the
  first.
- Any other heading inside a section ends a group (`### <label>`).
- A list line that looks like a to-do but is not a `- [ ]` line is a format problem:
  - one with a checkbox in another list form (`* [ ]`, `1. [ ]`);
  - one with a label but no checkbox (`- [owner] …`).
- Any run of spaces or tabs may follow `- [ ]`.
- In the English set, a bracket followed by `(` is a Markdown link, not a label.
- A key whose marker appears anywhere in the file is still present for the registrar.

## A file that mixes the sets

Readers take every line in either set, whatever the reader's own settings: an English instance may still have lines,
or a whole section, written in the zh-Hant set, and a line may carry a label of one set and a key marker of the other.
A key on any line of the to-do section counts as present, whatever the line's label. A writer writes its own set on
what it writes (a new line, a key marker, the empty line, a missing heading) and does not rewrite lines it does not
own; a key is never changed.

## Rules (readers parse with these regexes; one rule per line, a line matches if any rule does)

```heading section
## 待辦
## Tasks
```

```heading none
- (無)
- (none)
```

```regex item
^- \[ \] 〔({owner}|agent|待定)〕\s*(.+)$
^- \[ \] \[(owner|agent|undecided)\](?!\()\s*(.+)$
```

```regex group
^#{3,4}\s*〔({owner}|agent|待定)〕\s*$
^#{3,4}\s*\[(owner|agent|undecided)\]\s*$
```

```regex bare
^- \[ \] (?!〔)(?!\[[^\]]+\](?!\())(.+)$
```

```regex key
[(（]等 ?{owner}[:：]\s*([^()（）]+?)\s*[)）]\s*$
[(（]waiting on owner[:：]\s*([^\s()（）]+)\s*[)）]\s*$
```

## Versions

Adding explanation does not bump the version; changing the tags, the key format or the section heading does (readers
accept both until the producers have all switched). v1 stays next to this file until every reader reads v2.
