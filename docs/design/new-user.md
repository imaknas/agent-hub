# 新使用者:從零到第一次夜間整理,以及往這個方向要改的架構

> agent-hub session · 2026-10-26 · 只是設計,Knas 確認前不動手(第 0 步除外,見「步驟」)。
> 接續 `.state/phase1-design.md`(「平台**程式碼**裡沒有專案名稱」)跟 `runtime-abstraction.md`(「Claude Code 只是一個實作」),
> 不重談那兩份定案的事(集中登錄、`registry_dir`、`push_on_merge` 預設 false、ports & adapters)。
> **r2(10-26)**:照 Agent Hub session 的審查改過(方向同意;邊界、模組範圍、watchdog、驗收測試),改動處標 **(r2)**,
> 討論紀錄在第 8 節。
> **r3(10-26,已經 Agent Hub session 複審,意見併入,見第 8 節)**:跟 Knas 定了 code / config / data / secret 的分類跟「git 只放 code 跟
> config」(3.0);實例目錄因此拆成 config repo + data 目錄(3.1 改寫);新增 config 的 pipeline(3.7)跟對應的步驟。

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
| 2 | 沒有範例設定:`hub.json` 就是 Knas 的設定(`owner.tag: Knas`、`assistant.project: muninn`、`backup.repo: b2:<bucket>:…`、`memory_host: <hub 那台的 ssh 名稱>`、`github.owner`) | `hub.json` |
| 3 | 程式碼跟資料混在同一個 repo:clone 下來就拿到 Knas 的 `projects/*.yaml`、`digest/`、`reflect/`、`index.md`、`todo.md`、`review-log.md`、`schedule.yaml`、`mirror-list.txt`、`services.tsv`、`legacy-paths.tsv`、`openclaw-migration-status.md`、`agent-jobs/` | repo 根目錄 |
| 4 | 平台的 launchd plist 寫死兩台機器的家目錄路徑 | `launchd/*.plist`(7 份) |
| 5 | 寫死第二台機器的 ssh 名稱 | `minilib.sh:9`、`recover-project.sh:21`、`sync-all-memory.sh:34`、`transcript-backup.sh:20`、`trajectory.py:36`、`migrate-to-mini.py:42`、`openclaw-session-backup.sh:28` |
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

### 3.0 code / config / data / secret (r3)

現在的 repo 裡 git 同時做三件事:**版本控制**(程式碼)、**紀錄「什麼時候做了什麼」**(librarian、consolidate、reflect
每晚自動 commit digest/todo/review-log;最近 200 個 commit 有 57 個是這種)、**傳輸**(各專案 `.agent-memory` 經過 bare repo
在 workspace 跟 librarian 副本之間同步)。第二種是用錯工具:「什麼時候做了什麼」該由紀錄本身的時間跟匯流排的事件回答。

**規則:git 只放 code 跟 config;data 永遠不進 git。** 唯一的例外是 memory(見表)。

| 類別 | 定義 | 存在哪 | 怎麼追蹤 | 備份 |
|---|---|---|---|---|
| **code** | 刻意寫的、每個使用者都一樣 | 平台 repo | git;每個 commit 是一次有意的修改,過 `test-gate` | GitHub(+ mirror) |
| **config** | 使用者對「平台在我這裡怎麼運作」的宣告;期望的狀態 | 使用者的 config repo(private,要不要 push 由他決定) | git(config as code):每次修改是一個 commit,包括自動流程改的;過 3.7 的 pipeline;匯流排記「套用了哪個 commit」 | 每晚加密備份(remote 是選配) |
| **data:紀錄** | 只增不改的歷史 | data 目錄 | 紀錄本身帶時間;事件在匯流排 | 每晚加密備份 |
| **data:衍生物** | 隨時可以從別的東西重新產生 | data 目錄 | 不追蹤 | 不備份(重跑就有) |
| **data:執行狀態** | checkpoint、排程狀態、鎖、log | data 目錄 | 不追蹤 | 重建不回來的才備份 |
| **secret** | 金鑰、token | secrets 目錄 | 登錄只寫名稱、位置、到期(`check-secrets.py`) | 每晚加密備份 |

**例外:各專案的 memory**(`.agent-memory` 的筆記、STATE.md)是 data:紀錄,但用 git 當**傳輸**:session 跟 librarian
不在同一個時間、可能不在同一台機器,git 給了衝突處理跟離線 commit。它在每個專案自己的 memory repo(bare repo 的
`agent-memory` 分支),不跟 code 或 config 混在一起。這份不改它;要換傳輸方式是另一件事。

**現在的東西各是什麼**:

