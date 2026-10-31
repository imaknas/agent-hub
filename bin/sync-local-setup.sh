#!/usr/bin/env bash
# 把一個已 onboard 專案的「本機專用」設定對齊到 agent-hub 目前的版本。冪等,
# 永遠不改、不 commit 專案 repo 裡的任何被追蹤檔案:
#
#   1. 執行環境的規則檔(`hub-runtime instructions-file`;Claude Code 是 CLAUDE.local.md)裡
#      <!-- agent-hub:begin/end --> 之間的區塊,整塊用
#      templates/agent-instructions.md(名字照 hub.json 填,hubconf.fill_names;加上 .agent-memory/RULES 登記的 templates/rules/*.md)重生——改了 template,所有專案下次開 session 就
#      跟上,不用靠人記得去補。區塊外的內容不動。舊版沒有標記、直接 append
#      的 pointer 段落會被換成有標記的區塊。
#   2. hook(由執行環境裝:`hub-event install`;平台管的換新、使用者自己加的留著)
#   2b. .agent-memory/AGENT.md 對齊 templates/AGENT.md——這個是例外:它在
#      memory 分支上,會 commit+push(只 commit AGENT.md),不碰專案 repo
#   3. .agent-memory/ 跟執行環境的本機專用檔(`hub-runtime local-ignores`)如果沒被 ignore,寫進
#      .git/info/exclude——本機專屬的 ignore,不動會 commit 的 .gitignore,
#      不改專案共享的 .gitignore。
#
#   4. 已 commit 的檔案寫了這台機器的路徑(~/agent-hub/…、/Users/<使用者>/…)就印提醒(--quiet 也印)。
#      10-27 起 agent-hub、助理都會開源,只提名稱不算外洩;路徑跟個人資料才是
#
# 由專案的 SessionStart hook 自動呼叫(--quiet),onboard-project.sh、
# recover-project.sh 也呼叫。SessionStart hook 的 stdout 會進 session
# context,所以 --quiet 時只印第 4 項的提醒(有違規時),其他什麼都不印。
#
# 用法:sync-local-setup.sh [--quiet] [專案路徑,預設目前目錄]
set -uo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
QUIET=false
[ "${1:-}" = "--quiet" ] && { QUIET=true; shift; }
# Progress lines print only without --quiet ($QUIET || …): the SessionStart hook's stdout goes into the session, and the
# quiet path must not spawn anything. Warnings meant for that session print either way.

cd "${1:-.}" || exit 1
[ -d .agent-memory ] || {
  $QUIET || hub_say "sync-local-setup: {dir} has no .agent-memory, not an onboarded project; skipping" dir="$(pwd)"; exit 0; }

# --- 0. 這份資料夾已經搬到 mini(migrate-to-mini.py 留的標記)---
# --quiet 也印:SessionStart 的 stdout 會進 context,在 MBP 舊副本開 session 的那一刻就提醒,
# 不靠人記得。兩邊同時開 session 的話,同一天的 memory 筆記 push 會衝突。
moved=$(git rev-parse --git-common-dir 2>/dev/null)/agent-hub-migrated-to-mini
if [ -f "$moved" ]; then
  hub_say "⚠️ This project has moved to the hub machine ({target}, moved {date}). This is the old copy kept on the laptop as a backup;
do not work here: ask the user to close this session and open the folder on the hub machine over SSH ({server}) in Claude Desktop instead." \
    target="$(cut -f1 "$moved")" date="$(cut -f2 "$moved")" server="$(hub_server)"
  exit 0
fi

# --- 1. 規則檔的受管區塊 ---
# 寫到哪個檔由執行環境決定(runtime_ports 的 instructions;Claude Code 是 CLAUDE.local.md)。問不到就停,
# 不猜一個預設檔名——那等於把 Claude 寫死回來。
# 結束碼 3 = 這個執行環境沒有規則檔(例:runtime.default 是 none):明確跳過,其他步驟照做。
INSTR=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-runtime" instructions-file "$PWD" 2>/dev/null); rc=$?
if [ "$rc" = 3 ]; then
  $QUIET || hub_say "sync-local-setup: this runtime has no rules file (hub.json runtime.default); the managed block is not written"
