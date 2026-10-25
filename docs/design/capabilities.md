# 想法沙盒的額外能力(capabilities)+ 跟執行環境無關的宣告:設計,待 Knas 確認

來源:Muninn 的請求 `m10251437-1f92`(10-25 14:37)。Knas 同意的方向:平台維護一組固定的「能力」,專案宣告要用哪些,
沙盒裡的 session 透過同一支平台指令在對應的限制下執行。第一個能力是 `gpu`。
10-25 Knas 另外指定:**登錄裡的能力用跟執行環境無關的詞彙宣告,Claude 專用的翻譯集中在一處**。

草稿跟實測是另一個 agent-hub session(「測試」)做的,10-25 交接給這份設計的 session,之後由這邊負責設計、實作跟 commit。
profile 原型跟兩支逃脫測試複製在 `.state/cap-prototype/`(不進版控)。

## 起因

`idea-1`(用照片重建房間 3D)要用 Blender 出圖。Blender 在 Mac 上只支援 Metal,Claude Code 的 sandbox 擋住它
偵測 GPU,一啟動就崩潰;現在只能請 Knas 自己去終端機跑。Claude Code 的 sandbox 沒有開 GPU/IOKit/Metal 的設定,
也不能自訂 Seatbelt profile。

「不用 Knas 動手」的意思(10-25 Knas 在「測試」session 確認):**他不用自己操作 Blender**。手機上的確認照舊:
work 模式下 `hub-run …` 是一個 Bash 指令,每次都會送到手機讓他按允許。這道人工把關不變。

## 10-25 實測(「測試」session;都在暫存目錄或測試沙盒,做完已刪)

1. **平台自己的 macOS sandbox 規則(Seatbelt)可以讓 Blender 用 GPU**:同一個出圖腳本,不加限制跟套上 `gpu` 規則
   都在約 1 秒內產出兩張預覽圖、`.blend`、`.glb`。
2. **同一份規則擋得住 16 項逃脫**:寫資料夾外、讀 `~/.ssh`、讀 claude 憑證、讀別的專案、讀服務的 `.env`、
   讀平台機密、**Keychain**、連網路(含 127.0.0.1)、`open` 開 app、`osascript` 送 Apple Events、`launchctl`、`crontab`;
   寫/讀自己的資料夾跟暫存區正常。Keychain 是 Claude Code 的 sandbox 擋不住的(known-issues.md),平台的規則擋得住
   (直接擋 `securityd` 的 mach-lookup)。`open` 跟 `osascript` 一開始是放行的(Calculator 真的在 sandbox 外被開起來),
   補上 `(deny lsopen)`、`(deny appleevent-send)` 跟 process-exec 的 deny 才擋住。
3. **sandbox 不能疊兩層**:在 Claude Code 的 sandbox 裡跑 `sandbox-exec` 一律 `sandbox_apply: Operation not permitted`。
   所以套平台規則的那一步,不能發生在 session 的 sandbox 裡面。
4. **在真實位置驗證**:測試資料夾放在 `~/workspace/<探測用>`(規則先擋整個 `~/workspace`、再放行自己的資料夾,
   兩條真的重疊到)。放在暫存目錄測不出這個重疊。
5. **Claude Code 的 `excludedCommands` 可以用,但只對「單獨」的指令**:單獨跑排除的包裝指令會跳出 Claude Code 的
   sandbox;跟別的指令串在一起(`… && …`、`…; …`)時整串都留在 sandbox 裡,串在後面的逃脫都被擋下。有沒有租約的
   `--settings` 結果都一樣。

## 三層:宣告、翻譯、強制

```
專案登錄(中立詞彙)── bin/policy.py ──┬─ bin/runtime_claude.py → .claude/settings.local.json、租約的 --settings
  enforce.sandbox                     │   (Claude Code 怎麼做到;全平台唯一知道 Claude 設定格式的地方)
  enforce.capabilities                │
  network、allow_write                └─ bin/seatbelt.py → 平台的 Seatbelt profile,給 cap-runner 用
                                          (macOS 怎麼擋;不經過任何 agent)
```

