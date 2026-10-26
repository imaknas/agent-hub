# 規範、開源、文章:三條線一起推

> agent-hub session · 2026-10-27 · Knas 10-27 決定一起推進。決定:第一份規範 = 人與 agent 的任務交接;授權 Apache-2.0;
> 規範跟文章英文為主、中文另寫;規範放獨立的新 repo(先只在本機,公開要 Knas 確認)。

## 為什麼

大家都在造自己的 agent 輪子;能留下來的是協定,不是實作。但只有文件、沒有實作的規範很少被採用(MCP 是規格 + SDK +
範例 server 一起出來的)。所以:**從 agent-hub 已經在跑、有版本、有測試的契約裡,抽出現有標準沒有涵蓋的那幾塊**,寫成
跟實作無關的規範,agent-hub 當參考實作,再讓第二個實作也能用。

**先對齊、不重造**:工具跟資料 → MCP;給 agent 的專案說明 → AGENTS.md(兩個都在 Linux Foundation 的 Agentic AI
Foundation);agent 之間委派任務 → A2A;agent 記憶的格式 → 有 W3C 社群小組在做(重點是記憶單元、加密、刪除)。這些層
agent-hub 只當使用者。

## 三條線

| 線 | 產出 | 先決條件 | 要 Knas 做的 |
|---|---|---|---|
| 規範 | `agent-ops-specs` repo:第一份 Human Task Handoff(HTH)v0.1——模型、Markdown 綁定、JSON 訊息綁定、一致性測試資料、參考解析器;agent-hub 跑得過一致性測試 | 無 | 公開時建 GitHub repo |
| 開源 | agent-hub 公開 | new-user 第 6 步收尾(git rm 舊檔)→ 個人名稱 ratchet 歸零(現在 313)→ 第 7 步小心地重寫歷史 → 換 AGENTS.md(對齊標準)→ LICENSE、README、CONTRIBUTING | force push、把 repo 改成 public |
| 文章 | 英文長文 + 中文版:①一個人管十幾個 AI 寫程式專案的營運層(記憶、信箱、權限、排程);②人與 agent 的任務交接為什麼需要協定(帶出 HTH);③把 bash 平台改成 config as code 跟隔離演練的經驗 | 規範 v0.1 有草稿(文章②)、開源完成(文章①要能附連結) | 選發表的地方、最後審稿 |

進度(10-27):HTH 0.1 草稿、一致性測試資料、參考解析器在 `~/workspace/agent-ops-specs`(1200eb8,只在本機)。
agent-hub 跑 zh-hant 那份(`tests/test_hth_conformance.py`,c19e644):第一次跑就抓到兩個已上線的偏差——任務登記
不認全形括號跟「等」後面沒空格的 key 標記、gen-todo 在不認得的 `### 〔…〕` 底下把行歸給上一個群組(標籤不認得的行默默
漏掉)。都修了,現有資料的 key 沒變。這是文章②的好例子:規範先寫清楚,一致性測試就能抓到實作自己沒發現的洞。

順序:規範草稿跟文章大綱現在就做(不碰別人的東西、不對外);開源等明天的收尾;文章②在規範 v0.1 之後、文章①在開源之後發。

## 規範候選(之後幾份)

1. **Human Task Handoff**(第一份):agent 怎麼標出「只有人能做」的事、怎麼登記成穩定的 key、人那邊結案/取消/重開怎麼
   回來、agent 推斷做完了怎麼讓人確認。來源:`contracts/state-todo.v1.md`、`bin/knas-tasks.py`、hubbus 的 task/done/drop。
2. **Project State Document**:現況檔的結構跟更新規則(現況、待辦、引用要指到存在的檔)。
3. **Session Lease & Handover**:從另一個介面借用正在跑的專案 agent、結束時結構化交接(`contracts/lease-runner.v2`、`lease-handover.v1`)。
4. **Agent Project Manifest**:專案登錄(job、服務、隱私、權限、憑證宣告)。

## 跟既有協定比較(10-27,agent-ops-specs b657b33 `RELATED-WORK.md`)

Knas 問「AI 圈沒有這種協定嗎」。讀原文比過 A2A 1.0、MCP 2026-07-28(Tasks 擴充、elicitation)、AG-UI 1.0、CHAP 0.3.3、
iCalendar VTODO/iTIP、JSCalendar/JMAP Tasks、TASKS.md、各家 agent SDK 的 human-in-the-loop:
- **都把要人的那一步放在一個還在跑的單位裡**(一次呼叫、一個 agent 伺服器的 task、一個 run),完成與否由建立它的系統決定。
  HTH 管的是「只有 owner 能做、比任何 run 都活得久、由 owner 那邊決定結案」的事。三份獨立的研究結論一樣。
- **但這塊很薄**:傳輸層大概八成 A2A 就有(伺服器端的長 task、ListTasks、至少一次的 webhook、URI 擴充)。HTH 真正自己的:
  owner 那邊才算數(agent 的 done 只是宣稱)、同一個 key 重開、刪掉的行只是「推斷做完、要 owner 確認」、從內容算出的 key、
  Markdown 綁定。所以定位是「搭在 A2A 上、借 JSCalendar 字彙的一層薄薄的 owner 權威語意 + Markdown 綁定」,不是又一個 agent 協定。
  文章②用這個角度寫。
- **最接近的 CHAP**(Brightbeam,2026-06 論文,Apache-2.0):組織用的多人工作區、要 coordinator 伺服器、任何成員都能完成任務;
  沒有 repo 裡的文字形式、沒有推斷完成。重疊只在「指派給人的 task」。
- 待 0.2 決定(SPEC §12):A2A 綁定要哪一種(每個專案一個長 task 帶 HTH 事件,或一個 HTH task 一個 A2A task——後者表達不了
  reopened)、`dropped` 對 CANCELED 還是 REJECTED、狀態名對齊 JSCalendar、AAIF 審批閘門的「沉默不算結案」寫成規則。

## 互通的證明

規範 v0.1 之後:做第二個實作,兩個都跑過同一組一致性測試資料,才去社群(AAIF 等)提。比較之後,第二個實作最便宜也最有說服力的
是 **owner 端的轉接**:A2A 的 owner 端(HTH 當 A2A 擴充),或把 CalDAV/JMAP 任務清單當 owner 端(owner 原本的待辦 app 就能收)。
比「另一個 agent 工具讀寫同一份格式」更能證明它是搭在既有標準上。
