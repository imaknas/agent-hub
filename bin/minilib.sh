# Sourced by scripts that "may run on the laptop, or on the hub machine".
#
# Since 2026-10-15 every project lives on the hub machine (~/workspace/<name>), and agent-hub's session runs there too.
# The hub machine can't resolve its own tailnet name (ssh to its own name fails on the hub machine), so "doing something
# on the hub machine" runs directly when on the hub machine itself, and goes through ssh only from another machine
# (the laptop).
#
# Deciding "this is the hub machine": hub.json's hosts.server is not set (one machine does everything), or git_origin
# (where all the memory bare repos are) is on this machine. The server's ssh name is read from hub.json
# (hubconf.host("server")); JSON is read with plutil, not the platform's Python: the remote machine (the laptop) may
# not have it.

source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
MINI_HOST="$(hub_server)"
_GIT_ORIGIN="$(hub_json_get git_origin)"; _GIT_ORIGIN="${_GIT_ORIGIN:-~/git-origin}"; _GIT_ORIGIN="${_GIT_ORIGIN/#\~/$HOME}"

on_mini() { [ -z "$MINI_HOST" ] || [ -d "$_GIT_ORIGIN" ]; }

# mini_sh '<command>': run on the hub machine (~ and $HOME in the string are expanded on the hub machine's side)
mini_sh() {
  if on_mini; then bash -c "$1"; else ssh -o BatchMode=yes "$MINI_HOST" "$1"; fi
}

# mini_bash_stdin <<EOF ... EOF: hand a whole script to the hub machine to run
mini_bash_stdin() {
  if on_mini; then bash -s; else ssh -o BatchMode=yes "$MINI_HOST" "bash -s"; fi
}

# mini_memory_url <name>: what a project's .agent-memory remote on the hub machine should be set to
mini_memory_url() {
  if on_mini; then echo "$_GIT_ORIGIN/$1-memory.git"; else echo "$MINI_HOST:~/git-origin/$1-memory.git"; fi
}
