# Staleness anchors in STATE.md

> agent-hub session · 2026-11-01 · r1. Item 3 of "Build" in `competitive-gaps.md`.

## Why

STATE.md is rewritten every night by the librarian from memory notes. It says things about code ("the data check
lives in `src/checks/run.ts`", "`reconcileStoredCloses` only changes closes within 0.5%"). Nothing checks those
statements against the code: the file is renamed, the function is deleted, and STATE.md keeps saying it for weeks.
The librarian cannot see that, because it reads only the memory copy, never the project's code tree.

Copilot Memory guards against this by checking each memory's code citation before it uses the memory. The same idea,
in agent-hub's shape: a STATE.md statement about code can carry an **anchor** (a path, or a path plus a symbol); a
zero-LLM nightly check resolves every anchor against the project's main branch and lists the ones that no longer
resolve, in `todo.md` and the digest; and the librarian is told about them on its next run for that project.

What this is not: a check of every path STATE.md mentions (only anchors are checked, so prose that names a route,
a URL or a file in another repo is never a false alarm), nor a check of `memory/` paths (`librarian.dangling_refs`
already does that, and memory files live on the memory branch, not on main).

## Syntax

An anchor is the anchor sign `⚓` (U+2693; an optional U+FE0F after it is accepted) directly followed by a code span:

    ⚓`src/checks/run.ts`                 a file
    ⚓`src/checks/`                       a directory (trailing slash)
    ⚓`bin/librarian.py#dangling_refs`    a symbol in a file
    ⚓`app/models.py#Order.total`         a dotted symbol

Reading rule: `⚓️? ?` then `` `([^`\n]+)` ``. Inside the span, everything before the last `#` is the path,
the rest is the symbol.

**The anchor replaces the path's code span; it does not add a second mention.** A statement that already writes
`` `src/checks/run.ts` `` gains one character. That keeps STATE.md from growing and keeps the anchor attached to the
words that make the claim.

Why this form, against the two token sets of `contracts/state-todo.v2.md` (`bin/todo_tokens.py`):

- A **label** is a bracket at the start of an item: `〔…〕` or `[…]` not followed by `(`. An anchor starts with `⚓`,
  never with a bracket, and stands inside the text, never at its start after `- [ ] `. The `labelled`, `bare`,
  `tag_start` and `no_box` patterns only look at the start of a line, so no anchor can match them.
- A **key marker** is a parenthesised `(等 {owner}:<key>)` / `(waiting on owner: <key>)` at the end of a line. An anchor
  has no parentheses and no colon form; the `key` / `key_mark` patterns need `等`/`waiting on owner` right after the
  parenthesis.
- Neither set uses `⚓` or backticks. A test (`tests/test_state_anchors.py`) runs every pattern of both sets over lines
  that carry anchors and asserts that the labels, roles and keys read the same with and without them.
- Other candidates were rejected on real data (every STATE.md and memory note on the hub machine, 11-01): `§` is
  already used (22 lines, section references); `` `@path` `` collides with npm scopes and decorators (`@anthropic-ai/sdk`,
  `@dataclass`); `[[path]]` is read by `no_box` as a label without a checkbox on a to-do line. `⚓` had 0 hits.

Malformed anchors are reported, not skipped: an absolute path (`/…`, `~…`), a `..` segment, a URL (`://`), a line
number (`path:42`; line numbers drift with every edit, which is exactly what an anchor must not do), an empty path,
or a symbol that is not `[A-Za-z_$][\w$]*` joined by dots.

## Where anchors may appear

