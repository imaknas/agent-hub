# 執行環境抽象:平台每一層都是介面,Claude Code 只是一個實作

> **進度**(10-25):步驟 1(骨架)、2(第 5 層對話紀錄)、3(第 1 層「跑一次」)、5(第 3 層事件 `hub-event`、第 4 層規則檔
> `hub-runtime`)、6(第 6 層登入,介面 `auth`)完成,24 個專案都換成新的 hook。**還沒做**:第 1 層長駐 session(排在另一個
> session 的租約中立化之後,見 `lease-contract-neutral.md`)、判斷點宣告的中立化(等第 2 層的 approvals;順便加
> `platform_tools`)、`templates/CLAUDE.md` 改名、job 改名(`runtime-login`、`update-runtime`)。
> **第 6 層還留著的 Claude 名稱**:`lease-resume`(直接讀 token 檔、叫 `claude --resume`;跟長駐 session 的 `resume_command`
> 一起換)、`run-agent-job.sh` 跟 `lease-runner.py` 的 `unset/去掉 CLAUDE_CODE_OAUTH_TOKEN`(多一層保險,llm.py 已經經過
> `auth_env`;可以刪)、`classify_exit` 自己一份登入錯誤的判斷(之後改用 `is_login_error`)。
> **每一步的驗收**:`bin/test-gate`(commit 閘門)跟黃金檔——重構前先把外面看得到的行為固定成測試(第 6 層:
> `tests/test_check_login.py` 連警報字句都比、`tests/fixtures/llm-golden.json` 沒重生)。
> **還在用舊入口的兩處**(輸出跟以前一字不差,照樣能用):agent-hub 自己的 `.claude/settings.json`(它的 session 開始還要拉
> agent-hub 的 repo,`hub-event` 沒有這一步),跟使用者層級的 worktree hook(U4,要 Knas 親自改)。

## 目標

10-25 Knas:「這些都要變成接口,Claude Code 只是一個實作,每層都要有這樣的抽象以及泛化。」

現在 agent-hub 跟 Claude Code 的耦合散在六個地方(盤點見各層)。目標是:平台的程式只依賴**平台自己定義的介面**,
跟 Claude Code 有關的細節全部收進 `ClaudeCode*` 實作;要換工具、或同時用第二種(例如 Muninn 的本機模型、
其他 CLI),就是多寫一組實作,平台其他程式不用動。

## 原則

1. **ports & adapters 是結構,Strategy 是選實作的方式。** 每一層一個小介面(port,Python `Protocol`,跟 Muninn
   `core/ports.py` 同一種寫法);實作(adapter)是可替換的 Strategy,由設定決定用哪一個,呼叫端只拿到介面。
   不是每一層都長得像 Strategy:hook 那層是事件(平台定義事件、各 runtime 接上來),權限那層是翻譯。
2. **介面從平台的需要往下設計,不照 Claude Code 的功能往上抽。** 判斷標準:介面的名詞跟參數,換一個完全不同的
   工具還說得通嗎?說不通(例如出現 `allowedTools`、`--resume`、jsonl)就是把實作的形狀漏進介面了。
3. **至少兩個實作:Claude Code + 一個假的(Fake)。** 每個介面一組契約測試,兩個實作都要跑過。只有一個實作的
   介面一定會長成那個實作的樣子;假的實作也是平台其他程式的測試替身(今天信箱分流的測試已經這樣用)。
   真要換工具時,新的實作跑過同一組契約測試才算可以替換。
4. **一層一層換(strangler),不一次重寫。** 每一層:定介面 → 把現有的程式包成 `ClaudeCode*` 實作 → 呼叫端改成
   依賴介面 → 補契約測試 → 刪掉散在各處的 Claude 細節。每一步都能單獨上線、單獨回退。
