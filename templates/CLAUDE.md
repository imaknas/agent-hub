# CLAUDE.md

## agent-hub memory 追蹤

這個專案有跨專案 memory 追蹤機制(細節見 `.agent-memory/AGENT.md`)。工作中如果
有值得記錄的內容(重要決定、踩到的坑、進度),append 到
`.agent-memory/memory/YYYY-MM-DD.md`——寫原始記錄就好,不用花力氣摘要。不會
影響這個專案本身的程式碼或 git 歷史。

## 跨專案改動

這個 session 只 commit/push 這個專案。發現別的專案(包括 `~/agent-hub`)需要
改,不要直接動那邊的檔案——傳訊息給負責那個專案的 session,或告訴使用者。

這段說明放在 gitignored 的 CLAUDE.local.md,不在共享的 CLAUDE.md:不要把
agent-hub、`.agent-memory` 的名稱或路徑寫進任何會 commit 的檔案(CLAUDE.md、
`.claude/settings.json`、README 等),公開 repo 會跟著外洩。
