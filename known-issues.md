# 已知問題

> 人工維護。自動化(排程、`claude -p`、librarian、看門狗)踩過、之後可能再踩的坑,
> 以及從中得到的運作原則。遇到症狀先查這裡;設計新的自動化之前也先看一遍。

## 排程/自動化的 Claude Code 跑出 401

- **原因**:macOS 上 Claude Code 的 Keychain 憑證跟 `~/.claude/.credentials.json`
  會不同步,headless 跑的那份拿到舊的。
- **處理**:排程或自動化的 Claude Code 出現 401,先檢查這兩份憑證是否一致,
  再懷疑 token 過期或其他原因。librarian 用 `~/.claude/headless-oauth-token`
  (`CLAUDE_CODE_OAUTH_TOKEN`),也一併確認那份還有效。
- 來源:OpenClaw daily log 2026-07-12

## "Invalid signature in thinking block"

- **原因**:extended thinking 的 thinking block 帶有綁定對話的加密簽章;
  compaction 時截斷或修改它(例如 `truncateAfterCompaction: true`),下一次
  呼叫就會被拒。
- **處理**:長時間跑的自動化要嘛整個丟掉 thinking block,要嘛原封不動保留,
  **絕對不要截斷或編輯**。
- 來源:OpenClaw daily log 2026-06-05

## Tailscale funnel

- **每個 funnel target 只能有一個 owner**(見 services.tsv)。一個 node 可以同時開
  好幾個 funnel port(443/8443/10000),不需要 sudo。
- **非互動環境(launchd)呼叫 `tailscale funnel` 一定要加 `--yes`**:舊 OpenClaw
  restart-notify 沒加,off+on 靜默失敗,退回一個過期的舊 target(8791)。
  `bin/fix-project-15-funnel.sh` 已補上(2026-09-25)。
- 來源:OpenClaw #infrastructure
- **在 mini 自己身上 curl tailnet-only 的 serve port(443 狀態頁、8444 muninn)會 TLS 失敗**
  (curl exit 35 / `SSL_ERROR_SYSCALL`):因為開了 funnel,mini 本機把 `hub-host.<tailnet>.ts.net`
  解析成 funnel 的公網位址,不是 tailnet 的 100.x,tailnet-only 的 port 就拒絕。服務本身沒壞。
  本機測試要改成 `curl --resolve hub-host.<tailnet>.ts.net:<port>:$(tailscale ip -4) https://…`;
  手機/MBP 開著 Tailscale 時走 MagicDNS,不受影響(2026-10-20 架 muninn 時發現)

## 看門狗 / 排程 job

- **修完要再驗證**,失敗另外記:舊 `fix-funnel.sh` 在 launchd 的 PATH 找不到
  tailscale(沒有 `/opt/homebrew/bin`),卻每次都記「已修正」。launchd 底下用絕對路徑。
- **看門狗放 launchd**,不要放在某個 agent 內部的 cron(那個曾經無聲消失)。
- **輸入檔不見要大聲失敗**:project-17 搬走後,兩個 cron 靜默壞了好幾週。
- **提醒類 job 要能讀懂「已完成 / 已延後」**:曾經對 06-05 就發完的 某則貼文、
  Knas 延期的事持續催。
- **每晚整理的 job 寫日期檔**;每次都會被載入的 memory 檔只在有實質變化時才改
  (舊 job 每天 append 進 MEMORY.md,把每個 session 的 context 撐爆)。
- **純執行的排程不要放 LLM**。
- **同一個 checkout 被兩個排程碰 git 可能撞 `.git/index.lock`**:mini 的 `~/services/project-16`
  同時有每天的文獻 job(commit+push)跟每小時的 Zotero 同步(`git pull`),兩邊都沒包 lock。
  機率低、Zotero 那邊下個小時會自己重試,目前不處理;文獻 job 哪天 commit 失敗先查這個。
- 來源:OpenClaw #infrastructure、daily logs
- **排程器是依序跑 job 的,launchd 也不會在上一個 tick 還沒結束時開新的**:2026-10 之前
  muninn-reflect(最多 90 分鐘)、project-16(~30 分鐘)跑的時候,muninn-tick 跟所有
  準時的 job 都在等。2026-10-21 加了 `background: true`;新增會跑超過幾分鐘的 job 要記得加。
  背景 job 可能跟同一個 checkout 的其他 job 同時碰 git(上面 index.lock 那條),排時間時錯開。