5. **一個實作 = 一個模組,Strategy 選的是模組**(照 `bin/` 現在的寫法,模組層級的函式,不硬套 class):
   Claude Code 的實作就是 `bin/runtime_claude.py`(所有層共用;第 2 層的翻譯函式也在這裡,見下),假的實作是
   `bin/runtime_fake.py`。介面寫在 `bin/runtime_ports.py`(`Protocol`,只描述模組要提供哪些函式)。選哪個寫在
   `hub.json` 的 `runtime.default`(現在是 `claude`),必要時專案登錄或判斷點可以覆寫;平台程式用
   `runtime_ports.load(name)` 拿到模組,不直接 `import runtime_claude`。

6. **符合介面是機械檢查,不是靠約定**:`runtime_ports.load(name)` 檢查模組有沒有提供每個介面要求的函式,缺了就拒絕
   載入;契約測試從登記表自動找出所有實作來跑,不是手寫 `[claude, fake]`——第三個實作加進來不可能沒跑過契約測試。
7. **契約測試檢查語意,不是只看有沒有被呼叫**:例如分支接續之後原本對話的 context 大小跟紀錄完全不變、對話結束後
   `alive` 是 False、不認得的 id `context_size` 回 None、每次 `send` 剛好有一個 `turn_end`。所以 Fake 要真的記得狀態。
   Fake 也能從設定選(暫存 hub 裡 `runtime.default: fake`):librarian、信箱分流可以端到端跑、不花模型的錢。
8. **全系統只有一套「動作種類」(kind)**:讀、搜尋、問人、開網頁、寫檔、跑指令。第 1 層判斷點的政策、第 2 層的
   `approvals`、跟 Muninn 的權限契約都用同一組名稱,不各自取名。
9. **session 帶著它屬於哪個 runtime**:`SessionHandle = (runtime, id)`;信箱的在場紀錄、租約紀錄多一欄 `runtime`;
   `hubbus send` 的寄件 session 從 `runtime_ports.current_session()` 拿(現在直接讀 `CLAUDE_CODE_SESSION_ID`)。
   多個 runtime 可以同時登記、每個 session 用它自己的——是「並存、依 session 選」,不是「整個換掉」,舊的對話紀錄
   也一直讀得到。
10. **失敗政策留在介面上面**:`AgentRuntime.run` 只回中立的 `TaskResult`(成功/失敗、結構化輸出、成本、token);
   `on_failure`、`job.failed`、成本累計留在 `llm.py`,不讓每個 runtime 各自實作一套。

## 分層

檔案:`bin/runtime_ports.py`(介面 + `load`)、`bin/runtime_claude.py`(Claude Code 實作,所有層)、`bin/runtime_fake.py`
(假的實作)、`tests/test_runtime_contracts.py`(契約測試,兩個模組都跑)。下面各層的介面用 class 寫只是為了看得清楚,
實作是模組裡同名的函式。

### 1. AgentRuntime:讓一個 agent 做事

平台需要的:跑一次判斷點(給 prompt、工作目錄、政策、輸出格式,拿結果)、開一個長駐的對話(深談)、接續一個
既有的對話、在不動原本對話的情況下分支接續、這個對話還活著嗎、接續要載入多少 context、對話的事件串流。

```python
class AgentRuntime(Protocol):
    def run(self, task: Task) -> TaskResult: ...                 # 一次:prompt、cwd、policy、output_schema、timeout
    def open_session(self, spec: SessionSpec) -> SessionHandle: ...   # 長駐:id、cwd、policy、說明文字、能不能問人
    def send(self, h: SessionHandle, msg: Message) -> None: ...  # 文字 + 附件
    def events(self, h: SessionHandle) -> Iterator[Event]: ...   # 中立事件:text、tool_use、tool_result、turn_end、error
    def resume(self, sid: str, *, fork: bool, spec: SessionSpec) -> SessionHandle: ...
    def alive(self, sid: str) -> bool: ...
    def context_size(self, sid: str) -> int | None: ...
```

- 現在在哪:`llm.py`(`claude -p` 的旗標、輸出格式)、`lease-runner.py`(組旗標、解析 stream-json)、`runtime_claude.py`、
  `run-agent-job.sh`、`check-claude-login.py`/`update-claude.py` 呼叫 `claude`。
