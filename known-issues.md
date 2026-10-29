# 已知問題

> 人工維護。自動化(排程、headless 的 coding agent、librarian、看門狗)踩過、之後可能再踩的坑,
> 以及從中得到的運作原則。遇到症狀先查這裡;設計新的自動化之前也先看一遍。
> 這份是通用的;某個部署自己的事故紀錄(機器名、專案、日期)放在它實例目錄的 `config/known-issues.local.md`。
> 「hub 那台」= 跑排程器跟服務的機器;「遙控那台」= 用 coding agent 的遠端功能開 hub 那台資料夾的機器(選用)。

## 排程/自動化的 Claude Code 跑出 401

- **原因**:macOS 上 Claude Code 的 Keychain 憑證跟 `~/.claude/.credentials.json`
  會不同步,headless 跑的那份拿到舊的。
- **處理**:排程或自動化的 Claude Code 出現 401,先檢查這兩份憑證是否一致,
  再懷疑 token 過期或其他原因。平台的 headless 呼叫預設用 `~/.claude/headless-oauth-token`
  (`CLAUDE_CODE_OAUTH_TOKEN`),也一併確認那份還有效。

## "Invalid signature in thinking block"

- **原因**:extended thinking 的 thinking block 帶有綁定對話的加密簽章;
  compaction 時截斷或修改它(例如 `truncateAfterCompaction: true`),下一次
  呼叫就會被拒。
- **處理**:長時間跑的自動化要嘛整個丟掉 thinking block,要嘛原封不動保留,
  **絕對不要截斷或編輯**。

## Tailscale funnel / serve

- **每個 funnel target 只能有一個 owner**(記在 `services.tsv`)。一個 node 可以同時開
  好幾個 funnel port(443/8443/10000),不需要 sudo。
- **非互動環境(launchd、systemd)呼叫 `tailscale funnel` 一定要加 `--yes`**:沒加的話 off+on 靜默失敗,
  退回一個過期的舊 target。
- **在開了 funnel 的那台自己身上 curl tailnet-only 的 serve port 會 TLS 失敗**
  (curl exit 35 / `SSL_ERROR_SYSCALL`):本機把 `<host>.<tailnet>.ts.net` 解析成 funnel 的公網位址,
  不是 tailnet 的 100.x,tailnet-only 的 port 就拒絕。服務本身沒壞。
  本機測試要改成 `curl --resolve <host>.<tailnet>.ts.net:<port>:$(tailscale ip -4) https://…`;
  別台開著 Tailscale 時走 MagicDNS,不受影響

## 看門狗 / 排程 job

- **修完要再驗證**,失敗另外記:曾經有修復腳本在 launchd 的 PATH 找不到
  tailscale(沒有 `/opt/homebrew/bin`),卻每次都記「已修正」。背景服務底下用絕對路徑。
- **看門狗放在服務管理器上**(launchd / systemd),不要放在某個 agent 內部的 cron(那個曾經無聲消失)。
- **輸入檔不見要大聲失敗**:輸入檔被搬走後,依賴它的 cron 靜默壞了好幾週。
- **提醒類 job 要能讀懂「已完成 / 已延後」**:曾經對早就完成、或 owner 已經延期的事持續催。
- **每晚整理的 job 寫日期檔**;每次都會被載入的 memory 檔只在有實質變化時才改
  (每天 append 進常駐 memory 檔,會把每個 session 的 context 撐爆)。
- **純執行的排程不要放 LLM**。
- **同一個 checkout 被兩個排程碰 git 可能撞 `.git/index.lock`**:例:同一份服務 checkout 有每天的 job(commit+push)
  跟每小時的同步(`git pull`),兩邊都沒包 lock。要嘛包 `bin/with-lock.sh`,要嘛錯開時間。
- **排程器是依序跑 job 的,服務管理器也不會在上一個 tick 還沒結束時開新的**:跑幾十分鐘的 job 會讓
  其他準時的 job 都在等。會跑超過幾分鐘的 job 加 `background: true`;背景 job 可能跟同一個 checkout 的其他 job
  同時碰 git(上面 index.lock 那條),有先後就加 `after:`。
