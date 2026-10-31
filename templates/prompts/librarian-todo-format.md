Fix only the format of the `{{todo_heading}}` section of ./STATE.md. Do not change anything else, nor the content of the
to-dos, and do not touch any other file.

The correct format (one item per line; with no to-dos write `{{todo_none}}`; STATE.md must have this section):
  `- [ ] {{tag_owner}}…` (only {{owner}} can do it: accounts, passwords, payments, decisions {{owner}} has to make, things
  {{owner}} has to buy or do personally)
  `- [ ] {{tag_agent}}…` (an agent can do it directly)
  `- [ ] {{tag_tbd}}…` (a decision has to be made before anyone can act)

If STATE.md has to-do-like lists elsewhere (for example "open questions", "needs {{owner}} to decide", "can be done by
an agent", "next steps", "not done yet"), **move** those items into `{{todo_heading}}` (marking who is responsible) and
delete the original section; write `{{todo_none}}` only when the whole STATE.md truly has no to-dos. Schedules and facts
about finished work are not to-dos: leave them where they are. Anything you write is in {{content_language}}, like the
rest of the file.

The current problems:
{{problems}}
