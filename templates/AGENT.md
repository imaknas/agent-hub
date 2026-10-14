# Agent 行為規則

> 這套 memory 追蹤機制的入口說明在 `~/agent-hub/CLAUDE.md`(MBP 跟 mini 都有;基礎設施層級:
> 目錄慣例、機器分工、排程/整合 agent 都從那裡找)。專案本身的設計文件看專案自己的
> README/docs,不在這裡。

## 同步紀律

- 這個專案的 git remote 是 mac mini 上的 bare repo,同步靠 `.claude/settings.local.json`(本機、不進版控)裡的
  hook 自動處理:SessionStart 跑 `git pull`;SessionEnd,以及長 session 中途觸發
  compact 時(PreCompact),跑 `git-sync-memory.sh`——只 add `memory/` 跟
  `STATE.md`,不會 `git add -A`,commit 後 push。不需要手動操作。
- 不要手動 commit/push——除非確定 hook 沒被觸發(例如不是在 Claude Code 環境下工作)。
  另外 MBP 上每小時有一次 `sync-all-memory.sh` 補同步,session 沒正常結束也不會卡住。
- 這份 AGENT.md 由 agent-hub 的 template 自動覆寫(每次開 session 對齊),不要手改。

## 記憶紀律

- **每次在專案裡 commit 或 push 之後**,append 到 `memory/YYYY-MM-DD.md`:做了什麼、
  決定跟理由、踩到的坑。沒有 commit 的長討論在得出結論時也記。寫原始記錄就好,不用
  花力氣摘要——摘要跟跨專案關聯是「圖書館員」整合 agent 的工作,不是這裡的責任。
- 筆記比專案最新 commit 舊的時候,git commit/push 之後跟 session 開始(含對話壓縮後)
  會自動提醒;看到提醒就先補,再繼續手上的事。
- 檔名一律 `memory/YYYY-MM-DD.md`(當天日期),同一天的東西都 append 進同一份。
  唯一例外:一次性的大量匯入/回填(例如從別的系統搬過來的整批紀錄),另開
  `memory/YYYY-MM-DD-<主題>.md`,日期用匯入當天,主題用小寫加連字號。
- 整理過的主題文件(不是流水帳,會被改寫成最新、最準的版本)放
  `memory/reference/<主題>.md`,並維護 `memory/reference/README.md` 當索引。
  除了 `reference/`,`memory/` 底下不開其他子目錄,也不放 `.md` 以外的檔案。
- 日期檔是流水紀錄,只往後加、不回頭改;內容整理進 `reference/` 之後,原本的
  日期檔可以 `git rm`(歷史還在)。
- `RULES`(如果有):這個專案啟用的額外規則名稱,一行一個,內容在 agent-hub 的
  `templates/rules/`,開 session 時會放進 CLAUDE.local.md。由 agent-hub 管理,不要手改。
- 不要自己改 `STATE.md`——那是整合 agent 維護的精煉現況,只在有實質變化時才會被改寫。
