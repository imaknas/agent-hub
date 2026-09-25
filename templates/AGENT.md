# Agent 行為規則

> 對應設計文件見本專案的 `ROADMAP.md`,以及跨專案的
> [project-29/ROADMAP.md](/Users/you/project-29/ROADMAP.md)(基礎設施層級,
> 目錄慣例、機器分工、排程/整合 agent 的設計都在那裡)。

## 同步紀律

- 這個專案的 git remote 是 mac mini 上的 bare repo,同步靠 `.claude/settings.local.json`(本機、不進版控)裡的
  hook 自動處理:SessionStart 跑 `git pull`;SessionEnd,以及長 session 中途觸發
  compact 時(PreCompact),跑 `git-sync-memory.sh`——只 add `memory/` 跟
  `STATE.md`,不會 `git add -A`,commit 後 push。不需要手動操作。
- 不要手動 commit/push——除非確定 hook 沒被觸發(例如不是在 Claude Code 環境下工作)。

## 記憶紀律

- 工作中有值得留下的內容,直接 append 到 `memory/YYYY-MM-DD.md`,寫原始記錄就好,
  不用花力氣摘要——摘要跟跨專案關聯是「圖書館員」整合 agent 的工作,不是這裡的責任。
- 不要自己改 `STATE.md`——那是整合 agent 維護的精煉現況,只在有實質變化時才會被改寫。