- `llm.py` 變成「判斷點 → Task」的一層:讀 `llm-points.yaml`、組 `Task`、交給 `AgentRuntime.run`,不再自己組 Claude 旗標。
  `llm-points.yaml` 裡 Claude 專用的欄位(`allowed_tools`、`permission_mode`、`tools`)改成中立的政策(交給第 2 層翻譯);
  判斷點可以宣告 `runtime`(選填,預設 `hub.json`)——這要改共用的 `llm-points.v1.schema.json`,跟 Muninn 同一天同步。
- 「能不能問人」(權限轉接、提問)是 `SessionSpec` 的一部分:平台提供一個中立的「問人」服務(現在的 lease-relay 背後
  那套:送 Muninn、等回覆),各 runtime 的實作負責把它接到自己的權限機制上(Claude Code 是 `--permission-prompt-tool`)。
- 事件串流用中立的事件型別;lease-runner 現在已經把 Claude 的格式轉成自己的事件(`assistant`、`tool_use`、`text_delta`),
  這段轉換搬進 `runtime_claude.events`。
- **長駐 session(深談)這部分這份只定介面,不寫實作**:租約的契約中立化(Muninn 的 m10251446:tool → kind 翻譯表、
  事件、配額、錯誤碼、權限轉接改看 kind)橫跨這層跟第 2 層,還沒決定由哪個 session 做;定案前兩邊都不動
  lease-runner 的旗標、stream-json、lease-relay。

### 2. PolicyCompiler:權限政策(另一個 session 負責)

中立的政策翻成各 runtime 的設定。設計與實作由「沙盒設計與 Claude Code 耦合」session 負責,見
`docs/design/capabilities.md`(Knas 確認前格式都可能變)。這份只引用、不另外定義:

- **中立政策**:`policy.policy(name, index) -> dict`,欄位 `workspace`、`write`、`deny_write`、`deny_read`、`credentials`、
  `network`、`platform_commands`、`capabilities`。
- **翻譯(= 這層的 Claude Code 實作,模組層級函式)**:`runtime_claude.settings(policy)`(專案的 Claude 設定)、
  `runtime_claude.lease_settings(policy)`(租約的設定)、`runtime_claude.sandbox_rendered(...)`(work 模式開放條件)。
  平台自己的 Seatbelt `seatbelt.profile(policy, cap)` 跟 runtime 無關。
- **「哪些動作要問人」**(`approvals`,以動作種類 kind 為 key:讀、搜尋、問人、開網頁、寫檔、跑指令)還沒定案,
  會定在 m10251446 那份設計;第 1 層的 `SessionSpec` 等它定了再接。
- `AgentRuntime` 的 `Task`/`SessionSpec` 帶的是中立政策,由對應 runtime 的翻譯函式轉成它的設定。

### 3. LifecycleEvents:平台的事件,各 runtime 怎麼接上來

平台需要知道的時機:session 開始(含壓縮後重新開始)、session 結束、要壓縮了、用完一個工具、使用者送出訊息,
以及在這些時機「把一段文字放進 agent 的 context」(信箱、memory 提醒)。

```python
class LifecycleBinding(Protocol):
    def install(self, workspace: Path) -> None: ...        # 把 runtime 的原生 hook 接到 `hub event <事件>`
    def parse(self, event: str, raw: bytes) -> HubEvent: ...   # 原生輸入 → 中立的 HubEvent(session、cwd、事件)
    def inject(self, event: str, text: str) -> bytes: ...  # 中立的「放進 context」→ 原生輸出格式
```

- 平台這邊只有一個入口 `bin/hub-event <事件>`:用 `parse` 讀輸入,依事件呼叫平台的處理(memory 同步、信箱、
  memory 提醒、跨專案寫入防護、在場紀錄),用 `inject` 輸出。處理的程式(hubbus hook、memory-nudge、guard、
  git-sync-memory)不再知道 `hookSpecificOutput`、`session_id` 這些名字。
