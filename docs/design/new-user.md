# 新使用者:從零到第一次夜間整理,以及往這個方向要改的架構

> agent-hub session · 2026-10-26 · 只是設計,Knas 確認前不動手(第 0 步除外,見「步驟」)。
> 接續 `.state/phase1-design.md`(「平台**程式碼**裡沒有專案名稱」)跟 `runtime-abstraction.md`(「Claude Code 只是一個實作」),
> 不重談那兩份定案的事(集中登錄、`registry_dir`、`push_on_merge` 預設 false、ports & adapters)。
> **r2(10-26)**:照 Agent Hub session 的審查改過(方向同意;邊界、模組範圍、watchdog、驗收測試),改動處標 **(r2)**,
> 討論紀錄在第 8 節。

## 目標

10-26 Knas:「想像一個全新的使用者未來要使用這個平台,他要怎麼做最方便、最容易擴展。」

第一階段的驗收是「程式碼裡沒有**專案**名稱」。這份把它延伸成:**程式碼裡沒有人、助理、機器的名稱,也不假設有兩台機器**。
新使用者 clone 下來,只要回答幾個問題(名字、專案放哪、要不要個人助理/警報/雲端備份),一台 Mac 就能跑起來;
之後要加功能,是**多登記一個模組**,不是改 librarian 這種大腳本。

三個分離,一條路徑,一個自我檢查:

1. **程式碼 vs 使用者的資料**:平台 repo 只有程式、樣板、契約、範例;使用者的設定、登錄、產出放在他自己的實例目錄。
2. **核心 vs 模組**:核心是每個人都要的(登錄、排程、memory 同步、librarian 的 STATE.md 整理、信箱、狀態頁);
   其他(個人助理、警報、雲端備份、GitHub mirror、每週回顧、回頭清理、sandbox 強制)是可以不開的模組。
3. **角色 vs 機器**:程式只認角色(「memory 所在的機器」「跑排程的機器」),哪台機器是設定;預設全部是本機。
4. **一條路徑**:`git clone` → `bin/setup` → `bin/hub init` → `bin/hub doctor` → `bin/onboard-project.sh` → 隔天早上看 digest。
5. **自我檢查**:`tests/test_runtime_boundary.py` 那種機械檢查,掃 `bin/` 裡的個人名稱;只准變少(ratchet),其他 session 新寫的程式不會倒退。

## 1. 現在從零開始會卡在哪(2026-10-26 盤點)

照新使用者會走的順序;`bin/` + `templates/` + `launchd/` 共 88 個檔。

| # | 卡在哪 | 在哪裡 |
|---|---|---|
| 1 | 沒有 README,不知道從哪開始;75 支腳本,入口只寫在 `CLAUDE.md`(而那份是寫給 agent 看的、滿是 mini/MBP 的歷史) | repo 根目錄 |
| 2 | 沒有範例設定:`hub.json` 就是 Knas 的設定(`owner.tag: Knas`、`assistant.project: muninn`、`backup.repo: b2:<backup-bucket>:…`、`memory_host: hub-host`、`github.owner`) | `hub.json` |
| 3 | 程式碼跟資料混在同一個 repo:clone 下來就拿到 Knas 的 `projects/*.yaml`、`digest/`、`reflect/`、`index.md`、`todo.md`、`review-log.md`、`schedule.yaml`、`mirror-list.txt`、`services.tsv`、`legacy-paths.tsv`、`openclaw-migration-status.md`、`agent-jobs/` | repo 根目錄 |
| 4 | 平台的 launchd plist 寫死 `/Users/you`、`/Users/you` | `launchd/*.plist`(7 份) |
| 5 | 寫死第二台機器 `hub-host` | `minilib.sh:9`、`recover-project.sh:21`、`sync-all-memory.sh:34`、`transcript-backup.sh:20`、`trajectory.py:36`、`migrate-to-mini.py:42`、`openclaw-session-backup.sh:28` |
| 6 | 寫死 hub 位置、memory 副本位置 | `librarian.sh:22,104,111`、`commit-hub-files.sh:13`、`gen-index-roster.sh:20`、`hubbus:281`(給 agent 看的提示;**(r2)** hubbus 裡的 HEADER/FOOTER、questions_note、qcheck 的信、RESUME/DELEGATE_PROMPT 由 Agent Hub session 改) |
| 7 | librarian 的後段固定照順序跑 5 步,其中 `knas-tasks.py`、`reconcile-questions`(:384–391)是個人助理專屬,沒有助理也照跑 | `librarian.sh:375–420` |
| 8 | 待辦的 owner 標籤寫死 `Knas` | `gen-todo.py:38`、`librarian.sh:57` 的 prompt、`consolidate-memory.py:258` 的 prompt |
| 9 | 每週回顧、軌跡分析的 prompt 寫死「Knas」 | `reflect_llm.py:43`、`trajectory.py:134` |
| 10 | 給每個專案 agent 的說明寫死 Knas / Muninn | `templates/CLAUDE.md:40,44` |
| 11 | 警報只有 Discord;沒設定時是「狀態頁標紅」,不是「這個模組沒開」 | `alert.py`、`scheduler-watchdog.py` |
| 12 | 個人名稱散在程式跟註解:`Knas` 36 個檔 194 行、`Muninn` 25 個檔 155 行(多數是註解裡的決策紀錄) | `bin/` |

