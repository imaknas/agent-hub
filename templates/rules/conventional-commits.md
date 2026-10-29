## Commit 標題:Conventional Commits

這個專案的登錄寫了 `commits: conventional`:commit 標題用

    <type>[(<scope>)][!]: <說明>

- type:feat(新功能)、fix(修錯)、perf、refactor、test、docs、build、ci、chore、revert
- scope 選填,寫這個 repo 裡的區塊名;說明寫這個 commit 讓什麼變得不一樣
- 破壞相容的在冒號前加 `!`,內文寫 `BREAKING CHANGE: …`(發版時升 major)
- `agenthub merge-branch` 合併前會檢查這個 branch 上每個 commit 的標題,不合格就不合併(跟測試失敗一樣):改好標題再跑
- repo 裡有 `cliff.toml` 的話,release notes 由 git-cliff 從 commit 產生(feat、fix、perf、revert 會列出來)
