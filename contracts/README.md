# contracts/

模組之間的介面,一個介面一份、帶版本。**給別的 repo 讀的契約(會被 vendor 過去)只寫格式、意思、版本,
不寫這邊是哪支程式、哪個模型產生的**——那些寫在這份 README;給別人的檔案也不提這個平台的名字
(有開源的 repo,vendor 的副本會被 commit 進去)。

| 契約 | 誰產生(這邊的實作) | 誰讀 |
|---|---|---|
| `todo-md.v1.md` | `bin/gen-todo.py`(librarian 每晚,零 LLM);librarian 再補 memory 缺口區塊 | Knas、owner 的個人助理 |
| `state-todo.v1.md` | librarian(模型改寫)+ `bin/check-state-todo.py`(格式檢查)+ `bin/knas-tasks.py`(機械蓋 key、照 key 刪/加回) | `bin/gen-todo.py`、個人助理 |
| `reflect-week.v1.schema.json` | `bin/weekly-reflect.py`(每週一 07:00) | 個人助理的週反思 |
| `hubbus.v1.schema.json` | `bin/hubbus` | 平台自己(session、bridge、librarian、狀態頁) |
| `llm-points.v1.schema.json` | `contracts/llm-points.yaml`(人改;`bin/llm.py` 照它組旗標,contracts-check 每晚驗證) | 個人助理(它自己的判斷點登錄照這份格式,各自內容不共用) |
| `project.v1.schema.json` | 人(或 manifest.update 信)改 `projects/*.yaml`;`bin/registry.py` 驗證 | 平台自己 |
| `managed-files.md` | — | 平台會寫進專案的檔案清單 |
| `vendor/<專案>/` | 別的專案定義的契約的副本 | 這邊的程式 |

- `{owner}`:機器讀的規則裡的 owner 標籤,這邊從 `hub.json` 的 `owner.tag` 填(現在是 Knas);讀的人用自己的設定。
- 版本:加欄位/加說明不升版;改名、改意思升版,產生者兩版並存到讀的人換完。
- 檢查:`tests/test_contracts.py`(範例照自己的契約與副本,②)、`bin/contracts-check.py`(每晚,副本 vs 原版
  只比機器讀的部分:markdown 的 regex/heading 區塊、JSON schema 去掉 `$id`/`title`/`description`,①;加上實際資料)。
- 授權:契約跟整個 repo 一樣是 Apache-2.0(根目錄的 `LICENSE`)。檔頭不另外註明(JSON 沒有註解,改檔頭也會讓副本跟原版對不上),
  也沒有 NOTICE 檔;vendor 過去的副本放在同樣是 Apache-2.0 的 repo 裡就夠,不用帶 NOTICE。vendor 進來的別人的契約照它原本的授權。
