# AGENTS.md

這份是給任何在 agent-hub 這個 repo 裡工作的 coding agent(Claude Code、Codex、Cursor、Gemini CLI…)讀的。
`CLAUDE.md` 只有一行 `@AGENTS.md`(Claude Code 照引用讀這份);不要在 CLAUDE.md 另外寫東西。

**這台機器的實際情況**(哪台跑 hub、有沒有遙控端、owner 跟助理是誰、外部服務的名字、這個部署做過的決定)寫在實例目錄
(`<hub>/.hub-home` 指的地方,預設 `~/.agent-hub`;`bin/hubconf.py dir config` 印出 config 目錄)的 `config/AGENTS.local.md`,
**有就先讀**。這份只寫通用的規則跟索引;衝突時通用規則以這份為準、這台的事實以那份為準。

## 這是什麼

agent-hub 是跨專案的 memory 追蹤 + 排程 + 專案/服務註冊系統。這份檔案是給任何在這裡工作的 session 一個起點索引——細節都寫在
各腳本自己的檔頭註解裡,不在這裡重複。設計文件在 `docs/design/`。

部署的樣子:**一台跑 hub 的機器**(下面叫「hub 那台」:排程器、服務、memory 的 bare repo、專案的開發副本都在這台;hub.json
`hosts.server` 是它的 ssh 名稱,一台機器全包就留空),可以再加**遙控那台**(例:筆電用 coding agent 的遠端功能開 hub 那台的
資料夾,只跑少數東西,見 `bin/minilib.sh`)。

## 工具中立(硬規則;設計 docs/design/tool-agnostic.md)

agent-hub 要給任何 coding agent、任何 LLM、任何 key 的人用。

- **平台啟動的工作跟平台的資料(匯流排、登錄、STATE.md、筆記)由平台保證,不靠任何工具的功能。** 要成立的事用平台自己的
  機制做:判斷點讓模型回答案、平台寫檔(completion);要動檔案的 agent 工作在丟棄式 worktree 跑、平台只套用准許的路徑。
  工具原生的功能(權限設定、hook)只當加強,拿掉也不影響正確性。使用者自己開的 session 只有工具原生的保護,平台給建議、不保證。
- **工具的語法只在轉接器裡**:`runtime_<名稱>.py`、`provider_<名稱>.py`、`modules/<工具>.yaml`。平台其他地方只用中立的詞
  (`runtime_ports` 的介面、路徑、指令、tier、事件名)。
- **能力不足就明確關掉、說原因**(`runtime_ports.Unsupported`、doctor 的功能表),不拿空資料照跑。
- 機械檢查(commit 閘門會跑):`tests/test_runtime_boundary.py`(Claude 的語法、模型名稱只准變少)、`tests/test_tool_agnostic.py`
  (核心判斷點要能用任何 provider 跑、直接 import Claude 轉接器的檔案只准變少)、`tests/test_runtime_none_flow.py`(沒有 coding agent 時
  每個地方都說「不支援」)。

## 寫新程式的規則

- **路徑、名稱、owner 一律從 `bin/hubconf.py` 拿**(設定在 `hub.json`;hub 根目錄由腳本位置推出,
  `AGENT_HUB_ROOT` 可覆蓋)。不要寫死 `~/agent-hub`、`~/services`、專案名稱、owner 的名字。
- 協定裡的識別字用英文(hubbus 的 owner 地址是 `owner`,別名從 hub.json 讀);使用者身分(〔owner〕標籤)從設定讀。
- 不要把個人資料放進程式碼、測試、公開的文件(`tests/test_personal_boundary.py` 機械檢查);這個部署特有的事寫進實例目錄的
  `config/AGENTS.local.md`。**私有專案的名字一個都不准出現在平台 repo**(範例用通用的名字);清單在實例的 `config/private-names.txt`,
  commit 閘門會帶進去檢查(沒有清單的部署略過)。
- **Python 最低 3.13,一律用平台的 Python `bin/py`**:uv 管的 venv(3.13 + PyYAML,新機器跑
  `bin/setup-python`,`--check` 只驗證)。腳本第一行用 `#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"`(macOS 專用),
  shell/服務/登錄的 `run:` 叫 `bin/py <腳本>`,Python 裡啟動別的腳本用 `hubconf.launch()`(= `sys.executable` + 腳本),hook 這類
  shell 字串用 `hubconf.sh_py()`;不寫死任何直譯器、不靠 PATH 上的 `python3`,**也不靠第一行**(只在 macOS 有用;Linux 跑的是
  `bin/linux-test`,docker 容器裡跑全部測試)。測試:`bin/py -m unittest discover -s tests`,或平行跑的 `bin/run-tests`(閘門用這個,約 1 分鐘)。`tests/test_python_floor.py` 機械檢查;刻意的例外只有
  `scheduler-watchdog.py`(平台的 Python 壞了它還要能發警報,系統內建的 python3、只用標準函式庫)跟在遙控那台跑的 `migrate-to-mini.py`