已經做好、新使用者直接受益的:`hubconf.py`(hub 根目錄由腳本位置推出、`AGENT_HUB_ROOT`)、`bin/py` + `bin/setup-python`、
集中登錄 + `onboard-project.sh` 全自動、`github.owner` 從設定讀、runtime 介面(Claude 可換)、契約 + 契約測試。

## 2. 新使用者的路徑(目標)

```
git clone <agent-hub> ~/agent-hub && cd ~/agent-hub
bin/setup            # = setup-python + 檢查 git/gh/claude/jq;缺什麼印怎麼裝,不自己裝
bin/hub init         # 問:你的名字(待辦標籤)、時區、專案放哪(~/workspace)、實例資料放哪、要開哪些模組
                     # → 寫 <實例>/hub.json(從 hub.example.json)、空的 projects/、裝排程的 launchd plist
bin/hub doctor       # 一次看完:Python、claude 登入、登錄、排程有沒有在 tick、各模組的憑證;壞的說怎麼修
bin/onboard-project.sh ~/workspace/my-app
# 隔天早上:<實例>/digest/<日期>.md、狀態頁
```

- **一台機器是預設**。要像 Knas 這樣「Mac mini 跑、筆電遙控」,是 `hub init` 裡的一個選項(或之後改 `hosts`),不是前提。
- **個人助理是選配**。沒開 `assistant` 模組:沒有 knas-tasks、沒有 hubbus-bridge、`--to owner` 的信只進狀態頁跟 digest,
  通道租借、想法沙盒不存在。
- **警報、雲端備份、mirror 都是選配**,沒開就是沒有那幾個 job,狀態頁不標紅。

## 3. 架構方向

### 3.1 實例目錄(程式碼 vs 資料)

- `hubconf` 多一個概念:**實例目錄**(`AGENT_HUB_HOME`,預設 = hub 根目錄,也就是今天的樣子,所以切換前什麼都不變)。
  `hub.json` 跟所有使用者資料都從實例目錄讀寫;`hubconf.data(<名稱>)` 取代各腳本自己拼 `$HUB/digest`。
- 平台 repo 留:`bin/`、`templates/`、`contracts/`(不含 vendor 的別人契約?見「要確認的」)、`capabilities/`、`tests/`、
  `docs/`、`hub.example.json`、`projects.example/`、README。
- 實例目錄放:`hub.json`、`projects/`(含 job prompt)、`digest/`、`reflect/`、`index.md`、`todo.md`、`review-log.md`、
  `mirror-list.txt`、`services.tsv`、`models.env`、`.state/`、`.secrets/`、`.logs/`。實例目錄自己可以是一個 git repo
  (`commit-hub-files.sh` 改成 commit 到它),要不要 push 到哪是使用者的事。
- `known-issues.md` 是平台的知識,留在平台 repo,但改寫成不靠「mini/MBP」的說法;`openclaw-migration-status.md`、
  `legacy-paths.tsv` 是 Knas 的歷史,搬進實例。
- **(r2)** `agent-jobs/` 不是整個進實例:`mailbox.md` 是平台的 prompt(信箱 dispatch 用),搬到 `templates/prompts/`;
  實例只放使用者自己 job 的 prompt(第一階段 G 已經說跟著登錄走:`projects/<name>/jobs/*.md`)。
- **(r2)「每台機器一份」的東西不進實例**:平台的 Python(`~/.local/share/agent-hub/python`,刻意不寫在 hub.json——hubconf
  要先有 Python 才能讀)、匯流排(`bus_dir` = `~/.local/state/agent-hub-bus`,刻意放 hub 外面給 sandbox 寫)。
- **(r2)** 搬家前先把 `schedule.yaml`(切換期間的舊項目)清到 0 項、全部進登錄的 `jobs:`,實例裡才不會多一份要長期維護的格式。

