<!-- contract: managed-files v1 -->
# Files the platform writes into projects (managed files)

The platform (agent-hub) touches only the files below; every other file belongs to the project. Adding an entry means
changing this list first.

| File | Who writes it | Content |
|---|---|---|
| the `<!-- agent-hub:begin -->`…`<!-- agent-hub:end -->` block of `CLAUDE.local.md` | `sync-local-setup.sh` (every SessionStart) | `templates/CLAUDE.md` + the registered `templates/rules/*.md`; nothing outside the block is touched |
| the hooks in `.claude/settings.local.json` | `merge-hook-settings.py` (adds only, never removes) | memory sync, memory reminder, project mailbox |
| a few lines of `.git/info/exclude` | `sync-local-setup.sh` | `.agent-memory/`, `CLAUDE.local.md`, `.claude/settings.local.json` |
| `.agent-memory/AGENT.md` (memory branch) | `sync-local-setup.sh` (commits+pushes this one file) | `templates/AGENT.md` |
| `.agent-memory/STATE.md` (memory branch) | the librarian (model), `knas-tasks.py` (mechanical) | the distilled current state, `## 待辦` (contract state-todo) |
| the `.agent-memory` and `CLAUDE.local.md` symlinks in a worktree | `worktree-memory.sh` | point to the main checkout |
| `~/Library/LaunchAgents/<label>.plist` (not in the repo) | `render-services.py` | the services in the project registry |
| `.agent-memory/memory/<date>.md` (appends a section) | the channel lease runner (`bin/lease-runner.py`, once live) | the handover note of a lease conversation (`## 租借對話 HH:MM(session <id>)`, with the keys of the tasks it sent) |
| the `sandbox` and the few `permissions.deny/allow` entries the platform adds in `.claude/settings.local.json` | `render-project-settings.py` (registry `enforce.sandbox`) | phase-two sandbox and cross-project deny rules; drift is written back |
| `.git/hooks/pre-commit`, `pre-merge-commit` (the ones carrying the platform's marker) | `render-project-settings.py` (registry `gate: strict`) | merge gate enforcement |
