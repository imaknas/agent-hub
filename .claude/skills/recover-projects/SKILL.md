---
name: recover-projects
description: Restore agent-hub-onboarded projects on a new machine, or after a local project folder is lost/deleted. Use when setting up a fresh machine that needs some or all of these projects, or when a specific onboarded project's local directory is missing. Reads the project registry (projects/*.yaml) and walks through what to restore.
---

# Recover Projects

這份 skill 處理「新機器」或「本機資料夾遺失」的救援情境。跟 onboard 一個
全新專案不一樣,不要用 `onboard-project.sh` 做這件事——細節見
`bin/recover-project.sh` 開頭的註解。這裡負責的是純機械化腳本做不了的
判斷部分。

## 前提

新機器要先能連上 hub 那台(hub.json 的 `hosts.server`,SSH alias)跟 GitHub(SSH key),
這兩個不通,後面全部會失敗。先確認:

```bash
ssh <hub 那台的 ssh 名稱> echo ok
ssh -T git@github.com
```

## 步驟

1. 確認 `~/agent-hub` 本身存在且是最新的(`git pull`)。如果這是全新機器,
   先手動 clone:`git clone <hub 那台的 ssh 名稱>:~/git-origin/agent-hub.git ~/agent-hub`,再建平台的 Python
   (`~/agent-hub/bin/setup-python`,要先有 uv)、裝 commit 閘門(`git -C ~/agent-hub config core.hooksPath githooks`)。
   沒有平台的 Python,所有平台腳本(含每個專案的 hook)都會失敗。

2. 跑 `~/agent-hub/bin/py ~/agent-hub/bin/hubconf.py roster`(專案登錄 `projects/*.yaml`,每個專案一行:名稱、
   路徑、origin、memory bare repo),對每一列檢查路徑(第二欄)在這台
   機器上存不存在。已存在的跳過,不需要處理。登錄的路徑是 mini 上的
   `~/workspace/<name>`(hub 那台上);救到別的位置
   (例:另一台機器的使用者名稱不同)就給 `recover-project.sh` 第二個參數。
   以前在 MBP 上的位置記在 `legacy-paths.tsv`,只給統計舊對話用,不是還原目標。

3. **對每個不存在的專案,先問使用者,不要自己假設全部都要救回來**:
   - 這台機器是要完全取代舊機器(通常全部都要救),還是額外的第二台
     機器(可能只需要其中幾個正在用的專案)?
   - 有沒有哪些專案其實已經不需要了,不用救?

   不要一次靜默跑完 15 個專案的 restore——這是判斷題,不是機械化步驟。

4. 對使用者確認要救的每個專案,檢查 `origin` 欄位:
   - **不是 `(native)`**:直接跑 `bin/recover-project.sh <name>`(需要覆蓋
     路徑就加第二個參數)。腳本本身是冪等、機械化的,不需要額外判斷。
   - **是 `(native)`**:停下來告訴使用者——這個專案的真實程式碼除了原本
     那台機器的磁碟以外沒有任何備份。`recover-project.sh` 會直接 fail 並
     印出這個事實。真的要救,只能靠使用者自己找到原本機器的磁碟或其他
     備份管道(Time Machine 等,不在 agent-hub 的管轄範圍內),不要嘗試
     用 `memory_bare_repo` 的內容去猜或重建程式碼——那份只有 memory 筆記,
     不是原始碼。

5. 全部跑完後,提醒使用者:
   - 這是這個系列裡第一次出現「兩台機器都在用同一個專案」的情況(舊機器
     沒退役的話)——`.agent-memory` 的 push 靠 `git-safe-push.sh` 自動處理
     分岔,通常沒事,但如果兩邊真的同時在同一個 session 工作,還是可能
     撞。
   - mini 上的 scheduler/librarian 完全不需要因為換機器而調整——那些只
     認 mini 上的 bare repo 跟 `~/.memory-copies/`,不知道也不在乎 interactive
     dev 是在哪台機器上做的。
