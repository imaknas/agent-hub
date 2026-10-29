# 使用者自己選模型、選哪家 LLM、自己放 API key(開源前)

2026-10-29。目標:agent-hub 開源後,使用者不需要 Claude 訂閱、也不需要 Claude Code,只要一把任何一家的 API key
(或本機的 Ollama)就能跑起平台的核心;有 Claude Code / 其他 coding agent 的人可以照用。現在的行為(Knas 這台:
全部走 Claude Code + headless token)是其中一種設定,不是特例。

延續 `runtime-abstraction.md`(執行環境的介面)跟 `capabilities.md`(中立的權限政策),不取代它們。

## 現況(要改的是什麼)

平台每一次呼叫模型都經過 `bin/llm.py`,而且是 `contracts/llm-points.yaml` 宣告過的 12 個判斷點之一;`llm.py` 把工作交給
`runtime_ports` 的 agent 介面,Claude Code 的指令只在 `runtime_claude.py` 組。介面本身已經可以換,但有三件事把它綁在
Claude 上:

1. **12 個點全部被當成「coding agent 跑一次」**:模型自己用工具讀檔、改 `STATE.md`,權限靠 Claude 的 `allowed_tools`
   擋。這種點換 runtime 只能換成另一個 coding agent(有工具、有權限系統),一把 API key 跑不了。
2. **模型名稱是 Claude 的**:`model_default: haiku|sonnet|opus`、`models.env` 的 `STATE_MODEL=haiku`。
3. **憑證是 Claude 的**:headless token、訂閱登入;`hub doctor`、`claude-login`、`update-claude` 都假設有 Claude Code。

## 根本的改法:先把判斷點分成兩種

| 種類 | 需要什麼 | 誰能跑 |
|---|---|---|
| **completion**(判斷) | prompt 進、合 schema 的 JSON 出;**模型不碰檔案、沒有工具** | 任何 LLM provider(一把 API key、本機模型),也能交給 coding agent 跑 |
| **agent**(代理) | 工具、工作目錄、權限、接續對話 | 只有 coding agent runtime(Claude Code,之後 Codex / Gemini CLI / opencode…) |

**判斷點能做成 completion 就做成 completion**:平台把要給模型看的東西讀好放進 prompt,模型回「要怎麼改」(結構化),
平台檢查後自己寫檔。這不只是為了換 provider:

- **權限從「叫模型不要亂改」變成「平台只寫它允許的東西」**。現在 librarian-state 靠 `--allowedTools Edit(./STATE.md)` +
  `dontAsk` 擋住 haiku 亂改別的檔(10-27 還發現連 MCP 都要另外關);改成 completion 之後模型根本沒有工具,
  平台只接受「新的 STATE.md 內容」,寫哪個檔是平台的程式決定的。安全性質變成機械的,不是翻譯出來的設定。
- **可測**:輸入輸出都是資料,黃金檔能逐位元組比對;不用替身 runtime 模擬工具呼叫。
- **便宜、快**:沒有工具迴圈,一次呼叫。

### 每個點歸哪一種

| 判斷點 | 現在 | 之後 | 模型回什麼 / 為什麼 |
|---|---|---|---|
| librarian-state | agent(Read/Edit STATE.md) | **completion** | `{changed, state_md, reason, applied_handoffs…}`;平台把 memory diff、現在的 STATE.md、轉交、record 全部放進 prompt(現在截斷的 diff 改成「放得下就全放,放不下明確分批」,不再讓模型自己 Read 補) |
| librarian-todo-format | agent(Edit) | **completion** | 只回修好的 `## 待辦` 段落;平台換掉那一段,其他行逐字不動(機械檢查) |
| librarian-index | agent(Edit index.md) | **completion** | 回名冊區塊**外**的註記;名冊區塊本來就由腳本重生 |
| consolidate | agent(多檔 Edit + git rm) | **completion** | 回檔案操作清單 `[{op: write\|remove, path, content}]`;平台照白名單(`memory/reference/**`、`STATE.md`、要 rm 的日期檔)檢查路徑後套用、跑現有的機械檢查(「預計/可能」語氣)。已經有 `--dry-run` 產生提議 diff,等於本來就有這個形狀 |
| reflect-week | completion(tools "") | completion | 不變 |
| login-check | Claude 專用 | **provider 的 `health()`** | 每個設定的 provider 打一次最小請求;Claude Code 的那個留在 runtime_claude |
| agent-job | agent | agent | 使用者寫的任意 job,本來就是「叫一個 agent 做事」 |
| mailbox-escalation / mailbox-conversation | agent | agent | 要讀專案、跑 `hubbus`;conversation 還要接續 session |
| lease-session / lease-work | agent(長駐串流) | agent | 互動、權限轉接到手機 |
| lease-handover | agent(接回 session、tools "") | agent | 要那段對話的 context;靠接續,不是工具 |

