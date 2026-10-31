#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Claude Code's hook settings.

Project mode (`merge-hook-settings.py <.claude/settings.local.json>`): handed to runtime_claude.install_hooks: the
platform's hooks (commands containing ~/agent-hub/bin/) are replaced as a set with `hub-event <event>`, the user's own
are kept (layer 3 of the runtime abstraction, docs/design/runtime-abstraction.md). Normally sync-local-setup.sh's
`hub-event install` does this, without going through here.

`--worktree` mode (user-level settings) keeps the old approach: a mechanical merge that doesn't overwrite existing
content or add the same command twice: purely "does this command exist? if not, append".

There's only one mode: memory tracking always lives in the separate .agent-memory worktree, and hooks always cd into
it first, whether or not the project has an external remote.

Written into the local `.claude/settings.local.json` (gitignored, not in the repo), not the shared
`.claude/settings.json`: the hook commands contain ~/agent-hub paths, and in the repo they'd make a personal tool's
name/paths public (happened in a public repo in 2026-09). On a machine change recover-project.sh regenerates it.
Hooks always go into local, whether or not the shared settings.json has them: the hooks old projects committed into
settings.json were all removed on 2026-09-26, and that state is no longer accommodated.
Hooks still always check first whether `.agent-memory` exists, and if not the whole line quietly does nothing
(`|| true` makes sure the exit code is always 0, so sessions on other machines don't show errors because of this
hook).

Usage: merge-hook-settings.py <path to .claude/settings.local.json>
       merge-hook-settings.py --worktree <user-level settings.json>
         (hub machine: ~/.claude/settings.json; laptop: ~/.claude-personal/settings.json, not the work ~/.claude)
         installs worktree-memory.sh's hooks: a git worktree has no project local settings, so only user-level hooks
         fire. In the main checkout that script does nothing, so it doesn't duplicate the project's hooks.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hubconf  # noqa: E402  how to call a platform Python script in a shell string (hubconf.sh_py)

START_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh git pull -q || true"
END_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh || true"
# PreCompact uses the same sync script as SessionEnd (idempotent; nothing to commit, it ends quietly).
# A long session left open for days never fires SessionEnd, but filling the context fires compact; syncing at that
# moment keeps memory/ notes accumulated over a long time from staying uncommitted/unpushed until the session really
# ends.
PRECOMPACT_CMD = END_CMD
# Every session start also aligns the local setup (CLAUDE.local.md's managed block, these hooks, ignores) with
# agent-hub's current version: when the template changes, nobody has to remember to patch every project.
# --quiet: the SessionStart hook's stdout goes into the session context.
SYNC_CMD = "[ -d .agent-memory ] && [ -x ~/agent-hub/bin/sync-local-setup.sh ] && ~/agent-hub/bin/sync-local-setup.sh --quiet || true"

# After a git commit/push, if the memory notes are older than the latest commit, send a reminder into the model's
# context (PostToolUse's additionalContext). Notes not behind: no output at all.
NUDGE_CMD = "[ -d .agent-memory ] && [ -x ~/agent-hub/bin/memory-nudge.py ] && " + hubconf.sh_py("~/agent-hub/bin/memory-nudge.py") + " --post-tool || true"

# Project mailbox (hubbus): deliver new letters into the session in use. The SessionStart one is called by
# sync-local-setup.sh (no separate hook needed). hubbus works out which project it is itself, and prints nothing
# without mail; PostToolUse checks at most once every two minutes per session.
MAIL_PROMPT_CMD = "[ -x ~/agent-hub/bin/hubbus ] && " + hubconf.sh_py("~/agent-hub/bin/hubbus") + " hook prompt || true"
MAIL_TOOL_CMD = "[ -x ~/agent-hub/bin/hubbus ] && " + hubconf.sh_py("~/agent-hub/bin/hubbus") + " hook post-tool || true"

# Phase two: a project's session can't open another project's session (across projects only letters); when
# bin/guard.py blocks it, it returns deny + a reason
GUARD_CMD = "[ -x ~/agent-hub/bin/guard.py ] && " + hubconf.sh_py("~/agent-hub/bin/guard.py") + " pre-tool || true"

# (event, matcher, command); matcher None means none is set (fires on every instance of the event)
HOOKS = (
    ("SessionStart", None, START_CMD),
    ("SessionStart", None, SYNC_CMD),
    ("SessionEnd", None, END_CMD),
    ("PreCompact", None, PRECOMPACT_CMD),
    ("PostToolUse", "Bash", NUDGE_CMD),
    ("UserPromptSubmit", None, MAIL_PROMPT_CMD),
    ("PostToolUse", "Bash", MAIL_TOOL_CMD),
    ("PreToolUse", "mcp__ccd_session__start_session", GUARD_CMD),
)


WT = "[ -x ~/agent-hub/bin/worktree-memory.sh ] && ~/agent-hub/bin/worktree-memory.sh"
WORKTREE_HOOKS = (
    ("SessionStart", None, f"{WT} start || true"),
    ("SessionEnd", None, f"{WT} end || true"),
    ("PreCompact", None, f"{WT} end || true"),
    ("PostToolUse", "Bash", f"{WT} nudge || true"),
    ("UserPromptSubmit", None, f"{WT} prompt || true"),
)


def has_command(entries, command):
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def main():
    args = sys.argv[1:]
    if not (args and args[0] == "--worktree"):
        # project mode: now installed by the runtime's binding (runtime_claude.install_hooks: the platform's are
        # replaced as a set with hub-event, the user's own are kept). HOOKS above is only left for comparing against
        # old settings; sync-local-setup.sh goes through `hub-event install`, not through here.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import runtime_claude
        path = Path(args[0])
        settings = json.loads(path.read_text()) if path.exists() else {}
        out = json.dumps(runtime_claude.install_hooks(settings), indent=2) + "\n"
        if not path.exists() or path.read_text() != out:
            path.write_text(out)
        return
    # --worktree: user-level settings (the hub machine's ~/.claude/settings.json) change only when the owner confirms
    # directly in that session, so it's still the old set of worktree-memory.sh hooks, untouched
    wanted, args = WORKTREE_HOOKS, args[1:]
    path = Path(args[0])
    settings = json.loads(path.read_text()) if path.exists() else {}

    hooks = settings.setdefault("hooks", {})
    for event, matcher, command in wanted:
        entries = hooks.setdefault(event, [])
        if not has_command(entries, command):
            entry = {"hooks": [{"type": "command", "command": command}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            entries.append(entry)

    out = json.dumps(settings, indent=2) + "\n"
    if not path.exists() or path.read_text() != out:
        path.write_text(out)


if __name__ == "__main__":
    main()
