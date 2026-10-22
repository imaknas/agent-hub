<!-- contract: managed-files v1 -->
# 平台會寫進專案的檔案(managed files)

平台(agent-hub)只會動下面這些;其他檔案都是專案自己的。新增一項要先改這份清單。

| 檔案 | 誰寫 | 內容 |
|---|---|---|
| `CLAUDE.local.md` 的 `<!-- agent-hub:begin -->`…`<!-- agent-hub:end -->` 區塊 | `sync-local-setup.sh`(每次 SessionStart) | `templates/CLAUDE.md` + 登記的 `templates/rules/*.md`;區塊外不動 |
| `.claude/settings.local.json` 的 hook | `merge-hook-settings.py`(只補不刪) | memory 同步、memory 提醒、專案信箱 |
| `.git/info/exclude` 的幾行 | `sync-local-setup.sh` | `.agent-memory/`、`CLAUDE.local.md`、`.claude/settings.local.json` |
| `.agent-memory/AGENT.md`(memory 分支) | `sync-local-setup.sh`(會 commit+push 這一個檔) | `templates/AGENT.md` |
| `.agent-memory/STATE.md`(memory 分支) | librarian(模型)、`knas-tasks.py`(機械) | 精煉的現況、`## 待辦`(契約 state-todo) |
| worktree 裡的 `.agent-memory`、`CLAUDE.local.md` symlink | `worktree-memory.sh` | 指向主 checkout |
| `~/Library/LaunchAgents/<label>.plist`(不在 repo) | `render-services.py` | 專案登錄的 services |
| `.agent-memory/memory/<日期>.md`(append 一段) | 通道租借的 runner(`bin/lease-runner.py`,上線後) | 租借對話的交接筆記(`## 租借對話 HH:MM(session <id>)`,附寄出的任務 key) |
| `.claude/settings.local.json` 的 `sandbox` 跟平台加的 `permissions.deny/allow` 幾條 | `render-project-settings.py`(登錄 `enforce.sandbox`) | 第二階段的 sandbox 與跨專案 deny 規則;偏移會寫回 |
| `.git/hooks/pre-commit`、`pre-merge-commit`(有平台標記的) | `render-project-settings.py`(登錄 `gate: strict`) | 合併閘門強制 |
