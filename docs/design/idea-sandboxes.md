# 想法沙盒:替 Muninn 的想法開「探討用的專案」

> **狀態**:10-24 Knas 確認 A–F 照建議;第一階段(唯讀)已實作。第二階段(work 模式)還沒做,等 Knas 說要。

> 10-24 19:54 Muninn 看過並回覆(`m10241954-b179`):方向一致,沒有落差。它那邊的配合已寫進下面各段,
> 標「Muninn 已確認」的就是。

來源:Muninn 的請求 `m10241851-a45a`(10-24 18:51);Muninn 那邊的設計是 muninn repo
`docs/design/new-domains-and-projects.md` 的 C、D,介面是 `src/muninn/core/ports.py` 的
`Sandboxes.create(name, purpose) -> str | None`(已在 main、已部署,目前接的是 `NoSandboxes`/本機版)。

## 一句話

想法沙盒 = **一個正常 onboard、但沒有 GitHub remote、登錄裡標 `type: idea` 的專案**。
建立走 lease-runner 新增的 `POST /v1/sandboxes`;建好之後它自動出現在 lease 的 attachable 清單,
照現在的流程 /attach 深談。升級成正式專案 = 改 `type`、補 GitHub remote(要改名就順便 rename)。

## 對照 Muninn 的五點需求

| # | 需求 | 怎麼做 | 現有/新做 |
|---|---|---|---|
| 1 | 輕量、已登錄的專案,帶 purpose | `onboard-project.sh --no-remote` + 登錄加 `type: idea`、`purpose` | 新:包一層腳本;登錄加兩個欄位 |
| 2 | 預設唯讀,寫檔/跑工具要 Knas 允許 | 第一階段:只有現在的 discuss(唯讀)。第二階段:lease 加 `work` 模式 | 第二階段新做(見決定 D) |
| 3 | 出現在 attachable、能 /attach | attachable 本來就是「所有登錄過的專案」,登錄好就有 | 不用改 |
| 4 | 升級只改類型,歷史和檔案留著 | `bin/upgrade-idea.py`:改 type、建 GitHub repo + push、登記 mirror,要改名就呼叫 `rename-project.sh` | 新:一支腳本(rename 已確認能處理沒有 origin 的專案) |
| 5 | 給 Muninn 一個接點 | lease-runner `POST /v1/sandboxes`,同一個 token、同一份契約 | 新:一個端點 |

## 要你拍板的決定

**A. 放在哪:`~/workspace/idea-<n>`(扁平),不放 `~/workspace/ideas/<n>`**(建議)
- 扁平的話,現有的跨專案 deny 規則(`render-project-settings.desired()`)、transcript↔專案對應
  (`hublib.py`)都不用處理巢狀路徑。「是想法」由登錄的 `type: idea` 表示,不靠資料夾。
- 名稱由 Muninn 給(`idea-<編號>`),平台只驗證格式。

**B. 一定要有 memory(`.agent-memory`)**(建議)
- 深談收回時,交接筆記只會寫進 `<專案>/.agent-memory/memory/`(`lease-runner.deliver()`);對想法來說
  筆記就是成果。所以不做「只開一個資料夾」的超輕量版,而是 onboard 少掉 GitHub 那一步。
- 代價:每個想法多一個 `~/git-origin/idea-<n>-memory.git` 跟 `~/.memory-copies/idea-<n>`,librarian
  每晚會看它(沒新筆記就跳過,幾乎不花錢)。
- 沒有 remote,所以每晚的加密備份(「workspace 沒 push 的東西」)會把整個資料夾帶走,不會只活在 mini 上。

**C. `type: idea` 實際改變什麼**(建議以下四項)
1. 預設 `enforce: {sandbox: true}`:真正的寫入封鎖(只准寫自己的資料夾、讀不到別的專案、網路白名單),
   不只靠 prompt。升級成正式專案時保留,要關再關。
2. 預設 `privacy: {reflection: stats_only}`:想法可能很私人,週報只算數字、不送內容。
3. `index.md` 名冊把想法分一組列(`hubconf.py ideas`)。`todo.md` **不分組**:它的 `### <專案>` 小標是給
   Muninn 讀的契約(`^### (\S+)\s*$`),加字就讀不到;`idea-` 前綴本身就分得出來。狀態頁沒有逐專案的列表,不用改。
4. lease 的系統提示加上 purpose(「這是想法沙盒,在探討:…」),深談一開始就知道要談什麼。
   其他(librarian、memory-nudge、信箱、備份)照一般專案。

**D. 「寫檔、跑工具」的第二級要不要這次一起做**(建議:分兩階段,第二階段等你點頭)
- 這其實是 lease 的通用功能,不只沙盒用得到。要做的是:新的 llm 判斷點 `lease-session-work`
  (工具多 Edit/Write/Bash,但自動放行的還是只有讀;寫入都經過權限轉接)、`lease-relay.py` 對
  「工作資料夾內的 Edit/Write/Bash」送手機問(允許/拒絕/這個租約都允許)、`POST /v1/leases` 收 `mode`,
  Muninn 那邊加一張確認卡。先只開放 `type: idea` 的專案用。
