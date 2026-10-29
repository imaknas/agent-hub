# 使用者自己選模型、選哪家 LLM、自己放 API key(開源前)

2026-10-29(第二版:盲審之後改)。目標:agent-hub 開源後,使用者不需要 Claude 訂閱、也不需要 Claude Code,只要一把任何
一家的 API key(或本機模型)就能跑起平台每晚的核心;有 Claude Code 的人照用。Knas 這台現在的行為(全部走 Claude Code +
headless token)是其中一種設定,**每一步上線都不改變它**。

延續 `runtime-abstraction.md`(執行環境的介面)跟 `capabilities.md`(中立的權限政策)。

## 現況

平台每一次呼叫模型都經過 `bin/llm.py`、是 `contracts/llm-points.yaml` 宣告過的判斷點(12 個),`llm.py` 交給
`runtime_ports` 的 agent 介面,Claude Code 的指令只在 `runtime_claude.py` 組。綁在 Claude 上的有四件事:

1. 12 個點全部是「coding agent 跑一次」:模型自己用工具讀檔、改檔,權限靠 Claude 的 `allowed_tools` 擋。
2. 模型名稱是 Claude 的(`model_default: haiku|sonnet|opus`、`models.env`)。
3. 憑證、檢查、安裝都假設有 Claude Code(`bin/setup` 把 claude 列為必要、`claude-login` 每小時、`update-claude` 每晚)。
4. 平台的**輸入**也是 Claude 的:memory 筆記是專案 session 照 `CLAUDE.local.md` 寫的、提醒靠 Claude 的 hook、
   memory-gaps 跟週報讀 Claude 的對話紀錄(第 3–5 層的介面已經有,但只有 Claude 一個實作)。

## 根本的改法

### 判斷點分兩種形狀

| 形狀(`shape`) | 需要什麼 | 誰能跑 |
|---|---|---|
| **completion** | prompt 進、合 schema 的 JSON 出;**模型沒有工具、不碰檔案** | 任何實作了 completion 介面的(API provider、本機模型、Claude Code 關掉工具) |
| **agent** | 工具、工作目錄、權限、接續對話 | 只有 coding agent runtime |

能做成 completion 的就做成 completion:平台把要給模型看的東西放進 prompt,模型回「要怎麼改」,平台檢查後自己寫檔。
好處不只是能換 provider——**權限從「叫模型不要亂改」變成「平台只寫它允許的東西」**(現在 librarian-state 靠
`--allowedTools` + `dontAsk` + `--strict-mcp-config` 三層擋 haiku),而且輸入輸出都是資料,可測。

**模型回編輯清單,不是整份重寫**:`{"edits": [{"old": "…", "new": "…"}], …}`,每個 `old` 必須在原文剛好出現一次,平台照順序
套用,再機械檢查「(等 owner:<key>)」這類標記一個都沒少、沒多。這樣「其他行逐字不動」是構造保證的;小模型不會為了
改一行重寫 11KB 的 STATE.md、漏掉段落或改壞 key。

### 每個點的去處

| 判斷點 | 之後 | 說明 |
|---|---|---|
| librarian-state | **completion** | prompt 放:memory diff、現在的 STATE.md、AGENT.md、`memory/reference/` 的清單 + README(要比對的主題檔才放全文)、轉交與 record。回編輯清單 + 現在 JSON 裡的後設資料。diff 超過預算就**照 commit 邊界分段**、每段一次、checkpoint 推進到那段結尾(最近幾晚都沒觸發截斷,不做更複雜的) |
| librarian-todo-format | **completion** | 回 `## 待辦` 段落的編輯清單(給它一個 schema;現在是 text) |
| librarian-index | **completion** | 回名冊區塊外的編輯清單;給它的 digest 摘要照隱私標籤先過濾 |
| reflect-week | completion | 已經是(tools "") |
| consolidate | **agent(第一版不動)** | 要讀所有現存日期檔跟主題文件(大專案原始紀錄 260KB+),小模型、本機模型裝不下。之後可以拆成「每個主題一次」的 completion,另案 |
| login-check | completion 介面的 `health()` | Claude 專用的那份搬進 claude 模組 |
| agent-job、mailbox-*、lease-* | agent | 要工具、接續、權限轉接 |