- 現在在哪:`merge-hook-settings.py`(寫 Claude 的 hook 設定)、`sync-local-setup.sh`、`worktree-memory.sh`、
  `hubbus hook`、`memory-nudge.py`、`guard.py`、`CLAUDE_CODE_SESSION_ID`。
- 對應:Claude Code 的 SessionStart/SessionEnd/PreCompact/PostToolUse/UserPromptSubmit/PreToolUse。
- **過渡期**:`hubbus hook …` 先留著當轉接(內部呼叫 `hub-event`),等每個專案都重跑過 `sync-local-setup` 才拿掉——
  hook 的錯誤輸出是被丟掉的,直接拿掉舊指令會讓 memory 同步無聲壞掉。
- **worktree 的 hook 掛在使用者層級**(`~/.claude/settings.json`,U4):照 CLAUDE.md,改那個檔要 Knas 在那個 session
  直接確認,所以第 3 層上線時這一步要他動手。沒有某個時機的 runtime
  (例如沒有「壓縮前」),實作宣告不支援,平台照不支援處理(例如改成定時同步),不假裝有。

### 4. InstructionsChannel:規則跟 memory 說明怎麼交給 agent

```python
class InstructionsChannel(Protocol):
    def write(self, workspace: Path, block: str) -> None: ...   # 平台受管的區塊(寫在 runtime 會讀的地方,不進版控)
    def ignore_paths(self) -> list[str]: ...                     # 要加進 .git/info/exclude 的檔
```

- 現在在哪:`sync-local-setup.sh`(`CLAUDE.local.md` 受管區塊)、`onboard/recover/migrate` 的 ignore。
- 區塊的**內容**(`templates/CLAUDE.md`、`templates/rules/`)是平台的,跟 runtime 無關,改名成 `templates/agent-instructions.md`;
  只有「寫到哪個檔、什麼格式」是實作的事(Claude Code:`CLAUDE.local.md`;別的工具可能是 `AGENTS.md`)。

### 5. TranscriptSource:讀對話紀錄

```python
class TranscriptSource(Protocol):
    def sessions(self, since: datetime | None = None) -> Iterator[SessionInfo]: ...  # id、工作目錄、開始/最後時間
    def turns(self, sid: str) -> Iterator[Turn]: ...    # 誰說的(human/assistant/tool/system)、時間、文字、token
    def last_activity(self, workspace: Path) -> datetime | None: ...
```

- 現在在哪:`hublib.py`(`~/.claude/projects/<slug>/*.jsonl`、`origin.kind == human`、slug 規則)、`weekly-reflect.py`、
  `check-memory-gaps.py`、`trajectory.py`、`worktrees.py`(閒置天數)、`migrate-to-mini.py`、`runtime_claude.context_tokens`。
- 這一層**最容易無聲壞掉**:Claude Code 的對話紀錄是沒有公開文件的內部格式。`ClaudeCodeTranscripts` 讀的時候做格式檢查
  (認不得的版本 → 明確失敗、狀態頁標紅),不讓統計默默算錯。

### 6. AuthProvider:登入狀態

```python
class AuthProvider(Protocol):
    def check(self, mode: str) -> AuthStatus: ...   # 能不能用、什麼時候到期;mode = 無人值守 / 訂閱
    def env(self, mode: str) -> dict: ...           # 交給 AgentRuntime 用的環境(不印值)
```

**實作的形狀**(10-25,`runtime_ports` 的 `auth`):`auth_env(mode, env) -> (env | None, 原因)`、`clean_env(env)`、
`credentials() -> [Credential]`、`is_login_error(text)`。跟上面草稿的差別:「能不能用」不是 runtime 回答——實際打一次請求
(`llm.py run login-check`)是平台的檢查,runtime 只描述有哪些登入、到期、門檻、怎麼重新登入(`Credential`),跟分辨錯誤是不是
登入問題。mode 沿用 llm-points 的 `token`/`subscription`(跟 Muninn 共用的宣告,改名等中立化)。`check-claude-login.py` 照
`Credential` 組回原本的句子(警報照字句去重,一字不差);刻意改的一處:token 那次「找不到 claude」也算要人處理。

