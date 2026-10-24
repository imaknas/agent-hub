# CLAUDE.md

## 這是什麼

agent-hub 是跨專案的 memory 追蹤 + 排程 + 專案/服務註冊系統,設計見
`project-29/ROADMAP.md`(只在 MBP)。這份檔案是給任何在這裡工作的 session
一個起點索引——細節都寫在各腳本自己的檔頭註解裡,不在這裡重複。

**2026-10-15 起所有專案、agent-hub 本身的 session 都在 mac mini 上**(專案在
`~/workspace/<name>`,agent-hub 在 `~/agent-hub`)。MBP 只當遙控端:用 Claude Desktop 透過
SSH 開 mini 上的資料夾,MBP 斷線/沒電 session 也繼續跑。MBP 上的舊資料夾只是備份。

## 寫新程式的規則(第一階段,2026-10-22 起)

- **路徑、名稱、owner 一律從 `bin/hubconf.py` 拿**(設定在 `hub.json`;hub 根目錄由腳本位置推出,
  `AGENT_HUB_ROOT` 可覆蓋)。不要再寫死 `~/agent-hub`、`~/services`、專案名稱、`knas`。
- 協定裡的識別字用英文(hubbus 的 owner 地址是 `owner`,`knas` 是別名);使用者身分(〔Knas〕標籤)從設定讀。
- 不要把新的個人資料放進程式碼或測試。

## 先查這裡,不要自己猜

- **想知道某個專案現在狀態、綁定哪些服務、STATE.md 寫什麼**:
  `bin/overview.sh [專案名稱]`(不帶參數列出全部)
- **有哪些專案被 onboard 過**:專案登錄 `projects/<name>.yaml`(`paths.workspace` 是 mini 上的位置;
  `bin/hubconf.py roster` 一個專案一行列出名稱/路徑/origin/memory bare repo,shell 腳本用這個讀);
  以前在 MBP 的位置、改名前的路徑在 `legacy-paths.tsv`,只給統計舊對話用
- **mini 上有哪些背景服務在跑**:`services.tsv`
- **有哪些 GitHub repo 被 mirror 備份**:`mirror-list.txt`
- **跨專案還有哪些事沒做(誰能做)**:`todo.md`(librarian 每晚重生)
- **這週在各專案做了什麼、作息、跟 AI 協作的方式**:`reflect/index.html`(所有週的分析報告頁,
  資料在 `reflect/YYYY-Www.json`;每週一產生,Claude Code 版的 Reflect)
- **這幾個月(工作與職涯)怎麼走過來**:`reflect/trajectory/index.html`(`bin/trajectory.py`
  產生,偶爾重跑;需要 OpenClaw 日誌目錄當參數)
- **OpenClaw 搬遷到哪了、還剩什麼沒處理**:`openclaw-migration-status.md`
- **自動化/排程/看門狗出怪錯,或要設計新的自動化**:先查 `known-issues.md`
  (具體的坑 + 從 OpenClaw 時期學到的運作原則)

## 建立/救援

- **開一個新專案,或把既有資料夾接上這套系統**:
  `bin/onboard-project.sh [--public] [--no-remote] ~/workspace/<name>`(在 mini 上跑)——全自動(會自
  動 git init、補第一個 commit、建 GitHub repo),細節看檔頭註解跟
  `.claude/skills/new-or-onboard-project/SKILL.md`
- **新機器,或某個專案本機資料夾不見了**:`bin/recover-project.sh <name>`
  ——**絕對不要**對已經 onboard 過的專案重跑 `onboard-project.sh`,會建出
  一份全新、跟舊歷史斷開的 memory,不是救回來。細節看
  `.claude/skills/recover-projects/SKILL.md`
- **專案改名**(GitHub repo 已經改好之後):`bin/rename-project.sh <old> <new> [--path <新路徑>]`
  ——登記、mini 的 memory bare repo / ~/.memory-copies 副本 / checkpoint / mirror、本機資料夾
  一次改完,memory 歷史保留。先 dry-run,再加 `--apply`