- **`bin/policy.py`(新)**:從登錄算出一個專案的政策,只用中立的詞:
  `{workspace, write: [路徑], deny_write: [路徑], deny_read: [路徑], credentials: [路徑], network: [網域],
  platform_commands: [路徑], capabilities: [名稱]}`。內容就是現在 `render-project-settings.desired()` 算的東西,
  拿掉 Claude 的語法(`Read(...)`、`Edit(...)`、`Bash(... *)`)。`credentials`(`~/.ssh`、`~/.claude*`、`~/.aws`、
  `~/.config/gh`)現在只寫在 `lease-runner.deny_settings()`,搬過來成為同一份,位置放 `hub.json` 的 `credentials`
  (程式碼裡不寫執行環境的路徑)。Keychain 不是檔案,由 `seatbelt.py` 自己處理。路徑都從 `hubconf` 拿
  (現在 `desired()` 裡寫死的 `~/agent-hub` 一併改掉)。
- **`bin/runtime_claude.py`(擴充)**:檔頭本來就寫「換工具只改這個檔」,但目前只有信箱分流遵守。這次把下面兩件搬進來:
  - `settings(policy)` → `sandbox` 區塊 + 平台的 deny/allow 規則(現在的 `desired()` 的 Claude 那一半)、
    `settings_path(workspace)`、`sandbox_rendered(workspace)`(`lease-runner.work_ready` 現在自己讀 `settings.local.json`)
  - `lease_settings(policy)` → 租約的 `--settings` 檔(現在的 `lease-runner.deny_settings()`,含擋 `security` 的那幾條)
  `render-project-settings.py` 只剩「寫檔、記錄上次寫了什麼、偵測偏移」的機制,要寫什麼問 `runtime_claude`。
- **`bin/seatbelt.py`(新)**:`profile(policy, cap)` → SBPL 文字。**讀取**清單(`deny_read`、`credentials`)跟 Claude 那份
  用同一個 `policy`,不另外手寫(原型裡的讀取清單是手寫的,這次改掉)。**寫入**刻意不照 `policy.write`:只准專案資料夾 +
  暫存區。代跑的工作沒理由直接寫匯流排(會繞過 `hubbus` 的檢查)或共用的套件 cache。

**這次不搬、留給 m10251446(見最後一段)**:租約的 `claude` 旗標、stream-json 解析、工具名稱、rate limit、`--resume`、
`lease-relay.py` 的 Claude 工具規則、`contracts/llm-points.yaml` 的工具清單。做完那份之後,Claude Code 的知識只在
`runtime_claude.py`(翻譯)、`llm.py`(唯一執行 `claude -p` 的地方),加上 `lease-relay.py` 的傳輸那一層——它本身就是
Claude 的 `--permission-prompt-tool`(MCP、回 `behavior`/`updatedInput`),規則會改成看 kind,但程序跟回傳格式還是 Claude 專用。

**邊界測試**(`tests/test_runtime_boundary.py`,新):這次動到的中立檔案(`policy.py`、`seatbelt.py`、`cap-runner.py`、
`hub-run`、`registry.py` 的能力段)裡不准出現 Claude 的詞(`.claude`、`Read(`/`Edit(`/`Bash(`、`claude`)。m10251446
做完再把 `lease-runner.py` 加進來。

## 登錄

```yaml
type: idea
enforce:
  sandbox: true
  capabilities: [gpu]     # 新;平台有的能力名稱才收
network: [...]            # 既有,本來就是中立的
allow_write: [...]        # 既有
```

- `registry.py` 驗證:`capabilities` 每個名稱都要在平台的能力清單裡;要有 `enforce.sandbox: true`;這次只收 `type: idea`。
- **能力清單**:`hub.json` 的 `capabilities: {gpu: {risk: low, default_for: [idea], description}}` + `capabilities/<名稱>.sb`
  (那個能力的 profile 片段)。之後要新能力 = 多一筆清單 + 一個片段檔,入口跟專案都不用改。
- **`gpu` 預設給想法沙盒**:`create-idea-sandbox.py` 建立時照 `default_for` **明寫**進登錄(看登錄就知道有什麼,
  不靠 `type` 隱含);`idea-1` 補一行。`risk: high` 的能力不會自動加,由 Knas 同意後在 agent-hub session 改登錄。