| 現在的位置 | 類別 |
|---|---|
| `bin/`、`templates/`、`contracts/`(含 vendor 的別人契約)、`capabilities/`、`tests/`、`docs/`、`githooks/`、`launchd/` 的樣板、`agent-jobs/mailbox.md`、`known-issues.md`、`CLAUDE.md` | code |
| `hub.json`、`models.env`、`projects/*.yaml` + 使用者 job 的 prompt、`mirror-list.txt`、`services.tsv`(手寫的服務清單,含非平台的)、`schedule.yaml`(切換期間的舊格式,清到 0 項後刪掉) | config |
| `digest/`、`review-log.md`、`reflect/*.json`、`openclaw-migration-status.md`、`index.md` 裡 LLM 寫的註記、**(r3)** `legacy-paths.tsv`(「以前在哪」的歷史,rename-project.sh 自動寫,不是期望的狀態) | data:紀錄 |
| 匯流排(`bus_dir`) | data:紀錄,**每台機器一份、不在實例裡**;有備份(`backup.py` 用 SQLite backup API 另外抓 `hubconf.bus_db()`,不靠 include——CLAUDE.md「沒有備份」是舊的) |
| `todo.md`、`index.md` 的名冊區塊、`reflect/index.html`、狀態頁、`.state/registry/` 快取 | data:衍生物 |
| `.state/` 其餘、`.logs/` | data:執行狀態 |
| `.secrets/` | secret |
| **(r3) 放錯地方的**:`.state/phase1-design.md`、`phase2-design.md`、`backup-design.md`、`lease-design.md` | code(docs):搬家前先移到 `docs/design/` |
| **(r3)** `.state/` 裡 zprofile/zshenv/claude-settings 的 `.bak` | secret 等級:放 `secrets/` 或刪 |

`index.md` 是一個檔混了兩類(機械產生的名冊 + LLM 寫的註記),拆成兩個檔。

**(r3) 衍生物不代表位置可以隨便變**:`todo.md`(契約 todo-md v1)、`reflect/`(reflect-week v1)有外部讀者用路徑讀,
搬的時候要跟讀者同一步(見 3.1)。

**(r3) `.state/` 裡重建不回來、或重建很貴的,要明列進備份**:

| 檔案 | 丟了會怎樣 |
|---|---|
| `<專案>.sha`(librarian checkpoint) | librarian 把全部 memory 重讀一次:成本 + digest 重複 |
| `scheduler-last-run.json` | 到期判斷重跑:backup、agent job 多跑一輪、花錢 |
| `idea-upgrades.json`、`project-renames.json` | 做到一半的進度,重試接不上 |
| `knas-tasks-inferred.json` | 推斷過的 done 重寄 |
| `lease-runner/`(`leases.json` + 每個租約給過的檔案) | Muninn 會 GET 舊的 file id |
| `alerts-sent.json` | 警報重發(小事) |
| `agent-jobs/` 的報告 | 報告不見 |

**(r3) 只有 git 有的歷史會消失**:`todo.md` 每天整份覆寫、`index.md` 的 LLM 註記,過去的版本現在只在 git log 裡。
離開 git 之後,歷史粒度變成備份的保留規則(14 天每天、8 週每週、12 月每月)。**刻意接受**這個粒度;只有 `index-notes.md`
改成附加式(每段帶日期),註記的歷史留在檔案裡。

**自動流程改 config**(onboard、rename、`manifest.update`、upgrade-idea):驗證過就直接 commit 到 config repo,不等人核可;
安全欄位(`type`/`enforce`/`network`/`allow_write`/`privacy`)維持只有人能改(現在就是)。這是版本控制,不是活動紀錄:
commit 的是「期望的狀態變了」。

**「什麼時候做了什麼」**統一看匯流排事件跟紀錄本身(digest 的日期、review-log 的條目);git log 不再承擔這件事。

### 3.1 實例目錄 (r3 改寫)

- `hubconf` 多一個概念:**實例目錄**(`AGENT_HUB_HOME`,預設 = hub 根目錄,也就是今天的樣子,所以切換前什麼都不變),
  底下三塊,各自可以用設定指到別處:

  ```
  <AGENT_HUB_HOME>/
    config/     ← config repo(git;`hub init` 會 git init,沒有也能跑,平台只是不 commit)
      hub.json  models.env  mirror-list.txt  services.tsv  legacy-paths.tsv
      projects/<name>.yaml  projects/<name>/jobs/*.md
    data/       ← 不進 git;每晚備份(衍生物除外)
      digest/  reflect/  review-log.md  todo.md  index.md  index-notes.md  .state/  .logs/
    secrets/    ← 不進 git;每晚加密備份
  ```

  `hubconf.config(<名稱>)`、`hubconf.data(<名稱>)` 取代各腳本自己拼 `$HUB/digest`、`$HUB/projects`。
- 平台 repo 留:`bin/`、`templates/`(含 `templates/prompts/mailbox.md`)、`contracts/`、`capabilities/`、`tests/`、`docs/`、
  `hub.example.json`、`projects.example/`、README。