- 第一階段做完,Muninn 就能「開沙盒 → 唯讀深談 → 交接筆記」;要動手做的事會進交接的待辦。
- **Muninn 已確認**形狀一樣(讀寫兩級、每次寫入送手機:允許/拒絕/這個租約都允許,跟現在的權限卡同一種),
  它那邊要加一種寫入確認卡。

**E. 重複呼叫回傳同一個專案,不另開新的**(建議)
- Muninn 呼叫逾時重送時,同名已經登錄過就回 200 + 那個名稱(purpose 不同就回 409),不會變成
  `idea-3-2`。
- **Muninn 已確認**:它的本機版也改成同樣的語意(同名同 purpose 回同一個、名稱被占用回 None,不加後綴;
  `813a9ae` 已部署),兩種專案端有共同的契約測試,接 HttpSandboxes 時一起加。`idea-<n>` 全域唯一、
  永不重用(撤回最新想法會重用的問題也修了),所以不需要 request id;名稱只會是 `^idea-[0-9]+$`。

**F. 上限:同時最多 20 個沒升級的想法沙盒**(`hub.json` 的 `ideas.limit`)
- 超過回 `429 too_many`,帶 `limit`、`current` 兩個數字(Muninn 要講得出「已經有 N 個」)。
- 封存(**Muninn 已確認**,第一階段不做):只有**放棄**(dropped)的想法要封存——從 attachable 拿掉、
  保留歷史;**擱著**(shelved)的不封存,可能會拿回來。之後給 `POST /v1/sandboxes/<name>/archive`。

## 介面:`POST /v1/sandboxes`

```
POST /v1/sandboxes   Authorization: Bearer <runner-token>
{"v": 1, "name": "idea-12", "purpose": "<想法本身,一兩句>"}

201 {"v": 1, "name": "idea-12", "created": true}
200 {"v": 1, "name": "idea-12", "created": false}        同名、同 purpose 已存在(重送)
400 {"error": "bad_name", "detail": …}                    名稱不是 idea-<數字>
409 {"error": "exists", "detail": …}                      同名但 purpose 不同,或跟正式專案撞名
429 {"error": "too_many", "detail": …, "limit": 20, "current": 20}
500 {"error": "create_failed", "detail": …}               onboard 失敗(已清掉做到一半的東西)
```

- 同步回應(onboard 本機版大約幾秒;runner 是多執行緒的,不會卡住其他租約的串流)。
- Muninn 的 adapter:2xx → 回 name;其他 → 帶原因失敗(Muninn 會把 `Sandboxes` 介面改成能帶原因),
  照 error 碼講給 Knas:too_many →「已經有 N 個,先擱著或放棄一些舊想法」;exists →「名稱被用掉了」;
  bad_name / create_failed →「開不了」+ detail。
- 建立的紀錄:agent-hub 的登錄 commit(`register idea-<n>`)跟 lease-runner 的 log。不發匯流排事件
  (hubbus 的事件類型是固定清單,也沒有人讀)。

## 第一階段要動的檔案

| 檔案 | 改什麼 |
|---|---|
| `bin/create-idea-sandbox.py`(新) | 驗證名稱 → 建資料夾、README.md 寫 purpose → `onboard-project.sh --no-remote`(`ONBOARD_REGISTRY_EXTRA` 讓 `type`/`purpose`/`enforce`/`privacy` 跟登記同一個 commit)→ 失敗就清掉;一次只跑一個(檔案鎖) |
| `bin/lease-runner.py` | `POST /v1/sandboxes`(呼叫上面那支);PREAMBLE 加 purpose |
| `bin/registry.py`、`contracts/project.v1.schema.json` | 加 `type: project\|idea`(預設 project)、`purpose`;`registry.py add --extra <JSON>` |
| `contracts/lease-runner.v1.schema.json` | 加 `/v1/sandboxes` 的請求/回應 |
| `bin/hubconf.py`、`bin/gen-index-roster.sh` | `hubconf.py ideas`;名冊想法分一組 |
| `bin/upgrade-idea.py`(新) | 需要時 `rename-project.sh` → `gh repo create`(帳號從 `hub.json` 的 `github.owner`)+ push → 改 type、origin → mirror-list;預設 dry-run,`--apply` 才做 |
| `tests/` | 契約範例、建立/重送/撞名/失敗清理、升級 |

## 順序(避免契約檢查又失敗)

改 `lease-runner.v1.schema.json` 會讓 Muninn 的副本(`contracts/vendor/records/lease-runner.v1.schema.json`)
跟原版不一致,當晚 01:30 的 contracts-check 就會失敗,跟 10-24 的 `secrets` 一樣。所以:

