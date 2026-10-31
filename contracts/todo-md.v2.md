<!-- contract: todo-md v2 -->
# Contract: the cross-project to-do list (todo.md), v2

A mechanically generated markdown file listing every project's open to-do items, grouped by owner. Below, `{owner}` is
the owner's tag from the settings (the user; each reader fills it in from its own settings, e.g. `Alex`).

v2 = v1 plus a second, English heading set. The file is written in one set, chosen by the producer's interface
language; its version marker names the lowest version a reader needs: `v1` for the zh-Hant set (byte for byte a v1
file), `v2` for the English set.

## Layout

1. The first line is always the version marker `<!-- contract: todo-md v1 -->` or `<!-- contract: todo-md v2 -->` (an
   old file without it reads as v1).
2. One `# ` title, followed by lines starting with `>` (notes, not items).
3. Three owner sections, with fixed heading text in a fixed order: only the owner can do it; to be decided (a decision
   comes first); an agent can do it. One set per file: the zh-Hant set (any marker), or the English set (marker v2):

```heading owner
## 只有 {owner} 能做
## 待定(需要先做決定)
## agent 可以做
```

```heading owner-en
## Only {owner} can do
## To be decided (a decision comes first)
## An agent can do
```

4. Under each section: `### <project name>` subheadings, each followed by one item per line, `- [ ] <text>`; with no
   items, the single line `(無)` (zh-Hant) or `(none)` (English).
   The first section may start with a `>` note saying how many more items are registered as tasks and not listed here.
   It is a note, not an item.
5. There may be other `## ` sections (e.g. format problems, projects that are not recording); their lines are not items.
6. There may be blocks wrapped in `<!-- …:begin -->` … `<!-- …:end -->`; they are not items.

The item text is copied from the projects' files, so it is in their content language, whatever the heading set.

## Rules (readers parse with these regexes)

```regex project
^### (\S+)\s*$
```

```regex item
^- \[ \] (.+)$
```

Readers take only items that are inside an owner section and under a `### project` subheading; every other line is
skipped.

## Versions

Adding sections or notes does not bump the version (readers already skip lines they do not recognize); changing the
three owner headings or the item/project format does, and the producer writes both versions until the readers have
switched. v1 stays next to this file until every reader reads v2.
