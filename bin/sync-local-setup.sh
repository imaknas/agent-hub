#!/usr/bin/env bash
# 把一個已 onboard 專案的「本機專用」設定對齊到 agent-hub 目前的版本。冪等,
# 永遠不改、不 commit 專案 repo 裡的任何被追蹤檔案:
#
#   1. CLAUDE.local.md 裡 <!-- agent-hub:begin/end --> 之間的區塊,整塊用
#      templates/CLAUDE.md(加上 .agent-memory/RULES 登記的 templates/rules/*.md)重生——改了 template,所有專案下次開 session 就
#      跟上,不用靠人記得去補。區塊外的內容不動。舊版沒有標記、直接 append
#      的 pointer 段落會被換成有標記的區塊。
#   2. .claude/settings.local.json 的 hook(merge-hook-settings.py,只補不刪)
#   2b. .agent-memory/AGENT.md 對齊 templates/AGENT.md——這個是例外:它在
#      memory 分支上,會 commit+push(只 commit AGENT.md),不碰專案 repo
#   3. .agent-memory/、CLAUDE.local.md、.claude/settings.local.json 如果沒被 ignore,寫進
#      .git/info/exclude——本機專屬的 ignore,不動會 commit 的 .gitignore,
#      公開 repo 也不會多出任何跟這套工具有關的字。
#
#   4. 已 commit 的檔案提到 agent-hub / .agent-memory 就印提醒(--quiet 也印)
#
# 由專案的 SessionStart hook 自動呼叫(--quiet),onboard-project.sh、
# recover-project.sh 也呼叫。SessionStart hook 的 stdout 會進 session
# context,所以 --quiet 時只印第 4 項的提醒(有違規時),其他什麼都不印。
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
# 專案有在 memory 分支的 .agent-memory/RULES 登記額外規則(一行一個名稱,例如
# engineering)的話,把 templates/rules/<名稱>.md 接在區塊後面。登記放在 memory
# 分支上:換機器 recover 也還在;規則內容放 agent-hub:改一次,所有登記的專案都跟上。
python3 - "$AGENT_HUB/templates/CLAUDE.md" CLAUDE.local.md .agent-memory/RULES "$AGENT_HUB/templates/rules" <<'PY' && say "CLAUDE.local.md 已對齊"
import re, sys
from pathlib import Path

BEGIN, END = "<!-- agent-hub:begin -->", "<!-- agent-hub:end -->"
LEGACY_HEADING = "## agent-hub memory 追蹤"

tpl, path = Path(sys.argv[1]), Path(sys.argv[2])
rules_file, rules_dir = Path(sys.argv[3]), Path(sys.argv[4])
body = "\n".join(tpl.read_text().splitlines()[2:]).strip("\n")  # 去掉 "# CLAUDE.md" 標題
if rules_file.exists():
    for name in rules_file.read_text().split():
        rule = rules_dir / f"{name}.md"
        if rule.exists():
            body += "\n\n" + rule.read_text().strip("\n")
        else:
            print(f"sync-local-setup: RULES 登記了 {name},但找不到 {rule}", file=sys.stderr)
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
for f in .agent-memory CLAUDE.local.md .claude/settings.local.json; do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    # 已經被 commit 進 repo:ignore 救不了,要那個專案自己的 session 處理
    say "⚠️ $f 已經被這個 repo 追蹤(commit 過),ignore 無效——請該專案的 session 處理"
  else
    # 一律寫進 exclude,不管 .gitignore 有沒有:.gitignore 裡的那行之後會被拿掉
    # (它本身就洩漏名稱),不能依賴它。
    [ "$f" = .agent-memory ] && pat=".agent-memory/" || pat="$f"
    if ! grep -qxF "$pat" "$exclude" 2>/dev/null; then
      mkdir -p "$(dirname "$exclude")"
      printf '%s\n' "$pat" >> "$exclude"
      say "$pat 已加進 $exclude"
    fi
  fi
done
# --- 4. 被 commit 的檔案不能提到這套機制 ---
# repo 之後會不會公開沒有定論,所以一律不准:agent-hub、.agent-memory 的名稱或路徑
# 出現在已 commit 的內容裡就提醒。這段刻意在 --quiet 下也輸出——SessionStart hook
# 的 stdout 會進 session context,等於每次開 session 都把待修清單交給這個專案
# 自己的 session,不用靠人記得去巡。沒有違規就什麼都不印。
leaks=$(git grep -l -I -E 'agent-hub|agent-memory' HEAD -- . 2>/dev/null | sed 's#^HEAD:##' | head -10)
if [ -n "$leaks" ]; then
  cat <<MSG
⚠️ 這個 repo 已 commit 的檔案提到了 agent-hub / .agent-memory(repo 之後可能公開,這些名稱跟路徑不能進 commit):
$(echo "$leaks" | sed 's/^/  - /')
請在這個專案裡處理:維運需要的資訊搬到 CLAUDE.local.md(agent-hub:begin/end 區塊之外,不進版控),
committed 檔案裡改成不提工具名稱的寫法或刪掉,然後 commit。(.gitignore 裡的 .agent-memory/ 可以直接刪,
本機已經用 .git/info/exclude 排除。)細節可用:git grep -n -E 'agent-hub|agent-memory'
MSG
fi
exit 0
