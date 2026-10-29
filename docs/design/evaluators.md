# 判斷點的評估(evaluator):每個碰到模型的地方都能拿來評估換模型

> agent-hub session · 2026-10-27 · **r5**。r1–r4 各經一次盲審(都是「還不能開始」),中間做了兩輪封閉實驗。r5 以第二輪(有紀錄的)
> 實驗為準:原始輸出在 `.state/seal-check/2026-10-27/`(`FINDINGS.md` 是摘要、`../seal_exp2.py` 是程式)。最後一次盲審後開始實作。

## 目標與範圍

Knas 的要求:**所有邊界會碰到模型的地方都要有 evaluator**,用意是評估換模型(更便宜的、本機的、別家的)的可能。

**Knas 的決定(10-27)**:agent job(Upwork、影片研究、文獻搜尋…,登錄 job 經過判斷點 `agent-job`)第一階段**不做** evaluator——
這些專案的隱私風險比較小,換本機模型的壓力也小。記成已知、已接受的缺口;它們照樣要有 MCP 封閉(已做)跟宣告上的 `eval`(`scorer: none`
+ 理由「owner 10-27 決定暫不評估」)。

**第一階段能回答的**:每次呼叫留下可重跑的案例;機械可檢查的部分候選有沒有過;候選跟現任在同一個封閉環境裡的一致程度,對照現任自己
重跑的一致程度(雜訊底線),附依專案分群的信賴區間。
**還不能回答的**:多數點「誰比較對」(librarian-state 沒有可靠的正確答案訊號);reflect-week 樣本太少。第二階段。
**換模型的範圍**:B 類候選第一階段只有 claude 的其他模型;A 類換本機/別家要一個純文字 runtime(契約測試先改成依 runtime 宣告的介面),
排在第一階段後段。

骨架照 Muninn(`src/muninn/core/evals.py`、`cases.py`)。

## 原則

1. 宣告是契約,執行是可以不開的模組(`modules/evals.yaml`,預設關)。
2. 評估走 runtime 介面;評估程式不寫 runtime 專用語法。
3. **封閉是量出來的**:每一次正式呼叫都讀它自己的 `system/init`,記下這次到底載了什麼(`sealed` 是逐次量的,不是推測)。
4. 重播不准碰真的東西;**任何一次呼叫都不准連到環境裡的 MCP**(包括實驗跟 seal-check 的對照組)。
5. 案例只在平台的評估目錄(0700,不在任何判斷點的 cwd 底下,不進 git)。
6. 記錄只能多花一點時間,不能改變判斷點的行為。

## 已經做的

- **dcc61b6**:所有 headless 判斷點帶 `--strict-mcp-config`(實驗:`--tools ""` 擋不掉 MCP,工具全關的呼叫照樣拿到 265 個 MCP 工具、
  含正式資料庫的 `execute_sql`)。例外:`connectors: true` 的 job(Upwork 要 claude.ai connector)——**它們現在還是載得到正式資料庫的 MCP**,
  第 0 步用權限規則擋(見下)。
- **c89c25c**:判斷點的模型設定(`models.env`)以前沒生效(`librarian.sh` 沒 export),`llm.py` 改成自己讀。
- f2755ca(headless 呼叫不收信)、10c5fbb(送進 session 只算看過)。

## 封閉實驗的結論(第二輪,有紀錄)

每次都帶 `--strict-mcp-config --no-session-persistence`;工作 = 讀 STATE.md、用 Edit 加一行(真的工具、`--allowedTools Edit(./STATE.md)`、dontAsk);
暫存 cwd 有專案層級的金絲雀 hook(SessionStart/PostToolUse/SessionEnd 各寫一個標記檔);另開 `--debug hooks` 看實際執行。

| 設定 | 結果 |
|---|---|
| 不封閉(haiku、sonnet) | 三個金絲雀都觸發;vercel plugin;skills 61、agents 9;auto-memory 開——**指到 agent-hub 自己的 memory**(cwd 在 agent-hub 的 git repo 裡,Claude 以 repo 當專案) |
| `--settings`:hooks off、auto-memory off、使用者開的 plugin off | 金絲雀 0、vercel 沒了、auto-memory 沒了、Edit 照常;sonnet 那次 modelUsage 也只有 sonnet。**但內建 plugin 的 hook module 照跑**(`cc-plugin-agents-md` 聽 `prompt.context`、會注入 AGENTS.md 類的內容;telemetry;plugin-authoring)——`disableAllHooks` 管不到 |
| 再把三個 `@builtin` plugin 也設 false | **完整**:plugins 空、內建 module 0、金絲雀 0、auto-memory 關、Edit 成功 |
| `--setting-sources ""`(±auto-memory off) | 內建 module 還在,不比上一列好 |
| `--bare` | 不能用(headless token 不被接受) |
| 兩個 `--settings` | 後面的整個蓋掉前面的,不合併(第一輪) |
| 殘留 | 有 Edit 也沒有 `~/.claude/file-history` 新項目;沒有對話紀錄 |