**登錄的安全欄位只能由平台改**(修一個既有的洞):`registry.py apply-updates` 現在只檢查「寄件專案改的是自己的登錄」。
所以沙盒裡的 session 寄一封 `manifest.update` 就能把 `enforce.sandbox` 關掉、或把 `allow_write`/`network` 開大;
加了 `capabilities` 之後還能自己加能力;把 `type` 改成 `idea` 就拿得到 work 模式跟能力;把 `privacy` 的 `stats_only`
拿掉,想法的內容就會進週報的雲端呼叫。改成:`type`、`enforce`、`allow_write`、`network`、`privacy` 跟現在的登錄不一樣
就整封退回,說明要請 agent-hub session(Knas 確認)改。目前沒有專案用 manifest.update 改過這些欄位(git log 只有一次,
muninn,沒碰到),不影響現有流程。

## 執行入口:`hub-run`(平台代跑)

**兩種做法**

- **A. 排除 + 包裝(Muninn 原本提的)**:把 `hub-run` 寫進專案的 `excludedCommands`,它跳出 Claude Code 的 sandbox 後
  自己用 `sandbox-exec` 套平台規則。實測可行(上面第 5 點)。缺點:
  安全性靠 Claude Code 正確判斷串接指令,哪天它的判斷有破綻,串在後面的指令就整串跳出 sandbox;要寫進 Claude 的設定,
  換工具就要找對應的機制;跳出來的 `hub-run` 也沒辦法可靠地知道自己是哪個專案(`cd` 到別的專案再跑就冒充得了)。
- **B. 平台代跑(建議)**:`hub-run` 留在 session 的 sandbox 裡,只把請求寫進**自己專案資料夾**的 `.hub-run/requests/`;
  平台一個不在 sandbox 裡的常駐服務收到請求,檢查過才用平台規則執行,把輸出跟結束碼寫回去。
  對執行環境的唯一要求是「能寫自己的資料夾」,任何 agent 都做得到;Claude 的設定一行都不用加。A 留作備案。

**請求怎麼證明是哪個專案寄的**:sandbox 只准每個專案寫自己的資料夾,別的專案寫不進來。服務只掃登錄裡宣告了能力的專案的
`<workspace>/.hub-run/requests/`,**請求在哪個資料夾被找到,就是哪個專案**;執行用的專案資料夾從登錄來,不看請求裡寫什麼。
(不放共用的匯流排目錄:每個專案都寫得到那裡,A 專案就能冒充 B。)`.hub-run/` 寫進 `.git/info/exclude`。

**請求格式**:`{"v": 1, "id", "cap": "gpu", "argv": [...], "cwd": "<相對專案資料夾>", "timeout_s"}`。`argv` 是 JSON list,
服務直接 exec `sandbox-exec -f <profile> -D WS=… -D HOME=… <argv>`,中間沒有 shell 字串。

**服務收請求時的檢查**(全部不過就寫回 `rejected` 跟理由,不執行):
- `.hub-run/`、`requests/` 跟請求檔本身,解析真實路徑後都要在專案資料夾裡,而且是一般檔案/目錄——`.hub-run` 被換成
  symlink 指到別的專案時,結果檔會寫到別人那裡,所以這層也要擋(原型草稿只檢查了 `cwd`)。寫回結果用不跟隨 symlink 的方式開檔。
- `id` 只能是單純的檔名;`cwd` 解析成真實路徑後要在專案資料夾裡(資料夾裡的 symlink 指出去也擋)。
- `cap` 要在這個專案登錄的 `enforce.capabilities` 裡;請求檔不超過 64 KB;太舊或已經回覆過的不理。
- **profile 不放在專案資料夾**:每次執行時從登錄現算,寫在 `.state/cap-runner/<專案>/<id>/profile.sb`(session 寫不到)。
  原型草稿放在 `<workspace>/.hub-run/profiles/`——那是 session 自己能改的地方,改一行就等於沒有 sandbox。

