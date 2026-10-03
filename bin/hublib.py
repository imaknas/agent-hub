"""agent-hub 幾支腳本共用的判斷:哪些 transcript 是真人打的訊息、transcript 資料夾
對應到哪個專案、哪些 commit 算專案工作。放在一處,改一次所有腳本一起跟上。

使用者:check-memory-gaps.py、memory-nudge.py、weekly-reflect.py。
"""
import datetime
import json
import pathlib
import re
from typing import Iterator, NamedTuple

HOME = pathlib.Path.home()
HUB = HOME / "agent-hub"
MANIFEST = HUB / "onboarded-projects.tsv"
# MBP 的路徑(transcript 資料夾名稱是用 MBP 上的路徑算出來的,mini 上讀備份也一樣)
MBP_HOME = "/Users/you"

# 沒有 onboard、但 transcript 裡會出現、值得單獨算的工作目錄
EXTRA_PROJECTS = {
    "agent-hub": f"{MBP_HOME}/agent-hub",
    "project-29": f"{MBP_HOME}/project-29",
}
# 改過名/搬過家的專案:舊路徑的 transcript 算到新名稱
ALIASES = {
    f"{MBP_HOME}/projects/python/adaptive-iteration": "ordal",
}

# 不代表「有人在這個專案做了工作」的 commit:排程自動產生的(文獻、資料快照、librarian),
# 以及只動到 agent-hub 管的檔案(.gitignore/.claude/CLAUDE.md)的 commit(onboard、批次清理)。
AUTOMATED_SUBJECT = re.compile(r"^(papers: |data: nightly snapshot|librarian: )")
NON_WORK_PATHS = (":!.gitignore", ":!.claude", ":!CLAUDE.md")


def slug_for_path(path: str) -> str:
    """Claude Code 存 transcript 的資料夾名:路徑裡的 / 跟 . 換成 -。"""
    return path.replace("/", "-").replace(".", "-")


def project_paths() -> dict[str, list[str]]:
    """專案名稱 → 它的 MBP 路徑(含別名的舊路徑)。"""
    out: dict[str, list[str]] = {}
    for line in MANIFEST.read_text().splitlines()[1:]:
        if line.strip():
            name, path = line.split("\t")[:2]
            out.setdefault(name, []).append(path)
    for name, path in EXTRA_PROJECTS.items():
        out.setdefault(name, []).append(path)
    for path, name in ALIASES.items():
        out.setdefault(name, []).append(path)
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


def iter_entries(transcripts: pathlib.Path, paths: list[str],
                 since: datetime.datetime, until: datetime.datetime | None = None) -> Iterator[Entry]:
    """某個專案(可能有多個路徑)在時間範圍內的所有 transcript 紀錄。"""
    for path in paths:
        d = transcripts / slug_for_path(path)
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