- **排程器停了沒有人會知道**:它是所有 job 唯一的觸發點,狀態頁也是它產生的。`scheduler-watchdog.py`
  獨立在服務管理器上顧它;看門狗本身沒在顧時狀態頁會標紅。

- **shell 腳本裡用雙引號包的 prompt,反引號會被當成指令執行**:prompt 裡的格式範例
  (`` `- [ ] 〔owner〕…` ``)被 bash 吃掉(log 只看到 `-: command not found`),模型好幾週都沒看到。
  prompt 裡的反引號要寫 `\``,或改用單引號 heredoc;改完把 prompt 展開看一次(平台的 prompt 現在都在 Python 裡組)。
- **要 headless agent 只改某個檔案,用權限規則限制,不要只靠 prompt**:Claude Code 的 `--permission-mode
  acceptEdits` 會放行目錄裡所有檔案(小模型照樣去改別的檔案);改用 `--permission-mode dontAsk --allowedTools
  "Edit(./STATE.md)"`(`Edit(...)` 涵蓋 Write/Edit)。能讓模型只回答案、由平台寫檔(completion 判斷點)更好。

- **awk 用 `NR==FNR` 讀「排除清單」檔,清單是空檔時會把全部資料濾掉**:空檔一筆都沒讀,`NR==FNR` 就對
  第二個輸入的每一行都成立、全被 `next` 吃掉——index.md 的名冊曾因此被 commit 成空的。清單改在 `BEGIN` 用
  `getline` 讀;測試要包含「清單是空的」那一種(`tests/test_index_roster.py`)。

## 設定、安裝、憑證

- **改了設定檔 ≠ 執行中的 process 已經載入**:看 log 確認 reload,或直接重啟。
- **一個工具只留一個安裝來源**:Homebrew 跟 nvm 各一份同一個 CLI,版本漂移後
  protocol 不相容。升級後跑 `which -a <tool>` 檢查。
- **平台腳本不靠 PATH 找 python**(`bin/py` → 平台自己的 venv,`bin/setup-python` 建):專案要套件用 uv 的 venv
  (`uv run`),不要裝進全域 python。背景服務用平台的 PATH,不含使用者 shell 設定加的路徑。
- **移除本機模型前先 grep 設定**:模型被移掉後,用到它的功能一直 404。
- **測試 / 第二個 instance 要用乾淨的環境變數啟動**(`env -u <TOKEN> …`):測試用的 gateway 從 shell
  繼承了正式的 bot token,跟正式環境搶同一個 session。
- **不要為了 demo/測試還沒發布的功能去手改已安裝的套件或正式設定**:曾因此 crash loop 重啟 20+ 次。
  用獨立的測試帳號/環境;要救回來就退回上一個好的設定、重裝沒改過的檔案。
- **node(跟裝在它底下的 claude)只由 nvm 管**:`brew install <別的東西>` 順手跑的 autoremove 會把「當初是別的
  套件順帶裝的、現在沒人依賴」的 Homebrew `node` 刪掉,指著 `/opt/homebrew/bin/node` 的服務就停了,好幾天才被發現。
  背景服務跟排程沒有 shell 的 nvm 設定,平台用 `hubconf.nvm_bin()` 換算 nvm 預設版本的完整路徑(`LAUNCHD_PATH` 最前面;
  service 的 env 寫 `$PLATFORM_PATH`、`llm.py` 用 `hubconf.tool("claude")`),nvm 升級後 render-services 會跟著換。
  **不要用 Homebrew 裝 node 或用它的 npm 裝全域 CLI**(那份 claude 在 node 被刪後就停在舊版不會更新)。
- **`brew install/uninstall` 的輸出要整份看**:它會順便 autoremove 依賴,`| tail -2` 會把「Uninstalling node…」
  藏掉。之後跑 `brew missing` 確認沒弄壞別的;要保住某個 formula 就 `brew install` 它一次(標成 installed on request,
  autoremove 不會刪)。
