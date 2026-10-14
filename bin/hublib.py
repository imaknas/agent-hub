"""agent-hub 幾支腳本共用的判斷:哪些 transcript 是真人打的訊息、transcript 資料夾
對應到哪個專案、哪些 commit 算專案工作。放在一處,改一次所有腳本一起跟上。

使用者:check-memory-gaps.py、memory-nudge.py、weekly-reflect.py。
"""
import datetime
import json
import pathlib
import re
from typing import Iterable, Iterator, NamedTuple

HOME = pathlib.Path.home()
HUB = HOME / "agent-hub"
MANIFEST = HUB / "onboarded-projects.tsv"
# 2026-10-15 起所有專案都在 mini 的 ~/workspace/<name>(manifest 的 local_path 就是這個)。
# 之前在 MBP 上的位置(以及改名前的路徑)記在 legacy-paths.tsv:transcript 資料夾名稱是用
# 當時的路徑算的,統計舊對話要靠它。
LEGACY_PATHS = HUB / "legacy-paths.tsv"
MBP_HOME = "/Users/you"
MINI_HOME = "/Users/you"
MINI_WORKSPACE = f"{MINI_HOME}/workspace"
# mini 上 librarian 用的 memory-only 副本(只有 AGENT.md/STATE.md/memory/,不是專案程式碼)。
# 2026-10-15 前叫 ~/projects,改成隱藏目錄:跟 ~/workspace(開 session 的地方)名字太像,會被當成專案資料夾
MEMORY_COPIES = HOME / ".memory-copies"
# 跑在 mini 上的分析讀這些地方:MBP 每小時備份過來的 transcript + mini 本機 session 的。
# mini 的 ~/.claude/projects 也有 librarian `claude -p` 的 transcript(-Users-<user>--memory-copies-*),
# iter_entries 只用完整 slug 比對,不會算進去。
TRANSCRIPT_ROOTS = (
    HOME / "transcript-backups" / "claude-personal-projects",
    pathlib.Path(f"{MINI_HOME}/.claude/projects"),
)

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


def slug_for_path(path: str) -> str:
    """Claude Code 存 transcript 的資料夾名:路徑裡的 / 跟 . 換成 -。"""
    return path.replace("/", "-").replace(".", "-")


def project_paths() -> dict[str, list[str]]:
    """專案名稱 → 現在的路徑(mini 的 ~/workspace)+ 以前的路徑(legacy-paths.tsv)。"""
    out: dict[str, list[str]] = {}
    for line in MANIFEST.read_text().splitlines()[1:]:
        if line.strip():
            name, path = line.split("\t")[:2]
            out.setdefault(name, []).append(path)
    for name, path in EXTRA_PROJECTS.items():
        out.setdefault(name, []).append(path)
    for name, paths in legacy_paths().items():
        out.setdefault(name, []).extend(p for p in paths if p not in out[name])
    return out


def is_human_prompt(entry: dict) -> bool:
    """只算 Knas 實際送出的訊息。排除:其他 session 傳來的訊息、背景工作通知、
    slash command 的輸出、壓縮摘要、hook 注入等(它們在 transcript 裡也是 type=user)。"""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    if not isinstance(entry.get("message", {}).get("content"), str):
        return False  # tool result
    origin = entry.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "human"


def parse_ts(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))


class Entry(NamedTuple):
    ts: datetime.datetime      # aware, UTC
    session: str
    entry: dict


def iter_entries(transcripts: pathlib.Path | Iterable[pathlib.Path], paths: list[str],
                 since: datetime.datetime, until: datetime.datetime | None = None) -> Iterator[Entry]:
    """某個專案(可能有多個路徑)在時間範圍內的所有 transcript 紀錄。transcripts 可以是
    一個或多個根目錄(TRANSCRIPT_ROOTS)。"""
    roots = [transcripts] if isinstance(transcripts, pathlib.Path) else list(transcripts)
    for root, path in ((r, p) for r in roots for p in paths):
        d = root / slug_for_path(path)
        for f in d.glob("*.jsonl"):
            if datetime.datetime.fromtimestamp(f.stat().st_mtime, datetime.timezone.utc) < since:
                continue  # 這個檔案在範圍開始前就沒再寫過
            with f.open(errors="replace") as fh:
                for line in fh:
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    ts = o.get("timestamp")
                    if not ts:
                        continue
                    t = parse_ts(ts)
                    if t < since or (until and t >= until):
                        continue
                    yield Entry(t, o.get("sessionId") or f.stem, o)


def is_work_subject(subject: str) -> bool:
    return not AUTOMATED_SUBJECT.match(subject)