- **把專案的開發環境從 MBP 搬到 mini**(2026-10-15 全部搬完,之後只在還有東西留在 MBP 時用):
  `bin/migrate-to-mini.py <name>`,在 MBP 上跑——前置檢查(沒有未 commit/未 push、沒有 session 開著)、
  recover 到 mini 的 `~/workspace/<name>`、複製不進版控的狀態檔、auto memory、專案 MCP 設定、
  `.git/info/exclude`、重建依賴。先 dry-run,再加 `--apply`;MBP 的資料夾不動
- **onboard/rename 這類要動到 mini 的腳本**:在 mini 上跑時直接在本機做,在 MBP 上跑才走 ssh
  (共用判斷在 `bin/minilib.sh`)
- **某個背景服務(launchd)需要在新機器重建**:`bin/recover-service.sh <label>`
  ——只有 `services.tsv` 裡 owner 是 agent-hub 的項目能全自動重建,其他
  (OpenClaw、Homebrew、GitHub Actions 自己管的)只會印出重建步驟,不會
  用猜的去動別人的東西

## 專案信箱 / 事件匯流排

- `bin/hubbus`——跨專案(跟 Knas)的信,收件人是**專案**(或 `knas` = 送進 Muninn),不是 session;
  只 append 的事件 log(`.state/bus.db`,不進版控、沒有備份),信箱是它的投影。各專案的 session
  用 `hubbus send/inbox/done`(寫在 `templates/CLAUDE.md`);信由 SessionStart(`sync-local-setup.sh`、
  `worktree-memory.sh`)、UserPromptSubmit/PostToolUse hook 送進 session;`hubbus-bridge`(schedule.yaml,
  每 15 分鐘)負責 Muninn 的轉交進信箱、給 knas 的信跟轉交的 done 送進 Muninn、12 小時沒人收就在那個專案
  跑 headless agent(`agent-jobs/mailbox.md`,只寫筆記/回信/問 knas,不改程式碼;轉交不升級)。
  Muninn 的轉交由 librarian 每晚從信箱讀(`hubbus open-handoffs`)直接套用進 STATE.md,結果可信才
  `hubbus done`(bridge 再回報 Muninn 結案);session 白天看到只當參考,Knas 的回答由發問的 session
  處理、沒處理的也是晚上套用。介面跟失敗模式看 `bin/hubbus` 檔頭
- `bin/knas-tasks.py`——〔Knas〕待辦的 Muninn 任務登錄(librarian 每晚、gen-todo 之前呼叫,零 LLM):
  還沒 key 的〔Knas〕行(各 STATE.md、openclaw-migration-status.md)寄成 `task`、蓋上「(等 Knas:<key>)」
  (key 固定 = 專案 + 文字的 hash,重寄是 no-op);Muninn 回來的帶 key 轉交(done/dropped/already_done/
  reopened)照 key 刪掉或加回那一行,push 成功才結案;寄過的 key 從 STATE.md 消失(librarian 判定做完、整行刪掉)就寄
  推斷的 `done`(附 review-log 的理由)給 Muninn 讓 Knas 確認,現在有很像的行(改寫)就只警告

## 通道租借(Muninn 借對話給專案 session)

- `bin/lease-runner.py`(服務 `local.agent-hub.lease-runner`,127.0.0.1:18797,token `.secrets/runner-token`)——
  Muninn 開租約、轉送訊息、SSE 收事件、收回;每個租約一個 `llm.py exec lease-session` 的長駐 `claude -p`
  (唯讀工具、`--settings` 帶第二階段 deny 清單、`HUBBUS_LEASE=1`)。閒置 10 分鐘停程序、30 分鐘問一次
  (Muninn 回報 warned 後 10 分鐘收回)。收回時 `lease-handover` 判斷點寫結構化交接 → 匯流排、專案信箱、給 owner 的
  task/question/progress、memory 筆記。API 跟事件:`contracts/lease-runner.v1.schema.json`、交接:
  `contracts/lease-handover.v1.schema.json`;設計在檔頭
- `bin/lease-relay.py`——租約 session 的權限轉接(MCP):提問跟不在登錄網域的 WebFetch 送 Muninn(手機),其他拒絕;
  `hub.json` 的 `lease.no_session_domains` 不准「整個租約都允許」