- **launchctl bootout 是非同步的**:馬上 bootstrap 會 `5: Input/output error`,服務停在沒載入。render-services
  會等它從 launchd 消失再 bootstrap、失敗重試;手動重載也要等。
- **render-services 的安全網**:指令解析不到就不動現在能跑的那份;bootstrap 失敗、keepalive 的服務沒在跑、
  該載入的沒載入都發警報(沒解決的每 24 小時再提醒,恢復再發一次)並讓 job 失敗(狀態頁紅)。只有狀態頁紅的話,
  沒人看就停了一天半。警報本身發不出去(token 失效)也會在狀態頁標紅,不會默默算了。

## memory worktree 會繼承專案的 git hook

- **症狀**:某個專案的 memory 筆記一直卡著沒 commit,`sync-all-memory` log 有
  `git-sync-memory 失敗`、`npx: command not found`、`husky - pre-commit script failed`。
- **原因**:`.agent-memory` 是同一個 repo 的 worktree,共用 `core.hooksPath`。專案的
  husky pre-commit 跑 `npx lint-staged`;背景服務底下 PATH 沒有 npx 就失敗。互動
  session 裡雖然會成功,但專案的 prettier 會順便改寫 memory 筆記的格式。
- **處理**:`git-sync-memory.sh`、`sync-all-memory.sh`、`sync-local-setup.sh` 的 AGENT.md commit 都用
  `GIT_CONFIG_*` 環境變數把 `core.hooksPath` 設成 `/dev/null`,memory 的 commit/push 不跑專案 hook。以後寫新的
  memory commit/push 也要照做。

## 遙控那台關機時,它開的 SSH session 寫不進去

- **症狀**:用手機(Remote Control)叫 session 改檔、收尾,全部失敗,錯誤是
  `PreToolUse hook did not respond before its timeout (host client may be unreachable)`;讀檔、查資料正常。
- **原因**:session 的 claude 跑在 hub 那台,但它是遙控那台的桌面 app 透過 SSH 開的。寫入前的權限檢查
  (桌面 app 掛的 PreToolUse hook)、app 自己的工具都由遙控那台的 app 回答;遙控那台關機或睡眠,hub 那台上的程式
  還活著,所以讀得到、聊得了,寫入卻每次逾時。不是 hub 那台少裝什麼。
- **處理**:要在遙控那台關著時也能寫,在 hub 那台直接開本機 session(手機用 Remote Control),或讓遙控那台不睡眠。
  手機上叫它寫東西沒反應,先確認遙控那台開著。

## OS sandbox 擋不住 Keychain

- **發現**:sandbox 底下的 Bash 跑 `security find-generic-password -s 'Claude Code-credentials'`
  回 0(找得到);加上 `-w` 就會印出 Claude 的登入憑證。檔案層的封鎖(denyRead、Read deny 規則)對它沒用,
  Keychain 是透過系統服務存取的,不是讀檔案。同一個 sandbox 擋得住讀 `~/.ssh`、`~/.claude` 的憑證檔、`~/services/*/.env`。
- **影響**:不能讓沒人看著的 agent 在 sandbox 裡自由跑指令,然後以為憑證安全。想法沙盒的 work 模式因此維持
  「每個指令都問 owner」;租約的 deny 加了 `Bash(security …)`,但那只是補強(`sh -c`、python 就繞得過)。
- **要放寬指令之前**:先找到在系統層擋住 Keychain 的方法(例如 sandbox profile 擋 securityd 的 mach-lookup,
  或讓那些 job 用不在 Keychain 裡的憑證),而且要實測。
- **平台自己的 profile 擋得住**:`bin/seatbelt.py` 擋 `com.apple.SecurityServer` 等的 mach-lookup,
  直接叫 Security framework(不經過 `security` 這支程式)也查不到;`tests/test_cap_profile.py` 每次都測。目前只有平台代跑
  (`hub-run`)用這份 profile,session 自己的 Bash 還是 coding agent 自己的 sandbox。