- `commit-hub-files.sh` 改成只 commit **config repo** 的檔;librarian、consolidate、reflect、gen-todo 這些寫 data 的不再
  commit(它們現在 commit 的 digest/todo/index/review-log/reflect 全部是 data)。
- **備份範圍要跟著改**:現在 `hub.json` 的 `backup.include` 只有 `.state`、`.secrets`,因為登錄跟 digest 在 git、push 到
  GitHub 算有另一份。data 離開 git 之後,備份一定要包含整個 `data/`(衍生物除外)跟 `config/`,不然登錄跟紀錄就沒有任何
  備份。
- `known-issues.md` 是平台的知識,留在平台 repo,但改寫成不靠「mini/MBP」的說法;`openclaw-migration-status.md`、
  `legacy-paths.tsv`(都是 data:紀錄)是 Knas 的,搬進實例的 `data/`。
- **(r3) 外部讀者用路徑讀,搬檔要跟它們同一步**(或過渡期在舊位置留 symlink):
  - Muninn 的 `~/services/muninn/data/config.yaml`:`external_todos: ~/agent-hub/todo.md`、`weekly_stats: ~/agent-hub/reflect`、
    `lease_runner_token_file: <hub>/.secrets/runner-token`(`project_states` 讀 `~/.memory-copies`,不受影響)。
    契約(todo-md、reflect-week)不變,只有路徑。
  - `contracts-check` 讀 `todo.md`、最新的 reflect json:改成 `hubconf.data()`。
  - Knas 在 MBP 看報告:MBP 的 agent-hub 只 pull,離開 git 後就看不到 digest、reflect,改成看狀態頁(見第 7 節第 7 題)。
- **(r3) 靠 git 歷史做判斷的**:`gen-todo.py:107` 的註解(「要知道哪天產生的看 git log」→ 改成看 mtime 或 digest);
  weekly-reflect 數各專案 repo 的 commit,agent-hub 這份少掉 librarian 的自動 commit 反而更準,`hublib` 判斷「哪些 commit
  算專案工作」的規則可以順便簡化。hubbus、租約不讀 `index.md`;knas-tasks 讀 review-log 是讀檔、不靠 git。
- **(r3) secrets 的路徑**:8 支程式直接拼 `hubconf.ROOT / ".secrets"`(alert、backup、check-secrets、gen-status-page、
  lease-runner、lease-relay、policy、scheduler-watchdog)。搬之前全部改成 `hubconf.secret(<名稱>)`,不然搬完就壞;ratchet
  測試加一條:`bin/` 裡不准出現 `".secrets"` 字面值(看門狗用系統 python3、不 import hubconf,它那一份列例外,自己讀
  hub.json)。
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
  `hubconf.host(role)` 取代所有寫死的 ssh 名稱;`minilib.sh` 的「在 mini 上就本機做、否則 ssh」改成照角色判斷。
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

`tests/test_personal_boundary.py`:掃 `bin/`、`templates/`、`launchd/`、**(r2)** `tests/`、`agent-jobs/`(測試資料裡也有,開源前一樣是外洩),規則是 owner 跟助理的名字、機器名稱、兩台機器的家目錄路徑、帳號名稱(實際的 pattern 在測試裡)。
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

### 3.7 config 的 pipeline (r3)

config as code 要有一條 pipeline。現在零件大多有,但是散的:

| 階段 | 現在有的 | 缺的 |
|---|---|---|
| 驗證 | `registry.py` 每次載入驗證(排程器每個 tick),不合格沿用上一份合格的、狀態頁紅;`manifest.update` 驗證過才寫 | commit 時不驗證(`test-gate` 刻意略過登錄這類檔案,手改要等下一個 tick 才知道錯);`hub.json`、`models.env`、`mirror-list.txt` 沒有驗證 |
| 預覽 | `render-services.py --dry-run`、`render-project-settings.py --report` 各自能看 | 沒有一次看全部「這次修改會讓排程、服務、專案設定各變成怎樣」 |
| 套用 | 排程器每個 tick 讀登錄;`render-services`、`render-project-settings` 每 15 分鐘;`sync-local-setup` 在 session 開始 | 分散在幾個各自計時的 job,改完最多等 15 分鐘;沒有「現在就套用」 |
| 偏移偵測 | `render-project-settings` 被改就寫回、狀態頁紅;`render-services` 服務沒在跑發警報;`contracts-check` 每晚 | 不知道現在生效的是哪一版 config |
| 退回 | 每個專案有「上一份合格的」快取 | 沒有明確的退回,只能手動 revert 再等下一輪 |

**目標**(大多是把現有的串起來,`bin/hub config <子指令>`):