## 先查這裡,不要自己猜

- **使用者的東西放哪**:`hubconf.config_dir()`(hub.json、登錄、schedule.yaml…)、`data_dir()`(digest、todo、review-log、reflect…)、
  `secrets_dir()`;shell 用 `bin/hubpaths.sh`。`<hub>/.hub-home` 指到實例目錄(預設 `~/.agent-hub`:config 是本機 git repo、data、
  secrets);hub 根目錄沒有任何使用者的東西。新程式一律經過這些,不要自己拼路徑(ratchet 會擋)
- **平台的 CLI 叫 `agenthub`**(`bin/hub` 是舊名稱,轉過去)。`agenthub install-cli` 放上 PATH;
  專案的 session 用 `agenthub send/inbox/done/…`、`agenthub merge-branch`、`agenthub cap-run`(規則檔裡只寫這些,不寫本機路徑);
  同樣的信箱也是 MCP 伺服器 `agenthub mcp`(`bin/mcp_server.py`,任何支援 MCP 的 agent;登記方式 docs/mcp.md)
- **平台有哪些指令**:`bin/agenthub --help`(init、doctor、config、llm、modules、onboard、recover、rename、status、jobs、run);平台的判斷交給哪家模型、key 放哪:`agenthub llm setup`(init 也會問);
  哪裡壞了先跑 `bin/agenthub doctor`。新機器:`bin/setup` → `agenthub init`

- **想知道某個專案現在狀態、綁定哪些服務、STATE.md 寫什麼**:
  `bin/overview.sh [專案名稱]`(不帶參數列出全部)
- **有哪些專案被 onboard 過**:專案登錄(config 的 `projects/<name>.yaml`;`paths.workspace` 是 hub 那台上的位置;
  `bin/hubconf.py roster` 一個專案一行列出名稱/路徑/origin/memory bare repo,shell 腳本用這個讀)
- **hub 那台上有哪些背景服務在跑**:config 的 `services.tsv`(手寫的清單,含不是平台管的)
- **有哪些 GitHub repo 被 mirror 備份**:config 的 `mirror-list.txt`
- **跨專案還有哪些事沒做(誰能做)**:data 的 `todo.md`(每晚 librarian 之後的 `agent-hub/todo` job 重生)
- **這週在各專案做了什麼、作息、跟 AI 協作的方式**:data 的 `reflect/index.html`(所有週的分析報告頁,
  資料在 `reflect/YYYY-Www.json`;每週一產生,reflect 模組)
- **自動化/排程/看門狗出怪錯,或要設計新的自動化**:先查 `known-issues.md`(具體的坑 + 運作原則)

## 建立/救援

- **開一個新專案,或把既有資料夾接上這套系統**:
  `bin/onboard-project.sh [--public] [--no-remote] ~/workspace/<name>`(在 hub 那台跑)——全自動(會自
  動 git init、補第一個 commit、建 GitHub repo),細節看檔頭註解跟
  `.claude/skills/new-or-onboard-project/SKILL.md`
- **新機器,或某個專案本機資料夾不見了**:`bin/recover-project.sh <name>`
  ——**絕對不要**對已經 onboard 過的專案重跑 `onboard-project.sh`,會建出
  一份全新、跟舊歷史斷開的 memory,不是救回來。細節看
  `.claude/skills/recover-projects/SKILL.md`
- **想法沙盒**(助理替想法開的探討用專案,登錄 `type: idea`,`~/workspace/idea-<n>`,沒有 GitHub remote):
  助理透過 lease-runner 的 `POST /v1/sandboxes` 開(→ `bin/create-idea-sandbox.py`);升級成正式專案用
  `bin/upgrade-idea.py <idea-n> [--rename <新名稱>] [--public]`(先 dry-run,再加 `--apply`)。設計在
  `docs/design/idea-sandboxes.md`
- **專案改名**(GitHub repo 已經改好之後):`bin/rename-project.sh <old> <new> [--path <新路徑>]`
  ——登記、memory bare repo / `~/.memory-copies` 副本 / checkpoint / mirror、本機資料夾
  一次改完,memory 歷史保留。先 dry-run,再加 `--apply`
- **把專案的開發環境從遙控那台搬到 hub 那台**:`bin/migrate-to-mini.py <name>`,在遙控那台跑——前置檢查(沒有未 commit/未 push、
  沒有 session 開著)、recover 到 hub 那台的 `~/workspace/<name>`、複製不進版控的狀態檔、auto memory、專案 MCP 設定、
  `.git/info/exclude`、重建依賴。先 dry-run,再加 `--apply`;原本的資料夾不動
- **onboard/rename 這類要動到 hub 那台的腳本**:在 hub 那台跑時直接在本機做,在遙控那台跑才走 ssh
  (共用判斷在 `bin/minilib.sh`)
