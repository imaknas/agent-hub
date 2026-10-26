# 專案信箱:由平台機械地分流與處理

> **狀態**:10-24 Knas 確認並實作(第一步:notes 能力)。`mailbox.handle: work` 還沒做。

## 要解決什麼

信寄給「專案」,但現在要等那個專案剛好有 session 醒著、而且有動作(開 session、打字、用工具)才會被看到;
閒置的 session 不會自己醒來看信,沒人收的要等 12 小時才有 headless agent 接手,而且只能寫筆記。
10-24 實際發生:Muninn 21:30 的信,agent-hub 這邊等到 Knas 打字才看到。

Knas 不想管溝通層。而且有兩種很不一樣的情況,不能用同一套處理:

| | 對話 | 交辦 |
|---|---|---|
| 例子 | 兩個專案的 session 正在來回確認設計 | 「把這件事做掉」 |
| 對象 | 收件專案裡**某個已經有 context 的 session** | 那個**專案**,誰做都可以 |
| 需要 | 讓那個 session(或它的 context)接著回 | 有人負責做完 |

## 原則

1. **寄件只有一種方式**:agent 一律 `hubbus send`,不用判斷該用哪個管道;CLAUDE.md 不教它選。
2. **分流是程式裡的規則,不是 agent 的判斷**:平台用已經記下的事實決定。
3. **跟執行環境(Claude Code)有關的只有一個可替換的模組**(`bin/runtime_claude.py`):「這個 session 還
   活著嗎」「在背景接續這個 session」。換工具或 Claude Code 出了正式的叫醒方法,只改這一個檔。agent 不再
   用 Claude Code 的跨 session 訊息傳專案間的事。

## 已經有的(不用重做)

- `hubbus send` 從 `CLAUDE_CODE_SESSION_ID` 自動記下寄件的 session(`from_session`)。
- 回信(`--reply-to`)自動指定給當初發問的 session(`to_session`);信箱先保留給它 30 分鐘。
- 每封信送進哪個 session 都有 `message.delivered` 紀錄(hook 拿得到 `session_id`)。
- hook 在 session 開始、打字、用完工具(每 2 分鐘最多一次)時把新信送進 context。
- 10-24 驗證:每個 Claude Code session 的程序參數都帶著它的 id(`--resume=<id>` 或 `--session-id=<id>`),
  而且跟 hook 記下的 `session_id`、`CLAUDE_CODE_SESSION_ID` 是同一個——「這個 session 還活著嗎」可以機械判斷。

## 新的分流規則(全部在 hubbus 裡,機械判斷)

每封信寄出時分類:
- **對話**:有 `to_session`(回覆某個 session 寄出的信)。
- **交辦**:其他的 `request` / `question` / `task`。
- **通知**:`info` / `progress` / `done` 等——不用處理,下次有 session 開著時照常看到。

**對話**的送法:
1. 那個 session 還活著(`runtime_claude.alive(sid)`:有 Claude Code 程序的參數帶著這個 session id)→
   等它的 hook 送進去,最多 `CONVERSATION_WAIT`(建議 5 分鐘;它正在被用時幾乎是即時的)。
2. 5 分鐘內沒送進去(閒置中),或那個 session 已經不在了 → 背景接續(`runtime_claude.resume_background`):
   帶著它完整的 context 在背景接著處理,回覆照樣走 `hubbus send`。
   - **走判斷點**:這是無人值守的 claude 呼叫,一樣經過 `bin/llm.py`,宣告新的判斷點 `mailbox-conversation`
     (權限跟現在的 mailbox-escalation 一樣:dontAsk、只准寫 memory 筆記跟 `hubbus`;用 headless token)。
     **不沿用**原本 session 的權限模式(它可能是 acceptEdits/auto,沒人看著不能照用)。
   - 在那個 session **記下的 cwd** 跑(它可能在 worktree 裡),不是專案主資料夾。
   - **租約的 session 絕對不接續**:`to_session` 是 lease-runner 的租約時(深談裡寄出的信),交給 runner 送進
     那個租約;租約已經收回就當交辦處理。在 runner 外面接續租約會繞過它的權限轉接跟 sandbox 設定。
   **代價**:session 還開著時,Claude Code 會另開一個副本(帶著同樣的 context),所以 Knas 回到 Desktop 裡原本
   那個 session 不會直接看到這段,只會在下次有動作時收到「已經處理好了」的信;狀態頁也列出來。