結果:**只要一把 API key,每晚的 librarian、todo、index、週報都能跑**。consolidate、信箱代辦、通道租借、使用者的 agent job
需要 coding agent runtime;沒有就**明確關掉**,不默默降級。

### provider 就是 runtime_ports 的一個介面

不另外發明「provider kind」:在 `runtime_ports.PORTS` 加 **`completion` 介面**:

- `complete(req) -> CompletionResult`:`req` = messages、輸出 schema、模型、`max_tokens`、逾時;結果 = 文字/解析過的 JSON、
  停止原因(完成 / 長度截斷 / 拒絕)、token 數。
- `health() -> (ok, reason)`:最小的一次請求。

實作:
- `runtime_claude.complete`:`claude -p` 關掉所有工具、`--strict-mcp-config`、**prompt 從 stdin 傳**(現在是 argv,大 prompt
  會超過 ARG_MAX)、**在空的中立目錄跑**(在專案副本裡跑會觸發專案的 SessionEnd hook,librarian.py 記過這個坑)。
- `provider_anthropic.py`、`provider_openai.py`(Chat Completions 相容 + `base_url`:OpenAI、OpenRouter、Gemini 相容端點、
  DeepSeek、LM Studio、vLLM)只實作 completion 介面;`provider_ollama.py` 用**原生 API**(相容端點不能設 `num_ctx`,
  超長 prompt 會被默默從前面截掉)。
- `runtime_fake` 加 completion,契約測試自動跑所有實作(`load()` 本來就支援只實作部分介面)。
- **`runtime_none`**:每個介面都明確回「不支援 + 原因」。沒有 coding agent 時 `runtime.default: none`;呼叫端拿到「不支援」
  就顯示原因(memory-gaps:「沒有對話紀錄來源,不檢查」;週報:「沒有對話紀錄,只有 commit 跟筆記的統計」、不送空統計給模型),
  **不是拿到空資料照跑**。

只用標準函式庫(`urllib`),平台的 venv 維持 3.13 + PyYAML。

### 結構化輸出(每家都要過的同一套)

1. provider 支援 schema 就用,平台先把 schema 轉成那家要的形狀(OpenAI strict 要每個欄位 required + `additionalProperties:
   false`,`contracts/llm/*.schema.json` 不符合,直送會 400;轉不了就用非 strict)。Anthropic 用 tool 輸入、一定帶 `max_tokens`。
2. 回來先剝掉推理模型的 `<think>…</think>`,再用 `minischema` 驗證(不管 provider 說它多嚴格)。
3. 不合格重試**一次**,把 minischema 的錯誤附給模型;**長度截斷不重試**(會原樣再截一次),當成另一種失敗(`truncated`)。
4. 再不合格走宣告的 `on_failure`(現在的機制不變)。
5. 送出前算長度:超過那個模型的 context 就不送,失敗原因寫「prompt 太長」,不讓 provider 默默截斷。

## 設定

### 1. 判斷點宣告(平台的;`contracts/llm-points.yaml`)

**用共用格式裡已經有的欄位**(`contracts/llm-points.v1.schema.json`,Muninn vendor 同一份):
- `model_group`(已有):要多強的模型——`fast | standard | strong`(現在的 haiku / sonnet / opus)。
- `material`(已有,`cloud_safe | personal | sensitive | local_only`):送給模型的內容屬於哪一類。
- **新增 `shape: completion | agent`**(不用 `kind`:共用格式的 `kind` 已經是 `decide | generate`)。加一個選填欄位不改意思、
  不升版,但 point 層是 `additionalProperties: false`,**要先改 schema、同一天讓 Muninn 的副本跟上**(用信箱說好日期)。

`model_env`/`model_default` 過渡期照讀,之後拿掉。

### 2. 使用者的選擇(`hub.json`,config 管線驗證)

