# 給 shell 腳本 source:實例目錄的位置(跟 hubconf.config_dir/data_dir/secrets_dir 同一套規則,docs/design/new-user.md 3.1),
# 只用 shell + plutil(macOS)/ jq(其他系統)——遙控那台(MBP)不一定有平台的 Python。
#   HUB_ROOT     平台(這份 repo)    HUB_CONFIG  config(hub.json、登錄、mirror-list.txt、services.tsv)
#   HUB_DATA     data(digest/、reflect/、todo.md、index.md、review-log.md…)    HUB_SECRETS  憑證
#   HUB_JSON     hub.json 的完整路徑;hub_json_get <點分隔的 key> 讀單一值(沒有就印空、回非 0)
# <hub>/.hub-home 寫著實例目錄 → <實例>/{config,data,secrets};沒有這個檔 = 舊的擺法(全部在 hub 根目錄)。
HUB_ROOT="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_hp_home="${AGENT_HUB_HOME:-}"
[ -n "$_hp_home" ] || { [ -f "$HUB_ROOT/.hub-home" ] && _hp_home="$(head -1 "$HUB_ROOT/.hub-home")"; }
_hp_home="${_hp_home/#\~/$HOME}"
if [ -n "$_hp_home" ]; then
  HUB_CONFIG="${AGENT_HUB_CONFIG:-$_hp_home/config}"; HUB_DATA="${AGENT_HUB_DATA:-$_hp_home/data}"
  HUB_SECRETS="${AGENT_HUB_SECRETS:-$_hp_home/secrets}"
else
  HUB_CONFIG="${AGENT_HUB_CONFIG:-$HUB_ROOT}"; HUB_DATA="${AGENT_HUB_DATA:-$HUB_ROOT}"
  HUB_SECRETS="${AGENT_HUB_SECRETS:-$HUB_ROOT/.secrets}"
fi
HUB_JSON="$HUB_CONFIG/hub.json"
# macOS 用內建的 plutil;其他系統用 jq(bin/setup 要求有)。兩邊都是:沒有這個值(或不是單一值)→ 印空、回非 0
hub_json_get() {
  if command -v plutil >/dev/null 2>&1; then
    plutil -extract "$1" raw -o - "$HUB_JSON" 2>/dev/null
  else
    jq -er --arg k "$1" 'getpath($k | split(".")) | select(type == "string" or type == "number" or type == "boolean")' \
      "$HUB_JSON" 2>/dev/null
  fi
}
# 跑 hub 的那台(hosts.server;舊的鍵 memory_host);空 = 一台機器全包
hub_server() { hub_json_get hosts.server || hub_json_get memory_host; }
# Owner-facing text in the UI language (docs/design/i18n.md): hub_msg "<English msgid>" [name=value …] prints it without
# a newline, hub_say adds one. The domain is the file the call is in (BASH_SOURCE), so its catalog is
# locales/<lang>/<that file's name>.json, looked up by bin/i18n.py. Without the platform's Python (the remote machine,
# before bin/setup-python) the English msgid comes out, its {name} placeholders filled. The msgid is a plain "…" literal
# (no $, no backquote): tests/test_i18n_catalog.py reads it from the source.
_hp_bin="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_hub_msg() {
  local dom="${1##*/}" id="$2" out kv; shift 2
  if out="$("$_hp_bin/py" "$_hp_bin/i18n.py" msg "$dom" "$id" "$@" 2>/dev/null)"; then
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
hub_msg() { _hub_msg "${BASH_SOURCE[1]}" "$@"; }
hub_say() { _hub_msg "${BASH_SOURCE[1]}" "$@"; echo; }
