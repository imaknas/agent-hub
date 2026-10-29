"""agent-hub 幾支腳本共用的判斷:哪些 transcript 是真人打的訊息、transcript 資料夾
對應到哪個專案、哪些 commit 算專案工作。放在一處,改一次所有腳本一起跟上。

讀對話紀錄不在這裡:經過 runtime_ports(第 5 層 transcripts),Claude Code 的格式只在 runtime_claude.py。

使用者:check-memory-gaps.py、memory-nudge.py、weekly-reflect.py。
"""
from __future__ import annotations  # 系統的 python3(3.9,hook 裡用的就是它)也要能 import:memory-nudge.py 靠它

import datetime
import json
import pathlib
import re

import hubconf  # 同在 bin/;用 hublib 的腳本都已經把 bin/ 放進 sys.path

HOME = pathlib.Path.home()
HUB = hubconf.ROOT
# 2026-10-15 起所有專案都在 mini 的 ~/workspace/<name>(登錄的 paths.workspace 就是這個)。
# 之前在 MBP 上的位置(以及改名前的路徑)記在 legacy-paths.tsv:transcript 資料夾名稱是用
# 當時的路徑算的,統計舊對話要靠它。
LEGACY_PATHS = hubconf.data_file("legacy-paths.tsv")
MBP_HOME = hubconf.get("hosts.mbp_home", str(pathlib.Path.home()))   # 家目錄從 hub.json 讀,不寫死(CLAUDE.md 寫新程式的規則)
MINI_HOME = hubconf.get("hosts.mini_home", str(pathlib.Path.home()))
MINI_WORKSPACE = f"{MINI_HOME}/workspace"
# mini 上 librarian 用的 memory-only 副本(只有 AGENT.md/STATE.md/memory/,不是專案程式碼)。
# 2026-10-15 前叫 ~/projects,改成隱藏目錄:跟 ~/workspace(開 session 的地方)名字太像,會被當成專案資料夾
MEMORY_COPIES = hubconf.p("memory_copies", "~/.memory-copies")
# 沒有 onboard、但 transcript 裡會出現、值得單獨算的工作目錄(舊的 MBP 位置在 legacy-paths.tsv)
EXTRA_PROJECTS = {
    "agent-hub": f"{MINI_HOME}/agent-hub",
}


def legacy_paths(name: str | None = None) -> dict[str, list[str]] | list[str]:
    """專案名稱 → 它以前的路徑(MBP 上的位置、改名前的路徑)。給 name 就只回那個專案的。"""
    out: dict[str, list[str]] = {}
    if LEGACY_PATHS.exists():
        for line in LEGACY_PATHS.read_text().splitlines()[1:]:
            cols = line.split("\t")
            if len(cols) >= 2 and cols[0]:
                out.setdefault(cols[0], []).append(cols[1])
    return out.get(name, []) if name is not None else out


# 不代表「有人在這個專案做了工作」的 commit:排程自動產生的(文獻、資料快照、librarian),
# 以及只動到 agent-hub 管的檔案(.gitignore/.claude/CLAUDE.md)的 commit(onboard、批次清理)。
AUTOMATED_SUBJECT = re.compile(r"^(papers: |data: nightly snapshot|librarian: )")
NON_WORK_PATHS = (":!.gitignore", ":!.claude", ":!CLAUDE.md")


def project_paths() -> dict[str, list[str]]:
    """專案名稱 → 現在的路徑(mini 的 ~/workspace)+ 以前的路徑(legacy-paths.tsv)。"""
    out: dict[str, list[str]] = {}
    for name, paths in hubconf.roster().items():
        ws = paths.get("workspace")
        if ws:  # ~ 是 mini 的家目錄(transcript 資料夾名稱照 mini 的路徑算),在 MBP 上跑也一樣
            out.setdefault(name, []).append(MINI_HOME + ws[1:] if ws.startswith("~/") else ws)
    for name, path in EXTRA_PROJECTS.items():
        out.setdefault(name, []).append(path)
    for name, paths in legacy_paths().items():
        out.setdefault(name, []).extend(p for p in paths if p not in out[name])
    return out


def is_work_subject(subject: str) -> bool:
    return not AUTOMATED_SUBJECT.match(subject)


# ---------- 每天的 digest(digest/YYYY-MM-DD.md)----------
# 好幾個 job 各寫自己的一段(librarian 的專案摘要、memory-gaps…),各自重跑也不會互相蓋掉或重複:
# 每段用 <!-- digest:<名稱>:begin/end --> 包起來,同名的段落整段換掉,沒有就加在最後。

def digest_path(day: str | None = None) -> pathlib.Path:
    return hubconf.data_file("digest") / f"{day or datetime.date.today().isoformat()}.md"


def digest_append(section: str, text: str, day: str | None = None) -> pathlib.Path | None:
    """把 text 寫成今天 digest 的 <section> 段落(text 空 = 拿掉那一段)。檔案還沒有、text 又是空的就不建檔。"""
    f = digest_path(day)
    day = f.stem
    begin, end = f"<!-- digest:{section}:begin -->", f"<!-- digest:{section}:end -->"
    old = f.read_text() if f.exists() else ""
    if not old and not text.strip():
        return None
    block = f"{begin}\n{text.strip()}\n{end}" if text.strip() else ""
    pat = re.compile(rf"\n*{re.escape(begin)}.*?{re.escape(end)}", flags=re.S)
    if old and pat.search(old):  # 原地換掉,段落順序不變
        body = pat.sub(lambda _: f"\n\n{block}" if block else "", old, count=1)
    else:
        body = (old.rstrip() or f"# Digest {day}") + (f"\n\n{block}" if block else "")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body.rstrip() + "\n")
    return f


def main(argv: list) -> int:
    """shell 腳本用:hublib.py digest-append <段落名>(內容從 stdin)、hublib.py digest-section <段落名>(印出那一段)。"""
    import sys
    if len(argv) == 2 and argv[0] == "digest-append":
        f = digest_append(argv[1], sys.stdin.read())
        if f:
            print(f)
        return 0
    if len(argv) == 2 and argv[0] == "digest-section":
        f = digest_path()
        m = re.search(rf"<!-- digest:{re.escape(argv[1])}:begin -->\n(.*?)\n<!-- digest:{re.escape(argv[1])}:end -->",
                      f.read_text() if f.exists() else "", flags=re.S)
        print(m.group(1) if m else "", end="")
        return 0
    print("用法:hublib.py digest-append <段落名> < 內容 | digest-section <段落名>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
