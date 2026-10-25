# 租約契約跟執行環境無關(m10251446):設計,待 Knas 確認

> **狀態**:10-25 初版;同一天跟總設計的 session 對齊過分工(見「跟總設計的分工」),等 Knas 確認 A–E。確認前兩邊都不動程式。

來源:Muninn 的請求 `m10251446-318e`(10-25 14:46,Knas 已同意方向):`contracts/lease-runner.v1.schema.json` 改成跟 agent
實作無關的通用類別,Claude Code 的名稱在 runner 這一端翻譯。完成條件(Muninn 的):契約裡不再出現 Claude Code 的工具名跟
Claude 訂閱專屬的欄位;runner 有翻譯表跟測試;回信說清楚新舊格式並行到哪天。

跟其他兩份的關係:
- `docs/design/capabilities.md`(第 2 層的中立政策 `policy.py`、翻譯 `runtime_claude.settings()`)——這份補上第 2 層還缺的
  「哪些動作要問人」(`approvals`)。
- `docs/design/runtime-abstraction.md`(總設計,另一個 session)——這份的「事件翻譯」就是它第 1 層 `events()` 的內容;
  分工見「跟總設計的分工」(10-25 已對齊)。

## 現在綁住的地方(對照 Muninn 列的五點,都查過程式)

| # | 綁住的地方 | 程式裡在哪 |
|---|---|---|
| 1 | 事件 `tool_use.name` 是 Claude 的工具名 | `lease-runner.reader()`、`summarize_tool()` |
| 2 | 權限請求的 `tool`(AskUserQuestion / WebFetch / Bash)、`question` 直接是 AskUserQuestion 的 input;事件 `question`/`permission` 也帶 `tool` | `lease-relay.decide()`、`ask_owner()`;runner 把 relay 的回報原樣轉成事件 |
| 3 | `rate_limit` 的 `five_hour`/`seven_day`(+`_resets_at`,epoch 秒),事件跟 `/v1/status` 都有 | `reader()` 的 `rate_limit_event` |
| 4 | `/terminal` 回的指令;接續靠 Claude Code 的對話紀錄(保留 30 天),紀錄不在了會**默默**開新的對話 | `start_proc()` 的 `can_resume`、`bin/lease-resume` |
| 5 | 錯誤碼 `auth` 其實是「Claude 沒登入」 | `reader()` 結尾從 stderr 猜 |

另外兩處 Muninn 沒列、但也是 Claude 的字:`/v1/policy` 的 `no_session_domains` 說明寫 WebFetch;relay 回報的事件裡
`kind: question|permission` 跟事件類型重複(而且新格式要把 `kind` 拿來放動作種類,見下)。
`lease-handover.v1` 查過,沒有 Claude 的字。

## 一套動作種類(kind),全平台共用

```
read | search | ask_user | open_web | write_file | run_command | other
```

- 就是 Muninn 提的那組。**同一組**也用在第 2 層的 `approvals`、判斷點宣告(`llm-points.yaml`)、總設計的對話紀錄統計——
  總設計的原則 8 已經這樣寫,這份把名單定下來。
- 名單放在 `runtime_ports.py`(中立,`KINDS`);**Claude 工具名 → kind 的翻譯表只在 `runtime_claude.py`**:
  `Read`→read;`Glob`、`Grep`、`WebSearch`→search;`WebFetch`→open_web;`Edit`、`Write`、`MultiEdit`、`NotebookEdit`→write_file;
  `Bash`→run_command;`AskUserQuestion`→ask_user;其他(`Task`、`TodoWrite`、MCP 工具…)→other。
  `hub-run` 是 run_command(摘要會寫出是哪個能力)。
- 翻譯函式:`runtime_claude.action(tool, input) -> (kind, collab, target, summary)`——target 是判斷用的(路徑、網址、指令),
  summary 是給人看的一句。runner 的事件、relay 的權限判斷、對話紀錄的統計都用它,不各自寫一份。
