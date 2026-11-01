# Sourced by shell scripts: where the instance directory is (same rules as hubconf.config_dir/data_dir/secrets_dir,
# docs/design/new-user.md 3.1), using only shell + plutil (macOS) / jq (other systems): the remote machine (the laptop)
# doesn't necessarily have the platform's Python.
#   HUB_ROOT     the platform (this repo)    HUB_CONFIG  config (hub.json, registry, mirror-list.txt, services.tsv)
#   HUB_DATA     data (digest/, reflect/, todo.md, index.md, review-log.md…)    HUB_SECRETS  credentials
#   HUB_JSON     full path of hub.json; hub_json_get <dotted key> reads one value (prints nothing, non-zero if absent)
#   HUB_MAIN     the main checkout (HUB_ROOT, or for a linked worktree of the hub the checkout it belongs to: the
#                machine's .hub-home, .state and .logs are there, as in hubconf)    HUB_STATE   <main>/.state
# <hub>/.hub-home holds the instance directory → <instance>/{config,data,secrets}; no such file = the old layout
# (everything in the hub root).
HUB_ROOT="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HUB_MAIN="$HUB_ROOT"
# a linked worktree: .git is a file "gitdir: <dir>", and <dir>/commondir leads to the main checkout's .git (read as
# plain files, no git; an explicit AGENT_HUB_ROOT is taken as it is)
if [ -z "${AGENT_HUB_ROOT:-}" ] && [ -f "$HUB_ROOT/.git" ]; then
  _hp_gd="$(sed -n '1s/^gitdir: *//p' "$HUB_ROOT/.git")"
  case "$_hp_gd" in /*) ;; *) _hp_gd="$HUB_ROOT/$_hp_gd" ;; esac
  if [ -f "$_hp_gd/commondir" ]; then
    _hp_cd="$(head -1 "$_hp_gd/commondir")"
    case "$_hp_cd" in /*) ;; *) _hp_cd="$_hp_gd/$_hp_cd" ;; esac
    _hp_cd="$(cd "$_hp_cd" 2>/dev/null && pwd -P)"
    [ "${_hp_cd##*/}" = .git ] && HUB_MAIN="${_hp_cd%/.git}"
  fi
fi
HUB_STATE="${AGENT_HUB_STATE:-$HUB_MAIN/.state}"
_hp_home="${AGENT_HUB_HOME:-}"
[ -n "$_hp_home" ] || { [ -f "$HUB_MAIN/.hub-home" ] && _hp_home="$(head -1 "$HUB_MAIN/.hub-home")"; }
_hp_home="${_hp_home/#\~/$HOME}"
if [ -n "$_hp_home" ]; then
  HUB_CONFIG="${AGENT_HUB_CONFIG:-$_hp_home/config}"; HUB_DATA="${AGENT_HUB_DATA:-$_hp_home/data}"
  HUB_SECRETS="${AGENT_HUB_SECRETS:-$_hp_home/secrets}"
else
  HUB_CONFIG="${AGENT_HUB_CONFIG:-$HUB_ROOT}"; HUB_DATA="${AGENT_HUB_DATA:-$HUB_ROOT}"
  HUB_SECRETS="${AGENT_HUB_SECRETS:-$HUB_ROOT/.secrets}"
fi
HUB_JSON="$HUB_CONFIG/hub.json"
# macOS uses the built-in plutil; other systems jq (bin/setup requires it). Either way: no such value (or not a single
# value) → print nothing, return non-zero
hub_json_get() {
  if command -v plutil >/dev/null 2>&1; then
    plutil -extract "$1" raw -o - "$HUB_JSON" 2>/dev/null
  else
    jq -er --arg k "$1" 'getpath($k | split(".")) | select(type == "string" or type == "number" or type == "boolean")' \
      "$HUB_JSON" 2>/dev/null
  fi
}
# The name of a project's git remote for its memory bare repo on the hub machine (the .agent-memory branch's upstream).
# Older checkouts called it mini; sync-local-setup.sh renames those.
MEMORY_REMOTE=memory
# The machine that runs the hub (hosts.server; the old key memory_host); empty = one machine does everything
hub_server() { hub_json_get hosts.server || hub_json_get memory_host; }
# Owner-facing text in the UI language (docs/design/i18n.md): hub_msg "<English msgid>" [name=value …] prints it without
# a newline, hub_say adds one. The domain is the file the call is in (BASH_SOURCE), so its catalog is
# locales/<lang>/<that file's name>.json, looked up by bin/i18n.py. Without the platform's Python (the remote machine,
# before bin/setup-python) the English msgid comes out, its {name} placeholders filled. The msgid is a plain "…" literal
# (no $, no backquote): tests/test_i18n_catalog.py reads it from the source.
# hub_content is the same for content (what goes into a project's or the data dir's files): the content language.
_hp_bin="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_hub_msg() {
  local mode="$1" dom="${2##*/}" id="$3" out kv; shift 3
  if out="$("$_hp_bin/py" "$_hp_bin/i18n.py" "$mode" "$dom" "$id" "$@" 2>/dev/null)"; then
    printf '%s' "$out"; return 0
  fi
  for kv in "$@"; do id="$(_hp_replace "$id" "{${kv%%=*}}" "${kv#*=}")"; done
  id="$(_hp_replace "$id" "{{" "{")"; _hp_replace "$id" "}}" "}"
}
# literal replace-all: ${s//pat/rep} treats rep differently in bash 3.2 (quotes kept) and 5.2 (& is the match)
_hp_replace() {
  local s="$1" out=""
  while [[ "$s" == *"$2"* ]]; do out+="${s%%"$2"*}$3"; s="${s#*"$2"}"; done
  printf '%s' "$out$s"
}
hub_msg() { _hub_msg msg "${BASH_SOURCE[1]}" "$@"; }
hub_say() { _hub_msg msg "${BASH_SOURCE[1]}" "$@"; echo; }
hub_content() { _hub_msg content "${BASH_SOURCE[1]}" "$@"; }