沒測到:advisor 在長任務裡會不會出現;內建 module 以外有沒有別的注入來源——這兩個由逐次的 init 記錄跟 modelUsage 持續觀察。

## 第 0 步:A/B 類判斷點的封閉

拆成兩件事:**`seal`**(擋掉環境帶進來的東西)跟 **`persist_transcript`**(留不留對話紀錄)。

| 類 | 點 | seal | persist_transcript |
|---|---|---|---|
| A、B | reflect-week、librarian-state、librarian-todo-format、librarian-index、consolidate | 全部 | 否 |
| R | lease-handover、mailbox-conversation | 只有 MCP(已做) | 是(接續對話) |
| C | agent-job、mailbox-escalation、lease-session、lease-work | 只有 MCP(已做)+ connectors job 的 deny | 是 |
| N | login-check | MCP(已做) | 否 |

**封閉的參數**(runtime_claude 產生;實驗第 3 列):`--strict-mcp-config --no-session-persistence` + **一個** `--settings`(inline JSON,不用檔案——
`--settings` 收 file-or-json;也免掉每台機器路徑不同的問題):`disableAllHooks:true`、`autoMemoryEnabled:false`、`enabledPlugins` = 這次
呼叫看得到的**所有** plugin(使用者/專案/local 層級開著的 + 內建的)全部 false、`syncClaudeAiPlugins/Skills:false`、`advisorModel:""`、
`autoDreamEnabled:false`。內建 plugin 的名單**動態取**:每個 Claude 版本跑一次 discovery(`--strict-mcp-config` + 封閉其他東西但不關 plugin,
讀 init 的 `plugins`),快取到 `.state/seal-check/<版本>.json`。

**`--settings` 只有 runtime 能給**:實驗證明兩個 `--settings` 互相蓋掉。政策(租約的 deny 之類)改成 `Task` 的欄位,runtime 合成一份;
契約測試擋 `task.extra` 裡出現 `--settings`。租約(lease-runner 現在用 extra 傳 `--settings`)第 0 步不動,等它要改的時候一起換——
在那之前 R/C 類不加封閉的 settings,所以不會撞。

**connectors job 的正式資料庫**:它們不能 strict-mcp,改用權限規則 deny 環境裡的 MCP(`mcp__project-06-prod-readonly__*` 等,
名單從 `~/.claude.json` 的全域 mcpServers 產生),只放行它們登錄宣告的工具。

**環境變數**:封閉的點用 runtime 宣告的 passthrough 清單(`runtime_claude.passthrough_env()`:PATH、HOME、LANG、TMPDIR、
`CLAUDE_CONFIG_DIR`(MBP 的個人 profile 用 `~/.claude-personal`)、proxy/CA 變數、`ANTHROPIC_*`、登入變數、`HUB_LLM_POINT`)。
**cwd**:reflect-week 現在的 cwd 是 `~/agent-hub`(`weekly-reflect.sh` 先 `cd` 到 hub、`reflect_llm.py` 不給 `--cwd`),會載 agent-hub 的
CLAUDE.md 跟它的專案 hook——**改成空的暫存目錄**(行為改變,上線時對照)。B 類見「重播的忠實度」。

**逐次記錄 init**:A/B 點正式跑時改用 `--output-format stream-json --verbose`(runtime_claude 解析,輸出給 llm.py 的格式不變),
每次都留 init 的摘要:`mcp_servers`、`plugins`、`tools`、`memory_paths`、model、版本。**判定**:
- `mcp_servers` 不是空的、或 `tools` 多於宣告 → **這次呼叫失敗**(走 on_failure;安全性質,不只是警報);
- plugins 不是空的、`memory_paths` 有值 → 警報 + 案例標 `sealed:false`(重播時排除),呼叫照常。
記錄器沒開也做這個判定(它是安全檢查,不是評估功能)。

**seal-check(上線前、版本或設定改變時)**:用 `.state/seal-check/seal_exp2.py` 的做法(真的工具、一個無害的 Edit、金絲雀 hook、
`--debug hooks`),**對照組也帶 `--strict-mcp-config`**;最便宜的模型、每個版本跑一次(不是每個點)。觸發:`claude --version` 變了,或
`~/.claude/settings*.json`、`~/.claude/remote-settings.json`、`~/.claude/plugins/installed_plugins.json`、`~/.claude.json` 的 mcpServers
的 hash 變了;加鎖。分得出「跑不起來」(登入、配額)跟「有漏」。