1. **check**:一個入口驗證所有 config 檔——登錄(`registry.py check`)、`hub.json`(新的 `contracts/hub.v1.schema.json`)、
   `models.env`、`mirror-list.txt`、`services.tsv`、模組宣告(循環、指到不存在的 job)。config repo 的 pre-commit 跑它;
   載入時照樣再跑(不合格沿用上一份合格的,這個行為不變)。
2. **plan**:叫每個套用者用 dry-run 回報會改什麼,合起來印出來。套用者 = 實作一個小介面的模組:
   `plan(config) -> [變更]`、`apply(config) -> [變更]`(現在的 render-services、render-project-settings、排程器的 job 清單、
   sync-local-setup 的受管區塊各是一個)。新的模組要產生東西,就是多一個套用者。
   **(r3) 跟 runtime_ports 同一種寫法**:`APPLIERS` 登記表 `{名稱: 模組}`,`load()` 機械檢查函式在不在;`Change` 是中立的
   NamedTuple `(op: add|update|remove, target, detail)`。一份契約測試對每個套用者跑:① plan 沒有副作用(假 HOME + 檔案
   快照前後一樣)② apply 回傳的 == 剛才 plan 的 ③ apply 完再 plan 是空的(冪等)。沒過契約測試的套用者進不來。
   - **排程器的 job 清單**:每個 tick 自己讀登錄,`apply` 是 no-op,`plan` 回報 job 的增減。
   - **sync-local-setup**:範圍是「一個 workspace」(`--workspace X`;session 開始的 hook = `apply --only local-setup
     --workspace <這個>`)。它有對外動作(commit+push AGENT.md 到 memory repo),plan 不能做——把「AGENT.md 對齊」拆成另一個
     套用者,登記時標明 `external: true`(plan 只回報,apply 才 push)。
3. **apply**:立刻跑一次所有套用者;每 15 分鐘那一輪留著當安全網(它本來就是冪等的)。
4. **verify**:套用後檢查服務健康、受管的檔案沒有偏移(現有的檢查集中成一個)。
5. **record**:匯流排記 `config.applied {commit, 變更清單}`;狀態頁顯示目前生效的 config commit、跟 config repo 的 HEAD
   不一樣就標黃(改了還沒套用)。

**退回** = 在 config repo `git revert` + `hub config apply`。**自動流程**(onboard、`manifest.update`…)寫 config 也走同一條:
check → commit → apply,不另外一套。

**(r3) commit 閘門**:新增 `bin/config-check`(不做成 test-gate 的分支——test-gate 只管「程式碼過測試」)。
`githooks/pre-commit` 依序叫 `test-gate` 跟 `config-check`。要求:① config-check 驗**暫存區**的內容(照 test-gate 的
`checkout-index` 匯出),不是 working tree——共用的 working tree 裡可能有別人改到一半的;② post-commit 的「跳過閘門」紀錄
也要涵蓋 config 路徑(config-check 通過時寫自己的標記檔)。config 搬到自己的 repo 之後,那個 repo 有自己的 hooksPath,
由 `hub init` 設。

## 4. 步驟(每一步單獨上線)