- **另一個維度:協作方式**(`collab`,跟 kind 分開,只有一份定義):`subagent | workflow | peer_message | skill` 或 None。
  kind 說「這個動作的效果」(權限、approvals 用),collab 說「怎麼分工」(週報統計用)——交給子 agent、跑 workflow、跨 session
  傳訊息、用技能,在 kind 裡都是 other,統計會看不到,所以另外帶。`runtime_ports.Action` 改成 `Action(kind, detail, collab=None)`,
  總設計原本的 `ACTIONS` 跟 `runtime_claude._action` 在 `action()` 做好後由那邊收掉。

## 契約:v2 跟 v1 並存,Muninn 自己選哪天換

**做法(建議)**:新增 `contracts/lease-runner.v2.schema.json`,v1 那份**一個字都不改**。
- Muninn 用 `v` 選格式:請求帶 `"v": 2` 就回 v2 的形狀;SSE 用 `GET /v1/leases/<id>/events?v=2`。不帶就是 v1,行為跟現在
  逐位元組相同。所以 Muninn 可以一次切過去,不用處理「同一個事件裡新舊欄位都有」。
- 這是 CLAUDE.md 的規則「改名/改意思升版、兩版並存到讀的人換完」;不在 v1 裡加欄位並行,因為 `kind` 在 `question`/`permission`
  事件裡已經有別的意思(question|permission),加欄位會撞名。
- runner 內部只產生 v2 的事件;v1 是一個轉回舊形狀的小函式(`v1_compat()`),截止日一到整個刪掉。
- **截止日(建議 2026-11-08,兩週)**:之後 runner 只認 v2,v1 的 schema 檔移到 `contracts/retired/`。Muninn 提早換完就提早刪。
- 每晚的契約檢查不受影響:v1 檔沒變,Muninn 的副本照樣一致;v2 是新檔,Muninn 在自己的登錄加 `consumes` 那天才開始比對。

**v2 跟 v1 的差別**(其他欄位、端點不變)

| | v1 | v2 |
|---|---|---|
| 事件 `tool_use` | `name`(Claude 工具名)、`summary` | `kind`、`summary`、`detail: {tool}`(原名,除錯用,Muninn 不讀) |
| 事件 `question` | relay 的 `phase/tool/summary/kind/decision` 原樣 | `phase`、`kind: ask_user`、`summary`、`question`(通用結構,見下)、`decision` |
| 事件 `permission` | 同上 | `phase`、`kind`(open_web / write_file / run_command…)、`summary`、`target`、`decision` |
| 事件 `rate_limit` | `five_hour`、`seven_day`、`*_resets_at`(epoch 秒)、`status` | 改名 `quota`:`{status: ok\|warning\|exhausted, windows: [{id, label, used(0–1), resets_at(ISO)}]}` |
| `/v1/status` | `rate_limit: {five_hour, …}` | `quota`:同上,加 `at` |
| `/terminal` 回應 | `{command}` | `{command, hint}`;command 由 runtime 組(現在還是 `lease-resume <id>`),hint 是給人看的一句 |
| 接續 | 紀錄不在就默默開新的 | `state` 事件帶 `resumed: true\|false`;沒接回就 `resumed: false` + `detail`(「之前的對話紀錄已經不在,從新的對話開始」);`lease` 物件加 `resumable` |
| 錯誤碼 | `auth` | `agent_login`,`detail` 寫人看得懂的(「專案 session 用的 Claude Code 沒登入或憑證過期,平台會發警報」) |
| `/v1/policy` | `no_session_domains` 說明寫 WebFetch | 說明改成 open_web(欄位不變) |