### 3.2 模組(核心 vs 選配)

**不做新的 plugin 框架**:平台已經有兩個組合機制——登錄的 `jobs:`/`services:` 跟排程器的 `after:`。模組就是
「一份跟專案登錄同格式的宣告 + 它需要的設定段」:

```yaml
# modules/assistant/module.yaml(平台 repo 裡;hub.json 的 modules.assistant 開了才採用)
v: 1
name: assistant
requires_config: [assistant.api, assistant.ingest_token]
jobs:
  - {name: bridge, every: 15m, run: bin/hubbus bridge}
  - {name: knas-tasks, at: "03:30", after: [agent-hub/librarian-core], before: [agent-hub/todo], run: bin/knas-tasks.py}
  - {name: reconcile-questions, at: "03:30", after: [agent-hub/librarian-core], run: bin/hubbus reconcile-questions}
services: []
status_sections: [to_owner_backlog]      # 狀態頁要多顯示的區塊(gen-status-page 照名稱找)
```

- 排程器的 job 來源變成:專案登錄 + 開著的模組。job 名稱 `<模組>/<job>`;`after:` 跨模組照樣能指。
- `before:` 是新的(現在只有 `after:`):讓模組把自己插進核心流程中間,不用核心反過來知道它。實作上排程器把
  `before: [X]` 轉成 X 的 `after:`。**(r2)** 要有循環偵測(驗證時就擋,狀態頁紅);`after:`/`before:` 指到**沒開的模組**的
  job 當成不存在——核心 job 不能因為助理沒開就永遠等。兩個情境都進測試。
- **librarian 拆開**:`librarian-core`(逐一整理 STATE.md + digest + index 的判斷)留在核心;`todo`(gen-todo)、
  `memory-gaps`、`index-roster`、`commit` 變成核心的獨立 job;`knas-tasks`、`reconcile-questions` 進 assistant 模組。
  memory-gaps 現在會寫進 librarian 當天的 digest——拆開後改成 digest 由 `librarian-core` 開檔、其他 job 用一個
  小函式 append 自己的段落(`hublib.digest_append(<段落名>, text)`,同名段落覆寫,重跑冪等)。
- 第一批模組:`assistant`(Muninn)、`alerts`(Discord;介面 = `notify(level, title, body)`,`alert.py` 已經是那個
  形狀)、`backup`(restic;目標由設定決定)、`mirror`(GitHub mirror)、`reflect`(每週回顧、軌跡)、`consolidate`、
  `sandbox`(第二階段強制)。
- **(r2) 模組的範圍**:
  - `assistant` = knas-tasks、reconcile-questions、hubbus-bridge、**lease-runner 服務、想法沙盒**(create-idea-sandbox /
    upgrade-idea / project-rename 端點)、show_file、`contracts/vendor/muninn/` 跟 `contracts/lease-runner.v2`——它們存在的
    唯一理由是助理。契約本身是中立的(租約 v2 已經沒有 runtime 用語),所以 runner 的程式碼留在平台 repo,模組只決定
    要不要起服務、排 job。
  - `sandbox` = `enforce.sandbox` 的渲染(render-project-settings)+ 能力(cap-runner、`capabilities/`、hub.json
    `capabilities`)+ `enforce.work` 的意義。**登錄的 `enforce` 欄位驗證留在核心**:沒開模組也要讀得到登錄,不能因為
    欄位不認得而整份登錄失敗。
  - **信箱的 dispatch 是核心**(headless agent、分支接續):專案之間的信沒有助理也要有人收。沒開 assistant 時,只有
    `--to owner` 的信改成進狀態頁跟 digest。
- 模組跟 runtime 的差別:runtime 是「同一個能力、換實作」(Strategy);模組是「要不要這個能力」(開關)。
  警報同時是兩者:`alerts` 模組開著,`alerts.backend: discord` 選實作。

### 3.3 機器角色

- `hub.json` 的 `hosts`:`{"memory": "local", "scheduler": "local", "remote_control": null}`;值是 `local` 或 ssh host 名稱。
  `hubconf.host(role)` 取代所有 `hub-host`;`minilib.sh` 的「在 mini 上就本機做、否則 ssh」改成照角色判斷。
- `memory_host`、`hosts.mbp_home`、`hosts.mini_home` 併進這裡;`hublib.MBP_HOME/MINI_HOME` 只剩讀舊對話紀錄
  (`legacy-paths.tsv`)用。
