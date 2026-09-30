# CLAUDE.md

## agent-hub memory 追蹤

這個專案有跨專案 memory 追蹤機制(細節見 `.agent-memory/AGENT.md`)。

**每次在這個專案 commit 或 push 之後,先在 `.agent-memory/memory/YYYY-MM-DD.md`
(今天的日期)append 幾行再繼續**:做了什麼、做了什麼決定跟為什麼、踩到什麼坑。
沒有 commit 的長時間討論,在得出結論的時候也記一筆。寫原始記錄就好,不用摘要。
這是工作流程的固定一步,不是「有空再寫」——對話被壓縮後也照做。筆記落後時,
commit/push 之後跟每次 session 開始(含壓縮後)會自動提醒。不會影響這個專案
本身的程式碼或 git 歷史。

## 跨專案改動

這個 session 只 commit/push 這個專案。發現別的專案(包括 `~/agent-hub`)需要
改,不要直接動那邊的檔案——傳訊息給負責那個專案的 session,或告訴使用者。

這段說明放在 gitignored 的 CLAUDE.local.md,不在共享的 CLAUDE.md:不要把
agent-hub、`.agent-memory` 的名稱或路徑寫進任何會 commit 的檔案(CLAUDE.md、
`.claude/settings.json`、`.gitignore`、README、程式註解、commit 訊息等),公開 repo 會跟著外洩。
