# 深談裡把專案的檔案給使用者看(m10251606)

> **狀態**:10-25 Knas 確認 A–E 並同一天實作;做完請總設計的 session review。

來源:Muninn 的請求 `m10251606-7801`(10-25 16:06)。10-25 16:03 Knas 在 idea-1 的深談裡問「你可以給我看圖?」,session 沒有工具
能把圖給他,只能叫他回電腦打開 `results/render-v2/*.png`。現在檔案只能從他傳給 session(attachments),反過來沒有路。
完成條件(Muninn 的):idea-1 的 session 能讓 render-v2 的 png 直接出現在深談裡;資料夾外、類型不對、太大都被擋,有測試。

## 一句話

session 呼叫平台的 **「給使用者看」工具** `show_file(path, summary?)` → runner 檢查、**把檔案複製一份**到租約自己的目錄 →
送 v2 事件 `file` → Muninn 用 `GET /v1/leases/<id>/files/<file id>` 拿檔案,在深談裡內嵌顯示。

## 怎麼讓 session 說「給他看這個」(建議:平台工具,不是指令、也不是猜回覆)

| 做法 | 問題 |
|---|---|
| 從 session 的回覆裡偵測提到的檔名 | 靠模型的措辭;提到不等於要給他看,也會漏 |
| 平台指令 `hub-show <路徑>`(Bash) | **discuss 模式沒有 Bash**(唯讀深談用不了);work 模式每個指令都要 Knas 按允許,看張圖也要按一次 |
| **平台工具 `show_file`(建議)** | 明確的一次呼叫;discuss、work 都能用;不用按允許 |

- **中立的部分**:「給使用者看一個檔案」是平台的服務,不屬於哪個 agent。runner 開一個內部端點
  `POST /v1/internal/<lease>/show {path, summary?}`(跟現在的 `/v1/internal/<lease>/relay` 一樣只給租約自己的轉接用),
  所有檢查、複製、事件都在 runner。
- **Claude Code 的接法**:租約本來就掛著平台的 MCP 伺服器(`lease-relay.py`,現在只有權限轉接的 `approve`),多一個工具
  `show_file`;relay 只是把呼叫轉給 runner、把結果(成功/被擋的原因)回給 session。這個工具要在租約判斷點的自動放行清單裡
  (`llm-points` 的 lease-session/lease-work,**那支現在是另一個 session 的**,見最後)。
- 系統提示加一行:「要讓 Knas 看專案裡的圖或檔案,用 show_file(專案內的路徑, 一句說明);他在手機上會直接看到。」
- 換別的 agent:它的實作把同一個端點接到自己的工具機制就好,事件跟端點不變。

## runner 的檢查(不過就回原因給 session,不送事件)

1. **在專案資料夾裡**:路徑相對專案資料夾解析;解析成真實路徑後要在資料夾裡(`../`、symlink 指出去都擋),而且是一般檔案。
2. **類型**:只收 Muninn 列的 `png jpg jpeg webp gif pdf glb txt md json csv`,照副檔名決定 mime,**再看內容開頭對不對**
   (png/jpg/gif/webp/pdf/glb 的 magic bytes;txt/md/json/csv 要是 UTF-8)。改副檔名騙不過。**不收 svg、html**(Muninn 是網頁,
   這兩種能帶程式碼)。
3. **大小**:每個檔 ≤ 20 MB;每個租約最多 50 個、合計 200 MB(避免把磁碟塞滿)。
4. **不送看起來像憑證的檔**:檔名像 `.env*`、`*.pem`、`*.key`、`*secret*`、`*token*`、`credentials*`,或專案登錄 `secrets:` 宣告的
   路徑,一律拒絕(就算副檔名在允許清單裡,例:`secrets.json`)。
5. **不用 Knas 按允許**:給他看的是他自己專案裡的檔案,不會送到他以外的地方;每次都記在事件裡(手機上看得到)。

## 為什麼複製一份

- 檢查完就複製到 `.state/lease-runner/<lease>/files/<file id>`(session 的 sandbox 寫不到這裡),之後只從這份送:
  - 檢查跟送出之間檔案被換掉(換成 symlink、改內容)也沒用——送的是檢查當下那份。
  - Muninn 要的「重新打開也還在」:session 之後改了、刪了那個檔,深談裡看到的還是當時那張。