> **進度**:第 0 步 10-26 完成(librarian 拆成核心 + 5 個 job、digest 分段、路徑從 hubconf、README、`hub.example.json`)。
> 第 1 步 10-26 完成:`tests/test_personal_boundary.py` + `tests/fixtures/personal-baseline.json`(個人名稱 76 個檔 499 處、`.secrets` 字面值 7 個檔 11 處)。
> 跟 3.5 不同:不分程式/註解,每個檔一個總數只准變少(分類要靠猜,總數 ratchet 一樣擋得住變多)。
> 第 1.5 步 10-26 完成:`bin/config-check`(+ `contracts/hub.v1.schema.json`)、`bin/appliers.py` + `applier_scheduler.py`、render-services / render-project-settings 包成套用者(重構前後 42 個產出檔逐位元組相同)、`tests/test_appliers.py`、`bin/hub config check/plan/apply`、`config.applied`(Agent Hub session 597883a)、狀態頁顯示生效的 config commit、githooks pre-commit/post-commit 涵蓋 config。**還沒做**:sync-local-setup 的套用者(session 開始時跑的那支,AGENT.md 對齊要拆成 EXTERNAL 的套用者)——它本來就在每個 session 開始時對齊,沒有 plan 只是看不到預覽,留到第 4 步改樣板時一起做。
> 第 2 步 10-26 完成:只用一個角色 `hosts.server`(跑 hub 的那台;空 = 一台機器全包;沒寫沿用 `memory_host`),`hubconf.host("server")` / `hubconf.py host server`,shell 用 `plutil` 讀(遙控那台不一定有平台的 Python)。7 處寫死的 ssh 名稱換掉。狀態頁、信箱 dispatch、memory 同步搬進 `projects/agent-hub.yaml` 的 `services:`(用 `hub config plan` 先看、再 apply)。`launchd/` 只剩排程器、看門狗(第 5 步由 `hub init` 裝)跟遙控那台的兩份。個人名稱 499 → 479。
> 第 3 步 10-26 完成:模組 = `modules/<名稱>.yaml`(跟登錄同格式,`registry.load_modules()` 放進 `index["modules"]`),
> 排程器跟 render-services 讀專案 + 模組;`before:`、繞圈檢查(`registry.job_cycles`)、沒開的模組的 job 不存在。
> 搬進模組:assistant(knas-tasks、reconcile-questions、lease-runner)、backup、consolidate、mirror、reflect、sandbox(cap-runner),
> 用 `legacy_name` 帶著排程狀態、別人的 `after:` 經 alias 對得上(muninn 的 `after: agent-hub/weekly-reflect`)。
> **跟 3.2 不同、留在核心的**:hubbus-bridge(除了 Muninn 的轉交,還清 60 天前結案的信——要拆成兩半是 hubbus 那邊的事)、
> render-settings(enforce 關掉時的退回、合併閘門的 hook 也是它)、各模組的憑證宣告(還在 `projects/agent-hub.yaml` 的
> `secrets:`)、vendor/muninn 契約的 `consumes:`(contracts-check 只讀專案登錄)。
> 第 4 步 10-26 完成(平台這邊):`templates/CLAUDE.md` → `templates/agent-instructions.md`,人名寫成 `{{owner}}`/`{{owner_alias}}`/`{{assistant}}`,sync-local-setup 照 hub.json 填(`hubconf.fill_names`;24 個專案的輸出逐位元組相同);`hub.json` 加 `assistant.name`;gen-todo、librarian、consolidate、weekly reflect、trajectory 的標籤跟 prompt 改從設定讀(這台的 prompt 逐字相同)。個人名稱 479 → 407。**交給 Agent Hub session**:hubbus 裡的字串、`agent-jobs/mailbox.md` 搬到 `templates/prompts/`。**還沒做**:knas-tasks.py 的檔名/job 名(改名要跟 Muninn 的契約、STATE.md 裡已經蓋上的「等 Knas:」key 一起考慮,先留著)。
> 第 5 步 10-26 完成:`bin/setup`(檢查指令 + setup-python,不自己裝)、`bin/hub` 的 init(沒有 hub.json 才寫、問名字/時區/專案位置/模組;沒開的功能連設定段一起拿掉;排程器跟看門狗從 `launchd/` 的樣板裝,內容一樣就不動)、doctor(用 runtime 的 `auth.credentials()`,排程器要是**這份** hub 的)、modules、onboard/recover/rename/status/jobs/run 包裝。`launchd/` 的 plist 改成 `{{hub}}` 樣板(recover-service 用 sed 填)。沒設定 alerts 的話,看門狗只記錄、狀態頁只提醒,不再永遠紅。`tests/test_new_user.py` = 3.6 的驗收(空實例 + 假 HOME:init → config-check → plan → doctor)。**第 6 步要處理**:平台自己的登錄 `projects/agent-hub.yaml` 還在使用者的登錄資料夾裡(新使用者也需要它),要跟 modules/ 一樣搬回平台。
> 第 6 步 10-26 **程式碼完成、搬家還沒做**(等 Knas,見第 7 節第 9 題)。定下來的(跟 3.1 不同的地方):
> - **一個指標檔切換**:`<hub>/.hub-home` 寫著實例目錄 → `<實例>/{config,data,secrets}`;沒有這個檔 = 舊的擺法(今天的樣子)。
>   搬家時最後才寫,刪掉就退回。覆蓋:`AGENT_HUB_HOME`、`AGENT_HUB_CONFIG`/`DATA`/`SECRETS`(config repo 的 pre-commit 只把
>   `AGENT_HUB_CONFIG` 指到匯出的暫存區)。Python:`hubconf.config_dir()/data_dir()/secrets_dir()/config_file()/data_file()`;
>   shell:`bin/hubpaths.sh`(`$HUB_CONFIG`/`$HUB_DATA`/`$HUB_SECRETS`/`hub_server`,只用 plutil);看門狗自己一份(不 import 平台)。
>   三份一致由 `tests/test_instance_layout.py` 檢查。
> - **`.state`、`.logs` 不搬**:每台機器一份的執行狀態(跟 bus_dir、平台 Python 一樣)。`.logs` 的路徑寫在每個服務的 plist 裡,
>   搬它等於重啟所有服務;`.state` 裡有狀態頁、lease-runner、test-gate、registry 快取,都是這台機器的。
> - hub.json 裡的相對路徑以 hub.json 所在的 config 目錄為準;`.secrets/<名稱>` 一律指 secrets 目錄。
> - **平台自己的登錄**固定在平台 repo 的 `projects/agent-hub.yaml`,registry 不管 registry_dir 在哪都讀它。
> - `commit-hub-files.sh`:新擺法只在 config repo 裡 commit config,data 直接略過(data 不進 git)。
> - 新使用者一開始就是新擺法:`hub init` 建 `~/.agent-hub/{config(git repo,hooksPath = githooks/config),data,secrets}`,
>   最後寫指標檔(`tests/test_new_user.py`,含 config repo 的閘門擋下不合格的 hub.json)。
> - ratchet 多一條 `instance-path`(bin/ 裡自己拼 config/data 檔的路徑)。
> - 這台:42 個產出檔逐位元組相同、`hub config plan` 空、`hub doctor` 沒問題。
> **還沒做**:別的 session 的檔(lease-relay、lease-runner、upgrade-idea 的 `.secrets`/mirror-list;policy.py 的 `.secrets` deny——搬家時
> render-settings 會重寫 24 個專案的設定)已寄給它們;實際搬家。
> **10-26 23:40 搬家完成**(`hub move-instance --to ~/.agent-hub --apply`,先在隔離環境演練兩次、抓到兩個 bug 才做):
> 快照 + 退回腳本(`~/.agent-hub/secrets/agent-hub-move-snapshot-*/rollback.sh`,在搬過的隔離副本上實測過)→ 停排程器 → 搬 →
> `hub config plan` 發現舊的 `.secrets` 還在、deny 一換就沒人擋 → 沙盒設計 session 改 policy 兩個都擋(20e6467)→ apply(3 個 sandbox
> 專案的設定只多了新路徑)→ 重啟 lease-runner、cap-runner → 開回排程器;寫入都到 ~/.agent-hub、舊檔沒被碰。
> **還沒做(明天)**:確認 03:00 的夜間流程正常;Muninn 改 config(m10262338-f805);之後才 `git rm` 平台 repo 裡的舊檔、刪 `~/agent-hub/.secrets`
> (policy 會自動拿掉那條 deny);平台 repo 的 pre-commit 在舊檔刪掉之後要改成只驗平台自己的登錄。
> 狀態頁的三頁(第 7 節第 7 題選 (a))10-26 完成:`digest.html`(最近 7 天)、`todo.html`、`reflect/`(連到 data 的 reflect/),從 data 目錄讀。

