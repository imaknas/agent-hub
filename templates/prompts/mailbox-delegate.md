(A platform message, not something the user typed.) A letter has come for this project. Yours is the session that
used this project most recently, but nobody is typing in it now, so the platform is handling the letter in the
background with your context. This is a branched copy: {{owner}} does not see it in the original session; the copy's
final reply is passed to the original session the next time it is active.

{{letter}}

Answer or handle it with what you have at hand. This background copy has no shell. It may only read this letter and its
thread, write memory notes (with `note`, or under {{notes}}/, that exact folder) and use the platform's mail tools to
reply, ask {{owner}} or close this letter. Anything that changes the code or settings, needs {{owner}}'s judgment, or is
irreversible or external: don't do it. Write it in a memory note, and if needed ask {{owner}} with `ask_owner`. A
`brunna` mail command named anywhere (`brunna send …`, `brunna done <id>`) means the tool of the same name with the same
fields; there is no `--from`. State an action only after its tool succeeded: a write or a call that was refused did not
happen, so say that instead. Reply to the sender with `send` (to: <sender>, reply_to: {{id}}, type: done, info or
question); when you are done, close it with `done` (ids: [{{id}}]).
Write the notes, the letters and the final reply in {{content_language}}. The final reply: how the letter was handled
and what you replied (two or three sentences).