**Not in the to-do section.** The checker ignores anchors inside it (either heading, `todo_tokens.Patterns.section`),
and the librarian is told not to write them there. So `state-todo` stays v2 and nothing that reads the to-do section
changes (`gen-todo.py`, the registrar, the owner's assistant). Reasons:

- a to-do is about work not yet done; it often names a file that does not exist yet ("write `src/export.ts`"), where
  a missing path is the point, not staleness;
- an owner line with a key marker must not change its text (`edits.check_markers`, the registrar removes it by key),
  so a stale anchor on it could never be fixed;
- the key marker has to be the last thing on the line (the `key` regex ends in `$`): an anchor written after it would
  hide the key.

Fenced code blocks are skipped too (an anchor sign in a pasted log is not a claim).

## How an anchor resolves

- **Which STATE.md**: the librarian's memory copy, `hubconf.p("memory_copies")/<project>/STATE.md`. The check runs
  after the librarian, so this is the file it just wrote and pushed; the workspace's `.agent-memory` catches up only on
  the next sync.
- **Which tree**: the project's `paths.workspace` from the registry (`hubconf.roster()`), on its local base branch: the
  first of `main`, `master` that exists, the same rule as `merge-branch` and `worktrees.base_branch`. The local branch,
  not `origin/…` and not the working tree: on the hub machine `merge-branch` fast-forwards the local main, and the
  working tree holds unfinished work that is not a fact yet.
- **Paths are relative to the workspace folder**, which may be a subfolder of its repo: git is asked for
  `<branch>:./<path>` with `-C <workspace>`, and for `git grep` the pathspec is relative the same way.
- **A file or directory is present** when `git cat-file -e <branch>:./<path>` succeeds (a trailing `/` must be a
  tree, otherwise a blob).
- **A symbol is present** when every dot-separated part occurs as a whole word in that file on the branch:
  `git grep -q -w -F -e <part> <branch> -- ./<path>`. This is occurrence, not definition: it works for every language
  with no parser, and a symbol whose definition was deleted while a call to it remains reads as present. Short
  common-word symbols (`run`, `main`) read as present almost always. Both are accepted: the check prefers a missed
  stale anchor to a false one, and the librarian is told to anchor distinctive names.

## What counts as gone

Each anchor gets one outcome:

| outcome | when | reported |
|---|---|---|
| `ok` | path (and symbol) present on the base branch | no |
| `pending` | not on the base branch, but present on another local branch of the repo (a worktree session's branch not merged yet) | digest only, with the branch |
| `renamed` | was on the base branch; the last commit there that touched it renamed it (`git show -M --name-status`) | todo.md + digest, with the new path and the commit |
| `deleted` | was on the base branch; the last commit there that touched it removed it | todo.md + digest, with the commit |
| `missing` | never on the base branch nor on any local branch (a wrong path in the notes, or a branch already deleted) | todo.md + digest |
| `symbol` | the file is present, the symbol is not; if the symbol now occurs in other files on the branch, the first three are named | todo.md + digest |
| `malformed` | the anchor breaks the syntax above | todo.md + digest |

`pending` is the common case right after a session works in a worktree: the librarian reads its notes the same night,
before `merge-branch` lands the code on main. Reporting it as stale would be a false alarm every night until the
merge, so it is listed apart and is not a to-do. "Another local branch" means `git for-each-ref refs/heads`, excluding
the base branch; no state is kept between nights.

The rename and the "now found in" hints are only hints: one git call each, no `--follow` through history, no fuzzy
matching. A directory that was moved reads as `deleted` (its files were renamed one by one).

## Not checked

A project whose STATE.md has no anchors is not mentioned. A project with anchors that cannot be checked (no workspace
in the registry, the workspace is not a git repo, neither `main` nor `master` exists) gets a "not checked: <reason>"
line in the digest section: never a silent "0 stale" (tool neutrality: a missing capability is said, not hidden).

## The job

`bin/state_anchors.py` (the module: parse, resolve, classify; standard library + git) and
`bin/check-state-anchors.py` (the job), shaped like `check-memory-gaps.py`:

- `check-state-anchors.py [--project P]` prints the stale anchors as markdown lines, nothing when there are none;
- `--apply` (platform job `agent-hub/state-anchors`, nightly, `after: [librarian, todo]`) writes todo.md's
  `<!-- state-anchors:begin/end -->` block (stale only; removed when there are none) and the `state-anchors` section of
  today's digest (stale, pending and not-checked), and always prints a one-line summary, so "0 stale" can be told from
  "did not run". `librarian-publish` gets `state-anchors` in its `after:` so the block is in the commit.
- One todo.md line per stale anchor: `- [ ] **<project>**: ⚓`<anchor>` <outcome and hint> — "<the statement>"`,
  the statement cut to 100 characters. All owner-facing strings go through `_()` with `locales/zh-Hant`.
- Zero LLM, no writes outside todo.md and the digest, nothing kept between runs.

## The librarian

**Writing anchors.** One rule added to `templates/prompts/librarian-state-rules.md` (shared by `librarian-state` and
`librarian-state-edits`):

- an anchor marks a statement about code that exists now; write it by putting `⚓` right before the code span of a path
  the statement already names; never as an extra mention;
- only paths that appear, repo-relative, in the memory notes or in STATE.md (the librarian cannot see the code; it
  must not guess a path);
- at most one anchor per statement; a symbol only when the statement is about that symbol and its name is
  distinctive;
- not in the to-do section, not for `memory/` files, URLs, routes, paths outside the project, or history ("was
  removed on 10-24");
- keep every existing anchor exactly as written unless the statement goes, or the note below says it is stale.

**Being told what is stale.** When `process()` runs for a project, it resolves that project's anchors with the same
module (before the call, like `dangling_refs`) and appends a note to the prompt listing the stale ones (`renamed`,
`deleted`, `missing`, `symbol`, `malformed`; not `pending`), asking to update the statement and its anchor when the
notes say where the code went, or drop the anchor (and the statement, if it is no longer true) when they do not. The
note shares the `dangling_note` slot; `record_state_case` records the raw list in `args.json` (`anchors`) so evals
re-render it.

The librarian runs for a project only when its memory changed or a handoff arrived, so a stale anchor in a quiet
project stays in todo.md until the project has activity again. That is accepted: a quiet project's STATE.md is not
being read by sessions either, and a separate nightly fix-up call per stale project would spend a model call to edit
a file nobody is using.

## Evals

The rule change goes through E2 (`docs/design/i18n.md`) on both points, `librarian-state` (production) and
`librarian-state-edits` (shadow), comparing the prompts before and after this change (`new` arms of the same code
before and after; `e2.py run --tag` labels them). Pass = the usual: validity, contract and language not lower,
agreement with the before arm not worse than the before arm's own noise floor. New scores added to e2 for this:

- `anchors`: anchors in STATE.md after the run, and how many were added;
- `anchors_grounded`: the share of added anchors whose path appears verbatim in what the librarian was shown (memory
  diff, STATE.md before, reference index); an ungrounded anchor is a guessed path;
- `anchors_in_todo`: anchors written inside the to-do section (must be 0);
- `growth`: STATE.md length after / before, compared with the before arm (the inflation check);
- `anchor_retention`: on cases whose STATE.md already has anchors, the share still present after.

The frozen cases all have anchor-free STATE.md, so retention cannot be measured on them. Seeded copies of three cases
(their existing code spans of real paths turned into anchors, plus one stale anchor recorded in `args.json`) measure
retention and whether the stale note is acted on (`stale_fixed`: the share of the noted anchors gone or changed after).
The before arm runs on them too: if it already keeps anchors, the "keep" rule is cheap insurance; if it drops them, the
rule is required.

## Out of scope

- Line ranges and content hashes (Copilot's citations carry them). Lines drift with every edit; a hash makes every
  refactor read as stale. Path plus symbol is the stable part.
- Anchors in memory notes or reference documents: the notes are an append-only record of what was true then, and
  reference/ is folded by consolidate; only STATE.md claims to be current.
- Checking anchors against other repos than the project's own.
