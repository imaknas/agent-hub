---
name: recover-projects
description: Restore agent-hub-onboarded projects on a new machine, or after a local project folder is lost/deleted. Use when setting up a fresh machine that needs some or all of these projects, or when a specific onboarded project's local directory is missing. Reads the project registry (projects/*.yaml) and walks through what to restore.
---

# Recover Projects

This skill handles the "new machine" and "local folder lost" recovery cases. Unlike onboarding a brand-new project,
don't use `onboard-project.sh` for this; details in the comment at the top of `bin/recover-project.sh`. This skill is
responsible for the judgment the purely mechanical script cannot make.

## Prerequisites

The new machine must first be able to reach the hub machine (hub.json `hosts.server`, an SSH alias) and GitHub (SSH key);
if either doesn't work, everything after fails. Check first:

```bash
ssh <the hub machine's ssh name> echo ok
ssh -T git@github.com
```

## Steps

1. Make sure `~/agent-hub` itself exists and is up to date (`git pull`). If this is a brand-new machine, first clone it
   by hand: `git clone <the hub machine's ssh name>:~/git-origin/agent-hub.git ~/agent-hub`, then build the platform's
   Python (`~/agent-hub/bin/setup-python`, needs uv first) and install the commit gate
   (`git -C ~/agent-hub config core.hooksPath githooks`).
   Without the platform's Python, every platform script (including every project's hooks) fails.

2. Run `~/agent-hub/bin/py ~/agent-hub/bin/hubconf.py roster` (the project registry `projects/*.yaml`, one project per
   line: name, path, origin, memory bare repo) and for each line check whether the path (second column) exists on this
   machine. Skip the ones that exist; they need nothing. The registry's paths are `~/workspace/<name>` on the hub
   machine; to recover to another location (e.g. another machine with a different user name), give
   `recover-project.sh` a second argument.
   The old locations on the laptop are recorded in `legacy-paths.tsv`, only for the statistics on old conversations;
   they are not a restore target.

3. **For each project that doesn't exist, ask the user first; don't assume all of them need recovering**:
   - Is this machine fully replacing the old one (usually everything gets recovered), or an additional second machine
     (maybe only the few projects currently in use)?
   - Are there projects that are actually no longer needed and don't need recovering?

   Don't silently run the restore for all 15 projects in one go: this is a judgment call, not a mechanical step.

4. For each project the user confirmed for recovery, check the `origin` column:
   - **Not `(native)`**: run `bin/recover-project.sh <name>` directly (add the second argument if the path needs
     overriding). The script itself is idempotent and mechanical; no extra judgment needed.
   - **`(native)`**: stop and tell the user: this project's real code has no backup anywhere except the original
     machine's disk. `recover-project.sh` fails right away and prints this fact. To really recover it, the user has to
     find the original machine's disk or another backup channel themselves (Time Machine etc., outside agent-hub's
     remit); don't try to guess or rebuild the code from the contents of `memory_bare_repo`: that holds only memory
     notes, not source code.

5. When everything has run, remind the user:
   - This is the first time in this series that "two machines both use the same project" (if the old machine is not
     retired): `.agent-memory` pushes rely on `git-safe-push.sh` to handle divergence automatically, which is usually
     fine, but if both sides really work in the same session at the same time they can still collide.
   - The scheduler/librarian on the hub machine needs no adjusting at all for the machine change: they only know the
     bare repos and `~/.memory-copies/` on the hub machine, and neither know nor care which machine interactive dev
     happens on.
