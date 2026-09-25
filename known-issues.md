# 已知問題

> 人工維護。自動化(排程、`claude -p`、librarian)踩過、之後可能再踩的坑,
> 遇到症狀先查這裡。每條寫:症狀 → 原因 → 怎麼處理,加上來源日期。

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