## macOS:SSH 底下的 claude 是「未登入」

- **症狀**:SSH 進 hub 那台跑 `claude auth status` 顯示 `loggedIn: false`,`claude -p` 用不了 claude.ai
  connector;但明明在那台登入過。
- **原因**:登入憑證存在 login keychain,SSH session 讀不到(`security` 回
  `User interaction is not allowed`);launchd 的 gui domain(排程器、`schedulectl run`)讀得到。
- **處理**:要測需要 Claude 登入的排程 job,一律用 `schedulectl run <name>`(走 launchd),不要
  SSH 進去直接跑腳本。平台的 headless 呼叫另外用 `~/.claude/headless-oauth-token`,不受影響,但那個 token
  會讓 connector 不載入,所以登錄 `connectors: true` 的 job 刻意不用它。
- **其他 CLI 也一樣**:gh 預設把 token 存 keychain,SSH session(包括桌面 app 透過 SSH 開的
  session)讀不到,`gh auth status` 會說 token invalid。要在 SSH 底下用 gh 就
  `gh auth login -h github.com --git-protocol ssh --web --insecure-storage`(存在 `~/.config/gh/hosts.yml`,
  只有自己讀得到)。vercel 本來就存檔案,`vercel login` 一次就好。`migrate-to-mini.py` 會檢查這兩個。
  重新登入的 gh token 不一定有 `workflow` scope:要用 gh/HTTPS 改 `.github/workflows/` 時跑 `gh auth refresh -s workflow`;
  git push 走 SSH key 不受影響。
- **origin 是 HTTPS 的 repo**:git 預設從 keychain 拿 HTTPS 憑證,SSH 底下會
  `could not read Username`。跑一次 `gh auth setup-git`,github.com 的 HTTPS 改用 gh 存在檔案裡的 token。
- **三份憑證各自會壞**:keychain(launchd 底下的排程、助理、租借)、`~/.claude/.credentials.json`(SSH 底下讀不到
  keychain 時用;refresh token 約 28 天,用到就往後延)、`~/.claude/headless-oauth-token`(`claude setup-token`,一年;
  帶 `CLAUDE_CODE_OAUTH_TOKEN` 時 claude.ai connector 不載入)。平台的 `claude -p` 一律預設用 headless token
  (`llm.py` 統一處理,呼叫端不要自己設),只有登錄 `connectors: true` 的 job 用訂閱登入。claude 模組每小時跑
  `claude-login` 檢查三份,壞了發警報。
- **另一個坑**:launchd 的 job 結束時會殺掉同一個 process group 的程序,`nohup ... &` 擋不住;
  要在背景繼續跑得 double fork + setsid(`run-agent-job.sh` 的做法)。

## 不能送雲端的對話紀錄

- 有些對話紀錄(例:工作用的 profile)只能給本機模型讀。任何會呼叫雲端模型的東西(週報、librarian、agent job 的 prompt)
  都不能讀那些目錄——`hublib.TRANSCRIPT_ROOTS` 只列能送的。**遮蔽人名不夠**:客戶名、專案代號、程式碼、內部網址、
  商業條件照樣能辨識;目錄名本身就可能帶客戶/repo 名稱。要讓雲端看,只能先由本機模型改寫成去識別化的抽象摘要,
  雲端只看摘要,而且要先確認保密條款允許。

## 第二階段 sandbox / 權限規則(Claude Code)

- **權限的 deny 會蓋過 sandbox 的 allowWrite**:專案 deny `Edit(<hub>/**)`,sandbox 就算 allowWrite
  `<hub>/.state/bus` 也寫不進去(`Operation not permitted`)。所以匯流排放在 hub 目錄外(`hub.json` 的 `bus_dir`)。
  以後要讓沙箱裡的專案寫什麼,放在沒有被 deny 的路徑。
