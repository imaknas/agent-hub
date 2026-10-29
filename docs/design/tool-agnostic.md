# 工具中立(開源前):任何 coding agent、任何 LLM、任何 key

2026-10-29(第二版:盲審之後改)。Knas:「要最大化 tool agnostic,開源要吸引人用,就不能太多限制。」

總綱;模型與 API key 的細節在 `llm-providers.md`(第二版),介面分層在 `runtime-abstraction.md`,中立權限在 `capabilities.md`。
這份決定**平台跟工具的界線放在哪**。

Knas 10-29 同意的五點(第一版的「要決定的」):原則 1 當硬規則、第一個非 Claude 證明用 Gemini CLI、租借改走 ACP、內建最小
owner 端、Linux 進第一版。盲審之後 Knas 再確認(10-29):原則 1 收窄、租借走 ACP 延後、CLI 改名 `agenthub`;並且**把 Muninn
明確寫成可選的工具**(owner 端可以換,見「不是工具,但同樣是限制」)。

## 問題

agent-hub 對 Claude Code 的依賴有兩種:

- **用它做事**(判斷點、信箱代辦、租借):可以換成別的 agent 或直接打 API。
- **平台的保證靠它的功能**:寫入限制靠 Claude 的權限規則、信件靠 Claude 的 hook、筆記靠 `CLAUDE.local.md`、統計靠 Claude
  的對話紀錄。換工具,這些不是變差,是**默默消失**。

第二種才是開源的障礙。

## 核心:agent 提議,平台套用

第一版想用作業系統的沙盒把任意 agent 包起來,讓「寫不到就是寫不到」。盲審指出做不到:Seatbelt 沒辦法照網域過濾網路
(agent 要連自己的模型 API,一開網路白名單就是裝飾)、agent 必須讀得到自己的憑證、狀態目錄要可寫(裡面是所有專案的對話紀錄)、
訂閱登入要 Keychain,而且**沙盒不能疊兩層**(known-issues.md:222)——Claude Code、Codex、Gemini CLI 在 macOS 上自己的沙盒
都是 Seatbelt,外面再包一層它們自己那層就套不上,Knas 這台的 Bash 會直接失敗。

所以保證改成**結構上**成立,不靠沙盒:

- **判斷**(completion):模型回編輯清單,平台檢查、平台寫檔(`llm-providers.md` 第二版)。
- **平台啟動、要動到檔案的工作**(信箱代辦、agent job、consolidate):在**用完就丟的 worktree** 裡跑任何 agent,結束後平台
  `git diff` 檢查,**只套用准許的路徑**,其他的丟掉並記下來。agent 在 worktree 裡做什麼都無所謂——它碰不到真的 checkout。
- **要寄的信、要結案的事**:agent 在結構化輸出裡說,**平台代為寄出**(身分是平台啟動時給的,不是 agent 自己說的)。
- **匯流排的完整性**:現在 `policy.write` 准 agent 寫匯流排目錄、`hubbus` 在 agent 自己的程序裡跑,agent 可以直接寫 SQLite、
  冒用寄件人。改成平台的服務 + unix socket(身分由啟動時給),匯流排目錄對 agent 唯讀。

這套在 Linux 上一樣成立。沙盒降級成**縱深防禦**(擋讀取別的專案、擋外流),只在不會疊兩層的地方用(平台自己代跑的
cap-runner;平台啟動的 agent 如果那個工具自己的沙盒關掉才包)。

**原則 1 的範圍**(要寫進 CLAUDE.md 的版本):**平台啟動的工作、平台的資料完整性(匯流排、登錄、STATE.md、筆記)由平台
保證,不靠任何工具的功能。使用者自己開的 session(終端機、IDE、Desktop SSH)只有工具原生的保護,平台給建議、不保證**——
doctor 跟狀態頁照實標出來。Knas 自己的主力是互動 session,這一類現在靠 Claude 的設定(render-project-settings),照舊。

## 原則

1. 如上:平台啟動的工作與平台的資料,保證在結構上成立。
2. **用最通用的介面**:檔案跟平台的命令列 > MCP > 各工具的原生 hook/設定(只寫在轉接器裡)。
3. **能力從轉接器實作了哪些介面推出來**(`runtime_ports` 的 7 個介面,不另外列一份);每個平台功能宣告需要哪些介面;
   缺的 = 功能明確關掉,doctor 的「工具 × 功能」表寫原因。`load()` 已經只檢查要求的介面,轉接器可以只實作一部分。
4. **不寫程式也能接新工具**——但誠實:零程式的轉接器只做得到 completion 跟「在丟棄式 worktree 裡跑一次」。
5. 平台自己的設定只用中立詞彙(ratchet,847f32b)。
6. 平台推得出來的,不靠 agent 回報。

## 平台跟工具接觸的面

### 1. 判斷(completion)

