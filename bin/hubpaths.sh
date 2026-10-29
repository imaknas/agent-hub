# 給 shell 腳本 source:實例目錄的位置(跟 hubconf.config_dir/data_dir/secrets_dir 同一套規則,docs/design/new-user.md 3.1),
# 只用 shell + plutil——遙控那台(MBP)不一定有平台的 Python。
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
hub_json_get() { plutil -extract "$1" raw -o - "$HUB_JSON" 2>/dev/null; }
# 跑 hub 的那台(hosts.server;舊的鍵 memory_host);空 = 一台機器全包
hub_server() { hub_json_get hosts.server || hub_json_get memory_host; }