elif [ "$rc" != 0 ] || [ -z "$INSTR" ]; then
  hub_say "sync-local-setup: hub-runtime could not say where the rules file is; the managed block was not updated" >&2; exit 1
else
# 專案有在 memory 分支的 .agent-memory/RULES 登記額外規則(一行一個名稱,例如
# engineering)的話,把 templates/rules/<名稱>.md 接在區塊後面。登記放在 memory
# 分支上:換機器 recover 也還在;規則內容放 agent-hub:改一次,所有登記的專案都跟上。
# stdout: "<name>\t<path>" for each listed rule whose file is missing (reported below, in the UI language). A temp file,
# not $(…): bash 3.2 misparses a heredoc inside a command substitution
missing=$(mktemp) || exit 1
"$AGENT_HUB/bin/py" - "$AGENT_HUB/templates/agent-instructions.md" "$INSTR" .agent-memory/RULES "$AGENT_HUB/templates/rules" \
  <<'PY' >"$missing"
import re, sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]).resolve().parents[1] / "bin"))
import hubconf

BEGIN, END = "<!-- agent-hub:begin -->", "<!-- agent-hub:end -->"
LEGACY_HEADING = "## agent-hub memory 追蹤"

tpl, path = Path(sys.argv[1]), Path(sys.argv[2])
rules_file, rules_dir = Path(sys.argv[3]), Path(sys.argv[4])
body = "\n".join(tpl.read_text().splitlines()[2:]).strip("\n")  # 去掉 "# CLAUDE.md" 標題
names = rules_file.read_text().split() if rules_file.exists() else []
# 登錄決定的規則(不靠 RULES):commits: conventional → 合併閘門會檢查標題,規則檔一定要帶說明
here = Path.cwd().resolve()
proj = next((d for n, d in hubconf.projects().items()
             if (ws := hubconf.workspace(n)) and ws.resolve() == here), {})
if proj.get("commits") == "conventional" and "conventional-commits" not in names:
    names.append("conventional-commits")
if names:
    for name in names:
        rule = rules_dir / f"{name}.md"
        if rule.exists():
            body += "\n\n" + rule.read_text().strip("\n")
        else:
            print(f"{name}\t{rule}")
block = f"{BEGIN}\n{hubconf.fill_names(body)}\n{END}\n"
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
rc=$?
while IFS=$'\t' read -r name rule; do
  hub_say "sync-local-setup: RULES lists {name}, but {rule} is not there" name="$name" rule="$rule" >&2
done <"$missing"
rm -f "$missing"
[ "$rc" = 0 ] && { $QUIET || hub_say "sync-local-setup: {file} aligned" file="$INSTR"; }
fi

# --- 2. hook ---
# 由執行環境裝(runtime_ports 的 lifecycle):平台管的 hook 全部換成 `hub-event <事件>`,使用者自己加的留著
"$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-event" install "$PWD" 2>/dev/null; rc=$?
[ "$rc" = 0 ] && { $QUIET || hub_say "sync-local-setup: hooks aligned"; }
[ "$rc" = 3 ] && { $QUIET || hub_say "sync-local-setup: this runtime has no session events (hooks), none installed"; }

