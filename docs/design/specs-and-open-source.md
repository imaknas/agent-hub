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
| 規範 | `agent-ops-specs` repo:**Principal Handoff**(10-27 由 HTH 0.1 改名拆開)——Core 0.2 + Tasks 0.2 + Questions 0.1,各有 schema、一致性測試資料、參考實作;agent-hub 跑得過 Tasks(0.1 版)的一致性測試 | 無 | 公開時建 GitHub repo |
| 開源 | agent-hub 公開 | new-user 第 6 步收尾(git rm 舊檔)→ 個人名稱 ratchet 歸零(現在 313)→ 第 7 步小心地重寫歷史 → 換 AGENTS.md(對齊標準)→ LICENSE、README、CONTRIBUTING | force push、把 repo 改成 public |
| 文章 | 英文長文 + 中文版:①一個人管十幾個 AI 寫程式專案的營運層(記憶、信箱、權限、排程);②只有你能處理的事為什麼需要協定(帶出 Principal Handoff);~~③config as code 跟隔離演練~~(10-27 Knas:沒什麼特別,併進①當段落) | 規範 v0.1 有草稿(文章②)、開源完成(文章①要能附連結) | 選發表的地方、最後審稿 |

進度(10-27):HTH 0.1 草稿、一致性測試資料、參考解析器在 `~/workspace/agent-ops-specs`(1200eb8,只在本機)。
agent-hub 跑 zh-hant 那份(`tests/test_hth_conformance.py`,c19e644):第一次跑就抓到兩個已上線的偏差——任務登記
不認全形括號跟「等」後面沒空格的 key 標記、gen-todo 在不認得的 `### 〔…〕` 底下把行歸給上一個群組(標籤不認得的行默默
漏掉)。都修了,現有資料的 key 沒變。這是文章②的好例子:規範先寫清楚,一致性測試就能抓到實作自己沒發現的洞。

順序:規範草稿跟文章大綱現在就做(不碰別人的東西、不對外);開源等明天的收尾;文章②在規範 v0.1 之後、文章①在開源之後發。

## 規範候選(之後幾份)

1. **Principal Handoff**(第一份,原 Human Task Handoff):agent 怎麼標出「只有人能做」的事、怎麼登記成穩定的 key、人那邊結案/取消/重開怎麼
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

## 方向收斂(10-27,三輪盲審)

Knas 的出發點:Muninn 是 OpenClaw 一類的個人 AI agent(市面上很多競品),agent-hub 的一部分功能會被包進 agent 方案裡;
目標是把兩邊拆開,讓別的 agent-hub-like 平台能接 Muninn、未來的 agent 框架能採用這套協定接上 agent-hub-like 平台。三輪盲審
(第三方、看不到彼此跟我的結論)的結果:
- **N×M 是真的,但「個人 agent ↔ coding agent」這條已經被 ACP + MCP 填了**(驗證過:OpenClaw 用 ACP 開 Claude Code/Codex/
  Gemini 的 session、`openclaw mcp serve` 把對話跟權限問答給 coding agent)。所以不另做一個「agent ↔ 平台」大協定,而是
  一個**組合現有標準的 profile**:借專案 session → **ACP**(lease runner 的方法幾乎一對一);讀狀態、權限問答 → **MCP**;
  只有你能處理的事 → **Principal Handoff**(我們自己的那塊)。
- **Principal Handoff 收斂成**:Core(信封、id、送達、「只有 principal 那邊能結案、宣稱不算結案、沉默不算結案」)+ 兩個擴充:
  **Tasks**(付款、簽約這類事,寫在 STATE.md,key 從文字算)跟 **Questions**(要你回答或決定的事,用訊息,key 由發問方給、
  可以修正答案、預設值只是暫定、記錄是你本人還是助理回答的)。task 跟 question 本質上不同的地方:識別、誰決定還需不需要、
  修正的意思、答案要送到專案。「principal」是領域行話(代理關係裡被代理的那方),因為收件的不一定是人本人(可能是助理)。
- **兩輪都同意**:解耦在程式碼裡大多已經做了;剩下的是 Muninn 的轉交只能用本機指令拿(`muninn handoffs --json`)、
  紀錄靠讀檔案路徑——要改成 HTTP。採用靠「裝得起來的東西」(MCP server、OpenClaw plugin),不是規範文件;外面有人實作一邊
  之前不寫更多規範。

## 互通的證明

~~之前寫「第二個實作最便宜的是 CalDAV/JMAP 任務清單當 owner 端」~~:第一輪盲審指出那是讓 agent-hub 不用 Muninn,跟 Knas
的目標相反。改成兩邊各證明一次:**別的平台接 Muninn**(只看規範做一個最小平台,卡住的地方就是缺口),跟**別的 agent 框架接
agent-hub**(lease runner 講 ACP,看 OpenClaw 能不能直接借 agent-hub 的 session)。實驗依序:① lease runner 變 ACP agent
② Muninn 的轉交加 HTTP 列出 + 確認 ③ Muninn 的 local-projects 對齊 Tasks 的 `en` 格式(現在是 TODO.md + 標題關鍵字,
跟規範不一致)④ principal 端做成 MCP server / OpenClaw plugin。都過了才去社群(AAIF Workflows 工作組)提。

## Principal Handoff 0.3(10-27,agent-ops-specs c8727ed + 2fe74dc)

五輪盲審後從根本重做,不是打補丁:每個 item 兩個有主人的登記(需求歸發問方:raised/amended/withdrawn;結案歸
principal 端:settled/reopened)、宣稱(claim)跟附註(note)不改任何登記、每個結案記決定者跟授權種類(本人決定/
本人確認提議/在授權範圍內代決/依證據認定)、principal = 設定好的根(自然人或法人,不能是軟體 agent、也不能是參與者;
中間的 agent 都是 delegate)。Acts(原 Tasks,保留 Markdown 綁定)跟 Decisions(原 Questions)在上面。範圍外寫明:
授權書格式、群組內部決策程序、principal 問專案(asks,用 derived_from 連起來)、傳輸。最後一次查前例:沒有人做了整套;
最接近的是 OpenID AuthZEN 的審批 profile、IETF WIMSE AIMS、singla 的 principal 定義,已寫進 RELATED-WORK 跟對齊方式。
冷審查出 17 個缺陷已修(2fe74dc),參考實作 16 個測試過、跟到達順序無關。

**下一步:實作收斂。** agent-hub 現在跑的是 HTH 0.1(透過 adapter),Muninn 也還沒照 0.3。要讓規範跟實作一起收斂,
agent-hub 的任務登記/匯流排跟 Muninn 的 contract(v2)要改成 0.3 的 records——跨專案,要跟 Muninn 協調。

## 公開(10-27)

Knas 10-27:「先把 agent-ops-specs 做成公開的 GitHub repo(加 Pages),先檢查過再開」。檢查:追蹤的檔案、main 的 8 個 commit
每個版本跟 commit 訊息(名字、本機路徑、常見憑證格式都沒命中;gitleaks 沒裝,用 pattern 掃)、commit email 跟已公開的
sycophancy-eval / the-crucible 一樣。只推 main 跟 tag,agent-memory 分支留在 mini。

- repo:https://github.com/imaknas/agent-ops-specs(public),tag `principal-handoff-0.3`
- 網站:https://imaknas.github.io/agent-ops-specs/(GitHub Pages,main 的根目錄;相對連結轉成 .html,schema 跟一致性
  資料用原始 JSON 給)
- agent-hub 登錄的 origin 改成 GitHub、加進 mirror-list(config f943f7f)