- 平台自己的 launchd plist 跟專案服務一樣由 `render-services.py` 照樣板產生(路徑從 hubconf),`launchd/` 目錄只剩樣板。
  **(r2) 例外:排程器跟看門狗的 plist 由 `bin/hub init`/`bin/setup` 裝**,不經過 render-services——render-services 是排程器
  跑的 job,而排程器壞了正是看門狗要報的事。看門狗維持用系統內建的 python3、不靠平台 Python(`test_python_floor` 的
  EXEMPT 跟它的 3.9 檢查不變)。
- launchd 是邊界:排程跟服務都經過 `render-services.py` 跟 `scheduler.py`,哪天要 systemd 就是那兩支多一個實作。
  **現在不做 Linux。**

### 3.4 單一入口

`bin/hub <指令>`:薄薄一層分派,不重寫既有腳本。`init`、`doctor`、`onboard`、`recover`、`rename`、`status`、
`run <job>`、`jobs`(現在 `schedulectl` 的功能)、`modules [enable|disable] <名稱>`。`hub --help` 列出全部,
每個指令一行說明——新使用者跟新 session 都從這裡找,`CLAUDE.md` 的索引縮短成「先跑 `bin/hub --help`」。

### 3.5 個人名稱的機械檢查(ratchet)

`tests/test_personal_boundary.py`:掃 `bin/`、`templates/`、`launchd/`、**(r2)** `tests/`、`agent-jobs/`(測試資料裡也有,開源前一樣是外洩),規則 `Knas|knas|Muninn|muninn|project-30|/Users/you|/Users/you|imaknas`。
- 程式(字串、識別字)出現就失敗,例外寫在 allowlist(例:`hubbus` 認 `knas` 別名是協定的一部分——但值要從
  `owner.aliases` 讀,所以 allowlist 應該很快變空)。
- 註解裡的決策紀錄(「10-24 Knas 決定」)先記一份現況基準:每個檔的出現次數**只准變少**。改寫成「owner 決定」
  或移進 docs/ 的決策紀錄是逐步的,不一次清。
- 跟 runtime 那份檢查(`test_runtime_boundary.py`)同一種寫法;每晚的 contracts-check 也跑。

### 3.6 全新使用者的驗收測試 (r2)

ratchet 只看字;真正的驗收是「全新的實例跑得起來」。第 6 步之後加一個測試,沿用 `bin/test-gate` 已經有的假 HOME +
暫存區副本:`AGENT_HUB_HOME` 指到只有 `hub.example.json` 的空實例,`bin/hub doctor` 跟一次排程器 tick 跑得起來,
而且沒有用到任何個人名稱或寫死的路徑(假 HOME 底下沒有 `~/workspace`、`~/git-origin`,用到就失敗)。在那之前,
每一步各自用現有的測試驗收。

## 4. 步驟(每一步單獨上線)

| 步 | 內容 | 動到的檔案 | 估計 |
|---|---|---|---|
| **0** | librarian 拆成核心 + job(`after:`;knas-tasks 跟 reconcile-questions 先當成獨立 job,第 3 步再歸進 assistant);路徑改從 hubconf;`hub.example.json` + 根目錄 README(新使用者路徑,還沒有的指令寫「規劃中」) | `librarian.sh`、`projects/agent-hub.yaml`、`hublib.py`(digest_append)、`check-memory-gaps.py`、新增 README、`hub.example.json` | 0.5 天 |
| 1 | 個人名稱 ratchet 測試 + 現況基準(含 tests/、agent-jobs/) | 新增 `tests/test_personal_boundary.py` | 0.25 |
| 2 | 機器角色:`hosts`、`hubconf.host()`;換掉 7 處 `hub-host`;平台 plist 改成產生(排程器、看門狗除外) | `hubconf.py`、`minilib.sh` 等 7 支、`render-services.py`(動之前先問沙盒設計 session)、`launchd/` | 1 |
| 3 | 模組:`modules/<名稱>/module.yaml`、排程器讀模組、`before:`(循環偵測、沒開的模組當不存在);assistant(含 lease-runner、想法沙盒)、alerts、sandbox | `scheduler.py`、`registry.py`(不動 validate_enforce)、`gen-status-page.py`、新增 `modules/` | 1.5 |
| 4 | owner 名稱從設定讀:待辦標籤、prompt 裡的名字;`templates/CLAUDE.md` → `agent-instructions.md` 改名(保留 10-26 加的兩段)跟裡面的名字;`mailbox.md` 搬到 `templates/prompts/`。hubbus 裡的字樣跟 Agent Hub session 約時間一起改 | `gen-todo.py`、`librarian.sh`、`consolidate-memory.py`、`reflect_llm.py`、`trajectory.py`、`templates/`、`sync-local-setup.sh`(讀樣板那行) | 0.5 |
| 5 | `bin/hub` + `init`(含裝排程器、看門狗的 plist)+ `doctor` | 新增 `bin/hub` | 1 |
| 6 | 先把 `schedule.yaml` 清到 0 項;實例目錄:`AGENT_HUB_HOME`、`hubconf.data()`、讀寫使用者資料的腳本改用它、`commit-hub-files.sh` 改 commit 到實例;Knas 的資料搬過去;加 3.6 的空實例驗收測試 | 幾乎所有寫產出的腳本 | 2 |