**認領**(避免同一封信兩邊都做):平台派人處理一封信時記一筆 `message.delivered`(`via: handler`);之後 hook
送信給開著的 session 時,被認領的信只列一行「平台正在處理」,不當成要它做的事。反過來,hook 已經送進某個
session 的信,平台不再派人。

**在場紀錄**:每次 hook 執行都記一筆「這個 session 在這個專案、cwd 是哪裡、什麼時候有動作」(新的 `session.seen`
事件或小表),給下面的「有沒有人在用這個專案」跟背景接續的 cwd 用。

**交辦**的送法(取代 12 小時的升級):
- 收件專案**有 session 正在用**(活著、而且最近 `CONVERSATION_WAIT` 內有 hook 動作)→ 先等它的 hook 送進去,
  最多 `CONVERSATION_WAIT`;送進去了就是它的,平台不派人。
- 沒有人在用,或等完了還沒人收 → 馬上在那個專案派一個 headless agent 處理。
- 同一個專案一次只跑一個,同時到的信一起處理。
- 能力看專案登錄 `mailbox.handle`:
  - `notes`(預設):回答、寫 memory 筆記、要決定的事轉問 Knas——就是現在 12 小時升級的能力。
  - `work`(逐一開啟,第二步才做):在自己的 worktree 改程式、OS sandbox、跑測試、合併閘門合回 main(不 push)。
- 需要 Knas 判斷、不可逆、對外的事(push、部署、花錢、傳訊息給人)一律轉成給 Knas 的問題(進 Muninn),不做。

**觸發**:launchd `local.agent-hub-mailbox-dispatch` 每分鐘跑一次 `hubbus dispatch`(延遲最多一分鐘;「等 5 分鐘」
自然落在連續幾次之間),15 分鐘的 bridge 也會跑一次當安全網。**不從寄信的 session 觸發**:寄信的 session 可能在
OS sandbox 裡,從那裡開的背景程序寫不了平台的狀態、也叫不起 agent。
Muninn 的信是 bridge 從 Muninn 拉進來的,所以最慢 15 分鐘才進匯流排;要更快,請 Muninn 排好轉交後
直接跑一次 `hubbus bridge`(同一台機器)。

## 安全與成本

- 信是別的 agent 寄的:只當「請求」,不當「授權」;權限只看專案登錄跟 sandbox。
- 防止兩個 agent 互相回信回不完:同一串往來(reply_to 鏈)由平台接手處理最多 N 輪(建議 4),超過就轉問 Knas。
- 每個專案每天的處理次數/成本上限,超過就停、狀態頁標紅。
- 通道租借的 session(唯讀深談)照舊不算收件人。

## 對 agent 跟 Knas 的改變

- agent:沒有新規則。CLAUDE.md 只把「12 小時沒人收會升級」改成「寄出後平台會處理,不用等對方開著」。
- Knas:只會在 Muninn 收到真的要他決定的問題;處理紀錄在狀態頁。

## 要你拍板的決定

1. **閒置 session 的對話回信,用「接續原本的 session」還是「另派一個新的」?**
   - 接續(上面寫的):context 完整,但很長的 session(像 agent-hub 這個,兩千多則訊息)每接續一次都要
     重讀整段,成本高。
   - 另派新的(交辦那種):便宜,但只看得到這串往來的信件內容,不知道那個 session 之前討論的細節。
   - 折衷:對話不長就接續,太長就另派新的、附上整串信件。
   **Knas 決定(10-24)**:折衷,用「接續要載入的 context」判斷(對話紀錄最後一則回覆的 usage,壓縮過的就是壓縮後的
   大小),15 萬 token 以下接續、以上另派(`hub.json` `mailbox.resume_max_tokens`)。agent-hub 這個 session 當時是
   55 萬,接續一次就要整段重讀。

## 不在這次範圍

- 信已經送進某個 session、它卻一直沒處理(沒 done)的情況,現在沒有後續追蹤,這次也不處理;
  狀態頁會列出「送進去超過一天還沒結案」的信。

## 步驟

