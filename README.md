# agent-hub

給一個人用的、跨專案的 AI agent 平台:每個專案有自己的 memory(agent 寫的筆記 + 一份精煉的 `STATE.md`),
平台每晚整理它們、產生跨專案的 digest 跟待辦清單,並負責排程、背景服務、專案之間的信箱。
agent 目前是 [Claude Code](https://claude.com/claude-code);平台只依賴自己定義的介面
(`docs/design/runtime-abstraction.md`),Claude Code 是其中一個實作。

> 這份 README 是給**人**看的入口。在這個 repo 裡工作的 agent 讀 `CLAUDE.md`。
> 平台正在往「全新使用者也能直接用」的方向整理(`docs/design/new-user.md`)。所有指令都從 `bin/hub --help` 開始找。

## 需要什麼

- macOS(排程跟背景服務用 launchd)
- `git`、`jq`、[`uv`](https://docs.astral.sh/uv/)(建平台的 Python)
- Claude Code CLI(`claude`),登入過
- 選配:`gh`(onboard 時自動建 GitHub repo)、`restic`(雲端備份)、Discord bot(警報)

## 從零開始

```bash
git clone <這個 repo> ~/agent-hub && cd ~/agent-hub
bin/setup                         # 檢查需要的指令、建平台的 Python(3.13 + PyYAML),每台機器一次
bin/hub init                      # 問你的名字、時區、專案放哪、開哪些模組 → 寫 hub.json、裝排程器跟看門狗
bin/hub doctor                    # 一次看完還缺什麼(登入、排程器、服務…),每一項都說怎麼修
bin/hub onboard ~/workspace/my-app             # 把一個專案接上來(--no-remote:不建 GitHub repo)
```

隔天早上就有 `digest/<日期>.md` 跟 `todo.md`。可以不開的功能(個人助理、雲端備份、mirror、每週回顧…)看 `bin/hub modules`。

## 概念

| | 是什麼 | 在哪 |
|---|---|---|
| 專案登錄 | 一個專案一個 YAML:路徑、排程的 job、背景服務、隱私、測試指令 | `projects/<name>.yaml`(`bin/registry.py check` 驗證) |
| memory | 每個專案的 `.agent-memory/`:`memory/` 底下的筆記(agent 寫)+ `STATE.md`(現況) | 各專案的 workspace;bare repo 在 `git_origin` |
| 夜間整理 | consolidate-memory(舊筆記整理進 reference/)→ librarian(更新 STATE.md、寫 digest)→ todo、memory-gaps → librarian-publish | `projects/agent-hub.yaml` 的 `jobs:` |
| 排程器 | 每 15 分鐘一個 tick,跑到期的 job;`after:` 排順序 | `bin/scheduler.py`、`bin/schedulectl list` |
| 信箱 | 專案之間(跟你)的信,收件人是專案不是 session | `bin/hubbus` |
| 狀態頁 | 哪些 job 失敗、哪些報告要看、下次什麼時候跑 | `bin/gen-status-page.py` |

## 常用

```bash
bin/overview.sh [專案]            # 專案現況、綁定的服務、STATE.md
bin/hub config plan               # 改了 config(hub.json、登錄…)之後會改什麼;bin/hub config apply 套用
bin/hub modules                   # 可以不開的功能;bin/hub modules enable <名稱>
bin/schedulectl list              # 所有 job
bin/schedulectl run <job>         # 現在就跑一次
bin/recover-project.sh <name>     # 新機器 / 專案資料夾不見了(不要對已經 onboard 的專案重跑 onboard)
bin/rename-project.sh <old> <new> # 改名(先 dry-run,再加 --apply)
bin/py -m unittest discover -s tests
```

## 文件

- `docs/design/`:各部分的設計(新使用者、執行環境抽象、信箱分派、能力、想法沙盒)
- `contracts/`:平台定義的介面(JSON Schema + 範例),`tests/test_contracts.py` 驗證
- `known-issues.md`:踩過的坑跟自動化的運作原則