- **allow 開不了 deny 的例外**(deny → ask → allow,先 deny 先贏):「只准讀別的專案的 STATE.md」做不到,改讀
  `~/.memory-copies/<p>/STATE.md`。
- **hook 在 sandbox 外跑**:memory 同步、信箱 hook 不受影響。
- **專案自己的同名 MCP 會蓋掉 claude.ai 的 connector**:專案裡有自己叫 `upwork` 的 MCP(要 OAuth)時,
  在那個資料夾跑的 headless job 拿不到 claude.ai 的 Upwork 工具。agent job 用 `cwd: state` 在自己的狀態目錄跑。
- **dontAsk 底下工具名稱要完全對**:claude.ai connector 的工具叫 `mcp__claude_ai_<名稱>__<名稱>__<tool>`(多一段)。
  名字錯就是全部被拒,報告會寫「被權限擋掉」。
- **使用者層級 deny 掉 session 用 Edit 改 `~/workspace/**/.claude/settings.local.json`**(建議的設定):要改專案設定請改登錄,
  由 render-project-settings 寫;平台腳本不是用 Edit 工具寫,不受影響。

## agent 協作原則

- **不要相信 agent/subagent 的自我回報**(「完成了」「工具沒回應」),看客觀紀錄:
  git HEAD/diff/CI、排程器與各 job 的 log、transcript 裡實際的 tool call 跟
  permission_denials。agent 會編造原因跟進度。
- **不可逆或牽涉憑證的步驟留在主 session**,由 owner 直接確認;轉述來的「已經同意了」
  不算數。owner 貼出來的密碼,agent 一律不代打,由他自己輸入。
- **瀏覽器自動化:同一步失敗 3 次就停下來問**(曾經一個按鈕重試 30+ 次,花了約 $30)。
- **基礎設施除錯先設時限(約 30 分鐘)**,超過就記下 workaround 往前走。
- **截斷 transcript 不等於 compaction**:照行數砍會丟掉進度,要用摘要;長期 session 不要用短的閒置計時器重置
  (120 分鐘閒置重置弄丟過一整段對話)。
- **個人反思不寫進共享的 agent memory。**
- **斷言某個概念的出處前先搜尋確認**(agent 曾說「harness」是 Anthropic 專有的說法,
  其實 OpenAI 也寫過)。
- **sandbox 不能疊兩層**:Claude Code 的 sandbox 裡跑 `sandbox-exec` 一律 `sandbox_apply: Operation not
  permitted`。要在別的限制下跑東西(例:GPU 要 Metal,Claude Code 的 sandbox 擋住、也沒有設定能開),只能交給 sandbox
  外的程序套——平台代跑 `bin/cap-runner.py` + `hub-run`(docs/design/capabilities.md)。
- **`excludedCommands` 只對「單獨」的指令有效**:單獨跑排除的指令會跳出 sandbox;跟別的指令串在一起
  (`&&`、`;`)時整串都留在 sandbox 裡。測的時候別在後面多接 `; echo rc=$?`,會誤以為它沒效。平台沒有用它。
- **Seatbelt 的 `(deny signal (target others))` 擋不住**送信號給別的程序;要寫成
  `(deny signal)(allow signal (target same-sandbox))`,`process-info*` 同理。
- **常駐服務載入的是啟動當時的程式碼**:改了它會 import 的模組,新舊模組混用會在執行中途壞掉(工作卡住、交接失敗)。
  lease-runner、cap-runner 啟動時記下 `hubconf.code_stamp()`(bin/ 最後修改時間),變了、而且穩定 60 秒(連續改檔只重啟一次)
  就在沒有租約/工作在跑的空檔自己結束,服務管理器(KeepAlive)用新的程式重啟——改完程式不用手動重啟,但有租約在跑時
  會等到它閒下來。新的常駐服務也照這樣做。
- **commit 前看清楚暫存區**:共用 working tree 時 `git add` 一整批會把別的 session 暫存的東西一起 commit;用
  `git commit -- <路徑>`。
