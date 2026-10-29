#!/bin/sh -c exec${IFS}"${0%/*}/py"${IFS}"$0"${IFS}"$@"
"""commit 標題的格式:Conventional Commits(https://www.conventionalcommits.org/)。

  <type>[(<scope>)][!]: <說明>

type 跟助理的 repo 同一組(兩邊共用同一份 cliff.toml 產生 release notes:feat 升 minor、fix/perf 升 patch、! 升 major);
scope 是這個 repo 裡的區塊名(自由字串)。破壞相容的加 `!`,內文寫 `BREAKING CHANGE: …`。
git 自己產生的標題(Merge …、Revert "…")照收;`fixup!`/`squash!` 只在自己的 branch 上可以,合併閘門會擋。

用的地方:agent-hub 自己的 githooks/commit-msg、登錄 `commits: conventional` 的專案在 bin/merge-branch 合併前。

  commit_title.py --file <COMMIT_EDITMSG>    commit-msg hook:不合格印原因、結束碼 1
  commit_title.py <標題>                     檢查一行
"""
from __future__ import annotations

import re
import sys

TYPES = ("feat", "fix", "perf", "refactor", "test", "docs", "build", "ci", "chore", "revert")
PATTERN = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()\s][^()]*)\))?(?P<bang>!)?: (?P<desc>\S.*)$")
GIT_MADE = re.compile(r"""^(Merge (branch|remote-tracking branch|tag|commit|pull request) |Revert ")""")
AUTOSQUASH = re.compile(r"^(fixup|squash|amend)! ")
HELP = ("格式:<type>[(<scope>)][!]: <說明>,type 是 " + " / ".join(TYPES)
        + ";例:`fix(hubbus): dispatch 遇到不支援的執行環境不再重試`")


def problem(title: str, final: bool = False) -> str | None:
    """None = 合格;否則是給人看的原因。final = 要進 main 了(fixup!/squash! 不收)。"""
    title = title.strip()
    if not title:
        return "標題是空的"
    if GIT_MADE.match(title):
        return None
    if AUTOSQUASH.match(title):
        return "fixup!/squash! 要先 `git rebase --autosquash` 合進去" if final else None
    m = PATTERN.match(title)
    if not m:
        return f"不是 Conventional Commits 的格式。{HELP}"
    if m["type"] not in TYPES:
        return f"type `{m['type']}` 不在清單裡。{HELP}"
    return None


def title_of(message: str) -> str:
    """commit 訊息的第一個非註解、非空行(commit-msg hook 拿到的檔案含 # 註解)。"""
    for line in message.splitlines():
        if line.strip() and not line.startswith("#"):
            return line
    return ""


def main(argv: list[str]) -> int:
    if argv[:1] == ["--file"] and len(argv) == 2:
        with open(argv[1], encoding="utf-8") as f:
            title = title_of(f.read())
    elif len(argv) == 1:
        title = argv[0]
    else:
        print(__doc__, file=sys.stderr)
        return 2
    why = problem(title)
    if why:
        print(f"commit 標題:{why}\n  標題:{title.strip()!r}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