結果:**每晚的核心(librarian、consolidate、todo、index、reflect)只要一把 API key 就能跑**。信箱代辦、通道租借、
使用者的 agent job 需要一個 coding agent runtime;沒有的話這些功能**明確關掉**(狀態頁、`hub doctor` 寫清楚「沒有 agent
runtime:信箱交辦會留在信箱等人、租借不能開」),不默默降級。

## 設定的三層

### 1. 判斷點宣告(`contracts/llm-points.yaml`,平台的,使用者不改)

加兩個欄位(加欄位不升版,Muninn 共用的格式照樣讀得懂):

- `kind: completion | agent`
- `tier: fast | standard | strong`——要多強的模型,不寫哪一家的名字。現在的對應:haiku → fast、sonnet → standard、
  opus → strong。

`model_env` / `model_default` 在過渡期保留(舊的設定照讀),之後拿掉;`tools` / `allowed_tools` / `permission_mode` 只剩
agent 點用,而且只有 runtime 讀(現在就是這樣)。agent 點的權限改用中立政策是另一步(見「不在範圍」)。

### 2. 使用者的選擇(`hub.json` 的 `llm`,config 管線驗證)

```jsonc
"llm": {
  "providers": {
    // 名稱自己取;kind 是平台認得的介面
    "anthropic": {"kind": "anthropic", "key": {"secret": "anthropic-api-key"}},
    "openai":    {"kind": "openai", "key": {"env": "OPENAI_API_KEY"}},
    "local":     {"kind": "openai", "base_url": "http://127.0.0.1:11434/v1", "location": "local"},
    "claude-code": {"kind": "agent-runtime", "runtime": "claude"}   // 用 coding agent 跑 completion(Knas 現在的做法)
  },
  "tiers": {                        // tier → provider:model
    "fast": "anthropic:claude-haiku-5-5",
    "standard": "anthropic:claude-sonnet-5-5",
    "strong": "anthropic:claude-opus-5-5"
  },
  "points": {"reflect-week": "local:qwen3:32b"},   // 個別判斷點蓋過 tier(取代 models.env)
  "agent": {                        // agent 點用哪個 coding agent、模型怎麼對應
    "runtime": "claude",            // = 現在的 runtime.default;null = 沒有 agent runtime(上面說的明確關掉)
    "tiers": {"fast": "haiku", "standard": "sonnet", "strong": "opus"}
  }
}
```

- **provider 的 kind**(第一版):
  - `anthropic`:Messages API。
  - `openai`:Chat Completions 相容介面 + `base_url`——同一個實作涵蓋 OpenAI、OpenRouter、Gemini 的相容端點、
    DeepSeek、Ollama、LM Studio、vLLM。
  - `agent-runtime`:把 completion 交給 coding agent 跑(沒有工具、只要輸出)。**Knas 這台的設定就是全部 tier 指到
    `claude-code`**,行為跟現在一樣(訂閱的 headless token、不需要 API key)。
- 只用標準函式庫(`urllib`),不加 SDK 依賴;平台的 venv 維持 3.13 + PyYAML。
- **結構化輸出**:provider 支援 JSON schema 就用(OpenAI `response_format`、Anthropic 用 tool 輸入、Ollama `format`),
  不支援就要求 JSON;**不管哪種平台都用 `minischema` 驗證**,不合格重試一次、再不合格走宣告的 `on_failure`(現在的機制)。
- **舊設定**:`models.env` 照讀,換算成 `points` 的覆寫;`schedulectl set-model` 改寫 `hub.json`。沒有 `llm` 區塊 = 現在的
  行為(全部走 `runtime.default`)。

### 3. API key 放哪

- **值只放 `secrets_dir()`**(`{"secret": "<名稱>"}` → `secrets_dir()/<名稱>`,chmod 600)**或環境變數**(`{"env": "…"}`,
  給習慣用 env 的人、CI)。`hub.json` 裡永遠不准有值:config check 看到像 key 的字串就擋(`sk-…`、長的 base64)。
- `hub init` 問一次要用哪家、把 key 寫進 `secrets_dir()`;之後 `bin/hub llm set-key <provider>`(從 stdin 讀,不進 shell 歷史)。
- **check-secrets 自動涵蓋**:`llm.providers` 裡每個 `secret` 都當成平台宣告的憑證(在不在、權限、有沒有被備份),
  不用另外寫進登錄。
- key 只交給它自己的 provider:發請求時才讀,不放進環境變數傳給子程序(agent runtime 拿它自己的憑證,`auth` 介面已經這樣)。

