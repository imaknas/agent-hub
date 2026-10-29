#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""Claude Code 的 hook 設定。

專案模式(`merge-hook-settings.py <.claude/settings.local.json>`):交給 runtime_claude.install_hooks——平台管的
hook(指令裡有 ~/agent-hub/bin/)整組換成 `hub-event <事件>`,使用者自己加的留著(執行環境抽象第 3 層,
docs/design/runtime-abstraction.md)。平常由 sync-local-setup.sh 的 `hub-event install` 做,不經過這裡。

`--worktree` 模式(使用者層級設定)維持舊的做法:機械化合併,不覆寫既有內容、不重複加入同樣的 command——純粹的
「這個 command 存在嗎?不存在就 append」。

只有一種模式:memory 追蹤永遠在獨立的 .agent-memory worktree 裡,hook 一律
先 cd 進去再操作,不管這個專案有沒有外部 remote。

寫進本機的 `.claude/settings.local.json`(被 gitignore、不進 repo),不是共享的
`.claude/settings.json`:hook 指令含 ~/agent-hub 路徑,進了 repo 就等於把個人
工具名稱/路徑公開(2026-09 在公開 repo 發生過)。換機器時由 recover-project.sh
重新產生。hook 一律寫進 local,不管共享的 settings.json 有沒有——舊專案 commit
進 settings.json 的 hook 已在 2026-09-26 全部移除,不再遷就那種狀態。
hook 仍一律先檢查 `.agent-memory` 存不存在,
不存在就整條安靜地不做事(用 `|| true` 保證結束碼永遠是 0,不會讓其他機器
上的 session 因為這個 hook 而顯示錯誤)。

用法:merge-hook-settings.py <.claude/settings.local.json 路徑>
      merge-hook-settings.py --worktree <使用者層級 settings.json>
        (mini:~/.claude/settings.json;MBP:~/.claude-personal/settings.json——不是工作用的 ~/.claude)
        裝 worktree-memory.sh 的 hook:git worktree 裡沒有專案的 local settings,只有使用者層級的
        hook 會觸發。在主 checkout 裡那支腳本什麼都不做,不會跟專案的 hook 重複。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import hubconf  # noqa: E402  平台 Python 腳本在 shell 字串裡怎麼叫(hubconf.sh_py)

START_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh git pull -q || true"
END_CMD = "[ -d .agent-memory ] && cd .agent-memory && ~/agent-hub/bin/with-lock.sh ~/agent-hub/bin/git-sync-memory.sh || true"
# PreCompact 跟 SessionEnd 用同一支同步腳本(冪等,沒東西可 commit 就安靜結束)。
# 多天不關閉的長 session 不會觸發 SessionEnd,但打滿 context 時會觸發 compact,
# 藉這個時機點順便同步,避免長時間累積的 memory/ 筆記在 session 真的結束前
# 都沒被 commit/push 出去。
PRECOMPACT_CMD = END_CMD
# 每次開 session 順便把本機設定(CLAUDE.local.md 受管區塊、這份 hook、ignore)
# 對齊 agent-hub 目前的版本——template 改了不用靠人記得去每個專案補。
# --quiet:SessionStart hook 的 stdout 會進 session context。
SYNC_CMD = "[ -d .agent-memory ] && [ -x ~/agent-hub/bin/sync-local-setup.sh ] && ~/agent-hub/bin/sync-local-setup.sh --quiet || true"

# git commit/push 之後,如果 memory 筆記比最新 commit 舊,把提醒送進模型的 context
# (PostToolUse 的 additionalContext)。筆記沒落後就什麼都不輸出。
NUDGE_CMD = "[ -d .agent-memory ] && [ -x ~/agent-hub/bin/memory-nudge.py ] && " + hubconf.sh_py("~/agent-hub/bin/memory-nudge.py") + " --post-tool || true"

# 專案信箱(hubbus):有新信就送進正在用的 session。SessionStart 那一份由 sync-local-setup.sh 呼叫
# (不用另外一條 hook)。hubbus 自己判斷是哪個專案、沒信就不輸出;PostToolUse 每個 session 兩分鐘最多查一次。
MAIL_PROMPT_CMD = "[ -x ~/agent-hub/bin/hubbus ] && " + hubconf.sh_py("~/agent-hub/bin/hubbus") + " hook prompt || true"
MAIL_TOOL_CMD = "[ -x ~/agent-hub/bin/hubbus ] && " + hubconf.sh_py("~/agent-hub/bin/hubbus") + " hook post-tool || true"

# 第二階段:專案的 session 不能開別的專案的 session(跨專案只能寄信);bin/guard.py 擋的時候回 deny + 理由
GUARD_CMD = "[ -x ~/agent-hub/bin/guard.py ] && " + hubconf.sh_py("~/agent-hub/bin/guard.py") + " pre-tool || true"

# (event, matcher, command);matcher 為 None 表示不設(該事件全部觸發)
HOOKS = (
    ("SessionStart", None, START_CMD),
    ("SessionStart", None, SYNC_CMD),
    ("SessionEnd", None, END_CMD),
    ("PreCompact", None, PRECOMPACT_CMD),
    ("PostToolUse", "Bash", NUDGE_CMD),
    ("UserPromptSubmit", None, MAIL_PROMPT_CMD),
    ("PostToolUse", "Bash", MAIL_TOOL_CMD),
    ("PreToolUse", "mcp__ccd_session__start_session", GUARD_CMD),
)


WT = "[ -x ~/agent-hub/bin/worktree-memory.sh ] && ~/agent-hub/bin/worktree-memory.sh"
WORKTREE_HOOKS = (
    ("SessionStart", None, f"{WT} start || true"),
    ("SessionEnd", None, f"{WT} end || true"),
    ("PreCompact", None, f"{WT} end || true"),
    ("PostToolUse", "Bash", f"{WT} nudge || true"),
    ("UserPromptSubmit", None, f"{WT} prompt || true"),
)


def has_command(entries, command):
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def main():
    args = sys.argv[1:]
    if not (args and args[0] == "--worktree"):
        # 專案模式:改由執行環境的 binding 裝(runtime_claude.install_hooks:平台管的整組換成 hub-event,使用者自己加的
        # 留著)。上面的 HOOKS 只剩給舊設定對照用;sync-local-setup.sh 走 `hub-event install`,不經過這裡。
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import runtime_claude
        path = Path(args[0])
        settings = json.loads(path.read_text()) if path.exists() else {}
        out = json.dumps(runtime_claude.install_hooks(settings), indent=2) + "\n"
        if not path.exists() or path.read_text() != out:
            path.write_text(out)
        return
    # --worktree:使用者層級的設定(mini 的 ~/.claude/settings.json),只有 Knas 在那個 session 直接確認才改,
    # 所以還是舊的那組 worktree-memory.sh hook,不動
    wanted, args = WORKTREE_HOOKS, args[1:]
    path = Path(args[0])
    settings = json.loads(path.read_text()) if path.exists() else {}

    hooks = settings.setdefault("hooks", {})
    for event, matcher, command in wanted:
        entries = hooks.setdefault(event, [])
        if not has_command(entries, command):
            entry = {"hooks": [{"type": "command", "command": command}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            entries.append(entry)

    out = json.dumps(settings, indent=2) + "\n"
    if not path.exists() or path.read_text() != out:
        path.write_text(out)


if __name__ == "__main__":
    main()