| 步 | 內容 | 動到的檔案 | 估計 |
|---|---|---|---|
| **0** | librarian 拆成核心 + job(`after:`;knas-tasks 跟 reconcile-questions 先當成獨立 job,第 3 步再歸進 assistant);路徑改從 hubconf;`hub.example.json` + 根目錄 README(新使用者路徑,還沒有的指令寫「規劃中」) | `librarian.sh`、`projects/agent-hub.yaml`、`hublib.py`(digest_append)、`check-memory-gaps.py`、新增 README、`hub.example.json` | 0.5 天 |
| 1 | 個人名稱 ratchet 測試 + 現況基準(含 tests/、agent-jobs/) | 新增 `tests/test_personal_boundary.py` | 0.25 |
| **1.5 (r3)** | config pipeline:`config check`(含 `hub.v1.schema.json`)、套用者介面 + 把 render-services / render-project-settings 包成套用者、`plan`/`apply`/`verify`、`config.applied` 事件、狀態頁顯示生效的 commit。config 還在平台 repo 裡,pre-commit 先掛在現在的 `githooks/`(只在改到 config 檔時跑 check,不跑全部測試) | 新增 `bin/config-check`、`bin/appliers.py`(登記表 + 介面)+ 契約測試、`render-services.py`、`render-project-settings.py`、`sync-local-setup.sh`(拆出 AGENT.md 對齊)、`gen-status-page.py`、`githooks/pre-commit` 加一行 + `post-commit` 涵蓋 config(Agent Hub session 同意) | 1.5 |
| 2 | 機器角色:`hosts`、`hubconf.host()`;換掉 7 處寫死的 ssh 名稱;平台 plist 改成產生(排程器、看門狗除外) | `hubconf.py`、`minilib.sh` 等 7 支、`render-services.py`(動之前先問沙盒設計 session)、`launchd/` | 1 |
| 3 | 模組:`modules/<名稱>/module.yaml`、排程器讀模組、`before:`(循環偵測、沒開的模組當不存在);assistant(含 lease-runner、想法沙盒)、alerts、sandbox | `scheduler.py`、`registry.py`(不動 validate_enforce)、`gen-status-page.py`、新增 `modules/` | 1.5 |
| 4 | owner 名稱從設定讀:待辦標籤、prompt 裡的名字;`templates/CLAUDE.md` → `agent-instructions.md` 改名(保留 10-26 加的兩段)跟裡面的名字;`mailbox.md` 搬到 `templates/prompts/`。hubbus 裡的字樣跟 Agent Hub session 約時間一起改 | `gen-todo.py`、`librarian.sh`、`consolidate-memory.py`、`reflect_llm.py`、`trajectory.py`、`templates/`、`sync-local-setup.sh`(讀樣板那行) | 0.5 |
| 5 | `bin/hub` + `init`(含裝排程器、看門狗的 plist)+ `doctor` | 新增 `bin/hub` | 1 |
| 6 | 先把 `schedule.yaml` 清到 0 項;實例目錄 `config/` + `data/` + `secrets/`(3.1):`hubconf.config()`/`data()`、讀寫的腳本改用它;`commit-hub-files.sh` 只 commit config repo,寫 data 的腳本不再 commit;`index.md` 拆成名冊 + 附加式註記;`.state/` 的設計文件移到 `docs/design/`;secrets 經過 `hubconf.secret()`;**備份**:新路徑加進 include → **跑一次 `backup.py restore` 到暫存目錄,實際還原出 `config/`、`data/` 才算數**(不等每月的 backup-verify)→ exclude 換成新路徑(順便確認 `backup-staging` 不會被備份兩次)→ `check-secrets` 的 `covers()` 跟 `.secrets` 搬家同一個 commit;外部讀者(Muninn 的 config.yaml 三個路徑)同一步改或留 symlink;**在沒有 job 跑的時段搬**(避開 02:00–04:00 librarian、05:45 backup),或先停排程器、搬完 kickstart;Knas 的資料搬過去;加 3.6 的空實例驗收測試 | 幾乎所有寫產出的腳本、`backup.py`/`hub.json` 的 `backup`、讀 secrets 的 8 支 | 2.5–3 |
| **7 (r3)** | **開源前小心地重寫平台 repo 的歷史**(Knas 10-26 選的,見第 7 節第 8 題),只在第 6 步完成、tree 裡已經沒有使用者資料之後做:① 先做一份完整的 `--mirror` 備份(不動,出事就從這裡還原)② 在新的 mirror clone 上跑 `git filter-repo`,路徑清單明列(`digest/`、`reflect/`、`index.md`、`todo.md`、`review-log.md`、`projects/`、`hub.json`、`schedule.yaml`、`mirror-list.txt`、`services.tsv`、`legacy-paths.tsv`、`openclaw-migration-status.md`、`agent-jobs/` 的使用者 prompt、`.state/` 誤進版控的)③ 再掃一次內容(個人名稱、帳號、token 的字樣;用 ratchet 同一份規則 + 一個 secret 掃描工具),有命中就逐筆決定改寫或刪 ④ 驗收:重寫後 HEAD 的 tree 跟重寫前 HEAD 減掉那些路徑完全一樣、測試全過 ⑤ 保留 filter-repo 的 commit 對照表(文件、memory 筆記裡引用的舊 commit 編號查得到新的)⑥ 換掉之前停排程器、通知所有 session;mini 的 `~/agent-hub`、MBP 的副本、mirror-backup、各 worktree 全部重新 clone;force push 由 Knas 親自確認 | 平台 repo 的歷史(不改程式) | 1 |

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
3. ~~實例資料怎麼放~~ **(r3)** 10-26 跟 Knas 定了:config as code,config 放 private config repo;data 不進 git、靠備份;
   「什麼時候做了什麼」看匯流排跟紀錄本身(3.0)。
