#!/usr/bin/env bash
# 把一個已 onboard 專案的「本機專用」設定對齊到 agent-hub 目前的版本。冪等,
# 永遠不改、不 commit 專案 repo 裡的任何被追蹤檔案:
#
#   1. CLAUDE.local.md 裡 <!-- agent-hub:begin/end --> 之間的區塊,整塊用
#      templates/CLAUDE.md 重生——改了 template,所有專案下次開 session 就
#      跟上,不用靠人記得去補。區塊外的內容不動。舊版沒有標記、直接 append
#      的 pointer 段落會被換成有標記的區塊。
#   2. .claude/settings.local.json 的 hook(merge-hook-settings.py,只補不刪)
#   2b. .agent-memory/AGENT.md 對齊 templates/AGENT.md——這個是例外:它在
#      memory 分支上,會 commit+push(只 commit AGENT.md),不碰專案 repo
#   3. CLAUDE.local.md、.claude/settings.local.json 如果沒被 ignore,寫進
#      .git/info/exclude——本機專屬的 ignore,不動會 commit 的 .gitignore,
#      公開 repo 也不會多出任何跟這套工具有關的字。
#
# 由專案的 SessionStart hook 自動呼叫(--quiet),onboard-project.sh、
# recover-project.sh 也呼叫。SessionStart hook 的 stdout 會進 session
# context,所以 --quiet 時什麼都不印。
#
# 用法:sync-local-setup.sh [--quiet] [專案路徑,預設目前目錄]
set -uo pipefail

AGENT_HUB="$HOME/agent-hub"
QUIET=false
[ "${1:-}" = "--quiet" ] && { QUIET=true; shift; }
say() { $QUIET || echo "sync-local-setup: $*"; }

cd "${1:-.}" || exit 1
[ -d .agent-memory ] || { say "$(pwd) 沒有 .agent-memory,不是 onboard 過的專案,跳過"; exit 0; }

# --- 1. CLAUDE.local.md 受管區塊 ---
python3 - "$AGENT_HUB/templates/CLAUDE.md" CLAUDE.local.md <<'PY' && say "CLAUDE.local.md 已對齊"
import re, sys
from pathlib import Path

BEGIN, END = "<!-- agent-hub:begin -->", "<!-- agent-hub:end -->"
LEGACY_HEADING = "## agent-hub memory 追蹤"

tpl, path = Path(sys.argv[1]), Path(sys.argv[2])
body = "\n".join(tpl.read_text().splitlines()[2:]).strip("\n")  # 去掉 "# CLAUDE.md" 標題
block = f"{BEGIN}\n{body}\n{END}\n"
old = path.read_text() if path.exists() else ""

if BEGIN in old and END in old:
    new = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", lambda _: block, old, count=1, flags=re.S)
else:
    # 舊版 pointer:從標題到下一個 "## " 標題(或檔尾)整段拿掉,換成受管區塊
    lines, out, skipping = old.splitlines(keepends=True), [], False
    for line in lines:
        if line.rstrip("\n") == LEGACY_HEADING:
            skipping = True
            continue
        if skipping and line.startswith("## "):
            skipping = False
        if not skipping:
            out.append(line)
    rest = "".join(out).strip("\n")
    new = (rest + "\n\n" if rest else "") + block

if new != old:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(new)
    tmp.replace(path)
PY

# --- 2. hook ---
mkdir -p .claude
python3 "$AGENT_HUB/bin/merge-hook-settings.py" .claude/settings.local.json && say "hook 已對齊"

# --- 2b. .agent-memory/AGENT.md 對齊 templates/AGENT.md ---
# AGENT.md 在 memory 分支上(會被 commit、同步到 mini),不是本機檔;
# git-sync-memory.sh 只收 memory/ 跟 STATE.md,所以這裡自己 commit+push,
# 而且只 commit AGENT.md 這一個檔案。跟 SessionStart 的 git pull 平行跑,
# 用同一把 with-lock 鎖避免互撞。
# 比對的是 HEAD 裡的版本,不是 working tree:上次 cp 了但 commit/push 失敗的話,
# 下次還會再試。
if ! git -C .agent-memory show HEAD:AGENT.md 2>/dev/null | cmp -s "$AGENT_HUB/templates/AGENT.md" -; then
  ( cd .agent-memory \
    && cp "$AGENT_HUB/templates/AGENT.md" AGENT.md \
    && "$AGENT_HUB/bin/with-lock.sh" sh -c \
         'git commit -q -m "agent-hub: sync AGENT.md from template" -- AGENT.md \
          && "$0/bin/git-safe-push.sh"' "$AGENT_HUB" ) >/dev/null 2>&1 \
    && say "AGENT.md 已對齊 template 並 push" \
    || say "⚠️ AGENT.md 已更新但 commit/push 失敗,下次開 session 會再試"
fi

# --- 3. 本機檔一定要被 ignore ---
exclude=$(git rev-parse --git-path info/exclude 2>/dev/null) || exit 0
for f in CLAUDE.local.md .claude/settings.local.json; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    # 已經被 commit 進 repo:ignore 救不了,要那個專案自己的 session 處理
    say "⚠️ $f 已經被這個 repo 追蹤(commit 過),ignore 無效——請該專案的 session 處理"
  elif ! git check-ignore -q "$f" 2>/dev/null; then
    mkdir -p "$(dirname "$exclude")"
    printf '%s\n' "$f" >> "$exclude"
    say "$f 原本沒被 ignore,已加進 $exclude"
  fi
done
exit 0
