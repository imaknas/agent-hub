
{{owner}} told us these things about this project through the personal assistant ({{assistant}}). They are newer than
the old memory records, and more reliable:
{{items}}

How to handle them (these handoffs in themselves count as a substantive change):
- [resolves]: {{owner}} has already dealt with or answered the matching to-do. Remove that item from
  `{{todo_heading}}` (match by meaning; the wording may differ), and write the fact into the right section of STATE.md.
- [changes]: the matching to-do still stands, but its premise or date has changed. Rewrite that item according to the
  fact, and update related sections if needed.
- [relevant]: for reference only; update STATE.md only if it affects the current state.
- [answers]: {{owner}} answered a question this project asked (matching to-do = the original question, fact = the
  answer). Write the decision into the related sections; a to-do waiting on this answer is removed or rewritten
  according to it.
- If the matching to-do cannot be found in STATE.md, do not force the fact onto another item; only record it in the
  related section (if it is relevant).
- The "fact" is the assistant's retelling and may sound more certain than what {{owner}} actually said. Where there is a
  quote, the quote wins; when writing into STATE.md keep the uncertain tone (expected, planned, might, intends to) rather
  than stating it as settled.