1. `runtime_claude.py`(alive、resume_background 經過 llm.py)+ 實測 `--bg --resume` 在 session 開著/關著時的行為。
2. hubbus:寄出分類、寄出即觸發分派、對話的「等 5 分鐘 → 背景接續」、交辦的「馬上派 headless(notes)」、
   輪數與成本上限;拿掉 12 小時升級。測試用假的 runtime 模組。
3. 狀態頁、CLAUDE.md、known-issues。
4. (之後)`mailbox.handle: work` 逐一開啟。

## 實作紀錄(10-24)

- `bin/runtime_claude.py`:`live_sessions`(程序參數)、`context_tokens`(對話紀錄的 usage)、`resume_args`
  (`--resume <id> --fork-session`;實測分支帶著 context、拿到新 id、原本的對話紀錄一行都沒變)。
- `hubbus`:在場紀錄(`presence` 表,hook 每次更新)、認領(`message.delivered` 的 via:handler/resume/held)、
  `dispatch`、`thread`;拿掉 12 小時升級。判斷點 `mailbox-conversation`(dontAsk、只寫 memory 筆記跟 hubbus)。
- 只有 request/question 會被派人或接續;回覆類(done/info/progress)照舊等 hook 送進去,不會為了一封「做完了」
  就接續別人的 session。
- 測試:`tests/test_mailbox_dispatch.py`(14 個:交辦馬上派/等有人在用的 session/暫停的 session 不擋、
  通知不派、hook 送過的不派、不重派、agent-hub 的交辦留給它自己、對話等/接續/太長改派/租約不接續、防循環、
  hook 不把認領的信當成要做的事)。
- 上線時沒有任何待處理的 request/question,所以第一次跑沒有派人。
- **實測(測試沙盒 idea-999996,經過每分鐘的 launchd 服務)**:對話的回信寄出約 30 秒被背景接續,分支副本用原本
  session 才知道的暗號回信並結案,原本 session 的對話紀錄完全沒被動到;交辦約 30 秒派出 headless agent,寫了
  memory 筆記、回信、結案。從別的資料夾 `--resume` 也接得回來(用 session id 找,跟 cwd 無關)。
- advisor 審查後補的:背景接續開不起來就馬上改派交辦,35 分鐘還沒結案也改派(信不會卡在「平台正在處理」);
  bridge 跟每分鐘的服務用同一把鎖(不會兩邊同時派人);背景副本加 `--strict-mcp-config`(不載入專案的 MCP
  伺服器);閒置時被背景處理掉的回信,那個 session 下次有動作時會看到一行說明。

## 還要確認的

- 背景接續改用 `llm.py run`(`claude -p --resume <id> --fork-session`,跑完就結束),沒有用 `--bg`;分支副本
  會不會出現在 Desktop 的 session 清單還沒確認(要等第一次真的接續)。
- `CONVERSATION_WAIT` 5 分鐘、對話最多 4 輪、每日上限:先用建議值,看狀態頁再調。

## 10-26 試行:交辦也用 session 的 context(m10261706,Knas「OK 先試行」)

- **交辦**:專案有人在打字 → 照舊先等它的 hook 5 分鐘。之後如果這個專案 24 小時內(`hub.json mailbox.fork_recent_hours`)
  有動作、還活著的 session,就用它的 context 在背景分支處理(跟對話的接續同一條:`--resume --fork-session`,同樣的
  notes 能力、輪數跟每日上限),答得出那個 session 手上的事(例:Muninn 問 project-06 開著的 session 看 production
  data 的狀況);沒有、context 超過上限、今天派滿 → headless agent(跟以前一樣)。
- **agent-hub 的信**不再留給它專用的 session(沒人打字就沒人收):跟其他專案一樣分支或派 headless。
- **合併回原本的 session**:分支是副本,說的話不會進原本 session 的 context。所以分支做完(有最後回覆、信結案、或逾時)後,
  原本的 session 下次有動作時,那封信下面帶上分支的最後回覆(800 字內)跟它為這封寄出的信;分支還在跑的時候先不提。
  沒有做「真的合併 context」:runtime 沒有這種操作,而且兩邊同時往前走會互相覆蓋;摘要放進下一則提示是一樣的效果。
- **info 不算卡住**:`hubbus status` 的「卡住」只算 request/question;session 的說明寫明要對方做事就用 request/question。