- 現在在哪:`llm.py auth_env`(headless token / 訂閱登入)、`check-claude-login.py`、`update-claude.py`、`lease-resume`。
- 平台的 job(`agent-hub/claude-login`、`update-claude`)改成「對設定裡用到的每個 runtime 檢查」,名稱改成 `runtime-login`、
  `update-runtime`(用登錄的 `legacy_name`,狀態跟著搬)。

## 不在範圍

- **Claude Desktop 的 session 管理**(列 session、封存、「整理 session」):那是 Desktop 給 session 用的工具,不是平台程式
  呼叫的東西;平台這邊只有 `AgentRuntime.alive/resume` 需要它的概念。
- **現在就寫第二個真的 runtime**:不做。Fake 實作負責證明介面沒漏;真的第二個(Muninn 的本機模型、其他 CLI)等有需要再寫。
- **信箱、memory、librarian 的邏輯本身**:它們已經是平台的東西,只改成透過介面拿 runtime 的資訊。

## 步驟(每一步單獨上線)

1. **骨架**:`runtime_ports.py`(介面、`load` 的機械檢查、`current_session`、`hub.json` 的 `runtime.default`)、契約測試的框架、
   `runtime_fake.py`(有狀態)。這一步完全不碰別的檔,可以馬上做。
   **等另一個 session 第 2 層的第一步(`policy.py`、把設定翻譯搬進 `runtime_claude.py`)commit 之後才動
   `runtime_claude.py`、`render-project-settings.py`、`lease-runner.py`**——它們會同時改這三支。
2. **第 5 層 TranscriptSource**(最容易無聲壞掉、範圍最小;Claude 的實作寫進 `runtime_claude.py`——另一個 session 的第 1 步
   已經 commit(cbdf9bb),可以動了;它之後還會改 `lease-runner.py` 的 preamble,動那支前先跟它說):`hublib` 的對話紀錄解析、`runtime_claude.context_tokens`、
   `worktrees` 的閒置天數改走介面;加格式檢查。
3. **第 1 層 AgentRuntime 的「跑一次」**:`llm.py` 改成組 `Task` 交給介面;所有排程 job 不動就自動跟上。
   `llm-points` 的中立化跟第 2 層一起做(要等那個 session 的政策格式)。
4. **第 1 層的長駐 session**:lease-runner 改走 `open_session/send/events/resume`(跟第 2 層的 session 一起確認)。
5. **第 3、4 層**:`hub-event` 入口、`LifecycleBinding`、`InstructionsChannel`;各專案的 hook 跟受管區塊下次開 session 時
   自動換成新的(`sync-local-setup.sh` 本來就會)。
6. **第 6 層 AuthProvider**,把平台 job 改名。
7. **收尾**:`grep` 不到任何 `claude`、`.claude/`、Claude 的旗標出現在 `runtime_claude.py` 以外(加進每晚的
   contracts-check 當 lint,跟現在「bin/ 裡不准直接 claude -p」同一種檢查)。

## 要 Knas 確認的

0. (這份跟另一個 session 已經對齊邊界:第 2 層、能力、租約的契約中立化由它負責或待定;它 commit 第 1 步之前,
   這份不動 `runtime_claude.py`、`render-project-settings.py`、`lease-runner.py`。)

1. 整體做法(ports & adapters + Strategy 選實作 + Claude Code / Fake 兩個實作跑同一組契約測試 + 一層一層換)。
2. 順序(先第 5 層,再第 1 層)。
3. `llm-points.v1.schema.json` 加 `runtime`、把 Claude 專用欄位換成中立政策——這會動到跟 Muninn 共用的格式,兩邊要同一天同步。
4. 第 3 層上線時,worktree 的 hook(使用者層級設定)要你親自改。
5. 改名 `templates/CLAUDE.md` → `templates/agent-instructions.md` 時,skills 跟兩份 CLAUDE.md 裡引用它的地方一起改。