1. agent-hub:登錄欄位 + 建立腳本 + 升級腳本 + 測試(不碰契約)。
2. agent-hub:端點 + 契約一起 commit,**同時**寄信給 muninn「契約更新了,請同步副本」。
3. Muninn:同一天同步 `contracts/vendor/records/` 的副本、寫 `HttpSandboxes` adapter、組裝改用它
   (**Muninn 已確認**;我寄的信要寫出要更新的檔名)。
4. 我回 `m10241851-a45a` 並結案。

第二階段(如果 D 你說要)排在之後,另外給一份細的。

## 驗證紀錄(10-24)

- 建立:直接跑、跟經過 lease-runner 服務(launchd 環境)各一次,約 1 秒;登錄、memory、沒有 remote、
  sandbox 設定、attachable、系統提示帶 purpose 都確認過。重送 200、撞名 409、名稱錯 400、上限 429 都測過。
  兩個測試沙盒(idea-999998、idea-999999)已刪掉。
- 升級:只跑過 dry-run(`--apply` 會在 GitHub 建 repo,第一次真的升級時再看)。
- 失敗清理(onboard 中途失敗):`tests/test_create_idea_sandbox.py` 用假的 onboard 在暫存 hub 裡測——
  登錄檔還沒 commit 就全部清掉、已經 commit 就留著並在 detail 說要手動處理、清完可以重試;
  拿掉清理那段程式時三個測試都會失敗。

## 第二階段:work 模式(設計,10-24 Knas 說開始)

**範圍**:只有 `type: idea`、而且 `enforce.sandbox: true` 的專案能用(想法沙盒都是)。正式專案先不開放。

**怎麼進入**
- `POST /v1/leases` 帶 `mode: work`,或深談中途 `POST /v1/leases/<id>/mode {mode: work|discuss}`(Knas 在
  Muninn 的卡片上點「允許動手」之後才送;runner 不判斷 Knas 有沒有同意,那是 Muninn 的卡片)。
- 中途切換 = 停掉程序、用同一個 session `--resume` 接回,換成 work 的判斷點;事件 `state` 帶 `mode`。
- 不是想法沙盒 → 400 `work_not_allowed`。`GET /v1/policy` 多一個 `work_projects`(可以開 work 的專案)。

**權限(兩道)**
1. **OS sandbox**(第二階段平台本來就有,`render-project-settings.py` 寫進專案的 settings):Bash 只能寫自己的
   資料夾、讀不到別的專案、網路只有白名單。這是真的封鎖,不靠模型聽話。
2. **每次寫入送手機確認**(`lease-relay.py`,work 模式才有):
   - `Edit`/`Write`/`NotebookEdit`,路徑在這個專案資料夾裡 → 問 Knas(`risk: write_file`,摘要 = 路徑);
     「這個租約都允許」= 之後這個資料夾裡的寫入不再問。資料夾外 → 直接拒絕(不問)。
   - `Bash` → **每個指令都問** Knas(`risk: run_command`,摘要 = 指令);「這個租約都允許」只算這一次
     (試行期的保守做法;Muninn 的卡片在 `run_command` 不用顯示這個選項)。
   - 讀類(Read/Glob/Grep/WebSearch)照舊自動放行;WebFetch、提問照 discuss 的規則。
   - 5 分鐘沒回 = 拒絕,跟 discuss 一樣。
- 判斷點 `lease-work`(contracts/llm-points.yaml):工具多 Edit/Write/Bash,自動放行的還是只有讀;
  寫入全部經過權限轉接。

**10-24 實作與驗證**
- 開放條件看「實際寫好的設定檔」(`work_ready`):登錄 `type: idea`、而且 `<workspace>/.claude/settings.local.json`
  的 `sandbox.enabled` 是 true;建沙盒時就立刻渲染設定,不等 15 分鐘的排程。
- 實測(測試沙盒 idea-999997,用跟租約一樣的 `--settings`、`--strict-mcp-config`):Bash 寫資料夾外被擋、
  寫資料夾內成功、讀 `~/.ssh` 跟 `~/.claude` 的憑證都被擋(`operation not permitted`)。
- 權限轉接的規則有單元測試(`tests/test_lease_relay_work.py`:資料夾外、`../`、symlink 不問就拒絕;
  寫檔的「都允許」生效;指令每次都問;discuss 不變)。
- 還沒有真的從手機走過一次(要 Muninn 的寫入確認卡)。

**不做的**:正式專案的 work、git push(sandbox 的網路白名單本來就沒有 GitHub 的寫入;想法沙盒也沒有 remote)。

**要 Muninn 配合**:寫入確認卡(`write_file`/`run_command`,同樣三個選項);卡片上點「允許動手」後
送 `mode`;`lease-runner.v1.schema.json` 改了要同一天同步。