- `rate_limit` 的錯誤碼(額度用完)保留:這個詞本身是通用的。
- `quota.windows` 的 Claude 實作:`five_hour` → `{id: "five_hour", label: "5 小時"}`、`seven_day` → `{id: "seven_day", label: "7 天"}`;
  `status` 從 Claude 的 allowed / allowed_warning / rejected 對到 ok / warning / exhausted。Muninn 只照 label 顯示、照 used 判斷。
- `session_id`(建立租約時 Muninn 給的 UUID)保留:那是租約自己的 id;「要是 UUID」寫成 runtime 的限制,不是契約的意思。

## 權限請求(Muninn 的契約 `permission-requests`)

這份是 **Muninn 定義、我們 vendor** 的(`contracts/vendor/muninn/permission-requests.v1.schema.json`),所以由 Muninn 出 v2,
我們照著送。建議的形狀(寫在回信裡給它):

```
request:  {v: 2, id, lease, project, kind, summary, target?, question?, detail?: {tool}}
  kind      ask_user | open_web | write_file | run_command(會送來問的只有這幾種)
  question  {questions: [{question, options: [{label, description?}], multi?: bool}]}   通用結構,不是 AskUserQuestion 的 input
decision: {v: 2, decision: allow | deny | allow_session, answers?: {問題: label 或 [label…](multi)}}
```
- 拿掉 `tool`、`input_summary`(改名 `summary`)、`risk`(跟 kind 重複:question→ask_user、external_fetch→open_web)。
- 多選的答案在 Claude 那邊要合成一個字串,這是 `runtime_claude` 的事,契約用 list。
- relay 依 Muninn 給的版本送;Muninn 換好之前照 v1 送(relay 讀 Muninn 回的 `v` 或設定)。

## 「哪些動作要問人」:approvals(第 2 層補上的一塊)

現在寫死在 `lease-relay.decide()` 裡、用 Claude 工具名判斷。改成中立政策的一部分(`policy.approvals(name, mode)`),
relay 只剩「Claude 的權限協定 ↔ 中立判斷」的轉接:

```
discuss:  read/search → allow;open_web → 登錄網域 allow、其他 ask;ask_user → ask;write_file/run_command/other → deny
work:     同上,加 write_file → 專案資料夾裡 allow、外面 deny;run_command → ask(每次,allow_session 只算這次)
```
- `ask` = 送 owner(Muninn → Knas 的手機)。`allow_session` 的護欄(`no_session_domains`、指令不能整個租約都允許)也寫在這裡。
- 規則跟 Knas 10-25 的決定一樣,**行為不變**;現在的 `tests/test_lease_relay_work.py` 全部照過。
- 判斷點宣告(`llm-points.yaml` 的 `lease-session`/`lease-work`)的 `tools`/`allowed_tools` 也換成 kind 的清單,由 `runtime_claude`
  翻成 `--tools`/`--allowedTools`。這會動跟 Muninn 共用的 `llm-points.v1.schema.json`——總設計第 1 層本來就要做這件事,
  這份只提供 kind 跟 approvals 的格式(分工見下)。

## 跟總設計的分工(10-25 跟那個 session 對齊)

邊界:**這份是「翻譯跟契約」,總設計是「程序生命週期」**。

- **這份(這個 session)**:`KINDS`、`StreamEvent`(中立的對話事件,v2 那張表的形狀;不叫 `Event`/`HubEvent`——`HubEvent`
  已經是第 3 層 hook 事件的名字)、`Action` 加 `collab`,三個都放 `runtime_ports.py`;`runtime_claude.action()`(工具名 → kind、
  collab)、`runtime_claude.parse_stream(line) -> [StreamEvent]`、quota/錯誤分類/接續指令的翻譯、`policy.approvals()`、relay 拆成
  轉接、契約 v2 + v1 相容層、跟 Muninn 協調。會動 `lease-runner.py` 的 `reader()`、事件序列化、`/terminal`、`/v1/status`,跟
  `lease-relay.py`。