- **某個背景服務需要在新機器重建**:`bin/recover-service.sh <label>`
  ——只有 `services.tsv` 裡 owner 是 agent-hub 的項目能全自動重建,其他
  (Homebrew、GitHub Actions、別的工具自己管的)只會印出重建步驟,不會
  用猜的去動別人的東西

## 專案信箱 / 事件匯流排

- `bin/hubbus`——跨專案(跟 owner)的信,收件人是**專案**(或 `owner` = 送進 owner 端:助理,或內建的 owner 端),不是 session;
  只 append 的事件 log(`hub.json bus_dir`,預設 `~/.local/state/agent-hub-bus/bus.db`,每台機器一份、不進版控;每晚的加密備份用
  SQLite backup API 另外抓),信箱是它的投影。各專案的 session 用 `agenthub send/inbox/done`(寫在 `templates/agent-instructions.md`);
  信由 session 開始、使用者送訊息、跑完工具時的 hook 送進 session(有 hook 的執行環境),或由 MCP 伺服器的 `inbox` 讀;`hubbus-bridge`
  (每 15 分鐘)負責助理的轉交進信箱、給 owner 的信跟轉交的 done 送進助理。**沒人收的 request/question
  由平台處理**(`hubbus dispatch`,服務 `local.agent-hub-mailbox-dispatch` 每分鐘;設計
  `docs/design/mailbox-routing.md`):回覆某個 session 的對話,那個 session 開著就先等它的 hook 5 分鐘,閒置
  就用它的 context 在背景接續(分支副本,執行環境要支援;context 太大改派新的);交辦則專案
  有人正在用就等 5 分鐘,之後用這個專案 24 小時內有動作、還活著的 session 在背景分支處理(答得出它手上的事),
  沒有這種 session 才派 headless agent(`templates/prompts/mailbox.md`,只寫筆記/回信/問 owner,不改程式碼;`mailbox.handler: propose`
  時改在丟棄式 worktree 跑、平台代寄,`bin/propose_apply.py`)。agent-hub 的信也一樣。
  分支做完,原本的 session 下次有動作時會收到分支的回覆跟它寄出的信。只有 request/question 會被處理,info 是通知。
  派出去的信記成「平台正在處理」,開著的 session 不會重做;同一串最多代為處理 4 輪、每專案每天 6 次(hub.json
  `mailbox`)。跟 Claude Code 有關的判斷只在 `bin/runtime_claude.py`。轉交不經過這裡。
  專案問 owner 的問題在專案 session 裡解決了:`agenthub send --to owner --type done|drop --question <id>`,owner 端收掉;開 session
  時列出這個專案還開著的問題(❓),每晚 librarian 之後的 `assistant/reconcile-questions` job(`modules/assistant.yaml`)問開著超過 3 天的
  還要不要(深談交接的問題由助理自己管,不問)。
  助理的轉交由 librarian 每晚從信箱讀(`hubbus open-handoffs`)直接套用進 STATE.md,結果可信才
  `hubbus done`(bridge 再回報助理結案);session 白天看到只當參考,owner 的回答由發問的 session
  處理、沒處理的也是晚上套用。介面跟失敗模式看 `bin/hubbus` 檔頭
- **〔owner〕待辦的登錄**(`bin/registrar.py`,核心的平台 job,每晚 librarian 之後、todo 之前,零 LLM):各 STATE.md 的〔owner〕行
  照 Principal Handoff 0.3 寄給 owner 端(助理,或內建的 `bin/owner.py`)、收回結案照 key 套用;舊版的 `bin/knas-tasks.py` 在過渡期間留著

## 通道租借(助理借對話給專案 session;assistant 模組)

- `bin/lease-runner.py`(服務 `local.agent-hub.lease-runner`,127.0.0.1:18797,token 在平台的 secrets 目錄 `runner-token`)——
  助理開租約、轉送訊息、SSE 收事件、收回;每個租約一個 `llm.py exec lease-session` 的長駐 session
  (唯讀工具、帶第二階段的 deny 清單、`HUBBUS_LEASE=1`)。閒置 10 分鐘停程序、30 分鐘問一次
  (助理回報 warned 後 10 分鐘收回)。收回時 `lease-handover` 判斷點寫結構化交接 → 匯流排、專案信箱、給 owner 的
  task/question/progress、memory 筆記。API 跟事件:`contracts/lease-runner.v2.schema.json`、交接:
  `contracts/lease-handover.v1.schema.json`;設計在檔頭。沒有支援長駐串流的執行環境時,開租約回 `runtime_unsupported`
- `bin/lease-relay.py`——租約 session 的權限轉接(MCP):提問跟不在登錄網域的 WebFetch 送助理(手機),其他拒絕;
  `hub.json` 的 `lease.no_session_domains` 不准「整個租約都允許」