順序的理由:0、1 不碰別人的檔、馬上有用;2–4 是新使用者真的會撞到的;6 範圍最大、要 Knas 決定資料怎麼放,放最後,
而且前面每一步都已經把路徑收進 hubconf,6 就只是改 hubconf 的預設值加搬檔。

## 5. 跟其他 session 的邊界

- **runtime 抽象化**(`runtime_*.py`、`llm.py`、`hub-event`)跟**租約**(`lease-*`、`policy.py`、通道租借的契約 v2、
  `lease-show-file.md`)是另一個 session 正在做的;這份的步驟都不動那些檔。
- **(r2) Agent Hub session 的檔,這份也不動**:`bin/hubbus`(dispatch/對帳/分支合併還在試行;給 agent 看的提示由它改)、
  `bin/py`、`bin/setup-python`、`bin/test-gate`、`githooks/`、`tests/test_python_floor.py`、`bin/upgrade-idea.py`、
  `registry.py` 的 `validate_enforce`。
- **(r2)** `scheduler.py`、`render-services.py`、`gen-status-page.py` 沒人在動,這份可以拿;render-services 最近是沙盒設計
  session 改的,動之前先問它。
- **(r2)** `templates/CLAUDE.md` 的改名歸這份(第 4 步),runtime 那邊不做了。
- `hub.json` 的結構(加 `hosts`、`modules`)在第 2、3 步;別的 session 都即時讀它,只加欄位、不改既有欄位的意思,
  照「加欄位不升版」的規則。第 0 步不動 `hub.json`。

## 6. 不在範圍

- Linux / systemd(launchd 只標成邊界)。
- 第二個真的 agent runtime(runtime-abstraction 那份已經說不做)。
- 一台機器多個使用者、圖形化安裝。
- 把 Muninn 本身做成通用的個人助理——這份只讓「沒有助理」也能跑;assistant 模組的介面是 Muninn 定義的那幾份契約。

## 7. 要 Knas 確認的

1. 整體方向:實例目錄 + 模組(沿用登錄/`after:`,加 `before:`)+ 機器角色 + `bin/hub` + ratchet 測試。
2. 步驟順序,以及第 0 步可以先做(它本身就是上一輪談的「librarian 拆開」)。
3. 實例資料怎麼放:(a) 同一個 repo 的子目錄、(b) 另一個 git repo(例:`agent-hub-data`,private)、(c) 不進版控。我建議 (b):
   平台 repo 才能真的公開,而 digest/todo 還是有歷史可查。
4. 註解裡的決策紀錄(「Knas 決定」)要不要在開源前全部改寫,還是 ratchet 慢慢清就好。
5. ~~`contracts/vendor/muninn/` 算平台還是 assistant 模組~~ **(r2)** 跟 Agent Hub session 談定:歸 assistant 模組(見 3.2)。

## 8. 討論紀錄

**10-26 Agent Hub session 審查 r1**(方向同意:實例目錄 + 模組沿用 jobs/after + 角色 + bin/hub + ratchet)。採納的:
- 邊界補上它的檔(第 5 節);hubbus 裡給 agent 看的提示由它改,第 4 步改名字時約一起。
- `before:` 要有循環偵測、指到沒開的模組當不存在,進測試。
- assistant 模組包含 lease-runner、想法沙盒、show_file、vendor/muninn、租約 v2 契約;程式碼留平台 repo。
- sandbox 模組的範圍;`enforce` 欄位驗證留核心;信箱 dispatch 是核心。
- `agent-jobs/mailbox.md` 是平台的 prompt,不進實例。
- 平台 Python、匯流排是「每台機器一份」,不進實例。
- 看門狗跟排程器的 plist 不經過 render-services,由 `bin/hub init` 裝;看門狗維持系統 python3。
- 加「空實例跑得起來」的驗收測試(3.6),比 ratchet 直接。
- ratchet 範圍加 `tests/`、`agent-jobs/`。
- 搬家前 `schedule.yaml` 先清到 0 項。
- `templates/CLAUDE.md` 改名歸這份第 4 步。