**上線**:白天;當晚看 librarian、consolidate;reflect-week(週一)跟 consolidate(每專案一週一次)用 `schedulectl run` 強制跑一次對照。
黃金檔:inline 的 settings 內容跟動態的 plugin 名單要正規化。正式跑的是 `librarian.sh`;`librarian.py` 也是從宣告取參數。

## 重播的忠實度

- `--exclude-dynamic-system-prompt-sections` **只是把**每台機器不同的段落(cwd、環境、memory 路徑、git 狀態)移到第一則訊息,不是拿掉——
  r4 把它當成忠實度的開關是錯的。不用它。
- B 類的 cwd 是 git repo(memory 副本),系統提示裡有它的路徑、git 狀態、最近的 commit。做法:**正式也改在一個新的 clone 裡跑**,
  路徑規則跟重播一樣(`<暫存根>/agent-hub-eval/<點>/<專案>`),用 `git clone` 到那個 commit(不是 `git archive`,要有歷史跟狀態);
  跑完照原本的流程把修改帶回副本。consolidate 本來就在暫存 clone 裡跑,一樣改成固定的路徑規則。
- 案例記 cwd、`git status`、日期;日期沒辦法固定,用「正式的現任 vs 重播的現任」的漂移檢查量化它。
- CLAUDE.md 類檔案:目前 memory 副本沒有 CLAUDE.md(只有 AGENT.md)、使用者層級也沒有;內建的 agents-md module 已經關掉。
  案例記下 cwd 祖先目錄有沒有說明檔(hash),有的話重播照放。

## 判斷點分類與樣本量

| 類 | 判斷點 | 怎麼評 | 樣本量 |
|---|---|---|---|
| A | reflect-week | 換模型/runtime 重跑 | 一週 1 次。**回填**只做目前格式(10-21 起只送統計 + digest)的週、而且那週的 digest 還在;標成 synthetic。大約 5 週——不夠下結論,reflect-week 主要靠第二階段的裁判跟 owner 訊號 |
| B | librarian-state | 快照重跑,拆部分評分 | 約 6 次/晚;依專案分群 |
| B | consolidate | 快照重跑;現有機械檢查 | 每專案約一週一次;opus、單獨預算 |
| B | librarian-todo-format | 只測重播的路;案例從歷史 STATE.md 合成 | 37 天 1 次 |
| B′ | librarian-index | 輸入收窄成 `digest/$TODAY.md` + `index.md` 後升 B | 1 次/晚 |
| R、C | 見上 | 不重播;C 類第一階段不評估(owner 決定) | — |
| N | login-check | none | — |

## 宣告格式(跟 Muninn 同一天同步)

只加不升版:`class`、`seal`、`persist_transcript`、`writes: [路徑]`、`runtime`、豁免項目的 `eval`。點的 `material`、`eval` 照填共用欄位。
一致性測試:class 跟 seal/persist 不能矛盾(B 類不能 seal:false)。`agent-job` 的 job 名稱:`run-agent-job.sh` 給 `llm.py` 加 `--job`。

## 權限

`policy.policy()` 是「專案 workspace」的政策,而且它的 deny 剛好擋住 librarian 要讀寫的 memory 副本——不能拿來產生平台判斷點的權限。
**新的點範圍政策** `policy.for_point(point, cwd)`:只照宣告的 `writes`(相對 cwd)產生寫入權限,平台判斷點不套專案的 deny;正式跟重播
都從它產生(重播另外再擋 secrets 跟評估目錄)。非寫入的規則(hubbus 的 Bash、MCP 工具名)留在 `allowed_tools`。契約測試:正式跟重播的權限一樣。

## 案例、記錄、重播、評分、隱私

(跟 r4 相同,摘要)白名單欄位(不存 `Task.env`、denials 的工具輸入、C 類的結果全文);`TaskResult` 加 `duration_ms`、`resolved_model`
(主模型的規則:宣告/指定的那個 model 在 modelUsage 裡的項目);`evals_dir()` = `~/.local/state/agent-hub-evals/`、0700、SQLite;
預設不進雲端備份(並寫明其他副本:review-log、agent-jobs 報告、封閉前的對話紀錄);`eval_case` 往上回傳 + `Eval-Case` trailer;
`Hub-Writer: platform-model | project-agent | mechanical`;只有 owner 本人的訊號算正確答案;重播 = 同一套封閉參數 + `HUB_EVAL_REPLAY=1`
(llm.py/hubbus/alert.py 認得)+ `policy.for_point` + 新 clone 的 cwd,R/C/N/`sealed:false` 拒絕;候選跟現任都跑 k 次、依專案分群
bootstrap、次數預算、避開夜間、租約開著就延後;評分器在 `bin/evals/`、拆部分分數;案例的敏感等級 = 專案 `privacy.material`
(`project.v1.schema.json`、`registry.py` 要跟著改;不是共用格式)、點的 `material` 當上限;`evals.trust` 預設別家雲端只看 cloud_safe、裁判同樣篩選。