- `bin/lease-resume <id>`——在終端機接續租約

## 這些給 hook/其他腳本內部呼叫,不要手動直接跑

- `bin/git-safe-push.sh`——push 遇到分岔自動 pull 重試;真的內容衝突才
  會失敗讓人看,不會用任何策略幫你硬猜要留哪一邊
- `bin/git-sync-memory.sh`——session 結束 / 對話壓縮前的 hook 用,**只** commit
  `memory/` 跟 `STATE.md`,不會動到專案的真實程式碼。任何專案的這個 hook
  都不該改成 `git add -A`(會把使用者還沒準備好的真實工作一起 commit 掉)
- `bin/with-lock.sh`——避免多個 session 同時操作同一個 git repo 互撞
- `bin/gen-index-roster.sh`——從專案登錄機械重生
  `index.md` 的專案名冊區塊(`roster:begin/end` 之間),librarian 每次跑、
  `onboard-project.sh` 登記時都會呼叫。名冊不交給 LLM 判斷,LLM 只寫區塊外
  的註記
- `bin/sync-local-setup.sh`——把專案的本機專用設定(執行環境的規則檔——Claude Code 是 `CLAUDE.local.md`——裡
  `agent-hub:begin/end` 區塊、`.agent-memory/AGENT.md`(會 commit+push 這一個檔)、hook 設定、兩者的
  ignore)對齊目前的 `templates/`。每個專案的 session 開始時自動跑,
  onboard/recover 也呼叫——**改了 `templates/agent-instructions.md` 不用手動去各專案
  補**,下次開 session 就生效。只動不進版控的檔案,ignore 補在
  `.git/info/exclude`,不改 `.gitignore`
- `bin/memory-nudge.py`——memory 筆記比專案最新 commit 舊時提醒 agent 補寫:git commit/push
  之後跟 session 開始、含對話壓縮後(sync-local-setup.sh 呼叫)。筆記沒落後就完全不輸出
- `bin/worktree-memory.sh`——git worktree 裡的 session(例:agent 用 worktree 模式叫出來的
  `<專案>/.claude/worktrees/…`)沒有 `.agent-memory`、規則檔、專案 hook,這支用 symlink
  接上主 checkout 的那兩份並處理同步/提醒。掛在**使用者層級**的工具設定,用 `merge-hook-settings.py --worktree <檔案>` 安裝;
  新機器也要裝一次。在主 checkout 裡什麼都不做
- `bin/merge-hook-settings.py`——合併(不覆寫)一個專案 `.claude/
  settings.local.json` 的 hook 設定,`onboard-project.sh` / `recover-project.sh`
  自己會呼叫,不需要手動跑。hook 刻意不寫進共享的 `settings.json`:指令含
  hub 的本機路徑,commit 進 repo 就把這台機器的路徑公開了

## 排程 / 夜間自動化(跑在 hub 那台)

- `bin/scheduler.py` + `schedule.yaml` + `bin/schedulectl`——每 15 分鐘
  觸發的排程器;`schedulectl` 是查看/調整排程的 CLI(例如
  `schedulectl models` 查每個判斷點用的模型、`schedulectl run <name>` 現在就跑一次)。
  **所有排程只放這裡**,包括需要模型判斷的(不要用桌面 app 自己的排程:電腦醒著、app 開著才跑):登錄的 job(`<專案>/<job>`)
  或 command 寫 `bin/run-agent-job.sh <name> [工作目錄]`,報告與狀態在 `.state/agent-jobs/<name>/`
- **長 job 加 `background: true`**:scheduler 一個一個依序跑 job,幾分鐘以上的
  job(例:librarian)不加就會讓後面的全部等它。背景 job 跑完的結果下一個 tick 才併回狀態,細節在 `scheduler.py` 檔頭。
  背景 job 之間有先後(動同一份東西)就加 `after: [job]`,不要只靠時間錯開
- **排程器看門狗** `bin/scheduler-watchdog.py`(`local.agent-hub-scheduler-watchdog`,每 5 分鐘)——
  排程器超過 35 分鐘沒 tick 就發 Discord。刻意不靠排程器跟平台的 Python;Discord 設定在
  secrets 目錄(不進版控),沒設定狀態頁會標紅
- **排程狀態頁**(服務 `local.status-page`,預設只聽 127.0.0.1:18790;要從別台看就自己用 VPN / tunnel 開出去)——
  `bin/gen-status-page.py` 每個 tick 重生:哪些 job 失敗、哪些報告要處理(🔔)、下次什麼時候跑、
  agent job 的報告全文