`llm-providers.md` 第二版:librarian 三個點、週報改成 completion;任何 API key、本機模型,或任何 agent 關掉工具跑。

### 2. 平台啟動的 agent 工作

| 轉接器 | 能做什麼 |
|---|---|
| `command`(設定裡的指令樣板) | completion;**或**在丟棄式 worktree 裡跑一次(prompt 從 stdin、輸出用工具自己的「最後一則訊息 / 結構化輸出」旗標,例:`codex exec --output-last-message --output-schema`,不解析會變的事件串流)。逾時殺整個程序群組;不提供成本(只記 token,如果工具給)。**樣板不准寫成「全部自動允許」又直接跑在真的 checkout**——跑在 worktree 裡就沒有這個問題,這也是為什麼它只准用在 worktree |
| `acp` | 同上,外加多輪、事件串流(平台自己記一份中立的對話紀錄,不用每個工具寫解析器)。接續(`session/load`)是選配能力 |
| 原生(`runtime_claude`) | 全部能力(現在的行為) |

信箱代辦(只寫筆記、回信、問 owner)本來就只需要「讀專案 + 結構化地說要寫什麼筆記、寄什麼信」:改成提議→平台套用之後,
**任何轉接器都能跑**。這就是第一個非 Claude 的證明(Gemini CLI)。

### 3. 信件:匯流排保證不漏,推送只是讓人早點看到

- **「信一定收得到」的保證是 dispatch**(沒人收的 request/question 由平台派 agent 處理,已經有),不是任何推送管道。
- 推送(早一點讓 session 看到)由好到差:工具原生 hook(轉接器有就接;Claude、Codex、Gemini CLI 都有 SessionStart/
  UserPromptSubmit,語意不同)> MCP 工具(`收信`)> 規則檔裡請 agent 開始時跑一次收信 > git post-commit(**只當備援**:討論型
  session 不 commit、IDE 介面的 commit 輸出給人看、husky/lefthook 的 `core.hooksPath` 會衝突、`.agent-memory` worktree 共用
  hooks 會很吵,而且它不會記 `message.delivered`,dispatch 的等待判斷會失準——所以晚一點再做,而且只印、不記已送達)。

### 4. 平台的命令列跟 MCP

- 平台的 CLI 上 PATH,**改名**:`hub` 跟 GitHub 舊的 `hub` CLI 撞名(Homebrew 上還很多,常被 alias 成 git)。候選:`agenthub`。
- 規則檔裡只寫這個指令,不寫本機路徑。
- **MCP 伺服器**:收信、寄信、結案、寫筆記、問 owner;MCP 伺服器的 `instructions` 欄位可以帶平台的規則,**不用改任何專案的
  檔案**就把規則送進支援 MCP 的工具。轉接器負責登記進工具的 MCP 設定;做不到就 doctor 給手動步驟。stdio 的 MCP 繼承
  agent 的環境;遠端的 HTTP MCP、claude.ai connector 不在平台的控制內(寫明)。

### 5. 規則檔:不改使用者 commit 的檔

盲審查證:Codex 的 `AGENTS.override.md` 會**取代**同目錄的 `AGENTS.md`(會把使用者的規則藏起來);Gemini 的 `contextFileName`
在 `.gemini/settings.json`(常有 commit);Cursor 的 `.cursor/rules` 常有 commit;symlink 也是改 commit 的檔。所以:

- 平台的規則內容要短、沒有本機路徑(指令名稱、MCP 工具名)。
- 送進工具的方式由好到差:MCP `instructions`(全通用、不碰檔案)> 轉接器知道**不覆蓋**的本機檔(Claude 的
  `CLAUDE.local.md`,現在就這樣)> doctor 給一行讓使用者自己決定要不要加進他的 `AGENTS.md`。
- 規則檔現在靠 SessionStart 的 `sync-local-setup.sh` 對齊;沒有 hook 的工具改由平台 job 重生(每 15 分鐘的那批)。

### 6. 平台的輸入

- memory-gaps 抓的是「有對話、沒 commit、沒筆記」(project-06 88 則對話、0 筆)——這需要對話紀錄,不能改成只看 git。
  跟 memory-nudge(有 commit 沒筆記,工具中立)合併成一支;「有對話」那一半是 `transcripts` 能力,沒有就明確只做 commit 那一半。
- 週報的作息、深夜比例、真人訊息需要對話紀錄:沒有就明確關掉那幾段,commit、筆記、信件的統計照做。
- 平台自己啟動的 ACP session,平台自己記中立的紀錄(不靠工具的格式)。

## 不是工具,但同樣是限制

