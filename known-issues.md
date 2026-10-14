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
- 來源:OpenClaw #infrastructure、#general

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
- **另一個坑**:launchd 的 job 結束時會殺掉同一個 process group 的程序,`nohup ... &` 擋不住;
  要在背景繼續跑得 double fork + setsid(`run-agent-job.sh` 的做法)。

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