```jsonc
"runtime": {"default": "claude"},          // 已有:coding agent;沒有就 "none"
"llm": {
  "providers": {
    "anthropic": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"},
                  "models": {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}},
    "local":     {"impl": "ollama", "base_url": "http://127.0.0.1:11434", "location": "local",
                  "models": {"fast": {"name": "qwen3:8b", "context": 32768}, "standard": {"name": "qwen3:32b", "context": 32768}}},
    "claude":    {"impl": "claude", "models": {"fast": "haiku", "standard": "sonnet", "strong": "opus"}}
  },
  "use": {"fast": "anthropic", "standard": "anthropic", "strong": "anthropic"},   // model_group → provider
  "points": {"reflect-week": {"provider": "local", "model": "qwen3:32b"}},       // 個別判斷點蓋過(取代 models.env)
  "budget": {"nightly_usd": 2.0}
}
```

- **模型名稱寫在 provider 裡**(每家的名字只出現在那家),`use` 只說哪一組交給哪家;agent runtime 用的模型也從它自己那個
  provider 的 `models` 拿(`claude` 那個),不另外一張表。
- 模型可以是字串或 `{name, context}`(Ollama 的名字本身有冒號,不用 `provider:model` 字串)。
- **沒有 `llm` 區塊 = 現在的行為**:全部交給 `runtime.default` 的 completion 介面,模型照 `models.env`。Knas 這台不用改設定。
- `schedulectl set-model` 改寫 `hub.json` 的 `points`。

### 3. API key

- 值只放 `secrets_dir()`(`{"secret": "<名稱>"}`,chmod 600)或環境變數(`{"env": "…"}`)。`hub.json` 裡不准有值:
  config check 看到像 key 的字串(`sk-…`、長的隨機字串)就擋。
- **用檔案是預設、env 是選項**:launchd 底下沒有使用者 shell 的環境變數,`hub doctor` 在終端機過、半夜排程卻失敗——
  doctor 用排程器同一套環境檢查,env 的 key 在 launchd 底下讀不到就直接說。
- `hub init` 問一次、寫進 `secrets_dir()`;`bin/hub llm set-key <provider>` 從 stdin 讀(不進 shell 歷史)。
- check-secrets 自動把 `llm.providers` 的每個 `secret` 當成平台的憑證(在不在、權限、備份)。
- **key 不漏給子程序**:provider 發請求時才讀;`auth_env` / `clean_env` 明確刪掉所有宣告過的 key 變數(現在 `llm.py` 把整份
  `os.environ` 交給子程序,env 給的 key 會被每個有 Bash 的 agent job 繼承)。
- **送出前統一遮蔽**:現在只有 `reflect_llm.py` 會遮掉像憑證的字串;改成 `llm.py` 送任何 provider 之前都過同一個遮蔽點。

## 隱私:哪些內容能送哪個 provider

- provider 的 `location: cloud | local`(預設 cloud)。**`local` 只准 loopback、tailnet、私有網段的 `base_url`**(config check),
  不能自己宣告。
- 判斷點的 `material` 決定最低要求:`local_only` 只能送 local。
- 專案層級**新增** `privacy.memory: cloud_ok | local_only`(預設 `cloud_ok`,平台管的安全欄位)。**不沿用 `privacy.reflection`**:
  那個的意思是「對話內容不進週報」,不是「memory 不准上雲」;現在 3 個 `stats_only` 的專案每晚照樣送雲端模型更新 STATE.md,
  沿用會讓它們的 STATE.md 停止更新。`local_only` 的專案沒有 local provider 就不跑那個點(狀態頁寫原因),不退回雲端。
- librarian-index 吃的跨專案 digest 摘要照同一套標籤過濾。

## 成本、額度

- API 只回 token 數:`llm.py` 照 token 記帳(價目表放 provider 設定、選填;沒有就只記 token),狀態頁顯示。
- `budget.nightly_usd`:超過就停下後面的判斷點(`on_failure` 照宣告)、發警報;不寫就不限。
- 429 / 529 退避重試(有上限),不算進「不合格重試一次」。

## 只有 API key 的使用者:每一處都要明確

