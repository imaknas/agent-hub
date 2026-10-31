#!/usr/bin/env bash
# 在新機器上,或本機資料夾遺失後,重新接上一個「已經 onboard 過」的專案。
# 跟 onboard-project.sh 不是同一件事,不能互相取代:
#
#   onboard-project.sh 假設這個專案從來沒接過 agent-hub,會建一個全新的
#   orphan agent-memory 分支 + mini 上全新的 bare repo。對一個「其實已經
#   onboard 過,只是本機資料夾不見了」的專案跑 onboard-project.sh,會建出
#   一個全新、空白、跟舊歷史完全斷開的 agent-memory 分支,等於把 mini 上
#   已經存在的真實 memory 歷史孤兒化——不是救回來,是製造第二份互不相干
#   的記憶。
#
#   recover-project.sh 假設 mini 上的 agent-memory 分支歷史還在(從
#   專案登錄 projects/<name>.yaml 查 memory_bare),只是把本機工作副本重新接
#   上那個既有分支,不建立任何新的 branch/bare repo。
#
# 前置條件不滿足就直接失敗退出,不會自己猜著做。
#
# 用法:recover-project.sh <name> [目標路徑,預設用 manifest 記錄的 local_path]
set -euo pipefail

AGENT_HUB="${AGENT_HUB_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
# 跑 hub 的那台(hub.json hosts.server;用 plutil 讀,這支也可能在沒有平台 Python 的機器上跑)
source "$(dirname "${BASH_SOURCE[0]}")/hubpaths.sh"
MINI_HOST="$(hub_server)"
TEMPLATES="$AGENT_HUB/templates"
_rd="$(hub_json_get registry_dir)"; _rd="${_rd:-projects}"; _rd="${_rd/#\~/$HOME}"
REGISTRY="$([ "${_rd#/}" != "$_rd" ] && echo "$_rd" || echo "$HUB_CONFIG/$_rd")"   # hub.json registry_dir,相對 config 目錄

fail() { echo "recover-project: $1" >&2; exit 1; }

[ $# -ge 1 ] || fail "$(hub_msg "usage: recover-project.sh <name> [target path]")"
NAME="$1"

[ -d "$REGISTRY" ] || fail "$(hub_msg "{path} not found: first make sure agent-hub itself is cloned" path="$REGISTRY")"

row=$("$AGENT_HUB/bin/py" "$AGENT_HUB/bin/hubconf.py" roster | awk -F'\t' -v n="$NAME" '$1==n{print; found=1} END{exit !found}') \
  || fail "$(hub_msg "'{name}' is not in the project registry ({path}): check the spelling, or the project was never registered" name="$NAME" path="$REGISTRY")"

origin_url=$(echo "$row" | cut -f3)
memory_bare_repo=$(echo "$row" | cut -f4)
manifest_path=$(echo "$row" | cut -f2)
TARGET_PATH="${2:-$manifest_path}"

[ "$origin_url" != "(native)" ] || fail "$(hub_msg "'{name}' was a native project (no external GitHub remote).
So its code was never backed up anywhere but the original machine: the registry entry only has
memory_bare_repo ({memory_bare}), which is the history of memory tracking, not code.
If the original machine's disk is still there, rescue the code from it first, then decide by hand how to attach
{memory_bare}; this script will not guess at code." name="$NAME" memory_bare="$memory_bare_repo")"

[ ! -e "$TARGET_PATH" ] || fail "$(hub_msg "{path} already exists, not overwriting: sort it out by hand, then run again" path="$TARGET_PATH")"

hub_say "[1/5] Preconditions OK: {name} (origin={origin}, restoring to {path})" name="$NAME" origin="$origin_url" path="$TARGET_PATH"

# --- 2. clone 真實程式碼 ---
git clone -q "$origin_url" "$TARGET_PATH"
cd "$TARGET_PATH"
hub_say "[2/5] Cloned the code from {origin}" origin="$origin_url"

# --- 3. 接上既有的 agent-memory 分支(不是建新的) ---
# 在 mini 本機還原(工作副本放 ~/workspace/<name>)時,bare repo 就在這台:
# mini 解析不到自己的 tailnet 名稱,直接用本機路徑,之後 memory 的 pull/push 也不經過 ssh
bare_rel="${memory_bare_repo#*:~/}"
if [ "$bare_rel" != "$memory_bare_repo" ] && git -C "$HOME/$bare_rel" rev-parse --is-bare-repository >/dev/null 2>&1; then
  memory_bare_repo="$HOME/$bare_rel"
fi
git remote add mini "$memory_bare_repo"
git fetch -q mini agent-memory
git worktree add -b agent-memory .agent-memory mini/agent-memory -q
hub_say "[3/5] .agent-memory is on the existing agent-memory branch of {memory_bare} (full history, not a new branch)" memory_bare="$memory_bare_repo"

# --- 4-5. 本機專用設定:CLAUDE.local.md 的 agent-hub 區塊、.claude/settings.local.json
#         hook、確保兩者被 ignore(沒被 .gitignore 排除就寫進 .git/info/exclude)。
#         都不進 outer repo——含 ~/agent-hub 路徑/名稱,有些公開 repo 故意不收
#         (見 adaptive-iteration)。跟 onboard、每次 SessionStart 用同一支腳本。
"$AGENT_HUB/bin/sync-local-setup.sh" .
hub_say "[4-5/5] Local setup is aligned (rules-file block / hooks / ignores)"

echo
hub_say "Done: {name} is restored to {path}, with the full agent-memory history." name="$NAME" path="$TARGET_PATH"