- `bin/lease-resume <id>`——在終端機接續租約(帶 headless token;SSH 底下讀不到 keychain)

## 這些給 hook/其他腳本內部呼叫,不要手動直接跑

- `bin/git-safe-push.sh`——push 遇到分岔自動 pull 重試;真的內容衝突才
  會失敗讓人看,不會用任何策略幫你硬猜要留哪一邊
- `bin/git-sync-memory.sh`——SessionEnd/PreCompact hook 用,**只** commit
  `memory/` 跟 `STATE.md`,不會動到專案的真實程式碼。任何專案的這個 hook
  都不該改成 `git add -A`——agent-hub 自己的 hook 2026-09-22 才因為這個
  問題被修過,見 git log
- `bin/with-lock.sh`——避免多個 session 同時操作同一個 git repo 互撞
- `bin/gen-index-roster.sh`——從專案登錄機械重生
  `index.md` 的專案名冊區塊(`roster:begin/end` 之間),librarian 每次跑、
  `onboard-project.sh` 登記時都會呼叫。名冊不交給 LLM 判斷,LLM 只寫區塊外
  的註記
- `bin/sync-local-setup.sh`——把專案的本機專用設定(`CLAUDE.local.md` 裡
  `agent-hub:begin/end` 區塊、`.agent-memory/AGENT.md`(會 commit+push 這一個檔)、`.claude/settings.local.json` hook、兩者的
  ignore)對齊目前的 `templates/`。每個專案的 SessionStart hook 自動跑,
  onboard/recover 也呼叫——**改了 `templates/CLAUDE.md` 不用手動去各專案
  補**,下次開 session 就生效。只動不進版控的檔案,ignore 補在
  `.git/info/exclude`,不改 `.gitignore`
- `bin/memory-nudge.py`——memory 筆記比專案最新 commit 舊時提醒 agent 補寫:git commit/push
  之後(PostToolUse hook)跟 session 開始、含對話壓縮後(sync-local-setup.sh 呼叫)。
  筆記沒落後就完全不輸出
- `bin/worktree-memory.sh`——git worktree 裡的 session(例:agent 用 worktree 模式叫出來的
  `<專案>/.claude/worktrees/…`)沒有 `.agent-memory`、`CLAUDE.local.md`、專案 hook,這支用 symlink
  接上主 checkout 的那兩份並處理同步/提醒。掛在**使用者層級** settings(mini 的 `~/.claude/settings.json`、
  MBP 的 `~/.claude-personal/settings.json`),用 `merge-hook-settings.py --worktree <檔案>` 安裝;
  新機器也要裝一次。在主 checkout 裡什麼都不做
- `bin/merge-hook-settings.py`——合併(不覆寫)一個專案 `.claude/
  settings.local.json` 的 hook 設定,`onboard-project.sh` / `recover-project.sh`
  自己會呼叫,不需要手動跑。hook 刻意不寫進共享的 `settings.json`:指令含
  `~/agent-hub` 路徑,會把個人工具名稱/路徑 commit 進 repo(公開 repo 就外洩)

## 排程 / 夜間自動化(跑在 mini 上,transcript-backup 除外)

- `bin/scheduler.py` + `schedule.yaml` + `bin/schedulectl`——每 15 分鐘
  觸發的排程器;`schedulectl` 是查看/調整排程的 CLI(例如
  `schedulectl models` 查目前用的模型、`schedulectl run <name>` 現在就跑一次)。
  **所有排程只放這裡**,包括需要 Claude 判斷的(不用 Claude Desktop 的「Scheduled」:那只在
  MBP 開著、醒著時跑):command 寫 `bin/run-agent-job.sh <name> [工作目錄]`,prompt 放
  `agent-jobs/<name>.md`,在背景跑 `claude -p`,報告與狀態在 `.state/agent-jobs/<name>/`
- **長 job 在 `schedule.yaml` 加 `background: true`**:scheduler 一個一個依序跑 job,幾分鐘以上的
  job(librarian、project-16、muninn-reflect、project-36 研究)不加就會讓 muninn-tick、上傳
  全部等它。背景 job 跑完的結果下一個 tick 才併回狀態,細節在 `scheduler.py` 檔頭。背景 job 之間有先後(動同一份東西)就加 `after: [job]`,不要只靠時間錯開