- **總設計(另一個 session)**:第 1 層長駐 session 的介面(`open_session/send/events/resume`)跟 lease-runner 的程序管理
  (組旗標、開關程序、`--resume` 判斷),排在這份後面,`events()` 直接用 `parse_stream`;`SessionSpec` 帶
  `approvals = policy.approvals(name, mode)`,由 runtime 接到自己的權限機制。**`llm-points` 的中立化也是它做**(所有判斷點一次換、
  跟 Muninn 共用的 schema 只改一次),用這份的 `KINDS` 跟 approvals 格式;`lease-session`/`lease-work` 兩個點排在這份的 relay
  改完之後(allowed_tools 跟 relay 的行為要一起測)。**這份不改 `llm-points.yaml`。**
- 對話紀錄的統計改用 `action()` 的 collab,`tool_used` hook 事件之後也帶 kind(那邊做)。
- **檔案的先後**:那個 session 正在改 `runtime_ports.py`、`runtime_claude.py`(第 3 層 lifecycle),這份等它 commit 才動這兩支;
  這份做的期間,那邊不動 `lease-runner.py`、`lease-relay.py`,做完 commit 再通知。
- 那邊的 `runtime-abstraction.md` 會照這裡補三件:SessionSpec 引用 approvals、v2/v1 並存照這裡、HubEvent 跟 StreamEvent 分開。

## 測試

- 翻譯表:每個已知的 Claude 工具都有 kind,不認得的是 other;`action()` 的 target/summary。
- 事件翻譯:錄一段真的 stream-json(含 tool_use、rate_limit_event、result、錯誤)→ 中立事件,逐項比對。
- **v1 相容**:同一段錄音,v1 的輸出跟現在的 runner 逐位元組相同(改之前先錄下現在的輸出當基準)。
- v2 契約的範例都通過 `tests/test_contracts.py`;邊界測試把 `lease-runner.py`、`lease-relay.py` 的判斷部分加進
  `NEUTRAL`(Claude 的字只准出現在 `runtime_claude.py`、`llm.py`、relay 的 MCP 轉接那幾行)。
- relay:現在的 `test_lease_relay_work.py` 全部照過(行為不變),加 discuss 的 approvals 測試。
- 假的 runtime(`runtime_fake`)也產生同樣的中立事件,契約測試兩個都跑(總設計原則 3)。

## 要 Knas 確認的

**A. v2 跟 v1 並存、Muninn 用 `v` 選,截止日 2026-11-08**(建議)。不在 v1 裡加欄位並行(`kind` 會撞名)。
**B. 一套動作種類**(上面七個),契約、approvals、判斷點、對話紀錄統計都用它。
**C. 「哪些動作要問人」移進中立政策**(`policy.approvals`),relay 只剩轉接;規則跟現在一樣。
**D. 跟總設計的分工**(上一段,兩個 session 已對齊):這份做翻譯跟契約,那邊做程序生命週期跟 llm-points,這份先做。
**E. 權限請求的 v2 由 Muninn 出**(它的契約),我們在回信附上建議的形狀。

## 順序(每步一個 commit)

1. (等總設計的 session commit 第 3 層)錄下現在 runner 的輸出當 v1 基準;`KINDS`、`StreamEvent`、`Action.collab`、`runtime_claude.action()`、`parse_stream()`;runner 內部改產生 v2 事件、
   v1 走相容層(v1 輸出逐位元組相同)。
2. v2 契約 + `?v=2`/`"v": 2` 的選擇 + `quota`、`/terminal` 的 hint、`resumed`/`resumable`、`agent_login`。
3. `policy.approvals()` + relay 拆成轉接 + 權限請求依 Muninn 的版本送;做完通知總設計的 session(它接著換 llm-points、
   做長駐 session 的介面)。
4. 回信 muninn(新格式、截止日、權限請求 v2 的建議形狀);`hubbus done`。
5. 截止日:刪 v1 相容層、v1 schema 移到 `contracts/retired/`。