- 租約收回後保留 30 天(runner 每天清一次),跟對話紀錄差不多久。

## 契約(v2;v1 不加——v1 最晚 10-26 就刪)

**事件** `file`(加進 `lease-runner.v2.schema.json` 的事件類型):
```
{type: "file", id, path(專案內相對路徑), name, mime, size, summary?}
```

**取檔** `GET /v1/leases/<lease id>/files/<file id>`(同一個 Bearer token):
- 200:檔案內容,`Content-Type` = mime、`Content-Length`、`Content-Disposition: inline; filename=…`、`X-Content-Type-Options: nosniff`
- 404 `not_found`(沒有這個租約/檔案,或已經過了保留期限)
- 收回之後也拿得到(在保留期限內)。

v1 的事件串流不送 `file`(render 到 v1 時跳過);v1 刪掉之後就沒有這個差別。

## 隱私

想法沙盒是 `privacy: stats_only`(內容不送雲端模型)。檔案跟使用者傳進來的照片一樣,**Muninn 那邊要標 local_only**,不送雲端;
回信時寫清楚。

## 測試

- 檢查:資料夾外(絕對路徑、`../`、symlink 指出去、資料夾裡的 symlink)、不是一般檔案、類型不對(副檔名對但內容不對、svg、html)、
  太大、超過租約上限、像憑證的檔名、登錄宣告的 secrets 路徑——都被擋、不送事件、session 拿到原因。
- 正常:png → 事件格式合 v2 schema、取檔拿到一樣的 bytes 跟對的 header;原檔之後被改或刪,取檔還是當時那份。
- v1 串流不出現 `file`;v1 相容的既有測試照過。
- relay 的 `show_file` 工具:轉給 runner、把成功/拒絕的原因回給 session。
- 實測:idea-1 的 session(用租約一樣的設定)show_file `results/render-v2/*.png`,事件跟取檔都通。

## 要 Knas 確認的

**A. 用平台工具 `show_file`**(建議),不用指令、不偵測回覆。
**B. 檢查完複製一份再送**(建議),保留 30 天。
**C. 類型照 Muninn 的清單、看內容開頭、不收 svg/html;20 MB/檔、每租約 50 個/200 MB**(建議)。
**D. 像憑證的檔名、登錄宣告的 secrets 一律不送**(建議)。
**E. 不用按允許**(建議):是他自己專案的檔案、給他自己看;每次都記在事件裡。

## 跟另一個 session(總設計)的分工(10-25 談好)

- **現在**:這份在 `contracts/llm-points.yaml` 現在的格式裡,lease-session、lease-work 的 `allowed_tools` 各加一項
  `"mcp__lease_relay__show_file"`,用 `LLM_GOLDEN_UPDATE=1` 重生 `tests/fixtures/llm-golden.json`,確認 diff **只有**這兩個判斷點 ×
  4 種變化的 `--allowedTools` 多了那一項。`llm.py`、`runtime_claude.py` 不碰(那邊在改第 6 層)。
- **之後**:總設計把 llm-points 換成中立格式時,宣告 `platform_tools: [show_file]`(平台工具的中立名稱),`runtime_claude` 翻成
  `mcp__lease_relay__show_file` 放進允許清單——跟 kind 的翻譯同一個原則,翻譯表只在 runtime。驗收標準:換完 golden 完全不變。
- runner、relay、契約、測試由這份做。

## 順序

1. runner:`/v1/internal/<lease>/show`(檢查、複製、事件)+ `GET …/files/<id>` + v1 跳過 `file` + 保留期限清理 + 測試。
2. relay 加 `show_file` 工具;系統提示加一行。
3. 契約 v2 加 `file` 事件跟取檔端點的說明。
4. llm-points 的 lease-session/lease-work 放行 `mcp__lease_relay__show_file`,重生 golden、檢查 diff(上一段)。
5. idea-1 實測;回信 muninn(事件、端點、隱私);`hubbus done`。
