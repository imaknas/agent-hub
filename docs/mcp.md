# 平台的 MCP 伺服器:任何 coding agent 都能用專案信箱

`agenthub mcp` 是一個 stdio 的 MCP 伺服器(`bin/mcp_server.py`)。把它登記進你用的 coding agent,那個 agent 在專案資料夾裡開的
session 就能:

| 工具 | 做什麼 |
|---|---|
| `inbox` | 這個專案還沒結案的信(順便記成看過) |
| `show` / `thread` | 看一封信、一整串往來 |
| `send` | 寄信給另一個專案,或回覆(`reply_to`) |
| `ack` | 認領:要處理但不是現在 |
| `done` | 結案(可附 note) |
| `ask_owner` | 問 owner 一件要他決定的事 |
| `note` | 在這個專案今天的 memory 筆記加一條 |

跟命令列 `agenthub inbox|show|thread|send|ack|done` 同一份實作(`bin/hubbus`)。伺服器 `initialize` 時把平台的規則跟現在還沒結案的
信數放在 `instructions` 裡——不用改專案的任何檔案,規則就送進 agent。

## 哪個專案

工作目錄屬於哪個登錄過的專案(git worktree、`.agent-memory` 都算主專案);工具不是在專案資料夾裡啟動 MCP 伺服器的,在設定的
env 加 `AGENTHUB_PROJECT=<專案名稱>`。認不出來時每個工具都回錯誤、說怎麼設。

## 登記

平台不替你改工具的設定。`agenthub mcp config` 印出要用的那一段(用絕對路徑:從 GUI 開的工具不一定有你的 PATH)。

- **Codex CLI**:`codex mcp add agenthub -- ~/.local/bin/agenthub mcp`
- **Antigravity CLI**:`agy mcp add agenthub ~/.local/bin/agenthub mcp`
- **Claude Code**:平台已經用 hook 把信送進 session,不用登記;要的話 `claude mcp add agenthub -- ~/.local/bin/agenthub mcp`
- **其他支援 MCP 的工具**(Cursor、Gemini CLI…):照它的 MCP 設定格式,把 `agenthub mcp config` 印出的那段放進去

## 已知的限制

- **平台叫起的 agent 不能用**(環境變數 `HUB_LLM_POINT`):那種工作要寄的信寫在輸出裡、由平台代寄。
- MCP 伺服器是 agent 之外的另一個程序,**不受 agent 自己的沙盒限制**(例:Codex `--sandbox read-only` 時 `note` 照樣寫得進
  memory)。它只會寫這個專案的 memory 筆記跟匯流排。
- 只有 stdio;HTTP 傳輸、`roots/list` 還沒做。
- `send` 被 client 重試會寄兩封(信 id 由平台產生)。
- 讀信只算「看過」,不知道是哪個 session(MCP 不帶 session id):60 分鐘內沒 ack、沒回、沒 done 的 request/question,平台照樣
  會派別人處理。
