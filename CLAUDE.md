# CLAUDE.md

## 這是什麼

agent-hub 是跨專案的 memory 追蹤 + 排程 + 專案/服務註冊系統,設計見
`project-29/ROADMAP.md`。這份檔案是給任何在這裡工作的 session
一個起點索引——細節都寫在各腳本自己的檔頭註解裡,不在這裡重複。

## 先查這裡,不要自己猜

- **想知道某個專案現在狀態、綁定哪些服務、STATE.md 寫什麼**:
  `bin/overview.sh [專案名稱]`(不帶參數列出全部)
- **有哪些專案被 onboard 過**:`onboarded-projects.tsv`
- **mini 上有哪些背景服務在跑**:`services.tsv`
- **有哪些 GitHub repo 被 mirror 備份**:`mirror-list.txt`
- **OpenClaw 搬遷到哪了、還剩什麼沒處理**:`openclaw-migration-status.md`

## 建立/救援

- **開一個新專案,或把既有資料夾接上這套系統**:
  `bin/onboard-project.sh [--public] [--no-remote] <路徑>`——全自動(會自
  動 git init、補第一個 commit、建 GitHub repo),細節看檔頭註解跟
  `.claude/skills/new-or-onboard-project/SKILL.md`
- **新機器,或某個專案本機資料夾不見了**:`bin/recover-project.sh <name>`
  ——**絕對不要**對已經 onboard 過的專案重跑 `onboard-project.sh`,會建出
  一份全新、跟舊歷史斷開的 memory,不是救回來。細節看
  `.claude/skills/recover-projects/SKILL.md`
- **某個背景服務(launchd)需要在新機器重建**:`bin/recover-service.sh <label>`
  ——只有 `services.tsv` 裡 owner 是 agent-hub 的項目能全自動重建,其他
  (OpenClaw、Homebrew、GitHub Actions 自己管的)只會印出重建步驟,不會
  用猜的去動別人的東西

## 這些給 hook/其他腳本內部呼叫,不要手動直接跑

- `bin/git-safe-push.sh`——push 遇到分岔自動 pull 重試;真的內容衝突才
  會失敗讓人看,不會用任何策略幫你硬猜要留哪一邊
- `bin/git-sync-memory.sh`——SessionEnd/PreCompact hook 用,**只** commit
  `memory/` 跟 `STATE.md`,不會動到專案的真實程式碼。任何專案的這個 hook
  都不該改成 `git add -A`——agent-hub 自己的 hook 2026-09-22 才因為這個
  問題被修過,見 git log
- `bin/with-lock.sh`——避免多個 session 同時操作同一個 git repo 互撞
- `bin/gen-index-roster.sh`——從 `onboarded-projects.tsv` 機械重生
  `index.md` 的專案名冊區塊(`roster:begin/end` 之間),librarian 每次跑、
  `onboard-project.sh` 登記時都會呼叫。名冊不交給 LLM 判斷,LLM 只寫區塊外
  的註記
- `bin/merge-hook-settings.py`——合併(不覆寫)一個專案 `.claude/
  settings.local.json` 的 hook 設定,`onboard-project.sh` / `recover-project.sh`
  自己會呼叫,不需要手動跑。hook 刻意不寫進共享的 `settings.json`:指令含
  `~/agent-hub` 路徑,會把個人工具名稱/路徑 commit 進 repo(公開 repo 就外洩)

## 排程 / 夜間自動化(跑在 mini 上,transcript-backup 除外)

- `bin/scheduler.py` + `schedule.yaml` + `bin/schedulectl`——每 15 分鐘
  觸發的排程器;`schedulectl` 是查看/調整排程的 CLI(例如
  `schedulectl models` 查目前用的模型)
- `bin/librarian.sh`——「圖書館員」,讀各專案 `memory/` 的新增內容,判斷
  要不要更新 `STATE.md`,並產生跨專案 `digest/YYYY-MM-DD.md` + 更新
  `index.md`。模型設定在 `models.env`
- `bin/mirror-backup.sh`——讀 `mirror-list.txt`,對每個 GitHub repo 維護
  一份唯讀 `--mirror` clone,純粹災難復原用,跟 librarian 的摘要流程完全
  獨立、零 LLM 成本
- `bin/transcript-backup.sh`——MBP → mini 的 Claude Code chat transcript
  每日備份(這支跑在 MBP,不是 mini)

## mini 上的路徑慣例

三個看起來像但用途完全不同、不要混用:

- `~/git-origin/<name>-memory.git`——bare repo,只有 `agent-memory` 分支
  (`onboard-project.sh` 建的)
- `~/projects/<name>/`——**memory-only** 的工作副本,給 librarian 用
  (只有 AGENT.md/STATE.md/memory/,不是專案的真實程式碼;`onboard-
  project.sh` 步驟 9 clone 出來的)
- `~/services/<name>/`——**真的程式碼**的完整 git clone,給需要在 mini 上
  長駐執行 process 的專案用(例:project-15 的 webhook receiver)。
  跟 `~/projects/` 的差別:這裡是真的會被 `node`/`python` 之類的指令
  執行、需要 `.env`/`data/` 之類本機專屬狀態的東西,不是 librarian 讀
  memory 用的。更新靠該專案自己的 `bin/deploy.sh`(git pull + rebuild +
  `launchctl kickstart`),不是 rsync、不是 `onboard-project.sh`

新專案如果需要在 mini 上跑一個長駐服務,才用 `~/services/`;純粹要有
memory 追蹤但不需要在 mini 上執行任何東西的專案(目前大多數專案都是這種),
不需要在 `~/services/` 底下有任何東西。

## 目錄

- `bin/`——所有腳本
- `launchd/`——版控過的 launchd plist,對照 `services.tsv` 裡 owner 是
  agent-hub 的項目
- `templates/`——`onboard-project.sh` 用的骨架檔案(`AGENT.md`、
  `CLAUDE.md`、`claude-settings.json`)
- `.claude/skills/`——`new-or-onboard-project`、`recover-projects`
- `digest/`、`index.md`(名冊區塊由 `gen-index-roster.sh` 產生)、`review-log.md`——`librarian.sh` 的產出,不要
  手動改,改了下次跑會被蓋掉或造成混淆

## 安全性質(不要繞過)

- 任何專案的 SessionEnd/PreCompact hook 只 scope 到 `memory/`/
  `STATE.md`——不要改成 `git add -A`,會把使用者還沒準備好的真實工作
  意外 commit 掉
- mini 永遠不當任何專案主分支的 origin,只透過獨立的 `.agent-memory`
  worktree 處理 memory 追蹤——這個安全性質不該因為「這個專案剛好還沒有
  外部 remote」而放寬