- **排程器停了沒有人會知道**:它是所有 job 唯一的觸發點,狀態頁也是它產生的。2026-10-21 起
  `scheduler-watchdog.py` 獨立在 launchd 上顧它(見 services.tsv);看門狗本身沒在顧時狀態頁會標紅。

- **shell 腳本裡用雙引號包的 prompt,反引號會被當成指令執行**:librarian 主 prompt 裡
  `` `- [ ] 〔Knas〕…` `` 這些格式範例從 2026-09-30 起每次都被 bash 吃掉(log 只看到
  `-: command not found`、`syntax error near unexpected token '無'`),模型兩週都沒看到待辦格式。
  prompt 裡的反引號要寫 `\``,或改用單引號 heredoc;改完用 `eval` 把 prompt 展開看一次。
- **要 headless agent 只改某個檔案,用權限規則限制,不要只靠 prompt**:`--permission-mode
  acceptEdits` 會放行目錄裡所有檔案(10-15 project-06 的 haiku 照樣去改 memory/reference/);
  改用 `--permission-mode dontAsk --allowedTools "Edit(./STATE.md)"`(`Edit(...)` 涵蓋
  Write/Edit)。

## 設定、安裝、憑證

- **改了設定檔 ≠ 執行中的 process 已經載入**:看 log 確認 reload,或直接重啟。
- **一個工具只留一個安裝來源**:Homebrew 跟 nvm 各一份同一個 CLI,版本漂移後
  protocol 不相容。升級後跑 `which -a <tool>` 檢查。
- **移除本機模型前先 grep 設定**(例如 `models.env`):gemma4:e4b 被移掉後,LINE
  圖片分析一直 404。
- **測試 / 第二個 instance 要用乾淨的環境變數啟動**(`env -u DISCORD_BOT_TOKEN …`):
  07-11 QA gateway 從 shell 繼承了正式的 bot token,跟正式環境搶 Discord session。
- **不要為了 demo/測試還沒發布的功能去手改已安裝的套件或正式設定**:07-11 因此
  crash loop 重啟 20+ 次。用獨立的測試帳號/環境;要救回來就退回上一個好的設定、
  重裝沒改過的檔案。
- **node(跟裝在它底下的 claude)只由 nvm 管**(2026-10-24 Knas 決定):`brew install restic` 順手跑的清理
  (autoremove)把「當初是別的套件順帶裝的、現在沒人依賴」的 Homebrew `node` 刪了,LINE bot 的 plist 指著
  `/opt/homebrew/bin/node`,從 10-22 22:06 停到 10-24 才被發現。背景服務跟排程沒有 shell 的 nvm 設定,平台用
  `hubconf.nvm_bin()` 換算 nvm 預設版本的完整路徑(`LAUNCHD_PATH` 最前面;service 的 env 寫 `$PLATFORM_PATH`、
  `llm.py` 用 `hubconf.tool("claude")`),nvm 升級後 render-services 會跟著換。**不要再用 Homebrew 裝 node 或用
  它的 npm 裝全域 CLI**(那份 claude 在 node 被刪後就停在舊版不會更新)。
- **`brew install/uninstall` 的輸出要整份看**:它會順便 autoremove 依賴,`| tail -2` 會把「Uninstalling node…」
  藏掉(10-22 restic、10-24 node@22 → icu4c@78 都是這樣)。之後跑 `brew missing` 確認沒弄壞別的;要保住某個
  formula 就 `brew install` 它一次(標成 installed on request,autoremove 不會刪)。
- **launchctl bootout 是非同步的**:馬上 bootstrap 會 `5: Input/output error`,服務停在沒載入(10-22 LINE bot、
  10-24 muninn/lease-runner)。render-services 會等它從 launchd 消失再 bootstrap、失敗重試;手動重載也要等。
