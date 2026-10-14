# 給「可能在 MBP、也可能在 mini 上跑」的腳本 source 用。
#
# 2026-10-15 起所有專案都在 mini 上(~/workspace/<name>),agent-hub 的 session 也跑在 mini。
# mini 解析不到自己的 tailnet 名稱(ssh hub-host 在 mini 上會失敗),所以「在 mini 上
# 做某件事」在 mini 本機就直接執行,在別台(MBP)才走 ssh。
#
# 判斷「這台就是 mini」:~/git-origin(memory 的 bare repo 都在這裡)存在。

MINI_HOST="hub-host"

on_mini() { [ -d "$HOME/git-origin" ]; }

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
  if on_mini; then echo "$HOME/git-origin/$1-memory.git"; else echo "$MINI_HOST:~/git-origin/$1-memory.git"; fi
}
