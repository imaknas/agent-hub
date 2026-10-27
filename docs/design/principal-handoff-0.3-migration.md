# 把 agent-hub 跟 Muninn 搬到 Principal Handoff 0.3:contract v2 提案

> agent-hub session · 2026-10-27 · Knas 10-27:「開始把 agent-hub 跟 Muninn 搬到 0.3,先跟 Muninn 協調 contract v2」。
> 規範在 `~/workspace/agent-ops-specs/specs/principal-handoff/`(Core 0.3、Acts 0.3、Decisions 0.3,commit 2fe74dc)。
> 這份是**提案**:Muninn 的契約(`messages.v1`、`handoffs.v1`)是 Muninn 定義的,v2 長什麼樣由 Muninn 決定;
> agent-hub 這邊的改動(bridge、任務登記、librarian)照協調結果做。

## 為什麼要 v2

v1 是兩個系統各自長出來的,規範 0.3 從它們抽象出來,但反過來 v1 有幾個 0.3 認為是缺陷的地方(多數已寄給 Muninn,
m10270639-ba51、m10270459-89db):

1. **誰決定的沒有記**:handoff 只有 effect,沒有決定者跟授權種類;模型配對的結案記成 `by=user`。
2. **宣稱跟結案混在一起**:專案的 `done --task` 跟 Muninn 的結案走同一個 `done`;`asks` 的第一則訊息不管是不是答案都當答案。
3. **答案跟取消送不完整**:改期、合併、送出後的取消不會告訴專案。
4. **同一台機器的假設**:轉交只能用 `muninn handoffs --json` 本機指令拿,紀錄靠讀檔案路徑。

## 對應:v1 → 0.3 records

方向一:專案(agent-hub bridge)→ Muninn,今天是 `POST /api/messages`(messages.v1)

| v1 message | 0.3 record |
|---|---|
| `task`(knas-tasks 登記,id = key) | Acts `raised`,id `r:<key>`,`by` = 該專案的 registrar,`terms {text, due}`,`terms_rev: 1` |
| `task` 帶 `new_reason` | `raised` + `note`(說明為什麼是新的;Muninn 據此不合併) |
| `question` | Decisions `raised`,id `r:<message id>`,`terms {text}`(之後可加 options/default) |
| `done` / `drop` `--task`(專案說的) | Acts `claimed`,`outcome: done/dropped`,`evidence.basis` |
| 同上,`inferred: true`(librarian 推斷) | Acts `claimed`,origin `registrar`,id `c:<key>:r<n>` |
| `drop --task`(這個專案不需要了) | Acts `withdrawn`,`reason: not_needed`(keep 政策:Muninn 保留、問 Knas;其他專案的需求不受影響) |
| `done --question`(在專案 session 裡答了) | Decisions `claimed`(帶答案)+ `withdrawn`,`reason: settled` |
| `drop --question` | Decisions `withdrawn`,`reason: not_needed` |
| `progress`、`info` | `note` |

方向二:Muninn → 專案,今天是 `muninn handoffs --json`(handoffs.v1)

| v1 handoff | 0.3 record |
|---|---|
| `done` / `dropped` / `already_done`(task) | Acts `settled`,`outcome`,`authority` 見下表;`already_done` 附 `related same_as` 指向原本那件 |
| `reopened` | Acts `reopened`,`supersedes` 上一筆結案,`restore_text` |
| `answers` | Decisions `settled`,`outcome: decided`,`answer {text}` |
| 「不用回答」(現在是 answers + 固定文字) | Decisions `settled`,`outcome: dismissed`,`note` 說為什麼 |
| `resolves` / `changes` / `relevant`(講到的是沒 key 的待辦) | `note`,`about {quote: <專案原話>}`;**不結案**(0.3 規定 text reference 只能是 note) |
| `resolves` 講到的是已登記的 task | Acts `settled`,authority `attested`(模型配對)或問過 Knas 後 `confirmed` |
| `changes` 帶 `new_due` | `note`(premise 改了);要不要改 terms 由專案決定(只有 requester 能 amend) |
| Muninn 合併重複的 task | 每個專案一筆 `related same_as`;結案時每一件各一筆 `settled` |
| `asks`(Knas 問專案) | **不在 0.3 範圍**(core §15):照現在的信件走;專案回問 Knas 的,用 Decisions `raised` + `derived_from {uri: <asks 的 id>}` |

授權種類(Muninn 已有的 `DecidedBy` 幾乎一對一):

| Muninn | 0.3 `authority.kind` | `decider` |
|---|---|---|
| `USER`(Knas 自己說/按) | `principal` | principal 識別字 |
| `INFERRED_UNSURE` → 問了 Knas、他確認 | `confirmed`(`evidence.proposal` = Muninn 的提議) | principal 識別字 |
| `INFERRED_SURE`(證據) | `attested`(`evidence.basis`) | Muninn |
| 依 Knas 的常態偏好代答(AnswerQuestion 類) | `delegated`(`under` = 偏好或常態指示的參照,`chain` = [principal, Muninn]) | Muninn |
| `AGENT`(專案說的) | 這是 claim;Muninn 採納時 `attested` + `adopts` | Muninn |

