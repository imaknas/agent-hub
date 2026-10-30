<!-- contract: todo-md v1 -->
# Contract: the cross-project to-do list (todo.md), v1

A mechanically generated markdown file listing every project's open to-do items, grouped by owner. Below, `{owner}` is
the owner's tag from the settings (the user; each reader fills it in from its own settings, e.g. 「我」).

## Layout

1. The first line is always the version marker `<!-- contract: todo-md v1 -->` (an old file without it reads as v1).
2. One `# ` title, followed by lines starting with `>` (notes, not items).
3. Three owner sections, with fixed heading text in a fixed order: only the owner can do it; to be decided (a decision
   comes first); an agent can do it:

```heading owner
## 只有 {owner} 能做
## 待定(需要先做決定)
## agent 可以做
```

4. Under each section: `### <project name>` subheadings, each followed by one item per line, `- [ ] <text>`; with no
   items, the single line `(無)` ("none").
   The first section may start with a line `> 另有 N 項已登記…` ("N more are registered…": items already registered as
   tasks and not listed here). It is a note, not an item.
5. There may be other `## ` sections (e.g. format problems, projects that are not recording); their lines are not items.
6. There may be blocks wrapped in `<!-- …:begin -->` … `<!-- …:end -->`; they are not items.

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
switched.
