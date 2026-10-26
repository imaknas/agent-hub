# 給「可能在 MBP、也可能在 mini 上跑」的腳本 source 用。
#
# 2026-10-15 起所有專案都在 mini 上(~/workspace/<name>),agent-hub 的 session 也跑在 mini。
# mini 解析不到自己的 tailnet 名稱(ssh hub-host 在 mini 上會失敗),所以「在 mini 上
# 做某件事」在 mini 本機就直接執行,在別台(MBP)才走 ssh。
#
# 判斷「這台就是 mini」:hub.json 的 hosts.server 沒寫(一台機器全包),或 git_origin(memory 的 bare repo 都在這裡)
# 在這台。server 的 ssh 名稱從 hub.json 讀(hubconf.host("server"));用 plutil 讀 JSON,不靠平台的 Python——
# 遙控用的那台(MBP)不一定有。

_MINILIB_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
hub_json_get() { plutil -extract "$1" raw -o - "$_MINILIB_HUB/hub.json" 2>/dev/null; }
MINI_HOST="$(hub_json_get hosts.server)"
[ -n "$MINI_HOST" ] || MINI_HOST="$(hub_json_get memory_host)"
_GIT_ORIGIN="$(hub_json_get git_origin)"; _GIT_ORIGIN="${_GIT_ORIGIN:-~/git-origin}"; _GIT_ORIGIN="${_GIT_ORIGIN/#\~/$HOME}"

on_mini() { [ -z "$MINI_HOST" ] || [ -d "$_GIT_ORIGIN" ]; }

# mini_sh '<指令>':在 mini 上執行(字串裡的 ~、$HOME 由 mini 那邊展開)
mini_sh() {
  if on_mini; then bash -c "$1"; else ssh -o BatchMode=yes "$MINI_HOST" "$1"; fi
}

# mini_bash_stdin <<EOF ... EOF:把整段 script 丟到 mini 執行
mini_bash_stdin() {
  if on_mini; then bash -s; else ssh -o BatchMode=yes "$MINI_HOST" "bash -s"; fi
}

# mini_memory_url <name>:專案 .agent-memory 的 mini remote 要設成什麼
mini_memory_url() {
  if on_mini; then echo "$_GIT_ORIGIN/$1-memory.git"; else echo "$MINI_HOST:~/git-origin/$1-memory.git"; fi
}