## 邊界的保證

- lint(每個 runtime 宣告啟動方式、遞迴掃 `bin/`)**只警告**:靠字串一定有漏(例:runtime_claude 自己的 `[exe, "-p", …]`)。
- **執行時的保證**:在 `hubconf.tool("claude")` 換成一個 shim——帶 `-p` 卻沒有 `HUB_LLM_POINT` 就拒絕並記一筆;另外 hub-event 的
  SessionStart 記下「headless、沒有 HUB_LLM_POINT」的 session(`.logs/hub-event.log`),覆蓋率報告列出來。
- (10-30:`trajectory.py` 移出平台,成為實例自己的工具,下面這條跟要確認的第 3 點不再適用)`trajectory.py`:llm-points 的豁免寫著「Knas 2026-10-22 決定維持原樣」——要改走 llm.py 得他明確同意(要確認的 #3);在那之前它已經帶 strict-mcp。

## 開源的解耦

| 層 | 開源 | 新使用者預設 | Knas |
|---|---|---|---|
| MCP 封閉(已做)、A/B 封閉、逐次 init 判定、seal-check | ✓ | **開**(安全性質;seal-check 每版本一次、最便宜的模型) | 開 |
| 宣告欄位、lint(警告)、shim | ✓ | 開 | 開 |
| 評分器、重播、純文字 runtime | ✓ | 有,換模型時自己跑 | ✓ |
| 記錄器、feedback | ✓ | **關** | 開 |
| 真的案例 | ✗ | — | 本機 |

## 步驟

**第 0 步**:runtime_claude 的封閉參數產生器(inline settings、動態 plugin 名單)+ 逐次 init 判定 → A/B 點改用 → passthrough env、
reflect-week 空 cwd、B 類新 clone 的路徑規則 → connectors job 的 MCP deny → seal-check(含版本/設定 hash 觸發)→ 黃金檔 → 白天上線、
當晚看結果、強制跑 reflect-week 跟一個 consolidate。

**第一階段**:schema 欄位(Muninn 同天)→ `policy.for_point` → shim + 覆蓋率 → 記錄器 → 重播 → 評分(librarian-state、consolidate)→
reflect-week 回填(合成、少量)→ 契約測試依介面 → 純文字 runtime → 覆蓋率報告。

**第二階段**:truth 收集、assistant adapter、成對裁判、librarian-index 升 B、R 類線上評估、C 類(owner 決定之後再談)、別家 agent runtime。

## 要確認的

1. 哪些專案的資料可以標 cloud_safe(決定別家模型能比哪些案例)。
2. 案例預設不進雲端備份。
3. `trajectory.py` 改走 llm.py(推翻 10-22「維持原樣」的豁免)。

## 審查與實驗紀錄

- **r1–r3 盲審**:見 git 歷史的這份文件;重點:重播隔離、案例位置、case id 方向、接續對話、循環、token、輸入重現、seal/persist 拆開、
  `--settings` 互蓋、advisor、樣本量、權限來源、格式要改、lint。另外查到並修了:headless 收信(f2755ca)、送進 session 只算看過(10c5fbb)。
- **第一輪實驗**:`--tools ""` 擋不掉 MCP → dcc61b6;兩個 `--settings` 互蓋。
- **r4 盲審**:實驗證據不足、沒存;對照組會重開 MCP 洩漏;漏 MCP 要當成失敗;dynamic sections 是搬不是拿掉;`--bare`/`--setting-sources`
  沒試;執行時守門保證不了;reflect-week 回填不忠實;租約的 settings 沒有路;C 類沒有評估;minimal_env 弄壞別的設定;
  `policy.policy()` 擋的正是 librarian 要的;另外查到 `models.env` 沒生效(c89c25c)。
- **第二輪實驗**(有紀錄):內建 plugin 的 hook module 不受 `disableAllHooks` 管、要用 `enabledPlugins` 關;`--bare` 不能用;
  `--setting-sources ""` 不比較好;auto-memory 會指到包住 cwd 的 git repo 的 memory。