## 隱私:哪些資料能送哪個 provider

開源之後使用者會把 key 指到各種地方,所以「送去哪」要是設定、不是假設:

- provider 宣告 `location: cloud | local`(預設 cloud)。
- 專案登錄已經有 `privacy.reflection: include | stats_only | exclude`。**同一個標籤也管判斷點**:不是 `include` 的專案,
  它的 memory/STATE 內容只能送 `location: local` 的 provider;沒有設定 local provider 就**不跑**那個專案的那個點
  (librarian 跳過、狀態頁寫原因),不退回雲端。
- reflect-week 現在「只送統計」的規則不變。

## 新使用者會看到什麼

- `hub init`:有 Claude Code 而且登入了 → 預設 `claude-code`(跟現在一樣);沒有 → 問 provider + key,`agent.runtime: null`,
  列出哪些功能因此關掉。
- `hub doctor`:每個 provider 的 `health()`(打一次最小請求)、每個 tier 對應到什麼、agent runtime 在不在。
  Claude 專用的檢查(headless token、`update-claude`、`claude-login` job)只在 `agent.runtime == "claude"` 或有
  `agent-runtime` provider 指到 claude 時出現——搬進 `modules/claude.yaml`,沒用 Claude 的人就沒有這些 job。

## 證明真的換得掉(不只是有介面)

1. **先加 ratchet**(跟 `test_personal_boundary` 一樣只准變少):`runtime_claude.py` 以外出現 `.claude/`、Claude 的旗標、
   `CLAUDE_CODE`、`haiku|sonnet|opus` 的次數,存基準。現在約 16 個檔;每一步只准變少。
2. **先寫特性測試再改**(tests-before-rewrite):每個要改成 completion 的點,先用現在的實作把「給定輸入 → 寫出的 STATE.md /
   review-log / commit」固定成黃金檔,commit 之後才改;改完照同一份黃金檔比(prompt 會變,寫出的結果要一樣或差異說得出理由)。
3. **整條夜間流程用替身 provider 跑一次**(`runtime_fake` 已經有,加一個 fake completion provider):librarian → registrar →
   todo → publish,在隔離的 HOME。
4. **一個真的第二家**:`openai` kind 對本機 Ollama 跑 reflect-week 跟 librarian-state(不花錢、不送雲端),在隔離環境比對
   輸出合 schema、寫出的檔案合理。

## 步驟(每一步單獨上線,Knas 這台每一步都不變)

1. ratchet + 黃金檔(不改行為)。
2. `llm.py` 認得 `kind`/`tier`、`hub.json llm` 區塊、provider 介面(`complete(messages, schema, model) -> result`、
   `health()`);`agent-runtime` provider 包住現在的 runtime。沒有 `llm` 區塊 = 現在的行為。
3. `anthropic`、`openai` 兩個 kind;key 的位置、check-secrets 涵蓋、config check 擋值。
4. 判斷點一個一個改成 completion:todo-format → reflect(已經是)→ index → state → consolidate。每個都過黃金檔才換下一個。
5. 隱私路由(`location` + `privacy`)。
6. `hub init` / `doctor` / Claude 專用 job 搬進模組;`models.env` → `hub.json`。
7. agent runtime 可以是 null(功能明確關掉、狀態頁說明)。

## 不在範圍(之後)

- **agent 點的中立權限**(`tools`/`allowed_tools` → `policy.py` 那種中立詞彙):要跟 Muninn 同步改共用格式(v2 並存),
  另外做。開源第一版:agent 點只支援 Claude Code,其他 coding agent 的 runtime 是社群可以加的(介面跟契約測試已經在)。
- 平台自己做一個「用 provider 的 function calling 跑的小 agent」(給信箱代辦用,工具由平台實作、權限由平台直接執行)
  ——可以讓信箱代辦也只要一把 key,但是是新功能,等第一版之後看需要。
- 互動 session 用哪個 coding agent(hook、規則檔、對話紀錄)——`runtime-abstraction.md` 第 3–5 層,已經是介面。

## 要 Knas 確認的

1. 分兩種、能 completion 就 completion(尤其 librarian-state:模型不再自己 Read 補截斷的 diff,改成平台全放或分批)。
2. consolidate 也改成 completion(檔案操作清單 + 平台白名單),還是先留 agent。
3. 開源第一版 agent 點只支援 Claude Code(沒有就關掉那些功能),其他 coding agent 留給之後。
4. 隱私:`privacy` 不是 include 的專案只送 local provider、沒有就不跑。
5. 你這台的 `hub.json` 會加上 `llm` 區塊(全部 tier → `claude-code`),行為不變;要不要順便把 reflect-week 之類的試著接一個
   API key(要你自己放 key)。