- **排程器看門狗** `bin/scheduler-watchdog.py`(`local.agent-hub-scheduler-watchdog`,每 5 分鐘)——
  排程器超過 35 分鐘沒 tick 就發 Discord。刻意不靠排程器/Homebrew/muninn;Discord 設定在
  `.secrets/`(不進版控),沒設定狀態頁會標紅
- **排程狀態頁**:https://hub-host.<tailnet>.ts.net/(只有 tailnet 打得開)——
  `bin/gen-status-page.py` 每個 tick 重生:哪些 job 失敗、哪些報告要處理(🔔)、下次什麼時候跑、
  Claude job 的報告全文
- `bin/librarian.sh`——「圖書館員」,讀各專案 `memory/` 的新增內容,判斷
  要不要更新 `STATE.md`,並產生跨專案 `digest/YYYY-MM-DD.md` + 更新
  `index.md`。模型設定在 `models.env`
- `bin/consolidate-memory.py`——「回頭清理」,每晚 02:00(librarian 之前):超過 14 天的 memory 日期檔
  整理進 `reference/` 再 git rm、修正被推翻的敘述(保留「預計/可能」語氣,有機械檢查);一晚最多 3 個
  專案、每個專案最多一週一次,模型 `CONSOLIDATE_MODEL`(opus)。`--list` 看狀態、`--dry-run --project <name>`
  只產生提議的 diff。改動記在 `review-log.md`,細節看檔頭註解
- `bin/gen-todo.py`——librarian 每晚呼叫:收集各專案 STATE.md 的 `## 待辦` 段落(固定格式
  `- [ ] 〔Knas|agent|待定〕…`,由 librarian 維護)跟 `openclaw-migration-status.md` 的待辦,
  機械產生 `todo.md`,不用 LLM;格式不對的專案列在檔尾
- `bin/weekly-reflect.sh`(→ `weekly-reflect.py` + `reflect_llm.py`,頁面樣板 `templates/reflect.html`)
  ——每週一 07:00:上一週的跨專案活動分析。統計(各專案訊息/session/commit/筆記、時段、深夜比例、最長連續工作、交辦方式)零 LLM;
  「主題與 AI 協作觀察(4D)」一週一次模型呼叫,**只送統計數字,對話原文不送雲端**(2026-10-21 起,
  跟 muninn 同一條規則;之後會加上 muninn 本機模型寫的 session 摘要)
- `bin/hublib.py`——共用判斷:真人訊息(`origin.kind == human`)、transcript ↔ 專案對應、哪些
  commit 算專案工作。check-memory-gaps / memory-nudge / weekly-reflect 都用它
- `bin/backup.py`——**加密雲端備份**(restic → Backblaze B2 bucket `<backup-bucket>`;設定在 `hub.json` 的
  `backup`)。平台 job `agent-hub/backup` 每晚 05:45:SQLite 用 backup API 複製、`~/git-origin` 的 bare repo 打 bundle、
  各 workspace 沒 push/沒 commit 的東西跟 `.env`、加上 muninn data / `.state` / `.secrets` / 服務資料 / transcript;
  週日套保留(14 天、8 週、12 月)。`agent-hub/backup-verify` 每月 1 號還原到暫存目錄驗證。密碼在
  `.secrets/backup-password`(**另一份在 Knas 的密碼管理器**),B2 金鑰在 `.secrets/b2-env`。還原:
  `bin/backup.py restore <目的地> [--include <路徑>]`。工作 profile 的 transcript 刻意不備份
- `bin/mirror-backup.sh`——讀 `mirror-list.txt`,對每個 GitHub repo 維護
  一份唯讀 `--mirror` clone,純粹災難復原用,跟 librarian 的摘要流程完全
  獨立、零 LLM 成本
- `bin/transcript-backup.sh`——MBP → mini 的 Claude Code chat transcript
  每小時備份(這支跑在 MBP,不是 mini)
- `bin/check-memory-gaps.py`——librarian 每晚呼叫:最近 72 小時有對話、memory 卻沒有新筆記
  的專案,列進 `todo.md` 固定區塊跟 digest(零 LLM 成本)
