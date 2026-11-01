#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Claude Code's hook settings.

Project mode (`merge-hook-settings.py <.claude/settings.local.json>`): handed to runtime_claude.install_hooks: the
platform's hooks are replaced as a set with `hub-event <event>`, the user's own are kept (layer 3 of the runtime
abstraction, docs/design/runtime-abstraction.md). Normally sync-local-setup.sh's `hub-event install` does this, without
going through here.

`--worktree` mode (user-level settings): the worktree-memory.sh hooks. A mechanical merge: the user's own hooks and
other settings stay; a worktree-memory.sh hook written for another hub location is replaced; the same command is never
added twice.

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
         fire. In the main checkout that script does nothing, so it doesn't duplicate the project's hooks.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hubconf  # noqa: E402  the hub's location as written in a shell string (hubconf.hub_bin)

WT_SCRIPT = "worktree-memory.sh"
WT = f"[ -x {hubconf.hub_bin(WT_SCRIPT)} ] && {hubconf.hub_bin(WT_SCRIPT)}"
# (event, matcher, command); matcher None means none is set (fires on every instance of the event)
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


def drop_stale(entries, wanted):
    """The platform's worktree hooks that aren't one of today's commands (written for where the hub was before) go;
    an entry left with nothing goes with them."""
    out = []
    for e in entries:
        inner = [h for h in e.get("hooks", []) if f"/bin/{WT_SCRIPT}" not in str(h.get("command", ""))
                 or h.get("command") in wanted]
        if inner:
            out.append({**e, "hooks": inner})
    return out


def main():
    args = sys.argv[1:]
    if not (args and args[0] == "--worktree"):
        # project mode: installed by the runtime's binding (runtime_claude.install_hooks: the platform's are replaced
        # as a set with hub-event, the user's own are kept); sync-local-setup.sh goes through `hub-event install`, not
        # through here.
        import runtime_claude
        path = Path(args[0])
        settings = json.loads(path.read_text()) if path.exists() else {}
        out = json.dumps(runtime_claude.install_hooks(settings), indent=2) + "\n"
        if not path.exists() or path.read_text() != out:
            path.write_text(out)
        return
    # --worktree: user-level settings (the hub machine's ~/.claude/settings.json) change only when the owner confirms
    # directly in that session; run by hand, never by the platform
    path = Path(args[1])
    settings = json.loads(path.read_text()) if path.exists() else {}

    hooks = settings.setdefault("hooks", {})
    wanted = {command for _event, _matcher, command in WORKTREE_HOOKS}
    for event in list(hooks):
        hooks[event] = drop_stale(hooks[event], wanted)
    for event, matcher, command in WORKTREE_HOOKS:
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
