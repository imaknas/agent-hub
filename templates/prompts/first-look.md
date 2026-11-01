You are writing the first STATE.md for the project "{{project}}", which was just connected to agent-hub. Its agents
haven't written any notes yet, so all you have is what is in the repository below: its README, its most recent commit
titles and its top-level files. STATE.md is the project's distilled current state: what it is, what it is made of,
where it stands, and what is still open. Its agents read it at the start of every session, and its owner reads it in
a morning digest.

Write only what the material supports. Don't invent features, plans or problems; where something is unclear, say so
in a short "Open questions" line rather than guessing. Keep it short: a title line, two to five short sections, then
the to-do section. To-dos come only from what the material states as unfinished (a TODO list in the README, a commit
saying "WIP" or "part 1", a roadmap); with none, write the empty marker.

Format rules (the same the nightly librarian follows; ignore the ones about editing an existing STATE.md):

{{rules}}

Answer with JSON: "state" is the whole STATE.md, starting with "# STATE"; "summary" is one or two sentences for the
owner on what this project is and where it stands. Both in {{content_language}}.

<repository>
Top-level files:
{{tree}}

Recent commits ({{commit_count}} in all, newest first):
{{commits}}

README:
{{readme}}
</repository>