- `bin/librarian.py`——「圖書館員」,讀各專案 `memory/` 的新增內容,判斷
  要不要更新 `STATE.md`,並產生跨專案 `digest/YYYY-MM-DD.md` + 更新
  `index.md`。模型:判斷點宣告要多強(`model_group`),實際模型只由 `llm.model_for` 算(執行環境的組別表 → hub.json `llm.points` 蓋過;
  `schedulectl models` 看、`set-model` 改)。影子模式:completion 版的判斷點一起跑、只比對不寫(`bin/librarian-shadow-report.py`)。
  後面的機械步驟各自是 `after: librarian` 的平台 job(`projects/agent-hub.yaml`):
  registrar、reconcile-questions、todo、memory-gaps、`librarian-publish`(名冊 + 只 commit digest/index/review-log/todo);
  digest 分段寫(`hublib.py digest-append <段落>`),每個 job 只換自己那段
- `bin/consolidate-memory.py`——「回頭清理」(consolidate 模組),每晚 02:00(librarian 之前):超過 14 天的 memory 日期檔
  整理進 `reference/` 再 git rm、修正被推翻的敘述(保留「預計/可能」語氣,有機械檢查);一晚最多 3 個
  專案、每個專案最多一週一次,判斷點 `consolidate`(`model_group: strong`)。`--list` 看狀態、`--dry-run --project <name>`
  只產生提議的 diff。改動記在 `review-log.md`,細節看檔頭註解
- `bin/gen-todo.py`——平台 job `agent-hub/todo`(每晚 librarian、registrar 之後):收集各專案 STATE.md 的 `## 待辦` 段落(固定格式
  `- [ ] 〔owner|agent|待定〕…`,由 librarian 維護),機械產生 `todo.md`,不用 LLM;格式不對的專案列在檔尾
- `bin/weekly-reflect.sh`(→ `weekly-reflect.py` + `reflect_llm.py`,頁面樣板 `templates/reflect.html`;reflect 模組)
  ——每週一 07:00:上一週的跨專案活動分析。統計(各專案訊息/session/commit/筆記、時段、深夜比例、最長連續工作、交辦方式)零 LLM;
  「主題與 AI 協作觀察」一週一次模型呼叫,**只送統計數字,對話原文不送雲端**;專案登錄 `privacy.reflection` 不是 include 的連摘要也不送