# --- 2b. .agent-memory/AGENT.md 對齊 templates/AGENT.md ---
# AGENT.md 在 memory 分支上(會被 commit、同步到 mini),不是本機檔;
# git-sync-memory.sh 只收 memory/ 跟 STATE.md,所以這裡自己 commit+push,
# 而且只 commit AGENT.md 這一個檔案。跟 SessionStart 的 git pull 平行跑,
# 用同一把 with-lock 鎖避免互撞。
# 比對的是 HEAD 裡的版本,不是 working tree:上次 cp 了但 commit/push 失敗的話,
# 下次還會再試。
if ! git -C .agent-memory show HEAD:AGENT.md 2>/dev/null | cmp -s "$AGENT_HUB/templates/AGENT.md" -; then
  # 不跑專案自己的 git hook(見 git-sync-memory.sh 的說明)
  ( cd .agent-memory \
    && export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null \
    && cp "$AGENT_HUB/templates/AGENT.md" AGENT.md \
    && "$AGENT_HUB/bin/with-lock.sh" sh -c \
         'git commit -q -m "agent-hub: sync AGENT.md from template" -- AGENT.md \
          && "$0/bin/git-safe-push.sh"' "$AGENT_HUB" ) >/dev/null 2>&1 \
    && { $QUIET || hub_say "sync-local-setup: AGENT.md aligned with the template and pushed"; } \
    || { $QUIET || hub_say "sync-local-setup: ⚠️ AGENT.md updated, but the commit/push failed; the next session start tries again"; }
fi

# --- 3. 本機檔一定要被 ignore ---
exclude=$(git rev-parse --git-path info/exclude 2>/dev/null) || exit 0
# .agent-memory 是平台自己的;其他是執行環境的本機專用檔(規則檔、本機設定),由它回報
for f in .agent-memory $("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hub-runtime" local-ignores 2>/dev/null); do
  if git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
    # 已經被 commit 進 repo:ignore 救不了,要那個專案自己的 session 處理
    $QUIET || hub_say "sync-local-setup: ⚠️ {file} is tracked by this repo (it was committed), so ignoring it does nothing; the project's own session has to handle it" file="$f"
  else
    # 一律寫進 exclude,不管 .gitignore 有沒有:.gitignore 裡的那行之後會被拿掉
    # (它本身就洩漏名稱),不能依賴它。
    [ "$f" = .agent-memory ] && pat=".agent-memory/" || pat="$f"
    if ! grep -qxF "$pat" "$exclude" 2>/dev/null; then
      mkdir -p "$(dirname "$exclude")"
      printf '%s\n' "$pat" >> "$exclude"
      $QUIET || hub_say "sync-local-setup: {pattern} added to {exclude}" pattern="$pat" exclude="$exclude"
    fi
  fi
done
# --- 3b/3c. memory 落後提醒、專案信箱:不在這裡了 ---
# SessionStart 時由 `hub-event session_start` 統一呼叫(這支被它當成「純設定」跑;兩邊都做會送兩次)。

# --- 4. 被 commit 的檔案不能提到這套機制 ---
# repo 之後會不會公開沒有定論,所以一律不准:agent-hub、.agent-memory 的名稱或路徑
# 出現在已 commit 的內容裡就提醒。這段刻意在 --quiet 下也輸出——SessionStart hook
# 的 stdout 會進 session context,等於每次開 session 都把待修清單交給這個專案
# 自己的 session,不用靠人記得去巡。沒有違規就什麼都不印。
leak_re='~/agent-hub/|/Users/[A-Za-z0-9._-]+/(agent-hub|workspace|\.agent-hub|services|git-origin)'
leaks=$(git grep -l -I -E "$leak_re" HEAD -- . 2>/dev/null | sed 's#^HEAD:##' | head -10)
if [ -n "$leaks" ]; then
  hub_say "⚠️ Files committed in this repo name this machine's paths (the repo may become public, and local paths must not be committed; naming agent-hub is fine):
{files}
Please handle it in this project: move what operations need into {file} (outside the agent-hub:begin/end block, not version-controlled),
and in committed files write it without local paths or delete it, then commit. For details:
{command}" files="$(echo "$leaks" | sed 's/^/  - /')" file="$INSTR" command="git grep -n -E '$leak_re'"
fi
exit 0