- **render-services 的安全網**:指令解析不到就不動現在能跑的 plist;bootstrap 失敗、keepalive 的服務沒在跑、
  該載入的沒載入都發 Discord(同一組只發一次,恢復再發一次)並讓 job 失敗(狀態頁紅)。以前只有狀態頁紅,
  沒人看就一天半。
- 來源:OpenClaw #infrastructure、#general;2026-10-24 LINE bot 停擺

## memory worktree 會繼承專案的 git hook

- **症狀**:某個專案的 memory 筆記一直卡著沒 commit,`sync-all-memory` log 有
  `git-sync-memory 失敗`、`npx: command not found`、`husky - pre-commit script failed`。
- **原因**:`.agent-memory` 是同一個 repo 的 worktree,共用 `core.hooksPath`。project-06
  的 husky pre-commit 會跑 `npx lint-staged`;launchd 底下 PATH 沒有 npx 就失敗。互動
  session 裡雖然會成功,但專案的 prettier 會順便改寫 memory 筆記的格式。
- **處理**(2026-10-02 已修):`git-sync-memory.sh`、`sync-all-memory.sh`、
  `sync-local-setup.sh` 的 AGENT.md commit 都用 `GIT_CONFIG_*` 環境變數把
  `core.hooksPath` 設成 `/dev/null`,memory 的 commit/push 不跑專案 hook。以後寫新的
  memory commit/push 也要照做。

## MBP 連不到 mini

- **症狀**:`sync-all-memory` log 一整段 `ssh: connect to host ... port 22: Operation timed out`、
  `補 push 失敗`,同時幾個專案的 memory 有 unpushed commit。2026-09-30、10-08–09、10-13–14 都發生過。
- **原因**:MBP 不在 tailnet 上(不在家、睡眠、網路切換),mini 本身照常在跑(排程沒中斷)。
- **處理**:不用管,恢復連線後的那一輪會自動補 push。2026-10-14 起 `sync-all-memory.sh` 每輪先
  確認連得到 mini,連不到就只在本機 commit、整輪只記一行,不再每個專案各等一次逾時。
  急著要同步就手動跑 `bin/sync-all-memory.sh`。

## mini 上的 claude 在 SSH 底下是「未登入」

- **症狀**:SSH 進 mini 跑 `claude auth status` 顯示 `loggedIn: false`,`claude -p` 用不了 claude.ai
  connector(Upwork 等);但 Knas 明明在 mini 登入過。
- **原因**:登入憑證存在 mini 的 login keychain,SSH session 讀不到(`security` 回
  `User interaction is not allowed`);launchd 的 gui domain(排程器、`schedulectl run`)讀得到。
- **處理**:要測需要 Claude 登入的排程 job,一律用 `schedulectl run <name>`(走 launchd),不要
  SSH 進去直接跑腳本。librarian 另外用 `~/.claude/headless-oauth-token`,不受影響,但那個 token
  會讓 connector 不載入,所以 `run-agent-job.sh` 刻意 unset 它。
- **其他 CLI 也一樣**:gh 預設把 token 存 keychain,SSH session(包括 Claude Desktop 透過 SSH 開的
  session)讀不到,`gh auth status` 會說 token invalid。mini 上的 gh 要用
  `gh auth login -h github.com --git-protocol ssh --web --insecure-storage`(存在 `~/.config/gh/hosts.yml`,
  只有該使用者讀得到)。vercel 本來就存檔案,`vercel login` 一次就好。`migrate-to-mini.py` 會檢查這兩個。
  (2026-10-15 重新登入的 gh token 沒有 `workflow` scope:要用 gh/HTTPS 改 `.github/workflows/`
  時跑 `gh auth refresh -s workflow`;git push 走 SSH key 不受影響。)
- **origin 是 HTTPS 的 repo(例:project-25)**:git 預設從 keychain 拿 HTTPS 憑證,SSH 底下會
  `could not read Username`。mini 已跑過 `gh auth setup-git`,github.com 的 HTTPS 改用 gh 存在檔案裡的 token。
- **另一個坑**:launchd 的 job 結束時會殺掉同一個 process group 的程序,`nohup ... &` 擋不住;
  要在背景繼續跑得 double fork + setsid(`run-agent-job.sh` 的做法)。