- `bin/sync-all-memory.sh`——每小時對所有專案的 `.agent-memory` 補跑
  `git-sync-memory.sh` + 補 push(hook 的安全網;跑在 mini,`local.sync-all-memory-mini`。MBP 上
  那份 `local.sync-all-memory` 還在,但專案都不在 MBP 了,實際上不做事)

## mini 上的路徑慣例

四個看起來像但用途完全不同、不要混用:

- `~/git-origin/<name>-memory.git`——bare repo,只有 `agent-memory` 分支
  (`onboard-project.sh` 建的)
- `~/.memory-copies/<name>/`——**memory-only** 的副本,給 librarian 用(隱藏目錄,不是給人
  開 session 的;2026-10-15 前叫 `~/projects`,跟 `~/workspace` 太像而改名)
  (只有 AGENT.md/STATE.md/memory/,不是專案的真實程式碼;`onboard-
  project.sh` 步驟 9 clone 出來的)
- `~/services/<name>/`——**真的程式碼**的完整 git clone,給需要在 mini 上
  長駐執行 process 的專案用(例:project-15 的 webhook receiver)。
  跟 `~/.memory-copies/` 的差別:這裡是真的會被 `node`/`python` 之類的指令
  執行、需要 `.env`/`data/` 之類本機專屬狀態的東西,不是 librarian 讀
  memory 用的。**每個 `~/services/` 專案都要有自己的 `bin/deploy.sh`**,在 mini 的
  `~/workspace/<name>`(或 MBP)執行、一個指令完成部署(git pull + 裝依賴/rebuild + 需要的話 `launchctl
  kickstart` + 健康檢查),例:project-15、project-16。不是 rsync、
  不是 `onboard-project.sh`,也不是手動 ssh 進去 pull
- `~/workspace/<name>/`——**所有專案的開發副本**(含 `.agent-memory` worktree;就是
  登錄的 `paths.workspace`),給 Claude Desktop 透過 SSH 在 mini 上開 session 用
  (MBP 只當遙控端;2026-10-14 project-16 試行,10-15 全部搬完)。新專案用 `onboard-project.sh`
  建在這裡,救援用 `recover-project.sh`;memory remote 直接指向本機 bare repo。不要在這裡跑服務
  (那是 `~/services/`),也不要拿 `~/.memory-copies/` 當開發副本。MBP 上的舊資料夾是備份,
  `.git` 裡有標記,在那裡開 session 會被提醒改來 mini

新專案如果需要在 mini 上跑一個長駐服務,才用 `~/services/`;純粹要有
memory 追蹤但不需要在 mini 上執行任何東西的專案(目前大多數專案都是這種),
不需要在 `~/services/` 底下有任何東西。

## 目錄

- `bin/`——所有腳本
- `launchd/`——平台自己的 launchd plist(排程器、看門狗、狀態頁、memory 同步)。**專案的長駐服務不在這裡**:
  寫在 `projects/<專案>.yaml` 的 `services:`,`bin/render-services.py`(平台 job,每 15 分鐘)產生
  `~/Library/LaunchAgents/<label>.plist`,有變才重載
- **平行 session 的合併閘門** `bin/merge-branch`(在 session 自己的 worktree 跑):rebase → 登錄的
  `runtime.test` → fast-forward main(主 checkout)→ 只有登錄 `push_on_merge: true` 才 push;匯流排記
  `merge.done`/`merge.failed`/`session.report`。狀態頁列各專案的 worktree、兩個 branch 改同一檔、
  沒經過閘門直接 commit 到 main 的數量(只是資訊)。`bin/prune-worktrees [--yes]` 清已合併、乾淨的
  worktree——**由人跑**,閒置但沒封存的 session 可能還開在裡面
- **第二階段強制**:登錄 `enforce: {sandbox: true}` 的專案,`bin/render-project-settings.py`(平台 job,每 15
  分鐘)把 sandbox(只准寫自己的專案/runtime/匯流排/cache、讀不到別的專案、網路白名單)跟 deny 規則寫進它的
  `.claude/settings.local.json`;被改就寫回、狀態頁紅;`enforce` 關掉就拿掉。`--report` 看每個專案會寫什麼。
  所有 `claude -p` 經過 `bin/llm.py`(判斷點宣告在 `contracts/llm-points.yaml`,每晚 lint);無人值守的
  agent job 在登錄宣告 `allow:` 就用 dontAsk
