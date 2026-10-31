- Write in {{content_language}}: everything you add to STATE.md (prose and to-do items), and the summary and reason in
  your answer, are in that language, in the style of the text already there. Code, paths, commands and names stay as
  they are.
- Edit STATE.md only when there is a *substantive change* (it is a distilled current state, not a log); with no
  substantive change, do not touch STATE.md at all.
  If STATE.md still holds the placeholder content from onboarding while memory/ already has substantive content, that
  in itself counts as a substantive change.
- Where STATE.md mentions files that have been moved, or open questions that newer content has answered or overturned,
  fix those too.
- Code anchors: `⚓` right before the code span of a path (⚓`src/job.py`, or ⚓`src/job.py#run_job` for one named
  function or class) marks a statement about code as it exists now; a nightly check flags it once the path or name is
  gone from the project's main branch. Keep every existing anchor exactly as written. Add one only by putting `⚓`
  before a code span the statement already has, for a repo-relative path of tracked source code or docs that the
  memory notes or STATE.md name verbatim; at most one per statement; `#name` only when the statement is about that
  function or class and the name is distinctive. Never in the `{{todo_heading}}` section, and never for data, output,
  cache or ignored files, `memory/` files, URLs, routes, paths in other repositories, or code that no longer exists.
- STATE.md must have a `{{todo_heading}}` section in a fixed format, one item per line:
  `- [ ] {{tag_owner}}…` (only {{owner}} can do it: accounts, passwords, payments, a decision or an answer that is
  {{owner}}'s to give)
  `- [ ] {{tag_agent}}…` (an agent can do it directly)
  `- [ ] {{tag_tbd}}…` (blocked until something still open is settled, other than a decision that is {{owner}}'s: an
  item waiting on {{owner}}'s decision is {{tag_owner}})
  The `{{key_marker}}` at the end of a {{tag_owner}} line is the key of {{assistant}}'s task registry: **keep it exactly as
  it is, and do not change that line's text** (when {{assistant}} closes the task, {{owner}}'s line is removed
  automatically by its key; if the text changes, the key no longer matches).
  With no to-dos, write `{{todo_none}}`. List only what is not yet done and concretely actionable; remove what is done,
  overturned or out of date instead of leaving it ticked. To-dos go only in this section: do not open other lists such
  as "next steps" or "not done yet" anywhere else in STATE.md.
