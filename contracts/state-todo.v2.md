<!-- contract: state-todo v2 -->
# Contract: the to-do section of a project's STATE.md, v2

Every project has one status file with one fixed to-do section. Below, `{owner}` is the owner's tag from the settings
(each reader fills it in from its own settings).
This file is rewritten by a model, so **it carries no version marker** (a model drops lines it does not recognize); the
version is set by this contract.

v2 = v1 plus a second, English token set. A file in the v1 (zh-Hant) set is a valid v2 file and changes nothing; a file
in the English set needs a v2 reader.

## The two token sets

| | zh-Hant (v1) | English |
|---|---|---|
| section heading | `## 待辦` | `## To-do` |
| owner tags | `〔{owner}〕` `〔agent〕` `〔待定〕` | `[{owner}]` `[agent]` `[tbd]` |
| no items | `- (無)` | `- (none)` |
| key marker | `(等 {owner}:<key>)` | `(waiting on {owner}: <key>)` |

The owner tag's text is the configured tag in both sets (e.g. `[Alex]`). `待定` and `tbd` mean "to be decided"; `agent`
means an agent can do it; `{owner}` means only the owner can.

## Format

- The section heading is exactly one of the two heading lines, and the section runs to the next `## `. A file has one
  to-do section; two (one per set) are a format problem (readers still read both, so nothing is lost).
- One item per line, with one of the three owner tags of either set, after `- [ ] `. In the English set a bracket
  followed by `(` is a markdown link, not a tag. With no items the section is the single empty line of either set.
- Grouped form is also accepted: a `### <tag>` subheading with `- [ ] <text>` lines (no tag) under it. A subheading
  whose tag is not one of the three of its set, and a line whose tag is not recognized, are format problems; they are
  never assigned to the previous group.
- Once an owner item is registered as a task, the key marker is appended to the line (the English one after a space).
  The writer uses half-width parentheses and colon; readers also accept the full-width ones, and `等` with or without
  a space after it. The key is an opaque string (today `task-<project>-<10 hex digits>`); when that task is closed, the
  line is removed by its key.

## A file that mixes the sets

Readers take every line in either set, whatever the reader's own settings: an English instance may still have lines,
or a whole section, written in the zh-Hant set, and a line may carry a tag of one set and a key marker of the other.
A writer writes its own set on what it writes (a new line, a key marker, the empty line, a missing heading) and does
not rewrite lines it does not own; a key is never changed.

## Rules (readers parse with these regexes; one rule per line, a line matches if any rule does)

```heading section
## 待辦
## To-do
```

```heading none
- (無)
- (none)
```

```regex item
^- \[ \] 〔({owner}|agent|待定)〕\s*(.+)$
^- \[ \] \[({owner}|agent|tbd)\](?!\()\s*(.+)$
```

```regex group
^#{3,4}\s*〔({owner}|agent|待定)〕\s*$
^#{3,4}\s*\[({owner}|agent|tbd)\]\s*$
```

```regex bare
^- \[ \] (?!〔)(?!\[[^\]]+\](?!\())(.+)$
```

```regex key
[(（]等 ?{owner}\s*[:：]\s*([^()（）]+?)\s*[)）]\s*$
[(（]waiting on {owner}\s*[:：]\s*([^()（）]+?)\s*[)）]\s*$
```

## Versions

Adding explanation does not bump the version; changing the tags, the key format or the section heading does (readers
accept both until the producers have all switched). v1 stays next to this file until every reader reads v2.