> **10-26 Knas 決定:6 同意;7 選 (a) 狀態頁多三頁;8 選 (a) 小心地重寫歷史(做法見第 4 步的第 7 步)。**
> **10-26 Knas 決定(第 9 題):(a) config repo 只留本機 git、不設 remote(靠每晚備份);(b) 搬家時間跟 (c) Muninn 設定照建議:先確認 10-27 03:00 拆開後的第一次夜間流程(librarian + 模組的 job)正常,再挑避開 02:00–04:00、05:45 的時段短暫全停後搬;Muninn config.yaml 的三個路徑在搬家當天寄信請 muninn session 一起換。**

6. **(r3) 1.5 步(config pipeline)排在模組(第 3 步)之前嗎?**(Agent Hub session 同意)
   第 2、3、6 步都在改 config 的**結構**:`hub.json` 加 `hosts`、`modules`,多出 `modules/*/module.yaml`,最後整批搬到
   `config/`。沒有 pipeline 的話,改錯的代價是「要等出事才知道」:
   - **範例 A(打錯字)**:開模組時在 `hub.json` 寫成 `"modules": {"assitant": true}`。今天的 hubconf 不驗證 `hub.json`,
     這個鍵只是被忽略——assistant 模組沒開,knas-tasks、bridge 都不排程。沒有任何錯誤訊息;幾天後才發現 Muninn 沒收到
     新的〔Knas〕任務。有 `config check`:commit 時就被擋,「`modules.assitant`:沒有這個模組(有:assistant、alerts…)」。
   - **範例 B(看不到影響)**:`modules.assistant` 從 false 改成 true。有 `config plan` 時,commit 前先看到:
     ```
     scheduler   + assistant/knas-tasks       03:30(after agent-hub/librarian-core,before agent-hub/todo)
     scheduler   + assistant/bridge           每 15 分鐘
     services    + local.agent-hub.lease-runner   (127.0.0.1:18797)
     ```
     沒有 plan 的話,只能 commit 之後等 15 分鐘看狀態頁。
   - **範例 C(今天就會發生的)**:手改 `projects/<專案>.yaml`,`run:` 打錯路徑。現在要等下一個 tick 才被
     `registry.py` 發現,狀態頁變紅、沿用上一份;有 pre-commit 的 `config-check` 就進不了 commit。
   反過來說,如果先做模組再做 pipeline,第 3 步改排程器、加 `before:` 的時候,就只能靠「等下一個 tick 看結果」來驗證。