- `bin/evals/`: evals for prompt and rule changes (docs/design/i18n.md). `e2.py` freezes judgment-point inputs (the
  librarian records each night's into `hubconf.evals_dir()`), runs prompt versions through `llm.py` and scores them;
  `e3.py` runs the project rules in a throwaway project. Runs use their own config copy and bus
- `bin/hublib.py`——共用判斷:真人訊息、transcript ↔ 專案對應、哪些
  commit 算專案工作。check-memory-gaps / memory-nudge / weekly-reflect 都用它
- `bin/backup.py`——**加密雲端備份**(backup 模組;restic,目的地跟保留規則在 `hub.json` 的 `backup`)。
  `backup/backup` 每晚 05:45:SQLite 用 backup API 複製、memory 的 bare repo 打 bundle、
  各 workspace 沒 push/沒 commit 的東西跟 `.env`、加上 `.state` / secrets / 服務資料 / transcript;
  週日套保留(14 天、8 週、12 月)。`backup/backup-verify` 每月 1 號還原到暫存目錄驗證。密碼跟金鑰在 secrets 目錄
  (`backup-password`、`b2-env`;**密碼另外存一份在別的地方**——備份裡那份解不開自己)。還原:
  `bin/backup.py restore <目的地> [--include <路徑>]`
- `bin/mirror-backup.sh`——讀 `mirror-list.txt`,對每個 GitHub repo 維護
  一份唯讀 `--mirror` clone,純粹災難復原用,跟 librarian 的摘要流程完全
  獨立、零 LLM 成本(mirror 模組)
- `bin/transcript-backup.sh`——遙控那台 → hub 那台的對話紀錄每小時備份(這支跑在遙控那台;只有在遙控那台**本機**開 session 才需要,
  經 SSH 開在 hub 那台的 session 對話本來就在 hub 那台)
- `bin/check-memory-gaps.py`——平台 job `agent-hub/memory-gaps`(`--apply`,每晚 todo 之後):最近 72 小時有對話、memory 卻沒有新筆記
  的專案,列進 `todo.md` 固定區塊跟 digest(零 LLM 成本)
- `bin/sync-all-memory.sh`——每小時對所有專案的 `.agent-memory` 補跑
  `git-sync-memory.sh` + 補 push(hook 的安全網;服務 `local.sync-all-memory-mini`)

## hub 那台上的路徑慣例

四個看起來像但用途完全不同、不要混用(位置在 hub.json:`git_origin`、`memory_copies`、`services_root`、`workspace_root`):

- `~/git-origin/<name>-memory.git`——bare repo,只有 `agent-memory` 分支
  (`onboard-project.sh` 建的)
- `~/.memory-copies/<name>/`——**memory-only** 的副本,給 librarian 用(隱藏目錄,不是給人
  開 session 的)(只有 AGENT.md/STATE.md/memory/,不是專案的真實程式碼;`onboard-project.sh` clone 出來的)
- `~/services/<name>/`——**真的程式碼**的完整 git clone,給需要在 hub 那台上
  長駐執行 process 的專案用(例:webhook receiver)。
  跟 `~/.memory-copies/` 的差別:這裡是真的會被 `node`/`python` 之類的指令
  執行、需要 `.env`/`data/` 之類本機專屬狀態的東西,不是 librarian 讀
  memory 用的。**每個 `~/services/` 專案都要有自己的 `bin/deploy.sh`**,在開發副本
  執行、一個指令完成部署(git pull + 裝依賴/rebuild + 需要的話重啟服務 + 健康檢查)。不是 rsync、
  不是 `onboard-project.sh`,也不是手動 ssh 進去 pull
- `~/workspace/<name>/`——**所有專案的開發副本**(含 `.agent-memory` worktree;就是
  登錄的 `paths.workspace`),在這裡開 session。新專案用 `onboard-project.sh`
  建在這裡,救援用 `recover-project.sh`;memory remote 直接指向本機 bare repo。不要在這裡跑服務
  (那是 `~/services/`),也不要拿 `~/.memory-copies/` 當開發副本

新專案如果需要在 hub 那台上跑一個長駐服務,才用 `~/services/`;純粹要有
memory 追蹤但不需要長駐執行任何東西的專案(大多數專案都是這種),
不需要在 `~/services/` 底下有任何東西。

## 目錄

- `bin/`——所有腳本
- `launchd/`(Linux:同一份樣板由 `bin/systemd_units.py` 翻成 systemd user unit,`hubconf.service_manager()` 決定用哪個)——只剩排程器跟
  看門狗(刻意不經過 render-services:排程器壞了正是看門狗要報的事)、跟遙控那台的對話紀錄備份(`local.transcript-backup`)。
  狀態頁、信箱 dispatch、memory 同步寫在 `projects/agent-hub.yaml` 的 `services:`,由 render-services 產生。hub 那台的 ssh 名稱在
  `hub.json` 的 `hosts.server`(`hubconf.host("server")`;shell 用 `hubpaths.sh` 的 `hub_server`,遙控那台不一定有平台的 Python)。
  **專案的長駐服務不在這裡**:寫在專案登錄的 `services:`,`bin/render-services.py`(平台 job,每 15 分鐘)產生
  launchd plist / systemd unit,有變才重載;指令找不到就不動能跑的那份、服務沒在跑就發警報。
  **node(跟 claude)只由 nvm 管**:服務跟排程用 nvm 預設版本(`hubconf.nvm_bin()`、env 的 `$PLATFORM_PATH`),
  不要用 Homebrew 裝 node——原因看 known-issues.md「設定、安裝、憑證」。
  claude 模組(`modules/claude.yaml`):`agent-hub/update-claude`(每晚,`bin/update-claude.py`)確保 claude 沒有默默落後;
  `claude-login`(每小時,`bin/check-claude-login.py`)實際打請求/看到期日,壞了或快到期發警報;平台的 `claude -p`(`llm.py`)預設用
  headless token,要 claude.ai connector 的 job 在登錄寫 `connectors: true` 才用訂閱登入
- **平行 session 的合併閘門** `bin/merge-branch`(在 session 自己的 worktree 跑):rebase → 登錄的
  `runtime.test` → fast-forward main(主 checkout)→ 只有登錄 `push_on_merge: true` 才 push;匯流排記
  `merge.done`/`merge.failed`/`session.report`。狀態頁列各專案的 worktree、兩個 branch 改同一檔、
  沒經過閘門直接 commit 到 main 的數量(只是資訊)。`bin/prune-worktrees [--yes]` 清已合併、乾淨的
  worktree(連 branch,**由人跑**)。另外平台 job `agent-hub/prune-worktrees`(每晚 04:30,`--auto`)自動清**沒有任何 session 開著**的
  孤兒 worktree(有 session 開著就不碰,就算閒置):閒置 ≥ 2 天只刪重建得回來的依賴(資料夾留著,session 開始時會提醒
  重建)、≥ 14 天才移除 worktree(branch 留著);規則在 `bin/worktrees.py` 檔頭
- **config pipeline**(`docs/design/new-user.md` 3.7):config = `hub.json`、登錄、`mirror-list.txt`、`services.tsv`、
  `schedule.yaml`。`agenthub config check`(= `bin/config-check`,githooks/pre-commit 對暫存區跑;`hub.json` 照
  `contracts/hub.v1.schema.json`,不認得的鍵會被擋)、`plan`(會改什麼,不動任何東西)、`apply`(check → 套用 → 再 plan 要是空的 →
  記 `.state/config-applied.json` + 匯流排 `config.applied`;狀態頁標題顯示生效的 config commit)。套用者登記在 `bin/appliers.py`
  (排程 job 清單、render-services、render-project-settings),契約測試 `tests/test_appliers.py`;每 15 分鐘的兩個 job 照跑當安全網。
  改了登錄要馬上生效就跑 `agenthub config apply`
- **第二階段強制**:登錄 `enforce: {sandbox: true}` 的專案,`bin/render-project-settings.py`(平台 job,每 15
  分鐘)把 sandbox(只准寫自己的專案/runtime/匯流排/cache、讀不到別的專案、網路白名單)跟 deny 規則寫進它的
  工具設定(Claude Code:`.claude/settings.local.json`);被改就寫回、狀態頁紅;`enforce` 關掉就拿掉。`--report` 看每個專案會寫什麼。
  要寫什麼由 `bin/policy.py`(中立的政策,不准有執行環境的語法,`tests/test_runtime_boundary.py` 檢查)算、
  `runtime_claude.settings()` 翻成 Claude 的格式。**能力**(`enforce.capabilities`,例:想法沙盒的 `gpu`;清單在
  `hub.json` 的 `capabilities` + `capabilities/<名稱>.sb`):sandbox 擋住的程式用 `bin/hub-run --cap <能力> -- …`,由平台
  代跑服務 `bin/cap-runner.py` 在平台自己的 Seatbelt profile(`bin/seatbelt.py`,擋得住 Keychain;只有 macOS)底下執行。設計
  `docs/design/capabilities.md`。`type`/`enforce`/`network`/`allow_write`/`privacy` 是安全欄位,`manifest.update` 改不了。
  所有模型呼叫經過 `bin/llm.py`(判斷點宣告在 `contracts/llm-points.yaml`,每晚 lint);無人值守的
  agent job 在登錄宣告 `allow:` 就不會停下來問
- **使用者層級的工具設定**(例:Claude Code 的 `~/.claude/settings.json`)**只有 owner 本人在那個 session 直接確認才改**,轉述的同意不算
- `contracts/`——**版本契約**:agent-hub 定義的介面(hubbus、todo-md、state-todo、reflect-week、project、llm-points、lease-runner)
  跟 vendor 的別人的契約(`contracts/vendor/<專案>/`)。登錄(跟模組)的 `provides`/`consumes` 宣告誰定義、誰讀;
  `tests/test_contracts.py` 驗證範例,`bin/contracts-check.py`
  (每晚 01:30)比對副本跟原版、驗證實際資料。加欄位不升版;改名/改意思升版、兩版並存到讀的人換完
- `modules/`——**可以不開的功能**(docs/design/new-user.md 3.2):`<名稱>.yaml` 跟登錄同一種 jobs/services/secrets/consumes 格式
  (`type: module`、cwd 一律 hub),`hub.json` 的 `modules.<名稱>: true` 才採用,job 在排程裡叫 `<模組>/<job>`。現有:
  assistant(reconcile-questions、lease-runner)、backup、claude、consolidate、mirror、owner、reflect、sandbox(cap-runner)。
  模組用 `before: [<專案>/<job>]` 把自己插進核心流程(核心不用知道它),沒開就跟沒有一樣(別人的 after: 指到它也不會等);
  `after:`/`before:` 繞圈會被 registry 擋下。搬進模組的 job 用 `legacy_name: <舊全名>` 帶著排程狀態
- **專案登錄**(config 的 `projects/`,一個專案一個 `<name>.yaml`;目錄由 `hub.json` 的 `registry_dir` 指定;平台 repo 的 `projects/`
  只放平台自己的 `agent-hub.yaml`):
  路徑、job、service、隱私標籤、測試指令。驗證跟格式看 `bin/registry.py` 檔頭;`registry.py check` 驗證。
  排程器跑的 = `schedule.yaml`(切換期間的舊項目)+ 登錄的 job(`<專案>/<job>`)
- `templates/`——骨架檔案(`AGENT.md`、`agent-instructions.md`:給每個專案 agent 的說明,人名寫成 `{{owner}}`/`{{owner_alias}}`/`{{assistant}}`,
  sync-local-setup 照 hub.json 填(`hubconf.fill_names`));hook 設定不在這裡,
  寫在 `bin/merge-hook-settings.py`。`templates/rules/<名稱>.md` 是可選的專案規則,
  專案在 `.agent-memory/RULES` 登記名稱才會放進它的規則檔(onboard 新專案時自動登記 `engineering` 跟 `multi-agent-review`)
- `.claude/skills/`——`new-or-onboard-project`、`recover-projects`
- data 目錄的 `digest/`、`index.md`(名冊區塊由 `gen-index-roster.sh` 產生)、`review-log.md`、`todo.md`——librarian 的產出,不要
  手動改,改了下次跑會被蓋掉或造成混淆

## 憑證歸誰管

- **三層**:機器層級的(coding agent 的登入/token、警報管道、備份的金鑰跟密碼、gh)**平台管**,宣告在用到它的模組
  (`modules/<名稱>.yaml` 的 `secrets:`,沒開就不檢查;助理模組讀的別人的契約也寫在模組的 `consumes:`),警報跟 LLM provider 的 key
  由 check-secrets 從 hub.json 推出來;專案自己服務的金鑰(LINE、Google OAuth、IG…)**專案管**,宣告在自己的登錄
  (專案用 manifest.update 改);到外部服務發新的、撤銷、換掉**只有 owner 能做**(〔owner〕待辦)
- **登錄的 `secrets:` 只寫名稱、位置、到期,不寫值**(格式見 `bin/registry.py` 檔頭);平台 job `agent-hub/secrets`
  (每天 08:00,`bin/check-secrets.py`)檢查在不在、有沒有被備份(`backup.covers()`,跟每晚備份同一套規則)、
  快不快到期,有問題發警報;也列出檔名像憑證、卻沒宣告的檔案(只提醒)。這支跟 `check-claude-login.py` 都不印值
- **警報**(`bin/alert.py`;管道:Discord、ntfy、email、macOS 通知,設定在 hub.json `alerts`):新問題馬上發、沒解決的每 24 小時再提醒、
  解決時發恢復;**發不出去記在 `.state/alerts-failed.json`,狀態頁標紅**。secrets job 每天先確認警報管道
  還能用(不發訊息)。排程器看門狗刻意用自己的一份,不靠這支
- 專案這邊的做法(值放哪、程式怎麼讀、不默默降級、開源前撤銷進過歷史的)寫在 `templates/agent-instructions.md`「憑證」段,
  每個專案的規則檔都有

## 安全性質(不要繞過)

- 任何專案的 session 結束 / 對話壓縮前的 hook 只 scope 到 `memory/`/
  `STATE.md`——不要改成 `git add -A`,會把使用者還沒準備好的真實工作
  意外 commit 掉
- hub 那台永遠不當任何專案主分支的 origin,只透過獨立的 `.agent-memory`
  worktree 處理 memory 追蹤——這個安全性質不該因為「這個專案剛好還沒有
  外部 remote」而放寬

## 子 session

agent-hub 的 session 開的子 session 做完、確認過結果之後就封存它,不要留著閒置。各專案的同一條規則在
`templates/agent-instructions.md`(會放進每個專案的規則檔)。

## 誰負責 commit/push agent-hub

- **commit 閘門**(`git config core.hooksPath githooks`,`agenthub init` 會設;新 clone 也要):改到程式碼(`bin/` `tests/`
  `contracts/` `templates/`…)的 commit,`bin/test-gate` 拿**暫存區**(不是 working tree)先跑 `bin/lint`(ruff,只開 F 規則:
  沒定義的名字、沒用到的 import/變數;規則在 `ruff.toml`,ruff 由 `bin/setup-python` 裝進平台的 venv),再用平台的 Python、
  假的 HOME 跑全部測試(`bin/run-tests`:一個模組一個程序、平行跑,英文 golden 也是),都過、而且沒有測試寫進 HOME 才放行。只動資料檔的腳本 commit 不跑。
  `--no-verify` 跳過的會被 post-commit 記在 `.state/test-gate-bypassed.log`。重構前後外面看得到的行為用黃金檔固定
  (例:`tests/fixtures/llm-golden.json`),不要靠手動比對
- **commit 標題用 Conventional Commits**(`<type>[(<scope>)][!]: <說明>`,type:feat / fix / perf / refactor / test / docs / build /
  ci / chore / revert;scope 是區塊名,例 `fix(hubbus): …`;破壞相容的加 `!` 並在內文寫 `BREAKING CHANGE:`)。`githooks/commit-msg`
  擋不合格的標題(`bin/commit_title.py`);release notes 用 `cliff.toml`(git-cliff,跟助理的 repo 同一份)從 commit 產生,不另外維護
  CHANGELOG。其他專案:登錄寫 `commits: conventional` 就由 `merge-branch` 合併前檢查、規則檔自動帶說明
- **手動編輯(腳本、文件)**:只由在 hub 那台的 hub checkout 開的專用 session 做,它負責 commit+push。其他專案的 session 發現
  agent-hub 的問題,只通知(`agenthub send --to agent-hub`,或告訴使用者),不直接改——兩個 session 同時改同一份 working tree,
  commit 會互相夾帶對方改到一半的檔案。共用 working tree 時一律 `git commit -- <路徑>`,不要 `git add -A`
- **腳本寫的被版控檔案**(config repo 的 `projects/<name>.yaml`(onboard/rename/manifest.update)、`mirror-list.txt`、`schedule.yaml`、
  `hub.json`(schedulectl set-model)):寫檔的腳本自己用 `bin/commit-hub-files.sh` 只 commit 那幾個檔案,所以
  `onboard-project.sh` 從哪個 session 跑都安全;data 目錄的東西不進 git
