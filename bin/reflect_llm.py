"""weekly-reflect.py 的模型段落:本週主題,跟 Reflect 那套 4D AI 協作觀察
(Delegation 交辦 / Description 描述 / Discernment 判斷 / Diligence 把關)。

每週只呼叫一次(訂閱額度,不是 API 計費)。送進去的是本週你實際打的訊息抽樣,
每則截短;個人生活類專案(EXCLUDED)的原始訊息不送——它們跟 AI 協作能力無關,
而且只同意過它們的待辦出現在 agent-hub。
"""
import collections
import datetime
import re
import json
import os
import pathlib
import subprocess
import zoneinfo

import hublib

TZ = zoneinfo.ZoneInfo("Asia/Taipei")
TRANSCRIPTS = hublib.HOME / "transcript-backups" / "claude-personal-projects"
EXCLUDED = {"project-23", "project-24"}
PER_PROJECT = 40
TOTAL = 240
CHARS = 240


# 送給模型前遮掉像憑證的字串:2026-10 第一次試跑時,模型把對話裡貼過的 Finnhub API key
# 前幾碼引進了報告。已知前綴的 token,跟夠長、字母數字混雜的長字串一律換成 [已遮蔽]。
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_\-]{10,}|sk-ant-[A-Za-z0-9_\-]{10,}|AIza[0-9A-Za-z_\-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}"
    r"|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-.]+"
    r"|(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,})")


def redact(text: str) -> str:
    return SECRET.sub("[已遮蔽]", text)


def sample(start: datetime.datetime, end: datetime.datetime) -> list[tuple[datetime.datetime, str, str]]:
    by_proj: dict[str, list] = collections.defaultdict(list)
    for name, paths in hublib.project_paths().items():
        if name in EXCLUDED:
            continue
        for e in hublib.iter_entries(TRANSCRIPTS, paths, start.astimezone(datetime.timezone.utc),
                                     end.astimezone(datetime.timezone.utc)):
            if hublib.is_human_prompt(e.entry):
                text = redact(" ".join(e.entry["message"]["content"].split()))[:CHARS]
                by_proj[name].append((e.ts.astimezone(TZ), name, text))
    rows = []
    for name, items in by_proj.items():
        items.sort()
        if len(items) > PER_PROJECT:  # 平均取樣,保留整週的分佈
            step = len(items) / PER_PROJECT
            items = [items[int(i * step)] for i in range(PER_PROJECT)]
        rows += items
    rows.sort()
    if len(rows) > TOTAL:
        step = len(rows) / TOTAL
        rows = [rows[int(i * step)] for i in range(TOTAL)]
    return rows


PROMPT = """以下是 Knas 這一週在 Claude Code 裡實際送出的訊息抽樣(每則截短,格式:時間 [專案] 內容)。
請用繁體中文寫一段每週回顧,給他本人看。只根據這些訊息,不要編造;證據不夠就直說。不要客套、不要誇獎。

輸出 markdown,只要這三個小節(用 ### 標題):

### 本週主題
每個出現的專案一行:這週在做什麼(具體到功能/決定),沒有就略過。

### AI 協作觀察(4D)
四個面向各 2–3 句,每個都要引一則具體訊息當例子(引號內 ≤ 20 字):
- **交辦(Delegation)**:哪些事交給 AI、哪些自己決定,分工合不合理
- **描述(Description)**:指令清不清楚、有沒有給足背景,哪裡造成來回
- **判斷(Discernment)**:有沒有質疑或修正 AI 的產出,抓到了什麼
- **把關(Diligence)**:不可逆或對外的動作有沒有先確認、有沒有驗證結果

### 下週可以試的
2–3 條具體、可以直接照做的建議,每條對應上面某個觀察。

訊息:
{rows}"""


def section(start: datetime.datetime, end: datetime.datetime) -> str:
    rows = sample(start, end)
    head = ["", "## 主題與 AI 協作觀察(模型產生)", "",
            f"> 由 {os.environ.get('INDEX_MODEL', 'sonnet')} 根據本週 {len(rows)} 則訊息抽樣撰寫"
            f"(不含 {'、'.join(sorted(EXCLUDED))});是模型的解讀,不是統計。", ""]
    if not rows:
        return "\n".join(head + ["這週沒有可分析的訊息。", ""])
    text = "\n".join(f"{t:%m-%d %H:%M} [{p}] {m}" for t, p, m in rows)
    model = os.environ.get("INDEX_MODEL", "sonnet")
    res = subprocess.run(["claude", "-p", PROMPT.format(rows=text), "--model", model,
                          "--tools", "", "--output-format", "json"],
                         capture_output=True, text=True, timeout=900)
    try:
        body = json.loads(res.stdout).get("result", "").strip()
    except ValueError:
        body = ""
    if not body:
        body = f"(這次模型呼叫失敗,exit {res.returncode};統計部分不受影響)"
    return "\n".join(head + [body, ""])