**服務**:新的 launchd 服務 `local.agent-hub-cap-runner`(`bin/cap-runner.py`,常駐、`KeepAlive`、每秒掃一次),plist 放
`launchd/`、登記進 `services.tsv`(owner agent-hub,`recover-service.sh` 能重建)。不放進 lease-runner:runner 重啟不能殺掉
正在跑的工作。
- 環境變數只給 `PATH`、`HOME`、`TMPDIR`、`LANG`,其他全部清掉(沒有 token、沒有 `HUBBUS_*`)。
- 逾時預設 10 分鐘、上限 30 分鐘(`hub.json` 的 `capabilities_runner`),逾時整個 process group 一起殺掉;
  同時最多跑 2 個、每個專案 1 個。
- `stdout`、`stderr`、`exit` 寫在請求旁邊;每個工作記一行在 `.state/cap-runner/log.jsonl`(專案、能力、argv、結束碼、時間)。
- 服務沒在跑:`hub-run` 10 秒內沒看到服務接手就印「平台代跑服務沒在跑」並用 125 結束;服務每次掃描更新心跳檔,
  平台 job `agent-hub/cap-runner-check`(每 15 分鐘)看心跳,停了經 `alert.py` 發 Discord,狀態頁也標紅。

**`hub-run`(客戶端)**:標準函式庫的小腳本,`hub-run --cap gpu [--timeout 秒] -- <指令…>`。一開始就印出工作 id,一邊等一邊印輸出,
最後用同一個結束碼結束。session 的 Bash 呼叫有自己的逾時(預設 2 分鐘、最多 10 分鐘),被殺的是 `hub-run`、不是工作本身,
所以 `hub-run --wait <id>` 可以重新接上。不帶 `--cap` 不收(能力就是授權,一定要明講用哪個)。請求檔先寫暫存檔再
rename(服務不會讀到寫一半的 JSON);10 秒沒人接手就把請求刪掉再結束,晚啟動的服務不會跑沒人等的工作。

## `gpu` 的 profile 到底是什麼(說清楚)

- 規則是**先全部放行,再擋掉危險的部分**(deny-list):只准寫自己的資料夾跟暫存區;讀不到 `~/workspace`、`~/services`
  (自己的資料夾除外)、memory 副本、平台機密、憑證、Keychain;完全不能連網路(**含 127.0.0.1**,所以工作叫不到 lease-runner
  或 Muninn);擋 Keychain 的系統服務;擋「請別的系統服務替它啟動程式」(開 app、Apple Events、`launchctl`、`crontab`、`at`)。
  **其他沒列到的都放行,其中包括 Metal**。
- 所以這次 `gpu` 的片段是空的:Metal 本來就不在 deny 清單上。能力在這裡的作用是**授權**(誰能用平台代跑),不是多開一個洞。
  之後若改成「預設全擋、逐項開」(要一項項找出 Blender 需要什麼,這次不做),`gpu` 片段才會變成 Metal/IOKit 的那幾條。
- 跟 Claude Code 的 sandbox 比:讀取範圍比它嚴(整個 `~/workspace`、`~/services` 都擋)、網路比它嚴(完全不通)、
  多擋 Keychain 跟啟動程式;寫入少了匯流排目錄跟套件 cache,但多了**整個系統暫存區**(`/private/tmp`、`/private/var/folders`,
  其他程式的暫存檔也在裡面)。試行期先接受;哪個能力需要收緊,就給每個工作一個 `.hub-run/tmp` 當 `TMPDIR`、拿掉共用暫存區。
- 另外會加擋「看別的程序的資訊、對別的程序送信號」,做之前先實測。
- **真正的把關還是手機確認**:規則是補強,實測擋得住上面列的;沒列到的不保證。

## 租約跟 Muninn

- **租約契約不用改**:指令形狀照 Muninn 提的(`hub-run --cap gpu -- <指令>`),在 work 模式就是一個要確認的 Bash 指令。
- work 模式的系統提示,專案有宣告能力時加一行:「需要 GPU 的程式用 `<hub>/bin/hub-run --cap gpu -- …` 跑(長的工作用
  `--wait <id>` 接回)」,路徑從 `hubconf` 組(租約的 PATH 沒有 agent-hub 的 bin)。
