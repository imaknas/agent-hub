- Write in {{content_language}}: everything you add to STATE.md (prose and to-do items) is in that language, in the
  style of the text already there. Code, paths, commands and names stay as they are.
- Edit STATE.md only when there is a *substantive change* (it is a distilled current state, not a log); with no
  substantive change, do not touch STATE.md at all.
  If STATE.md still holds the placeholder content from onboarding while memory/ already has substantive content, that
  in itself counts as a substantive change.
- Where STATE.md mentions files that have been moved, or open questions that newer content has answered or overturned,
  fix those too.
- STATE.md must have a `{{todo_heading}}` section in a fixed format, one item per line:
  `- [ ] {{tag_owner}}…` (only {{owner}} can do it: accounts, passwords, payments, decisions {{owner}} has to make)
  `- [ ] {{tag_agent}}…` (an agent can do it directly)
  `- [ ] {{tag_tbd}}…` (a decision has to be made before anyone can act)
  The `{{key_marker}}` at the end of a {{tag_owner}} line is the key of {{assistant}}'s task registry: **keep it exactly as
  it is, and do not change that line's text** (when {{assistant}} closes the task, {{owner}}'s line is removed
  automatically by its key; if the text changes, the key no longer matches).
  With no to-dos, write `{{todo_none}}`. List only what is not yet done and concretely actionable; remove what is done,
  overturned or out of date instead of leaving it ticked. To-dos go only in this section: do not open other lists such
  as "next steps" or "not done yet" anywhere else in STATE.md.