9. **(第 6 步) 搬家前要 Knas 決定的**:(a) config repo 要不要 remote——新開一個 private GitHub repo(`gh repo create`,對外)
   或只留本機 git;(b) 搬家的時間:要短暫全停(排程器、lease-runner、cap-runner、信箱 dispatch、狀態頁都拿著路徑),
   不要在 librarian 02:00–04:00、backup 05:45 前後;(c) Muninn 的 config.yaml 三個路徑(todo、reflect、runner token)
   由 muninn session 改(寄信),同一個時段換。
7. **(r3) digest、todo 不進 git 之後,Knas 在哪裡看?**
   - **現在怎麼看**:狀態頁(tailnet 上的網址)只有一頁 `index.html`——job 成敗、🔔 報告、下次
     什麼時候跑;**沒有** digest、todo、每週回顧。這三樣現在只能在 repo 裡打開檔案看(session 裡,或 MBP pull 之後開
     `reflect/index.html`;Knas 沒有用 GitHub 看)。
   - **搬出 git 之後**:這三樣只在 mini 的 `data/` 裡,MBP pull 不到了。
   - **選項**:
     - (a) **狀態頁多三頁**:`/digest/`(最近幾天,markdown 轉成 HTML)、`/todo`、`/reflect/`(直接放現在的 `reflect/index.html`)。
       範例:早上在手機開狀態頁 → 上方的「今日 digest」分頁 → 看到昨晚 librarian 寫的「## <專案> …」;「待辦」分頁就是
       今天的 `todo.md`,〔Knas〕的項目排在最上面。gen-status-page 每個 tick 本來就在重生,多產生三頁的成本很小;
       只在 tailnet 打得開,跟現在一樣。
     - (b) **只看 Muninn**:Muninn 已經讀 `todo.md` 跟 `reflect/`(用路徑),可以在它的 PWA 裡顯示;digest 它現在不讀。
     - (c) **不提供**:要看就 SSH 進 mini。
   - 我建議 (a):不多依賴一個專案,新使用者沒有 Muninn 也看得到。
8. **(r3) 平台 repo 要公開的話,過去的 git 歷史怎麼辦?** 搬檔不會讓歷史消失——digest、todo、STATE.md 的引文、登錄、
   `hub.json` 都在過去的 commit 裡。兩個做法:(a) 重寫歷史(`git filter-repo` 拿掉這些路徑,所有 clone 要重拉);
   (b) 開源時從一個新的 repo 開始(第一個 commit 是當時乾淨的 tree),現在這個 repo 留著當 Knas 自己的私有歷史。
   我建議 (b):比較不會漏,也不影響現在的 mirror 跟 clone。
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

**10-26 Agent Hub session 複審 r3**(1.5 排在模組之前,同意)。採納的:
- `legacy-paths.tsv` 是 data:紀錄,不是 config;匯流排有備份(CLAUDE.md 的說法是舊的)。
- `.state/` 裡重建不回來、或重建很貴的 7 類明列進備份;放錯地方的設計文件、`.bak` 檔。
- 外部讀者用路徑讀:Muninn 的 config.yaml(我另外找到第三個:`lease_runner_token_file`)、contracts-check、Knas 在 MBP 看報告。
- 只有 git 有的歷史(todo、index 註記)會變粗:刻意接受,index-notes 改成附加式。
- 套用者照 runtime_ports 的寫法:登記表 + 機械檢查 + 三條契約測試;排程器 job 清單 apply 是 no-op;sync-local-setup 帶
  範圍參數,AGENT.md 對齊拆成有對外動作的套用者。
- `bin/config-check` 獨立,不是 test-gate 的分支;驗暫存區;post-commit 也涵蓋 config。
- 備份:加 include 後實際還原驗證、exclude 換路徑、`covers()` 跟 secrets 搬家同一個 commit、secrets 經過
  `hubconf.secret()`(加進 ratchet)、在沒有 job 的時段搬。
- 開源前的 git 歷史(第 7 節第 8 題)。