- SSH 開的互動 session 一樣能用(它的 sandbox 也准寫自己的資料夾)。
- 手機確認不放寬。平台 profile 擋得住 Keychain,所以「`hub-run` 免確認」之後可以重新討論,但要 Knas 決定,不在這次。

## 要你拍板的決定

**A. 用平台代跑(B),不用 excludedCommands(A)**(建議)——理由見上;A 實測可行,留作備案。

**B. `gpu` 建立時明寫進登錄**(建議)——不靠 `type: idea` 隱含;`idea-1` 補一行 `capabilities: [gpu]`。

**C. `type`、`enforce`、`allow_write`、`network`、`privacy` 只能由平台改**(建議)——`manifest.update` 改到就退回。這是現在就有的洞,
不只能力會用到。

**D. 翻譯層的位置跟範圍**(建議)——中立的 `policy.py`;Claude 的翻譯收進 `runtime_claude.py`;平台 profile 在 `seatbelt.py`。
這次只搬「設定怎麼寫」,租約的旗標、事件、工具名稱留給 m10251446。

**E. `gpu` profile 試行期用「先放行再擋」**(建議)——含共用暫存區這個已知的寬鬆處,上面列清楚了。

**F. m10251446 另外處理**(建議)——見下一段。

## 順序(每步一個 commit,只 add 自己的檔案)

1. `policy.py` + `runtime_claude.settings()`/`lease_settings()`,`render-project-settings.py`、`lease-runner.py` 改用它們。
   **行為不變**:改之前、之後對每個專案算出的設定跟 `.state/rendered-settings/` 逐位元組相同,租約的 `--settings` 也相同。加邊界測試。
2. 登錄:`enforce.capabilities`、能力清單、`apply-updates` 擋安全欄位;`contracts/project.v1.schema.json` 加欄位(加欄位不升版)。
3. `seatbelt.py` + `capabilities/gpu.sb` + `tests/test_cap_profile.py`(用原型的 16 項;測試資料夾建在 `workspace_root` 底下
   才測得到重疊,不在 mini 就 skip;`open` 那項沒擋住會真的開 Calculator,teardown 要關掉)。
4. `cap-runner.py` + `hub-run` + plist + `services.tsv` + 心跳檢查 job + 測試(別的專案資料夾的請求、`.hub-run` 是 symlink、
   `cwd` 跑出去、沒宣告能力、逾時,都要被拒絕或殺掉)。
5. `create-idea-sandbox.py` 照 `default_for` 寫能力;`idea-1` 登錄補一行;work 模式系統提示加一行。
6. 實際跑一次:在 `idea-1`、用它真的設定開的 session 裡跑 `hub-run --cap gpu -- blender -b … -P tools/blender_render.py …`,
   出得了圖。`known-issues.md` 補「sandbox 不能疊兩層」「excludedCommands 只對單獨指令有效」。
7. 回信 muninn(`m10251437-1f92`):做了什麼、指令怎麼用、跟它提的差在哪(代跑而不是排除),然後 `hubbus done`。

## 另外一封:m10251446(契約跟執行環境無關)

Muninn 10-25 14:46 另外要求 `contracts/lease-runner.v1.schema.json` 不再用 Claude Code 的字彙:事件的工具名稱改成通用類別
(`kind: ask_user|read|search|write_file|run_command|open_web|other` + `summary`)、提問用通用結構、rate limit 改成
`windows: [{id, label, used, resets_at}]`、`/terminal` 回 `{command, hint}`、錯誤碼 `auth` 改 `agent_login`。

它跟這份是同一個原則,翻譯表也會放在同一個 `runtime_claude.py`(工具名稱 → kind、stream-json → 中立事件、AskUserQuestion ↔
通用提問、rate limit → windows、接續指令、錯誤分類;`lease-relay.py` 的規則改成看 kind)。但它要跟 Muninn 約一個並行截止日、
牽涉 Muninn 擁有的 `permission-requests` 契約,所以**另外寫一份設計**,不跟這份一起確認;這份做完之後再開始。
