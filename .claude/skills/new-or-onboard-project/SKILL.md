---
name: new-or-onboard-project
description: Create a brand-new project or bring an existing folder into the agent-hub pipeline, end to end. Use whenever the user asks to "start/create a new project" or "onboard this folder/project" — handles location, scaffolding, and visibility decisions, then delegates everything mechanical to onboard-project.sh in one call.
---

# New or Onboard Project

`bin/onboard-project.sh` 現在會自動處理 git init、第一個 commit、GitHub
repo 建立——一次呼叫做完全套。這份 skill 只負責它做不到、需要判斷的部分。
不要把已經機械化的事再手動做一次(不用自己先 `git init`、先手動
`gh repo create`——腳本會做)。

## 判斷 1:這是全新專案,還是要 onboard 既有資料夾?

- **全新專案**(使用者說「幫我建一個新專案」):目錄通常還不存在或是空的。
  跳到判斷 2、3、4,決定好之後直接呼叫腳本,腳本自己會建目錄。
- **onboard 既有資料夾**(使用者說「幫我 onboard 這個」或指了一個已經有
  程式碼的路徑):跳過判斷 3(語言鷹架,東西已經在那裡了),直接看判斷 4,
  然後呼叫腳本。如果那個資料夾已經是 git repo 且 working tree 是髒的,
  腳本會直接 fail 並印出原因——**不要試著幫使用者先清乾淨再重跑**,把
  這個訊息原封不動轉告使用者,讓他自己決定要 commit 還是丟棄那些變動。
  這是刻意的安全機制,保護使用者真實的、還沒準備好的工作。

## 判斷 2:專案要放在哪個路徑?

2026-10-15 起所有專案都在 **mini 的 `~/workspace/<name>`**(MBP 只當遙控端,用 Claude Desktop
透過 SSH 在 mini 上開 session)。這支 skill 跟 `onboard-project.sh` 都在 mini 上跑:

- **預設**:`~/workspace/<name>`,不分語言、不分是不是程式碼專案。
- 不要放 `~/.memory-copies/`(librarian 的 memory 副本)或 `~/services/`(正式服務的部署副本)。
- 使用者自己指定路徑的話,以使用者說的為準,不要覆蓋——但要提醒:不在 `~/workspace/` 底下的話,
  `sync-all-memory.sh` 的掃描跟一些慣例(資料夾名稱 = 專案名稱)會對不上。

## 判斷 3:全新專案需要語言鷹架嗎?

如果使用者講了語言/框架(「幫我建一個 Rust 專案」「用 Python」),先在
目錄裡跑對應的初始化指令(`cargo init`、`uv init`、`npm init` 等),生出
真的初始檔案,**再**呼叫 `onboard-project.sh`——這樣腳本自動 commit 的
第一個 commit 就是真的鷹架,不是只有一份空白 README.md 佔位。

如果使用者沒講語言、只是要一個地方記東西(像 project-31 那種筆記型
專案),不用鷹架,讓腳本自己補的 README.md 佔位就好。

## 判斷 4:GitHub repo 要 public 還是 private?

**預設一律 private**——這也是腳本自己的預設值,不用加任何 flag。

只有使用者明確說這是要公開的作品集/開源專案(像 adaptive-iteration、
project-10 那種情況)才加 `--public`。不確定的話,問使用者,不要自己猜
「這個看起來可以公開」。

如果使用者明確說這個專案暫時不需要 GitHub(罕見,今天已經把所有 native
專案都轉成 hosted 了,因為 native = 沒有本機以外的備份),才加
`--no-remote`,並且要跟使用者確認他知道這個取捨。

## 執行

決定完判斷 2-4,一行指令做完全套:

```bash
~/agent-hub/bin/onboard-project.sh [--public] [--no-remote] <path>
```

腳本本身處理 mini 連不上的情況(整個不做,不留半套狀態)跟中途被打斷的
resume——這兩個不需要 skill 介入,腳本自己會印清楚訊息。腳本跑完直接把
輸出(包含 GitHub URL、有沒有裝 --public)貼給使用者看,不用另外摘要。
