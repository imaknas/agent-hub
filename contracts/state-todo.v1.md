<!-- contract: state-todo v1 -->
# Contract: the `## 待辦` (to-do) section of a project's STATE.md, v1

Every project has one status file with one fixed to-do section. Below, `{owner}` is the owner's tag from the settings
(each reader fills it in from its own settings).
This file is rewritten by a model, so **it carries no version marker** (a model drops lines it does not recognize); the
version is set by this contract.

The tokens are Chinese because the data is: they are what the file contains, not translated prose.

## Format

- The section heading is exactly the line `## 待辦` ("to-do"), and the section runs to the next `## `.
- One item per line, with one of three owner tags: `〔{owner}〕` (only the owner can do it), `〔agent〕`, `〔待定〕` ("to be
  decided"). With no items the section is the single line `- (無)` ("none").
- Grouped form is also accepted: a `### 〔owner〕` subheading with `- [ ] <text>` lines (no tag) under it. A subheading
  whose tag is not one of the three, and a line whose tag is not recognized, are format problems (listed at the end of
  todo.md); they are never assigned to the previous group.
- Once an owner item is registered as a task, `(等 {owner}:<key>)` ("waiting on owner: key") is appended to the line
  (full-width parentheses; the colon may be half- or full-width). The key is an opaque string (today
  `task-<project>-<10 hex digits>`); when that task is closed, the line is removed by its key.

```regex item
^- \[ \] 〔({owner}|agent|待定)〕\s*(.+)$
```

```regex group
^#{3,4}\s*〔({owner}|agent|待定)〕\s*$
```

```regex bare
^- \[ \] (?!〔)(.+)$
```

```regex key
[((]等 ?{owner}[::]\s*([^()()]+?)\s*[))]\s*$
```

## Versions

Adding explanation does not bump the version; changing the tags, the key format or the section heading does (readers
accept both until the producers have all switched).