- **mini 的使用者層級設定**(`~/.claude/settings.json`,2026-10-22 Knas 在 Schedule 強化 session 直接確認後套用,
  備份 `.state/claude-settings.json.bak-20261022-u1u4`):U1 deny `Edit(~/workspace/**/.claude/settings.local.json)`
  (session 改不了平台產生的設定)、U2 `sandbox.allowUnsandboxedCommands: false`、U3
  `permissions.disableBypassPermissionsMode: "disable"`(要 bypass 會從 default 開始)、U4 worktree 的
  UserPromptSubmit 信箱 hook。**使用者層級的設定只有 Knas 在那個 session 直接確認才改**,轉述的同意不算
- `contracts/`——**版本契約**:agent-hub 定義的介面(hubbus、todo-md、state-todo、reflect-week、project)
  跟 vendor 的別人的契約(`contracts/vendor/<專案>/`)。登錄的 `provides`/`consumes` 宣告誰定義、誰讀;
  `tests/test_contracts.py`(`python3 -m unittest discover -s tests`)驗證範例,`bin/contracts-check.py`
  (每晚 01:30)比對副本跟原版、驗證實際資料。加欄位不升版;改名/改意思升版、兩版並存到讀的人換完
- `projects/`——**專案登錄**(一個專案一個 `<name>.yaml`;目錄由 `hub.json` 的 `registry_dir` 指定):
  路徑、job、service、隱私標籤、測試指令。驗證跟格式看 `bin/registry.py` 檔頭;`registry.py check` 驗證。
  排程器跑的 = `schedule.yaml`(切換期間的舊項目)+ 登錄的 job(`<專案>/<job>`)
- `templates/`——骨架檔案(`AGENT.md`、`CLAUDE.md`);hook 設定不在這裡,
  寫在 `bin/merge-hook-settings.py`。`templates/rules/<名稱>.md` 是可選的專案規則,
  專案在 `.agent-memory/RULES` 登記名稱才會放進它的 CLAUDE.local.md。目前所有專案
  都登記了 `engineering`(軟體工程規則),onboard 新專案時也會自動登記
- `.claude/skills/`——`new-or-onboard-project`、`recover-projects`
- `digest/`、`index.md`(名冊區塊由 `gen-index-roster.sh` 產生)、`review-log.md`、`todo.md`——`librarian.sh` 的產出,不要
  手動改,改了下次跑會被蓋掉或造成混淆

## 安全性質(不要繞過)

- 任何專案的 SessionEnd/PreCompact hook 只 scope 到 `memory/`/
  `STATE.md`——不要改成 `git add -A`,會把使用者還沒準備好的真實工作
  意外 commit 掉
- mini 永遠不當任何專案主分支的 origin,只透過獨立的 `.agent-memory`
  worktree 處理 memory 追蹤——這個安全性質不該因為「這個專案剛好還沒有
  外部 remote」而放寬

## 誰負責 commit/push agent-hub

- **手動編輯(腳本、文件、services.tsv 等)**:只由工作目錄在 mini 的 `~/agent-hub`
  的專用 session 做(2026-10-15 前在 MBP 的 `~/agent-hub`;MBP 那份現在只 pull,不在那裡改),它負責 commit+push。其他專案的 session 發現 agent-hub
  的問題,只通知(`hubbus send --to agent-hub`,或告訴使用者),不直接改——2026-09-25
  兩個 session 同時改這份 working tree,commit 互相夾帶對方改到一半的檔案
- **腳本寫的被版控檔案**(`projects/<name>.yaml`(onboard/rename/manifest.update)、`mirror-list.txt`、
  `index.md`、`todo.md`、`reflect/`、`schedule.yaml`、`models.env`、`digest/`、`review-log.md`):
  寫檔的腳本自己用 `bin/commit-hub-files.sh` 只 commit 那幾個檔案,所以
  `onboard-project.sh` 從哪個 session 跑都安全
