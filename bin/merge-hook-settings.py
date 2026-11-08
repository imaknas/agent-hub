#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Claude Code's hook settings.

Project mode (`merge-hook-settings.py <.claude/settings.local.json>`): handed to runtime_claude.install_hooks: the
platform's hooks are replaced as a set with `hub-event <event>`, the user's own are kept (layer 3 of the runtime
abstraction, docs/design/runtime-abstraction.md). Normally sync-local-setup.sh's `hub-event install` does this, without
going through here.

`--worktree` mode (user-level settings): the worktree-memory.sh hooks (runtime_claude.merge_worktree_hooks). A mechanical
merge: the user's own hooks and other settings stay; a worktree-memory.sh hook written for another hub location is
replaced; the same command is never added twice. The owner runs it once, by hand: user-level settings get nothing new
without the owner. After that `brunna upgrade` keeps those entries current (runtime_claude.user_refresh), and doctor
says when they are behind.

There's only one mode: memory tracking always lives in the separate .agent-memory worktree, and hooks always cd into
it first, whether or not the project has an external remote.

Written into the local `.claude/settings.local.json` (gitignored, not in the repo), not the shared
`.claude/settings.json`: the hook commands contain the hub's path on this machine, and in the repo they'd make a
personal tool's name/paths public (happened in a public repo in 2026-09). On a machine change recover-project.sh
regenerates it. Hooks always go into local, whether or not the shared settings.json has them: the hooks old projects
committed into settings.json were all removed on 2026-09-26, and that state is no longer accommodated.
The hub's location is resolved when the hooks are written (hubconf.hub_bin), as for the git hooks the platform writes;
after the hub moves, running this again rewrites them.

Usage: merge-hook-settings.py <path to .claude/settings.local.json>
       merge-hook-settings.py --worktree <user-level settings.json>
         (hub machine: ~/.claude/settings.json; laptop: ~/.claude-personal/settings.json, not the work ~/.claude)
         installs worktree-memory.sh's hooks: a git worktree has no project local settings, so only user-level hooks
         fire. In the main checkout that script does nothing, so it doesn't duplicate the project's hooks. Its Stop hook
         is the wake waiter (docs/design/mailbox-runtimes.md B1).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_claude  # noqa: E402

WORKTREE_HOOKS = runtime_claude.worktree_hooks()   # the table and the merge live in the adapter (it keeps them current)
USAGE = ("usage: merge-hook-settings.py <a project's local settings file>\n"
         "       merge-hook-settings.py --worktree <user-level settings.json>")


def main():
    args = sys.argv[1:]
    # a path, or --worktree and a path: anything else (--help, a typo) writes nothing, never a settings file of that name
    if not (len(args) == 1 and not args[0].startswith("-") or len(args) == 2 and args[0] == "--worktree"):
        print(USAGE, file=sys.stderr)
        sys.exit(2)
    if args[0] != "--worktree":
        # project mode: installed by the runtime's binding (runtime_claude.install_hooks: the platform's are replaced
        # as a set with hub-event, the user's own are kept); sync-local-setup.sh goes through `hub-event install`, not
        # through here.
        path = Path(args[0])
        settings = json.loads(path.read_text()) if path.exists() else {}
        out = json.dumps(runtime_claude.install_hooks(settings), indent=2) + "\n"
        if not path.exists() or path.read_text() != out:
            path.write_text(out)
        return
    # --worktree: the owner installs them, by hand; after that upgrade keeps them current (runtime_claude.user_refresh)
    path = Path(args[1])
    settings = json.loads(path.read_text()) if path.exists() else {}
    out = json.dumps(runtime_claude.merge_worktree_hooks(settings), indent=2) + "\n"
    if not path.exists() or path.read_text() != out:
        path.write_text(out)


if __name__ == "__main__":
    main()