| 地方 | 現在 | 之後 |
|---|---|---|
| `bin/setup` | claude 必要 | 選配(沒有 = `runtime.default: none`) |
| `claude-login`(每小時)、`update-claude`(每晚)、doctor 的 headless token | 平台 job | 搬進 `modules/claude.yaml`,`runtime.default == "claude"` 或有 provider 用 claude 時才開 |
| memory-gaps、週報的對話統計 | 讀 Claude 的對話紀錄 | `runtime_none` 回「不支援」→ 明確寫出不檢查 / 只有 commit 跟筆記的統計 |
| memory 筆記(平台的輸入) | 專案 session 照 `CLAUDE.local.md` 寫,hook 提醒 | 見下面「要 Knas 決定的」第 3 點 |
| consolidate、信箱代辦、租借、agent job | 跑 | 關掉,狀態頁跟 doctor 說明 |

## 步驟(每一步單獨上線,Knas 這台不變)

1. ✅ ratchet(847f32b):`runtime_claude.py` 以外的 Claude 語法跟模型名稱只准變少(基準 30 個檔、139 處)。
2. `completion` 介面 + `runtime_claude.complete`(stdin、中立目錄)+ `runtime_fake` + `runtime_none` + 契約測試;`llm.py` 認得
   `shape`/`model_group`、`hub.json llm`(沒有 = 現在的行為)。**同時**把 Claude 專用的 job 搬進 `modules/claude.yaml`、
   `bin/setup` 的 claude 改選配——只有 API key 的人從第一天就不會被警報洗版。
3. `provider_anthropic`、`provider_openai`、`provider_ollama`;結構化輸出那一套;key 的位置、遮蔽、`clean_env`、check-secrets、
   config check;token 記帳、預算、退避。共用 schema 加 `shape`,跟 Muninn 同一天。
4. 判斷點一個一個改成 completion,每個都先**影子模式**:completion 版用 dry-run 跟現在的 agent 版並跑幾晚,比對提議的結果
   (STATE.md、待辦段落、index 註記),沒問題再切換。順序:todo-format → index → state。黃金檔改成固定「替身回的編輯清單 →
   平台寫出什麼」(現在的替身是直接寫檔模擬 agent,要跟著改);`librarian.py` 裡「沒有 structured_output 以實際檔案為準」那條
   路徑切換後拿掉。
5. 隱私路由(`location` 檢查、`material`、`privacy.memory`、digest 過濾)。
6. `models.env` → `hub.json`(`librarian.py` 自己讀 `models.env` 的那條路一起換掉);`hub init` / `doctor` 問 provider 跟 key。
7. 用本機 Ollama 跑一次完整的夜間流程(隔離的 HOME,不送雲端):證明只有本機模型也能跑。

## 不在範圍(之後)

- agent 點的中立權限(`tools`/`allowed_tools` → 中立詞彙):要跟 Muninn 同步改共用格式,另案。
- 其他 coding agent 的 runtime(Codex、Gemini CLI、opencode):介面跟契約測試都在,社群可以加。
- consolidate 拆成 completion。
- 平台自己用 function calling 跑的小 agent(讓信箱代辦也只要一把 key)。

## 要 Knas 決定的

1. 分兩種形狀、能 completion 就 completion,模型回編輯清單;consolidate 第一版留 agent。
2. 開源第一版的 agent 點只支援 Claude Code,沒有就關掉那些功能。
3. **memory 筆記從哪來**(只有 API key、不用 Claude Code 的人,平台沒有輸入):
   (a) 第 4 層加一個 `AGENTS.md` 的實作(Codex、Cursor、Gemini CLI 都讀它):規則照樣寫進去、請 agent 寫筆記;沒有 hook 就
   沒有提醒,doctor 說明。成本小,涵蓋多數 coding agent。
   (b) 第一版只支援 Claude Code 當互動 agent,其他人自己寫筆記。
   建議 (a)。
4. 隱私:新增 `privacy.memory`(預設 `cloud_ok`,你這台不變),不沿用 `privacy.reflection`。
5. 共用格式加 `shape`,跟 Muninn 說好同一天改。