## 工作 profile 的 transcript 在 mini 上,但只能本機處理

- 2026-10-19 起,muninn 的 MBP agent 把工作 profile 的 Claude Code transcript 同步到 mini 的
  `~/transcript-backups/claude-work-projects/`(Knas 決定公司內容「全部」觀測、只在本機處理)。
  之前 agent-hub 刻意不備份工作 profile,這是政策改變,不是意外。
- **原文沒有遮蔽**(2026-10-21 查過;Knas 決定 Claude 對話「完整分析、只給本機模型看」,不遮蔽),目錄名本身就帶客戶/
  repo 名稱。遮蔽人名也不夠:客戶名、專案代號、程式碼、內部網址、商業條件照樣能辨識。
- **規則**:這個目錄只給 mini 本機模型讀。任何會呼叫雲端模型的東西(`weekly-reflect` /
  `reflect_llm.py`、librarian、`run-agent-job.sh` 的 prompt)都不能讀它——`hublib.TRANSCRIPT_ROOTS`
  刻意不含這個目錄。要讓雲端看工作內容,只能先由本機模型改寫成去識別化的抽象摘要,雲端只看摘要,
  而且要先確認 公司 的保密條款允許。

## 第二階段 sandbox / 權限規則

- **權限的 deny 會蓋過 sandbox 的 allowWrite**:專案 deny `Edit(~/agent-hub/**)`,sandbox 就算 allowWrite
  `~/agent-hub/.state/bus` 也寫不進去(`Operation not permitted`,2026-10-22 pilot 實測)。所以匯流排搬到 hub 目錄外
  (`hub.json` 的 `bus_dir`)。以後要讓沙箱裡的專案寫什麼,放在沒有被 deny 的路徑。
- **allow 開不了 deny 的例外**(deny → ask → allow,先 deny 先贏):「只准讀別的專案的 STATE.md」做不到,改讀
  `~/.memory-copies/<p>/STATE.md`。
- **hook 在 sandbox 外跑**:memory 同步、信箱 hook 不受影響。
- **專案自己的同名 MCP 會蓋掉 claude.ai 的 connector**:project-39 有自己叫 `upwork` 的 MCP(要 OAuth),
  在那個資料夾跑的 headless job 拿不到 claude.ai 的 Upwork 工具。agent job 用 `cwd: state` 在自己的狀態目錄跑。
- **dontAsk 底下工具名稱要完全對**:claude.ai connector 的工具叫 `mcp__claude_ai_Upwork__upwork__<tool>`(多一段)。
  名字錯就是全部被拒,報告會寫「被權限擋掉」。
- **使用者層級 U1 擋所有 session 用 Edit 改 `~/workspace/**/.claude/settings.local.json`**:要改專案設定請改登錄
  (`projects/<p>.yaml`),由 render-project-settings 寫;平台腳本不是用 Edit 工具寫,不受影響。

## agent 協作原則

- **不要相信 agent/subagent 的自我回報**(「完成了」「工具沒回應」),看客觀紀錄:
  git HEAD/diff/CI、排程器與各 job 的 log、transcript 裡實際的 tool call 跟
  permission_denials。agent 會編造原因跟進度。
- **不可逆或牽涉憑證的步驟留在主 session**,由 Knas 直接確認;轉述來的「已經同意了」
  不算數。Knas 貼出來的密碼,agent 一律不代打,由他自己輸入。
- **瀏覽器自動化:同一步失敗 3 次就停下來問**(Upwork 一個按鈕重試 30+ 次,約 $30)。
- **基礎設施除錯先設時限(約 30 分鐘)**,超過就記下 workaround 往前走。
- **截斷 transcript 不等於 compaction**:照行數砍會丟掉進度(#infrastructure 就這樣
  丟過),要用摘要;長期 session 不要用短的閒置計時器重置(120 分鐘閒置重置弄丟過
  一段 #career-path 對話)。
- **個人反思不寫進共享的 agent memory。**
- **斷言某個概念的出處前先搜尋確認**(agent 曾說「harness」是 Anthropic 專有的說法,
  其實 OpenAI 也寫過)。
- 來源:OpenClaw daily logs、#self-reflection、#general