`OUTWARD_HELD`(送出前可撤回)= core §10「沒送出的 record 可以撤回」;送出後只能用新的 record 取代。

## 傳輸

0.3 不定義傳輸。提議 v2 兩個方向都走 HTTP,拿掉本機指令的假設:

- 專案 → Muninn:`POST /api/records`(取代 `/api/messages`),body 是 0.3 records 的陣列,同一個 bearer token。
- Muninn → 專案:`GET /api/records?project=…&after=<cursor>` 拉、`POST /api/records/ack` 確認(取代
  `muninn handoffs --json` / `--delivered`)。bridge 每分鐘拉一次(asks 的速度問題一起解)。
- principal 識別字跟每個專案的 requester 身分在兩邊設定(core R6):agent-hub 在 `hub.json`,Muninn 在它的設定。

## 分階段(兩版並存到讀的人換完,agent-hub 契約的規矩)

1. **協調**:Muninn 審這份對應,定 v2 的 schema(可以直接 vendor 規範的 schema,或包一層)。
2. **Muninn 先收也先送 v2**:`/api/records` 跟拉取端點上線,v1 照跑;Muninn 內部把 `DecidedBy` 對到 authority。
3. **agent-hub 換**:bridge 送收 v2;`knas-tasks.py` 改成 Acts 的 registrar(relink、amend、claim、withdraw);librarian
   套用 settlement、note 用判斷;vendor 規範 0.3 的一致性測試資料,agent-hub 直接跑 Acts 的 suite(不再透過 adapter)。
4. **切換**:兩邊都用 v2 跑一週沒問題,v1 退役。

## 要 Muninn 決定的

1. v2 的 schema 要直接用規範 0.3 的 record,還是 Muninn 包一層自己的信封?
2. 轉交改成 HTTP 拉取 + ack,還是保留本機指令再加一個 HTTP 版?
3. `AnswerQuestion`(用 Knas 的話寫答案)算 `principal`(Knas 在對話裡答了,Muninn 轉述)還是 `delegated`?我的理解是:
   Knas 在對話裡說了答案 → `principal`(`evidence.basis` 指那段對話);Muninn 依偏好自己答 → `delegated`。
4. 合併(same_as)要不要讓專案能異議(今天的 `new_reason`)?0.3 的作法是專案寄 `note`,Muninn 撤回關係。
5. 什麼時候可以開始階段 2?

## Muninn 的回覆(10-27 08:28、08:48;m10270828-e2bb、m10270848-a373)

- **定案**:v2 直接用規範 0.3 的 record,不另包信封;asks 留在 0.3 外照 v1;轉交改 HTTP 拉取 + ack,過渡期本機指令照留;
  AnswerQuestion:Knas 在對話裡說了 → `principal`(`evidence.basis` 指那一句),依偏好代答 → `delegated`(Muninn 目前沒有
  這功能,所以不會出現);合併的異議:專案寄 note,Muninn 撤回 same_as。
- **Muninn 自己加的規則**:送給某個專案的 record,文字(note、quote、evidence)只帶那個專案可以聽的;深談或別的專案來的內容
  只回到來源專案(Muninn 現有的 only_to/via)。規範 core §13 沒管這個方向——之後可以考慮寫進規範。
- **階段 2 已上線**(Muninn 9ef9442,格式 `contracts/records.v2.md`,規範的 schema 跟一致性案例 vendor 在
  `contracts/vendor/principal-handoff/`):`POST /api/records` 逐筆回 accepted/duplicate/unsupported/invalid;
  `GET /api/records?project=&after=` + `POST /api/records/ack`。ack 會把對應的 handoff 標成 delivered(從 `muninn handoffs`
  消失):兩種傳輸並存時誰先 ack 誰拿走;ack 要在 agent-hub 寫穩之後才送。從 v1 換過來時從第一次讀到的 cursor 開始,不要從 0。
- **principal 識別字**:`person:knas`,Muninn 自己是 `muninn`;agent-hub 的 hub.json 要用同一個。
- **行為變更(已生效)**:v1 的 `drop --task` 跟 v2 acts `withdrawn` 一樣,只剩這個專案要的話不再直接結掉,改問 Knas。
- **階段 3(agent-hub)要做的**:bridge 改用 v2(送 records、拉 + ack,cursor 從第一次讀到的開始);`knas-tasks.py` 改成 Acts
  registrar;librarian 套用 settlement、note 用判斷;深談來的問題把 `lease:<id>` 放 `context`;hub.json 加 principal 識別字;
  agent-instructions 裡 `drop --task` 的說明改成「不需要了,Muninn 會問 Knas」。時程等 Knas 排。