- **owner 端(Principal Handoff 0.3 的 principal 端)可以換,Muninn 是可選的工具**——跟 coding agent、LLM provider 同一個層級:
  - 平台是 **requester 端**:registrar 把〔owner〕待辦寫成 `raised` record、專案的問題寫成 decision、收回 `settled`/`decided`
    套用。這一半**屬於核心**,不管接哪個 owner 端都在跑。**現在 registrar(`assistant/knas-tasks`)放在 assistant 模組裡,
    沒有 Muninn 就跟著關掉——要搬回核心**;assistant 模組只留 Muninn 專屬的(bridge 的 HTTP 那段、reconcile-questions、通道租借)。
  - **owner 端的實作**(`hub.json` 的 `owner_side`,預設 `builtin`):
    | 實作 | 是什麼 | 能力 |
    |---|---|---|
    | `builtin`(內建,預設) | 平台自己的最小 principal 端:直接讀本機匯流排上給 owner 的 record | 擺出來(`todo.md` + `agenthub owner list`)、通知(通知轉接器)、結案(`agenthub owner done/drop <key>`、`answer <id> "…"` → `settled`/`decided` record,authority `principal`) |
    | `muninn`(可選,另一個開源專案) | 個人助理:bridge v2 透過 HTTP 交換 record(`POST/GET /api/records`、ack) | 上面全部,加上手機對話、提醒時機、從對話推斷做完(`attested`)、深談、**通道租借** |
    | 其他 | 任何照 Principal Handoff 0.3 實作 principal 端的系統 | 規範定義的介面就是接口 |
  - 文件、`hub init`、doctor 都把 Muninn 寫成「可選:想要手機上的個人助理就裝它」,跟「想讓 agent 工作用 Codex 就裝 Codex」
    同一種說法。通道租借、問題對帳寫明「需要 Muninn(或其他支援租借的 owner 端)」。
  - **這也是 Principal Handoff 規範的第二個獨立實作**:`builtin` 完全照公開的規範寫、能跟 Muninn 互換,證明規範讓兩個不相干
    的系統接得起來,不只是 agent-hub 跟 Muninn 之間的私有格式。
  - Knas 這台:`owner_side: muninn`,行為不變。
  - **沒有 owner 端,開源使用者的〔owner〕待辦跟問題就無處可去**——所以 `builtin` 在第一版、比任何 agent 轉接器都優先。
- **通知**:`alert.py` 改成轉接器(Discord、ntfy、email、Slack)。
- **Linux**:排程跟服務是 launchd → systemd user unit / cron 的那一層(排程器跟 render-services 已經是平台的程式,換的是產生的
  檔案跟載入指令);沙盒在 Linux 先標「不提供」(提議→套用不需要它)。

## 開源第一版的範圍(盲審建議,採用)

1. ✅(33b7b2c)**能力表 + `runtime_none`**:能力從介面推、doctor 的「工具 × 功能」表;Claude 專用 job 搬進模組;`bin/setup` 的 claude 改選配。
2. ✅(b0e4e13)**completion + providers**:librarian、待辦、index、週報用任何 API key。介面、三家 provider、統一的處理、key、預算都好了;
   reflect-week 已經是 completion(本機 Ollama 跑通)。librarian 的三個點改成 completion 是另一步(影子模式,`llm-providers.md` 步驟 4)。
3. **owner 端可以換**:registrar 搬回核心、`owner_side: builtin | muninn`、內建最小 owner 端 + 通知轉接器;Muninn 寫成可選工具。
4. **平台 CLI 改名上 PATH**、規則內容去掉本機路徑、MCP 伺服器(含 `instructions`)。
5. **提議→平台套用**:丟棄式 worktree、路徑白名單套用、平台代寄信;信箱代辦用 Gemini CLI(`command` 或 `acp`)跑通。
6. **Linux 的排程跟服務**(可以跟 5 平行)。
7. **匯流排改成平台服務 + socket**(資料完整性;可以在 4 之後)。

第一版能誠實說的:「判斷點用任何 LLM、任何 key;平台啟動的 agent 工作用任何 CLI agent;owner 端內建一個,想要手機上的
個人助理可以接 Muninn(或任何 Principal Handoff 的實作);互動 session 的整合、對話統計目前只有 Claude Code 是完整的轉接器,
租借需要 Claude Code + Muninn。」

延後:租借改走 ACP、平台沙盒包 agent、post-commit 推送、`agenthub agent` 啟動器、其他工具的對話紀錄解析。

## 步驟(Knas 這台每一步都不變)

0. ✅ ratchet(847f32b)。
1–7 照上面的範圍,每一步單獨上線。每步的驗證:Knas 這台的夜間流程照跑(影子模式、黃金檔);隔離的 HOME 用只有 API key /
只有 Gemini CLI 的設定跑一次完整的夜間 + 一封信箱交辦。

## 已確認(10-29)

1. 原則 1 收窄成「平台啟動的工作 + 平台的資料」;使用者自己開的 session 只給建議。
2. 租借改走 ACP 延後;第一版維持原生 Claude(租借需要 Claude Code + Muninn)。
3. 平台 CLI 改名 `agenthub`。
4. Muninn 是可選的工具;owner 端預設內建,registrar 搬回核心。
